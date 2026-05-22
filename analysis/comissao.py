"""Comparativo de membros da Comissão de Dativos vs o restante da base.

Lógica reaproveitada pela aba "🏛️ Comissão" do BIZÃO. Só faz sentido em modo
privado (PRIVATE=True), porque o matching depende dos nomes reais.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Iterable

import duckdb
import pandas as pd


# Lista padrão. O usuário pode editar via UI; isto é só o ponto de partida.
COMISSAO_DEFAULT: list[tuple[str, str]] = [
    ("Presidente",            "SOLANGE DO NASCIMENTO OLIVEIRA PRATA"),
    ("Vice-Presidente",       "CAROLINE DE SOUZA DIAS"),
    ("Secretário Geral",      "GILBERTO COSTA MOTA JÚNIOR"),
    ("Secretária Adjunta",    "PRISCILA ROSA DE ARAÚJO"),
    ("Membro", "BRUNO LUIZ LIAL FURTADO"),
    ("Membro", "ELIANA APARECIDA NASCIMENTO"),
    ("Membro", "ENEIAS DE SOUZA"),
    ("Membro", "ERCKA RENATA DE LIMA AUGUSTO"),
    ("Membro", "JOSELITA ASSIS DE LIMA"),
    ("Membro", "JOYCE CAMPANA"),
    ("Membro", "JULIANO GREGÁRIO DA ROCHA"),
    ("Membro", "KELER CIRSTINA BRAUM"),
    ("Membro", "MAGDIEL OLIVEIRA PRATES"),
    ("Membro", "MIKAELY COVRE CORREA DA SILVA"),
    ("Membro", "PABLO RAMOS LARANJA"),
    ("Membro", "RITA DE CASSIA MAGALHÃES ALMEIDA"),
    ("Membro", "TEREZINHA SANT'ANA DE CASTRO"),
]


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


@dataclass(frozen=True)
class Match:
    cargo: str
    nome_input: str
    advogado_id: str | None
    nome_matched: str | None
    method: str  # "exato" | "fuzzy" | "nao_encontrado"


def match_members(
    con: duckdb.DuckDBPyConnection,
    members: Iterable[tuple[str, str]],
    fuzzy_cutoff: float = 0.85,
) -> list[Match]:
    """Match each (cargo, nome) against the advogados table.

    Requires PRIVATE mode (advogados table has the `nome` column).
    """
    adv = con.execute(
        "SELECT advogado_id, nome, nome_normalizado FROM advogados"
    ).df()
    norm_to_row = {r.nome_normalizado: r for r in adv.itertuples(index=False)}
    all_norm = list(norm_to_row.keys())

    out: list[Match] = []
    for cargo, nome in members:
        key = _normalize(nome)
        if key in norm_to_row:
            r = norm_to_row[key]
            out.append(Match(cargo, nome, r.advogado_id, r.nome, "exato"))
            continue
        cand = get_close_matches(key, all_norm, n=1, cutoff=fuzzy_cutoff)
        if cand:
            r = norm_to_row[cand[0]]
            out.append(Match(cargo, nome, r.advogado_id, r.nome, "fuzzy"))
        else:
            out.append(Match(cargo, nome, None, None, "nao_encontrado"))
    return out


def metricas_por_membro(
    con: duckdb.DuckDBPyConnection,
    advogado_ids: list[str],
) -> pd.DataFrame:
    """Estatísticas detalhadas por membro encontrado."""
    if not advogado_ids:
        return pd.DataFrame()
    ids_sql = "(" + ",".join(f"'{aid}'" for aid in advogado_ids) + ")"
    return con.execute(f"""
        SELECT p.advogado_id, a.nome,
               COUNT(*)                    AS n_pgto,
               SUM(p.valor_bruto)          AS total,
               AVG(p.valor_bruto)          AS ticket,
               COUNT(DISTINCT p.processo)  AS n_proc,
               COUNT(DISTINCT p.comarca)   AS n_com,
               MIN(p.ano)                  AS pgto_min,
               MAX(p.ano)                  AS pgto_max,
               AVG(CASE WHEN ano_processo IS NOT NULL
                        THEN ano - ano_processo END) AS anos_medio
        FROM pagamentos p JOIN advogados a USING (advogado_id)
        WHERE p.advogado_id IN {ids_sql}
        GROUP BY p.advogado_id, a.nome
        ORDER BY total DESC NULLS LAST
    """).df()


def comparativo_medio(
    con: duckdb.DuckDBPyConnection,
    advogado_ids: list[str],
) -> pd.DataFrame:
    """Mediana e média de cada métrica para Comissão vs Demais."""
    if not advogado_ids:
        return pd.DataFrame()
    todos = con.execute("""
        SELECT advogado_id,
               COUNT(*)                    AS n_pgto,
               SUM(valor_bruto)            AS total,
               AVG(valor_bruto)            AS ticket,
               COUNT(DISTINCT processo)    AS n_proc,
               COUNT(DISTINCT comarca)     AS n_com,
               AVG(CASE WHEN ano_processo IS NOT NULL
                        THEN ano - ano_processo END) AS anos_medio
        FROM pagamentos
        GROUP BY advogado_id
    """).df()
    todos["grupo"] = todos["advogado_id"].apply(
        lambda i: "Comissão" if i in advogado_ids else "Demais"
    )

    rows = []
    for col, label in [
        ("total",      "Total recebido (R$)"),
        ("n_pgto",     "Nº de pagamentos"),
        ("ticket",     "Ticket médio (R$)"),
        ("n_proc",     "Nº de processos"),
        ("n_com",      "Nº de comarcas"),
        ("anos_medio", "Tempo médio até pgto (anos)"),
    ]:
        com = todos.loc[todos["grupo"] == "Comissão", col]
        dem = todos.loc[todos["grupo"] == "Demais",   col]
        com_med, dem_med = com.median(), dem.median()
        razao = com_med / dem_med if dem_med else float("nan")
        rows.append({
            "metrica":         label,
            "comissao_med":    com_med,
            "comissao_avg":    com.mean(),
            "demais_med":      dem_med,
            "demais_avg":      dem.mean(),
            "razao_med":       razao,
        })
    return pd.DataFrame(rows)


def ranking_membros(
    con: duckdb.DuckDBPyConnection,
    advogado_ids: list[str],
) -> pd.DataFrame:
    """Posição de cada membro no ranking geral por total recebido."""
    if not advogado_ids:
        return pd.DataFrame()
    df = con.execute("""
        SELECT advogado_id, SUM(valor_bruto) AS total
        FROM pagamentos
        GROUP BY advogado_id
        ORDER BY total DESC
    """).df()
    df["rk"] = range(1, len(df) + 1)
    df["pct_top"] = df["rk"] / len(df)
    sub = df[df["advogado_id"].isin(advogado_ids)].copy()
    names = con.execute("SELECT advogado_id, nome FROM advogados").df()
    return sub.merge(names, on="advogado_id", how="left").sort_values("rk")


def prepos_membros(
    con: duckdb.DuckDBPyConnection,
    advogado_ids: list[str],
    cutoff_year: int,
) -> pd.DataFrame:
    """Pré/Pós cutoff_year por membro + fator de crescimento."""
    if not advogado_ids:
        return pd.DataFrame()
    ids_sql = "(" + ",".join(f"'{aid}'" for aid in advogado_ids) + ")"
    df = con.execute(f"""
        SELECT p.advogado_id, a.nome,
               SUM(CASE WHEN ano <  {cutoff_year} THEN valor_bruto ELSE 0 END) AS pre,
               SUM(CASE WHEN ano >= {cutoff_year} THEN valor_bruto ELSE 0 END) AS pos,
               SUM(CASE WHEN ano <  {cutoff_year} THEN 1 ELSE 0 END) AS n_pre,
               SUM(CASE WHEN ano >= {cutoff_year} THEN 1 ELSE 0 END) AS n_pos
        FROM pagamentos p JOIN advogados a USING (advogado_id)
        WHERE p.advogado_id IN {ids_sql}
        GROUP BY p.advogado_id, a.nome
    """).df()
    df["fator"] = df.apply(
        lambda r: (r["pos"] / r["pre"]) if r["pre"] > 0 else None, axis=1
    )
    return df.sort_values(["fator", "pos"], ascending=[False, False], na_position="last")


def evolucao_temporal(
    con: duckdb.DuckDBPyConnection,
    advogado_ids: list[str],
) -> pd.DataFrame:
    """Total por ano de pagamento para cada membro (longo formato para Altair)."""
    if not advogado_ids:
        return pd.DataFrame()
    ids_sql = "(" + ",".join(f"'{aid}'" for aid in advogado_ids) + ")"
    return con.execute(f"""
        SELECT p.advogado_id, a.nome, p.ano,
               SUM(p.valor_bruto) AS total,
               COUNT(*) AS n_pgto
        FROM pagamentos p JOIN advogados a USING (advogado_id)
        WHERE p.advogado_id IN {ids_sql}
        GROUP BY p.advogado_id, a.nome, p.ano
        ORDER BY p.ano, a.nome
    """).df()
