"""Configuration resolution.

Every input can come from a CLI flag, an environment variable, a ``.env`` file, or a
built-in default -- in that order of precedence. ``pydantic-settings`` already gives us
``env > .env > default``; CLI flags are layered on top by passing only the flags the user
actually supplied (everything else arrives as ``None``) into :func:`resolve_settings`.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigError

DEFAULT_SESSION_FILE = Path.home() / ".config" / "seesaw-dl" / "session.json"


class LogLevel(str, Enum):
    error = "error"
    warn = "warn"
    info = "info"
    debug = "debug"


class Settings(BaseSettings):
    """Fully resolved configuration for a run."""

    model_config = SettingsConfigDict(
        env_prefix="SEESAW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Credentials
    email: str | None = None
    password: str | None = None

    # Output
    output_dir: Path | None = None

    # Modes
    list_only: bool = False
    # Skipping what is already downloaded is the default and has no flag of its own:
    # `--all` (or `--no-all`) is the single, unambiguous way to control it.
    download_all: bool = False
    since: str | None = None

    # Behaviour
    concurrency: int = Field(default=4, ge=1, le=16)
    session_file: Path = DEFAULT_SESSION_FILE
    json_output: bool = Field(
        default=False,
        validation_alias=AliasChoices("json_output", "SEESAW_JSON"),
    )
    log_level: LogLevel = LogLevel.info

    @field_validator("email", "password", "since", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("output_dir", "session_file", mode="before")
    @classmethod
    def _expand(cls, value: Any) -> Any:
        if isinstance(value, str):
            if not value.strip():
                return None
            return Path(value).expanduser()
        if isinstance(value, Path):
            return value.expanduser()
        return value

    @property
    def writes_files(self) -> bool:
        """``--list-only`` never touches disk, so it does not need an output directory."""
        return not self.list_only

    def require_credentials(self) -> tuple[str, str]:
        """Return ``(email, password)``, reporting *all* missing credentials at once."""
        missing = [
            name
            for name, value in (("email", self.email), ("password", self.password))
            if value is None
        ]
        if missing:
            raise ConfigError(_missing_message(missing))
        assert self.email is not None and self.password is not None
        return self.email, self.password

    def require_output_dir(self) -> Path:
        if self.output_dir is None:
            raise ConfigError(_missing_message(["output_dir"]))
        return self.output_dir


_SOURCES: dict[str, tuple[str, str]] = {
    # field name -> (CLI flag, environment variable)
    "email": ("--email", "SEESAW_EMAIL"),
    "password": ("--password", "SEESAW_PASSWORD"),
    "output_dir": ("--out", "SEESAW_OUTPUT_DIR"),
    "list_only": ("--list-only", "SEESAW_LIST_ONLY"),
    "download_all": ("--all/--no-all", "SEESAW_DOWNLOAD_ALL"),
    "since": ("--since", "SEESAW_SINCE"),
    "concurrency": ("--concurrency", "SEESAW_CONCURRENCY"),
    "session_file": ("--session-file", "SEESAW_SESSION_FILE"),
    "json_output": ("--json", "SEESAW_JSON"),
    "log_level": ("--log-level", "SEESAW_LOG_LEVEL"),
}


def _missing_message(fields: list[str]) -> str:
    lines = ["Missing required configuration:"]
    for name in fields:
        flag, env = _SOURCES[name]
        lines.append(f"  - {name}: pass {flag}, or set {env} in the environment or .env")
    return "\n".join(lines)


def resolve_settings(**overrides: Any) -> Settings:
    """Build :class:`Settings` from ``.env``/environment, then apply CLI overrides.

    Only overrides that are not ``None`` win, so an unspecified flag falls through to the
    environment, then ``.env``, then the built-in default.
    """
    supplied = {key: value for key, value in overrides.items() if value is not None}
    return Settings(**supplied)
