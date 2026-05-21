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

from .anonymize import AdvogadoIdentity, normalize_name
from .ckan.parse import Row as CkanRow
from .transparencia.parse import Payment


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS pagamentos (
    advogado_id        VARCHAR NOT NULL,
    processo           VARCHAR NOT NULL,
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
"""

ADVOGADOS_FULL = """
CREATE TABLE IF NOT EXISTS advogados (
    advogado_id        VARCHAR PRIMARY KEY,
    nome               VARCHAR NOT NULL,
    nome_normalizado   VARCHAR NOT NULL,
    cpf_mascarado      VARCHAR
);
"""

ADVOGADOS_ANON = """
CREATE TABLE IF NOT EXISTS advogados (
    advogado_id        VARCHAR PRIMARY KEY,
    cpf_mascarado      VARCHAR
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_pag_adv ON pagamentos(advogado_id);
CREATE INDEX IF NOT EXISTS idx_pag_ano ON pagamentos(ano, mes_pagamento);
CREATE INDEX IF NOT EXISTS idx_pag_comarca ON pagamentos(comarca);
"""


def _init_schema(con: duckdb.DuckDBPyConnection, kind: str) -> None:
    con.execute(SCHEMA)
    con.execute(ADVOGADOS_FULL if kind == "full" else ADVOGADOS_ANON)
    con.execute(INDEXES)


def write_databases(
    anon_path: Path,
    full_path: Path | None,
    payments: list[Payment],
    identities: dict[tuple[str, str | None], AdvogadoIdentity],
    ckan_payload: dict,
    transparencia_sources: list[dict],
) -> dict:
    """Replace both databases. `full_path=None` skips the full DB."""
    now = _now()
    summary: dict = {}

    targets = [(anon_path, "anon")]
    if full_path is not None:
        targets.append((full_path, "full"))

    for path, kind in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        con = duckdb.connect(str(path))
        try:
            _init_schema(con, kind)

            # advogados
            if kind == "full":
                con.executemany(
                    "INSERT INTO advogados (advogado_id, nome, nome_normalizado, cpf_mascarado) VALUES (?, ?, ?, ?)",
                    [
                        (i.advogado_id, i.nome, i.nome_normalizado, i.cpf_mascarado)
                        for i in identities.values()
                    ],
                )
            else:
                # anon DB: keep only id + masked CPF (which is already non-PII)
                con.executemany(
                    "INSERT INTO advogados (advogado_id, cpf_mascarado) VALUES (?, ?)",
                    [
                        (i.advogado_id, i.cpf_mascarado)
                        for i in identities.values()
                    ],
                )

            # pagamentos — same in both DBs, identified only by advogado_id
            payment_rows = []
            for p in payments:
                ident = identities[(normalize_name(p.nome), p.cpf_mascarado)]
                competencia = f"{p.ano:04d}-{p.mes_pagamento:02d}-01"
                payment_rows.append(
                    (
                        ident.advogado_id,
                        p.processo,
                        p.valor_bruto,
                        p.valor_liquido,
                        p.valor_irrf,
                        p.valor_inss,
                        p.comarca,
                        p.vara,
                        p.vara_nome,
                        p.conta_judicial,
                        p.ano,
                        p.mes_pagamento,
                        competencia,
                        p.source_download_id,
                        now,
                    )
                )
            con.executemany(
                """
                INSERT INTO pagamentos
                (advogado_id, processo, valor_bruto, valor_liquido, valor_irrf, valor_inss,
                 comarca, vara, vara_nome, conta_judicial, ano, mes_pagamento, competencia,
                 source_download_id, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payment_rows,
            )

            # CKAN aggregates
            for row_tuple in ckan_payload["rows"]:
                ckan_row, src_id, modified = row_tuple
                con.execute(
                    """
                    INSERT OR REPLACE INTO agregado_oficial
                    (period_start, period_end, mes_referencia, period_label,
                     n_solicitacoes, n_analises, valor_bruto,
                     source_resource_id, source_resource_modified, imported_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ckan_row.period_start.isoformat(),
                        ckan_row.period_end.isoformat(),
                        ckan_row.mes_referencia,
                        ckan_row.period_label,
                        ckan_row.n_solicitacoes,
                        ckan_row.n_analises,
                        ckan_row.valor_bruto,
                        src_id,
                        modified,
                        now,
                    ),
                )

            # sources lineage
            for s in ckan_payload["sources"]:
                con.execute(
                    """
                    INSERT OR REPLACE INTO sources
                    (source_kind, source_id, label, url, file_sha256, last_modified, rows_loaded, imported_at)
                    VALUES ('ckan', ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        s["resource_id"],
                        s.get("resource_name"),
                        s["url"],
                        s.get("file_sha256"),
                        s.get("last_modified"),
                        now,
                    ),
                )
            for t in transparencia_sources:
                con.execute(
                    """
                    INSERT OR REPLACE INTO sources
                    (source_kind, source_id, label, url, file_sha256, last_modified, rows_loaded, imported_at)
                    VALUES ('transparencia', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(t["download_id"]),
                        t.get("label"),
                        t.get("url"),
                        t.get("file_sha256"),
                        t.get("last_modified"),
                        t.get("rows_loaded"),
                        now,
                    ),
                )
        finally:
            con.close()
        summary[kind] = str(path)

    return summary
