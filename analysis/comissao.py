"""Comparativo de membros da Comissão de Dativos vs o restante da base.

Lógica reaproveitada pela aba "🏛️ Comissão" do BIZÃO. Só faz sentido em modo
privado (PRIVATE=True), porque o matching depende dos nomes reais.
"""
from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Iterable

import duckdb
import numpy as np
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


# ────────────────────────────────────────────────────────────────────────
# Análise estatística (testes + descritivas + dados pra gráficos)
# ────────────────────────────────────────────────────────────────────────

def _totals_por_advogado(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("""
        SELECT p.advogado_id, a.nome,
               SUM(p.valor_bruto) AS total,
               COUNT(*) AS n_pgto
        FROM pagamentos p JOIN advogados a USING (advogado_id)
        GROUP BY p.advogado_id, a.nome
    """).df()


def serie_por_grupo(
    con: duckdb.DuckDBPyConnection,
    advogado_ids: list[str],
) -> pd.DataFrame:
    """Dataset longo para Altair: cada linha é um advogado, com flag de grupo."""
    df = _totals_por_advogado(con)
    df["grupo"] = df["advogado_id"].apply(
        lambda i: "Comissão" if i in advogado_ids else "Demais"
    )
    df["total_log"] = np.log10(df["total"].clip(lower=1))
    return df


def estatisticas_descritivas(
    con: duckdb.DuckDBPyConnection,
    advogado_ids: list[str],
) -> pd.DataFrame:
    """Tabela com média, mediana, P25/P75/P90/P95/P99, min, max por grupo."""
    df = serie_por_grupo(con, advogado_ids)
    rows = []
    for grupo, sub in df.groupby("grupo"):
        s = sub["total"]
        rows.append({
            "grupo":  grupo,
            "n":      int(len(s)),
            "media":  s.mean(),
            "desvio": s.std(),
            "min":    s.min(),
            "p25":    s.quantile(.25),
            "p50":    s.median(),
            "p75":    s.quantile(.75),
            "p90":    s.quantile(.90),
            "p95":    s.quantile(.95),
            "p99":    s.quantile(.99),
            "max":    s.max(),
        })
    return pd.DataFrame(rows).set_index("grupo").reindex(["Comissão", "Demais"]).reset_index()


def mann_whitney_u(
    con: duckdb.DuckDBPyConnection,
    advogado_ids: list[str],
) -> dict:
    """Mann-Whitney U test (bilateral, com correção para ties).

    Retorna dict com U, Z, p (aproximação normal — ok para n grandes),
    e probabilidade de superioridade.
    """
    df = _totals_por_advogado(con)
    com = df[df["advogado_id"].isin(advogado_ids)]["total"].values
    dem = df[~df["advogado_id"].isin(advogado_ids)]["total"].values
    n1, n2 = len(com), len(dem)
    if n1 == 0 or n2 == 0:
        return {"U": 0, "Z": 0, "p": 1.0, "prob_sup": 0.5,
                "n_comissao": n1, "n_demais": n2}

    combined = np.concatenate([com, dem])
    labels = np.array([1] * n1 + [0] * n2)
    order = np.argsort(combined)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(combined) + 1)

    # Ajuste para empates: média dos ranks
    t = pd.DataFrame({"v": combined, "lab": labels, "rk": ranks})
    t["rk_adj"] = t.groupby("v")["rk"].transform("mean")

    R1 = t.loc[t["lab"] == 1, "rk_adj"].sum()
    U1 = R1 - n1 * (n1 + 1) / 2

    mean_U = n1 * n2 / 2
    std_U = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
    Z = (U1 - mean_U) / std_U if std_U > 0 else 0.0

    # p-value bilateral via aproximação normal
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(Z) / math.sqrt(2))))
    return {
        "U": float(U1),
        "Z": float(Z),
        "p": float(p),
        "prob_sup": float(U1 / (n1 * n2)) if n1 * n2 else 0.5,
        "n_comissao": n1,
        "n_demais": n2,
    }


def percentil_de_cada_membro(
    con: duckdb.DuckDBPyConnection,
    advogado_ids: list[str],
) -> pd.DataFrame:
    """Cada membro com seu percentil na distribuição dos DEMAIS (alto = melhor)."""
    df = _totals_por_advogado(con)
    dem = np.sort(df[~df["advogado_id"].isin(advogado_ids)]["total"].values)
    n_dem = len(dem)
    sub = df[df["advogado_id"].isin(advogado_ids)].copy()
    sub["percentil"] = sub["total"].apply(
        lambda v: np.searchsorted(dem, v) / n_dem if n_dem else 0.0
    )

    # Z-score robusto em log-scale
    log_dem = np.log10(dem.clip(min=1))
    med, mad = np.median(log_dem), np.median(np.abs(log_dem - np.median(log_dem)))
    sigma = 1.4826 * mad
    sub["z_log"] = sub["total"].apply(
        lambda v: (math.log10(max(v, 1)) - med) / sigma if sigma > 0 else 0.0
    )
    return sub.sort_values("percentil", ascending=False)


def histograma_buckets(
    con: duckdb.DuckDBPyConnection,
    advogado_ids: list[str],
) -> pd.DataFrame:
    """Frequência em buckets fixos de R$ para cada grupo."""
    df = _totals_por_advogado(con)
    df["grupo"] = df["advogado_id"].apply(
        lambda i: "Comissão" if i in advogado_ids else "Demais"
    )
    edges = [0, 1000, 5000, 10000, 25000, 50000, 100000, 200000, 500000, 1_000_000, float("inf")]
    labels = ["< 1k", "1-5k", "5-10k", "10-25k", "25-50k", "50-100k",
              "100-200k", "200-500k", "500k-1M", "> 1M"]
    df["bucket"] = pd.cut(df["total"], bins=edges, labels=labels, right=False)
    counts = df.groupby(["grupo", "bucket"]).size().reset_index(name="n")
    totais = df.groupby("grupo").size().to_dict()
    counts["pct"] = counts.apply(lambda r: r["n"] / totais[r["grupo"]] if totais[r["grupo"]] else 0, axis=1)
    return counts


def cdf_data(
    con: duckdb.DuckDBPyConnection,
    advogado_ids: list[str],
) -> pd.DataFrame:
    """CDF empírica para cada grupo (para plotar curva acumulada)."""
    df = serie_por_grupo(con, advogado_ids)
    parts = []
    for grupo, sub in df.groupby("grupo"):
        s = np.sort(sub["total"].values)
        cum = np.arange(1, len(s) + 1) / len(s)
        parts.append(pd.DataFrame({"grupo": grupo, "total": s, "cdf": cum}))
    return pd.concat(parts, ignore_index=True)
