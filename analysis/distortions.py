"""SQL-based distortion / concentration analytics on the pagamentos table.

Every function takes a duckdb connection plus optional global filters (ano,
advogado_id) and returns a pandas DataFrame (or scalar for gini).

The same code runs against `dativos_anon.duckdb` (advogado_id only) and
`dativos_full.duckdb` (joined with the real name on the caller side) — the
queries never touch the `advogados` table directly.
"""
from __future__ import annotations

import duckdb
import pandas as pd


def _where(ano: int | None, advogado_id: str | None, extra: str = "") -> str:
    """Build a WHERE clause from the global filters."""
    clauses = []
    if ano is not None:
        clauses.append(f"ano = {int(ano)}")
    if advogado_id:
        # quote-escape the id for safety (it's an ADV_xxxx hex string, but
        # let's be defensive)
        safe = advogado_id.replace("'", "''")
        clauses.append(f"advogado_id = '{safe}'")
    if extra:
        clauses.append(extra)
    return f"WHERE {' AND '.join(clauses)}" if clauses else ""


# ────────────────────────────────────────────────────────────────────────
# Concentração e ranking
# ────────────────────────────────────────────────────────────────────────

def top_recebedores(
    con: duckdb.DuckDBPyConnection,
    ano: int | None = None,
    advogado_id: str | None = None,
    n: int = 50,
) -> pd.DataFrame:
    """Top N advogados by total received."""
    return con.execute(f"""
        SELECT advogado_id,
               COUNT(*)                       AS n_pagamentos,
               SUM(valor_bruto)               AS total_bruto,
               AVG(valor_bruto)               AS ticket_medio,
               COUNT(DISTINCT processo)       AS n_processos,
               COUNT(DISTINCT comarca)        AS n_comarcas
        FROM pagamentos {_where(ano, advogado_id)}
        GROUP BY advogado_id
        ORDER BY total_bruto DESC
        LIMIT {n}
    """).df()


def concentracao_pareto(
    con: duckdb.DuckDBPyConnection,
    ano: int | None = None,
    advogado_id: str | None = None,
) -> pd.DataFrame:
    """% acumulado do valor total nos top 1/5/10/25/50/100/500/1000 advogados."""
    return con.execute(f"""
        WITH ranked AS (
            SELECT advogado_id,
                   SUM(valor_bruto) AS v,
                   ROW_NUMBER() OVER (ORDER BY SUM(valor_bruto) DESC) AS rk
            FROM pagamentos {_where(ano, advogado_id)}
            GROUP BY advogado_id
        ),
        total AS (SELECT SUM(v) AS gross FROM ranked)
        SELECT bucket,
               SUM(v) FILTER (WHERE rk <= bucket) AS valor,
               SUM(v) FILTER (WHERE rk <= bucket) / total.gross AS pct
        FROM (SELECT UNNEST([1, 5, 10, 25, 50, 100, 500, 1000]) AS bucket) b,
             ranked, total
        GROUP BY bucket, total.gross
        ORDER BY bucket
    """).df()


