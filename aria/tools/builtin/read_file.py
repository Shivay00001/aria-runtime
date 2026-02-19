"""Built-in tool: read_file."""

from __future__ import annotations

from pathlib import Path

from aria.models.types import ToolManifest, ToolPermission


class ToolPlugin:
    manifest = ToolManifest(
        name="read_file",
        version="1.0.0",
        description="Read the text contents of a file within the allowed workspace.",
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
    )

    @staticmethod
    def execute(inp: dict) -> dict:
        p = Path(inp["path"])
        max_bytes = inp.get("max_bytes", 1_048_576)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {p}")
        if not p.is_file():
            raise ValueError(f"Not a regular file: {p}")
        size = p.stat().st_size
        truncated = size > max_bytes
        with p.open("r", encoding="utf-8", errors="replace") as f:
            content = f.read(max_bytes)
        return {"content": content, "size_bytes": size, "truncated": truncated}
