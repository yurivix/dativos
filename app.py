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
import numpy as np
import pandas as pd
import streamlit as st

from analysis import comissao as com_mod, distortions as dist

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
            "SELECT advogado_id, nome, nome_normalizado, cpfs_vistos FROM advogados"
        ).df()
    return get_conn().execute("SELECT advogado_id, n_cpfs_vistos FROM advogados").df()


@st.cache_data(ttl=3600)
def load_comissao_index() -> pd.DataFrame:
    """ADV_ids da comissão (com cargo). Existe em ambos os DBs."""
    try:
        return get_conn().execute(
            "SELECT advogado_id, cargo, ordem FROM comissao ORDER BY ordem"
        ).df()
    except Exception:
        return pd.DataFrame(columns=["advogado_id", "cargo", "ordem"])


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
    "📈 Comparar 5",
    "🗺️ Geografia",
    "⏱️ Tempo até pagamento",
    "📅 Antes × Depois Lista",
    "🏛️ Comissão",
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

    # Comissão: usado para flag + filtro
    comissao_idx = load_comissao_index()
    cargo_by_id = dict(zip(comissao_idx["advogado_id"], comissao_idx["cargo"]))

    so_comissao = st.checkbox(
        f"Mostrar APENAS membros da comissão ({len(cargo_by_id)})",
        value=False,
        key="rank_so_com",
    )

    top = dist.top_recebedores(con, ano=ano_filtro, advogado_id=adv_filtro, n=500)
    top = attach_name(top)
    top["cargo"] = top["advogado_id"].map(cargo_by_id).fillna("")

    if so_comissao:
        top = top[top["cargo"] != ""]

    cols = ["cargo", "advogado_id"] + (["nome"] if PRIVATE else []) + [
        "n_pagamentos", "total_bruto", "ticket_medio", "n_processos", "n_comarcas"
    ]
    top = top[cols]
    rename = {
        "cargo": "🏛️ Comissão",
        "advogado_id": "ADV", "nome": "Nome",
        "n_pagamentos": "Pagamentos", "total_bruto": "Total (R$)",
        "ticket_medio": "Ticket médio", "n_processos": "Processos",
        "n_comarcas": "Comarcas",
    }

    def _highlight_comissao(row):
        if row["🏛️ Comissão"]:
            return ["background-color: #fff3cd; font-weight: 600"] * len(row)
        return [""] * len(row)

    styled = (top.rename(columns=rename)
                 .style
                 .format({"Total (R$)": "{:,.2f}", "Ticket médio": "{:,.2f}"})
                 .apply(_highlight_comissao, axis=1))
    st.dataframe(styled, use_container_width=True, hide_index=True, height=500)
    st.caption(
        f"🟨 Linhas destacadas = membros da Comissão de Dativos "
        f"({len(cargo_by_id)} ADV_ids cadastrados)."
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


# ===== COMPARAR 5 ========================================================
with tabs[4]:
    st.subheader("Comparativo lado a lado (até 5 advogados)")
    st.caption(
        "Selecione até 5 advogados na caixa abaixo. Os gráficos mostram a "
        "evolução do total pago por **ano do pagamento** e por **ano do processo (CNJ)**. "
        "Os filtros globais da sidebar **não** se aplicam aqui — esta aba sempre "
        "olha a série histórica completa de cada advogado selecionado."
    )

    if PRIVATE:
        # label = "Nome · ADV_xxxxxxxxxxxx" (search-friendly)
        opcoes_comp = sorted(
            adv_idx.apply(
                lambda r: f"{r['nome']}  ·  {r['advogado_id']}", axis=1
            ).tolist()
        )
    else:
        opcoes_comp = sorted(adv_idx["advogado_id"].tolist())

    selected_labels = st.multiselect(
        "Selecione até 5 advogados (digite parte do nome para filtrar)",
        options=opcoes_comp,
        max_selections=5,
        key="comparar_5_select",
    )

    if not selected_labels:
        st.info("Selecione 1 a 5 advogados para ver a comparação.")
    else:
        selected_ids = [
            (lbl.rsplit("·", 1)[-1].strip() if "·" in lbl else lbl)
            for lbl in selected_labels
        ]
        placeholders = ",".join(f"'{aid}'" for aid in selected_ids)

        # Pagamentos agregados por ano DE PAGAMENTO
        df_pag = con.execute(f"""
            SELECT advogado_id, ano,
                   SUM(valor_bruto) AS total,
                   COUNT(*) AS n
            FROM pagamentos
            WHERE advogado_id IN ({placeholders})
            GROUP BY advogado_id, ano
            ORDER BY advogado_id, ano
        """).df()

        # Pagamentos agregados por ano DO PROCESSO (CNJ)
        df_proc = con.execute(f"""
            SELECT advogado_id, ano_processo AS ano,
                   SUM(valor_bruto) AS total,
                   COUNT(*) AS n
            FROM pagamentos
            WHERE advogado_id IN ({placeholders}) AND ano_processo IS NOT NULL
            GROUP BY advogado_id, ano_processo
            ORDER BY advogado_id, ano_processo
        """).df()

        # Build a friendly label per advogado (Nome (ADV_xxxxxxx) or just ADV_)
        if PRIVATE:
            names_lookup = adv_idx.set_index("advogado_id")["nome"].to_dict()
            label_of = {
                aid: f"{names_lookup.get(aid, '?')}  ({aid[:11]}…)"
                for aid in selected_ids
            }
        else:
            label_of = {aid: aid for aid in selected_ids}
        df_pag["advogado"] = df_pag["advogado_id"].map(label_of)
        df_proc["advogado"] = df_proc["advogado_id"].map(label_of)

        # Side-by-side metric strip
        cols = st.columns(len(selected_ids))
        for col, aid in zip(cols, selected_ids):
            sub = df_pag[df_pag["advogado_id"] == aid]
            tot = sub["total"].sum() if not sub.empty else 0.0
            n = int(sub["n"].sum()) if not sub.empty else 0
            label = label_of[aid]
            col.metric(
                label[:30] + ("…" if len(label) > 30 else ""),
                fmt_brl(tot),
                f"{fmt_int(n)} pgto",
            )

        st.markdown("### 💰 Evolução por **ano de pagamento**")
        if df_pag.empty:
            st.info("Sem pagamentos para os advogados selecionados.")
        else:
            chart_pag = (
                alt.Chart(df_pag)
                .mark_line(point=alt.OverlayMarkDef(size=80), strokeWidth=2.5)
                .encode(
                    x=alt.X("ano:O", title="Ano do pagamento"),
                    y=alt.Y("total:Q", title="R$ total no ano"),
                    color=alt.Color("advogado:N", title="Advogado",
                                    legend=alt.Legend(orient="bottom", columns=2)),
                    tooltip=[
                        alt.Tooltip("advogado:N", title="Advogado"),
                        alt.Tooltip("ano:O", title="Ano"),
                        alt.Tooltip("total:Q", title="Total (R$)", format=",.2f"),
                        alt.Tooltip("n:Q", title="Pagamentos"),
                    ],
                )
                .properties(height=420)
            )
            st.altair_chart(chart_pag, use_container_width=True)

        st.markdown("### ⚖️ Volume por **ano do processo (CNJ)**")
        st.caption(
            "Este é o ano de **distribuição** do processo (extraído do número CNJ), "
            "não o ano do pagamento. Mostra de quais 'safras' de processos cada "
            "advogado vem recebendo."
        )
        if df_proc.empty:
            st.info("Nenhum dos selecionados tem processos com CNJ parseável.")
        else:
            chart_proc = (
                alt.Chart(df_proc)
                .mark_line(point=alt.OverlayMarkDef(size=80), strokeWidth=2.5)
                .encode(
                    x=alt.X("ano:O", title="Ano de distribuição do processo"),
                    y=alt.Y("total:Q", title="R$ total recebido por processos desse ano"),
                    color=alt.Color("advogado:N", title="Advogado",
                                    legend=alt.Legend(orient="bottom", columns=2)),
                    tooltip=[
                        alt.Tooltip("advogado:N", title="Advogado"),
                        alt.Tooltip("ano:O", title="Ano do processo"),
                        alt.Tooltip("total:Q", title="Total (R$)", format=",.2f"),
                        alt.Tooltip("n:Q", title="Pagamentos"),
                    ],
                )
                .properties(height=420)
            )
            st.altair_chart(chart_proc, use_container_width=True)

        st.markdown("### 📋 Resumo lado a lado")
        resumo = con.execute(f"""
            SELECT advogado_id,
                   COUNT(*) AS n_pgto,
                   SUM(valor_bruto) AS total,
                   AVG(valor_bruto) AS ticket,
                   COUNT(DISTINCT processo) AS n_proc,
                   COUNT(DISTINCT comarca) AS n_com,
                   MIN(ano) AS pgto_primeiro,
                   MAX(ano) AS pgto_ultimo,
                   MIN(ano_processo) AS proc_primeiro,
                   MAX(ano_processo) AS proc_ultimo,
                   AVG(CASE WHEN ano_processo IS NOT NULL
                            THEN ano - ano_processo END) AS anos_medio_pgto
            FROM pagamentos
            WHERE advogado_id IN ({placeholders})
            GROUP BY advogado_id
        """).df()
        resumo["advogado"] = resumo["advogado_id"].map(label_of)
        ordered_cols = [
            "advogado", "n_pgto", "total", "ticket", "n_proc", "n_com",
            "pgto_primeiro", "pgto_ultimo", "proc_primeiro", "proc_ultimo",
            "anos_medio_pgto",
        ]
        st.dataframe(
            resumo[ordered_cols].rename(columns={
                "advogado": "Advogado", "n_pgto": "Pagamentos",
                "total": "Total (R$)", "ticket": "Ticket médio (R$)",
                "n_proc": "Processos", "n_com": "Comarcas",
                "pgto_primeiro": "1º pgto (ano)", "pgto_ultimo": "Último pgto",
                "proc_primeiro": "Proc + antigo", "proc_ultimo": "Proc + novo",
                "anos_medio_pgto": "Anos médios até pgto",
            }).style.format({
                "Total (R$)": "{:,.2f}", "Ticket médio (R$)": "{:,.2f}",
                "Anos médios até pgto": "{:.2f}",
            }),
            use_container_width=True, hide_index=True,
        )


# ===== GEOGRAFIA =========================================================
with tabs[5]:
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
with tabs[6]:
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
with tabs[7]:
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


# ===== COMISSÃO =========================================================
with tabs[8]:
    st.subheader("Análise da Comissão de Dativos")
    st.caption(
        "Compara membros da comissão (você pode editar a lista abaixo) contra "
        "o restante dos advogados na base. Útil para identificar padrões "
        "atípicos de pagamentos a quem fiscaliza o sistema."
    )

    if not PRIVATE:
        st.warning(
            "Esta aba só funciona em **modo privado** (com nomes reais). "
            "O matching de nomes é necessário para vincular aos `advogado_id`. "
            "Rode local com `streamlit run app.py` quando o `dativos_full.duckdb` existir."
        )
    else:
        st.markdown("**Membros da comissão** (edite cargos/nomes se necessário):")
        # Editable list — defaults to the pre-configured 17 members
        default_df = pd.DataFrame(
            com_mod.COMISSAO_DEFAULT, columns=["Cargo", "Nome"]
        )
        edited = st.data_editor(
            default_df,
            num_rows="dynamic",
            use_container_width=True,
            key="comissao_editor",
            column_config={
                "Cargo": st.column_config.TextColumn(width="medium"),
                "Nome": st.column_config.TextColumn(width="large"),
            },
        )

        # Filter out empty rows
        edited = edited.dropna(how="all")
        edited = edited[edited["Nome"].astype(str).str.strip() != ""]
        members = list(zip(edited["Cargo"].fillna("").tolist(),
                           edited["Nome"].astype(str).tolist()))

        if not members:
            st.info("Adicione ao menos um nome para rodar a análise.")
        else:
            # ── Matching ──────────────────────────────────────────────
            matches = com_mod.match_members(con, members)
            n_exato = sum(1 for m in matches if m.method == "exato")
            n_fuzzy = sum(1 for m in matches if m.method == "fuzzy")
            n_nf    = sum(1 for m in matches if m.method == "nao_encontrado")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total", len(matches))
            c2.metric("Match exato", n_exato)
            c3.metric("Match fuzzy", n_fuzzy)
            c4.metric("Não encontrados", n_nf)

            with st.expander("🔎 Detalhe do matching", expanded=(n_fuzzy + n_nf > 0)):
                match_df = pd.DataFrame([{
                    "Cargo": m.cargo,
                    "Nome informado": m.nome_input,
                    "Nome na base": m.nome_matched or "—",
                    "Método": m.method,
                    "ADV_id": m.advogado_id or "—",
                } for m in matches])
                st.dataframe(match_df, use_container_width=True, hide_index=True)

            found_ids = [m.advogado_id for m in matches if m.advogado_id]

            if not found_ids:
                st.error("Nenhum nome casou com a base.")
            else:
                # ── Métricas por membro ──────────────────────────────
                st.markdown("### 💰 Métricas por membro")
                df_membros = com_mod.metricas_por_membro(con, found_ids)
                # join cargo
                cargo_map = {m.advogado_id: m.cargo for m in matches if m.advogado_id}
                df_membros["cargo"] = df_membros["advogado_id"].map(cargo_map)
                df_membros = df_membros[[
                    "cargo", "nome", "n_pgto", "total", "ticket", "n_proc",
                    "n_com", "pgto_min", "pgto_max", "anos_medio",
                ]]
                st.dataframe(
                    df_membros.rename(columns={
                        "cargo": "Cargo", "nome": "Nome",
                        "n_pgto": "Pgto", "total": "Total (R$)",
                        "ticket": "Ticket (R$)", "n_proc": "Processos",
                        "n_com": "Comarcas",
                        "pgto_min": "1º pgto", "pgto_max": "Último pgto",
                        "anos_medio": "Anos médios até pgto",
                    }).style.format({
                        "Total (R$)": "{:,.2f}", "Ticket (R$)": "{:,.2f}",
                        "Anos médios até pgto": "{:.2f}",
                    }),
                    use_container_width=True, hide_index=True, height=550,
                )

                total_com = df_membros["total"].sum()
                st.caption(
                    f"Total recebido pelos {len(found_ids)} membros encontrados: "
                    f"**{fmt_brl(total_com)}**"
                )

                # ── Comparativo Comissão vs Demais ───────────────────
                st.markdown("### ⚖️ Comissão vs Demais (mediana e média por advogado)")
                comp = com_mod.comparativo_medio(con, found_ids)
                if not comp.empty:
                    # Highlight ratio
                    def _highlight(val):
                        if pd.isna(val):
                            return ""
                        if val >= 3.0:
                            return "background-color: #fee; color: #900; font-weight: bold;"
                        if val >= 2.0:
                            return "background-color: #fff4e5;"
                        return ""

                    st.dataframe(
                        comp.rename(columns={
                            "metrica": "Métrica",
                            "comissao_med": "Com. mediana",
                            "comissao_avg": "Com. média",
                            "demais_med": "Demais mediana",
                            "demais_avg": "Demais média",
                            "razao_med": "Razão med",
                        }).style.format({
                            "Com. mediana": "{:,.2f}", "Com. média": "{:,.2f}",
                            "Demais mediana": "{:,.2f}", "Demais média": "{:,.2f}",
                            "Razão med": "{:.2f}×",
                        }).applymap(_highlight, subset=["Razão med"]),
                        use_container_width=True, hide_index=True,
                    )
                    st.caption(
                        "🔴 Razão ≥ 3× | 🟠 Razão ≥ 2× — sinais a investigar."
                    )

                # ── Ranking ────────────────────────────────────────────
                st.markdown("### 🏆 Posição no ranking geral")
                rk = com_mod.ranking_membros(con, found_ids)
                rk["cargo"] = rk["advogado_id"].map(cargo_map)
                # Build clearer columns:
                #   - "Posição" = N/total (e.g. "30 / 8.283")
                #   - "Top X%" = quão alto na pirâmide (menor = melhor)
                #   - "Percentil" = inverso, alto = melhor (98,79% = recebeu mais que 98,79% dos demais)
                total_adv = int(con.execute(
                    "SELECT COUNT(*) FROM (SELECT DISTINCT advogado_id FROM pagamentos)"
                ).fetchone()[0])
                rk["posicao_label"] = rk["rk"].apply(
                    lambda n: f"#{int(n):,} / {total_adv:,}".replace(",", ".")
                )
                rk["top_pct"] = rk["pct_top"]            # menor = melhor (top 1% = 0,01)
                rk["percentil"] = 1 - rk["pct_top"]      # maior = melhor (98,79%)
                rk = rk[["posicao_label", "top_pct", "percentil", "cargo", "nome", "total"]]
                st.caption(
                    "**Como ler**: *Top X%* é onde a pessoa está na pirâmide "
                    "(0,5% = melhor que 99,5% da base). *Percentil* é a "
                    "versão complementar (98,8% = recebeu mais que 98,8% dos demais). "
                    "São a mesma informação, formatos diferentes."
                )
                st.dataframe(
                    rk.rename(columns={
                        "posicao_label": "Posição",
                        "top_pct": "Top X% (menor = melhor)",
                        "percentil": "Percentil (maior = melhor)",
                        "cargo": "Cargo", "nome": "Nome", "total": "Total (R$)",
                    }).style.format({
                        "Top X% (menor = melhor)": "{:.2%}",
                        "Percentil (maior = melhor)": "{:.2%}",
                        "Total (R$)": "{:,.2f}",
                    }),
                    use_container_width=True, hide_index=True, height=550,
                )

                # ── Pré × Pós cutoff ─────────────────────────────────
                st.markdown(f"### 📅 Pré × Pós {cutoff_year}")
                pp = com_mod.prepos_membros(con, found_ids, cutoff_year)
                pp["cargo"] = pp["advogado_id"].map(cargo_map)
                pp = pp[["cargo", "nome", "pre", "pos", "n_pre", "n_pos", "fator"]]
                st.dataframe(
                    pp.rename(columns={
                        "cargo": "Cargo", "nome": "Nome",
                        "pre": "Pré (R$)", "pos": "Pós (R$)",
                        "n_pre": "Pgto pré", "n_pos": "Pgto pós",
                        "fator": "Fator pós÷pré",
                    }).style.format({
                        "Pré (R$)": "{:,.2f}", "Pós (R$)": "{:,.2f}",
                        "Fator pós÷pré": lambda v: f"{v:.2f}×" if pd.notna(v) else "—",
                    }),
                    use_container_width=True, hide_index=True, height=550,
                )

                # ── Evolução temporal ────────────────────────────────
                st.markdown("### 📈 Evolução temporal (todos juntos)")
                evol = com_mod.evolucao_temporal(con, found_ids)
                if not evol.empty:
                    chart = (
                        alt.Chart(evol)
                        .mark_line(point=alt.OverlayMarkDef(size=50), strokeWidth=2)
                        .encode(
                            x=alt.X("ano:O", title="Ano de pagamento"),
                            y=alt.Y("total:Q", title="R$ no ano"),
                            color=alt.Color("nome:N", title="Membro",
                                            legend=alt.Legend(orient="bottom", columns=2)),
                            tooltip=[
                                alt.Tooltip("nome:N", title="Membro"),
                                alt.Tooltip("ano:O", title="Ano"),
                                alt.Tooltip("total:Q", title="R$", format=",.2f"),
                                alt.Tooltip("n_pgto:Q", title="Pgto"),
                            ],
                        )
                        .properties(height=450)
                    )
                    st.altair_chart(chart, use_container_width=True)

                # ── Análise estatística ────────────────────────────────
                st.markdown("---")
                st.markdown("### 📊 Análise estatística — a Comissão está na mesma curva dos demais?")
                st.caption(
                    "Teste formal **Mann-Whitney U** (não-paramétrico): testa se "
                    "as distribuições de valor total recebido têm forma/local "
                    "compatíveis ou são distintas estatisticamente."
                )

                mw = com_mod.mann_whitney_u(con, found_ids)
                if mw["p"] < 0.001:
                    veredito = "🔴 Diferença EXTREMAMENTE significativa"
                elif mw["p"] < 0.01:
                    veredito = "🟠 Diferença muito significativa"
                elif mw["p"] < 0.05:
                    veredito = "🟡 Diferença significativa"
                else:
                    veredito = "🟢 Sem evidência de diferença"

                k1, k2, k3, k4 = st.columns(4)
                k1.metric("p-value (Mann-Whitney U)",
                          f"{mw['p']:.2e}",
                          help="Probabilidade de ver esta diferença por acaso. p<0.05 = significativo.")
                k2.metric("Z-statistic", f"{mw['Z']:+.2f}",
                          help="Quantos desvios-padrão a Comissão está deslocada.")
                k3.metric("Prob. de superioridade",
                          f"{mw['prob_sup']:.1%}",
                          help="Chance de um membro escolhido aleatoriamente "
                               "receber MAIS que um advogado aleatório dos demais. "
                               "50% = igual; 100% = sempre maior.")
                k4.metric("Veredito",
                          veredito.split(" ", 1)[0],
                          veredito.split(" ", 1)[1] if " " in veredito else "")

                # Descritivas
                st.markdown("#### Estatísticas descritivas")
                desc = com_mod.estatisticas_descritivas(con, found_ids)
                st.dataframe(
                    desc.rename(columns={
                        "grupo": "Grupo", "n": "N",
                        "media": "Média", "desvio": "Desvio-padrão",
                        "min": "Mínimo", "p25": "P25",
                        "p50": "Mediana (P50)", "p75": "P75",
                        "p90": "P90", "p95": "P95", "p99": "P99",
                        "max": "Máximo",
                    }).style.format({
                        c: "{:,.2f}" for c in
                        ["Média", "Desvio-padrão", "Mínimo", "P25", "Mediana (P50)",
                         "P75", "P90", "P95", "P99", "Máximo"]
                    }),
                    use_container_width=True, hide_index=True,
                )

                # 4 gráficos lado a lado em 2x2
                serie = com_mod.serie_por_grupo(con, found_ids)
                hist = com_mod.histograma_buckets(con, found_ids)
                cdf  = com_mod.cdf_data(con, found_ids)
                pct_membros = com_mod.percentil_de_cada_membro(con, found_ids)

                COLOR_SCALE = alt.Scale(
                    domain=["Comissão", "Demais"],
                    range=["#d62728", "#1f77b4"],
                )

                g1, g2 = st.columns(2)

                # Gráfico 1: Boxplot lado a lado em log
                with g1:
                    st.markdown("**Boxplot — log-scale**")
                    chart_box = (
                        alt.Chart(serie)
                        .mark_boxplot(size=60, extent="min-max")
                        .encode(
                            x=alt.X("grupo:N", title="", scale=alt.Scale(paddingInner=0.5)),
                            y=alt.Y("total:Q", title="R$ total recebido",
                                    scale=alt.Scale(type="log")),
                            color=alt.Color("grupo:N", scale=COLOR_SCALE, legend=None),
                        )
                        .properties(height=320)
                    )
                    st.altair_chart(chart_box, use_container_width=True)
                    st.caption(
                        "A caixa contém 50% dos advogados (P25 a P75). Linha central = mediana. "
                        "Se as caixas não se sobrepõem, as distribuições são claramente distintas."
                    )

                # Gráfico 2: Histograma sobreposto em buckets de R$
                with g2:
                    st.markdown("**Histograma (% por bucket)**")
                    chart_hist = (
                        alt.Chart(hist)
                        .mark_bar(opacity=0.75)
                        .encode(
                            x=alt.X("bucket:N", title="Faixa de R$",
                                    sort=["< 1k","1-5k","5-10k","10-25k","25-50k",
                                          "50-100k","100-200k","200-500k","500k-1M","> 1M"]),
                            y=alt.Y("pct:Q", title="% dos advogados do grupo",
                                    axis=alt.Axis(format=".0%")),
                            color=alt.Color("grupo:N", scale=COLOR_SCALE,
                                            legend=alt.Legend(orient="top")),
                            xOffset="grupo:N",
                            tooltip=[
                                alt.Tooltip("grupo:N"),
                                alt.Tooltip("bucket:N", title="Faixa"),
                                alt.Tooltip("n:Q", title="N advogados"),
                                alt.Tooltip("pct:Q", title="% do grupo", format=".1%"),
                            ],
                        )
                        .properties(height=320)
                    )
                    st.altair_chart(chart_hist, use_container_width=True)
                    st.caption(
                        "A massa da Comissão está em R$ 50k-200k; a dos demais em < R$ 5k. "
                        "Bimodalidade da Comissão = poucos no meio."
                    )

                g3, g4 = st.columns(2)

                # Gráfico 3: CDF (curva acumulada)
                with g3:
                    st.markdown("**CDF — % acumulada de advogados que ficam abaixo de X reais**")
                    chart_cdf = (
                        alt.Chart(cdf)
                        .mark_line(strokeWidth=2.5)
                        .encode(
                            x=alt.X("total:Q", title="R$ total recebido",
                                    scale=alt.Scale(type="log")),
                            y=alt.Y("cdf:Q", title="% acumulada",
                                    axis=alt.Axis(format=".0%")),
                            color=alt.Color("grupo:N", scale=COLOR_SCALE,
                                            legend=alt.Legend(orient="top")),
                            tooltip=[
                                "grupo:N",
                                alt.Tooltip("total:Q", title="R$", format=",.2f"),
                                alt.Tooltip("cdf:Q", title="% até aqui", format=".1%"),
                            ],
                        )
                        .properties(height=320)
                    )
                    st.altair_chart(chart_cdf, use_container_width=True)
                    st.caption(
                        "Se as curvas estão separadas, a Comissão tem uma cauda muito "
                        "mais 'pra direita' (recebe mais) que os demais."
                    )

                # Gráfico 4: Strip plot — cada membro como bolinha sobre a curva dos demais
                with g4:
                    st.markdown("**Posição de cada membro (R$ em log)**")

                    # Seletor de ano específico para o strip plot
                    anos_strip = [int(r[0]) for r in con.execute(
                        "SELECT DISTINCT ano FROM pagamentos ORDER BY ano"
                    ).fetchall()]
                    strip_ano_sel = st.selectbox(
                        "Filtrar por ano de pagamento",
                        options=["Vida toda (todos os anos)"] + anos_strip,
                        index=0,
                        key="strip_ano_sel",
                        help="Recalcula o gráfico considerando apenas pagamentos "
                             "daquele ano. 'Vida toda' soma todos os anos.",
                    )
                    strip_ano = None if strip_ano_sel == "Vida toda (todos os anos)" else int(strip_ano_sel)
                    serie_strip = com_mod.serie_por_grupo(con, found_ids, ano=strip_ano)

                    n_com_strip = int((serie_strip["grupo"] == "Comissão").sum())
                    n_dem_strip = int((serie_strip["grupo"] == "Demais").sum())
                    if n_com_strip == 0:
                        st.warning(
                            f"Nenhum membro da comissão recebeu em {strip_ano}. "
                            "Nada para plotar."
                        )
                    else:
                        # Demais: amostra para não poluir; Comissão: tudo
                        dem_strip = serie_strip[serie_strip["grupo"] == "Demais"]
                        dem_sample = dem_strip.sample(
                            n=min(1500, n_dem_strip), random_state=42,
                        ) if n_dem_strip else dem_strip
                        dem_sample = dem_sample.copy()
                        dem_sample["y_jit"] = np.random.uniform(0, 1, len(dem_sample))
                        com_pts = serie_strip[serie_strip["grupo"] == "Comissão"].copy()
                        com_pts["y_jit"] = np.random.uniform(0.2, 0.8, len(com_pts))

                        bg = (
                            alt.Chart(dem_sample)
                            .mark_circle(opacity=0.15, color="#1f77b4")
                            .encode(
                                x=alt.X("total:Q", title="R$ total recebido"
                                        + (f" em {strip_ano}" if strip_ano else " (vida toda)"),
                                        scale=alt.Scale(type="log")),
                                y=alt.Y("y_jit:Q", title="", axis=None),
                                tooltip=[
                                    alt.Tooltip("nome:N"),
                                    alt.Tooltip("total:Q", title="R$", format=",.2f"),
                                ],
                            )
                        )
                        fg = (
                            alt.Chart(com_pts)
                            .mark_circle(size=200, opacity=0.95, color="#d62728",
                                         stroke="white", strokeWidth=2)
                            .encode(
                                x=alt.X("total:Q"),
                                y=alt.Y("y_jit:Q"),
                                tooltip=[
                                    alt.Tooltip("nome:N", title="Membro"),
                                    alt.Tooltip("total:Q", title="R$", format=",.2f"),
                                ],
                            )
                        )
                        st.altair_chart(
                            (bg + fg).properties(height=320),
                            use_container_width=True,
                        )
                        st.caption(
                            f"🔴 Membros da comissão ({n_com_strip} pessoas) · "
                            f"🔵 Amostra dos demais ({len(dem_sample)} de {n_dem_strip}). "
                            f"Filtro: **{strip_ano_sel}**. "
                            "Se os 🔴 estão concentrados na direita, é evidência visual "
                            "da diferença de patamar."
                        )

                # Tabela de percentil + Z de cada membro (resumo da análise)
                st.markdown("#### Percentil de cada membro na distribuição dos demais")
                show = pct_membros[["nome", "total", "percentil", "z_log"]].copy()
                show = show.rename(columns={
                    "nome": "Nome", "total": "Total (R$)",
                    "percentil": "Percentil",
                    "z_log": "Z (log) — |Z|>2 anomalia",
                })
                def _hi(v):
                    if pd.isna(v): return ""
                    if v >= 0.99: return "background-color:#fee;color:#900;font-weight:bold"
                    if v >= 0.95: return "background-color:#fff4e5"
                    return ""
                st.dataframe(
                    show.style.format({
                        "Total (R$)": "{:,.2f}",
                        "Percentil": "{:.2%}",
                        "Z (log) — |Z|>2 anomalia": "{:+.2f}",
                    }).applymap(_hi, subset=["Percentil"]),
                    use_container_width=True, hide_index=True, height=520,
                )


# ===== RECONCILIAÇÃO ====================================================
with tabs[9]:
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
with tabs[10]:
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
