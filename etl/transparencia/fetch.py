"""Discover and download the ES Transparência Advogados Dativos files.

The portal exposes a treegrid with annual "Acumulado" XLSX files. The structure
is queried via /Comum/AdvogadosDativos/ObterFilhos/<parent_id>, which returns
small HTML fragments containing <a href="/Comum/AdvogadosDativos/Download/<id>">.

These are stable IDs — once a year's "Acumulado" file is published, its
download ID does not change (only the contents of the XLSX behind it).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import requests

BASE = "https://transparencia.es.gov.br"
ROOT_PARENTS = [3, 4, 5, 32, 62, 86, 95, 103, 108, 115]
USER_AGENT = "Mozilla/5.0 (dativos-etl https://github.com/yurivix/dativos)"
TIMEOUT = 60

# Captures <a href="/Comum/AdvogadosDativos/Download/123">Label</a>
LINK_RE = re.compile(
    r'href="(/Comum/AdvogadosDativos/Download/(\d+))"[^>]*>\s*([^<]+?)\s*<',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TransparenciaFile:
    download_id: int
    label: str
    parent_id: int
    url: str


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    return s


def discover_files(session: requests.Session | None = None) -> list[TransparenciaFile]:
    """Walk the known parent IDs and collect every linked Download URL."""
    s = session or _session()
    out: list[TransparenciaFile] = []
    for parent in ROOT_PARENTS:
        r = s.get(
            f"{BASE}/Comum/AdvogadosDativos/ObterFilhos/{parent}",
            params={"NivelAnterior": 0},
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        for m in LINK_RE.finditer(r.text):
            path, did, label = m.groups()
            out.append(
                TransparenciaFile(
                    download_id=int(did),
                    label=_unescape_html_entities(label.strip()),
                    parent_id=parent,
                    url=f"{BASE}{path}",
                )
            )
    return out


def _unescape_html_entities(s: str) -> str:
    """Lightweight unescape for the few entities the portal emits (&nbsp;, &#xxx;)."""
    import html

    return html.unescape(s)


def download_file(
    f: TransparenciaFile,
    raw_dir: Path,
    session: requests.Session | None = None,
) -> Path:
    """Download a TransparenciaFile to raw_dir/<download_id>.xlsx (or .pdf).

    Content-type from the response decides the extension. Legislation files
    return application/pdf; the data files return XLSX.
    """
    s = session or _session()
    raw_dir.mkdir(parents=True, exist_ok=True)
    r = s.get(f.url, timeout=TIMEOUT, stream=True)
    r.raise_for_status()
    ct = (r.headers.get("content-type") or "").lower()
    ext = "pdf" if "pdf" in ct else "xlsx"
    dest = raw_dir / f"transparencia_{f.download_id}.{ext}"
    with dest.open("wb") as fh:
        for chunk in r.iter_content(65536):
            if chunk:
                fh.write(chunk)
    return dest


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_data_xlsx(f: TransparenciaFile) -> bool:
    """Filter helper — selects the yearly Acumulado XLSX files only."""
    return "Acumulado" in f.label and not f.label.lower().startswith("decreto")
