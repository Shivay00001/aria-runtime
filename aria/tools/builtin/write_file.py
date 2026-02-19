"""Built-in tool: write_file."""
from __future__ import annotations
from pathlib import Path
from aria.models.types import ToolManifest, ToolPermission

class ToolPlugin:
    manifest = ToolManifest(
        name="write_file",
        version="1.0.0",
        description="Write text content to a file within the allowed workspace.",
        permissions=frozenset({ToolPermission.FILESYSTEM_WRITE}),
        timeout_seconds=10,
        max_memory_mb=64,
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1, "maxLength": 4096},
                "content": {"type": "string"},
                "mode": {"type": "string", "enum": ["overwrite", "append"]},
                "create_dirs": {"type": "boolean"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "bytes_written": {"type": "integer"},
                "mode": {"type": "string"},
            },
            "required": ["path", "bytes_written", "mode"],
            "additionalProperties": False,
        },
    )

    @staticmethod
    def execute(inp: dict) -> dict:
        p = Path(inp["path"])
        content = inp["content"]
        mode = inp.get("mode", "overwrite")
        create_dirs = inp.get("create_dirs", False)
        if create_dirs:
            p.parent.mkdir(parents=True, exist_ok=True)
        elif not p.parent.exists():
            raise FileNotFoundError(f"Parent dir does not exist: {p.parent}")
        write_mode = "a" if mode == "append" else "w"
        with p.open(write_mode, encoding="utf-8") as f:
            f.write(content)
        return {"path": str(p), "bytes_written": len(content.encode()), "mode": mode}
