"""Stamping downloaded media with a usable timestamp.

Photo libraries (Apple Photos, Immich, Lightroom) sort by EXIF ``DateTimeOriginal`` and
fall back to the file's modification time. Seesaw serves **re-encoded composites with no
EXIF at all** -- verified on live downloads: a JPEG arrives with JFIF and ICC segments and
nothing else -- so an untouched download imports in download order, not in the order the
moments happened.

What we can honestly stamp is the *post* time. Seesaw's API exposes no capture date
anywhere: the item carries a single ``create_date``, and the epoch embedded in a signed
asset URL is the signature's issue time (its companion ``1209600`` is a 14-day TTL), not
when the shutter fired. So a photo posted days after it was taken gets the post time, and
the EXIF we write says so in ``UserComment`` rather than pretending to be camera truth.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import piexif

from .logging import Reporter

#: Formats where we can write real EXIF. Everything else gets the mtime treatment only.
EXIF_SUFFIXES = {".jpg", ".jpeg"}

_PROVENANCE = "Timestamp is the Seesaw post date, not the original capture time."


def stamp(
    path: Path,
    taken_at: datetime,
    reporter: Reporter,
    caption: str = "",
    source: str = "",
) -> bool:
    """Give ``path`` a timestamp photo libraries will sort by.

    Always sets the file's modification time; additionally writes EXIF for JPEGs. Returns
    whether EXIF was written. Metadata failures are warnings, never fatal -- a file with a
    correct mtime is still a good download.
    """
    wrote_exif = False
    if path.suffix.lower() in EXIF_SUFFIXES:
        wrote_exif = _write_exif(path, taken_at, reporter, caption, source)
    set_file_times(path, taken_at)
    return wrote_exif


def set_file_times(path: Path, taken_at: datetime) -> None:
    """Set mtime/atime, the fallback every photo library uses when EXIF is absent."""
    stamp_seconds = taken_at.timestamp()
    os.utime(path, (stamp_seconds, stamp_seconds))


def _write_exif(
    path: Path, taken_at: datetime, reporter: Reporter, caption: str, source: str
) -> bool:
    local = taken_at.astimezone()
    when = local.strftime("%Y:%m:%d %H:%M:%S")
    offset = _utc_offset(local)

    try:
        existing = piexif.load(str(path))
    except Exception as exc:  # noqa: BLE001 - piexif raises bare exceptions
        reporter.debug(f"no readable EXIF in {path.name} ({exc}); writing a fresh block")
        existing = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

    zeroth = existing.get("0th") or {}
    exif = existing.get("Exif") or {}

    zeroth[piexif.ImageIFD.DateTime] = when
    zeroth[piexif.ImageIFD.Software] = "seesaw-media-downloader"
    if caption:
        zeroth[piexif.ImageIFD.ImageDescription] = _ascii(caption)

    exif[piexif.ExifIFD.DateTimeOriginal] = when
    exif[piexif.ExifIFD.DateTimeDigitized] = when
    # Without an offset the timestamp is naive, and libraries assume their own timezone.
    exif[piexif.ExifIFD.OffsetTime] = offset
    exif[piexif.ExifIFD.OffsetTimeOriginal] = offset
    exif[piexif.ExifIFD.OffsetTimeDigitized] = offset

    note = _PROVENANCE + (f" Source: {source}" if source else "")
    # UserComment is a charset-prefixed byte field, not a plain string.
    exif[piexif.ExifIFD.UserComment] = b"ASCII\x00\x00\x00" + _ascii(note)

    payload = {
        "0th": zeroth,
        "Exif": exif,
        "GPS": existing.get("GPS") or {},
        "1st": existing.get("1st") or {},
        "thumbnail": existing.get("thumbnail"),
    }
    try:
        piexif.insert(piexif.dump(payload), str(path))
    except Exception as exc:  # noqa: BLE001 - never fail a download over metadata
        reporter.warn(f"could not write EXIF to {path.name}: {exc}")
        return False
    return True


def _utc_offset(moment: datetime) -> str:
    """``+HH:MM`` as EXIF 2.31 wants it."""
    delta = moment.utcoffset()
    if delta is None:
        return "+00:00"
    total = int(delta.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def _ascii(text: str) -> bytes:
    """EXIF's older string fields are ASCII; keep them readable rather than crashing."""
    return text.encode("ascii", errors="replace")[:1000]
