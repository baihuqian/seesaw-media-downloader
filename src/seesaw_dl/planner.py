"""Turning the feed into a concrete plan.

The plan is the single source of truth shared by ``--list-only`` and a real download, so
what the listing shows is exactly what a download would fetch.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .api import SeesawClient
from .logging import Reporter
from .models import FeedItem, MediaAsset

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


def build_plan(
    client: SeesawClient,
    reporter: Reporter,
    since: datetime | None = None,
    is_present: PresenceCheck | None = None,
) -> Plan:
    """Walk every child and class and collect the downloadable assets."""
    planned: list[PlannedAsset] = []
    seen_assets: set[str] = set()

    for child in client.children():
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
