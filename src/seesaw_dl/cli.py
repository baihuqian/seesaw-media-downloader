"""Command line interface."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from . import __version__
from .api import SeesawClient
from .auth import Session, SessionStore, get_session, load_session
from .config import LogLevel, Settings, resolve_settings
from .dates import describe, parse_since
from .downloader import Report, run_downloads
from .errors import SeesawError
from .logging import Reporter, register_secret
from .manifest import Manifest
from .planner import Plan, build_plan, resolve_child
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


# Options shared by `list` and `download`, defined once so the two commands cannot drift.
# That drift is the one real risk in having them separate: a listing is only useful if it
# shows exactly what a download would fetch, which means both must take the same inputs.
_CHILD = typer.Option(
    None, "--child", help="Which child. Required when the account has more than one."
)
_SINCE = typer.Option(
    None, "--since", help="Only posts on or after this date (YYYY-MM-DD, or e.g. 30d)."
)
_SESSION_FILE = typer.Option(None, "--session-file", help="Where the session is cached.")
_JSON = typer.Option(None, "--json/--no-json", help="Machine-readable output.")
_LOG_LEVEL = typer.Option(
    None, "--log-level", help="error | warn | info | debug (default: info)."
)


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
    if len(children) > 1:
        # A download covers one child, so this is where the user learns the names to pass.
        reporter.info("More than one child: pick one per run with `download --child <name>`.")


@app.command("list")
def list_media(
    child: str | None = _CHILD,
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Optional: an existing download directory, to report what is already there.",
    ),
    since: str | None = _SINCE,
    session_file: Path | None = _SESSION_FILE,
    json_output: bool | None = _JSON,
    log_level: LogLevel | None = _LOG_LEVEL,
) -> None:
    """Show what is in the feed. Writes nothing.

    `--out` is optional and only affects reporting: point it at an existing download and
    the listing gains a "Have?" column, which is what makes it answer "what is new?".
    """
    settings = resolve_settings(
        child=child,
        output_dir=out,
        since=since,
        session_file=session_file,
        json_output=json_output,
        log_level=log_level,
    )
    reporter = Reporter(settings.log_level, json_output=settings.json_output)
    output_dir = settings.output_dir

    plan, _, _ = _plan_run(settings, reporter, output_dir, mode="list")

    # Presence is only meaningful once we have somewhere to look.
    _render(plan, reporter, settings.json_output, include_presence=output_dir is not None)
    reporter.info(
        f"Would download: {len(plan.to_download)} assets ({plan.present_count} already present)"
    )


@app.command()
def download(
    child: str | None = _CHILD,
    out: Path | None = typer.Option(None, "--out", help="Directory to download into."),
    download_all: bool | None = typer.Option(
        None,
        "--all/--no-all",
        help="Re-fetch everything, including files already downloaded (default: skip them).",
    ),
    since: str | None = _SINCE,
    concurrency: int | None = typer.Option(None, "--concurrency", help="Parallel downloads."),
    session_file: Path | None = _SESSION_FILE,
    json_output: bool | None = _JSON,
    log_level: LogLevel | None = _LOG_LEVEL,
) -> None:
    """Download journal media, reusing the session cached by `seesaw-dl login`."""
    settings = resolve_settings(
        child=child,
        output_dir=out,
        download_all=download_all,
        since=since,
        concurrency=concurrency,
        session_file=session_file,
        json_output=json_output,
        log_level=log_level,
    )
    reporter = Reporter(settings.log_level, json_output=settings.json_output)

    try:
        output_dir = settings.require_output_dir()
    except SeesawError as exc:
        reporter.error(str(exc))
        raise typer.Exit(code=1) from exc

    plan, manifest, session = _plan_run(settings, reporter, output_dir, mode="download")
    assert manifest is not None  # an output directory always yields one

    _render(plan, reporter, settings.json_output, include_presence=True)

    try:
        report = run_downloads(
            plan,
            output_dir,
            manifest,
            reporter,
            concurrency=settings.concurrency,
            cookies=session.cookies,
        )
    except SeesawError as exc:
        reporter.error(str(exc))
        raise typer.Exit(code=1) from exc

    _summarise(report, output_dir, reporter, settings.json_output)
    if report.failed:
        raise typer.Exit(code=1)


def _plan_run(
    settings: Settings, reporter: Reporter, output_dir: Path | None, mode: str
) -> tuple[Plan, Manifest | None, Session]:
    """Everything `list` and `download` do identically: session, child, plan.

    Keeping this in one place is what guarantees a listing shows exactly what a download
    would fetch -- the two commands differ only in what they do with the plan afterwards.
    """
    try:
        # Distinct name: `since` is the raw CLI string, `since_at` the resolved instant.
        since_at = parse_since(settings.since)
        store = SessionStore(settings.session_file)
        session = load_session(store, reporter)

        reporter.info(f"Signed in as {session.email} (cached session)")
        reporter.info(
            f"Mode: {mode}, {describe(since_at)}"
            + (f", into {output_dir}" if output_dir else "")
        )

        # Skipping what is already on disk is the default; --all is the only opposite.
        manifest = Manifest.load(output_dir) if output_dir else None
        is_present = manifest.has if (manifest and not settings.download_all) else None

        with SeesawClient(session, reporter) as client:
            selected = resolve_child(client, reporter, settings.child)
            reporter.info(f"Child: {selected.display_name}")
            plan = build_plan(client, reporter, selected, since=since_at, is_present=is_present)
    except SeesawError as exc:
        reporter.error(str(exc))
        raise typer.Exit(code=1) from exc
    return plan, manifest, session


def _render(plan: Plan, reporter: Reporter, as_json: bool, include_presence: bool) -> None:
    if as_json:
        typer.echo(plan_json(plan, include_presence))
    else:
        reporter.print_raw(plan_table(plan, include_presence))


def _summarise(report: Report, output_dir: Path, reporter: Reporter, as_json: bool) -> None:
    summary = {
        "downloaded": len(report.downloaded),
        "skipped": len(report.skipped),
        "failed": len(report.failed),
        "bytes": report.total_bytes,
        "seconds": round(report.elapsed, 1),
        "output_dir": str(output_dir),
    }
    if as_json:
        typer.echo(json.dumps({"summary": summary}))
        return
    reporter.info(
        f"Done: {summary['downloaded']} downloaded, {summary['skipped']} skipped, "
        f"{summary['failed']} failed -> {output_dir} "
        f"({_human(report.total_bytes)} in {report.elapsed:.1f}s)"
    )


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("interrupted", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":  # pragma: no cover
    main()
