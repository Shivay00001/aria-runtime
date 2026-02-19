"""
aria/models/types.py  —  All shared data contracts.
Uses stdlib dataclasses + enums. No external deps.
Immutable records use frozen=True. Mutable step traces use frozen=False.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC
from enum import Enum
from typing import Any

# ── Enumerations ──────────────────────────────────────────────────────────────


class SessionStatus(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepType(str, Enum):
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    FINAL_ANSWER = "final_answer"


class StepStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class ToolPermission(str, Enum):
    NONE = "none"
    FILESYSTEM_READ = "fs_read"
    FILESYSTEM_WRITE = "fs_write"
    NETWORK = "network"
    SHELL = "shell"


class ActionType(str, Enum):
    TOOL_CALL = "tool_call"
    FINAL_ANSWER = "final_answer"


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# ── Helpers ───────────────────────────────────────────────────────────────────


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


def sha256_str(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ── Validation helpers ────────────────────────────────────────────────────────


def _validate_required(data: dict, *keys: str, class_name: str = "") -> None:
    for k in keys:
        if k not in data or data[k] is None:
            raise ValueError(f"{class_name}: required field {k!r} is missing or None")


def _validate_str(value: Any, field: str, min_len: int = 0, max_len: int = 65535) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be str, got {type(value).__name__}")
    if len(value) < min_len:
        raise ValueError(f"{field} too short (min {min_len})")
    if len(value) > max_len:
        raise ValueError(f"{field} too long (max {max_len})")
    return value


def _validate_int(value: Any, field: str, ge: int = 0, le: int = 2**31) -> int:
    if not isinstance(value, int):
        raise TypeError(f"{field} must be int, got {type(value).__name__}")
    if value < ge:
        raise ValueError(f"{field} must be >= {ge}")
    if value > le:
        raise ValueError(f"{field} must be <= {le}")
    return value


# ── Core data structures ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolManifest:
    name: str
    version: str
    description: str
    permissions: frozenset
    timeout_seconds: int
    input_schema: dict
    output_schema: dict
    max_memory_mb: int = 256
    allowed_paths: tuple = ()

    def __post_init__(self) -> None:
        import re

        if not re.match(r"^[a-z][a-z0-9_]{1,63}$", self.name):
            raise ValueError(f"Tool name {self.name!r} invalid (must match [a-z][a-z0-9_]{{1,63}})")
        if not re.match(r"^\d+\.\d+\.\d+$", self.version):
            raise ValueError(f"Version {self.version!r} invalid (must be semver like 1.0.0)")
        if len(self.description) < 10:
            raise ValueError("description must be at least 10 characters")
        if self.timeout_seconds < 1 or self.timeout_seconds > 300:
            raise ValueError("timeout_seconds must be 1-300")
        if self.max_memory_mb < 32 or self.max_memory_mb > 2048:
            raise ValueError("max_memory_mb must be 32-2048")
        for p in self.allowed_paths:
            from pathlib import Path

            if not Path(p).is_absolute():
                raise ValueError(f"allowed_paths must be absolute, got: {p!r}")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "permissions": [p.value if hasattr(p, "value") else p for p in self.permissions],
            "timeout_seconds": self.timeout_seconds,
            "max_memory_mb": self.max_memory_mb,
            "allowed_paths": list(self.allowed_paths),
        }


@dataclass(frozen=True)
class KernelConfig:
    primary_provider: str = "ollama"
    primary_model: str = "tinyllama"
    fallback_provider: str | None = None
    fallback_model: str | None = None
    max_steps: int = 20
    max_cost_usd: float = 1.0
    max_context_tokens: int = 80000
    allowed_permissions: frozenset = field(
        default_factory=lambda: frozenset(
            {
                ToolPermission.NONE,
                ToolPermission.FILESYSTEM_READ,
                ToolPermission.FILESYSTEM_WRITE,
            }
        )
    )
    plugin_dirs: tuple = ()
    log_level: str = "INFO"
    db_path: str = "~/.aria/aria.db"
    log_path: str = "~/.aria/logs/aria.jsonl"

    def to_json(self) -> str:
        return json.dumps(
            {
                "primary_provider": self.primary_provider,
                "primary_model": self.primary_model,
                "max_steps": self.max_steps,
                "max_cost_usd": self.max_cost_usd,
                "log_level": self.log_level,
            }
        )


@dataclass(frozen=True)
class SessionRequest:
    task: str
    session_id: str = field(default_factory=new_id)
    provider_override: str | None = None
    model_override: str | None = None
    max_steps_override: int | None = None
    dry_run: bool = False

    def __post_init__(self) -> None:
        if not self.task or not self.task.strip():
            raise ValueError("task must not be empty")
        if len(self.task) > 4096:
            raise ValueError("task too long (max 4096 characters)")


@dataclass(frozen=True)
class SessionResult:
    session_id: str
    status: SessionStatus
    answer: str | None
    steps_taken: int
    total_cost_usd: float
    duration_ms: int
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class Message:
    role: MessageRole
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "role": self.role.value,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
        }

    @staticmethod
    def from_dict(d: dict) -> Message:
        return Message(
            role=MessageRole(d["role"]),
            content=d["content"],
            tool_name=d.get("tool_name"),
            tool_call_id=d.get("tool_call_id"),
        )


@dataclass(frozen=True)
class ToolCallRequest:
    tool_name: str
    arguments: dict
    tool_call_id: str = field(default_factory=new_id)


@dataclass(frozen=True)
class RawModelResponse:
    action: ActionType
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    raw_response_hash: str
    tool_call: ToolCallRequest | None = None
    final_answer: str | None = None

    def __post_init__(self) -> None:
        if self.action == ActionType.TOOL_CALL and self.tool_call is None:
            raise ValueError("action=tool_call requires tool_call to be set")
        if self.action == ActionType.FINAL_ANSWER and not self.final_answer:
            raise ValueError("action=final_answer requires final_answer to be non-empty")


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    tool_name: str
    tool_call_id: str
    data: dict | None = None
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: int = 0


@dataclass  # NOT frozen — updated during step lifecycle
class StepTrace:
    session_id: str
    step_number: int
    step_type: StepType
    status: StepStatus
    step_id: str = field(default_factory=new_id)
    prompt_hash: str | None = None
    model_output_hash: str | None = None
    tool_name: str | None = None
    tool_input_json: str | None = None
    tool_output_json: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    started_at: str = field(default_factory=utcnow)
    finished_at: str | None = None
    audit_chain_hash: str = ""


@dataclass(frozen=True)
class AuditEvent:
    session_id: str
    event_type: str
    level: LogLevel
    payload: dict
    event_id: str = field(default_factory=new_id)
    step_id: str | None = None
    chain_hash: str = ""
    timestamp: str = field(default_factory=utcnow)


@dataclass(frozen=True)
class PromptRequest:
    messages: tuple
    system_prompt: str
    tools: tuple
    provider: str
    model: str
    session_id: str
    step_number: int
    temperature: float = 0.0
    max_tokens: int = 4096
