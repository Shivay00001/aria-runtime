"""Unit tests for sandbox validation functions (no subprocess needed)."""

import pytest

from aria.models.errors import (
    PathTraversalError,
    ToolInputValidationError,
    ToolOutputValidationError,
)
from aria.models.types import ToolPermission
from aria.tools.sandbox import validate_input, validate_output, validate_paths
from tests.conftest import make_manifest


class TestValidatePaths:
    def test_within_allowlist_passes(self, tmp_path):
        m = make_manifest(
            permissions={ToolPermission.FILESYSTEM_READ}, allowed_paths=(str(tmp_path),)
        )
        validate_paths({"path": str(tmp_path / "file.txt")}, m)

    def test_dotdot_traversal_blocked(self, tmp_path):
        m = make_manifest(
            permissions={ToolPermission.FILESYSTEM_READ}, allowed_paths=(str(tmp_path),)
        )
        with pytest.raises(PathTraversalError):
            validate_paths({"path": str(tmp_path) + "/../../../etc/passwd"}, m)

    def test_etc_blocked(self, tmp_path):
        m = make_manifest(
            permissions={ToolPermission.FILESYSTEM_READ}, allowed_paths=(str(tmp_path),)
        )
        with pytest.raises(PathTraversalError):
            validate_paths({"path": "/etc/hosts"}, m)

    def test_no_paths_allowed_skips_check(self):
        m = make_manifest(permissions={ToolPermission.NONE}, allowed_paths=())
        validate_paths({"path": "/etc/hosts"}, m)  # no raise

    def test_non_path_string_value_skipped(self, tmp_path):
        m = make_manifest(
            permissions={ToolPermission.FILESYSTEM_READ}, allowed_paths=(str(tmp_path),)
        )
        validate_paths({"value": "just a regular string"}, m)  # no raise

    def test_nested_subdirectory_allowed(self, tmp_path):
        sub = tmp_path / "a" / "b" / "c"
        sub.mkdir(parents=True)
        m = make_manifest(
            permissions={ToolPermission.FILESYSTEM_READ}, allowed_paths=(str(tmp_path),)
        )
        validate_paths({"path": str(sub / "file.txt")}, m)


class TestValidateInput:
    def test_valid_passes(self):
        m = make_manifest()
        validate_input({"value": "hello"}, m)

    def test_missing_required_raises(self):
        m = make_manifest()
        with pytest.raises(ToolInputValidationError, match="required"):
            validate_input({}, m)

    def test_wrong_type_raises(self):
        m = make_manifest()
        with pytest.raises(ToolInputValidationError):
            validate_input({"value": 42}, m)  # int not string

    def test_extra_field_raises(self):
        m = make_manifest()
        with pytest.raises(ToolInputValidationError):
            validate_input({"value": "ok", "extra": "bad"}, m)

    def test_null_raises(self):
        m = make_manifest()
        with pytest.raises(ToolInputValidationError):
            validate_input({"value": None}, m)


class TestValidateOutput:
    def test_valid_output_passes(self):
        m = make_manifest()
        validate_output({"result": "ok"}, m)

    def test_missing_required_output_raises(self):
        m = make_manifest()
        with pytest.raises(ToolOutputValidationError):
            validate_output({}, m)

    def test_wrong_type_output_raises(self):
        m = make_manifest()
        with pytest.raises(ToolOutputValidationError):
            validate_output({"result": 123}, m)
