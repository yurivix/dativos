"""BIZÃO público de Advogados Dativos do Espírito Santo.

Reads from `dativos_full.duckdb` if it exists locally (private mode, with names),
else from `dativos_anon.duckdb` (public mode, ADV_xxx pseudonyms only).
Override via DATIVOS_DB env var.
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
DEFAULT_CUTOFF_YEAR = 2020  # implementação da Lista de Dativos

st.set_page_config(
    page_title="Dativos ES — BIZÃO",
    page_icon="⚖️",
    layout="wide",
)


@st.cache_resource
def get_conn() -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        st.error(f"Banco `{DB_PATH.name}` não existe. Rode `python -m etl` primeiro.")
        st.stop()
    return duckdb.connect(str(DB_PATH), read_only=True)


def fmt_brl(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_int(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{int(v):,}".replace(",", ".")


def has_name_column(con: duckdb.DuckDBPyConnection) -> bool:
    cols = [r[0] for r in con.execute("DESCRIBE advogados").fetchall()]
    return "nome" in cols


con = get_conn()
PRIVATE = has_name_column(con)
APP_MODE = "🔒 Privado (com nomes)" if PRIVATE else "🌐 Público (anonimizado)"


@st.cache_data(ttl=3600)
def load_advogados_index() -> pd.DataFrame:
    """Cached advogados table (used for join + name search). Built once per session."""
    if PRIVATE:
        return get_conn().execute(
            "SELECT advogado_id, nome, nome_normalizado, cpf_mascarado FROM advogados"
        ).df()
    return get_conn().execute("SELECT advogado_id, cpf_mascarado FROM advogados").df()


def attach_name(df: pd.DataFrame, id_col: str = "advogado_id") -> pd.DataFrame:
    """Left-join the real name into a DataFrame, only if PRIVATE mode."""
    if not PRIVATE or df.empty:
        return df
    names = load_advogados_index()[["advogado_id", "nome"]]
    return df.merge(names, left_on=id_col, right_on="advogado_id", how="left", suffixes=("", "_y"))


# ───── HEADER ────────────────────────────────────────────────────────────
st.title("⚖️ Dativos ES — BIZÃO")
st.caption(
    f"Modo: **{APP_MODE}** · Honorários a Advogados Dativos do Espírito Santo · "
    "[transparencia.es.gov.br](https://transparencia.es.gov.br/Comum/AdvogadosDativos)"
)

# ───── SIDEBAR ───────────────────────────────────────────────────────────
adv_idx = load_advogados_index()

with st.sidebar:
    st.header("🎯 Filtros globais")

    # Ano filter
    anos_disp = [int(r[0]) for r in con.execute(
        "SELECT DISTINCT ano FROM pagamentos ORDER BY ano"
    ).fetchall()]
    if not anos_disp:
        st.error("Sem dados.")
        st.stop()
    ano_sel = st.selectbox(
        "Ano do pagamento",
        options=["Todos"] + anos_disp,
        index=0,
    )
    ano_filtro: int | None = None if ano_sel == "Todos" else int(ano_sel)

    st.markdown("---")
    st.subheader("🔎 Foco em advogado")

    # Search: name (private) or ADV_id (public). The selectbox is searchable
    # natively, so the user can just start typing.
    if PRIVATE:
        opcoes = ["— Todos —"] + sorted(
            adv_idx.apply(
                lambda r: f"{r['nome']}  ·  {r['advogado_id']}", axis=1
            ).tolist()
        )
        escolha = st.selectbox(
            "Selecione um advogado",
            options=opcoes,
            index=0,
            help="Digite parte do nome para filtrar a lista.",
        )
        if escolha == "— Todos —":
            adv_filtro: str | None = None
            adv_label = None
        else:
            adv_label = escolha
            adv_filtro = escolha.rsplit("·", 1)[-1].strip()
    else:
        opcoes = ["— Todos —"] + sorted(adv_idx["advogado_id"].tolist())
        escolha = st.selectbox("Selecione um ADV_id", options=opcoes, index=0)
        adv_filtro = None if escolha == "— Todos —" else escolha
        adv_label = adv_filtro

    if adv_filtro:
        st.success(f"Filtro ativo: **{adv_label}**")

    st.markdown("---")
    cutoff_year = st.number_input(
        "Marco temporal (Lista de Dativos)",
        min_value=min(anos_disp), max_value=max(anos_disp),
        value=DEFAULT_CUTOFF_YEAR, step=1,
        help="Ano de corte usado nas comparações pré × pós.",
    )

    st.markdown("---")
    last_import = con.execute("SELECT MAX(imported_at) FROM pagamentos").fetchone()[0]
    st.caption(f"Período: {min(anos_disp)}–{max(anos_disp)}\n\nÚltima carga: {last_import}")
    st.caption(f"Banco: `{DB_PATH.name}`")


# ───── ABAS ──────────────────────────────────────────────────────────────
tabs = st.tabs([
    "📊 Visão geral",
    "🏆 Ranking",
    "🔍 Distorções",
    "👤 Drill-down",
    "🗺️ Geografia",
    "⏱️ Tempo até pagamento",
    "📅 Antes × Depois Lista",
    "🔗 Reconciliação",
    "ℹ️ Sobre",
])

# helpers for SQL WHERE building inside the app
def _where_filtros(extra: str = "") -> str:
    parts = []
    if ano_filtro is not None:
        parts.append(f"ano = {ano_filtro}")
    if adv_filtro:
        parts.append(f"advogado_id = '{adv_filtro}'")
    if extra:
        parts.append(extra)
    return f"WHERE {' AND '.join(parts)}" if parts else ""


# ===== VISÃO GERAL =======================================================
with tabs[0]:
    where = _where_filtros()
    totals = con.execute(f"""
        SELECT COUNT(*)                     AS n_pag,
               SUM(valor_bruto)             AS total,
               COUNT(DISTINCT advogado_id)  AS n_adv,
               COUNT(DISTINCT processo)     AS n_proc,
               COUNT(DISTINCT comarca)      AS n_comarcas,
               AVG(valor_bruto)             AS ticket
        FROM pagamentos {where}
    """).fetchone()
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Pagamentos", fmt_int(totals[0]))
    c2.metric("Valor bruto", fmt_brl(totals[1]))
    c3.metric("Advogados", fmt_int(totals[2]))
    c4.metric("Processos", fmt_int(totals[3]))
    c5.metric("Comarcas", fmt_int(totals[4]))
    c6.metric("Ticket médio", fmt_brl(totals[5]))

    if adv_filtro:
        st.info(f"Visão filtrada para **{adv_label}**.")

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
                y=alt.Y("valor:Q", title="R$ pago"),
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


# ===== RANKING ===========================================================
with tabs[1]:
    titulo = f"Top advogados"
    if ano_filtro:
        titulo += f" em {ano_filtro}"
    if adv_filtro:
        titulo += f" (filtrado: {adv_label})"
    st.subheader(titulo)
    top = dist.top_recebedores(con, ano=ano_filtro, advogado_id=adv_filtro, n=300)
    top = attach_name(top)
    cols = ["advogado_id"] + (["nome"] if PRIVATE else []) + [
        "n_pagamentos", "total_bruto", "ticket_medio", "n_processos", "n_comarcas"
    ]
    top = top[cols]
    rename = {
        "advogado_id": "ADV", "nome": "Nome",
        "n_pagamentos": "Pagamentos", "total_bruto": "Total (R$)",
        "ticket_medio": "Ticket médio", "n_processos": "Processos",
        "n_comarcas": "Comarcas",
    }
    st.dataframe(
        top.rename(columns=rename).style.format(
            {"Total (R$)": "{:,.2f}", "Ticket médio": "{:,.2f}"}
        ),
        use_container_width=True, hide_index=True, height=500,
    )
    st.download_button(
        "⬇️ Baixar CSV",
        top.to_csv(index=False).encode("utf-8"),
        file_name=f"ranking_{ano_filtro or 'tudo'}_{adv_filtro or 'todos'}.csv",
        mime="text/csv",
    )


# ===== DISTORÇÕES ========================================================
with tabs[2]:
    if adv_filtro:
        st.info(f"Distorções filtradas para **{adv_label}**.")
    st.markdown(
        "Heurísticas para sinalizar concentração ou padrões fora do comum. "
        "**Nenhuma destas métricas, por si só, prova irregularidade** — são pontos de partida."
    )

    c1, c2 = st.columns(2)
    g = dist.gini(con, ano=ano_filtro, advogado_id=adv_filtro)
    c1.metric("Gini (concentração)", f"{g:.3f}",
              help="0 = igualitário, 1 = total concentração.")
    pareto = dist.concentracao_pareto(con, ano=ano_filtro, advogado_id=adv_filtro)
    if not pareto.empty:
        top10_pct = pareto.loc[pareto["bucket"] == 10, "pct"]
        c2.metric("Top 10 capturam",
                  f"{top10_pct.iloc[0]:.1%}" if not top10_pct.empty else "—")

    st.subheader("Pareto — concentração no topo")
    if not pareto.empty:
        st.dataframe(
            pareto.assign(pct=lambda d: (d["pct"] * 100).round(2))
            .rename(columns={"bucket": "Top N", "valor": "R$ acumulado", "pct": "% do total"})
            .style.format({"R$ acumulado": "{:,.2f}", "% do total": "{:.2f}%"}),
            use_container_width=True, hide_index=True,
        )

    with st.expander("🌍 Dispersão geográfica (advogados em N+ comarcas)"):
        min_com = st.slider("Mínimo de comarcas", 3, 30, 5, key="disp_geo")
        df = attach_name(dist.dispersao_geografica(
            con, ano=ano_filtro, advogado_id=adv_filtro, min_comarcas=min_com))
        st.dataframe(
            df.rename(columns={
                "advogado_id": "ADV", "nome": "Nome",
                "n_comarcas": "Comarcas", "n_pagamentos": "Pagamentos", "total": "Total (R$)",
            }).style.format({"Total (R$)": "{:,.2f}"}),
            use_container_width=True, hide_index=True, height=400,
        )

    with st.expander("📈 Picos atípicos (mês ≫ mediana do próprio)"):
        k = st.slider("Múltiplo da mediana (k)", 2.0, 10.0, 4.0, 0.5, key="picos_k")
        df = attach_name(dist.picos_intra_pessoa(
            con, ano=ano_filtro, advogado_id=adv_filtro, k=k))
        st.dataframe(
            df.rename(columns={
                "advogado_id": "ADV", "nome": "Nome",
                "competencia": "Mês", "valor_mes": "Valor (R$)",
                "mediana": "Mediana (R$)", "razao": "Razão",
            }).style.format({"Valor (R$)": "{:,.2f}", "Mediana (R$)": "{:,.2f}", "Razão": "{:.1f}×"}),
            use_container_width=True, hide_index=True, height=400,
        )

    with st.expander("📊 Crescimento yoy (ignora filtro de ano — precisa de série)"):
        fator = st.slider("Fator mínimo", 2.0, 20.0, 4.0, 0.5, key="yoy_k")
        df = attach_name(dist.crescimento_yoy(con, advogado_id=adv_filtro, fator=fator))
        st.dataframe(
            df.rename(columns={
                "advogado_id": "ADV", "nome": "Nome",
                "ano_curr": "Ano", "v_prev": "Anterior (R$)",
                "v_curr": "Atual (R$)", "fator": "Crescimento",
            }).style.format({"Anterior (R$)": "{:,.2f}", "Atual (R$)": "{:,.2f}", "Crescimento": "{:.1f}×"}),
            use_container_width=True, hide_index=True, height=400,
        )

    with st.expander("🏛️ Concentração por vara (% top-1)"):
        df = dist.concentracao_por_vara(con, ano=ano_filtro, advogado_id=adv_filtro)
        df = attach_name(df, id_col="top_advogado")
        st.dataframe(
            df.rename(columns={
                "comarca": "Comarca", "vara_nome": "Vara",
                "top_advogado": "ADV top", "nome": "Nome",
                "valor_top": "Valor top (R$)", "total_vara": "Total vara (R$)",
                "pct_top": "% top",
            }).style.format({
                "Valor top (R$)": "{:,.2f}", "Total vara (R$)": "{:,.2f}", "% top": "{:.1%}",
            }),
            use_container_width=True, hide_index=True, height=400,
        )

    with st.expander("💰 Ticket atípico (> P95 da vara)"):
        df = attach_name(dist.ticket_atipico(con, ano=ano_filtro, advogado_id=adv_filtro))
        st.dataframe(
            df.rename(columns={
                "advogado_id": "ADV", "nome": "Nome",
                "vara_nome": "Vara", "n": "N pgto",
                "ticket_adv": "Ticket adv (R$)", "p95_ticket": "P95 vara (R$)",
                "razao_p95": "Razão",
            }).style.format({
                "Ticket adv (R$)": "{:,.2f}", "P95 vara (R$)": "{:,.2f}", "Razão": "{:.2f}×",
            }),
            use_container_width=True, hide_index=True, height=400,
        )

    with st.expander("🔁 Repetência de mesmo processo"):
        min_n = st.slider("Mín. pagamentos no mesmo processo", 3, 20, 5, key="rep_n")
        df = attach_name(dist.repetencia_processo(
            con, ano=ano_filtro, advogado_id=adv_filtro, min_pagamentos=min_n))
        st.dataframe(
            df.rename(columns={
                "advogado_id": "ADV", "nome": "Nome",
                "processo": "Processo", "n_pagamentos": "Pagamentos",
                "total": "Total (R$)", "primeiro": "1º pgto", "ultimo": "Último",
            }).style.format({"Total (R$)": "{:,.2f}"}),
            use_container_width=True, hide_index=True, height=400,
        )


# ===== DRILL-DOWN ========================================================
with tabs[3]:
    st.subheader("Drill-down por advogado")
    if not adv_filtro:
        st.info("Selecione um advogado na sidebar para ver o detalhamento.")
    else:
        st.success(f"Dados de **{adv_label}**")
        pgs = con.execute(
            "SELECT * FROM pagamentos WHERE advogado_id = ? ORDER BY competencia, comarca",
            [adv_filtro],
        ).df()
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Pagamentos", fmt_int(len(pgs)))
        c2.metric("Total bruto", fmt_brl(pgs["valor_bruto"].sum()))
        c3.metric("Ticket médio", fmt_brl(pgs["valor_bruto"].mean()))
        c4.metric("Anos ativos", fmt_int(pgs["ano"].nunique()))
        c5.metric("Comarcas", fmt_int(pgs["comarca"].dropna().nunique()))

        st.subheader("Evolução")
        ev = pgs.groupby("competencia", as_index=False)["valor_bruto"].sum()
        ev["competencia"] = pd.to_datetime(ev["competencia"])
        st.altair_chart(
            alt.Chart(ev).mark_bar().encode(
                x="competencia:T", y="valor_bruto:Q",
                tooltip=["competencia:T", alt.Tooltip("valor_bruto:Q", format=",.2f")],
            ).properties(height=250),
            use_container_width=True,
        )

        st.subheader("Pagamentos")
        st.dataframe(
            pgs[["competencia", "processo", "ano_processo", "valor_bruto",
                 "comarca", "vara_nome", "vara"]]
            .rename(columns={
                "competencia": "Competência", "processo": "Processo",
                "ano_processo": "Ano CNJ", "valor_bruto": "Valor (R$)",
                "comarca": "Comarca", "vara_nome": "Vara", "vara": "Nº",
            }).style.format({"Valor (R$)": "{:,.2f}"}),
            use_container_width=True, hide_index=True, height=500,
        )


# ===== GEOGRAFIA =========================================================
with tabs[4]:
    st.subheader("Distribuição por comarca")
    where = _where_filtros("comarca IS NOT NULL")
    by_com = con.execute(f"""
        SELECT comarca,
               COUNT(*) AS n_pgto,
               SUM(valor_bruto) AS total,
               COUNT(DISTINCT advogado_id) AS n_adv
        FROM pagamentos {where}
        GROUP BY comarca ORDER BY total DESC
    """).df()
    if by_com.empty:
        st.warning("Nenhuma comarca para os filtros atuais.")
    else:
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


# ===== TEMPO ATÉ PAGAMENTO ==============================================
with tabs[5]:
    st.subheader("Tempo entre distribuição do processo e o pagamento")
    st.caption(
        "Diferença entre o **ano do pagamento** e o **ano do processo** "
        "(extraído do número CNJ). Granularidade: anos inteiros."
    )

    # how many payments have a CNJ-parseable year?
    cob = con.execute(f"""
        SELECT
            SUM(CASE WHEN ano_processo IS NOT NULL THEN 1 ELSE 0 END) AS com_cnj,
            COUNT(*) AS total
        FROM pagamentos {_where_filtros()}
    """).fetchone()
    if cob[1] == 0:
        st.warning("Sem dados para os filtros atuais.")
    else:
        cobertura = cob[0] / cob[1]
        st.caption(
            f"Cobertura: {cob[0]:,} de {cob[1]:,} pagamentos têm CNJ parseável "
            f"({cobertura:.1%}). Os demais usam numeração antiga não-CNJ."
        )

        hist = dist.tempo_distribuicao(con, ano=ano_filtro, advogado_id=adv_filtro)
        if not hist.empty:
            hist["anos_label"] = hist["anos_ate_pagamento"].apply(
                lambda x: f"{int(x)}+" if x >= 15 else str(int(x))
            )
            # Bucket 15+ together
            hist["bucket"] = hist["anos_ate_pagamento"].clip(upper=15)
            agg = hist.groupby("bucket", as_index=False).agg(
                n_pagamentos=("n_pagamentos", "sum"),
                total=("total", "sum"),
            )
            chart = (
                alt.Chart(agg)
                .mark_bar(opacity=0.85)
                .encode(
                    x=alt.X("bucket:O", title="Anos entre processo e pagamento"),
                    y=alt.Y("n_pagamentos:Q", title="Pagamentos"),
                    tooltip=[
                        alt.Tooltip("bucket:O", title="Anos"),
                        alt.Tooltip("n_pagamentos:Q", title="Pagamentos"),
                        alt.Tooltip("total:Q", title="Total R$", format=",.2f"),
                    ],
                )
                .properties(height=300)
            )
            st.altair_chart(chart, use_container_width=True)

            mediana = con.execute(f"""
                SELECT MEDIAN(ano - ano_processo) FROM pagamentos
                {_where_filtros('ano_processo IS NOT NULL')}
            """).fetchone()[0]
            media = con.execute(f"""
                SELECT AVG(ano - ano_processo) FROM pagamentos
                {_where_filtros('ano_processo IS NOT NULL')}
            """).fetchone()[0]
            c1, c2 = st.columns(2)
            c1.metric("Mediana", f"{int(mediana) if mediana else 0} ano(s)")
            c2.metric("Média", f"{media:.1f} anos" if media else "—")

        st.subheader("Tempo médio por comarca (mín. 100 pgto)")
        por_com = dist.tempo_por_comarca(con, ano=ano_filtro, advogado_id=adv_filtro)
        if not por_com.empty:
            st.dataframe(
                por_com.rename(columns={
                    "comarca": "Comarca", "n_pagamentos": "Pgto",
                    "anos_medio": "Média (anos)", "anos_mediana": "Mediana (anos)",
                    "total": "Total (R$)",
                }).style.format({
                    "Média (anos)": "{:.1f}", "Mediana (anos)": "{:.0f}",
                    "Total (R$)": "{:,.2f}",
                }),
                use_container_width=True, hide_index=True, height=400,
            )

        st.subheader("Advogados com pagamento mais rápido (mín. 20 pgto)")
        rapidos = attach_name(dist.tempo_por_advogado(con, ano=ano_filtro, n=50))
        if not rapidos.empty:
            st.dataframe(
                rapidos.rename(columns={
                    "advogado_id": "ADV", "nome": "Nome",
                    "n_pagamentos": "Pgto",
                    "anos_medio": "Média (anos)", "anos_mediana": "Mediana (anos)",
                    "total": "Total (R$)",
                }).style.format({
                    "Média (anos)": "{:.1f}", "Mediana (anos)": "{:.0f}",
                    "Total (R$)": "{:,.2f}",
                }),
                use_container_width=True, hide_index=True, height=400,
            )


# ===== ANTES × DEPOIS LISTA =============================================
with tabs[6]:
    st.subheader(f"Comparação antes × depois de {cutoff_year}")
    st.caption(
        f"Marco temporal: **{cutoff_year}**. Pré = pagamentos com `ano < {cutoff_year}`; "
        f"Pós = `ano >= {cutoff_year}`. Ajuste o valor na sidebar."
    )

    comp = dist.comparativo_periodos(con, cutoff_year=cutoff_year, advogado_id=adv_filtro)
    st.dataframe(
        comp.rename(columns={
            "periodo": "Período", "n_pagamentos": "Pagamentos",
            "total": "Total (R$)", "n_advogados": "Advogados",
            "ticket_medio": "Ticket médio", "ano_inicial": "Ano inicial",
            "ano_final": "Ano final",
        }).style.format({"Total (R$)": "{:,.2f}", "Ticket médio": "{:,.2f}"}),
        use_container_width=True, hide_index=True,
    )

    st.subheader("Concentração por período")
    if adv_filtro:
        st.info("A análise de concentração ignora o filtro de advogado (precisa da população inteira).")
    cp = dist.concentracao_por_periodo(con, cutoff_year=cutoff_year)
    st.dataframe(
        cp.rename(columns={
            "periodo": "Período", "n_advogados": "Advogados",
            "total": "Total (R$)", "gini": "Gini",
            "pct_top10": "% Top 10", "pct_top50": "% Top 50", "pct_top100": "% Top 100",
        }).style.format({
            "Total (R$)": "{:,.2f}", "Gini": "{:.3f}",
            "% Top 10": "{:.1%}", "% Top 50": "{:.1%}", "% Top 100": "{:.1%}",
        }),
        use_container_width=True, hide_index=True,
    )

    st.subheader(f"Top 50 advogados — quem aparece nos dois períodos")
    cont = attach_name(dist.continuidade_top_recebedores(con, cutoff_year=cutoff_year, n=50))
    cont["em_ambos"] = cont["em_ambos"].fillna(False)
    em_ambos = int(cont["em_ambos"].sum())
    st.caption(
        f"**{em_ambos}** advogados estão no top-50 dos dois períodos (continuidade). "
        f"Os outros são novos entrantes ou veteranos que sumiram."
    )
    st.dataframe(
        cont.rename(columns={
            "advogado_id": "ADV", "nome": "Nome",
            "rk_pre": "Rk pré", "total_pre": "Total pré (R$)",
            "rk_pos": "Rk pós", "total_pos": "Total pós (R$)",
            "em_ambos": "Nos dois?",
        }).style.format({
            "Total pré (R$)": "{:,.2f}", "Total pós (R$)": "{:,.2f}",
        }),
        use_container_width=True, hide_index=True, height=500,
    )


# ===== RECONCILIAÇÃO ====================================================
with tabs[7]:
    st.markdown(
        "Comparação entre a fonte detalhada (transparencia.es.gov.br) e a "
        "fonte agregada da PGE-ES (CKAN)."
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
    if ckan.empty:
        st.info("Sem dados CKAN para reconciliação.")
    else:
        merged = indiv.merge(ckan, on="ano", how="outer").fillna(0)
        merged["diff_pct"] = (
            merged["detalhado_total"] - merged["ckan_total"]
        ) / merged["detalhado_total"].replace(0, pd.NA)
        st.dataframe(
            merged.rename(columns={
                "ano": "Ano",
                "detalhado_total": "Transparência (R$)",
                "ckan_total": "CKAN/PGE (R$)",
                "diff_pct": "Δ%",
            }).style.format({
                "Transparência (R$)": "{:,.2f}", "CKAN/PGE (R$)": "{:,.2f}",
                "Δ%": "{:+.2%}",
            }),
            use_container_width=True, hide_index=True,
        )


# ===== SOBRE =============================================================
with tabs[8]:
    st.markdown(f"""
