"""End-to-end ETL orchestrator: transparencia + CKAN → DuckDB (anon + full)."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

# Windows consoles default to cp1252 and choke on accented names.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from . import anonymize as anon_mod
from .ckan.run import collect as collect_ckan
from .duckdb_loader import write_databases
from .transparencia.fetch import (
    discover_files,
    download_file,
    file_sha256,
    is_data_xlsx,
)
from .transparencia.parse import Payment, parse_xlsx

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
ANON_DB = ROOT / "data" / "dativos_anon.duckdb"
FULL_DB = ROOT / "data" / "dativos_full.duckdb"
SALT_PATH = ROOT / "data" / "salt.txt"


def extract_year(label: str) -> int | None:
    """'2024 - Acumulado' -> 2024; '2026 Acumulado' -> 2026."""
    import re
    m = re.search(r"\b(20\d{2})\b", label)
    return int(m.group(1)) if m else None


def run(build_full_db: bool = True, force_redownload: bool = False) -> int:
    """End-to-end ETL.

    Past-year XLSX files are immutable in practice; we keep them cached in
    `data/raw/` and skip the network call. Only the current year is always
    re-downloaded. Override with `force_redownload=True` (or DATIVOS_FORCE=1).
    """
    current_year = date.today().year
    force = force_redownload or os.environ.get("DATIVOS_FORCE", "").lower() in {"1", "true", "yes"}

    print("[etl] === transparencia.es.gov.br ===")
    files = [f for f in discover_files() if is_data_xlsx(f)]
    print(f"[etl] discovered {len(files)} data files (current_year={current_year})")
    files.sort(key=lambda f: extract_year(f.label) or 0)

    all_payments: list[Payment] = []
    transparencia_sources: list[dict] = []
    for f in files:
        ano = extract_year(f.label)
        if ano is None:
            print(f"[etl] WARN: cannot infer year from {f.label!r}; skipping")
            continue

        cached = RAW_DIR / f"transparencia_{f.download_id}.xlsx"
        is_current = (ano == current_year)
        use_cache = cached.exists() and cached.stat().st_size > 0 and not is_current and not force

        if use_cache:
            path = cached
            print(f"[etl] cache hit id={f.download_id} year={ano} ({cached.name})")
        else:
            why = "current year" if is_current else ("forced" if force else "no cache")
            print(f"[etl] downloading id={f.download_id} year={ano} [{why}]")
            path = download_file(f, RAW_DIR)

        sha = file_sha256(path)
        rows = list(parse_xlsx(path, ano=ano, download_id=f.download_id))
        print(f"[etl]   parsed {len(rows):>6} payments  sha256={sha[:12]}")
        all_payments.extend(rows)
        transparencia_sources.append(
            {
                "download_id": f.download_id,
                "label": f.label,
                "url": f.url,
                "file_sha256": sha,
                "last_modified": None,
                "rows_loaded": len(rows),
            }
        )

    print(f"[etl] total payments parsed: {len(all_payments):,}")

    # Build identities map keyed by nome_normalizado only. We accumulate every
    # masked CPF observed in `cpfs_vistos` for auditability. See the docstring
    # in etl/anonymize.py for why the masked CPF is not part of the key.
    salt = anon_mod.load_or_create_salt(SALT_PATH)
    print(f"[etl] salt loaded ({'env' if os.environ.get('DATIVOS_SALT') else 'file'})")
    identities: dict[str, anon_mod.AdvogadoIdentity] = {}
    cpfs_by_id: dict[str, set[str]] = {}
    for p in all_payments:
        key = anon_mod.normalize_name(p.nome)
        if key not in identities:
            adv_id = anon_mod.pseudonym(p.nome, None, salt)
            identities[key] = anon_mod.AdvogadoIdentity(
                advogado_id=adv_id,
                nome=p.nome,
                nome_normalizado=key,
                cpfs_vistos=(),
            )
            cpfs_by_id[adv_id] = set()
        if p.cpf_mascarado:
            cpfs_by_id[identities[key].advogado_id].add(p.cpf_mascarado)
    # Freeze cpfs_vistos as sorted tuples
    for k, ident in list(identities.items()):
        cpfs = tuple(sorted(cpfs_by_id[ident.advogado_id]))
        identities[k] = anon_mod.AdvogadoIdentity(
            advogado_id=ident.advogado_id,
            nome=ident.nome,
            nome_normalizado=ident.nome_normalizado,
            cpfs_vistos=cpfs,
        )
    multi_cpf = sum(1 for i in identities.values() if len(i.cpfs_vistos) > 1)
    print(f"[etl] identities: {len(identities):,} unique advogados  "
          f"({multi_cpf:,} com múltiplas máscaras de CPF)")

    print("[etl] === CKAN (reconciliation source) ===")
    ckan_payload = collect_ckan(RAW_DIR)
    print(f"[etl]   {len(ckan_payload['rows']):,} CKAN aggregate rows")

    print("[etl] === writing DuckDB ===")
    full = FULL_DB if build_full_db else None
    summary = write_databases(
        anon_path=ANON_DB,
        full_path=full,
        payments=all_payments,
        identities=identities,
        ckan_payload=ckan_payload,
        transparencia_sources=transparencia_sources,
    )
    print(f"[etl] wrote: {summary}")
    return 0


if __name__ == "__main__":
    no_full = os.environ.get("DATIVOS_NO_FULL_DB", "").lower() in {"1", "true", "yes"}
    raise SystemExit(run(build_full_db=not no_full))
