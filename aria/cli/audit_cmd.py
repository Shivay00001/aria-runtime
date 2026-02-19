"""aria/cli/audit_cmd.py — `aria audit` commands."""

from __future__ import annotations

import json
import sys

import click


@click.group()
def audit_group() -> None:
    """Audit log commands."""


@audit_group.command("list")
@click.option("--last", "-n", default=10, show_default=True)
@click.pass_context
def audit_list(ctx: click.Context, last: int) -> None:
    """List recent sessions."""
    from aria.cli.bootstrap import build_kernel, load_config

    obj = ctx.obj or {}
    try:
        cfg = load_config(obj.get("config_path", ""))
        _, storage = build_kernel(cfg, obj.get("log_level", "INFO"))
        sessions = storage.list_sessions(limit=last)
        storage.close()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not sessions:
        click.echo("No sessions.")
        return
    click.echo(f"{'SESSION ID':<38} {'STATUS':<12} {'STEPS':<6} {'COST':>8}  {'TASK'}")
    click.echo("-" * 90)
    for s in sessions:
        click.echo(
            f"{s['session_id']:<38} {s['status']:<12} {s.get('total_steps', 0):<6} "
            f"${s.get('total_cost_usd', 0):.4f}  {(s.get('task', ''))[:30]}"
        )


@audit_group.command("export")
@click.option("--session-id", required=True)
@click.option("--format", "fmt", type=click.Choice(["json", "text"]), default="text")
@click.pass_context
def audit_export(ctx: click.Context, session_id: str, fmt: str) -> None:
    """Export full audit trail for a session."""
    from aria.cli.bootstrap import build_kernel, load_config

    obj = ctx.obj or {}
    try:
        cfg = load_config(obj.get("config_path", ""))
        _, storage = build_kernel(cfg, obj.get("log_level", "INFO"))
        events = storage.get_session_events(session_id)
        storage.close()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not events:
        click.echo(f"No events for session {session_id!r}.")
        return
    if fmt == "json":
        click.echo(json.dumps(events, indent=2))
    else:
        click.echo(f"Audit — {session_id}\n{'=' * 70}")
        for e in events:
            payload = json.loads(e.get("payload_json", "{}"))
            click.echo(
                f"[{e.get('timestamp', '')[:19]}] {e.get('level', ''):<8} "
                f"{e.get('event_type', ''):<30} {str(payload)[:60]}"
            )


@audit_group.command("verify")
@click.option("--session-id", required=True)
@click.pass_context
def audit_verify(ctx: click.Context, session_id: str) -> None:
    """Verify audit chain integrity."""
    from aria.cli.bootstrap import build_kernel, load_config

    obj = ctx.obj or {}
    try:
        cfg = load_config(obj.get("config_path", ""))
        _, storage = build_kernel(cfg, obj.get("log_level", "INFO"))
        ok = storage.verify_chain(session_id)
        storage.close()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if ok:
        click.echo(f"[OK] Audit chain INTACT for {session_id!r}")
    else:
        click.echo(f"[FAIL] Audit chain BROKEN for {session_id!r}", err=True)
        sys.exit(1)