def pareto_curve(
    con: duckdb.DuckDBPyConnection,
    ano: int | None = None,
    advogado_id: str | None = None,
    max_points: int = 500,
) -> pd.DataFrame:
    """Curva de Pareto: posição (rank) vs % acumulado do total.

    Retorna DataFrame com (rank, rank_pct, total_acum, pct_acum). Sub-amostrado
    para no máximo `max_points` linhas (mantém início e cauda).
    """
    df = con.execute(f"""
        SELECT advogado_id, SUM(valor_bruto) AS v
        FROM pagamentos {_where(ano, advogado_id)}
        GROUP BY advogado_id
        ORDER BY v DESC
    """).df()
    if df.empty:
        return df
    df["rank"] = range(1, len(df) + 1)
    total = df["v"].sum()
    df["total_acum"] = df["v"].cumsum()
    df["pct_acum"] = df["total_acum"] / total
    df["rank_pct"] = df["rank"] / len(df)
    # Sub-amostragem: preserva os 100 primeiros e amostra o resto
    if len(df) > max_points:
        head = df.iloc[:100]
        tail = df.iloc[100:].iloc[::max(1, (len(df) - 100) // (max_points - 100))]
        df = pd.concat([head, tail], ignore_index=True)
    return df[["rank", "rank_pct", "v", "total_acum", "pct_acum"]]


def lorenz_curve(
    con: duckdb.DuckDBPyConnection,
    ano: int | None = None,
    advogado_id: str | None = None,
    max_points: int = 500,
) -> pd.DataFrame:
    """Curva de Lorenz: % cumulativo da população (ascendente) vs % do total.

    Diagonal (45°) = igualdade perfeita. Quanto mais a curva afunda abaixo da
    diagonal, maior a desigualdade. A área entre a diagonal e a curva é
    proporcional ao coeficiente de Gini.
    """
    df = con.execute(f"""
        SELECT SUM(valor_bruto) AS v
        FROM pagamentos {_where(ano, advogado_id)}
        GROUP BY advogado_id
        ORDER BY v ASC
    """).df()
    if df.empty:
        return df
    n = len(df)
    total = df["v"].sum()
    df["pop_pct"] = pd.Series(range(1, n + 1)) / n
    df["valor_pct"] = df["v"].cumsum() / total
    if n > max_points:
        df = df.iloc[:: max(1, n // max_points)]
    # Garantir início em (0,0)
    head = pd.DataFrame([{"v": 0, "pop_pct": 0.0, "valor_pct": 0.0}])
    return pd.concat([head, df[["v", "pop_pct", "valor_pct"]]], ignore_index=True)


def gini(
    con: duckdb.DuckDBPyConnection,
    ano: int | None = None,
    advogado_id: str | None = None,
) -> float:
    """Gini coefficient (0 = equal, 1 = total concentration)."""
    series = con.execute(f"""
        SELECT SUM(valor_bruto) AS v FROM pagamentos {_where(ano, advogado_id)}
        GROUP BY advogado_id ORDER BY v
    """).fetchall()
    values = [r[0] for r in series]
    n = len(values)
    if n == 0:
        return 0.0
    s = sum(values)
    if s == 0:
        return 0.0
    cum = 0.0
    for i, v in enumerate(values, start=1):
        cum += i * v
    return (2.0 * cum) / (n * s) - (n + 1.0) / n


# ────────────────────────────────────────────────────────────────────────
# Distorções (heurísticas)
# ────────────────────────────────────────────────────────────────────────

def dispersao_geografica(
    con: duckdb.DuckDBPyConnection,
    ano: int | None = None,
    advogado_id: str | None = None,
    min_comarcas: int = 5,
) -> pd.DataFrame:
    """Advogados atuando em N+ comarcas."""
    return con.execute(f"""
        SELECT advogado_id,
               COUNT(DISTINCT comarca) AS n_comarcas,
               COUNT(*)                AS n_pagamentos,
               SUM(valor_bruto)        AS total
        FROM pagamentos
        {_where(ano, advogado_id, "comarca IS NOT NULL")}
        GROUP BY advogado_id
        HAVING COUNT(DISTINCT comarca) >= {min_comarcas}
        ORDER BY n_comarcas DESC, total DESC
    """).df()


def picos_intra_pessoa(
    con: duckdb.DuckDBPyConnection,
    ano: int | None = None,
    advogado_id: str | None = None,
    k: float = 4.0,
    min_meses: int = 6,
) -> pd.DataFrame:
    """Mês em que advogado recebeu > k vezes a mediana mensal dele mesmo."""
    return con.execute(f"""
        WITH mensal AS (
            SELECT advogado_id, competencia, SUM(valor_bruto) AS v
            FROM pagamentos {_where(ano, advogado_id)}
            GROUP BY advogado_id, competencia
        ),
        stats AS (
            SELECT advogado_id,
                   COUNT(*)               AS meses_ativos,
                   MEDIAN(v)              AS mediana,
                   QUANTILE_CONT(v, 0.95) AS p95
            FROM mensal GROUP BY advogado_id
        )
        SELECT m.advogado_id, m.competencia, m.v AS valor_mes,
               s.mediana, m.v / NULLIF(s.mediana, 0) AS razao
        FROM mensal m JOIN stats s USING (advogado_id)
        WHERE s.meses_ativos >= {min_meses}
          AND s.mediana > 0
          AND m.v >= {k} * s.mediana
        ORDER BY razao DESC
        LIMIT 500
    """).df()


def crescimento_yoy(
    con: duckdb.DuckDBPyConnection,
    advogado_id: str | None = None,
    min_anterior: float = 5000.0,
    fator: float = 4.0,
) -> pd.DataFrame:
    """Saltos súbitos ano(N) >= fator × ano(N-1). Ignora filtro de ano (precisa de série)."""
    extra = ""
    if advogado_id:
        safe = advogado_id.replace("'", "''")
        extra = f"WHERE advogado_id = '{safe}'"
    return con.execute(f"""
        WITH anual AS (
            SELECT advogado_id, ano, SUM(valor_bruto) AS v
            FROM pagamentos {extra}
            GROUP BY advogado_id, ano
        ),
        comp AS (
            SELECT a.advogado_id, a.ano AS ano_curr, a.v AS v_curr,
                   b.v AS v_prev,
                   a.v / NULLIF(b.v, 0) AS fator
            FROM anual a JOIN anual b
              ON b.advogado_id = a.advogado_id AND b.ano = a.ano - 1
        )
        SELECT advogado_id, ano_curr, v_prev, v_curr, fator
        FROM comp
        WHERE v_prev >= {min_anterior} AND fator >= {fator}
        ORDER BY fator DESC
        LIMIT 500
    """).df()


def concentracao_por_vara(
    con: duckdb.DuckDBPyConnection,
    ano: int | None = None,
    advogado_id: str | None = None,
    min_total_vara: float = 50000.0,
) -> pd.DataFrame:
    """% dos pagamentos de cada (comarca, vara) que vai para o advogado top-1."""
    where = _where(ano, advogado_id, "comarca IS NOT NULL AND vara_nome IS NOT NULL")
    return con.execute(f"""
        WITH por_vara AS (
            SELECT comarca, vara_nome, SUM(valor_bruto) AS total_vara
            FROM pagamentos {where}
            GROUP BY comarca, vara_nome
        ),
        por_adv AS (
            SELECT comarca, vara_nome, advogado_id,
                   SUM(valor_bruto) AS total_adv,
                   ROW_NUMBER() OVER (
                       PARTITION BY comarca, vara_nome
                       ORDER BY SUM(valor_bruto) DESC
                   ) AS rk
            FROM pagamentos {where}
            GROUP BY comarca, vara_nome, advogado_id
        )
        SELECT pa.comarca, pa.vara_nome, pa.advogado_id AS top_advogado,
               pa.total_adv AS valor_top,
               pv.total_vara,
               pa.total_adv / pv.total_vara AS pct_top
        FROM por_adv pa JOIN por_vara pv USING (comarca, vara_nome)
        WHERE pa.rk = 1 AND pv.total_vara >= {min_total_vara}
        ORDER BY pct_top DESC
        LIMIT 500
    """).df()


def ticket_atipico(
    con: duckdb.DuckDBPyConnection,
    ano: int | None = None,
    advogado_id: str | None = None,
) -> pd.DataFrame:
    """Advogados cujo ticket médio supera o P95 da vara (categoria)."""
    where = _where(ano, advogado_id, "vara_nome IS NOT NULL")
    return con.execute(f"""
        WITH adv_vara AS (
            SELECT advogado_id, vara_nome,
                   COUNT(*) AS n,
                   AVG(valor_bruto) AS ticket_adv
            FROM pagamentos {where}
            GROUP BY advogado_id, vara_nome
            HAVING COUNT(*) >= 10
        ),
        thresh AS (
            SELECT vara_nome,
                   QUANTILE_CONT(ticket_adv, 0.95) AS p95_ticket
            FROM adv_vara
            GROUP BY vara_nome
            HAVING COUNT(*) >= 5
        )
        SELECT av.advogado_id, av.vara_nome, av.n,
               av.ticket_adv, t.p95_ticket,
               av.ticket_adv / t.p95_ticket AS razao_p95
        FROM adv_vara av JOIN thresh t USING (vara_nome)
        WHERE av.ticket_adv > t.p95_ticket
        ORDER BY razao_p95 DESC
        LIMIT 500
    """).df()


def repetencia_processo(
    con: duckdb.DuckDBPyConnection,
    ano: int | None = None,
    advogado_id: str | None = None,
    min_pagamentos: int = 5,
) -> pd.DataFrame:
    """Processos pagos N+ vezes ao mesmo advogado."""
    return con.execute(f"""
        SELECT advogado_id, processo,
               COUNT(*) AS n_pagamentos,
               SUM(valor_bruto) AS total,
               MIN(competencia) AS primeiro,
               MAX(competencia) AS ultimo
        FROM pagamentos {_where(ano, advogado_id)}
        GROUP BY advogado_id, processo
        HAVING COUNT(*) >= {min_pagamentos}
        ORDER BY n_pagamentos DESC, total DESC
        LIMIT 500
    """).df()


# ────────────────────────────────────────────────────────────────────────
# Tempo até pagamento (CNJ vs competência)
# ────────────────────────────────────────────────────────────────────────

def tempo_distribuicao(
    con: duckdb.DuckDBPyConnection,
    ano: int | None = None,
    advogado_id: str | None = None,
) -> pd.DataFrame:
    """Histograma do tempo (anos) entre ano do processo e ano do pagamento."""
    where = _where(ano, advogado_id, "ano_processo IS NOT NULL")
    return con.execute(f"""
        SELECT (ano - ano_processo) AS anos_ate_pagamento,
               COUNT(*) AS n_pagamentos,
               SUM(valor_bruto) AS total
        FROM pagamentos {where}
        GROUP BY (ano - ano_processo)
        ORDER BY anos_ate_pagamento
    """).df()


def tempo_por_advogado(
    con: duckdb.DuckDBPyConnection,
    ano: int | None = None,
    n: int = 50,
) -> pd.DataFrame:
    """Advogados com menor tempo médio até pagamento (sinal de prioridade)."""
    where = _where(ano, None, "ano_processo IS NOT NULL")
    return con.execute(f"""
        SELECT advogado_id,
               COUNT(*) AS n_pagamentos,
               AVG(ano - ano_processo) AS anos_medio,
               MEDIAN(ano - ano_processo) AS anos_mediana,
               SUM(valor_bruto) AS total
        FROM pagamentos {where}
        GROUP BY advogado_id
        HAVING COUNT(*) >= 20
        ORDER BY anos_medio ASC
        LIMIT {n}
    """).df()


def tempo_por_comarca(
    con: duckdb.DuckDBPyConnection,
    ano: int | None = None,
    advogado_id: str | None = None,
) -> pd.DataFrame:
    """Mediana e média de tempo até pagamento por comarca."""
    where = _where(ano, advogado_id, "ano_processo IS NOT NULL AND comarca IS NOT NULL")
    return con.execute(f"""
        SELECT comarca,
               COUNT(*) AS n_pagamentos,
               AVG(ano - ano_processo) AS anos_medio,
               MEDIAN(ano - ano_processo) AS anos_mediana,
               SUM(valor_bruto) AS total
        FROM pagamentos {where}
        GROUP BY comarca
        HAVING COUNT(*) >= 100
        ORDER BY anos_mediana DESC
    """).df()


# ────────────────────────────────────────────────────────────────────────
# Antes vs depois (corte temporal — "Lista de Dativos")
# ────────────────────────────────────────────────────────────────────────

def comparativo_periodos(
    con: duckdb.DuckDBPyConnection,
    cutoff_year: int,
    advogado_id: str | None = None,
) -> pd.DataFrame:
    """Resumo comparativo: pré-cutoff vs cutoff em diante.

    Métricas: nº pagamentos, valor total, advogados únicos, ticket médio,
    Gini estimado via expressão SQL (aproximação rápida — para o Gini exato
    use a função `gini` com filtros de ano em loop).
    """
    extra_adv = ""
    if advogado_id:
        safe = advogado_id.replace("'", "''")
        extra_adv = f" AND advogado_id = '{safe}'"
    return con.execute(f"""
        SELECT
            CASE WHEN ano < {cutoff_year} THEN 'Pré-cutoff' ELSE 'Pós-cutoff' END AS periodo,
            COUNT(*) AS n_pagamentos,
            SUM(valor_bruto) AS total,
            COUNT(DISTINCT advogado_id) AS n_advogados,
            AVG(valor_bruto) AS ticket_medio,
            MIN(ano) AS ano_inicial,
            MAX(ano) AS ano_final
        FROM pagamentos
        WHERE 1=1 {extra_adv}
        GROUP BY (ano < {cutoff_year})
        ORDER BY periodo DESC
    """).df()


def continuidade_top_recebedores(
    con: duckdb.DuckDBPyConnection,
    cutoff_year: int,
    n: int = 50,
) -> pd.DataFrame:
    """Top N advogados em cada período e quem aparece nos dois (continuidade)."""
    return con.execute(f"""
        WITH pre AS (
            SELECT advogado_id, SUM(valor_bruto) AS total_pre,
                   ROW_NUMBER() OVER (ORDER BY SUM(valor_bruto) DESC) AS rk_pre
            FROM pagamentos WHERE ano < {cutoff_year}
            GROUP BY advogado_id
        ),
        pos AS (
            SELECT advogado_id, SUM(valor_bruto) AS total_pos,
                   ROW_NUMBER() OVER (ORDER BY SUM(valor_bruto) DESC) AS rk_pos
            FROM pagamentos WHERE ano >= {cutoff_year}
            GROUP BY advogado_id
        )
        SELECT COALESCE(pre.advogado_id, pos.advogado_id) AS advogado_id,
               pre.rk_pre, pre.total_pre,
               pos.rk_pos, pos.total_pos,
               (pre.rk_pre IS NOT NULL AND pos.rk_pos IS NOT NULL) AS em_ambos
        FROM pre FULL OUTER JOIN pos USING (advogado_id)
        WHERE COALESCE(pre.rk_pre, 999999) <= {n} OR COALESCE(pos.rk_pos, 999999) <= {n}
        ORDER BY COALESCE(pos.total_pos, 0) + COALESCE(pre.total_pre, 0) DESC
    """).df()


def concentracao_por_periodo(
    con: duckdb.DuckDBPyConnection,
    cutoff_year: int,
) -> pd.DataFrame:
    """Gini + % top-10/50/100 em cada período."""
    out = []
    for label, where in [
        ("Pré-cutoff",  f"ano < {cutoff_year}"),
        ("Pós-cutoff",  f"ano >= {cutoff_year}"),
    ]:
        ranked = con.execute(f"""
            SELECT advogado_id, SUM(valor_bruto) AS v
            FROM pagamentos WHERE {where}
            GROUP BY advogado_id
            ORDER BY v
        """).fetchall()
        values = [r[1] for r in ranked]
        n = len(values)
        s = sum(values) if values else 0
        if n == 0 or s == 0:
            out.append({"periodo": label, "n_advogados": 0, "total": 0,
                        "gini": 0, "pct_top10": 0, "pct_top50": 0, "pct_top100": 0})
            continue
        cum = sum(i * v for i, v in enumerate(values, start=1))
        g = (2.0 * cum) / (n * s) - (n + 1.0) / n
        top = sorted(values, reverse=True)
        out.append({
            "periodo": label,
            "n_advogados": n,
            "total": s,
            "gini": g,
            "pct_top10": sum(top[:10]) / s,
            "pct_top50": sum(top[:50]) / s,
            "pct_top100": sum(top[:100]) / s,
        })
    return pd.DataFrame(out)
