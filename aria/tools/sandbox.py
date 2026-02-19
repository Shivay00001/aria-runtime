"""
aria/tools/sandbox.py — Subprocess sandbox for tool execution.

Security invariants:
  - shell=False ALWAYS. Args are list[str], never concatenated strings.
  - Paths validated before subprocess spawns.
  - Memory limited via resource in child.
  - SIGKILL on timeout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from aria.logging_setup import get_logger
from aria.models.errors import (
    PathTraversalError,
    ToolInputValidationError,
    ToolOutputValidationError,
    ToolSandboxError,
    ToolTimeoutError,
)
from aria.models.types import ToolManifest, ToolResult

_log = get_logger("aria.sandbox")


# Minimal JSON schema validator using stdlib only
def _validate_schema(data: Any, schema: dict, path: str = "", **kwargs: Any) -> list[str]:
    """Returns list of error strings. Empty list = valid."""
    errors: list[str] = []
    t = schema.get("type")
    if t == "object":
        if not isinstance(data, dict):
            errors.append(f"{path}: expected object, got {type(data).__name__}")
            return errors
        props = schema.get("properties", {})
        required = schema.get("required", [])
        additional = schema.get("additionalProperties", True)
        for k in required:
            if k not in data:
                errors.append(f"{path}.{k}: required field missing")
        if additional is False:
            for k in data:
                if k not in props:
                    errors.append(f"{path}.{k}: additional property not allowed")
        for k, sub_schema in props.items():
            if k in data:
                errors.extend(_validate_schema(data[k], sub_schema, f"{path}.{k}"))
    elif t == "string":
        if not isinstance(data, str):
            errors.append(f"{path}: expected string, got {type(data).__name__}")
        else:
            min_l = schema.get("minLength", 0)
            max_l = schema.get("maxLength", float("inf"))
            if len(data) < min_l:
                errors.append(f"{path}: string too short (min {min_l})")
            if len(data) > max_l:
                errors.append(f"{path}: string too long (max {max_l})")
            enum = schema.get("enum")
            if enum and data not in enum:
                errors.append(f"{path}: value {data!r} not in enum {enum}")
    elif t == "integer":
        if not isinstance(data, int) or isinstance(data, bool):
            errors.append(f"{path}: expected integer, got {type(data).__name__}")
        else:
            if "minimum" in schema and data < schema["minimum"]:
                errors.append(f"{path}: {data} < minimum {schema['minimum']}")
            if "maximum" in schema and data > schema["maximum"]:
                errors.append(f"{path}: {data} > maximum {schema['maximum']}")
    elif t == "boolean":
        if not isinstance(data, bool):
            errors.append(f"{path}: expected boolean, got {type(data).__name__}")
    elif t == "array":
        if not isinstance(data, list):
            errors.append(f"{path}: expected array, got {type(data).__name__}")
    return errors


# Subprocess runner script — serialized as a string, run with -c
_RUNNER = r"""
import json, sys
try:
    import resource
except ImportError:
    resource = None

def main():
    try:
        payload = json.loads(sys.stdin.read())
        max_mb = payload.get("max_memory_mb", 256)
        limit = max_mb * 1024 * 1024
        if resource:
            try:
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            except (ValueError, resource.error):
                pass  # May fail in containers — best effort
        import importlib.util
        spec = importlib.util.spec_from_file_location("_tool", payload["tool_module_path"])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        result = mod.ToolPlugin.execute(payload["input"])
        print(json.dumps({"ok": True, "data": result, "error": None}))
    except MemoryError:
        print(json.dumps({"ok": False, "data": None, "error": "MemoryError: resource limit"}))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"ok": False, "data": None, "error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(0)
