"""--since parsing.

Dates are resolved in local time, matching the folder layout and the EXIF stamps; a cutoff
interpreted in a different zone would silently include or drop a day's posts.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from seesaw_dl.dates import describe, parse_since
from seesaw_dl.errors import ConfigError


def test_none_and_blank_mean_no_filter() -> None:
    assert parse_since(None) is None
    assert parse_since("") is None
    assert parse_since("   ") is None


def test_absolute_date_starts_at_local_midnight() -> None:
    since = parse_since("2026-01-31")
    assert since is not None
    assert (since.year, since.month, since.day) == (2026, 1, 31)
    assert (since.hour, since.minute, since.second) == (0, 0, 0)
    assert since.tzinfo is not None


def test_absolute_date_includes_the_whole_named_day() -> None:
    """--since 2026-01-31 must not drop a post made at 00:30 that morning."""
    since = parse_since("2026-01-31")
    early = datetime(2026, 1, 31, 0, 30).astimezone()
    assert since is not None and early >= since


def test_relative_days_are_a_rolling_window() -> None:
    now = datetime(2026, 8, 25, 16, 0).astimezone()
    since = parse_since("30d", now=now)
    assert since == now - timedelta(days=30)


@pytest.mark.parametrize("text", ["7d", "7D", "7 d", "7days", "7day", "07d"])
def test_relative_spellings(text: str) -> None:
    now = datetime(2026, 8, 25, 12, 0).astimezone()
    assert parse_since(text, now=now) == now - timedelta(days=7)


def test_zero_days_means_from_now() -> None:
    now = datetime(2026, 8, 25, 12, 0).astimezone()
    assert parse_since("0d", now=now) == now


@pytest.mark.parametrize(
    "text",
    ["last tuesday", "2026/01/31", "31-01-2026", "yesterday", "1w", "3m", "-5d", "d", "2026-01"],
)
def test_unparseable_values_are_rejected_with_help(text: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        parse_since(text)
    assert "2026-01-31" in str(excinfo.value)  # the message shows a usable example


def test_impossible_dates_are_rejected() -> None:
    with pytest.raises(ConfigError) as excinfo:
        parse_since("2026-02-30")
    assert "not a real date" in str(excinfo.value)


def test_naive_reference_is_localised() -> None:
    """A naive `now` must not be read as UTC, or the window shifts by the offset."""
    since = parse_since("1d", now=datetime(2026, 8, 25, 12, 0))
    assert since is not None and since.tzinfo is not None
    assert since == datetime(2026, 8, 24, 12, 0).astimezone()


def test_describe_is_human_readable() -> None:
    assert describe(None) == "all dates"
    assert "2026-01-31" in describe(parse_since("2026-01-31"))
