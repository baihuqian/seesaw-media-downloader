"""Parsing the ``--since`` window.

Two forms are accepted: an absolute ``YYYY-MM-DD`` and a relative ``Nd`` (days back).
Everything is resolved in the user's local timezone, matching how the rest of the tool
treats dates -- the folder layout and the EXIF stamps are local too, so a cutoff that
meant something different would be quietly confusing.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .errors import ConfigError

_ABSOLUTE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_RELATIVE = re.compile(r"^(\d+)\s*d(?:ays?)?$", re.IGNORECASE)

_HELP = (
    "Use a date like 2026-01-31, or a number of days back like 30d."
)


def parse_since(value: str | None, now: datetime | None = None) -> datetime | None:
    """Return the inclusive lower bound for post times, or ``None`` for no filter.

    ``2026-01-31`` means *from the start of that day*, so the whole day is included.
    ``30d`` means a rolling window: exactly 30 days before this moment.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None

    reference = (now or datetime.now()).astimezone()

    relative = _RELATIVE.match(text)
    if relative:
        days = int(relative.group(1))
        return reference - timedelta(days=days)

    absolute = _ABSOLUTE.match(text)
    if absolute:
        year, month, day = (int(part) for part in absolute.groups())
        try:
            # Midnight local: an inclusive bound covering the entire named day.
            return datetime(year, month, day).astimezone()
        except ValueError as exc:
            raise ConfigError(f"--since {value!r} is not a real date: {exc}. {_HELP}") from exc

    raise ConfigError(f"Could not understand --since {value!r}. {_HELP}")


def describe(since: datetime | None) -> str:
    if since is None:
        return "all dates"
    return f"posts on or after {since:%Y-%m-%d %H:%M}"