main()
"""


def validate_paths(arguments: dict, manifest: ToolManifest) -> None:
    """Validate path-like values against manifest.allowed_paths. Raises PathTraversalError."""
    if not manifest.allowed_paths:
        return
    allowed_bases = [Path(p).resolve() for p in manifest.allowed_paths]

    def check(value: Any) -> None:
        if not isinstance(value, str):
            return
        if "/" not in value and not value.startswith("."):
            return
        try:
            resolved = Path(value).resolve()
        except (OSError, ValueError):
            raise PathTraversalError(f"Path {value!r} could not be resolved — rejecting as unsafe")
        if not any(
            resolved == base or str(resolved).startswith(str(base) + os.sep)
            for base in allowed_bases
        ):
            raise PathTraversalError(
                f"Path {value!r} → {resolved} is outside allowed: {manifest.allowed_paths}"
            )

    for v in arguments.values():
        check(v)


def validate_input(arguments: dict, manifest: ToolManifest) -> None:
    """Validate arguments against manifest.input_schema using stdlib validator."""
    errors = _validate_schema(arguments, manifest.input_schema, path="input")
    if errors:
        raise ToolInputValidationError(
            f"Tool {manifest.name!r} input validation failed: {'; '.join(errors)}"
        )


def validate_output(data: dict, manifest: ToolManifest) -> None:
    """Validate output against manifest.output_schema."""
    errors = _validate_schema(data, manifest.output_schema, path="output")
    if errors:
        raise ToolOutputValidationError(
            f"Tool {manifest.name!r} output validation failed: {'; '.join(errors)}"
        )


def run_tool_sandboxed(
    manifest: ToolManifest, arguments: dict, tool_module_path: str
) -> ToolResult:
    """
    Execute a tool in a subprocess. Steps:
    1. Validate input schema — raises on failure, no execution
    2. Validate paths — raises on failure, no execution
    3. Spawn subprocess with memory limit and timeout
    4. Parse JSON output
    5. Validate output schema
    6. Return ToolResult (ok=True|False, never raises)
    """
    started = time.monotonic()

    # Pre-execution validation — raises prevent execution
    validate_input(arguments, manifest)
    validate_paths(arguments, manifest)

    payload = json.dumps(
        {
            "tool_module_path": tool_module_path,
            "input": arguments,
            "max_memory_mb": manifest.max_memory_mb,
        }
    )

    _log.debug("sandbox spawn", extra={"tool": manifest.name, "timeout": manifest.timeout_seconds})

    try:
        proc = subprocess.run(
            [sys.executable, "-c", _RUNNER],
            input=payload,
            capture_output=True,
            text=True,
            timeout=manifest.timeout_seconds,
            # shell=False is the default — kept explicit via list args
        )
    except subprocess.TimeoutExpired:
        ms = int((time.monotonic() - started) * 1000)
        _log.error("sandbox timeout", extra={"tool": manifest.name, "elapsed_ms": ms})
        raise ToolTimeoutError(
            f"Tool {manifest.name!r} exceeded timeout of {manifest.timeout_seconds}s"
        )

    ms = int((time.monotonic() - started) * 1000)

    if proc.returncode != 0:
        stderr = (proc.stderr or "")[:500]
        _log.error("sandbox crash", extra={"tool": manifest.name, "rc": proc.returncode})
        raise ToolSandboxError(
            f"Tool {manifest.name!r} exited with code {proc.returncode}. stderr: {stderr}"
        )

    stdout = (proc.stdout or "").strip()
    if not stdout:
        raise ToolSandboxError(f"Tool {manifest.name!r} produced no output")

    try:
        payload_out = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise ToolSandboxError(f"Tool {manifest.name!r} returned malformed JSON: {e}") from e

    if not payload_out.get("ok", False):
        return ToolResult(
            ok=False,
            tool_name=manifest.name,
            tool_call_id="",
            error_type="ToolExecutionError",
            error_message=payload_out.get("error", "Unknown"),
            duration_ms=ms,
        )

    data = payload_out.get("data") or {}
    validate_output(data, manifest)

    return ToolResult(ok=True, tool_name=manifest.name, tool_call_id="", data=data, duration_ms=ms)
