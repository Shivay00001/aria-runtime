"""
aria/models/providers/ollama_provider.py
————————————————————————————————————————————
Ollama (local LLM) adapter.
Connects to local Ollama instance (default: http://localhost:11434).
"""

from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from aria.models.errors import (
    ModelProviderError,
    ModelTimeoutError,
)
from aria.models.providers.base import ModelProviderInterface
from aria.models.types import (
    ActionType,
    PromptRequest,
    RawModelResponse,
    ToolCallRequest,
    sha256_str,
)


class OllamaProvider(ModelProviderInterface):
    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        self.base_url = base_url

    @property
    def name(self) -> str:
        return "ollama"

    def call(self, request: PromptRequest) -> RawModelResponse:
        messages = []
        for m in request.messages:
            role = m.role.value
            if role == "tool":
                # Ollama's chat API typically expects tool results as system or user messages depending on the model
                # mapping to user for generic compatibility
                role = "user"
                content = f"Tool output: {m.content}"
            else:
                content = m.content

            messages.append({"role": role, "content": content})

        # Prepend system prompt if present
        if request.system_prompt:
            messages.insert(0, {"role": "system", "content": request.system_prompt})

        payload = {
            "model": request.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": request.temperature,
                # "num_ctx": 4096 # Adjust context window if needed
            },
        }

        # Add tool definitions if tools are present and model supports function calling (e.g. llama3.1, mistral)
        # Note: tinyllama v1.1 doesn't support native tool calling well, but we pass it effectively as text if needed.
        # For now, we omit 'tools' key in the payload for tinyllama unless we know it supports it,
        # or we implement strict JSON enforcing.
        # We will attempt to use simple JSON mode if tools are requested to guide the model.
        if request.tools:
            # For weak local models, we used to force JSON mode but it confuses tinyllama.
            # payload["format"] = "json"
            # Simplified tool prompt for small local models
            tool_list = "\n".join(
                [
                    f"- {t.name}: {t.description} (Input: {t.input_schema['properties']})"
                    for t in request.tools
                ]
            )
            system_injection = f'\n\nYou have tools available:\n{tool_list}\n\nTo use a tool, respond with: {{"tool": "name", "arguments": {{...}} }}\nIf answering directly, just provide the text.'

            if messages[0]["role"] == "system":
                messages[0]["content"] += system_injection
            else:
                messages.insert(0, {"role": "system", "content": system_injection})

        req = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

        try:
            with urlopen(req, timeout=300) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            raise ModelProviderError(
                f"Ollama API error ({e.code}): {e.reason}", status_code=e.code
            ) from e
        except URLError as e:
            raise ModelTimeoutError(f"Ollama connection failed: {e.reason}") from e

        return self._parse_response(result, request)

    def _parse_response(self, response: dict, request: PromptRequest) -> RawModelResponse:
        msg = response.get("message", {})
        content = msg.get("content", "")

        raw_hash = sha256_str(json.dumps(response))
        # Ollama returns token counts in 'eval_count' and 'prompt_eval_count'
        in_tok = response.get("prompt_eval_count", 0)
        out_tok = response.get("eval_count", 0)

        # Attempt to parse JSON tool call if we forced JSON mode
        if request.tools and "{" in content:
            try:
                data = json.loads(content)
                if "tool" in data and "arguments" in data:
                    return RawModelResponse(
                        action=ActionType.TOOL_CALL,
                        tool_call=ToolCallRequest(
                            tool_call_id=f"call_{int(time.time())}",  # Ollama doesn't give IDs
                            tool_name=data["tool"],
                            arguments=data["arguments"],
                        ),
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        model=request.model,
                        provider=self.name,
                        raw_response_hash=raw_hash,
                    )
                # Fallback to final answer if JSON structure matches "answer" or just generic text
                if "answer" in data:
                    content = data["answer"]
            except json.JSONDecodeError:
                pass  # Treat as normal text if parse fails

        return RawModelResponse(
            action=ActionType.FINAL_ANSWER,
            final_answer=content,
            input_tokens=in_tok,
            output_tokens=out_tok,
            model=request.model,
            provider=self.name,
            raw_response_hash=raw_hash,
        )

    def estimate_tokens(self, request: PromptRequest) -> int:
        # Rough estimate: 4 chars per token
        chars = len(request.system_prompt)
        for m in request.messages:
            chars += len(m.content)
        return chars // 4
