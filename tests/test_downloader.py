"""Downloading: atomicity, layout, manifest bookkeeping."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import respx
from PIL import Image

from seesaw_dl.downloader import run_downloads
from seesaw_dl.logging import Reporter
from seesaw_dl.manifest import Manifest
from seesaw_dl.models import FeedItem
from seesaw_dl.planner import Plan, PlannedAsset

ASSET_URL = "https://assets.seesaw.me/us-2/a/b/photo.jpg:::1:::2:::1:::SIG"


def jpeg_bytes() -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    Image.new("RGB", (8, 6), (10, 120, 200)).save(buffer, "JPEG")
    return buffer.getvalue()


def make_plan(url: str = ASSET_URL, present: bool = False) -> Plan:
    item = FeedItem.parse(
        {
            "item_id": "item.abcd1234-0000-4000-8000-000000000000",
            "create_date": datetime(2026, 5, 14, 9, 12, 3).astimezone().timestamp(),
            "num_pages": 1,
            "caption": "Tower day",
            "pages": {"objects": [{"composite_image_url": url}]},
        },
        "Alex Rivera",
        "Class A",
    )
    asset = item.assets()[0]
    return Plan(assets=[PlannedAsset(asset=asset, item=item, already_present=present)])


@pytest.fixture
def reporter() -> Reporter:
    return Reporter()


@respx.mock
def test_downloads_into_year_date_layout(tmp_path: Path, reporter: Reporter) -> None:
    respx.get(url__startswith="https://assets.seesaw.me").mock(
        return_value=httpx.Response(200, content=jpeg_bytes())
    )
    plan = make_plan()
    report = run_downloads(plan, tmp_path, Manifest(tmp_path), reporter)

    assert len(report.downloaded) == 1
    landed = report.downloaded[0].path
    assert landed is not None
    assert landed.relative_to(tmp_path).parts[:2] == ("2026", "2026-05-14")
    assert landed.exists()


@respx.mock
def test_stamps_exif_on_arrival(tmp_path: Path, reporter: Reporter) -> None:
    import piexif

    respx.get(url__startswith="https://assets.seesaw.me").mock(
        return_value=httpx.Response(200, content=jpeg_bytes())
    )
    run_downloads(make_plan(), tmp_path, Manifest(tmp_path), reporter)
    landed = next(tmp_path.rglob("*.jpg"))
    exif = piexif.load(str(landed))
    assert exif["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:05:14 09:12:03"
    assert landed.stat().st_mtime == pytest.approx(
        datetime(2026, 5, 14, 9, 12, 3).astimezone().timestamp(), abs=1
    )


@respx.mock
def test_manifest_records_on_disk_size_not_served_size(
    tmp_path: Path, reporter: Reporter
) -> None:
    """EXIF stamping grows the file after download; presence compares against disk."""
    body = jpeg_bytes()
    respx.get(url__startswith="https://assets.seesaw.me").mock(
        return_value=httpx.Response(200, content=body)
    )
    manifest = Manifest(tmp_path)
    run_downloads(make_plan(), tmp_path, manifest, reporter)

    entry = next(iter(manifest.entries.values()))
    landed = tmp_path / entry.path
    assert entry.size == landed.stat().st_size
    assert entry.source_size == len(body)
    assert entry.size > entry.source_size  # the EXIF block we added
    assert manifest.has(make_plan().assets[0].asset) is True


@respx.mock
def test_rerun_skips_what_is_already_there(tmp_path: Path, reporter: Reporter) -> None:
    respx.get(url__startswith="https://assets.seesaw.me").mock(
        return_value=httpx.Response(200, content=jpeg_bytes())
    )
    manifest = Manifest(tmp_path)
    run_downloads(make_plan(), tmp_path, manifest, reporter)
    manifest = Manifest.load(tmp_path)

    asset = make_plan().assets[0].asset
    assert manifest.has(asset) is True
    report = run_downloads(make_plan(present=True), tmp_path, manifest, reporter)
    assert len(report.skipped) == 1
    assert len(report.downloaded) == 0


@respx.mock
def test_failed_download_leaves_no_partial_file(tmp_path: Path, reporter: Reporter) -> None:
    """A half-written file would be mistaken for a finished one by the next run."""
    respx.get(url__startswith="https://assets.seesaw.me").mock(
        return_value=httpx.Response(500)
    )
    report = run_downloads(make_plan(), tmp_path, Manifest(tmp_path), reporter)

    assert len(report.failed) == 1
    assert list(tmp_path.rglob("*.part")) == []
    assert list(tmp_path.rglob("*.jpg")) == []


@respx.mock
def test_truncated_response_is_rejected(tmp_path: Path, reporter: Reporter) -> None:
    respx.get(url__startswith="https://assets.seesaw.me").mock(
        return_value=httpx.Response(
            200, content=b"short", headers={"Content-Length": "99999"}
        )
    )
    report = run_downloads(make_plan(), tmp_path, Manifest(tmp_path), reporter)
    assert len(report.failed) == 1
    assert list(tmp_path.rglob("*.jpg")) == []


@respx.mock
def test_transient_failure_is_retried(tmp_path: Path, reporter: Reporter) -> None:
    respx.get(url__startswith="https://assets.seesaw.me").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, content=jpeg_bytes())]
    )
    report = run_downloads(make_plan(), tmp_path, Manifest(tmp_path), reporter)
    assert len(report.downloaded) == 1


@respx.mock
def test_sidecar_records_the_post(tmp_path: Path, reporter: Reporter) -> None:
    respx.get(url__startswith="https://assets.seesaw.me").mock(
        return_value=httpx.Response(200, content=jpeg_bytes())
    )
    run_downloads(make_plan(), tmp_path, Manifest(tmp_path), reporter)
    sidecar = next(p for p in tmp_path.rglob("*.json") if p.name != "manifest.json")
    data = json.loads(sidecar.read_text())
    assert data["caption"] == "Tower day"
    assert data["child"] == "Alex Rivera"
    assert data["item_id"].startswith("item.")


@respx.mock
def test_manifest_survives_a_reload(tmp_path: Path, reporter: Reporter) -> None:
    respx.get(url__startswith="https://assets.seesaw.me").mock(
        return_value=httpx.Response(200, content=jpeg_bytes())
    )
    run_downloads(make_plan(), tmp_path, Manifest(tmp_path), reporter)
    reloaded = Manifest.load(tmp_path)
    assert len(reloaded.entries) == 1
    assert reloaded.has(make_plan().assets[0].asset) is True
