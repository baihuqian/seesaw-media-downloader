"""CLI wiring: the mode matrix, and the login/download boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from seesaw_dl import cli
from seesaw_dl.auth import Session
from seesaw_dl.models import FeedItem
from seesaw_dl.planner import Plan, PlannedAsset

runner = CliRunner()


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the network with a one-item plan, capturing how the CLI called us."""
    captured: dict[str, Any] = {"is_present": "unset", "downloaded": False}

    item = FeedItem.parse(
        {
            "item_id": "item.abcd1234-0000-4000-8000-000000000000",
            "create_date": 1778000000.0,
            "num_pages": 1,
            "pages": {"objects": [{"composite_image_url": "https://assets.seesaw.me/us-2/a.jpg"}]},
        },
        "Alex Rivera",
        "Class A",
    )
    plan = Plan(assets=[PlannedAsset(asset=item.assets()[0], item=item)])

    def fake_load_session(store: Any, reporter: Any) -> Session:
        return Session(storage_state={"cookies": []}, xsrf="x", email="family@example.com")

    def fake_build_plan(client: Any, reporter: Any, **kwargs: Any) -> Plan:
        captured["is_present"] = kwargs.get("is_present")
        captured["since"] = kwargs.get("since")
        return plan

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...
        def __enter__(self) -> FakeClient:
            return self
        def __exit__(self, *exc: object) -> None: ...

    def fake_run_downloads(*args: Any, **kwargs: Any) -> Any:
        captured["downloaded"] = True
        captured["concurrency"] = kwargs.get("concurrency")
        from seesaw_dl.downloader import Report

        return Report(outcomes=[], elapsed=0.0)

    monkeypatch.setattr(cli, "load_session", fake_load_session)
    monkeypatch.setattr(cli, "build_plan", fake_build_plan)
    monkeypatch.setattr(cli, "SeesawClient", FakeClient)
    monkeypatch.setattr(cli, "run_downloads", fake_run_downloads)
    return captured


def test_list_only_needs_no_output_dir(fake_backend: dict[str, Any]) -> None:
    result = runner.invoke(cli.app, ["download", "--list-only"])
    assert result.exit_code == 0
    assert fake_backend["downloaded"] is False


def test_download_without_output_dir_is_refused(fake_backend: dict[str, Any]) -> None:
    result = runner.invoke(cli.app, ["download"])
    assert result.exit_code == 1
    assert "--out" in result.output and "SEESAW_OUTPUT_DIR" in result.output


def test_default_consults_presence(fake_backend: dict[str, Any], tmp_path: Path) -> None:
    runner.invoke(cli.app, ["download", "--out", str(tmp_path)])
    assert fake_backend["is_present"] is not None


def test_all_bypasses_presence(fake_backend: dict[str, Any], tmp_path: Path) -> None:
    runner.invoke(cli.app, ["download", "--out", str(tmp_path), "--all"])
    assert fake_backend["is_present"] is None


def test_no_all_restores_skipping(fake_backend: dict[str, Any], tmp_path: Path) -> None:
    """--no-all is the inverse of --all, for overriding SEESAW_DOWNLOAD_ALL from .env."""
    runner.invoke(cli.app, ["download", "--out", str(tmp_path), "--no-all"])
    assert fake_backend["is_present"] is not None


def test_skip_existing_flag_is_gone(fake_backend: dict[str, Any], tmp_path: Path) -> None:
    """It was an exact synonym for --all; one spelling only."""
    result = runner.invoke(
        cli.app, ["download", "--out", str(tmp_path), "--no-skip-existing"]
    )
    assert result.exit_code != 0


def test_since_is_parsed_before_the_network(
    fake_backend: dict[str, Any], tmp_path: Path
) -> None:
    runner.invoke(cli.app, ["download", "--out", str(tmp_path), "--since", "2026-01-31"])
    since = fake_backend["since"]
    assert since is not None and (since.year, since.month, since.day) == (2026, 1, 31)


def test_bad_since_fails_without_touching_the_network(
    fake_backend: dict[str, Any], tmp_path: Path
) -> None:
    result = runner.invoke(cli.app, ["download", "--out", str(tmp_path), "--since", "nope"])
    assert result.exit_code == 1
    assert fake_backend["downloaded"] is False


def test_json_mode_emits_parseable_output(
    fake_backend: dict[str, Any], tmp_path: Path
) -> None:
    result = runner.invoke(cli.app, ["download", "--out", str(tmp_path), "--json"])
    lines = [line for line in result.output.splitlines() if line.startswith("{")]
    assert lines, result.output
    for line in lines:
        json.loads(line)
    assert json.loads(lines[-1])["summary"]["output_dir"] == str(tmp_path)


def test_env_var_supplies_the_output_dir(
    fake_backend: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEESAW_OUTPUT_DIR", str(tmp_path))
    result = runner.invoke(cli.app, ["download"])
    assert result.exit_code == 0
    assert fake_backend["downloaded"] is True


def test_missing_session_never_opens_a_browser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole point of the login/download split."""
    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("download must never launch a browser")

    monkeypatch.setattr(cli, "get_session", explode)
    result = runner.invoke(
        cli.app,
        [
            "download",
            "--out",
            str(tmp_path),
            "--session-file",
            str(tmp_path / "missing.json"),
        ],
    )
    assert result.exit_code == 1
    assert "seesaw-dl login" in result.output


def test_version() -> None:
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert "seesaw-dl" in result.output
