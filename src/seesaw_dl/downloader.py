"""Fetching the planned assets.

Downloads run concurrently but politely, stream to a temporary file and are moved into
place only once complete, so an interrupted run never leaves a half-written photo that a
later ``--skip-existing`` would mistake for finished work.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .errors import DownloadError
from .logging import Reporter
from .manifest import Manifest
from .metadata import stamp
from .models import FeedItem
from .planner import Plan, PlannedAsset

CHUNK_SIZE = 1 << 16
MAX_ATTEMPTS = 3


@dataclass
class Outcome:
    entry: PlannedAsset
    status: str  # "downloaded" | "skipped" | "failed"
    path: Path | None = None
    size: int = 0
    error: str | None = None


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def downloaded(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status == "downloaded"]

    @property
    def skipped(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status == "skipped"]

    @property
    def failed(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status == "failed"]

    @property
    def total_bytes(self) -> int:
        return sum(o.size for o in self.downloaded)


def run_downloads(
    plan: Plan,
    root: Path,
    manifest: Manifest,
    reporter: Reporter,
    concurrency: int = 4,
    cookies: dict[str, str] | None = None,
) -> Report:
    """Synchronous entry point: download everything the plan says to download."""
    return asyncio.run(_run(plan, root, manifest, reporter, concurrency, cookies or {}))


async def _run(
    plan: Plan,
    root: Path,
    manifest: Manifest,
    reporter: Reporter,
    concurrency: int,
    cookies: dict[str, str],
) -> Report:
    started = time.monotonic()
    report = Report()
    semaphore = asyncio.Semaphore(concurrency)
    # The manifest is shared mutable state; serialise writes to it.
    lock = asyncio.Lock()

    root.mkdir(parents=True, exist_ok=True)
    for entry in plan.assets:
        if not entry.should_download:
            report.outcomes.append(
                Outcome(entry=entry, status="skipped", path=root / entry.asset.relative_path())
            )
            reporter.info(f"skip  {entry.asset.asset_id} (already downloaded)")

    pending = [entry for entry in plan.assets if entry.should_download]
    if pending:
        async with httpx.AsyncClient(
            cookies=cookies, timeout=120.0, follow_redirects=True
        ) as client:
            tasks = [
                _download_one(entry, root, manifest, reporter, client, semaphore, lock)
                for entry in pending
            ]
            report.outcomes.extend(await asyncio.gather(*tasks))

    _write_sidecars(plan, root, reporter)
    async with lock:
        manifest.save()
    report.elapsed = time.monotonic() - started
    return report


async def _download_one(
    entry: PlannedAsset,
    root: Path,
    manifest: Manifest,
    reporter: Reporter,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    lock: asyncio.Lock,
) -> Outcome:
    asset = entry.asset
    destination = root / asset.relative_path()
    source = Path(asset.asset_id).name

    async with semaphore:
        for attempt in range(MAX_ATTEMPTS):
            try:
                size, digest = await _fetch(client, asset.url, destination)
            except (httpx.HTTPError, DownloadError, OSError) as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    reporter.error(f"failed {source}: {exc}")
                    return Outcome(entry=entry, status="failed", error=str(exc))
                delay = 2.0**attempt
                reporter.warn(f"retrying {source} in {delay:.0f}s -- {exc}")
                await asyncio.sleep(delay)
                continue

            # Timestamps are what make the file sort correctly in a photo library.
            # This grows the file, so the manifest is written afterwards -- recording the
            # served length here would make every later presence check miss.
            await asyncio.to_thread(
                stamp,
                destination,
                asset.created_at,
                reporter,
                asset.caption,
                asset.item_id,
            )
            async with lock:
                manifest.record(asset, destination, sha256=digest, source_size=size)
            reporter.info(f"{source} -> {asset.relative_path()}")
            return Outcome(entry=entry, status="downloaded", path=destination, size=size)

    return Outcome(entry=entry, status="failed", error="exhausted retries")


async def _fetch(client: httpx.AsyncClient, url: str, destination: Path) -> tuple[int, str]:
    """Stream ``url`` to ``destination`` atomically, returning ``(size, sha256)``."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    digest = hashlib.sha256()
    written = 0

    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            expected = response.headers.get("Content-Length")
            with open(partial, "wb") as stream:
                async for chunk in response.aiter_bytes(CHUNK_SIZE):
                    stream.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                stream.flush()
                os.fsync(stream.fileno())

        if expected is not None and written != int(expected):
            raise DownloadError(
                f"truncated download: got {written} bytes, expected {expected}"
            )
        if written == 0:
            raise DownloadError("server returned an empty file")

        # Only now does the file exist under its real name: a partial download can never
        # be mistaken for a finished one by a later --skip-existing run.
        os.replace(partial, destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    return written, digest.hexdigest()


def _write_sidecars(plan: Plan, root: Path, reporter: Reporter) -> None:
    """One metadata file per post, next to that post's media."""
    seen: dict[str, FeedItem] = {}
    for entry in plan.assets:
        seen.setdefault(entry.item.item_id, entry.item)

    for item in seen.values():
        assets = item.assets()
        if not assets:
            continue
        folder = root / assets[0].relative_path().parent
        if not folder.exists():
            continue  # nothing from this post landed; no sidecar to write
        target = folder / f"{assets[0].filename.rsplit('_p', 1)[0]}.json"
        try:
            target.write_text(json.dumps(item.sidecar(), indent=1), encoding="utf-8")
        except OSError as exc:
            reporter.warn(f"could not write {target.name}: {exc}")
