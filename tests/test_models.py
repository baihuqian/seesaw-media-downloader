from __future__ import annotations

from datetime import datetime

from seesaw_dl.models import FeedItem, _asset_id, _kind_for, _safe


def make_item(**overrides) -> FeedItem:
    data = {
        "item_id": "item.1a2b3c4d-5e6f-4000-8000-000000000000",
        "create_date": 1787692822.4114704,
        "num_pages": 1,
        "pages": {
            "objects": [
                {"composite_image_url": "https://assets.seesaw.me/us-2/a/b.jpg:::1:::2:::1:::SIG"}
            ]
        },
    }
    data.update(overrides)
    return FeedItem.parse(data, "Alex Rivera", "Class A")


def test_asset_id_strips_signature_and_host() -> None:
    assert _asset_id("https://assets.seesaw.me/us-2/a/b.jpg:::1:::2:::1:::SIG") == "us-2/a/b.jpg"


def test_asset_id_is_stable_across_resignings() -> None:
    base = "https://assets.seesaw.me/us-2/a/b.jpg:::1786762368:::1209600:::1:::"
    assert _asset_id(base + "SIG_ONE") == _asset_id(base + "SIG_TWO")


def test_kind_detection() -> None:
    assert _kind_for("x/y.jpg") == "photo"
    assert _kind_for("x/y.MP4".lower()) == "video"
    assert _kind_for("x/y.pdf") == "pdf"
    assert _kind_for("x/y.zzz") == "file"


def test_needs_full_fetch_only_when_pages_are_missing() -> None:
    assert make_item(num_pages=2).needs_full_fetch is True
    assert make_item(num_pages=1).needs_full_fetch is False


def test_filename_and_layout() -> None:
    item = make_item()
    asset = item.assets()[0]
    path = asset.relative_path()
    day = asset.created_at.strftime("%Y-%m-%d")
    # No child folder: a run covers one child, so the name would repeat on every file.
    assert path.parts == (day[:4], day, asset.filename)
    assert asset.filename.startswith(day)
    assert asset.filename.endswith("_1a2b3c4d_p1.jpg")


def test_timestamps_are_local_so_evening_posts_keep_their_date() -> None:
    item = make_item()
    assert item.created_at.tzinfo is not None
    assert item.created_at == datetime.fromtimestamp(1787692822.4114704).astimezone()


def test_pages_without_a_usable_url_are_skipped() -> None:
    item = make_item(
        pages={
            "objects": [
                {"text": "note only"},
                {"composite_image_url": "https://assets.seesaw.me/us-2/c.jpg"},
            ]
        }
    )
    assets = item.assets()
    assert len(assets) == 1
    assert assets[0].asset_id == "us-2/c.jpg"


def test_resizable_url_is_only_a_last_resort() -> None:
    """imaging.seesaw.me returns a display rendition, not the original file."""
    item = make_item(pages={"objects": [{
        "composite_image_resizable_url": "https://imaging.seesaw.me/?url=x",
        "composite_image_url": "https://assets.seesaw.me/us-2/orig.jpg:::1:::2:::1:::SIG",
    }]})
    assert item.assets()[0].asset_id == "us-2/orig.jpg"


def test_safe_names_keep_distinct_children_distinct() -> None:
    assert _safe("Alex Rivera") == "Alex Rivera"
    assert _safe("A/B") != _safe("AB")
    assert _safe("../etc") == "_etc"
    assert _safe("") == "unknown"
