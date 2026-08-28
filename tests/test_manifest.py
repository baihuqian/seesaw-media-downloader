"""The manifest's resilience paths.

These are the branches that only run when something has already gone wrong -- a deleted
file, a corrupt index, a half-written save. They are exactly the paths that stay silent
until they matter, so they are tested directly rather than through a download.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from seesaw_dl.manifest import MANIFEST_NAME, Manifest
from seesaw_dl.models import FeedItem, MediaAsset

ASSET_URL = "https://assets.seesaw.me/us-2/a/b/photo.jpg:::1:::2:::1:::SIG"


def make_asset(url: str = ASSET_URL) -> MediaAsset:
    item = FeedItem.parse(
        {
            "item_id": "item.abcd1234-0000-4000-8000-000000000000",
            "create_date": datetime(2026, 5, 14, 9, 12, 3).astimezone().timestamp(),
            "num_pages": 1,
            "pages": {"objects": [{"composite_image_url": url}]},
        },
        "Alex Rivera",
        "Class A",
    )
    return item.assets()[0]


def place(root: Path, asset: MediaAsset, body: bytes = b"jpeg-ish") -> Path:
    """Put a file where the asset says it belongs."""
    destination = root / asset.relative_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)
    return destination


# -- presence without an index -------------------------------------------------------


def test_file_at_the_expected_path_counts_without_a_manifest_entry(tmp_path: Path) -> None:
    """A restored backup or a lost index must not trigger a full re-download."""
    asset = make_asset()
    place(tmp_path, asset)
    assert Manifest(tmp_path).has(asset) is True


def test_absent_file_with_no_entry_is_absent(tmp_path: Path) -> None:
    assert Manifest(tmp_path).has(make_asset()) is False


def test_empty_file_at_the_expected_path_does_not_count(tmp_path: Path) -> None:
    """A zero-byte file is the shape an interrupted write leaves behind."""
    asset = make_asset()
    place(tmp_path, asset, body=b"")
    assert Manifest(tmp_path).has(asset) is False


def test_recorded_file_that_was_deleted_is_not_present(tmp_path: Path) -> None:
    """The filesystem outranks the index: the user deleting a file means they want it back."""
    asset = make_asset()
    landed = place(tmp_path, asset)
    manifest = Manifest(tmp_path)
    manifest.record(asset, landed, sha256="abc", source_size=len(b"jpeg-ish"))
    assert manifest.has(asset) is True

    landed.unlink()
    assert manifest.has(asset) is False


def test_recorded_file_of_the_wrong_size_is_not_present(tmp_path: Path) -> None:
    """A truncated file is worse than a missing one; re-fetch it."""
    asset = make_asset()
    landed = place(tmp_path, asset)
    manifest = Manifest(tmp_path)
    manifest.record(asset, landed, sha256="abc", source_size=8)

    landed.write_bytes(b"short")
    assert manifest.has(asset) is False


def test_entry_pointing_elsewhere_is_believed_over_the_computed_path(tmp_path: Path) -> None:
    """The recorded path wins, which is what lets an older layout keep working."""
    asset = make_asset()
    legacy = tmp_path / "Alex Rivera" / "2026" / "2026-05-14" / asset.filename
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"jpeg-ish")

    manifest = Manifest(tmp_path)
    manifest.record(asset, legacy, sha256="abc", source_size=8)
    assert manifest.has(asset) is True
    assert not (tmp_path / asset.relative_path()).exists()  # nothing at the new path


# -- surviving a damaged index -------------------------------------------------------


def test_corrupt_manifest_loads_as_empty_rather_than_raising(tmp_path: Path) -> None:
    """A broken index must never block a download; presence falls back to the disk."""
    (tmp_path / MANIFEST_NAME).write_text("{not json at all", encoding="utf-8")
    manifest = Manifest.load(tmp_path)
    assert manifest.entries == {}

    asset = make_asset()
    place(tmp_path, asset)
    assert manifest.has(asset) is True  # the file on disk still counts


def test_unreadable_manifest_loads_as_empty(tmp_path: Path) -> None:
    """A directory where the file should be is an OSError, not a JSON error."""
    (tmp_path / MANIFEST_NAME).mkdir()
    assert Manifest.load(tmp_path).entries == {}


def test_missing_manifest_loads_as_empty(tmp_path: Path) -> None:
    assert Manifest.load(tmp_path).entries == {}


def test_malformed_entries_are_skipped_and_good_ones_kept(tmp_path: Path) -> None:
    """One bad row must not cost the whole index."""
    good = {
        "asset_id": "us-2/a/b/photo.jpg",
        "path": "2026/2026-05-14/photo.jpg",
        "size": 8,
        "sha256": "abc",
        "item_id": "item.abcd1234",
        "downloaded_at": "2026-05-14T09:12:03+00:00",
        "source_size": 8,
    }
    (tmp_path / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "version": 1,
                "assets": [
                    good,
                    {"asset_id": "missing-everything-else"},  # KeyError
                    {**good, "asset_id": "us-2/x.jpg", "nope": 1},  # TypeError
                    "not even an object",
                ],
            }
        ),
        encoding="utf-8",
    )
    entries = Manifest.load(tmp_path).entries
    assert list(entries) == ["us-2/a/b/photo.jpg"]


# -- saving -------------------------------------------------------------------------


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    asset = make_asset()
    landed = place(tmp_path, asset)
    manifest = Manifest(tmp_path)
    manifest.record(asset, landed, sha256="abc", source_size=8)
    manifest.save()

    reloaded = Manifest.load(tmp_path)
    assert reloaded.entries[asset.asset_id].path == str(asset.relative_path())
    assert reloaded.has(asset) is True


def test_a_failed_save_leaves_no_temp_file_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupted save must not litter, and must not truncate the existing index."""
    asset = make_asset()
    landed = place(tmp_path, asset)
    manifest = Manifest(tmp_path)
    manifest.record(asset, landed, sha256="abc", source_size=8)
    manifest.save()
    before = (tmp_path / MANIFEST_NAME).read_text(encoding="utf-8")

    def boom(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("seesaw_dl.manifest.os.replace", boom)
    with pytest.raises(KeyboardInterrupt):
        manifest.save()

    assert (tmp_path / MANIFEST_NAME).read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob(".manifest-*.tmp"))
