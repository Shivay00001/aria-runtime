"""FSM tests."""
import pytest
from aria.kernel.fsm import SessionFSM
from aria.models.errors import InvalidStateTransitionError
from aria.models.types import SessionStatus


class TestSessionFSM:
    def test_initial_idle(self):
        assert SessionFSM("s1").state == SessionStatus.IDLE

    def test_idle_to_running(self):
        fsm = SessionFSM("s1")
        fsm.transition(SessionStatus.RUNNING)
        assert fsm.state == SessionStatus.RUNNING

    def test_running_to_done(self):
        fsm = SessionFSM("s1")
        fsm.transition(SessionStatus.RUNNING)
        fsm.transition(SessionStatus.DONE)
        assert fsm.state == SessionStatus.DONE
        assert fsm.is_terminal

    def test_full_tool_call_cycle(self):
        fsm = SessionFSM("s1")
        fsm.transition(SessionStatus.RUNNING)
        fsm.transition(SessionStatus.WAITING)
        fsm.transition(SessionStatus.RUNNING)
        fsm.transition(SessionStatus.DONE)
        assert fsm.is_terminal

    def test_invalid_idle_to_done_raises(self):
        with pytest.raises(InvalidStateTransitionError):
            SessionFSM("s1").transition(SessionStatus.DONE)

    def test_terminal_done_no_exit(self):
        fsm = SessionFSM("s1")
        fsm.transition(SessionStatus.RUNNING)
        fsm.transition(SessionStatus.DONE)
        with pytest.raises(InvalidStateTransitionError):
            fsm.transition(SessionStatus.RUNNING)

    def test_terminal_failed_no_exit(self):
        fsm = SessionFSM("s1")
        fsm.transition(SessionStatus.RUNNING)
        fsm.transition(SessionStatus.FAILED)
        for t in [SessionStatus.IDLE, SessionStatus.RUNNING, SessionStatus.DONE]:
            with pytest.raises(InvalidStateTransitionError):
                fsm.transition(t)

    def test_cancel_from_any_running(self):
        fsm = SessionFSM("s1")
        fsm.transition(SessionStatus.RUNNING)
        fsm.transition(SessionStatus.CANCELLED)
        assert fsm.is_terminal

    def test_cancel_from_waiting(self):
        fsm = SessionFSM("s1")
        fsm.transition(SessionStatus.RUNNING)
        fsm.transition(SessionStatus.WAITING)
        fsm.transition(SessionStatus.CANCELLED)
        assert fsm.is_terminal

    def test_history_recorded(self):
        fsm = SessionFSM("s1")
        fsm.transition(SessionStatus.RUNNING)
        fsm.transition(SessionStatus.WAITING)
        fsm.transition(SessionStatus.RUNNING)
        h = fsm.transition_history()
        assert len(h) == 3
        assert h[0] == (SessionStatus.IDLE, SessionStatus.RUNNING)
        assert h[2] == (SessionStatus.WAITING, SessionStatus.RUNNING)

    def test_error_message_contains_states(self):
        fsm = SessionFSM("s1")
        with pytest.raises(InvalidStateTransitionError) as ei:
            fsm.transition(SessionStatus.FAILED)
        assert "IDLE" in str(ei.value)
        assert "FAILED" in str(ei.value)
