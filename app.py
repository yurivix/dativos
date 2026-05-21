"""BIZÃO público de Advogados Dativos do Espírito Santo (visão anonimizada).

Reads from data/dativos_anon.duckdb — committed to the repo, contains only
pseudonyms (ADV_xxxxxxxxxxxx). For the version with real names, run app_private.py
locally against data/dativos_full.duckdb (which is .gitignored).
"""
from __future__ import annotations

import os
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

from analysis import distortions as dist

ROOT = Path(__file__).resolve().parent


def _resolve_db_path() -> Path:
    """Pick which DuckDB file to read.

    Resolution order:
      1. DATIVOS_DB env var (explicit override)
      2. dativos_full.duckdb if present (i.e. running locally with names)
      3. dativos_anon.duckdb (the only one shipped in the repo / on deploy)
    """
    env = os.environ.get("DATIVOS_DB")
    if env:
        return Path(env)
    full = ROOT / "data" / "dativos_full.duckdb"
    if full.exists():
        return full
    return ROOT / "data" / "dativos_anon.duckdb"


DB_PATH = _resolve_db_path()

st.set_page_config(
    page_title="Dativos ES — BIZÃO",
    page_icon="⚖️",
    layout="wide",
)


@st.cache_resource
def get_conn() -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        st.error(
            f"Banco `{DB_PATH.name}` não existe. Rode `python -m etl` primeiro."
        )
        st.stop()
    return duckdb.connect(str(DB_PATH), read_only=True)


@st.cache_data(ttl=3600)
def fetch(_con_id: str, sql: str, params: tuple = ()) -> pd.DataFrame:
    """Cache by SQL text. _con_id avoids hashing the connection."""
    return get_conn().execute(sql, params).df()


def fmt_brl(v: float | None) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_int(v) -> str:
    return f"{int(v):,}".replace(",", ".")


def has_name_column(con: duckdb.DuckDBPyConnection) -> bool:
    """True if the advogados table has a `nome` column (i.e. private mode)."""
    cols = [r[0] for r in con.execute("DESCRIBE advogados").fetchall()]
    return "nome" in cols


con = get_conn()
PRIVATE = has_name_column(con)
APP_MODE = "Privado (nomes reais)" if PRIVATE else "Público (anonimizado)"

st.title("⚖️ Dativos ES — BIZÃO")
st.caption(
    f"Modo: **{APP_MODE}** · "
    "Honorários de Advogados Dativos pagos pelo Estado do Espírito Santo · "
    "Fonte: [transparencia.es.gov.br](https://transparencia.es.gov.br/Comum/AdvogadosDativos)"
)

# ───── Sidebar: filtros globais ──────────────────────────────────────────
with st.sidebar:
    st.header("Filtros globais")
    anos_disp = [int(r[0]) for r in con.execute(
        "SELECT DISTINCT ano FROM pagamentos ORDER BY ano"
    ).fetchall()]
    if not anos_disp:
        st.error("Nenhum dado no banco.")
        st.stop()
    ano_sel = st.selectbox(
        "Ano (para abas que filtram)",
        options=["Todos"] + anos_disp,
        index=0,
    )
    ano_filtro = None if ano_sel == "Todos" else int(ano_sel)
    st.caption(
        f"Dados de {min(anos_disp)} a {max(anos_disp)}. "
        f"Última atualização local: {con.execute('SELECT MAX(imported_at) FROM pagamentos').fetchone()[0]}"
    )


# ───── Abas ──────────────────────────────────────────────────────────────
tab_visao, tab_ranking, tab_dist, tab_drill, tab_geo, tab_recon, tab_sobre = st.tabs([
    "📊 Visão geral",
    "🏆 Ranking",
    "🔍 Distorções",
    "👤 Drill-down",
    "🗺️ Geografia",
    "🔗 Reconciliação",
    "ℹ️ Sobre",
])

