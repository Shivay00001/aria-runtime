"""Unit tests: SQLite memory and audit layer."""
from __future__ import annotations
import pytest
from aria.models.types import (AuditEvent, KernelConfig, LogLevel, Message,
                                   MessageRole, SessionStatus, StepStatus, StepTrace, StepType)


class TestSQLiteMemory:
    def test_create_and_list_session(self, tmp_db):
        tmp_db.create_session("s1", "Test task", KernelConfig())
        sessions = tmp_db.list_sessions()
        assert any(s["session_id"] == "s1" for s in sessions)

    def test_session_task_stored(self, tmp_db):
        tmp_db.create_session("s1", "My task", KernelConfig())
        s = next(x for x in tmp_db.list_sessions() if x["session_id"] == "s1")
        assert s["task"] == "My task"

    def test_update_session_status(self, tmp_db):
        tmp_db.create_session("s1", "Task", KernelConfig())
        tmp_db.update_session_status("s1", SessionStatus.DONE, 3, 0.05)
        s = next(x for x in tmp_db.list_sessions() if x["session_id"] == "s1")
        assert s["status"] == "DONE"
        assert s["total_steps"] == 3
        assert abs(s["total_cost_usd"] - 0.05) < 1e-6

    def test_append_and_retrieve_messages(self, tmp_db):
        tmp_db.create_session("s1", "T", KernelConfig())
        tmp_db.append_message("s1", Message(role=MessageRole.USER, content="Hello"))
        tmp_db.append_message("s1", Message(role=MessageRole.ASSISTANT, content="Hi"))
        history = tmp_db.get_conversation_history("s1")
        assert len(history) == 2
        assert history[0].role == MessageRole.USER
        assert history[1].content == "Hi"

    def test_kv_set_get(self, tmp_db):
        tmp_db.set_kv("key1", {"data": 42})
        assert tmp_db.get_kv("key1") == {"data": 42}

    def test_kv_namespace_isolation(self, tmp_db):
        tmp_db.set_kv("k", "v1", namespace="ns1")
        tmp_db.set_kv("k", "v2", namespace="ns2")
        assert tmp_db.get_kv("k", namespace="ns1") == "v1"
        assert tmp_db.get_kv("k", namespace="ns2") == "v2"

    def test_kv_missing_returns_none(self, tmp_db):
        assert tmp_db.get_kv("nonexistent_xyz_key") is None

    def test_kv_overwrite(self, tmp_db):
        tmp_db.set_kv("k", "v1")
        tmp_db.set_kv("k", "v2")
        assert tmp_db.get_kv("k") == "v2"

    def test_audit_event_written_and_retrieved(self, tmp_db):
        tmp_db.create_session("s1", "T", KernelConfig())
        tmp_db.write_event(AuditEvent(
            session_id="s1", event_type="test_event",
            level=LogLevel.INFO, payload={"x": 1}))
        events = tmp_db.get_session_events("s1")
        assert any(e["event_type"] == "test_event" for e in events)

    def test_chain_valid_on_fresh_session(self, tmp_db):
        tmp_db.create_session("s1", "T", KernelConfig())
        tmp_db.write_event(AuditEvent(session_id="s1", event_type="e1",
                                       level=LogLevel.INFO, payload={}))
        tmp_db.write_event(AuditEvent(session_id="s1", event_type="e2",
                                       level=LogLevel.INFO, payload={"step": 1}))
        assert tmp_db.verify_chain("s1")

    def test_unknown_session_events_empty(self, tmp_db):
        assert tmp_db.get_session_events("no-such-session") == []

    def test_session_isolation(self, tmp_db):
        tmp_db.create_session("s1", "T1", KernelConfig())
        tmp_db.create_session("s2", "T2", KernelConfig())
        tmp_db.append_message("s1", Message(role=MessageRole.USER, content="A"))
        assert tmp_db.get_conversation_history("s2") == []
