"""The download index.

``manifest.json`` lives in the output directory and records what has already been
fetched, keyed on the asset's **storage path** rather than its URL -- Seesaw re-signs
media URLs constantly, so the URL is not an identity.

The manifest is a convenience, not the truth: a file the user deleted is not "present"
just because the manifest says so, and a file that exists with the right size is treated
as present even if the manifest was lost. That keeps skip-existing honest in both
directions.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import MediaAsset

MANIFEST_NAME = "manifest.json"
VERSION = 1


@dataclass
class Entry:
    """One downloaded asset.

    ``size`` is the size **on disk**, which is what presence checks compare against. That
    is deliberately not the number of bytes Seesaw served: EXIF stamping adds an APP1
    segment afterwards, so the served length (kept as ``source_size``) is a few hundred
    bytes smaller. ``sha256`` hashes the served bytes, so it identifies the source file
    rather than our stamped copy.
    """

    asset_id: str
    path: str
    size: int
    sha256: str
    item_id: str
    downloaded_at: str
    source_size: int = 0


class Manifest:
    """An index of downloaded assets, loaded from and saved to the output directory."""

    def __init__(self, root: Path, entries: dict[str, Entry] | None = None) -> None:
        self.root = root
        self.entries: dict[str, Entry] = entries or {}

    @property
    def path(self) -> Path:
        return self.root / MANIFEST_NAME

    @classmethod
    def load(cls, root: Path) -> Manifest:
        path = root / MANIFEST_NAME
        if not path.exists():
            return cls(root)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A corrupt index must not block a download; presence falls back to the disk.
            return cls(root)
        entries = {}
        for item in raw.get("assets", []):
            try:
                entries[item["asset_id"]] = Entry(**item)
            except (KeyError, TypeError):
                continue
        return cls(root, entries)

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "version": VERSION,
            "updated_at": datetime.now(UTC).isoformat(),
            "assets": [asdict(entry) for entry in sorted(self.entries.values(), key=_key)],
        }
        # Written through a temp file so an interrupted save cannot truncate the index.
        fd, temp_name = tempfile.mkstemp(dir=self.root, prefix=".manifest-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=1)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def record(self, asset: MediaAsset, path: Path, sha256: str, source_size: int) -> None:
        """Record a finished download. Call *after* metadata stamping, not before."""
        self.entries[asset.asset_id] = Entry(
            asset_id=asset.asset_id,
            path=str(path.relative_to(self.root)),
            size=path.stat().st_size,
            sha256=sha256,
            item_id=asset.item_id,
            downloaded_at=datetime.now(UTC).isoformat(),
            source_size=source_size,
        )

    def has(self, asset: MediaAsset) -> bool:
        """Is this asset already on disk?

        Trusts the filesystem over the index: a recorded file that has since been deleted
        is not present, and an unrecorded file that is already sitting at the expected
        path (a restored backup, a lost manifest) is.
        """
        entry = self.entries.get(asset.asset_id)
        if entry is not None:
            recorded = self.root / entry.path
            return recorded.exists() and recorded.stat().st_size == entry.size
        expected = self.root / asset.relative_path()
        return expected.exists() and expected.stat().st_size > 0


def _key(entry: Entry) -> tuple[str, str]:
    return (entry.downloaded_at, entry.asset_id)
