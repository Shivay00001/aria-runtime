"""
aria/kernel/kernel.py — The Agent Kernel.

Orchestrates: sequences steps, delegates to router/sandbox, enforces limits.
Contains no business logic. All reasoning delegated to the model.

Invariants:
  - Every side effect preceded + followed by audit write.
  - FSM state is ground truth for session lifecycle.
  - AuditWriteFailureError → halt immediately.
  - No bare Exception swallowed silently.
"""
from __future__ import annotations
import inspect
import json
import time
import traceback
from aria.kernel.context import ExecutionContext
from aria.kernel.fsm import SessionFSM
from aria.logging_setup import get_logger
from aria.memory.sqlite import AuditInterface, MemoryInterface
from aria.models.errors import (
    ARIAError, AuditWriteFailureError, CircuitBreakerOpenError,
    CostBudgetExceededError, LimitError, ModelProviderExhaustedError,
    PathTraversalError, PermissionDeniedError, PromptInjectionWarning,
    SecurityError, StepLimitExceededError, ToolInputValidationError,
    ToolSandboxError, ToolTimeoutError, UnknownToolError,
)
from aria.models.router import ModelRouter
from aria.models.types import (
    ActionType, AuditEvent, KernelConfig, LogLevel, Message, MessageRole,
    PromptRequest, SessionRequest, SessionResult, SessionStatus,
    StepStatus, StepTrace, StepType, ToolResult, new_id, sha256_str, utcnow,
)
from aria.security.scrubber import assert_clean_input
from aria.tools.registry import ToolRegistry
from aria.tools.sandbox import run_tool_sandboxed

_log = get_logger("aria.kernel")

_SYSTEM_PROMPT = """\
You are a task execution agent. Complete the given task using the available tools.

Rules:
1. Think step by step before acting.
2. Use tools when needed to gather information or take actions.
3. When the task is complete, provide your final answer as plain text.
4. Only use tool names listed in the API tool definitions — never invent tool names.
5. Be precise and factual. Do not invent information.
"""


