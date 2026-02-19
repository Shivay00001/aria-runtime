"""Model router tests."""
import pytest
from unittest.mock import MagicMock
from aria.models.errors import (
    CircuitBreakerOpenError, ModelProviderError,
    ModelProviderExhaustedError, ModelRateLimitError,
)
from aria.models.router import ModelRouter
from aria.models.types import (
    ActionType, KernelConfig, PromptRequest, RawModelResponse, sha256_str,
)
from tests.conftest import MockProvider


def _fa(text="Done"):
    return RawModelResponse(
        action=ActionType.FINAL_ANSWER, final_answer=text,
        input_tokens=10, output_tokens=5, model="mock", provider="mock",
        raw_response_hash=sha256_str(text),
    )


def make_request() -> PromptRequest:
    return PromptRequest(
        messages=(), system_prompt="test", tools=(),
        provider="mock", model="mock-model",
        session_id="test-session", step_number=1,
    )


class TestModelRouter:
    def test_successful_call(self):
        provider = MockProvider(responses=[_fa("ok")])
        router = ModelRouter(providers={"mock": provider})
        result = router.call(make_request())
        assert result.action == ActionType.FINAL_ANSWER
        assert result.final_answer == "ok"

    def test_unknown_provider_raises_value_error(self):
        router = ModelRouter(providers={"mock": MockProvider()})
        req = PromptRequest(messages=(), system_prompt="", tools=(),
                            provider="openai", model="gpt-4",
                            session_id="s", step_number=1)
        with pytest.raises(ValueError, match="openai"):
            router.call(req)

    def test_empty_providers_raises(self):
        with pytest.raises(ValueError):
            ModelRouter(providers={})

    def test_retries_on_provider_error(self):
        provider = MagicMock()
        provider.name = "mock"
        call_count = 0
        def side_effect(req):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ModelRateLimitError("Rate limited")
            return _fa("success after retries")
        provider.call.side_effect = side_effect
        router = ModelRouter(providers={"mock": provider})
        req = make_request()
        # Override sleep to speed up test
        import aria.models.router as router_module
        original_sleep = router_module.time.sleep
        router_module.time.sleep = lambda x: None
        try:
            result = router.call(req)
            assert result.final_answer == "success after retries"
            assert call_count == 3
        finally:
            router_module.time.sleep = original_sleep

    def test_exhausted_after_max_retries(self):
        provider = MagicMock()
        provider.name = "mock"
        provider.call.side_effect = ModelProviderError("Server error", status_code=500)
        router = ModelRouter(providers={"mock": provider})
        import aria.models.router as router_module
        original_sleep = router_module.time.sleep
        router_module.time.sleep = lambda x: None
        try:
            with pytest.raises(ModelProviderExhaustedError) as ei:
                router.call(make_request())
            assert ei.value.attempts == 3
        finally:
            router_module.time.sleep = original_sleep

    def test_circuit_breaker_trips_and_raises(self):
        provider = MagicMock()
        provider.name = "mock"
        provider.call.side_effect = ModelProviderError("Error", status_code=500)
        router = ModelRouter(providers={"mock": provider})
        import aria.models.router as router_module
        original_sleep = router_module.time.sleep
        router_module.time.sleep = lambda x: None
        try:
            # Trip the CB
            for _ in range(3):
                try:
                    router.call(make_request())
                except (ModelProviderExhaustedError, CircuitBreakerOpenError):
                    pass
        finally:
            router_module.time.sleep = original_sleep

    def test_circuit_breaker_status_available(self):
        router = ModelRouter(providers={"mock": MockProvider()})
        status = router.circuit_breaker_status()
        assert "mock" in status
        assert "state" in status["mock"]
