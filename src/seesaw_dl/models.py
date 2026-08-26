"""Typed views over Seesaw's private API payloads.

Seesaw's JSON is wide and undocumented, so these models read the handful of fields we
actually need and keep the rest at arm's length. Anything structural that goes missing
raises :class:`~seesaw_dl.errors.ApiContractError` rather than producing a half-built
object, because a silent partial download is worse than a loud failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from .errors import ApiContractError

#: Media we know how to name. Anything else is downloaded with its own extension.
_EXTENSION_KINDS = {
    ".jpg": "photo",
    ".jpeg": "photo",
    ".png": "photo",
    ".gif": "photo",
    ".heic": "photo",
    ".webp": "photo",
    ".mp4": "video",
    ".mov": "video",
    ".m4v": "video",
    ".webm": "video",
    ".m4a": "audio",
    ".mp3": "audio",
    ".wav": "audio",
    ".pdf": "pdf",
}


def _require(data: dict[str, Any], key: str, context: str) -> Any:
    if key not in data:
        raise ApiContractError(
            f"Expected {key!r} in the {context} payload from Seesaw, but it was not there. "
            f"Seesaw has probably changed its web app; keys present: {sorted(data)[:12]}"
        )
    return data[key]


@dataclass(frozen=True)
class Child:
    person_id: str
    display_name: str

    @classmethod
    def parse(cls, data: dict[str, Any]) -> Child:
        person_id = _require(data, "person_id", "child")
        name = data.get("display_name") or " ".join(
            part for part in (data.get("first_name"), data.get("last_name")) if part
        )
        return cls(person_id=person_id, display_name=name or person_id)


@dataclass(frozen=True)
class SchoolClass:
    class_id: str
    name: str
    archived: bool = False

    @classmethod
    def parse(cls, data: dict[str, Any], archived: bool = False) -> SchoolClass:
        class_id = _require(data, "class_id", "class")
        return cls(
            class_id=class_id,
            name=data.get("name") or class_id,
            archived=bool(data.get("is_archived", archived)),
        )


@dataclass(frozen=True)
class MediaAsset:
    """One downloadable file.

    ``asset_id`` is the storage path Seesaw serves the file from (for example
    ``us-2/a/b/c/d/e/f/1a2b3c4d-...jpg``). Download URLs carry an expiring signature, so
    the path -- not the URL -- is what the manifest keys on.
    """

    asset_id: str
    url: str
    kind: str
    item_id: str
    page_index: int
    created_at: datetime
    child_name: str
    class_name: str
    caption: str = ""
    width: int | None = None
    height: int | None = None

    @property
    def extension(self) -> str:
        suffix = PurePosixPath(self.asset_id).suffix.lower()
        return suffix or ".bin"

    @property
    def filename(self) -> str:
        stamp = self.created_at.strftime("%Y-%m-%dT%H-%M-%S")
        return f"{stamp}_{_short(self.item_id)}_p{self.page_index + 1}{self.extension}"

    def relative_path(self) -> PurePosixPath:
        """``<Child>/<year>/<YYYY-MM-DD>/<filename>``."""
        day = self.created_at.strftime("%Y-%m-%d")
        return PurePosixPath(_safe(self.child_name), day[:4], day, self.filename)


@dataclass
class FeedItem:
    item_id: str
    created_at: datetime
    child_name: str
    class_name: str
    caption: str = ""
    num_pages: int = 1
    pages: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_full_fetch(self) -> bool:
        """Feed responses carry only the first page of a multi-page item.

        This is exactly the gap that makes Seesaw's own export lossy, so any item whose
        page count exceeds what we were handed has to be re-fetched via ``item_v2``.
        """
        return self.num_pages > len(self.pages)

    @classmethod
    def parse(cls, data: dict[str, Any], child_name: str, class_name: str) -> FeedItem:
        item = data.get("item", data)
        item_id = _require(item, "item_id", "feed item")
        created = _require(item, "create_date", "feed item")
        pages = item.get("pages", {}).get("objects", []) or []
        return cls(
            item_id=item_id,
            created_at=_local_datetime(created),
            child_name=child_name,
            class_name=item.get("class_name") or class_name,
            caption=_caption(item),
            num_pages=int(item.get("num_pages") or max(1, len(pages))),
            pages=list(pages),
            raw=item,
        )

    def assets(self) -> list[MediaAsset]:
        """Every downloadable file on this item, one per page."""
        found: list[MediaAsset] = []
        for index, page in enumerate(self.pages):
            url = _page_url(page)
            if url is None:
                continue
            asset_id = _asset_id(url)
            size = page.get("composite_image_size") or {}
            found.append(
                MediaAsset(
                    asset_id=asset_id,
                    url=url,
                    kind=_kind_for(asset_id),
                    item_id=self.item_id,
                    page_index=index,
                    created_at=self.created_at,
                    child_name=self.child_name,
                    class_name=self.class_name,
                    caption=self.caption,
                    width=size.get("width"),
                    height=size.get("height"),
                )
            )
        return found

    def sidecar(self) -> dict[str, Any]:
        """The post's metadata, saved next to its media."""
        return {
            "item_id": self.item_id,
            "created_at": self.created_at.isoformat(),
            "created_at_epoch": self.created_at.timestamp(),
            "child": self.child_name,
            "class": self.class_name,
            "caption": self.caption,
            "num_pages": self.num_pages,
        }


def _local_datetime(epoch: float | str) -> datetime:
    """Seesaw timestamps are epoch seconds; render them in the user's own timezone.

    Dates drive the folder layout, so local time is what matters: a post made at 9pm
    should land under that evening's date, not the next day's UTC one.
    """
    return datetime.fromtimestamp(float(epoch)).astimezone()


def _caption(item: dict[str, Any]) -> str:
    for key in ("caption", "text", "item_text", "note", "title"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


#: Ordered by preference: the first is the unresized original, the rest are fallbacks.
#: ``*_resizable_url`` deliberately comes last -- it is served through Seesaw's image
#: proxy and yields a display-sized rendition, not the original file.
_PAGE_URL_KEYS = (
    "composite_image_url",
    "video_url",
    "audio_url",
    "attachment_url",
    "pdf_url",
    "original_url",
    "composite_image_resizable_url",
)


def _page_url(page: dict[str, Any]) -> str | None:
    for key in _PAGE_URL_KEYS:
        value = page.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    return None


_ASSET_PATH = re.compile(r"https?://[^/]+/(?P<path>[^:?#]+)")


def _asset_id(url: str) -> str:
    """The stable storage path inside a signed Seesaw asset URL.

    ``https://assets.seesaw.me/us-2/f/8/.../x.jpg:::1786762368:::1209600:::1:::<sig>``
    becomes ``us-2/f/8/.../x.jpg`` -- identical across re-signings, which is what makes
    skip-existing reliable.
    """
    match = _ASSET_PATH.match(url)
    if not match:
        return url
    return match.group("path")


def _kind_for(asset_id: str) -> str:
    return _EXTENSION_KINDS.get(PurePosixPath(asset_id).suffix.lower(), "file")


def _short(item_id: str) -> str:
    """``item.1a2b3c4d-5e6f-...`` -> ``1a2b3c4d``."""
    tail = item_id.split(".", 1)[-1]
    return tail.split("-", 1)[0] or tail


_UNSAFE = re.compile(r"[^\w \-.]+", re.UNICODE)


def _safe(name: str) -> str:
    """A filesystem-safe directory name, without collapsing distinct names together."""
    cleaned = _UNSAFE.sub("_", name).strip().strip(".")
    return cleaned or "unknown"
