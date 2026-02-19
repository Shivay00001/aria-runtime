"""aria/cli/main.py — CLI entry point. Thin boundary — no business logic."""

from __future__ import annotations

import sys

import click

from aria.cli.audit_cmd import audit_group
from aria.cli.run_cmd import run_command
from aria.cli.tools_cmd import tools_group


@click.group()
@click.version_option(version="0.1.0", prog_name="aria")
@click.option(
    "--config", envvar="ARIA_CONFIG", default="~/.aria/config.toml", help="Config file path."
)
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARN", "ERROR"], case_sensitive=False),
    default="INFO",
    envvar="ARIA_LOG_LEVEL",
)
@click.pass_context
def cli(ctx: click.Context, config: str, log_level: str) -> None:
    """ARIA — Agent Runtime for Intelligent Automation.
    Local-first. Secure by default. Fully auditable."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["log_level"] = log_level


cli.add_command(run_command, name="run")
cli.add_command(audit_group, name="audit")
cli.add_command(tools_group, name="tools")


@cli.command("config")
@click.pass_context
def config_cmd(ctx: click.Context) -> None:
    """Show active configuration (secrets redacted)."""
    import json

    from aria.cli.bootstrap import load_config

    obj = ctx.obj or {}
    try:
        cfg = load_config(obj.get("config_path", ""))
        import dataclasses

        click.echo(json.dumps(dataclasses.asdict(cfg), default=str, indent=2))
    except Exception as e:
        click.echo(f"Config error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
