"""aria/memory/sqlite.py — SQLite-backed memory and audit storage."""
from __future__ import annotations
import json
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional
from aria.models.errors import AuditWriteFailureError, MemoryCorruptionError
from aria.models.types import (AuditEvent, KernelConfig, Message, MessageRole,
                                  SessionStatus, StepTrace, sha256_str, utcnow)

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    task            TEXT NOT NULL,
    status          TEXT NOT NULL,
    config_json     TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    total_steps     INTEGER DEFAULT 0,
    total_cost_usd  REAL DEFAULT 0.0,
    error_type      TEXT,
    error_msg       TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    step_id             TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES sessions(session_id),
    step_number         INTEGER NOT NULL,
    step_type           TEXT NOT NULL,
    status              TEXT NOT NULL,
    prompt_hash         TEXT,
    model_output_hash   TEXT,
    tool_name           TEXT,
    tool_input_json     TEXT,
    tool_output_json    TEXT,
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    cost_usd            REAL,
    duration_ms         INTEGER,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    audit_chain_hash    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_steps_session ON steps(session_id, step_number);

CREATE TABLE IF NOT EXISTS kv_memory (
    key        TEXT NOT NULL,
    namespace  TEXT NOT NULL DEFAULT 'default',
    value_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    session_id TEXT,
    PRIMARY KEY (key, namespace)
);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id     TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    step_id      TEXT,
    event_type   TEXT NOT NULL,
    level        TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    chain_hash   TEXT NOT NULL,
    timestamp    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_events(session_id, timestamp);
