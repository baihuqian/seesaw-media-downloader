"""Timestamp stamping.

Photo libraries sort by EXIF DateTimeOriginal and fall back to mtime, so both have to be
right or a year of journal photos imports in download order.
"""

from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import piexif
import pytest
from PIL import Image

from seesaw_dl.logging import Reporter
from seesaw_dl.metadata import EXIF_SUFFIXES, _utc_offset, stamp


def make_jpeg(path: Path, size: tuple[int, int] = (8, 6)) -> Path:
    """A genuine, EXIF-free JPEG -- the shape of file Seesaw actually serves."""
    Image.new("RGB", size, (120, 90, 200)).save(path, "JPEG", quality=80)
    return path


@pytest.fixture
def reporter() -> Reporter:
    return Reporter()


@pytest.fixture
def jpeg(tmp_path: Path) -> Path:
    return make_jpeg(tmp_path / "photo.jpg")


def taken() -> datetime:
    return datetime(2026, 5, 14, 9, 12, 3, tzinfo=timezone(timedelta(hours=-5)))


def test_writes_datetime_original(jpeg: Path, reporter: Reporter) -> None:
    assert stamp(jpeg, taken(), reporter) is True
    exif = piexif.load(str(jpeg))
    assert exif["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:05:14 09:12:03"
    assert exif["Exif"][piexif.ExifIFD.DateTimeDigitized] == b"2026:05:14 09:12:03"
    assert exif["0th"][piexif.ImageIFD.DateTime] == b"2026:05:14 09:12:03"


def test_writes_utc_offset_so_the_time_is_not_naive(jpeg: Path, reporter: Reporter) -> None:
    stamp(jpeg, taken(), reporter)
    exif = piexif.load(str(jpeg))
    assert exif["Exif"][piexif.ExifIFD.OffsetTimeOriginal] == b"-05:00"


def test_sets_file_mtime_for_libraries_without_exif_support(
    jpeg: Path, reporter: Reporter
) -> None:
    stamp(jpeg, taken(), reporter)
    assert jpeg.stat().st_mtime == pytest.approx(taken().timestamp(), abs=1)


def test_non_jpeg_gets_mtime_but_no_exif(tmp_path: Path, reporter: Reporter) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")
    assert stamp(video, taken(), reporter) is False
    assert video.stat().st_mtime == pytest.approx(taken().timestamp(), abs=1)
    assert video.read_bytes() == b"not really a video"


def test_caption_and_provenance_are_recorded(jpeg: Path, reporter: Reporter) -> None:
    stamp(jpeg, taken(), reporter, caption="Building towers", source="item.abc123")
    exif = piexif.load(str(jpeg))
    assert exif["0th"][piexif.ImageIFD.ImageDescription] == b"Building towers"
    comment = exif["Exif"][piexif.ExifIFD.UserComment]
    # The stamp is the post date, not camera truth, and the file has to say so.
    assert b"not the original capture time" in comment
    assert b"item.abc123" in comment


def test_image_data_is_untouched(jpeg: Path, reporter: Reporter) -> None:
    """Stamping must add an APP1 segment, never re-encode the picture."""
    before = _scan_segments(jpeg.read_bytes())
    stamp(jpeg, taken(), reporter)
    after = _scan_segments(jpeg.read_bytes())
    assert 0xE1 not in before and 0xE1 in after  # APP1/EXIF added
    assert before["entropy"] == after["entropy"]  # compressed image data identical


def test_non_ascii_caption_does_not_crash(jpeg: Path, reporter: Reporter) -> None:
    stamp(jpeg, taken(), reporter, caption="café \U0001f382 day")
    assert piexif.load(str(jpeg))["0th"][piexif.ImageIFD.ImageDescription]


def test_corrupt_file_warns_instead_of_failing(tmp_path: Path, reporter: Reporter) -> None:
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"this is not a jpeg at all")
    assert stamp(broken, taken(), reporter) is False
    # The mtime is still corrected -- a readable timestamp beats no timestamp.
    assert broken.stat().st_mtime == pytest.approx(taken().timestamp(), abs=1)


@pytest.mark.parametrize(
    ("hours", "expected"),
    [(0, "+00:00"), (-5, "-05:00"), (5.5, "+05:30"), (-9.5, "-09:30"), (14, "+14:00")],
)
def test_utc_offset_formatting(hours: float, expected: str) -> None:
    moment = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=hours)))
    assert _utc_offset(moment) == expected


def test_naive_datetime_is_interpreted_locally(jpeg: Path, reporter: Reporter) -> None:
    naive = datetime(2026, 5, 14, 9, 12, 3)
    stamp(jpeg, naive, reporter)
    exif = piexif.load(str(jpeg))
    assert exif["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:05:14 09:12:03"


def test_jpeg_suffixes_are_recognised_case_insensitively(
    tmp_path: Path, reporter: Reporter
) -> None:
    upper = make_jpeg(tmp_path / "PHOTO.JPG")
    assert upper.suffix.lower() in EXIF_SUFFIXES
    assert stamp(upper, taken(), reporter) is True


def _scan_segments(data: bytes) -> dict:
    """Return the markers present and the entropy-coded payload after SOS."""
    markers = set()
    i = 2
    while i < len(data) - 1 and data[i] == 0xFF:
        marker = data[i + 1]
        markers.add(marker)
        if marker == 0xDA:  # start of scan; the rest is image data
            size = struct.unpack(">H", data[i + 2 : i + 4])[0]
            result = dict.fromkeys(markers, True)
            result["entropy"] = data[i + 2 + size :]
            return result
        size = struct.unpack(">H", data[i + 2 : i + 4])[0]
        i += 2 + size
    return {"entropy": b""}


def test_explicit_offset_is_preserved_not_rewritten(jpeg: Path, reporter: Reporter) -> None:
    """The stamp must not depend on the downloading machine's timezone.

    Feed timestamps already carry the offset the post was made in. Converting them to the
    local zone would make the same post stamp differently on a laptop in New York and a
    server in UTC -- and made the CI run fail while passing locally.
    """
    india = datetime(2026, 5, 14, 9, 12, 3, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    stamp(jpeg, india, reporter)
    exif = piexif.load(str(jpeg))
    assert exif["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:05:14 09:12:03"
    assert exif["Exif"][piexif.ExifIFD.OffsetTimeOriginal] == b"+05:30"
