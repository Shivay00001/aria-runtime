"""aria/security/scrubber.py — Log scrubber and injection scanner."""
from __future__ import annotations
import re
from typing import Any
from aria.models.errors import PromptInjectionWarning

_SECRET_KEYS = {"api_key","apikey","secret","password","token","authorization",
                "auth","credential","private_key","access_key"}
_SECRET_RE = re.compile(
    r"(sk-[a-zA-Z0-9\-_]{20,}|sk-ant-[a-zA-Z0-9\-_]{20,}|"
    r"Bearer [a-zA-Z0-9\-_.]{20,}|[A-Za-z0-9+/]{40,}={0,2})"
)
_REDACTED = "[REDACTED]"

def scrub_value(value: Any, known: frozenset) -> Any:
    if isinstance(value, str):
        for s in known:
            if s and s in value:
                value = value.replace(s, _REDACTED)
        return _SECRET_RE.sub(_REDACTED, value)
    if isinstance(value, dict):
        return {
            k: _REDACTED if any(p in k.lower() for p in _SECRET_KEYS)
               else scrub_value(v, known)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub_value(i, known) for i in value]
    return value

def scrub_record(record: dict, known: frozenset) -> dict:
    return {
        k: _REDACTED if any(p in k.lower() for p in _SECRET_KEYS)
           else scrub_value(v, known)
        for k, v in record.items()
    }

_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bignore\s+(previous|above|all|prior)\s+(instructions?|prompts?|rules?)\b", re.I),
    re.compile(r"\byou\s+are\s+now\b", re.I),
    re.compile(r"\bsystem\s*:\s*", re.I),
    re.compile(r"\bdisregard\s+(your|all|the)\b", re.I),
    re.compile(r"\bforget\s+(your|all|previous)\b", re.I),
    re.compile(r"\bnew\s+instructions?\b", re.I),
    re.compile(r"\bjailbreak\b", re.I),
    re.compile(r"\bDAN\b"),
    re.compile(r"\[INST\]|\[/INST\]"),
]

class InjectionScanResult:
    __slots__ = ("clean", "matched_patterns")
    def __init__(self, clean: bool, matched_patterns: list) -> None:
        self.clean = clean
        self.matched_patterns = matched_patterns

def scan_for_injection(text: str) -> InjectionScanResult:
    matched = [p.pattern for p in _INJECTION_PATTERNS if p.search(text)]
    return InjectionScanResult(clean=len(matched) == 0, matched_patterns=matched)

def assert_clean_input(text: str, field_name: str = "input") -> None:
    r = scan_for_injection(text)
    if not r.clean:
        raise PromptInjectionWarning(
            f"Potential prompt injection in {field_name!r}. Patterns: {r.matched_patterns}")

class SecretsScrubberProcessor:
    """Compatible wrapper for tests that use the processor pattern."""
    def __init__(self, known_secrets_getter) -> None:
        self._get = known_secrets_getter
    def __call__(self, logger, method, event_dict: dict) -> dict:
        return scrub_record(event_dict, self._get())
