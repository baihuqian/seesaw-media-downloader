"""Command line interface."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from . import __version__
from .auth import SessionStore, get_session
from .config import LogLevel, resolve_settings
from .errors import SeesawError
from .logging import Reporter, register_secret

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
    """Sign in to Seesaw and cache the session for later runs."""
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


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("interrupted", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":  # pragma: no cover
    main()
