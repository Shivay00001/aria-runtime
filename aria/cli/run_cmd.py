"""aria/cli/run_cmd.py — `aria run` command."""

from __future__ import annotations

import sys

import click

from aria.models.types import SessionRequest, SessionStatus


@click.command()
@click.option("--task", "-t", required=True, help="Task for the agent.")
@click.option("--provider", default=None, help="Override model provider.")
@click.option("--model", default=None, help="Override model name.")
@click.option("--max-steps", default=None, type=int, help="Override max steps.")
@click.option("--dry-run", is_flag=True, help="Validate config only — no execution.")
@click.pass_context
def run_command(
    ctx: click.Context,
    task: str,
    provider: str | None,
    model: str | None,
    max_steps: int | None,
    dry_run: bool,
) -> None:
    """Run an agent session."""
    from aria.cli.bootstrap import build_kernel, load_config

    obj = ctx.obj or {}
    config_path = obj.get("config_path", "")
    log_level = obj.get("log_level", "INFO")

    try:
        config = load_config(config_path)
    except Exception as e:
        click.echo(f"Config error: {e}", err=True)
        sys.exit(1)

    if dry_run:
        click.echo("[OK] Config valid. Dry run — no execution.")
        click.echo(f"  Provider: {config.primary_provider} / {config.primary_model}")
        click.echo(f"  Max steps: {max_steps or config.max_steps}")
        return

    try:
        kernel, storage = build_kernel(config, log_level=log_level)
    except Exception as e:
        import traceback

        traceback.print_exc()
        click.echo(f"Startup error: {e}", err=True)
        sys.exit(1)

    request = SessionRequest(
        task=task, provider_override=provider, model_override=model, max_steps_override=max_steps
    )
    click.echo(f"Session: {request.session_id}")
    click.echo(f"Task:    {task[:80]}{'...' if len(task) > 80 else ''}")
    click.echo("-" * 60)

    try:
        result = kernel.run(request)
    except Exception as e:
        click.echo(f"Critical error: {e}", err=True)
        sys.exit(1)
    finally:
        storage.close()

    if result.status == SessionStatus.DONE and result.answer:
        click.echo(f"\n{result.answer}")
        click.echo(
            f"\n[OK] Done  steps={result.steps_taken}  "
            f"cost=${result.total_cost_usd:.4f}  {result.duration_ms}ms"
        )
    else:
        click.echo(f"\n[FAIL] Failed: {result.error_message}", err=True)
        click.echo(f"  Run: aria audit export --session-id {result.session_id}")
        sys.exit(1)
