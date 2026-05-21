"""End-to-end ETL: discover -> download -> parse -> dedupe -> load SQLite."""
from __future__ import annotations

import sys
from pathlib import Path

# Windows consoles default to cp1252 and choke on accented resource names.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from .fetch import discover_resources, download_resource, file_sha256
from .load import dedupe, write_db
from .parse import parse_xlsx

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "dativos.db"


def run() -> int:
    print(f"[etl] discovering CKAN resources...")
    resources = discover_resources()
    print(f"[etl] found {len(resources)} XLSX resources")
    if not resources:
        print("[etl] nothing to do", file=sys.stderr)
        return 1

    all_rows: list = []
    sources_meta: list[dict] = []
    for res in resources:
        print(f"[etl] downloading {res.resource_name} ({res.resource_id})")
        path = download_resource(res, RAW_DIR)
        sha = file_sha256(path)
        rows = parse_xlsx(path)
        print(f"[etl]   parsed {len(rows)} rows")
        all_rows.extend((r, res.resource_id, res.last_modified) for r in rows)
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

    deduped = dedupe(all_rows)
    print(f"[etl] dedupe: {len(all_rows)} -> {len(deduped)} unique rows")

    summary = write_db(DB_PATH, deduped, sources_meta)
    print(f"[etl] wrote {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
