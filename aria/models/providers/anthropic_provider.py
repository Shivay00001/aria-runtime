"""
aria/models/providers/anthropic_provider.py
————————————————————————————————————————————
Anthropic Claude adapter.
Uses the official `anthropic` SDK if installed.
Raises ImportError with clear instructions if not.
"""
from __future__ import annotations
import json
from aria.models.errors import (ModelOutputValidationError, ModelProviderError,
                                  ModelRateLimitError, ModelTimeoutError)
from aria.models.providers.base import ModelProviderInterface
from aria.models.types import (ActionType, Message, MessageRole, PromptRequest,
                                 RawModelResponse, ToolCallRequest, sha256_str)
from aria.security.secrets import get_secrets_loader

# Cost per 1M tokens (USD) — update when pricing changes
_COST_TABLE: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00},
    "claude-opus-4-6":           {"input": 15.00, "output": 75.00},
}


class AnthropicProvider(ModelProviderInterface):
    def __init__(self) -> None:
        self._client = None

    @property
    def name(self) -> str:
        return "anthropic"

    def _get_client(self):  # type: ignore[return]
        if self._client is None:
            try:
                import anthropic
                import httpx
            except ImportError as exc:
                raise ImportError(
                    "The 'anthropic' package is required for the Anthropic provider. "
                    "Install it with: pip install anthropic"
                ) from exc
            api_key = get_secrets_loader().require("ANTHROPIC_API_KEY")
            self._client = anthropic.Anthropic(
                api_key=api_key,
                timeout=60.0,
            )
        return self._client

    def call(self, request: PromptRequest) -> RawModelResponse:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError("anthropic package not installed") from exc

        client = self._get_client()
        api_messages = _build_messages(request.messages)
        api_tools = _build_tools(request.tools) if request.tools else []

        try:
            response = client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=request.system_prompt,
                messages=api_messages,
                tools=api_tools if api_tools else anthropic.NOT_GIVEN,
                temperature=request.temperature,
            )
        except anthropic.RateLimitError as exc:
            raise ModelRateLimitError(f"Anthropic rate limit: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise ModelProviderError(f"Anthropic API error ({exc.status_code}): {exc.message}",
                                     status_code=exc.status_code) from exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            raise ModelTimeoutError(f"Anthropic timeout: {exc}") from exc

        return _parse_response(response, request.model, self.name)

    def estimate_tokens(self, request: PromptRequest) -> int:
        chars = len(request.system_prompt)
        for m in request.messages:
            chars += len(m.content)
        return chars // 4

    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        rates = _COST_TABLE.get(model, {"input": 3.00, "output": 15.00})
        return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def _build_messages(messages: tuple) -> list[dict]:
    result = []
    for msg in messages:
        if msg.role == MessageRole.SYSTEM:
            continue
        if msg.role == MessageRole.TOOL:
            result.append({"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id or "unknown",
                "content": msg.content,
            }]})
        elif msg.role == MessageRole.ASSISTANT:
            result.append({"role": "assistant", "content": msg.content})
        else:
            result.append({"role": "user", "content": msg.content})
    return result


def _build_tools(tools: tuple) -> list[dict]:
    return [{"name": t.name, "description": t.description,
             "input_schema": t.input_schema} for t in tools]


def _parse_response(response, model: str, provider: str) -> RawModelResponse:  # type: ignore
    raw_hash = sha256_str(json.dumps({"model": model, "id": getattr(response, "id", "")}))
    in_tok = response.usage.input_tokens
    out_tok = response.usage.output_tokens

    for block in response.content:
        if block.type == "tool_use":
            return RawModelResponse(
                action=ActionType.TOOL_CALL,
                tool_call=ToolCallRequest(
                    tool_call_id=block.id,
                    tool_name=block.name,
                    arguments=dict(block.input) if isinstance(block.input, dict) else {},
                ),
                input_tokens=in_tok, output_tokens=out_tok,
                model=model, provider=provider, raw_response_hash=raw_hash,
            )

    text = " ".join(
        b.text for b in response.content if hasattr(b, "text") and b.text
    ).strip()
    if not text:
        raise ModelOutputValidationError(
            f"Model returned empty response. Stop reason: {response.stop_reason}")

    return RawModelResponse(
        action=ActionType.FINAL_ANSWER,
        final_answer=text,
        input_tokens=in_tok, output_tokens=out_tok,
        model=model, provider=provider, raw_response_hash=raw_hash,
    )
