"""Sandbox tests — tool execution, timeout, output validation."""

import pytest

from aria.models.errors import (
    PathTraversalError,
    ToolInputValidationError,
    ToolTimeoutError,
)
from aria.models.types import ToolPermission
from aria.tools.sandbox import run_tool_sandboxed
from tests.conftest import make_manifest


class TestSandboxExecution:
    def test_real_tool_executes(self, tmp_path):
        """read_file executes correctly on a real file."""
        f = tmp_path / "hello.txt"
        f.write_text("hello world")
        import inspect

        from aria.tools.builtin.read_file import ToolPlugin

        module_path = inspect.getfile(ToolPlugin)
        from aria.models.types import ToolManifest, ToolPermission

        # Use read_file manifest with allowed path set
        manifest = ToolManifest(
            name="read_file",
            version="1.0.0",
            description="Read text contents of a file. Returns content string. Only allowed paths accessible.",
            permissions=frozenset({ToolPermission.FILESYSTEM_READ}),
            timeout_seconds=10,
            max_memory_mb=64,
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 4096},
                    "max_bytes": {"type": "integer", "minimum": 1, "maximum": 10485760},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "size_bytes": {"type": "integer"},
                    "truncated": {"type": "boolean"},
                },
                "required": ["content", "size_bytes", "truncated"],
                "additionalProperties": False,
            },
            allowed_paths=(str(tmp_path),),
        )
        result = run_tool_sandboxed(manifest, {"path": str(f)}, module_path)
        assert result.ok
        assert result.data["content"] == "hello world"
        assert result.data["size_bytes"] == 11
        assert not result.data["truncated"]

    def test_tool_timeout_raises(self, tmp_path):
        """A tool that sleeps beyond timeout is killed."""
        slow_tool = tmp_path / "slow_tool.py"
        slow_tool.write_text("""
import time
class ToolPlugin:
    @staticmethod
    def execute(d):
        time.sleep(60)
        return {"result": "done"}
""")
        from aria.models.types import ToolManifest, ToolPermission

        m = ToolManifest(
            name="slow_tool",
            version="1.0.0",
            description="A deliberately slow tool for testing timeout behavior.",
            permissions=frozenset({ToolPermission.NONE}),
            timeout_seconds=1,
            input_schema={"type": "object", "properties": {}, "additionalProperties": True},
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
                "additionalProperties": False,
            },
        )
        with pytest.raises(ToolTimeoutError):
            run_tool_sandboxed(m, {}, str(slow_tool))

    def test_tool_crash_returns_error_result(self, tmp_path):
        """A crashing tool returns ToolResult(ok=False), doesn't raise."""
        bad_tool = tmp_path / "bad_tool.py"
        bad_tool.write_text("""
class ToolPlugin:
    @staticmethod
    def execute(d):
        raise RuntimeError("Tool intentionally crashed")
""")
        from aria.models.types import ToolManifest, ToolPermission

        m = ToolManifest(
            name="bad_tool",
            version="1.0.0",
            description="A deliberately failing tool for testing error handling.",
            permissions=frozenset({ToolPermission.NONE}),
            timeout_seconds=10,
            input_schema={"type": "object", "properties": {}, "additionalProperties": True},
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
                "additionalProperties": False,
            },
        )
        result = run_tool_sandboxed(m, {}, str(bad_tool))
        assert not result.ok
        assert "RuntimeError" in (result.error_message or "")

    def test_path_traversal_blocked_before_exec(self, tmp_path):
        """Path validation fires before subprocess spawns."""
        m = make_manifest(
            permissions={ToolPermission.FILESYSTEM_READ}, allowed_paths=(str(tmp_path),)
        )
        with pytest.raises(PathTraversalError):
            run_tool_sandboxed(m, {"value": "/etc/passwd"}, "/fake/path.py")
        # Note: value field has path-like content — checked by validate_paths

    def test_input_validation_fires_before_exec(self):
        """Schema validation fires before subprocess spawns."""
        m = make_manifest()
        with pytest.raises(ToolInputValidationError):
            run_tool_sandboxed(m, {}, "/fake/path.py")  # missing "value"

    def test_duration_ms_recorded(self, tmp_path):
        """ToolResult includes execution time."""
        noop = tmp_path / "noop.py"
        noop.write_text("""
class ToolPlugin:
    @staticmethod
    def execute(d): return {"result": "ok"}
""")
        from aria.models.types import ToolManifest, ToolPermission

        m = ToolManifest(
            name="noop_tool",
            version="1.0.0",
            description="A no-operation tool for testing duration tracking.",
            permissions=frozenset({ToolPermission.NONE}),
            timeout_seconds=10,
            input_schema={"type": "object", "properties": {}, "additionalProperties": True},
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
                "additionalProperties": False,
            },
        )
        result = run_tool_sandboxed(m, {}, str(noop))
        assert result.ok
        assert result.duration_ms >= 0
