"""Load parsed payments + CKAN aggregates into two DuckDB databases.

  - data/dativos_anon.duckdb   (committed; contains only ADV_xxxx pseudonyms)
  - data/dativos_full.duckdb   (gitignored; contains real names for local use)

Both databases share the same schema except for the `advogados` table:
  full DB has columns (advogado_id, nome, nome_normalizado, cpf_mascarado)
  anon DB has columns (advogado_id, cpf_mascarado)  -- no name fields

This way the anon DB cannot leak a real name even by SQL inspection.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import duckdb
import pandas as pd

from .anonymize import AdvogadoIdentity, normalize_name
from .ckan.parse import Row as CkanRow
from .transparencia.parse import Payment


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS pagamentos (
    advogado_id        VARCHAR NOT NULL,
    processo           VARCHAR NOT NULL,
    ano_processo       INTEGER,
    valor_bruto        DOUBLE  NOT NULL,
    valor_liquido      DOUBLE,
    valor_irrf         DOUBLE,
    valor_inss         DOUBLE,
    comarca            VARCHAR,
    vara               VARCHAR,
    vara_nome          VARCHAR,
    conta_judicial     VARCHAR,
    ano                INTEGER NOT NULL,
    mes_pagamento      INTEGER NOT NULL,
    competencia        DATE    NOT NULL,
    source_download_id INTEGER NOT NULL,
    imported_at        TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS agregado_oficial (
    period_start              DATE NOT NULL,
    period_end                DATE NOT NULL,
    mes_referencia            VARCHAR NOT NULL,
    period_label              VARCHAR NOT NULL,
    n_solicitacoes            INTEGER NOT NULL,
    n_analises                INTEGER NOT NULL,
    valor_bruto               DOUBLE NOT NULL,
    source_resource_id        VARCHAR NOT NULL,
    source_resource_modified  VARCHAR,
    imported_at               TIMESTAMP NOT NULL,
    PRIMARY KEY (period_start, period_end)
);

CREATE TABLE IF NOT EXISTS sources (
    source_kind        VARCHAR NOT NULL,
    source_id          VARCHAR NOT NULL,
    label              VARCHAR,
    url                VARCHAR,
    file_sha256        VARCHAR,
    last_modified      VARCHAR,
    rows_loaded        INTEGER,
    imported_at        TIMESTAMP NOT NULL,
    PRIMARY KEY (source_kind, source_id)
);

CREATE TABLE IF NOT EXISTS comissao (
    advogado_id        VARCHAR PRIMARY KEY,  -- referência à advogados.advogado_id
    cargo              VARCHAR NOT NULL,
    ordem              INTEGER NOT NULL      -- Presidente=1, Vice=2, etc.
);
"""

ADVOGADOS_FULL = """
CREATE TABLE IF NOT EXISTS advogados (
    advogado_id        VARCHAR PRIMARY KEY,
    nome               VARCHAR NOT NULL,
    nome_normalizado   VARCHAR NOT NULL,
    cpfs_vistos        VARCHAR  -- '|'-separated list of masked CPFs
);
"""

ADVOGADOS_ANON = """
CREATE TABLE IF NOT EXISTS advogados (
    advogado_id        VARCHAR PRIMARY KEY,
    n_cpfs_vistos      INTEGER NOT NULL  -- count only, not the values
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_pag_adv ON pagamentos(advogado_id);
CREATE INDEX IF NOT EXISTS idx_pag_ano ON pagamentos(ano, mes_pagamento);
CREATE INDEX IF NOT EXISTS idx_pag_comarca ON pagamentos(comarca);
CREATE INDEX IF NOT EXISTS idx_pag_ano_processo ON pagamentos(ano_processo);
"""


def _init_schema(con: duckdb.DuckDBPyConnection, kind: str) -> None:
    con.execute(SCHEMA)
    con.execute(ADVOGADOS_FULL if kind == "full" else ADVOGADOS_ANON)
    con.execute(INDEXES)


