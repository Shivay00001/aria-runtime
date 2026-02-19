"""Unit tests: circuit breaker state machine."""

from __future__ import annotations

import time

import pytest

from aria.models.errors import CircuitBreakerOpenError
from aria.models.providers.circuit_breaker import CBState, CircuitBreaker


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        assert CircuitBreaker("t").state == CBState.CLOSED

    def test_allows_when_closed(self):
        CircuitBreaker("t").allow_request()  # no raise

    def test_trips_after_threshold(self):
        cb = CircuitBreaker("t", failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CBState.OPEN

    def test_rejects_when_open(self):
        cb = CircuitBreaker("t", failure_threshold=1)
        cb.record_failure()
        with pytest.raises(CircuitBreakerOpenError):
            cb.allow_request()

    def test_success_resets_failures(self):
        cb = CircuitBreaker("t", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.state == CBState.CLOSED

    def test_half_open_after_recovery(self):
        cb = CircuitBreaker("t", failure_threshold=1, recovery_seconds=0.05)
        cb.record_failure()
        time.sleep(0.1)
        assert cb.state == CBState.HALF_OPEN

    def test_half_open_success_closes(self):
        cb = CircuitBreaker("t", failure_threshold=1, recovery_seconds=0.05)
        cb.record_failure()
        time.sleep(0.1)
        _ = cb.state  # trigger half_open
        cb.record_success()
        assert cb.state == CBState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker("t", failure_threshold=1, recovery_seconds=0.05)
        cb.record_failure()
        time.sleep(0.1)
        _ = cb.state  # trigger half_open
        cb.record_failure()
        assert cb.state == CBState.OPEN

    def test_manual_reset(self):
        cb = CircuitBreaker("t", failure_threshold=1)
        cb.record_failure()
        cb.reset()
        assert cb.state == CBState.CLOSED
        cb.allow_request()  # should not raise

    def test_error_has_provider_name(self):
        cb = CircuitBreaker("anthropic", failure_threshold=1)
        cb.record_failure()
        with pytest.raises(CircuitBreakerOpenError) as exc:
            cb.allow_request()
        assert "anthropic" in str(exc.value)

    def test_failures_outside_window_expire(self):
        cb = CircuitBreaker("t", failure_threshold=3, window_seconds=0.05)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.1)  # window expires
        cb.record_failure()
        assert cb.state == CBState.CLOSED  # only 1 in window
