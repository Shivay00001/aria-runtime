"""Built-in tool end-to-end tests using real subprocess sandbox."""
import inspect
import pytest
from pathlib import Path
from aria.tools.builtin.read_file import ToolPlugin as ReadFileTool
from aria.tools.builtin.write_file import ToolPlugin as WriteFileTool
from aria.models.types import ToolManifest, ToolPermission


def make_read_manifest(allowed_path: str) -> ToolManifest:
    m = ReadFileTool.manifest
    # Return a copy with the allowed path set
    return ToolManifest(
        name=m.name, version=m.version, description=m.description,
        permissions=m.permissions, timeout_seconds=m.timeout_seconds,
        max_memory_mb=m.max_memory_mb,
        input_schema=m.input_schema, output_schema=m.output_schema,
        allowed_paths=(allowed_path,),
    )

def make_write_manifest(allowed_path: str) -> ToolManifest:
    m = WriteFileTool.manifest
    return ToolManifest(
        name=m.name, version=m.version, description=m.description,
        permissions=m.permissions, timeout_seconds=m.timeout_seconds,
        max_memory_mb=m.max_memory_mb,
        input_schema=m.input_schema, output_schema=m.output_schema,
        allowed_paths=(allowed_path,),
    )


class TestReadFileTool:
    def test_read_existing_file(self, tmp_path):
        from aria.tools.sandbox import run_tool_sandboxed
        f = tmp_path / "test.txt"
        f.write_text("hello world content")
        m = make_read_manifest(str(tmp_path))
        result = run_tool_sandboxed(m, {"path": str(f)},
                                     inspect.getfile(ReadFileTool))
        assert result.ok
        assert result.data["content"] == "hello world content"
        assert result.data["size_bytes"] == 19
        assert not result.data["truncated"]

    def test_read_missing_file_returns_error(self, tmp_path):
        from aria.tools.sandbox import run_tool_sandboxed
        m = make_read_manifest(str(tmp_path))
        result = run_tool_sandboxed(m, {"path": str(tmp_path / "nope.txt")},
                                     inspect.getfile(ReadFileTool))
        assert not result.ok
        assert "not found" in (result.error_message or "").lower() or \
               "FileNotFoundError" in (result.error_message or "")

    def test_read_large_file_truncated(self, tmp_path):
        from aria.tools.sandbox import run_tool_sandboxed
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 2000)
        m = make_read_manifest(str(tmp_path))
        result = run_tool_sandboxed(m, {"path": str(f), "max_bytes": 100},
                                     inspect.getfile(ReadFileTool))
        assert result.ok
        assert result.data["truncated"]
        assert len(result.data["content"]) <= 100

    def test_path_traversal_blocked(self, tmp_path):
        from aria.tools.sandbox import run_tool_sandboxed
        from aria.models.errors import PathTraversalError
        m = make_read_manifest(str(tmp_path))
        with pytest.raises(PathTraversalError):
            run_tool_sandboxed(m, {"path": "/etc/passwd"},
                                inspect.getfile(ReadFileTool))


class TestWriteFileTool:
    def test_write_new_file(self, tmp_path):
        from aria.tools.sandbox import run_tool_sandboxed
        m = make_write_manifest(str(tmp_path))
        target = tmp_path / "output.txt"
        result = run_tool_sandboxed(m, {"path": str(target), "content": "written!"},
                                     inspect.getfile(WriteFileTool))
        assert result.ok
        assert target.read_text() == "written!"
        assert result.data["bytes_written"] == 8

    def test_overwrite_existing_file(self, tmp_path):
        from aria.tools.sandbox import run_tool_sandboxed
        f = tmp_path / "existing.txt"
        f.write_text("old content")
        m = make_write_manifest(str(tmp_path))
        result = run_tool_sandboxed(m, {"path": str(f), "content": "new content"},
                                     inspect.getfile(WriteFileTool))
        assert result.ok
        assert f.read_text() == "new content"

    def test_append_to_file(self, tmp_path):
        from aria.tools.sandbox import run_tool_sandboxed
        f = tmp_path / "append.txt"
        f.write_text("line1\n")
        m = make_write_manifest(str(tmp_path))
        result = run_tool_sandboxed(m,
            {"path": str(f), "content": "line2\n", "mode": "append"},
            inspect.getfile(WriteFileTool))
        assert result.ok
        assert f.read_text() == "line1\nline2\n"

    def test_write_path_traversal_blocked(self, tmp_path):
        from aria.tools.sandbox import run_tool_sandboxed
        from aria.models.errors import PathTraversalError
        m = make_write_manifest(str(tmp_path))
        with pytest.raises(PathTraversalError):
            run_tool_sandboxed(m,
                {"path": "/etc/cron.d/evil", "content": "evil"},
                inspect.getfile(WriteFileTool))
