"""aria/models/providers/circuit_breaker.py — Per-provider circuit breaker."""

from __future__ import annotations

import time
from enum import Enum

from aria.models.errors import CircuitBreakerOpenError


class CBState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(
        self,
        provider: str,
        failure_threshold: int = 3,
        window_seconds: float = 60.0,
        recovery_seconds: float = 120.0,
    ) -> None:
        self.provider = provider
        self._threshold = failure_threshold
        self._window = window_seconds
        self._recovery = recovery_seconds
        self._state = CBState.CLOSED
        self._failures: list[float] = []
        self._opened_at: float | None = None

    @property
    def state(self) -> CBState:
        if self._state == CBState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self._recovery:
                self._state = CBState.HALF_OPEN
        return self._state

    def allow_request(self) -> None:
        if self.state == CBState.OPEN:
            raise CircuitBreakerOpenError(self.provider)

    def record_success(self) -> None:
        self._failures.clear()
        if self._state == CBState.HALF_OPEN:
            self._state = CBState.CLOSED
            self._opened_at = None

    def record_failure(self) -> None:
        now = time.monotonic()
        if self._state == CBState.HALF_OPEN:
            self._opened_at = now
            self._state = CBState.OPEN
            return
        self._failures = [t for t in self._failures if now - t < self._window]
        self._failures.append(now)
        if len(self._failures) >= self._threshold:
            self._opened_at = now
            self._state = CBState.OPEN
            self._failures.clear()

    def reset(self) -> None:
        self._state = CBState.CLOSED
        self._failures.clear()
        self._opened_at = None

    def status_dict(self) -> dict:
        return {
            "provider": self.provider,
            "state": self.state.value,
            "failure_count": len(self._failures),
            "opened_at": self._opened_at,
        }
