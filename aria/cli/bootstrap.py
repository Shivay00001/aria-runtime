"""aria/cli/bootstrap.py — Dependency wiring. One place for concrete implementations."""
from __future__ import annotations
import os
from pathlib import Path
from aria.kernel.kernel import AgentKernel
from aria.logging_setup import configure_logging
from aria.memory.sqlite import SQLiteStorage
from aria.models.types import KernelConfig, ToolPermission
from aria.tools.registry import ToolRegistry


def load_config(config_path: str = "") -> KernelConfig:
    """Build KernelConfig from defaults + ARIA_* env vars."""
    kw: dict = {}
    _e = os.environ.get
    if _e("ARIA_PRIMARY_PROVIDER"): kw["primary_provider"] = _e("ARIA_PRIMARY_PROVIDER")
    if _e("ARIA_PRIMARY_MODEL"):    kw["primary_model"] = _e("ARIA_PRIMARY_MODEL")
    if _e("ARIA_MAX_STEPS"):
        try: kw["max_steps"] = int(_e("ARIA_MAX_STEPS"))  # type: ignore
        except ValueError: pass
    if _e("ARIA_MAX_COST_USD"):
        try: kw["max_cost_usd"] = float(_e("ARIA_MAX_COST_USD"))  # type: ignore
        except ValueError: pass
    if _e("ARIA_DB_PATH"):  kw["db_path"] = _e("ARIA_DB_PATH")
    if _e("ARIA_LOG_PATH"): kw["log_path"] = _e("ARIA_LOG_PATH")
    return KernelConfig(**kw)


def build_kernel(config: KernelConfig,
                 log_level: str = "INFO") -> tuple[AgentKernel, SQLiteStorage]:
    """Wire up all kernel dependencies. Returns (kernel, storage)."""
    configure_logging(log_level=log_level, log_path=config.log_path)

    db_path = str(Path(config.db_path).expanduser())
    storage = SQLiteStorage(db_path)

    registry = ToolRegistry(config)
    registry.build()

    providers: dict = {}
    primary = config.primary_provider
    if primary == "anthropic" or config.fallback_provider == "anthropic":
        from aria.models.providers.anthropic_provider import AnthropicProvider
        providers["anthropic"] = AnthropicProvider()

    if primary == "ollama" or config.fallback_provider == "ollama":
        from aria.models.providers.ollama_provider import OllamaProvider
        providers["ollama"] = OllamaProvider()

    if not providers:
        raise ValueError(f"No provider available. primary_provider={primary!r}")

    from aria.models.router import ModelRouter
    router = ModelRouter(providers=providers, audit_writer=storage)

    kernel = AgentKernel(
        model_router=router, tool_registry=registry,
        memory=storage, audit=storage, config=config,
    )
    return kernel, storage
