"""aria/tools/registry.py — Tool registry: load, validate, enforce permissions."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from aria.logging_setup import get_logger
from aria.models.errors import ManifestValidationError, PermissionDeniedError, UnknownToolError
from aria.models.types import KernelConfig, ToolManifest

_log = get_logger("aria.registry")


class ToolRegistry:
    """Immutable after build(). All validation happens in build()."""

    def __init__(self, config: KernelConfig) -> None:
        self._config = config
        self._manifests: dict[str, ToolManifest] = {}
        self._executors: dict[str, Any] = {}
        self._module_paths: dict[str, str] = {}

    def build(self, extra_plugin_dirs: list | None = None) -> None:
        from aria.tools.builtin import BUILTIN_TOOLS

        for cls in BUILTIN_TOOLS:
            self._register(cls, module_path=_module_path(cls))
        dirs = [Path(d).expanduser() for d in self._config.plugin_dirs]
        if extra_plugin_dirs:
            dirs.extend(extra_plugin_dirs)
        for d in dirs:
            if not d.is_dir():
                raise ManifestValidationError(f"plugin_dir {d!r} does not exist")
            self._load_dir(d)
        _log.info("registry built", extra={"tools": list(self._manifests)})

    def _load_dir(self, plugin_dir: Path) -> None:
        for py_file in sorted(plugin_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            spec = importlib.util.spec_from_file_location(f"aria_plugin_{py_file.stem}", py_file)
            if spec is None or spec.loader is None:
                raise ManifestValidationError(f"Cannot load plugin from {py_file}")
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as e:
                raise ManifestValidationError(f"Plugin {py_file} raised on load: {e}") from e
            cls = getattr(module, "ToolPlugin", None)
            if cls is None:
                raise ManifestValidationError(f"Plugin {py_file} has no ToolPlugin class")
            self._register(cls, module_path=str(py_file))

    def _register(self, cls: Any, module_path: str) -> None:
        manifest = getattr(cls, "manifest", None)
        if not isinstance(manifest, ToolManifest):
            raise ManifestValidationError(f"{cls.__name__}.manifest must be a ToolManifest")
        if not callable(getattr(cls, "execute", None)):
            raise ManifestValidationError(f"Tool {manifest.name!r} has no execute() method")
        disallowed = manifest.permissions - self._config.allowed_permissions
        if disallowed:
            raise PermissionDeniedError(
                f"Tool {manifest.name!r} requires {disallowed} "
                f"not in allowed_permissions: {self._config.allowed_permissions}"
            )
        if manifest.name in self._manifests:
            raise ManifestValidationError(f"Duplicate tool name: {manifest.name!r}")
        self._manifests[manifest.name] = manifest
        self._executors[manifest.name] = cls
        self._module_paths[manifest.name] = module_path
        _log.info("tool registered", extra={"tool_name": manifest.name})

    def get_manifest(self, name: str) -> ToolManifest:
        if name not in self._manifests:
            raise UnknownToolError(
                f"Tool {name!r} not registered. Available: {list(self._manifests)}"
            )
        return self._manifests[name]

    def get_executor(self, name: str) -> Any:
        if name not in self._executors:
            raise UnknownToolError(f"Tool executor {name!r} not found")
        return self._executors[name]

    def get_module_path(self, name: str) -> str:
        return self._module_paths.get(name, "")

    @property
    def all_manifests(self) -> tuple:
        return tuple(self._manifests.values())

    def has_tool(self, name: str) -> bool:
        return name in self._manifests


def _module_path(cls: Any) -> str:
    import inspect

    try:
        return inspect.getfile(cls)
    except (TypeError, OSError):
        return ""
