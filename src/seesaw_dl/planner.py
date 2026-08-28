"""Turning the feed into a concrete plan.

The plan is the single source of truth shared by ``--list-only`` and a real download, so
what the listing shows is exactly what a download would fetch.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .api import SeesawClient
from .errors import ConfigError
from .logging import Reporter
from .models import Child, FeedItem, MediaAsset

#: Answers "do we already have this asset?" -- supplied by the manifest in later slices.
PresenceCheck = Callable[[MediaAsset], bool]


@dataclass
class PlannedAsset:
    asset: MediaAsset
    item: FeedItem
    already_present: bool = False

    @property
    def should_download(self) -> bool:
        return not self.already_present


@dataclass
class Plan:
    assets: list[PlannedAsset]

    @property
    def to_download(self) -> list[PlannedAsset]:
        return [entry for entry in self.assets if entry.should_download]

    @property
    def present_count(self) -> int:
        return sum(1 for entry in self.assets if entry.already_present)

    def __len__(self) -> int:
        return len(self.assets)


def resolve_child(client: SeesawClient, reporter: Reporter, wanted: str | None) -> Child:
    """Pick the one child this run covers.

    A run is deliberately single-child: the output layout no longer carries a name, so
    downloading two children into one directory would interleave them past telling apart.
    With a single child on the account there is nothing to ask, so ``--child`` is optional
    until it is genuinely needed.
    """
    children = client.children()
    if not children:
        raise ConfigError(
            "No children found on this account. If that is wrong, sign in again with "
            "`seesaw-dl login --force`."
        )

    if wanted is None:
        if len(children) == 1:
            reporter.debug(f"one child on the account: {children[0].display_name}")
            return children[0]
        raise ConfigError(
            f"Found {len(children)} children on this account. Pick one with --child "
            "(or set SEESAW_CHILD):\n" + _bullets(children)
        )

    matched = [child for child in children if child.matches(wanted)]
    if len(matched) == 1:
        return matched[0]
    if not matched:
        raise ConfigError(
            f"No child on this account matches --child {wanted!r}. Available:\n"
            + _bullets(children)
        )
    raise ConfigError(
        f"--child {wanted!r} matches {len(matched)} children. Use a full name:\n"
        + _bullets(matched)
    )


def _bullets(children: list[Child]) -> str:
    return "\n".join(f"  - {child.display_name}" for child in children)


def build_plan(
    client: SeesawClient,
    reporter: Reporter,
    child: Child,
    since: datetime | None = None,
    is_present: PresenceCheck | None = None,
) -> Plan:
    """Walk one child's classes and collect the downloadable assets."""
    planned: list[PlannedAsset] = []
    seen_assets: set[str] = set()

    classes = client.classes(child)
    reporter.debug(f"{child.display_name}: {len(classes)} class(es)")
    for school_class in classes:
        label = f"{child.display_name} / {school_class.name}"
        reporter.debug(f"reading feed for {label}")
        for feed_item in client.iter_class_feed(child, school_class, since=since):
            item = _ensure_all_pages(client, feed_item, reporter)
            for asset in item.assets():
                if asset.asset_id in seen_assets:
                    # The same photo can surface in more than one class feed.
                    reporter.debug(f"skipping duplicate asset {asset.asset_id}")
                    continue
                seen_assets.add(asset.asset_id)
                planned.append(
                    PlannedAsset(
                        asset=asset,
                        item=item,
                        already_present=bool(is_present and is_present(asset)),
                    )
                )

    planned.sort(key=lambda entry: (entry.asset.created_at, entry.asset.filename))
    return Plan(assets=planned)


def _ensure_all_pages(client: SeesawClient, item: FeedItem, reporter: Reporter) -> FeedItem:
    """Feed responses carry only page 1; re-fetch anything with more."""
    if not item.needs_full_fetch:
        return item
    reporter.debug(
        f"{item.item_id} reports {item.num_pages} pages but the feed sent "
        f"{len(item.pages)}; fetching the full item"
    )
    return client.full_item(item)
