from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from seesaw_dl.config import DEFAULT_SESSION_FILE, LogLevel, resolve_settings
from seesaw_dl.errors import ConfigError


def write_env(tmp_path: Path, body: str) -> None:
    (tmp_path / ".env").write_text(body, encoding="utf-8")


def test_defaults_when_nothing_supplied() -> None:
    settings = resolve_settings()
    assert settings.email is None
    assert settings.output_dir is None
    assert settings.list_only is False
    assert settings.download_all is False
    assert settings.since is None
    assert settings.concurrency == 4
    assert settings.session_file == DEFAULT_SESSION_FILE
    assert settings.json_output is False
    assert settings.log_level is LogLevel.info


def test_dotenv_is_read(isolated_env: Path) -> None:
    write_env(isolated_env, "SEESAW_EMAIL=dotenv@example.com\nSEESAW_CONCURRENCY=7\n")
    settings = resolve_settings()
    assert settings.email == "dotenv@example.com"
    assert settings.concurrency == 7


def test_env_beats_dotenv(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_env(isolated_env, "SEESAW_EMAIL=dotenv@example.com\n")
    monkeypatch.setenv("SEESAW_EMAIL", "env@example.com")
    assert resolve_settings().email == "env@example.com"


def test_flag_beats_env(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_env(isolated_env, "SEESAW_EMAIL=dotenv@example.com\n")
    monkeypatch.setenv("SEESAW_EMAIL", "env@example.com")
    assert resolve_settings(email="flag@example.com").email == "flag@example.com"


def test_unsupplied_flags_do_not_clobber_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEESAW_EMAIL", "env@example.com")
    settings = resolve_settings(email=None, password="flagpass")
    assert settings.email == "env@example.com"
    assert settings.password == "flagpass"


def test_json_flag_env_name_is_seesaw_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEESAW_JSON", "true")
    assert resolve_settings().json_output is True
    # An unsupplied boolean flag arrives as None and must not clobber the environment,
    # which is why the CLI declares them as --flag/--no-flag with a None default.
    assert resolve_settings(json_output=None).json_output is True
    assert resolve_settings(json_output=False).json_output is False


def test_paths_are_expanded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEESAW_OUTPUT_DIR", "~/Seesaw")
    settings = resolve_settings()
    assert settings.output_dir == Path.home() / "Seesaw"


def test_blank_values_are_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEESAW_EMAIL", "   ")
    monkeypatch.setenv("SEESAW_SINCE", "")
    settings = resolve_settings()
    assert settings.email is None
    assert settings.since is None


def test_missing_credentials_names_flag_and_env() -> None:
    with pytest.raises(ConfigError) as excinfo:
        resolve_settings().require_credentials()
    message = str(excinfo.value)
    assert "--email" in message and "SEESAW_EMAIL" in message
    assert "--password" in message and "SEESAW_PASSWORD" in message


def test_output_dir_required_only_when_writing(tmp_path: Path) -> None:
    listing = resolve_settings(list_only=True)
    assert listing.writes_files is False

    downloading = resolve_settings(list_only=False)
    assert downloading.writes_files is True
    with pytest.raises(ConfigError) as excinfo:
        downloading.require_output_dir()
    assert "--out" in str(excinfo.value) and "SEESAW_OUTPUT_DIR" in str(excinfo.value)

    assert resolve_settings(output_dir=tmp_path).require_output_dir() == tmp_path


def test_concurrency_bounds() -> None:
    with pytest.raises(ValidationError):
        resolve_settings(concurrency=0)
    with pytest.raises(ValidationError):
        resolve_settings(concurrency=99)
