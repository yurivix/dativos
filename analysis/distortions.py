"""SQL-based distortion / concentration analytics on the pagamentos table.

Every function takes a duckdb connection and returns a pandas DataFrame.
The same code runs against `dativos_anon.duckdb` (advogado_id only) and
`dativos_full.duckdb` (joined with the real name) — the queries don't
reference the `advogados` table directly, so they're safe in both modes.
"""
from __future__ import annotations

import duckdb
import pandas as pd


def top_recebedores(con: duckdb.DuckDBPyConnection, ano: int | None = None, n: int = 50) -> pd.DataFrame:
    """Top N advogados by total received, with payment count and avg ticket."""
    filt = f"WHERE ano = {ano}" if ano else ""
    return con.execute(f"""
        SELECT advogado_id,
               COUNT(*)                       AS n_pagamentos,
               SUM(valor_bruto)               AS total_bruto,
               AVG(valor_bruto)               AS ticket_medio,
               COUNT(DISTINCT processo)       AS n_processos,
               COUNT(DISTINCT comarca)        AS n_comarcas
        FROM pagamentos {filt}
        GROUP BY advogado_id
        ORDER BY total_bruto DESC
        LIMIT {n}
    """).df()


def concentracao_pareto(con: duckdb.DuckDBPyConnection, ano: int | None = None) -> pd.DataFrame:
    """% acumulado do valor total nos top 1/5/10/25/50/100/500/1000 advogados."""
    filt = f"WHERE ano = {ano}" if ano else ""
    return con.execute(f"""
        WITH ranked AS (
            SELECT advogado_id,
                   SUM(valor_bruto) AS v,
                   ROW_NUMBER() OVER (ORDER BY SUM(valor_bruto) DESC) AS rk
            FROM pagamentos {filt}
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


def gini(con: duckdb.DuckDBPyConnection, ano: int | None = None) -> float:
    """Gini coefficient of total received per advogado (0 = equal, 1 = total concentration)."""
    filt = f"WHERE ano = {ano}" if ano else ""
    series = con.execute(f"""
        SELECT SUM(valor_bruto) AS v FROM pagamentos {filt}
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


def dispersao_geografica(con: duckdb.DuckDBPyConnection, min_comarcas: int = 5) -> pd.DataFrame:
    """Advogados atuando em N ou mais comarcas — possível sinal de operação profissionalizada."""
    return con.execute(f"""
        SELECT advogado_id,
               COUNT(DISTINCT comarca) AS n_comarcas,
               COUNT(*)                AS n_pagamentos,
               SUM(valor_bruto)        AS total
        FROM pagamentos
        WHERE comarca IS NOT NULL
        GROUP BY advogado_id
        HAVING COUNT(DISTINCT comarca) >= {min_comarcas}
        ORDER BY n_comarcas DESC, total DESC
    """).df()


def picos_intra_pessoa(con: duckdb.DuckDBPyConnection, k: float = 4.0, min_meses: int = 6) -> pd.DataFrame:
    """Mês em que advogado recebeu > k vezes a mediana mensal dele mesmo.

    Só considera advogados com >= min_meses ativos (estatística com poucas
    observações fica ruidosa).
    """
    return con.execute(f"""
        WITH mensal AS (
            SELECT advogado_id, competencia, SUM(valor_bruto) AS v
            FROM pagamentos
            GROUP BY advogado_id, competencia
        ),
        stats AS (
            SELECT advogado_id,
                   COUNT(*)                              AS meses_ativos,
                   MEDIAN(v)                             AS mediana,
                   QUANTILE_CONT(v, 0.95)                AS p95
            FROM mensal GROUP BY advogado_id
        )
        SELECT m.advogado_id, m.competencia, m.v AS valor_mes,
               s.mediana, m.v / NULLIF(s.mediana, 0) AS razao
        FROM mensal m JOIN stats s USING (advogado_id)
        WHERE s.meses_ativos >= {min_meses}
          AND s.mediana > 0
          AND m.v >= {k} * s.mediana
        ORDER BY razao DESC
        LIMIT 200
    """).df()


def crescimento_yoy(con: duckdb.DuckDBPyConnection, min_anterior: float = 5000.0, fator: float = 4.0) -> pd.DataFrame:
    """Advogados com saltos súbitos: ano(N) >= fator × ano(N-1), com baseline mínimo."""
    return con.execute(f"""
        WITH anual AS (
            SELECT advogado_id, ano, SUM(valor_bruto) AS v
            FROM pagamentos GROUP BY advogado_id, ano
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
        LIMIT 200
    """).df()


def concentracao_por_vara(con: duckdb.DuckDBPyConnection, min_total_vara: float = 50000.0) -> pd.DataFrame:
    """% dos pagamentos de cada (comarca, vara) que vai para o advogado top-1."""
    return con.execute(f"""
        WITH por_vara AS (
            SELECT comarca, vara_nome, SUM(valor_bruto) AS total_vara
            FROM pagamentos
            WHERE comarca IS NOT NULL AND vara_nome IS NOT NULL
            GROUP BY comarca, vara_nome
        ),
        por_adv AS (
            SELECT comarca, vara_nome, advogado_id,
                   SUM(valor_bruto) AS total_adv,
                   ROW_NUMBER() OVER (
                       PARTITION BY comarca, vara_nome
                       ORDER BY SUM(valor_bruto) DESC
                   ) AS rk
            FROM pagamentos
            WHERE comarca IS NOT NULL AND vara_nome IS NOT NULL
            GROUP BY comarca, vara_nome, advogado_id
        )
        SELECT pa.comarca, pa.vara_nome, pa.advogado_id AS top_advogado,
               pa.total_adv AS valor_top,
               pv.total_vara,
               pa.total_adv / pv.total_vara AS pct_top
        FROM por_adv pa JOIN por_vara pv USING (comarca, vara_nome)
        WHERE pa.rk = 1 AND pv.total_vara >= {min_total_vara}
        ORDER BY pct_top DESC
        LIMIT 200
    """).df()


def ticket_atipico(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Advogados cujo ticket médio supera o P95 da categoria da vara (nome).

    Categoria = vara_nome (Vara Criminal, Vara de Família, etc.). Útil pra
    flag ticket médio muito acima do par.
    """
    return con.execute("""
        WITH adv_vara AS (
            SELECT advogado_id, vara_nome,
                   COUNT(*) AS n,
                   AVG(valor_bruto) AS ticket_adv
            FROM pagamentos
            WHERE vara_nome IS NOT NULL
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
        LIMIT 200
    """).df()


def repetencia_processo(con: duckdb.DuckDBPyConnection, min_pagamentos: int = 5) -> pd.DataFrame:
    """Processos pagos N+ vezes ao mesmo advogado — parcelas legítimas viram flag se acima do esperado."""
    return con.execute(f"""
        SELECT advogado_id, processo,
               COUNT(*) AS n_pagamentos,
               SUM(valor_bruto) AS total,
               MIN(competencia) AS primeiro,
               MAX(competencia) AS ultimo
        FROM pagamentos
        GROUP BY advogado_id, processo
        HAVING COUNT(*) >= {min_pagamentos}
        ORDER BY n_pagamentos DESC, total DESC
        LIMIT 200
    """).df()
