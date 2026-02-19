"""aria/security/secrets.py — Env-based secrets loader."""
from __future__ import annotations
import os
from aria.models.errors import SecretInvalidError, SecretNotFoundError

class SecretsLoader:
    def __init__(self) -> None:
        self._loaded: dict[str, str] = {}

    def require(self, env_key: str, min_length: int = 8) -> str:
        if env_key in self._loaded:
            return self._loaded[env_key]
        value = os.environ.get(env_key)
        if value is None:
            raise SecretNotFoundError(
                f"Required env var {env_key!r} not set. Set it before starting ARIA.")
        value = value.strip()
        if len(value) < min_length:
            raise SecretInvalidError(
                f"Env var {env_key!r} appears invalid (length {len(value)} < {min_length})")
        self._loaded[env_key] = value
        return value

    def optional(self, env_key: str, default: str = "") -> str:
        if env_key in self._loaded:
            return self._loaded[env_key]
        value = os.environ.get(env_key, default).strip()
        if value:
            self._loaded[env_key] = value
        return value

    @property
    def known_values(self) -> frozenset:
        return frozenset(v for v in self._loaded.values() if len(v) >= 4)


_loader: SecretsLoader | None = None

def get_secrets_loader() -> SecretsLoader:
    global _loader
    if _loader is None:
        _loader = SecretsLoader()
    return _loader
