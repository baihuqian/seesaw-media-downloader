"""Rendering a plan for the user: a table by default, JSON with ``--json``."""

from __future__ import annotations

import json
from typing import Any

from rich.table import Table

from .planner import Plan, PlannedAsset


def asset_record(entry: PlannedAsset, include_presence: bool) -> dict[str, Any]:
    asset = entry.asset
    record: dict[str, Any] = {
        "asset_id": asset.asset_id,
        "item_id": asset.item_id,
        "child": asset.child_name,
        "class": asset.class_name,
        "created_at": asset.created_at.isoformat(),
        "kind": asset.kind,
        "page": asset.page_index + 1,
        "path": str(asset.relative_path()),
    }
    if include_presence:
        record["already_present"] = entry.already_present
    return record


def plan_table(plan: Plan, include_presence: bool) -> Table:
    table = Table(show_lines=False, header_style="bold")
    table.add_column("Date", no_wrap=True)
    table.add_column("Child")
    table.add_column("Kind", no_wrap=True)
    table.add_column("Pg", justify="right", no_wrap=True)
    table.add_column("Destination")
    if include_presence:
        table.add_column("Have?", no_wrap=True)

    for entry in plan.assets:
        asset = entry.asset
        row = [
            asset.created_at.strftime("%Y-%m-%d %H:%M"),
            asset.child_name,
            asset.kind,
            str(asset.page_index + 1),
            str(asset.relative_path()),
        ]
        if include_presence:
            row.append("yes" if entry.already_present else "no")
        table.add_row(*row)
    return table


def plan_json(plan: Plan, include_presence: bool) -> str:
    """One JSON object per asset, then a summary object -- newline delimited for ``jq``."""
    lines = [
        json.dumps(asset_record(entry, include_presence)) for entry in plan.assets
    ]
    lines.append(
        json.dumps(
            {
                "summary": {
                    "assets": len(plan),
                    "would_download": len(plan.to_download),
                    "already_present": plan.present_count,
                }
            }
        )
    )
    return "\n".join(lines)
