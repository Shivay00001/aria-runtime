"""aria/logging_setup.py — Structured JSON logging via stdlib."""
from __future__ import annotations
import json
import logging
import sys
from pathlib import Path
from typing import Any
from aria.security.secrets import get_secrets_loader

class _JSONFormatter(logging.Formatter):
    """Formats every log record as a single JSON line. Scrubs secrets."""

    def format(self, record: logging.LogRecord) -> str:
        # PURE SAFE MODE: Only extract explicitly known safe fields + 'extra' dict if possible
        # We avoid iterating record.__dict__ entirely to prevent the "overwrite 'name'" error
        
        data: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }

        # In Python logging, 'extra' fields are merged into __dict__. 
        # Since we can't safely iterate __dict__ due to the error, we will ONLY include fields 
        # that we know might be there if we put them there (e.g. via our own logging calls).
        # This is a trade-off: we might miss some random extra fields from library logs, 
        # but we guarantee stability.
        
        # Helper to safely get from record without triggering descriptors/properties
        def safe_get(key):
            try:
                return getattr(record, key, None)
            except Exception:
                return None

        # Common extra keys we use in ARIA
        known_extras = [
            "session_id", "step_id", "trace_id", "tool", "tool_call_id", 
            "provider", "model", "cost_usd", "duration_ms", "tokens",
            "input_tokens", "output_tokens", "error", "error_type", "ok", 
            "attempt", "trace"
        ]
        
        for k in known_extras:
            val = safe_get(k)
            if val is not None:
                data[k] = val

        if record.exc_info:
            # Format exception info if present
            try:
                if not record.exc_text:
                    record.exc_text = self.formatException(record.exc_info)
                data["exc_info"] = record.exc_text
            except Exception:
                data["exc_info"] = str(record.exc_info)

        # Scrub secrets
        try:
            secrets = get_secrets_loader().known_values
            # simple string replacement-based scrub to avoid recursion issues
            json_str = json.dumps(data, default=str)
            for secret in secrets:
                 if secret and len(secret) > 4:
                     json_str = json_str.replace(secret, "********")
            return json_str
        except Exception:
             return json.dumps(data, default=str)


_configured = False

def configure_logging(log_level: str = "INFO", log_path: str | None = None) -> None:
    global _configured
    if _configured:
        return
    _configured = True

    level = getattr(logging, log_level.upper(), logging.INFO)
    formatter = _JSONFormatter()

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_path:
        p = Path(log_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(str(p), mode="a", encoding="utf-8"))

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    for h in handlers:
        h.setFormatter(formatter)
        root.addHandler(h)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
