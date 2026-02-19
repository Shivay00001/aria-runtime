"""Type validation tests."""
import pytest
from aria.models.types import ToolManifest, ToolPermission, SessionRequest, KernelConfig


class TestToolManifest:
    def test_valid_accepted(self):
        m = ToolManifest(
            name="my_tool", version="1.0.0",
            description="A test tool for unit testing only.",
            permissions=frozenset({ToolPermission.NONE}),
            timeout_seconds=30, max_memory_mb=128,
            input_schema={"type": "object"}, output_schema={"type": "object"},
        )
        assert m.name == "my_tool"

    def test_uppercase_name_rejected(self):
        with pytest.raises(ValueError, match="invalid"):
            ToolManifest(name="MyTool", version="1.0.0",
                         description="A test tool for unit testing only.",
                         permissions=frozenset({ToolPermission.NONE}),
                         timeout_seconds=10, input_schema={}, output_schema={})

    def test_name_with_hyphen_rejected(self):
        with pytest.raises(ValueError):
            ToolManifest(name="my-tool", version="1.0.0",
                         description="A test tool for unit testing only.",
                         permissions=frozenset({ToolPermission.NONE}),
                         timeout_seconds=10, input_schema={}, output_schema={})

    def test_invalid_version_rejected(self):
        with pytest.raises(ValueError, match="semver"):
            ToolManifest(name="tool", version="v1",
                         description="A test tool for unit testing only.",
                         permissions=frozenset({ToolPermission.NONE}),
                         timeout_seconds=10, input_schema={}, output_schema={})

    def test_short_description_rejected(self):
        with pytest.raises(ValueError):
            ToolManifest(name="tool", version="1.0.0", description="Short",
                         permissions=frozenset({ToolPermission.NONE}),
                         timeout_seconds=10, input_schema={}, output_schema={})

    def test_relative_path_rejected(self):
        with pytest.raises(ValueError, match="absolute"):
            ToolManifest(name="file_tool", version="1.0.0",
                         description="A test tool for unit testing only.",
                         permissions=frozenset({ToolPermission.FILESYSTEM_READ}),
                         timeout_seconds=10, input_schema={}, output_schema={},
                         allowed_paths=("relative/path",))

    def test_absolute_path_accepted(self):
        m = ToolManifest(name="file_tool", version="1.0.0",
                         description="A test tool for unit testing only.",
                         permissions=frozenset({ToolPermission.FILESYSTEM_READ}),
                         timeout_seconds=10, input_schema={}, output_schema={},
                         allowed_paths=("/workspace", "/tmp/aria"))
        assert "/workspace" in m.allowed_paths

    def test_frozen_immutable(self):
        m = ToolManifest(name="tool", version="1.0.0",
                         description="A test tool for unit testing only.",
                         permissions=frozenset({ToolPermission.NONE}),
                         timeout_seconds=10, input_schema={}, output_schema={})
        with pytest.raises((AttributeError, TypeError)):
            m.name = "other"  # type: ignore

    def test_timeout_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            ToolManifest(name="tool", version="1.0.0",
                         description="A test tool for unit testing only.",
                         permissions=frozenset({ToolPermission.NONE}),
                         timeout_seconds=0, input_schema={}, output_schema={})

    def test_to_dict_serialisable(self):
        import json
        m = ToolManifest(name="my_tool", version="1.0.0",
                         description="A test tool for unit testing only.",
                         permissions=frozenset({ToolPermission.NONE}),
                         timeout_seconds=10, input_schema={}, output_schema={})
        d = m.to_dict()
        assert json.dumps(d)  # Should not raise


class TestSessionRequest:
    def test_valid_accepted(self):
        r = SessionRequest(task="Do something useful")
        assert r.task == "Do something useful"
        assert r.session_id  # auto-generated

    def test_empty_task_rejected(self):
        with pytest.raises(ValueError):
            SessionRequest(task="")

    def test_whitespace_only_task_rejected(self):
        with pytest.raises(ValueError):
            SessionRequest(task="   ")

    def test_too_long_task_rejected(self):
        with pytest.raises(ValueError):
            SessionRequest(task="x" * 4097)

    def test_session_id_is_unique(self):
        r1, r2 = SessionRequest(task="task"), SessionRequest(task="task")
        assert r1.session_id != r2.session_id
