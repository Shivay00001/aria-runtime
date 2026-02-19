# ARIA — Agent Runtime for Intelligent Automation

**Local-first. Secure by default. Fully auditable.**

> *Build smallest possible correct system. Fail loudly, never silently.*

---

## What This Is

ARIA is a production-grade, single-agent AI execution runtime. It is not a framework, not a platform, and not a library. It is a complete, runnable system with explicit contracts between every component.

**Design philosophy:**

- Fail loudly, never silently
- Explicit state transitions only (typed FSM)
- Every side effect logged before and after
- All boundaries validate input/output against schemas
- Synchronous core — no async race conditions
- Security by default, least privilege
- No vendor lock-in (abstraction layers)

---

## Quick Start

```bash
# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Run a task
python -m aria.cli.main run --task "What are the first 5 prime numbers?"

# See registered tools
python -m aria.cli.main tools list

# View audit log
python -m aria.cli.main audit list
python -m aria.cli.main audit export --session-id <id>
python -m aria.cli.main audit verify --session-id <id>
```

---

## Architecture

```text
CLI Layer (thin boundary, zero business logic)
    │
    ▼
AgentKernel (orchestrator — sequences, delegates, enforces limits)
    │
    ├── SessionFSM      (IDLE→RUNNING→WAITING→DONE|FAILED|CANCELLED)
    ├── ModelRouter     (retry + circuit breaker per provider)
    ├── ToolRegistry    (manifest validation, permission enforcement)
    ├── SandboxRunner   (subprocess isolation, path traversal prevention)
    └── SQLiteStorage   (memory + audit, WAL mode, chain hashing)
```

### State Machine

```text
IDLE ──► RUNNING ──► WAITING ──► RUNNING
  │          │           │
  │          ▼           ▼
  └────► CANCELLED    FAILED
             ▲
         RUNNING ──► DONE
```

Every transition is validated. Invalid transitions raise `InvalidStateTransitionError` immediately.

---

## Security Architecture

| Threat                   | Mitigation                                                    |
| :----------------------- | :------------------------------------------------------------ |
| Malicious model output   | Schema validation before any tool execution                   |
| Path traversal           | `Path.resolve()` + allowlist check before subprocess          |
| Prompt injection         | Syntactic scanner + structural separation + schema validation |
| Command injection        | `shell=False` always; args as `list[str]`, never concatenated |
| API key leaks            | Secrets scrubber in every log record — cannot be bypassed     |
| Malicious plugins        | Subprocess isolation + permission boundaries enforced at load |
| Audit tampering          | SHA-256 chain hash across all audit records                   |

**Security invariants that must never be broken:**

1. `shell=False` everywhere. No exceptions.
2. Paths resolved and validated before subprocess spawns.
3. Tool input/output validated against manifest schemas.
4. Audit writes preceded by chain hash computation.
5. `AuditWriteFailureError` always halts the process.

---

## Directory Structure

```text
aria/
├── aria/
│   ├── kernel/
│   │   ├── fsm.py          # Session finite state machine
│   │   ├── context.py      # Immutable per-step execution context
│   │   └── kernel.py       # Agent kernel (orchestrator)
│   ├── models/
│   │   ├── types.py        # All shared data contracts (dataclasses)
│   │   ├── errors.py       # Typed exception hierarchy
│   │   ├── router.py       # Model router: retry + circuit breaker
│   │   └── providers/
│   │       ├── base.py              # ModelProviderInterface ABC
│   │       ├── circuit_breaker.py   # Per-provider circuit breaker
│   │       ├── anthropic_provider.py # Anthropic Claude adapter
│   │       └── ollama_provider.py    # Local Ollama adapter (tinyllama)
│   ├── tools/
│   │   ├── registry.py     # Tool registry: load, validate, enforce permissions
│   │   ├── sandbox.py      # Subprocess sandbox + path/schema validation
│   │   └── builtin/
│   │       ├── read_file.py
│   │       └── write_file.py
│   ├── memory/
│   │   └── sqlite.py       # SQLite memory + audit (WAL, chain hashing)
│   ├── security/
│   │   ├── secrets.py      # Env-based secrets loader
│   │   └── scrubber.py     # Log scrubber + injection scanner
│   ├── cli/
│   │   ├── main.py         # CLI entry point
│   │   ├── bootstrap.py    # Dependency wiring
│   │   ├── run_cmd.py
│   │   ├── audit_cmd.py
│   │   ├── tools_cmd.py
│   │   └── config_cmd.py
│   └── logging_setup.py    # Structured JSON logging (stdlib)
└── tests/
    ├── unit/               # FSM, scrubber, CB, memory, manifest validation
    ├── integration/        # Full kernel with mock provider + real SQLite
    └── security/           # Path traversal, injection, tampering, permissions
```

