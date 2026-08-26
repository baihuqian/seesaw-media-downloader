"""Console output.

Human-readable progress goes to stdout; diagnostics go to stderr. Secrets are redacted at
every level -- there is no log level that prints a password, cookie or ``_xsrf`` token.
"""

from __future__ import annotations

import re
from typing import Any

from rich.console import Console

from .config import LogLevel

_ORDER = {LogLevel.error: 0, LogLevel.warn: 1, LogLevel.info: 2, LogLevel.debug: 3}

# Redaction is deliberately *literal-first*: the reporter is told the actual secret
# values (password, cookies, _xsrf) and blanks those exact strings. Pattern-scrubbing
# arbitrary prose mangles legitimate messages -- "pass --password" is not a secret.
_PATTERNS = (
    re.compile(r"(_xsrf=)[^&\s]+", re.IGNORECASE),
    # Header values run to the end of the line -- "Bearer <token>" is two words.
    re.compile(r"(\b(?:set-)?cookie:\s*).+", re.IGNORECASE),
    re.compile(r"(\bauthorization:\s*).+", re.IGNORECASE),
)

REDACTED = "[redacted]"

#: Exact secret values registered during a run. Short values are ignored so that a
#: one-character password cannot blank out half the output.
_SECRETS: set[str] = set()
_MIN_SECRET_LEN = 4


def register_secret(value: str | None) -> None:
    """Mark a literal value as secret so it is never printed."""
    if value and len(value) >= _MIN_SECRET_LEN:
        _SECRETS.add(value)


def clear_secrets() -> None:
    """Test helper: forget every registered secret."""
    _SECRETS.clear()


def redact(text: str) -> str:
    for secret in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    return text


class Reporter:
    """Level-aware printer used by every command."""

    def __init__(self, level: LogLevel = LogLevel.info, json_output: bool = False) -> None:
        self.level = level
        self.json_output = json_output
        self._out = Console()
        self._err = Console(stderr=True)

    def _enabled(self, level: LogLevel) -> bool:
        return _ORDER[level] <= _ORDER[self.level]

    def error(self, message: str) -> None:
        if self._enabled(LogLevel.error):
            self._err.print(f"[bold red]error[/] {redact(message)}")

    def warn(self, message: str) -> None:
        if self._enabled(LogLevel.warn):
            self._err.print(f"[yellow]warn[/]  {redact(message)}")

    def info(self, message: str) -> None:
        if self._enabled(LogLevel.info) and not self.json_output:
            self._out.print(redact(message))

    def debug(self, message: str) -> None:
        if self._enabled(LogLevel.debug):
            self._err.print(f"[dim]debug {redact(message)}[/dim]")

    def print_raw(self, renderable: Any) -> None:
        """Print without redaction filtering -- for tables we constructed ourselves."""
        self._out.print(renderable)
