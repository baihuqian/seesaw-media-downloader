"""Command line interface."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from . import __version__
from .api import SeesawClient
from .auth import SessionStore, get_session, load_session
from .config import LogLevel, resolve_settings
from .dates import describe, parse_since
from .errors import SeesawError
from .logging import Reporter, register_secret
from .planner import build_plan
from .render import plan_json, plan_table

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Download photos, videos and PDFs from your Seesaw family feed.",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"seesaw-dl {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Seesaw family media downloader."""


@app.command()
def login(
    email: str | None = typer.Option(None, "--email", help="Seesaw account email."),
    password: str | None = typer.Option(None, "--password", help="Seesaw account password."),
    session_file: Path | None = typer.Option(
        None, "--session-file", help="Where to cache the session."
    ),
    log_level: LogLevel | None = typer.Option(
        None, "--log-level", help="error | warn | info | debug (default: info)."
    ),
    headful: bool = typer.Option(False, "--headful", help="Show the browser and sign in manually."),
    force: bool = typer.Option(False, "--force", help="Ignore any cached session."),
) -> None:
    """Sign in to Seesaw and cache the session for later runs.

    This is the only command that opens a browser. Everything else reuses the cached
    session, so you sign in once and downloads stay non-interactive.
    """
    settings = resolve_settings(
        email=email,
        password=password,
        session_file=session_file,
        log_level=log_level,
    )
    reporter = Reporter(settings.log_level)
    try:
        account, secret = settings.require_credentials()
        register_secret(secret)
        store = SessionStore(settings.session_file)
        session = get_session(account, secret, store, reporter, force=force, headful=headful)
    except SeesawError as exc:
        reporter.error(str(exc))
        raise typer.Exit(code=1) from exc

    reporter.info(f"Signed in as {session.email}")
    reporter.info(f"Session cached at {store.path}")
    if session.release:
        reporter.debug(f"web app release: {session.release}")

    try:
        with SeesawClient(session, reporter) as client:
            children = client.children()
    except SeesawError as exc:
        reporter.warn(f"Signed in, but could not read the account yet: {exc}")
        return
    names = ", ".join(child.display_name for child in children) or "none found"
    reporter.info(f"Children: {names}")


@app.command()
def download(
    out: Path | None = typer.Option(
        None, "--out", help="Directory to download into. Not needed with --list-only."
    ),
    list_only: bool | None = typer.Option(
        None, "--list-only/--no-list-only", help="Show what would be downloaded; write nothing."
    ),
    download_all: bool | None = typer.Option(
        None, "--all/--no-all", help="Fetch everything, including files already present."
    ),
    skip_existing: bool | None = typer.Option(
        None, "--skip-existing/--no-skip-existing", help="Skip files already downloaded."
    ),
    since: str | None = typer.Option(
        None, "--since", help="Only posts on or after this date (YYYY-MM-DD, or e.g. 30d)."
    ),
    concurrency: int | None = typer.Option(None, "--concurrency", help="Parallel downloads."),
    session_file: Path | None = typer.Option(
        None, "--session-file", help="Where the session is cached."
    ),
    json_output: bool | None = typer.Option(
        None, "--json/--no-json", help="Machine-readable output."
    ),
    log_level: LogLevel | None = typer.Option(
        None, "--log-level", help="error | warn | info | debug (default: info)."
    ),
) -> None:
    """Download journal media, reusing the session cached by `seesaw-dl login`."""
    settings = resolve_settings(
        output_dir=out,
        list_only=list_only,
        download_all=download_all,
        skip_existing=skip_existing,
        since=since,
        concurrency=concurrency,
        session_file=session_file,
        json_output=json_output,
        log_level=log_level,
    )
    reporter = Reporter(settings.log_level, json_output=settings.json_output)

    try:
        output_dir = settings.output_dir if settings.list_only else settings.require_output_dir()
        # Distinct name: `since` is the raw CLI string, `since_at` the resolved instant.
        since_at = parse_since(settings.since)
        store = SessionStore(settings.session_file)
        session = load_session(store, reporter)

        reporter.info(f"Signed in as {session.email} (cached session)")
        reporter.info(
            "Mode: "
            + ("list-only" if settings.list_only else "download")
            + f", {describe(since_at)}"
            + (f", into {output_dir}" if output_dir else "")
        )

        with SeesawClient(session, reporter) as client:
            plan = build_plan(client, reporter, since=since_at)
    except SeesawError as exc:
        reporter.error(str(exc))
        raise typer.Exit(code=1) from exc

    # Presence is only meaningful once we have somewhere to look.
    include_presence = output_dir is not None

    if settings.json_output:
        typer.echo(plan_json(plan, include_presence))
    else:
        reporter.print_raw(plan_table(plan, include_presence))

    if settings.list_only:
        reporter.info(
            f"Would download: {len(plan.to_download)} assets ({plan.present_count} already present)"
        )
        return

    reporter.error("Downloading is not wired up yet -- use --list-only for now.")
    raise typer.Exit(code=2)


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("interrupted", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":  # pragma: no cover
    main()
