"""Deduplicate parsed rows and load them into SQLite."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .parse import Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS solicitacoes_mensais (
    period_start   TEXT NOT NULL,
    period_end     TEXT NOT NULL,
    mes_referencia TEXT NOT NULL,
    period_label   TEXT NOT NULL,
    n_solicitacoes INTEGER NOT NULL,
    n_analises     INTEGER NOT NULL,
    valor_bruto    REAL    NOT NULL,
    source_resource_id TEXT NOT NULL,
    source_resource_modified TEXT,
    imported_at    TEXT NOT NULL,
    PRIMARY KEY (period_start, period_end)
);

CREATE TABLE IF NOT EXISTS sources (
    resource_id    TEXT PRIMARY KEY,
    package_id     TEXT NOT NULL,
    package_title  TEXT,
    resource_name  TEXT,
    url            TEXT NOT NULL,
    last_modified  TEXT,
    created        TEXT,
    file_sha256    TEXT,
    imported_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_solic_mes ON solicitacoes_mensais(mes_referencia);
CREATE INDEX IF NOT EXISTS idx_solic_start ON solicitacoes_mensais(period_start);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dedupe(rows: Iterable[tuple[Row, str, str | None]]) -> list[tuple[Row, str, str | None]]:
    """Resolve overlapping rows from multiple source files.

    Input: iterable of (Row, source_resource_id, source_resource_modified).
    Strategy: for each (period_start, period_end) key, keep the row whose
    source file has the most recent `last_modified`. Falls back to the row
    seen latest in iteration order when timestamps tie or are missing.
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


def write_db(
    db_path: Path,
    rows: list[tuple[Row, str, str | None]],
    sources_meta: list[dict],
) -> dict:
    """Replace the contents of dativos.db with the given rows and source metadata.

    Returns a small summary dict for logging.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    now = _now()
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            """
            INSERT INTO solicitacoes_mensais
              (period_start, period_end, mes_referencia, period_label,
               n_solicitacoes, n_analises, valor_bruto,
               source_resource_id, source_resource_modified, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.period_start.isoformat(),
                    r.period_end.isoformat(),
                    r.mes_referencia,
                    r.period_label,
                    r.n_solicitacoes,
                    r.n_analises,
                    r.valor_bruto,
                    src,
                    mod,
                    now,
                )
                for (r, src, mod) in rows
            ],
        )
        conn.executemany(
            """
            INSERT INTO sources
              (resource_id, package_id, package_title, resource_name,
               url, last_modified, created, file_sha256, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    s["resource_id"],
                    s["package_id"],
                    s.get("package_title"),
                    s.get("resource_name"),
                    s["url"],
                    s.get("last_modified"),
                    s.get("created"),
                    s.get("file_sha256"),
                    now,
                )
                for s in sources_meta
            ],
        )
    return {"rows": len(rows), "sources": len(sources_meta), "db": str(db_path)}
