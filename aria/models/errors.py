"""aria/models/errors.py — Typed exception hierarchy."""
from __future__ import annotations


class ARIAError(Exception): pass

class ValidationError(ARIAError): pass
class ToolInputValidationError(ValidationError): pass
class ToolOutputValidationError(ValidationError): pass
class ModelOutputValidationError(ValidationError): pass
class ManifestValidationError(ValidationError): pass

class SecurityError(ARIAError): pass
class PathTraversalError(SecurityError): pass
class PermissionDeniedError(SecurityError): pass
class PromptInjectionWarning(ARIAError): pass
class UnknownToolError(SecurityError): pass
class SecretNotFoundError(ARIAError): pass
class SecretInvalidError(ARIAError): pass

class SandboxError(ARIAError): pass
class ToolTimeoutError(SandboxError): pass
class ToolSandboxError(SandboxError): pass

class ProviderError(ARIAError): pass

class ModelProviderError(ProviderError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

class ModelRateLimitError(ProviderError): pass
class ModelTimeoutError(ProviderError): pass

class ModelProviderExhaustedError(ProviderError):
    def __init__(self, message: str, attempts: int) -> None:
        super().__init__(message)
        self.attempts = attempts

class CircuitBreakerOpenError(ProviderError):
    def __init__(self, provider: str) -> None:
        super().__init__(f"Circuit breaker OPEN for provider {provider!r}")
        self.provider = provider

class StateMachineError(ARIAError): pass

class InvalidStateTransitionError(StateMachineError):
    def __init__(self, from_state: str, to_state: str) -> None:
        super().__init__(f"Invalid FSM transition: {from_state} → {to_state}")

class LimitError(ARIAError): pass
class StepLimitExceededError(LimitError): pass
class CostBudgetExceededError(LimitError): pass

class StorageError(ARIAError): pass
class AuditWriteFailureError(StorageError): pass
class MemoryCorruptionError(StorageError): pass

class ConfigError(ARIAError): pass