# ───── Visão geral ───────────────────────────────────────────────────────
with tab_visao:
    where = f"WHERE ano = {ano_filtro}" if ano_filtro else ""
    totals = con.execute(f"""
        SELECT COUNT(*)                     AS n_pag,
               SUM(valor_bruto)             AS total,
               COUNT(DISTINCT advogado_id)  AS n_adv,
               COUNT(DISTINCT processo)     AS n_proc,
               COUNT(DISTINCT comarca)      AS n_comarcas
        FROM pagamentos {where}
    """).fetchone()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Pagamentos", fmt_int(totals[0]))
    c2.metric("Valor bruto", fmt_brl(totals[1]))
    c3.metric("Advogados", fmt_int(totals[2]))
    c4.metric("Processos", fmt_int(totals[3]))
    c5.metric("Comarcas", fmt_int(totals[4]))

    st.subheader("Evolução mensal")
    mensal = con.execute(f"""
        SELECT competencia, SUM(valor_bruto) AS valor, COUNT(*) AS pagamentos
        FROM pagamentos {where}
        GROUP BY competencia ORDER BY competencia
    """).df()
    if not mensal.empty:
        mensal["competencia"] = pd.to_datetime(mensal["competencia"])
        chart = (
            alt.Chart(mensal)
            .mark_bar(opacity=0.85)
            .encode(
                x=alt.X("competencia:T", title="Competência"),
                y=alt.Y("valor:Q", title="R$ pago no mês"),
                tooltip=[
                    alt.Tooltip("competencia:T", title="Mês"),
                    alt.Tooltip("valor:Q", title="R$", format=",.2f"),
                    alt.Tooltip("pagamentos:Q", title="Pagamentos"),
                ],
            )
            .properties(height=300)
        )
        st.altair_chart(chart, use_container_width=True)

    st.subheader("Heatmap: pagamentos por ano × mês")
    heat = con.execute(f"""
        SELECT ano, mes_pagamento, SUM(valor_bruto) AS valor
        FROM pagamentos {where}
        GROUP BY ano, mes_pagamento
    """).df()
    if not heat.empty:
        chart_h = (
            alt.Chart(heat)
            .mark_rect()
            .encode(
                x=alt.X("mes_pagamento:O", title="Mês"),
                y=alt.Y("ano:O", title="Ano", sort="descending"),
                color=alt.Color("valor:Q", scale=alt.Scale(scheme="greens"), title="R$"),
                tooltip=["ano:O", "mes_pagamento:O", alt.Tooltip("valor:Q", format=",.2f")],
            )
            .properties(height=280)
        )
        st.altair_chart(chart_h, use_container_width=True)

# ───── Ranking ───────────────────────────────────────────────────────────
with tab_ranking:
    st.subheader(f"Top advogados {f'em {ano_filtro}' if ano_filtro else '(período completo)'}")
    top = dist.top_recebedores(con, ano=ano_filtro, n=200)
    if PRIVATE:
        names = con.execute("SELECT advogado_id, nome FROM advogados").df()
        top = top.merge(names, on="advogado_id", how="left")
        col_order = ["advogado_id", "nome", "n_pagamentos", "total_bruto", "ticket_medio", "n_processos", "n_comarcas"]
    else:
        col_order = ["advogado_id", "n_pagamentos", "total_bruto", "ticket_medio", "n_processos", "n_comarcas"]
    top = top[col_order]
    rename = {
        "advogado_id": "ADV",
        "nome": "Nome",
        "n_pagamentos": "Pagamentos",
        "total_bruto": "Total (R$)",
        "ticket_medio": "Ticket médio",
        "n_processos": "Processos",
        "n_comarcas": "Comarcas",
    }
    st.dataframe(
        top.rename(columns=rename).style.format(
            {"Total (R$)": "{:,.2f}", "Ticket médio": "{:,.2f}"}
        ),
        use_container_width=True,
        hide_index=True,
        height=500,
    )
    st.download_button(
        "⬇️ Baixar CSV",
        top.to_csv(index=False).encode("utf-8"),
        file_name=f"ranking_{ano_filtro or 'tudo'}.csv",
        mime="text/csv",
    )

