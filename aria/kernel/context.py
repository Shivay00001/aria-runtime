"""aria/kernel/context.py — Immutable per-step execution context."""

from __future__ import annotations

from aria.models.types import KernelConfig, utcnow


class ExecutionContext:
    __slots__ = (
        "session_id",
        "trace_id",
        "step_number",
        "conversation_history",
        "available_tools",
        "config",
        "started_at",
    )

    def __init__(
        self,
        session_id: str,
        trace_id: str,
        step_number: int,
        conversation_history: tuple,
        available_tools: tuple,
        config: KernelConfig,
    ) -> None:
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "trace_id", trace_id)
        object.__setattr__(self, "step_number", step_number)
        object.__setattr__(self, "conversation_history", conversation_history)
        object.__setattr__(self, "available_tools", available_tools)
        object.__setattr__(self, "config", config)
        object.__setattr__(self, "started_at", utcnow())

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"ExecutionContext is immutable. Cannot set {name!r}.")

    def with_step(
        self, new_step_number: int, new_trace_id: str, new_history: tuple
    ) -> ExecutionContext:
        return ExecutionContext(
            session_id=self.session_id,
            trace_id=new_trace_id,
            step_number=new_step_number,
            conversation_history=new_history,
            available_tools=self.available_tools,
            config=self.config,
        )