### Como este BIZÃO foi feito

**Modo**: `{APP_MODE}` · **Banco**: `{DB_PATH.name}`

**Fontes**:
1. **Primária**: [transparencia.es.gov.br/Comum/AdvogadosDativos](https://transparencia.es.gov.br/Comum/AdvogadosDativos) — 9 XLSX anuais "Acumulado" (~247 mil pagamentos individuais, 2018→2026).
2. **Secundária**: [dados.es.gov.br PGE-ES](https://dados.es.gov.br/organization/procuradoria-geral-do-estado-do-espirito-santo) — agregados mensais para reconciliação.

**Anonimização**: `ADV_xxxxxxxxxxxx` = SHA-256(nome_normalizado | CPF_mascarado | salt)[:12]. Estável entre execuções, irreversível sem o salt.

**Ano do processo**: extraído via regex `\\d{{7}}-\\d{{2}}\\.(\\d{{4}})\\.\\d\\.\\d{{2}}\\.\\d{{4}}` (formato CNJ — Resolução 65/2009). Processos com numeração antiga ficam sem `ano_processo`.

**Detecção de distorções**: heurísticas de concentração (Gini, Pareto), dispersão geográfica, picos intra-pessoa, crescimento yoy, concentração por vara, ticket atípico, repetência de processo. **Cada métrica é ponto de partida**, não prova de irregularidade.

**Marco da Lista de Dativos**: usuário configurou `{cutoff_year}` como ano de corte. Ajuste na sidebar.

**Refresh**: GitHub Actions roda o ETL diariamente; só o ano corrente é re-baixado.

**Código**: [github.com/yurivix/dativos](https://github.com/yurivix/dativos)

**Limitações**:
- CPF mascarado pela SEFAZ.
- Tempo até pagamento é granular em anos (CNJ só carrega o ano de distribuição).
- Schema XLSX mudou em 2025 (`Valor INSS` → `Conta Judicial`); o ETL unifica.
""")
