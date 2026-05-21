"""Discover and download PGE-ES dativos XLSX files from the CKAN portal."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import requests

CKAN_BASE = "https://dados.es.gov.br/api/3/action"
SEARCH_QUERY = "dativo"
SEARCH_ROWS = 100
TIMEOUT = 30


@dataclass(frozen=True)
class Resource:
    package_id: str
    package_title: str
    resource_id: str
    resource_name: str
    url: str
    last_modified: str | None
    created: str | None


def discover_resources(session: requests.Session | None = None) -> list[Resource]:
    """Search CKAN for all 'dativo' packages and return their XLSX resources."""
    s = session or requests.Session()
    r = s.get(
        f"{CKAN_BASE}/package_search",
        params={"q": SEARCH_QUERY, "rows": SEARCH_ROWS},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    payload = r.json()
    if not payload.get("success"):
        raise RuntimeError(f"CKAN package_search failed: {payload}")
    results = payload["result"]["results"]
    out: list[Resource] = []
    for pkg in results:
        for res in pkg.get("resources", []):
            if (res.get("format") or "").upper() != "XLSX":
                continue
            out.append(
                Resource(
                    package_id=pkg["id"],
                    package_title=pkg.get("title", ""),
                    resource_id=res["id"],
                    resource_name=res.get("name", ""),
                    url=res["url"],
                    last_modified=res.get("last_modified"),
                    created=res.get("created"),
                )
            )
    return out


def download_resource(
    res: Resource,
    raw_dir: Path,
    session: requests.Session | None = None,
) -> Path:
    """Download an XLSX to raw_dir/<resource_id>.xlsx. Returns the local path."""
    s = session or requests.Session()
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / f"{res.resource_id}.xlsx"
    r = s.get(res.url, timeout=TIMEOUT)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
