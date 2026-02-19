"""aria/kernel/fsm.py — Session finite state machine."""
from __future__ import annotations
from aria.models.errors import InvalidStateTransitionError
from aria.models.types import SessionStatus

_VALID: dict[SessionStatus, set[SessionStatus]] = {
    SessionStatus.IDLE:      {SessionStatus.RUNNING, SessionStatus.CANCELLED},
    SessionStatus.RUNNING:   {SessionStatus.WAITING, SessionStatus.DONE,
                              SessionStatus.FAILED, SessionStatus.CANCELLED},
    SessionStatus.WAITING:   {SessionStatus.RUNNING, SessionStatus.FAILED,
                              SessionStatus.CANCELLED},
    SessionStatus.DONE:      set(),
    SessionStatus.FAILED:    set(),
    SessionStatus.CANCELLED: set(),
}

class SessionFSM:
    def __init__(self, session_id: str) -> None:
        self._state = SessionStatus.IDLE
        self._session_id = session_id
        self._history: list[tuple[SessionStatus, SessionStatus]] = []

    @property
    def state(self) -> SessionStatus:
        return self._state

    @property
    def is_terminal(self) -> bool:
        return self._state in (SessionStatus.DONE, SessionStatus.FAILED, SessionStatus.CANCELLED)

    def transition(self, to_state: SessionStatus) -> None:
        allowed = _VALID.get(self._state, set())
        if to_state not in allowed:
            raise InvalidStateTransitionError(self._state.value, to_state.value)
        self._history.append((self._state, to_state))
        self._state = to_state

    def transition_history(self) -> list[tuple[SessionStatus, SessionStatus]]:
        return list(self._history)