# ───── Distorções ────────────────────────────────────────────────────────
with tab_dist:
    st.markdown(
        "Heurísticas para sinalizar concentração ou padrões fora do comum. "
        "**Nenhuma destas métricas, por si só, prova irregularidade** — são pontos de partida para investigação."
    )

    c1, c2 = st.columns(2)
    with c1:
        gini_geral = dist.gini(con, ano=ano_filtro)
        st.metric(
            "Gini (concentração)",
            f"{gini_geral:.3f}",
            help="0 = perfeitamente igualitário; 1 = 1 pessoa fica com tudo",
        )
    with c2:
        pareto = dist.concentracao_pareto(con, ano=ano_filtro)
        top10 = pareto.loc[pareto["bucket"] == 10, "pct"].iloc[0] if not pareto.empty else 0
        st.metric("Top 10 capturam", f"{top10:.1%}", help="% do valor bruto total")

    st.subheader("Pareto: concentração no topo")
    st.dataframe(
        pareto.assign(pct=lambda d: (d["pct"] * 100).round(2))
        .rename(columns={"bucket": "Top N", "valor": "R$ acumulado", "pct": "% do total"})
        .style.format({"R$ acumulado": "{:,.2f}", "% do total": "{:.2f}%"}),
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("🌍 Dispersão geográfica (advogados em N+ comarcas)", expanded=False):
        min_com = st.slider("Mínimo de comarcas distintas", 3, 30, 5, key="disp_geo")
        df = dist.dispersao_geografica(con, min_comarcas=min_com)
        if PRIVATE:
            names = con.execute("SELECT advogado_id, nome FROM advogados").df()
            df = df.merge(names, on="advogado_id", how="left")
        st.dataframe(
            df.rename(columns={
                "advogado_id": "ADV",
                "nome": "Nome",
                "n_comarcas": "Comarcas",
                "n_pagamentos": "Pagamentos",
                "total": "Total (R$)",
            }).style.format({"Total (R$)": "{:,.2f}"}),
            use_container_width=True,
            hide_index=True,
            height=400,
        )

    with st.expander("📈 Picos atípicos por advogado (mês ≫ mediana do próprio)", expanded=False):
        k = st.slider("Múltiplo da mediana (k)", 2.0, 10.0, 4.0, 0.5, key="picos_k")
        df = dist.picos_intra_pessoa(con, k=k)
        if PRIVATE and not df.empty:
            names = con.execute("SELECT advogado_id, nome FROM advogados").df()
            df = df.merge(names, on="advogado_id", how="left")
        st.dataframe(
            df.rename(columns={
                "advogado_id": "ADV", "nome": "Nome",
                "competencia": "Mês", "valor_mes": "Valor (R$)",
                "mediana": "Mediana (R$)", "razao": "Razão",
            }).style.format({"Valor (R$)": "{:,.2f}", "Mediana (R$)": "{:,.2f}", "Razão": "{:.1f}×"}),
            use_container_width=True, hide_index=True, height=400,
        )

    with st.expander("📊 Crescimento ano a ano (yoy)", expanded=False):
        fator = st.slider("Fator de crescimento mínimo", 2.0, 20.0, 4.0, 0.5, key="yoy_k")
        df = dist.crescimento_yoy(con, fator=fator)
        if PRIVATE and not df.empty:
            names = con.execute("SELECT advogado_id, nome FROM advogados").df()
            df = df.merge(names, on="advogado_id", how="left")
        st.dataframe(
            df.rename(columns={
                "advogado_id": "ADV", "nome": "Nome",
                "ano_curr": "Ano", "v_prev": "Anterior (R$)",
                "v_curr": "Atual (R$)", "fator": "Crescimento",
            }).style.format({"Anterior (R$)": "{:,.2f}", "Atual (R$)": "{:,.2f}", "Crescimento": "{:.1f}×"}),
            use_container_width=True, hide_index=True, height=400,
        )

    with st.expander("🏛️ Concentração por vara (% top-1)", expanded=False):
        df = dist.concentracao_por_vara(con)
        if PRIVATE and not df.empty:
            names = con.execute("SELECT advogado_id, nome FROM advogados").df()
            df = df.merge(names, left_on="top_advogado", right_on="advogado_id", how="left")
        st.dataframe(
            df.rename(columns={
                "comarca": "Comarca", "vara_nome": "Vara",
                "top_advogado": "ADV top", "nome": "Nome",
                "valor_top": "Valor top (R$)", "total_vara": "Total vara (R$)",
                "pct_top": "% top",
            }).style.format({
                "Valor top (R$)": "{:,.2f}", "Total vara (R$)": "{:,.2f}",
                "% top": "{:.1%}",
            }),
            use_container_width=True, hide_index=True, height=400,
        )

    with st.expander("💰 Ticket médio atípico (> P95 da vara)", expanded=False):
        df = dist.ticket_atipico(con)
        if PRIVATE and not df.empty:
            names = con.execute("SELECT advogado_id, nome FROM advogados").df()
            df = df.merge(names, on="advogado_id", how="left")
        st.dataframe(
            df.rename(columns={
                "advogado_id": "ADV", "nome": "Nome",
                "vara_nome": "Vara", "n": "N pagamentos",
                "ticket_adv": "Ticket adv (R$)", "p95_ticket": "P95 da vara (R$)",
                "razao_p95": "Razão",
            }).style.format({
                "Ticket adv (R$)": "{:,.2f}", "P95 da vara (R$)": "{:,.2f}",
                "Razão": "{:.2f}×",
            }),
            use_container_width=True, hide_index=True, height=400,
        )

    with st.expander("🔁 Repetência de mesmo processo", expanded=False):
        min_n = st.slider("Mín. pagamentos no mesmo processo", 3, 20, 5, key="rep_n")
        df = dist.repetencia_processo(con, min_pagamentos=min_n)
        if PRIVATE and not df.empty:
            names = con.execute("SELECT advogado_id, nome FROM advogados").df()
            df = df.merge(names, on="advogado_id", how="left")
        st.dataframe(
            df.rename(columns={
                "advogado_id": "ADV", "nome": "Nome",
                "processo": "Processo", "n_pagamentos": "Pagamentos",
                "total": "Total (R$)", "primeiro": "1º pgto", "ultimo": "Último",
            }).style.format({"Total (R$)": "{:,.2f}"}),
            use_container_width=True, hide_index=True, height=400,
        )

# ───── Drill-down ────────────────────────────────────────────────────────
with tab_drill:
    st.subheader("Drill-down por advogado")
    todos = con.execute("""
        SELECT advogado_id, SUM(valor_bruto) AS total, COUNT(*) AS n
        FROM pagamentos GROUP BY advogado_id
        ORDER BY total DESC
    """).df()
    if PRIVATE:
        names = con.execute("SELECT advogado_id, nome FROM advogados").df()
        todos = todos.merge(names, on="advogado_id", how="left")
        opcoes = todos.apply(
            lambda r: f"{r['advogado_id']}  ·  {r['nome']}  ·  {fmt_brl(r['total'])}",
            axis=1,
        ).tolist()
    else:
        opcoes = todos.apply(
            lambda r: f"{r['advogado_id']}  ·  {fmt_brl(r['total'])}  ·  {r['n']} pgto",
            axis=1,
        ).tolist()
    escolha = st.selectbox("Selecione um advogado (ordenados por total)", opcoes)
    if escolha:
        adv_id = escolha.split(" ", 1)[0]
        pgs = con.execute(
            "SELECT * FROM pagamentos WHERE advogado_id = ? ORDER BY competencia, comarca",
            [adv_id],
        ).df()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Pagamentos", fmt_int(len(pgs)))
        c2.metric("Total bruto", fmt_brl(pgs["valor_bruto"].sum()))
        c3.metric("Ticket médio", fmt_brl(pgs["valor_bruto"].mean()))
        c4.metric("Anos ativos", f"{pgs['ano'].nunique()}")
        st.dataframe(
            pgs[["competencia", "processo", "valor_bruto", "comarca", "vara_nome", "vara"]]
            .rename(columns={
                "competencia": "Competência", "processo": "Processo",
                "valor_bruto": "Valor (R$)", "comarca": "Comarca",
                "vara_nome": "Vara", "vara": "Nº",
            }).style.format({"Valor (R$)": "{:,.2f}"}),
            use_container_width=True, hide_index=True, height=500,
        )

# ───── Geografia ─────────────────────────────────────────────────────────
with tab_geo:
    st.subheader("Distribuição por comarca")
    by_com = con.execute(f"""
        SELECT comarca,
               COUNT(*) AS n_pgto,
               SUM(valor_bruto) AS total,
               COUNT(DISTINCT advogado_id) AS n_adv
        FROM pagamentos
        WHERE comarca IS NOT NULL {('AND ano = ' + str(ano_filtro)) if ano_filtro else ''}
        GROUP BY comarca ORDER BY total DESC
    """).df()
    chart = (
        alt.Chart(by_com.head(30))
        .mark_bar()
        .encode(
            x=alt.X("total:Q", title="R$ total"),
            y=alt.Y("comarca:N", sort="-x", title=""),
            tooltip=["comarca", "n_pgto", alt.Tooltip("total:Q", format=",.2f"), "n_adv"],
        )
        .properties(height=600)
    )
    st.altair_chart(chart, use_container_width=True)
    st.dataframe(
        by_com.rename(columns={
            "comarca": "Comarca", "n_pgto": "Pagamentos",
            "total": "Total (R$)", "n_adv": "Advogados",
        }).style.format({"Total (R$)": "{:,.2f}"}),
        use_container_width=True, hide_index=True, height=400,
    )

# ───── Reconciliação ─────────────────────────────────────────────────────
with tab_recon:
    st.markdown(
        "Comparação entre a fonte detalhada (transparencia.es.gov.br) e a fonte "
        "agregada da PGE-ES (CKAN). Pequenas diferenças são esperadas — as fontes "
        "usam janelas de competência diferentes."
    )
    indiv = con.execute("""
        SELECT ano, SUM(valor_bruto) AS detalhado_total
        FROM pagamentos GROUP BY ano ORDER BY ano
    """).df()
    ckan = con.execute("""
        SELECT EXTRACT(year FROM period_start) AS ano,
               SUM(valor_bruto) AS ckan_total
        FROM agregado_oficial
        GROUP BY EXTRACT(year FROM period_start)
        ORDER BY ano
    """).df()
    merged = indiv.merge(ckan, on="ano", how="outer").fillna(0)
    merged["diff_pct"] = (merged["detalhado_total"] - merged["ckan_total"]) / merged["detalhado_total"]
    st.dataframe(
        merged.rename(columns={
            "ano": "Ano",
            "detalhado_total": "Transparência (R$)",
            "ckan_total": "CKAN/PGE (R$)",
            "diff_pct": "Δ%",
        }).style.format({
            "Transparência (R$)": "{:,.2f}", "CKAN/PGE (R$)": "{:,.2f}", "Δ%": "{:+.2%}",
        }),
        use_container_width=True, hide_index=True,
    )

# ───── Sobre ─────────────────────────────────────────────────────────────
with tab_sobre:
    st.markdown(
        f"""
### Como este BIZÃO foi feito

**Modo atual**: `{APP_MODE}`

**Fontes**:
1. **Primária**: [transparencia.es.gov.br/Comum/AdvogadosDativos](https://transparencia.es.gov.br/Comum/AdvogadosDativos) — 9 arquivos XLSX anuais "Acumulado" com dados por pagamento (nome do beneficiário, CPF mascarado, processo, valor, comarca, vara, mês). ~247 mil linhas.
2. **Secundária** (reconciliação): [dados.es.gov.br](https://dados.es.gov.br/organization/procuradoria-geral-do-estado-do-espirito-santo) — agregados mensais publicados pela PGE-ES via CKAN.

**Anonimização**: o ID `ADV_xxxxxxxxxxxx` é um hash SHA-256 de (nome normalizado + CPF mascarado + salt secreto), truncado a 12 caracteres hexadecimais. É **estável** entre execuções, então análises por advogado continuam fazendo sentido, mas **não é reversível** sem o salt. O CPF já é mascarado pela própria SEFAZ (`***116817**`).

**Detecção de distorções**: 7 heurísticas baseadas em concentração (Gini, Pareto, dispersão geográfica, picos intra-pessoa, crescimento yoy, concentração por vara, ticket médio atípico, repetência de processos). Cada uma é um *ponto de partida* para investigação, não prova de irregularidade.

**Atualização**: GitHub Actions roda o ETL semanalmente. O arquivo do ano corrente é sempre re-baixado; anos passados só re-baixam se o SHA-256 mudou.

**Código**: [github.com/yurivix/dativos](https://github.com/yurivix/dativos)

**Limitações conhecidas**:
- CPF vem mascarado da fonte (não é decisão nossa).
- Mês do pagamento é o mês de processamento financeiro, não o do fato gerador.
- Schema dos XLSX mudou entre 2024 e 2025 (Valor INSS → Conta Judicial); o ETL unifica.
"""
    )
