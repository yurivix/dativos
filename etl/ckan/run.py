"""Collect CKAN aggregate data — returns Python data structures, no DB writes.

The orchestrator (etl/__main__.py) is responsible for materializing this into
DuckDB. CKAN here is the *secondary* source — used for reconciliation against
the individual-level transparencia data.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .fetch import Resource, discover_resources, download_resource, file_sha256
from .parse import Row, parse_xlsx


def dedupe(
    rows: Iterable[tuple[Row, str, str | None]],
) -> list[tuple[Row, str, str | None]]:
    """Resolve overlapping rows from multiple CKAN snapshots.

    Strategy: for each (period_start, period_end) key, keep the row whose
    source file has the most recent `last_modified`. Ties or missing
    timestamps fall back to iteration order.
    """
    best: dict[tuple[str, str], tuple[Row, str, str | None]] = {}
    for entry in rows:
        row, _src, modified = entry
        key = (row.period_start.isoformat(), row.period_end.isoformat())
        if key not in best:
            best[key] = entry
            continue
        _, _, prev_mod = best[key]
        if (modified or "") > (prev_mod or ""):
            best[key] = entry
    return list(best.values())


def collect(raw_dir: Path) -> dict:
    """Discover all CKAN dativos resources, download, parse, dedupe.

    Returns a dict with:
      rows: list of (Row, source_resource_id, source_last_modified) tuples
      sources: list of dicts with resource metadata + file_sha256
    """
    resources = discover_resources()
    all_rows: list[tuple[Row, str, str | None]] = []
    sources_meta: list[dict] = []
    for res in resources:
        path = download_resource(res, raw_dir)
        sha = file_sha256(path)
        parsed = parse_xlsx(path)
        all_rows.extend((r, res.resource_id, res.last_modified) for r in parsed)
        sources_meta.append(
            {
                "resource_id": res.resource_id,
                "package_id": res.package_id,
                "package_title": res.package_title,
                "resource_name": res.resource_name,
                "url": res.url,
                "last_modified": res.last_modified,
                "created": res.created,
                "file_sha256": sha,
            }
        )
    return {"rows": dedupe(all_rows), "sources": sources_meta}