def write_databases(
    anon_path: Path,
    full_path: Path | None,
    payments: list[Payment],
    identities: dict[str, AdvogadoIdentity],
    ckan_payload: dict,
    transparencia_sources: list[dict],
    comissao_rows: list[tuple[str, str, int]] | None = None,
) -> dict:
    """Replace both databases. `full_path=None` skips the full DB.

    `comissao_rows` é lista de (advogado_id, cargo, ordem) — gravada na
    tabela `comissao` em AMBOS os DBs (não revela nome no anon).
    """
    now = _now()
    summary: dict = {}

    targets = [(anon_path, "anon")]
    if full_path is not None:
        targets.append((full_path, "full"))

    # Materialize the bulk frames ONCE — pandas → DuckDB bulk INSERT is
    # ~100× faster than executemany with tuple lists.
    pagamentos_df = pd.DataFrame(
        [
            {
                "advogado_id": identities[normalize_name(p.nome)].advogado_id,
                "processo": p.processo,
                "ano_processo": p.ano_processo,
                "valor_bruto": p.valor_bruto,
                "valor_liquido": p.valor_liquido,
                "valor_irrf": p.valor_irrf,
                "valor_inss": p.valor_inss,
                "comarca": p.comarca,
                "vara": p.vara,
                "vara_nome": p.vara_nome,
                "conta_judicial": p.conta_judicial,
                "ano": p.ano,
                "mes_pagamento": p.mes_pagamento,
                "competencia": f"{p.ano:04d}-{p.mes_pagamento:02d}-01",
                "source_download_id": p.source_download_id,
                "imported_at": now,
            }
            for p in payments
        ]
    )
    pagamentos_df["competencia"] = pd.to_datetime(pagamentos_df["competencia"])
    pagamentos_df["imported_at"] = pd.to_datetime(pagamentos_df["imported_at"])

    advogados_full_df = pd.DataFrame(
        [
            {
                "advogado_id": i.advogado_id,
                "nome": i.nome,
                "nome_normalizado": i.nome_normalizado,
                "cpfs_vistos": "|".join(i.cpfs_vistos) if i.cpfs_vistos else None,
            }
            for i in identities.values()
        ]
    )
    advogados_anon_df = pd.DataFrame(
        [
            {"advogado_id": i.advogado_id, "n_cpfs_vistos": len(i.cpfs_vistos)}
            for i in identities.values()
        ]
    )

    agregado_rows = [
        {
            "period_start": ckan_row.period_start,
            "period_end": ckan_row.period_end,
            "mes_referencia": ckan_row.mes_referencia,
            "period_label": ckan_row.period_label,
            "n_solicitacoes": ckan_row.n_solicitacoes,
            "n_analises": ckan_row.n_analises,
            "valor_bruto": ckan_row.valor_bruto,
            "source_resource_id": src_id,
            "source_resource_modified": modified,
            "imported_at": now,
        }
        for (ckan_row, src_id, modified) in ckan_payload["rows"]
    ]
    agregado_df = pd.DataFrame(agregado_rows)
    if not agregado_df.empty:
        agregado_df["period_start"] = pd.to_datetime(agregado_df["period_start"])
        agregado_df["period_end"] = pd.to_datetime(agregado_df["period_end"])
        agregado_df["imported_at"] = pd.to_datetime(agregado_df["imported_at"])

    sources_rows = []
    for s in ckan_payload["sources"]:
        sources_rows.append(
            {
                "source_kind": "ckan",
                "source_id": s["resource_id"],
                "label": s.get("resource_name"),
                "url": s["url"],
                "file_sha256": s.get("file_sha256"),
                "last_modified": s.get("last_modified"),
                "rows_loaded": None,
                "imported_at": now,
            }
        )
    for t in transparencia_sources:
        sources_rows.append(
            {
                "source_kind": "transparencia",
                "source_id": str(t["download_id"]),
                "label": t.get("label"),
                "url": t.get("url"),
                "file_sha256": t.get("file_sha256"),
                "last_modified": t.get("last_modified"),
                "rows_loaded": t.get("rows_loaded"),
                "imported_at": now,
            }
        )
    sources_df = pd.DataFrame(sources_rows)
    if not sources_df.empty:
        sources_df["imported_at"] = pd.to_datetime(sources_df["imported_at"])

    comissao_df = pd.DataFrame(
        comissao_rows or [],
        columns=["advogado_id", "cargo", "ordem"],
    )

    for path, kind in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        con = duckdb.connect(str(path))
        try:
            _init_schema(con, kind)

            # Bulk-insert via DataFrame: DuckDB sees `df` as a virtual table
            # in the local scope and copies it in C++.
            df_adv = advogados_full_df if kind == "full" else advogados_anon_df  # noqa: F841
            con.execute("INSERT INTO advogados SELECT * FROM df_adv")

            df_pag = pagamentos_df  # noqa: F841
            con.execute("INSERT INTO pagamentos SELECT * FROM df_pag")

            if not agregado_df.empty:
                df_agg = agregado_df  # noqa: F841
                con.execute("INSERT INTO agregado_oficial SELECT * FROM df_agg")

            if not sources_df.empty:
                df_src = sources_df  # noqa: F841
                con.execute("INSERT INTO sources SELECT * FROM df_src")

            if not comissao_df.empty:
                df_com = comissao_df  # noqa: F841
                con.execute("INSERT INTO comissao SELECT * FROM df_com")
        finally:
            con.close()
        summary[kind] = str(path)

    return summary
