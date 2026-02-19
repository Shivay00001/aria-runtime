"""aria/cli/tools_cmd.py — `aria tools` commands."""
from __future__ import annotations
import sys
import click


@click.group()
def tools_group() -> None:
    """Tool registry commands."""


@tools_group.command("list")
@click.pass_context
def tools_list(ctx: click.Context) -> None:
    """List all registered tools."""
    from aria.cli.bootstrap import load_config
    from aria.tools.registry import ToolRegistry
    obj = ctx.obj or {}
    try:
        cfg = load_config(obj.get("config_path",""))
        reg = ToolRegistry(cfg)
        reg.build()
    except Exception as e:
        click.echo(f"Error: {e}", err=True); sys.exit(1)
    ms = reg.all_manifests
    if not ms:
        click.echo("No tools."); return
    click.echo(f"{'TOOL':<25} {'VER':<8} {'TIMEOUT':<9} PERMISSIONS")
    click.echo("─" * 70)
    for m in ms:
        perms = ", ".join(sorted(p.value for p in m.permissions)) or "none"
        click.echo(f"{m.name:<25} {m.version:<8} {m.timeout_seconds}s{'':<7} {perms}")
        click.echo(f"  └─ {m.description[:65]}")