"""

_SCHEMA_VERSION = 1


# ── Interfaces ────────────────────────────────────────────────────────────────

class MemoryInterface(ABC):
    @abstractmethod
    def create_session(self, session_id: str, task: str, config: KernelConfig) -> None: ...
    @abstractmethod
    def update_session_status(self, session_id: str, status: SessionStatus,
                               total_steps: int, total_cost_usd: float,
                               error_type: Optional[str] = None,
                               error_msg: Optional[str] = None) -> None: ...
    @abstractmethod
    def get_conversation_history(self, session_id: str) -> list[Message]: ...
    @abstractmethod
    def append_message(self, session_id: str, message: Message) -> None: ...
    @abstractmethod
    def set_kv(self, key: str, value: Any, namespace: str = "default",
               session_id: Optional[str] = None) -> None: ...
    @abstractmethod
    def get_kv(self, key: str, namespace: str = "default") -> Any: ...


class AuditInterface(ABC):
    @abstractmethod
    def write_step_start(self, trace: StepTrace) -> None: ...
    @abstractmethod
    def write_step_end(self, trace: StepTrace) -> None: ...
    @abstractmethod
    def write_event(self, event: AuditEvent) -> None: ...
    @abstractmethod
    def get_session_events(self, session_id: str) -> list[dict]: ...
    @abstractmethod
    def verify_chain(self, session_id: str) -> bool: ...


# ── SQLite implementation ─────────────────────────────────────────────────────

class SQLiteStorage(MemoryInterface, AuditInterface):
    def __init__(self, db_path: str) -> None:
        resolved = Path(db_path).expanduser()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._path = resolved
        self._conn = sqlite3.connect(str(resolved), check_same_thread=True)
        self._conn.row_factory = sqlite3.Row
        self._apply_schema()
        self._run_integrity_check()
        # Two separate chain sequences: audit_events and steps (never interleaved)
        self._chain_hashes: dict[str, str] = {}       # for audit_events
        self._step_chain_hashes: dict[str, str] = {}  # for steps table
        self._load_chain_hashes()

    def _apply_schema(self) -> None:
        try:
            self._conn.executescript(_SCHEMA)
            cur = self._conn.execute("SELECT MAX(version) FROM schema_version")
            row = cur.fetchone()
            current = row[0] if row[0] is not None else 0
            if current < _SCHEMA_VERSION:
                self._conn.execute(
                    "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?,?)",
                    (_SCHEMA_VERSION, utcnow()))
                self._conn.commit()
        except sqlite3.Error as e:
            raise MemoryCorruptionError(f"Schema apply failed for {self._path}: {e}") from e

    def _run_integrity_check(self) -> None:
        try:
            r = self._conn.execute("PRAGMA integrity_check").fetchone()
            if r[0] != "ok":
                raise MemoryCorruptionError(f"SQLite integrity check failed: {r[0]}")
        except sqlite3.Error as e:
            raise MemoryCorruptionError(f"Integrity check failed: {e}") from e

    def _load_chain_hashes(self) -> None:
        try:
            rows = self._conn.execute(
                "SELECT session_id, chain_hash FROM audit_events "
                "WHERE (session_id, timestamp) IN ("
                "SELECT session_id, MAX(timestamp) FROM audit_events GROUP BY session_id)"
            ).fetchall()
            for r in rows:
                self._chain_hashes[f"{r['session_id']}:event"] = r["chain_hash"]
        except sqlite3.Error:
            pass

    def _next_step_chain_hash(self, session_id: str, payload: str) -> str:
        prev = self._step_chain_hashes.get(session_id, "0" * 64)
        h = sha256_str(prev + sha256_str(payload))
        self._step_chain_hashes[session_id] = h
        return h

    def _next_chain_hash(self, session_id: str, payload_json: str,
                          namespace: str = "event") -> str:
        key = f"{session_id}:{namespace}"
        prev = self._chain_hashes.get(key, "0" * 64)
        h = sha256_str(prev + sha256_str(payload_json))
        self._chain_hashes[key] = h
        return h

    # ── MemoryInterface ───────────────────────────────────────────────────────

    def create_session(self, session_id: str, task: str, config: KernelConfig) -> None:
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO sessions (session_id,task,status,config_json,started_at) "
                    "VALUES (?,?,?,?,?)",
                    (session_id, task, SessionStatus.IDLE.value, config.to_json(), utcnow()))
        except sqlite3.Error as e:
            raise AuditWriteFailureError(f"create_session failed: {e}") from e

    def update_session_status(self, session_id: str, status: SessionStatus,
                               total_steps: int, total_cost_usd: float,
                               error_type: Optional[str] = None,
                               error_msg: Optional[str] = None) -> None:
        finished = utcnow() if status in (
            SessionStatus.DONE, SessionStatus.FAILED, SessionStatus.CANCELLED) else None
        try:
            with self._conn:
                self._conn.execute(
                    "UPDATE sessions SET status=?,total_steps=?,total_cost_usd=?,"
                    "finished_at=?,error_type=?,error_msg=? WHERE session_id=?",
                    (status.value, total_steps, total_cost_usd,
                     finished, error_type, error_msg, session_id))
        except sqlite3.Error as e:
            raise AuditWriteFailureError(f"update_session_status failed: {e}") from e

    def get_conversation_history(self, session_id: str) -> list[Message]:
        data = self.get_kv(f"conv_{session_id}", namespace="system")
        if data is None:
            return []
        return [Message.from_dict(m) for m in data]

    def append_message(self, session_id: str, message: Message) -> None:
        existing = self.get_conversation_history(session_id)
        existing.append(message)
        self.set_kv(f"conv_{session_id}", [m.to_dict() for m in existing],
                    namespace="system", session_id=session_id)

    def set_kv(self, key: str, value: Any, namespace: str = "default",
               session_id: Optional[str] = None) -> None:
        now = utcnow()
        try:
            with self._conn:
                row = self._conn.execute(
                    "SELECT created_at FROM kv_memory WHERE key=? AND namespace=?",
                    (key, namespace)).fetchone()
                created = row["created_at"] if row else now
                self._conn.execute(
                    "INSERT OR REPLACE INTO kv_memory "
                    "(key,namespace,value_json,created_at,updated_at,session_id) "
                    "VALUES (?,?,?,?,?,?)",
                    (key, namespace, json.dumps(value), created, now, session_id))
        except sqlite3.Error as e:
            raise AuditWriteFailureError(f"set_kv failed: {e}") from e

    def get_kv(self, key: str, namespace: str = "default") -> Any:
        try:
            row = self._conn.execute(
                "SELECT value_json FROM kv_memory WHERE key=? AND namespace=?",
                (key, namespace)).fetchone()
            return json.loads(row["value_json"]) if row else None
        except sqlite3.Error as e:
            raise MemoryCorruptionError(f"get_kv failed: {e}") from e

    # ── AuditInterface ────────────────────────────────────────────────────────

    def write_step_start(self, trace: StepTrace) -> None:
        payload = json.dumps({"step_id": trace.step_id, "status": "started"})
        chain = self._next_step_chain_hash(trace.session_id, payload)
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO steps (step_id,session_id,step_number,step_type,status,"
                    "prompt_hash,tool_name,tool_input_json,started_at,audit_chain_hash) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (trace.step_id, trace.session_id, trace.step_number,
                     trace.step_type.value, trace.status.value,
                     trace.prompt_hash, trace.tool_name, trace.tool_input_json,
                     trace.started_at, chain))
        except sqlite3.Error as e:
            raise AuditWriteFailureError(f"write_step_start failed: {e}") from e

    def write_step_end(self, trace: StepTrace) -> None:
        payload = json.dumps({"step_id": trace.step_id, "status": trace.status.value,
                               "hash": trace.model_output_hash})
        chain = self._next_chain_hash(trace.session_id, payload, namespace="step")
        try:
            with self._conn:
                self._conn.execute(
                    "UPDATE steps SET status=?,model_output_hash=?,tool_output_json=?,"
                    "input_tokens=?,output_tokens=?,cost_usd=?,duration_ms=?,"
                    "finished_at=?,audit_chain_hash=? WHERE step_id=?",
                    (trace.status.value, trace.model_output_hash, trace.tool_output_json,
                     trace.input_tokens, trace.output_tokens, trace.cost_usd,
                     trace.duration_ms, trace.finished_at, chain, trace.step_id))
        except sqlite3.Error as e:
            raise AuditWriteFailureError(f"write_step_end failed: {e}") from e

    def write_event(self, event: AuditEvent) -> None:
        payload_json = json.dumps(event.payload)
        chain = self._next_chain_hash(event.session_id, payload_json)
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO audit_events "
                    "(event_id,session_id,step_id,event_type,level,"
                    "payload_json,chain_hash,timestamp) VALUES (?,?,?,?,?,?,?,?)",
                    (event.event_id, event.session_id, event.step_id,
                     event.event_type, event.level.value,
                     payload_json, chain, event.timestamp))
        except sqlite3.Error as e:
            raise AuditWriteFailureError(f"write_event failed: {e}") from e

    def get_session_events(self, session_id: str) -> list[dict]:
        try:
            rows = self._conn.execute(
                "SELECT * FROM audit_events WHERE session_id=? ORDER BY timestamp",
                (session_id,)).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            raise MemoryCorruptionError(f"get_session_events failed: {e}") from e

    def list_sessions(self, limit: int = 20) -> list[dict]:
        try:
            rows = self._conn.execute(
                "SELECT session_id,task,status,started_at,finished_at,"
                "total_steps,total_cost_usd,error_type "
                "FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            raise MemoryCorruptionError(f"list_sessions failed: {e}") from e

    def verify_chain(self, session_id: str) -> bool:
        try:
            rows = self._conn.execute(
                "SELECT payload_json,chain_hash FROM audit_events "
                "WHERE session_id=? ORDER BY timestamp", (session_id,)).fetchall()
            if not rows:
                return True
            prev = "0" * 64
            for r in rows:
                expected = sha256_str(prev + sha256_str(r["payload_json"]))
                if expected != r["chain_hash"]:
                    return False
                prev = r["chain_hash"]
            # Audit chain is intact
            return True
        except sqlite3.Error:
            return False

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass
