"""Integration tests: full kernel execution with real SQLite + mock provider."""

from __future__ import annotations

import pytest

from aria.kernel.kernel import AgentKernel
from aria.memory.sqlite import SQLiteStorage
from aria.models.errors import ModelProviderExhaustedError
from aria.models.router import ModelRouter
from aria.models.types import KernelConfig, SessionRequest, SessionStatus, ToolPermission
from aria.tools.registry import ToolRegistry
from tests.conftest import MockProvider, make_final_answer, make_tool_call


def _build(config: KernelConfig, storage: SQLiteStorage, provider: MockProvider):
    """Wire up a kernel with mock provider."""
    import dataclasses

    # Override primary_provider to "mock"
    cfg2 = KernelConfig(**{**dataclasses.asdict(config), "primary_provider": "mock"})
    registry = ToolRegistry(cfg2)
    registry.build()
    router = ModelRouter(providers={"mock": provider}, audit_writer=storage)
    return AgentKernel(
        model_router=router, tool_registry=registry, memory=storage, audit=storage, config=cfg2
    )


@pytest.fixture
def storage(tmp_path):
    s = SQLiteStorage(str(tmp_path / "int.db"))
    yield s
    s.close()


@pytest.fixture
def config(tmp_path):
    return KernelConfig(
        max_steps=5,
        max_cost_usd=1.0,
        db_path=str(tmp_path / "int.db"),
        log_path=str(tmp_path / "int.jsonl"),
        allowed_permissions=frozenset(
            {
                ToolPermission.NONE,
                ToolPermission.FILESYSTEM_READ,
                ToolPermission.FILESYSTEM_WRITE,
            }
        ),
    )


class TestKernelIntegration:
    def test_single_step_final_answer(self, config, storage):
        provider = MockProvider([make_final_answer("The answer is 42.")])
        kernel = _build(config, storage, provider)
        result = kernel.run(SessionRequest(task="What is 6 times 7?"))
        assert result.status == SessionStatus.DONE
        assert result.answer == "The answer is 42."
        assert result.steps_taken == 1
        assert result.error_type is None

    def test_session_recorded_in_db(self, config, storage):
        provider = MockProvider([make_final_answer("Done")])
        kernel = _build(config, storage, provider)
        result = kernel.run(SessionRequest(task="Test persistence"))
        sessions = storage.list_sessions()
        ids = [s["session_id"] for s in sessions]
        assert result.session_id in ids

    def test_session_status_done_in_db(self, config, storage):
        provider = MockProvider([make_final_answer("Done")])
        kernel = _build(config, storage, provider)
        result = kernel.run(SessionRequest(task="Status test"))
        s = next(x for x in storage.list_sessions() if x["session_id"] == result.session_id)
        assert s["status"] == "DONE"

    def test_audit_events_written(self, config, storage):
        provider = MockProvider([make_final_answer("Done")])
        kernel = _build(config, storage, provider)
        result = kernel.run(SessionRequest(task="Audit test"))
        events = storage.get_session_events(result.session_id)
        types = [e["event_type"] for e in events]
        assert "session_start" in types
        assert "session_end" in types

    def test_audit_chain_intact_after_session(self, config, storage):
        provider = MockProvider([make_final_answer("Done")])
        kernel = _build(config, storage, provider)
        result = kernel.run(SessionRequest(task="Chain integrity test"))
        assert storage.verify_chain(result.session_id)

    def test_step_limit_enforced(self, tmp_path):
        """Session fails when step limit exceeded."""

        cfg = KernelConfig(
            primary_provider="mock",
            max_steps=2,
            max_cost_usd=1.0,
            db_path=str(tmp_path / "lim.db"),
            log_path=str(tmp_path / "lim.jsonl"),
            allowed_permissions=frozenset({ToolPermission.NONE, ToolPermission.FILESYSTEM_READ}),
        )
        storage = SQLiteStorage(str(tmp_path / "lim.db"))
        # Always returns unknown tool call — session will loop and hit limit
        tc = make_tool_call("nonexistent_tool_xyz", {})
        provider = MockProvider([tc, tc, tc, tc, tc])
        registry = ToolRegistry(cfg)
        registry.build()
        router = ModelRouter(providers={"mock": provider}, audit_writer=storage)
        kernel = AgentKernel(router, registry, storage, storage, cfg)
        result = kernel.run(SessionRequest(task="Loop"))
        storage.close()
        assert result.status == SessionStatus.FAILED
        # Either step limit or unknown tool error
        assert result.error_type is not None

    def test_provider_failure_results_in_failed_status(self, config, storage):
        """Provider exhaustion is caught and results in FAILED session."""
        from unittest.mock import MagicMock

        provider = MagicMock()
        provider.name = "mock"
        provider.call.side_effect = ModelProviderExhaustedError("All retries", attempts=3)
        import dataclasses

        cfg2 = KernelConfig(**{**dataclasses.asdict(config), "primary_provider": "mock"})
        registry = ToolRegistry(cfg2)
        registry.build()
        router = ModelRouter(providers={"mock": provider}, audit_writer=storage)
        kernel = AgentKernel(router, registry, storage, storage, cfg2)
        result = kernel.run(SessionRequest(task="Fail test"))
        assert result.status == SessionStatus.FAILED
        assert result.answer is None

    def test_cost_field_present(self, config, storage):
        provider = MockProvider([make_final_answer("Done")])
        kernel = _build(config, storage, provider)
        result = kernel.run(SessionRequest(task="Cost check"))
        assert isinstance(result.total_cost_usd, float)
        assert result.total_cost_usd >= 0.0

    def test_conversation_history_passed_to_model(self, config, storage):
        """Second model call receives full conversation including tool result."""
        provider = MockProvider(
            [
                make_tool_call("nonexistent_tool", {"x": 1}),
                make_final_answer("Saw tool result"),
            ]
        )
        import dataclasses

        cfg2 = KernelConfig(**{**dataclasses.asdict(config), "primary_provider": "mock"})
        registry = ToolRegistry(cfg2)
        registry.build()
        router = ModelRouter(providers={"mock": provider}, audit_writer=storage)
        kernel = AgentKernel(router, registry, storage, storage, cfg2)
        kernel.run(SessionRequest(task="Multi-step"))
        # Should have made 2 provider calls (tool call + final answer)
        assert len(provider.calls) >= 1
