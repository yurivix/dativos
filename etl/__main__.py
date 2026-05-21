"""End-to-end ETL orchestrator: transparencia + CKAN → DuckDB (anon + full)."""
from __future__ import annotations

import os
import sys
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


def run(build_full_db: bool = True) -> int:
    print("[etl] === transparencia.es.gov.br ===")
    files = [f for f in discover_files() if is_data_xlsx(f)]
    print(f"[etl] discovered {len(files)} data files")
    files.sort(key=lambda f: extract_year(f.label) or 0)

    all_payments: list[Payment] = []
    transparencia_sources: list[dict] = []
    for f in files:
        ano = extract_year(f.label)
        if ano is None:
            print(f"[etl] WARN: cannot infer year from {f.label!r}; skipping")
            continue
        print(f"[etl] downloading id={f.download_id} year={ano} ({f.label})")
        path = download_file(f, RAW_DIR)
        sha = file_sha256(path)
        rows = list(parse_xlsx(path, ano=ano, download_id=f.download_id))
        print(f"[etl]   parsed {len(rows):>6} payments")
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

    # Build identities map keyed by the *normalized* (nome, cpf) so that minor
    # casing/whitespace differences in the source don't fork the same person
    # into multiple ADV_ids.
    salt = anon_mod.load_or_create_salt(SALT_PATH)
    print(f"[etl] salt loaded ({'env' if os.environ.get('DATIVOS_SALT') else 'file'})")
    identities: dict[tuple[str, str | None], anon_mod.AdvogadoIdentity] = {}
    for p in all_payments:
        key = (anon_mod.normalize_name(p.nome), p.cpf_mascarado)
        if key in identities:
            continue
        adv_id = anon_mod.pseudonym(p.nome, p.cpf_mascarado, salt)
        identities[key] = anon_mod.AdvogadoIdentity(
            advogado_id=adv_id,
            nome=p.nome,
            nome_normalizado=key[0],
            cpf_mascarado=p.cpf_mascarado,
        )
    print(f"[etl] identities: {len(identities):,} unique advogados")

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