---

## Error Taxonomy

| Error                       | Retryable | Action                      |
| :-------------------------- | :-------- | :-------------------------- |
| `ToolInputValidationError`  | No        | FAILED + log                |
| `ToolTimeoutError`          | No        | FAILED + log                |
| `ModelProviderError` (5xx)  | Yes (3x)  | Retry with backoff          |
| `ModelRateLimitError` (429) | Yes (3x)  | Retry with backoff          |
| `CircuitBreakerOpenError`   | No        | Try fallback or FAILED      |
| `StepLimitExceededError`    | No        | FAILED + log                |
| `InvalidStateTransitionError` | No        | CRITICAL + halt             |
| `AuditWriteFailureError`    | No        | CRITICAL + halt             |
| `UnknownToolError`          | No        | FAILED + log                |
| `PathTraversalError`        | No        | FAILED + log                |

**No silent failures. No bare `except Exception: pass`. Every error has a name.**

---

## Configuration

All configuration via environment variables:

```bash
ANTHROPIC_API_KEY=sk-ant-...         # Required for Anthropic provider
ARIA_PRIMARY_PROVIDER=ollama          # Default: ollama
ARIA_PRIMARY_MODEL=tinyllama          # Default: tinyllama
ARIA_MAX_STEPS=20                     # Default: 20
ARIA_MAX_COST_USD=1.0                 # Default: 1.00
ARIA_DB_PATH=~/.aria/aria.db
ARIA_LOG_PATH=~/.aria/logs/aria.jsonl
ARIA_LOG_LEVEL=INFO
```

---

## Writing a Plugin Tool

```python
# my_tool.py — place in a plugin_dirs directory
from aria.models.types import ToolManifest, ToolPermission

class ToolPlugin:
    manifest = ToolManifest(
        name="word_count",
        version="1.0.0",
        description="Count words in a text string. Returns integer count.",
        permissions=frozenset({ToolPermission.NONE}),  # No FS/network access
        timeout_seconds=5,
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
            "additionalProperties": False,
        },
    )

    @staticmethod
    def execute(input_data: dict) -> dict:
        return {"count": len(input_data["text"].split())}
```

**Plugin rules:**

- Must define `ToolPlugin` class with `manifest: ToolManifest` and `execute(dict) -> dict`
- `execute` runs in a subprocess — it cannot import ARIA internals
- Schema validation happens before and after execution
- Path access validated against `allowed_paths` before subprocess spawns
- `shell=False` always — never use `subprocess.call` with string args

---

## Audit & Observability

Every session produces a complete, append-only audit trail:

```bash
# List recent sessions
aria audit list --last 20

# Export full audit trail (JSON or human-readable text)
aria audit export --session-id <id> --format json
aria audit export --session-id <id> --format text

# Verify audit chain integrity (detect tampering)
aria audit verify --session-id <id>
```

The audit chain uses SHA-256 linking: each record's hash is computed from the previous record's hash and the current record's content. Any modification breaks the chain.

---

## Roadmap

**Month 1 (Foundation — ONLY phase that matters):**
✅ Agent kernel + FSM  
✅ ToolManifest validation  
✅ Subprocess sandbox  
✅ Anthropic + Ollama provider adapters  
✅ SQLite memory + audit with chain hashing  
✅ Structured JSON logging with secrets scrubber  
✅ CLI: run, audit, tools  
✅ Unit + integration + security tests  

**Month 3 (Stability):** OpenAI adapter, full circuit breaker, schema migration, cost dashboard, fuzzing tests.

**Month 6 (Hardening):** Prometheus metrics, 4 built-in tools, plugin SDK, chaos testing, session resumption.

**Month 12 (Enterprise):** Postgres backend, multi-session concurrency, read-only web UI, RBAC, OpenTelemetry.

---

## Known Limitations (v1)

- **Subprocess ≠ container**: Same-user processes can observe each other. For untrusted plugins, upgrade to namespace isolation (Month 6).
- **No session resumption**: FAILED sessions are terminal. Replay from beginning.
- **Context truncation**: Conversation history truncated when approaching token limits. Crude but deterministic.
- **SQLite only**: Concurrent write throughput bottleneck. Acceptable for single-process v1. `MemoryInterface` abstraction enables Postgres migration.
- **Prompt injection**: Syntactic + structural defenses implemented. Schema validation is the last hard boundary, not the only one.

---

### ARIA Philosophy

*"Stable > Feature-rich. Predictable > Smart. Auditable > Autonomous."*
