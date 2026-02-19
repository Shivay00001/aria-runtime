"""aria/models/router.py — Model router: retry, backoff, circuit breaker."""
from __future__ import annotations
import random
import time
from aria.logging_setup import get_logger
from aria.models.errors import (
    CircuitBreakerOpenError, ModelOutputValidationError,
    ModelProviderError, ModelProviderExhaustedError,
    ModelRateLimitError, ModelTimeoutError,
)
from aria.models.providers.base import ModelProviderInterface
from aria.models.providers.circuit_breaker import CircuitBreaker
from aria.models.types import AuditEvent, LogLevel, PromptRequest, RawModelResponse

_log = get_logger("aria.router")
_RETRYABLE = (ModelProviderError, ModelRateLimitError, ModelTimeoutError)
_MAX_RETRIES = 3
_BASE_BACKOFF = 2.0
_MAX_BACKOFF = 30.0


class ModelRouter:
    def __init__(self, providers: dict[str, ModelProviderInterface],
                 audit_writer=None) -> None:
        if not providers:
            raise ValueError("ModelRouter requires at least one provider")
        self._providers = providers
        self._breakers = {n: CircuitBreaker(provider=n) for n in providers}
        self._audit = audit_writer

    def call(self, request: PromptRequest) -> RawModelResponse:
        provider = self._providers.get(request.provider)
        if provider is None:
            raise ValueError(f"Provider {request.provider!r} not registered. "
                             f"Available: {list(self._providers)}")

        breaker = self._breakers[request.provider]
        last_err: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                breaker.allow_request()
            except CircuitBreakerOpenError:
                _log.warning("circuit_breaker_open", extra={
                    "provider": request.provider, "session_id": request.session_id})
                raise

            try:
                _log.info("model_call_attempt", extra={
                    "provider": request.provider, "model": request.model,
                    "attempt": attempt, "session_id": request.session_id})
                result = provider.call(request)
                breaker.record_success()
                return result

            except ModelOutputValidationError:
                breaker.record_failure()
                raise

            except _RETRYABLE as exc:  # type: ignore[misc]
                breaker.record_failure()
                last_err = exc
                _log.warning("model_call_retry", extra={
                    "attempt": attempt, "error_type": type(exc).__name__,
                    "error": str(exc), "session_id": request.session_id})
                if attempt < _MAX_RETRIES:
                    delay = min(_BASE_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0, 1),
                                _MAX_BACKOFF)
                    time.sleep(delay)

        raise ModelProviderExhaustedError(
            f"Provider {request.provider!r} failed after {_MAX_RETRIES} attempts. "
            f"Last: {last_err}", attempts=_MAX_RETRIES)

    def circuit_breaker_status(self) -> dict:
        return {n: cb.status_dict() for n, cb in self._breakers.items()}
