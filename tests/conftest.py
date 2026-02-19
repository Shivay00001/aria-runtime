"""tests/conftest.py — Shared fixtures."""

from __future__ import annotations

import pytest

from aria.memory.sqlite import SQLiteStorage
from aria.models.types import (
    ActionType,
    KernelConfig,
    RawModelResponse,
    ToolPermission,
    new_id,
    sha256_str,
)


@pytest.fixture
def tmp_db(tmp_path):
    s = SQLiteStorage(str(tmp_path / "test.db"))
    yield s
    s.close()


@pytest.fixture
def test_config(tmp_path):
    return KernelConfig(
        primary_provider="mock",
        primary_model="mock-model",
        max_steps=5,
        max_cost_usd=0.10,
        db_path=str(tmp_path / "test.db"),
        log_path=str(tmp_path / "test.jsonl"),
        plugin_dirs=(),
        allowed_permissions=frozenset(
            {
                ToolPermission.NONE,
                ToolPermission.FILESYSTEM_READ,
                ToolPermission.FILESYSTEM_WRITE,
            }
        ),
    )


class MockProvider:
    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self._call_count = 0
        self.calls = []

    @property
    def name(self):
        return "mock"

    def call(self, request):
        self.calls.append(request)
        self._call_count += 1
        if self._responses:
            idx = min(self._call_count - 1, len(self._responses) - 1)
            return self._responses[idx]
        return make_final_answer("Mock response")

    def estimate_tokens(self, request):
        return 100


def make_final_answer(text="Task complete."):
    return RawModelResponse(
        action=ActionType.FINAL_ANSWER,
        final_answer=text,
        input_tokens=50,
        output_tokens=20,
        model="mock-model",
        provider="mock",
        raw_response_hash=sha256_str(text),
    )


def make_tool_call(tool_name, args):
    from aria.models.types import ToolCallRequest

    return RawModelResponse(
        action=ActionType.TOOL_CALL,
        tool_call=ToolCallRequest(tool_call_id=new_id(), tool_name=tool_name, arguments=args),
        input_tokens=50,
        output_tokens=20,
        model="mock-model",
        provider="mock",
        raw_response_hash=sha256_str(tool_name),
    )


def make_manifest(name="test_tool", permissions=None, timeout=10, allowed_paths=None):
    from aria.models.types import ToolManifest

    return ToolManifest(
        name=name,
        version="1.0.0",
        description="A test tool for unit testing purposes only.",
        permissions=frozenset(permissions or {ToolPermission.NONE}),
        timeout_seconds=timeout,
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
            "additionalProperties": False,
        },
        allowed_paths=tuple(allowed_paths or []),
    )