class AgentKernel:
    """Single-agent, synchronous kernel. One instance per session. Not thread-safe."""

    def __init__(self, model_router: ModelRouter, tool_registry: ToolRegistry,
                 memory: MemoryInterface, audit: AuditInterface,
                 config: KernelConfig) -> None:
        self._router = model_router
        self._registry = tool_registry
        self._memory = memory
        self._audit = audit
        self._config = config

    def run(self, request: SessionRequest) -> SessionResult:
        """Execute a session. Returns SessionResult — never raises to caller."""
        session_id = request.session_id
        fsm = SessionFSM(session_id)
        started = time.monotonic()
        total_cost = 0.0
        step_count = 0

        # Initialise storage — failure here is re-raised (startup, not runtime)
        try:
            self._memory.create_session(session_id, request.task, self._config)
        except AuditWriteFailureError:
            _log.critical("session_create_failed", extra={"session_id": session_id})
            raise

        # Injection scan — warn, don't block
        try:
            assert_clean_input(request.task, field_name="task")
        except PromptInjectionWarning as exc:
            _log.warning("injection_scan_hit", extra={"session_id": session_id, "w": str(exc)})
            self._emit(session_id, None, "injection_scan_warn", LogLevel.WARN, {"w": str(exc)})

        self._emit(session_id, None, "session_start", LogLevel.INFO, {
            "task_len": len(request.task),
            "provider": request.provider_override or self._config.primary_provider,
            "model": request.model_override or self._config.primary_model,
        })

        fsm.transition(SessionStatus.RUNNING)
        self._sync_session(session_id, fsm, step_count, total_cost)

        self._memory.append_message(session_id, Message(role=MessageRole.USER, content=request.task))

        ctx = ExecutionContext(
            session_id=session_id, trace_id=new_id(), step_number=0,
            conversation_history=tuple(self._memory.get_conversation_history(session_id)),
            available_tools=self._registry.all_manifests, config=self._config,
        )

        provider = request.provider_override or self._config.primary_provider
        model = request.model_override or self._config.primary_model

        final_answer: str | None = None
        error_type: str | None = None
        error_msg: str | None = None

        try:
            while not fsm.is_terminal:
                step_count += 1
                ctx = ctx.with_step(
                    new_step_number=step_count, new_trace_id=new_id(),
                    new_history=tuple(self._memory.get_conversation_history(session_id)),
                )

                if step_count > self._config.max_steps:
                    raise StepLimitExceededError(
                        f"Exceeded max_steps={self._config.max_steps}")
                if total_cost > self._config.max_cost_usd:
                    raise CostBudgetExceededError(
                        f"Cost ${total_cost:.4f} exceeded budget ${self._config.max_cost_usd:.2f}")

                # ── Model call ────────────────────────────────────────────────
                prompt_hash = sha256_str(
                    json.dumps([m.to_dict() for m in ctx.conversation_history]))
                trace = StepTrace(
                    session_id=session_id, step_number=step_count,
                    step_type=StepType.MODEL_CALL, status=StepStatus.STARTED,
                    prompt_hash=prompt_hash,
                )
                self._audit.write_step_start(trace)

                t0 = time.monotonic()
                response = self._router.call(PromptRequest(
                    messages=ctx.conversation_history,
                    system_prompt=_SYSTEM_PROMPT,
                    tools=ctx.available_tools,
                    provider=provider, model=model,
                    session_id=session_id, step_number=step_count,
                ))
                step_ms = int((time.monotonic() - t0) * 1000)

                step_cost = self._calculate_cost(provider, model,
                                                  response.input_tokens, response.output_tokens)
                total_cost += step_cost

                trace.model_output_hash = response.raw_response_hash
                trace.input_tokens = response.input_tokens
                trace.output_tokens = response.output_tokens
                trace.cost_usd = step_cost
                trace.duration_ms = step_ms
                trace.finished_at = utcnow()

                if response.action == ActionType.FINAL_ANSWER:
                    trace.step_type = StepType.FINAL_ANSWER
                    trace.status = StepStatus.COMPLETED
                    self._audit.write_step_end(trace)
                    final_answer = response.final_answer
                    self._memory.append_message(
                        session_id, Message(role=MessageRole.ASSISTANT, content=final_answer or ""))
                    fsm.transition(SessionStatus.DONE)

                elif response.action == ActionType.TOOL_CALL:
                    tc = response.tool_call
                    assert tc is not None

                    if not self._registry.has_tool(tc.tool_name):
                        raise UnknownToolError(f"Model requested unknown tool: {tc.tool_name!r}")

                    manifest = self._registry.get_manifest(tc.tool_name)
                    disallowed = manifest.permissions - self._config.allowed_permissions
                    if disallowed:
                        raise PermissionDeniedError(
                            f"Tool {tc.tool_name!r} requires disallowed permissions: {disallowed}")

                    trace.step_type = StepType.TOOL_CALL
                    trace.tool_name = tc.tool_name
                    trace.tool_input_json = json.dumps(tc.arguments)
                    trace.status = StepStatus.COMPLETED
                    self._audit.write_step_end(trace)

                    self._memory.append_message(session_id, Message(
                        role=MessageRole.ASSISTANT,
                        content=f"[Tool call: {tc.tool_name}]",
                        tool_call_id=tc.tool_call_id,
                    ))

                    fsm.transition(SessionStatus.WAITING)
                    self._sync_session(session_id, fsm, step_count, total_cost)

                    tool_result = self._execute_tool(
                        session_id=session_id, step_id=trace.step_id,
                        manifest=manifest, arguments=tc.arguments,
                        tool_call_id=tc.tool_call_id,
                    )

                    fsm.transition(SessionStatus.RUNNING)
                    self._sync_session(session_id, fsm, step_count, total_cost)

                    tool_content = (
                        json.dumps(tool_result.data) if tool_result.ok
                        else f"ERROR: {tool_result.error_message}"
                    )
                    self._memory.append_message(session_id, Message(
                        role=MessageRole.TOOL, content=tool_content,
                        tool_name=tc.tool_name, tool_call_id=tc.tool_call_id,
                    ))

        except (StepLimitExceededError, CostBudgetExceededError, LimitError) as exc:
            error_type, error_msg = type(exc).__name__, str(exc)
            _log.error("limit_exceeded", extra={"session_id": session_id, "error": error_msg})
            self._emit(session_id, None, "limit_exceeded", LogLevel.ERROR,
                       {"error_type": error_type, "error": error_msg})
            if not fsm.is_terminal:
                fsm.transition(SessionStatus.FAILED)

        except (SecurityError, PathTraversalError) as exc:
            error_type, error_msg = type(exc).__name__, str(exc)
            _log.error("security_error", extra={"session_id": session_id, "error": error_msg})
            self._emit(session_id, None, "security_error", LogLevel.ERROR,
                       {"error_type": error_type, "error": error_msg})
            if not fsm.is_terminal:
                fsm.transition(SessionStatus.FAILED)

        except (ModelProviderExhaustedError, CircuitBreakerOpenError) as exc:
            error_type, error_msg = type(exc).__name__, str(exc)
            _log.error("provider_failure", extra={"session_id": session_id, "error": error_msg})
            self._emit(session_id, None, "provider_failure", LogLevel.ERROR,
                       {"error_type": error_type, "error": error_msg})
            if not fsm.is_terminal:
                fsm.transition(SessionStatus.FAILED)

        except AuditWriteFailureError:
            _log.critical("audit_write_failure_halt", extra={"session_id": session_id})
            if not fsm.is_terminal:
                fsm.transition(SessionStatus.FAILED)
            self._sync_session(session_id, fsm, step_count, total_cost,
                               error_type="AuditWriteFailureError",
                               error_msg="Audit write failed — session terminated")
            raise  # Always re-raise: process must halt

        except Exception as exc:
            error_type = type(exc).__name__
            full_trace = traceback.format_exc()
            _log.critical("unexpected_error", extra={
                "session_id": session_id, "error_type": error_type, "trace": full_trace})
            self._emit(session_id, None, "unexpected_error", LogLevel.CRITICAL,
                       {"error_type": error_type, "trace": full_trace})
            error_msg = f"Unexpected error ({error_type}). Check audit log."
            if not fsm.is_terminal:
                fsm.transition(SessionStatus.FAILED)

        duration_ms = int((time.monotonic() - started) * 1000)
        self._emit(session_id, None, "session_end", LogLevel.INFO, {
            "status": fsm.state.value, "steps": step_count,
            "cost_usd": round(total_cost, 6), "duration_ms": duration_ms,
        })
        self._sync_session(session_id, fsm, step_count, total_cost,
                           error_type=error_type, error_msg=error_msg)

        return SessionResult(
            session_id=session_id, status=fsm.state, answer=final_answer,
            steps_taken=step_count, total_cost_usd=round(total_cost, 6),
            duration_ms=duration_ms, error_type=error_type, error_message=error_msg,
        )

    def _execute_tool(self, session_id: str, step_id: str, manifest: object,
                      arguments: dict, tool_call_id: str) -> ToolResult:
        from aria.models.types import ToolManifest as TM
        assert isinstance(manifest, TM)
        module_path = self._registry.get_module_path(manifest.name)
        self._emit(session_id, step_id, "tool_call_start", LogLevel.INFO,
                   {"tool": manifest.name, "tool_call_id": tool_call_id})
        try:
            result = run_tool_sandboxed(manifest=manifest, arguments=arguments,
                                        tool_module_path=module_path)
        except (ToolTimeoutError, ToolSandboxError, ToolInputValidationError,
                PathTraversalError) as exc:
            et = type(exc).__name__
            _log.error("tool_failed", extra={"session_id": session_id, "tool": manifest.name,
                                              "error_type": et, "error": str(exc)})
            self._emit(session_id, step_id, "tool_call_failed", LogLevel.ERROR,
                       {"tool": manifest.name, "error_type": et, "error": str(exc)})
            return ToolResult(ok=False, tool_name=manifest.name, tool_call_id=tool_call_id,
                              error_type=et, error_message=str(exc))
        # Attach correct tool_call_id
        result2 = ToolResult(ok=result.ok, tool_name=result.tool_name, tool_call_id=tool_call_id,
                             data=result.data, error_type=result.error_type,
                             error_message=result.error_message, duration_ms=result.duration_ms)
        self._emit(session_id, step_id, "tool_call_end", LogLevel.INFO,
                   {"tool": manifest.name, "ok": result2.ok, "duration_ms": result2.duration_ms})
        return result2

    def _calculate_cost(self, provider: str, model: str,
                         input_tokens: int, output_tokens: int) -> float:
        if provider == "anthropic":
            try:
                from aria.models.providers.anthropic_provider import AnthropicProvider
                p = self._router._providers.get(provider)
                if isinstance(p, AnthropicProvider):
                    return p.calculate_cost(model, input_tokens, output_tokens)
            except Exception:
                pass
        return 0.0

    def _sync_session(self, session_id: str, fsm: SessionFSM, steps: int, cost: float,
                      error_type: str | None = None, error_msg: str | None = None) -> None:
        try:
            self._memory.update_session_status(
                session_id=session_id, status=fsm.state,
                total_steps=steps, total_cost_usd=round(cost, 6),
                error_type=error_type, error_msg=error_msg)
        except AuditWriteFailureError:
            _log.critical("session_sync_failed", extra={"session_id": session_id})
            raise

    def _emit(self, session_id: str, step_id: str | None, event_type: str,
               level: LogLevel, payload: dict) -> None:
        try:
            self._audit.write_event(AuditEvent(
                session_id=session_id, step_id=step_id,
                event_type=event_type, level=level, payload=payload))
        except AuditWriteFailureError:
            _log.critical("audit_emit_failed", extra={
                "event_type": event_type, "session_id": session_id})
            raise
