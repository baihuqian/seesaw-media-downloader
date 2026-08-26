"""HTTP client for Seesaw's private API.

Once :mod:`seesaw_dl.auth` has captured a session, no browser is involved: the cookies and
``_xsrf`` token are enough to replay every endpoint the web app uses. Nothing here writes
to the account -- in particular we never POST ``item/update_seen_state_v2``, so downloading
does not mark posts as read.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import httpx

from .auth import SESSION_REJECTED_MESSAGE, Session
from .errors import ApiContractError, AuthError, SeesawError
from .logging import Reporter
from .models import Child, FeedItem, SchoolClass

BASE_URL = "https://app.seesaw.me"
BUNDLE = "me.see-saw.web_magiccam"
FALLBACK_RELEASE = "prod_2026-08-25.3"

# Endpoints observed in the live web app. If Seesaw moves them the client fails loudly
# with ApiContractError rather than silently downloading nothing.
DASHBOARD = "/api/person/parent/dashboard_v3"
CHILD_CLASSES = "/api/person/parent/child_classes"
CLASS_FEED = "/api/person/parent/class_feed"
ITEM = "/api/item_v2"

DEFAULT_PAGE_SIZE = 50
MAX_RETRIES = 4


class SeesawClient:
    """A thin, read-only client over the private API."""

    def __init__(
        self,
        session: Session,
        reporter: Reporter,
        client: httpx.Client | None = None,
        tz_offset_seconds: int | None = None,
    ) -> None:
        self.session = session
        self.reporter = reporter
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=BASE_URL, cookies=session.cookies, timeout=30.0
        )
        self._tz_offset = (
            tz_offset_seconds if tz_offset_seconds is not None else _local_tz_offset()
        )

    def __enter__(self) -> SeesawClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- plumbing ---------------------------------------------------------------

    def _params(self, **extra: Any) -> dict[str, Any]:
        params: dict[str, Any] = {
            "_bundle": BUNDLE,
            "_release": self.session.release or FALLBACK_RELEASE,
            "_tz_offset": self._tz_offset,
            "_xsrf": self.session.xsrf,
        }
        params.update({k: v for k, v in extra.items() if v is not None})
        return params

    def _get(self, path: str, **extra: Any) -> dict[str, Any]:
        params = self._params(**extra)
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            self.reporter.debug(f"GET {path} {_loggable(params)}")
            try:
                response = self._client.get(path, params=params)
            except httpx.HTTPError as exc:  # network-level failure
                last_error = exc
                self._backoff(attempt, f"{path}: {exc}")
                continue

            if response.status_code in (401, 403):
                raise AuthError(SESSION_REJECTED_MESSAGE)
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = _retry_after(response)
                self._backoff(attempt, f"{path}: HTTP {response.status_code}", retry_after)
                last_error = SeesawError(f"HTTP {response.status_code} from {path}")
                continue
            if response.status_code != 200:
                raise SeesawError(f"HTTP {response.status_code} from {path}")

            return _unwrap(response, path)

        raise SeesawError(f"Giving up on {path} after {MAX_RETRIES} attempts: {last_error}")

    def _backoff(self, attempt: int, why: str, retry_after: float | None = None) -> None:
        delay = retry_after if retry_after is not None else 2.0**attempt
        self.reporter.warn(f"retrying in {delay:.0f}s -- {why}")
        time.sleep(delay)

    # -- account ----------------------------------------------------------------

    def children(self) -> list[Child]:
        data = self._get(DASHBOARD)
        parent = data.get("parent")
        if not isinstance(parent, dict):
            raise ApiContractError(
                "The dashboard payload had no 'parent' object. Seesaw has probably changed "
                f"its web app; keys present: {sorted(data)[:12]}"
            )
        objects = (parent.get("children") or {}).get("objects") or []
        return [Child.parse(child) for child in objects]

    def classes(self, child: Child) -> list[SchoolClass]:
        """Active *and* archived classes, so previous school years stay reachable."""
        data = self._get(CHILD_CLASSES, child_id=child.person_id)

        # The live shape is a bare {"objects": [...]} containing both active and archived
        # classes; the split-key and nested shapes are tolerated in case it changes back.
        classes = [SchoolClass.parse(entry) for entry in _objects(data)]
        for key, archived in (("classes", False), ("archived_classes", True)):
            classes.extend(
                SchoolClass.parse(entry, archived=archived) for entry in _objects(data.get(key))
            )
        if not classes:
            raise ApiContractError(
                f"No classes found for {child.display_name}. The child_classes payload had "
                f"keys {sorted(data)[:12]}, none of which held a class list."
            )
        return classes

    # -- feed -------------------------------------------------------------------

    def iter_class_feed(
        self,
        child: Child,
        school_class: SchoolClass,
        since: datetime | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Iterator[FeedItem]:
        """Yield journal items, stopping early once a whole page predates ``since``.

        The feed is ordered newest-first, but stopping at the *first* older item would
        truncate the run if that ordering ever wobbles (a pinned or back-dated post is
        enough). Instead, older items are skipped individually and pagination stops only
        when a page yields nothing new at all.
        """
        start_key: str | None = None
        seen = 0
        skipped = 0
        while True:
            data = self._get(
                CLASS_FEED,
                child_id=child.person_id,
                class_id=school_class.class_id,
                limit=page_size,
                start_key=start_key,
            )
            items = data.get("items")
            if not isinstance(items, dict):
                raise ApiContractError(
                    "The class feed payload had no 'items' object. Seesaw has probably "
                    f"changed its web app; keys present: {sorted(data)[:12]}"
                )
            objects = items.get("objects") or []
            yielded_here = 0
            for entry in objects:
                item = FeedItem.parse(entry, child.display_name, school_class.name)
                if since is not None and item.created_at < since:
                    skipped += 1
                    continue
                yielded_here += 1
                seen += 1
                yield item

            start_key = items.get("last_key")
            if not objects or not start_key:
                self.reporter.debug(
                    f"feed exhausted for {school_class.name} after {seen} items"
                )
                return
            if since is not None and objects and yielded_here == 0:
                self.reporter.debug(
                    f"whole page predates the --since cutoff; stopping after {seen} items "
                    f"({skipped} older ones skipped)"
                )
                return

    def full_item(self, item: FeedItem) -> FeedItem:
        """Re-fetch an item so every page is present.

        Feed responses carry only the first page, which is the same gap that makes
        Seesaw's own export lossy.
        """
        data = self._get(ITEM, item_id=item.item_id)
        payload = data.get("item")
        if not isinstance(payload, dict):
            raise ApiContractError(
                f"The item payload for {item.item_id} had no 'item' object. "
                f"Keys present: {sorted(data)[:12]}"
            )
        return FeedItem.parse(payload, item.child_name, item.class_name)


def _local_tz_offset() -> int:
    """Seconds east of UTC, matching the web app's ``_tz_offset`` (e.g. -18000 for EST)."""
    return -(time.altzone if time.daylight and time.localtime().tm_isdst else time.timezone)


def _objects(value: Any) -> list[dict[str, Any]]:
    """Seesaw wraps most lists as ``{"objects": [...]}`` but sometimes returns a bare list."""
    if isinstance(value, dict):
        return list(value.get("objects") or [])
    if isinstance(value, list):
        return list(value)
    return []


def _unwrap(response: httpx.Response, path: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise ApiContractError(f"{path} returned a non-JSON body") from exc
    if not isinstance(body, dict):
        raise ApiContractError(f"{path} returned {type(body).__name__}, expected an object")
    if body.get("status") not in (None, "OK"):
        raise SeesawError(f"{path} returned status {body.get('status')}: {body.get('error_dict')}")
    payload = body.get("response", body)
    if not isinstance(payload, dict):
        raise ApiContractError(f"{path} returned a 'response' that is not an object")
    return payload


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _loggable(params: dict[str, Any]) -> dict[str, Any]:
    """The reporter redacts the token, but keep it out of the message in the first place."""
    return {k: v for k, v in params.items() if k not in {"_xsrf", "_bundle", "_release"}}
