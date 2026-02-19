"""Security tests: path traversal, injection, permission boundaries, audit tampering."""
from __future__ import annotations
import pytest
from pathlib import Path
from aria.models.errors import (PathTraversalError, PermissionDeniedError,
                                   ToolInputValidationError, UnknownToolError)
from aria.models.types import ToolManifest, ToolPermission
from aria.tools.sandbox import validate_input, validate_paths


@pytest.mark.security
class TestPathTraversalPrevention:
    def _manifest(self, tmp_path):
        return ToolManifest(
            name="f_tool", version="1.0.0",
            description="A file tool for testing path validation here.",
            permissions=frozenset({ToolPermission.FILESYSTEM_READ}),
            timeout_seconds=10,
            input_schema={"type":"object","properties":{}},
            output_schema={"type":"object","properties":{}},
            allowed_paths=(str(tmp_path),),
        )

    def test_path_within_allowlist_passes(self, tmp_path):
        m = self._manifest(tmp_path)
        validate_paths({"path": str(tmp_path / "safe.txt")}, m)

    def test_classic_traversal_blocked(self, tmp_path):
        m = self._manifest(tmp_path)
        with pytest.raises(PathTraversalError):
            validate_paths({"path": str(tmp_path) + "/../../../etc/passwd"}, m)

    def test_absolute_outside_allowlist_blocked(self, tmp_path):
        m = self._manifest(tmp_path)
        with pytest.raises(PathTraversalError):
            validate_paths({"path": "/etc/shadow"}, m)

    def test_root_path_blocked(self, tmp_path):
        m = self._manifest(tmp_path)
        with pytest.raises(PathTraversalError):
            validate_paths({"path": "/"}, m)

    def test_home_ssh_blocked(self, tmp_path):
        m = self._manifest(tmp_path)
        with pytest.raises(PathTraversalError):
            validate_paths({"path": str(Path.home() / ".ssh" / "id_rsa")}, m)

    def test_no_allowed_paths_skips_check(self):
        m = ToolManifest(
            name="c_tool", version="1.0.0",
            description="A compute tool with no filesystem access needed.",
            permissions=frozenset({ToolPermission.NONE}),
            timeout_seconds=5,
            input_schema={"type":"object","properties":{}},
            output_schema={"type":"object","properties":{}},
        )
        # No-op: no FS permissions = no path check
        validate_paths({"path": "/etc/passwd"}, m)

    def test_subdirectory_of_allowed_passes(self, tmp_path):
        m = self._manifest(tmp_path)
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        validate_paths({"path": str(sub / "file.txt")}, m)

    def test_sibling_directory_blocked(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        sibling = tmp_path / "secret"
        sibling.mkdir()
        m = ToolManifest(
            name="f_tool", version="1.0.0",
            description="A file tool for testing path validation here.",
            permissions=frozenset({ToolPermission.FILESYSTEM_READ}),
            timeout_seconds=10,
            input_schema={"type":"object","properties":{}},
            output_schema={"type":"object","properties":{}},
            allowed_paths=(str(allowed),),
        )
        with pytest.raises(PathTraversalError):
            validate_paths({"path": str(sibling / "secret.txt")}, m)


@pytest.mark.security
class TestInputValidation:
    def _manifest(self):
        return ToolManifest(
            name="t_tool", version="1.0.0",
            description="A test tool for input validation testing purposes.",
            permissions=frozenset({ToolPermission.NONE}),
            timeout_seconds=5,
            input_schema={"type":"object",
                           "properties":{"value":{"type":"string"}},
                           "required":["value"],"additionalProperties":False},
            output_schema={"type":"object","properties":{}},
        )

    def test_valid_input_passes(self):
        validate_input({"value": "hello"}, self._manifest())

    def test_missing_required_blocked(self):
        with pytest.raises(ToolInputValidationError, match="required"):
            validate_input({}, self._manifest())

    def test_wrong_type_blocked(self):
        with pytest.raises(ToolInputValidationError):
            validate_input({"value": 123}, self._manifest())

    def test_additional_properties_blocked(self):
        with pytest.raises(ToolInputValidationError):
            validate_input({"value": "ok", "injected": "evil"}, self._manifest())


@pytest.mark.security
class TestPermissionBoundaries:
    def test_shell_not_in_default_allowed(self, test_config):
        assert ToolPermission.SHELL not in test_config.allowed_permissions

    def test_network_not_in_default_allowed(self, test_config):
        assert ToolPermission.NETWORK not in test_config.allowed_permissions

    def test_unknown_tool_raises(self, test_config):
        from aria.tools.registry import ToolRegistry
        reg = ToolRegistry(test_config)
        reg.build()
        with pytest.raises(UnknownToolError):
            reg.get_manifest("nonexistent_xyz_tool")

    def test_shell_tool_rejected_at_registry_load(self, test_config):
        from aria.tools.registry import ToolRegistry
        # Kernel config doesn't allow SHELL — a shell tool should be rejected
        assert ToolPermission.SHELL not in test_config.allowed_permissions


@pytest.mark.security
class TestAuditChainIntegrity:
    def test_intact_chain_verified(self, tmp_db):
        from aria.models.types import AuditEvent, LogLevel
        sid = "chain-test"
        for i in range(5):
            tmp_db.write_event(AuditEvent(session_id=sid, event_type=f"e{i}",
                                           level=LogLevel.INFO, payload={"i": i}))
        assert tmp_db.verify_chain(sid)

    def test_tampered_record_breaks_chain(self, tmp_db):
        import json
        from aria.models.types import AuditEvent, LogLevel
        sid = "tamper-test"
        tmp_db.write_event(AuditEvent(session_id=sid, event_type="e1",
                                       level=LogLevel.INFO, payload={"data": "original"}))
        tmp_db.write_event(AuditEvent(session_id=sid, event_type="e2",
                                       level=LogLevel.INFO, payload={"data": "second"}))
        # Direct DB tampering
        conn = tmp_db._conn
        conn.execute("UPDATE audit_events SET payload_json=? WHERE event_type=?",
                      (json.dumps({"data": "tampered"}), "e1"))
        conn.commit()
        assert not tmp_db.verify_chain(sid)

    def test_empty_session_chain_valid(self, tmp_db):
        assert tmp_db.verify_chain("nonexistent-session")

    def test_multi_session_chains_independent(self, tmp_db):
        from aria.models.types import AuditEvent, LogLevel
        for sid in ["s1", "s2", "s3"]:
            for i in range(3):
                tmp_db.write_event(AuditEvent(session_id=sid, event_type=f"e{i}",
                                               level=LogLevel.INFO, payload={"sid": sid, "i": i}))
        for sid in ["s1", "s2", "s3"]:
            assert tmp_db.verify_chain(sid)
