"""Streamlit dashboard for PGE-ES dativos transparency data."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).resolve().parent / "data" / "dativos.db"

st.set_page_config(
    page_title="Dativos ES — Transparência",
    page_icon="⚖️",
    layout="wide",
)


@st.cache_data(ttl=3600)
def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not DB_PATH.exists():
        return pd.DataFrame(), pd.DataFrame()
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            """
            SELECT period_start, period_end, mes_referencia, period_label,
                   n_solicitacoes, n_analises, valor_bruto,
                   source_resource_id, imported_at
            FROM solicitacoes_mensais
            ORDER BY period_start
            """,
            conn,
        )
        sources = pd.read_sql_query(
            "SELECT * FROM sources ORDER BY last_modified DESC", conn
        )
    if not df.empty:
        df["period_start"] = pd.to_datetime(df["period_start"])
        df["period_end"] = pd.to_datetime(df["period_end"])
        df["ticket_medio_analise"] = df["valor_bruto"] / df["n_analises"]
        df["saldo_mensal"] = df["n_solicitacoes"] - df["n_analises"]
        df["backlog_acumulado"] = df["saldo_mensal"].cumsum()
    return df, sources


def fmt_brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def find_gaps(df: pd.DataFrame) -> list[tuple[date, date]]:
    """Return list of missing (expected_start, expected_end) windows."""
    if df.empty:
        return []
    sorted_df = df.sort_values("period_start").reset_index(drop=True)
    gaps: list[tuple[date, date]] = []
    for i in range(1, len(sorted_df)):
        prev_end = sorted_df.loc[i - 1, "period_end"].date()
        curr_start = sorted_df.loc[i, "period_start"].date()
        # Each window ends on day-15 and the next starts on day-16; any gap > 1 day is missing data.
        if (curr_start - prev_end).days > 1:
            gaps.append((prev_end + timedelta(days=1), curr_start - timedelta(days=1)))
    return gaps


df, sources = load_data()

st.title("⚖️ Dativos ES — Honorários de Advogados Dativos")
st.caption(
    "Solicitações de pagamento, análises e valores brutos pagos pela "
    "Procuradoria-Geral do Estado do Espírito Santo (PGE-ES) — fonte: "
    "[dados.es.gov.br](https://dados.es.gov.br/organization/procuradoria-geral-do-estado-do-espirito-santo)."
)

if df.empty:
    st.error(
        "Banco de dados vazio. Rode `python -m etl` para popular `data/dativos.db`."
    )
    st.stop()

tab_visao, tab_backlog, tab_achados, tab_dados, tab_sobre = st.tabs(
    ["Visão geral", "Backlog", "Achados", "Dados", "Sobre"]
)

with tab_visao:
    total_sol = int(df["n_solicitacoes"].sum())
    total_ana = int(df["n_analises"].sum())
    total_val = float(df["valor_bruto"].sum())
    ticket = total_val / total_ana if total_ana else 0.0
    primeiro = df["period_start"].min().strftime("%b/%Y")
    ultimo = df["period_end"].max().strftime("%b/%Y")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Solicitações", f"{total_sol:,}".replace(",", "."))
    c2.metric("Análises", f"{total_ana:,}".replace(",", "."))
    c3.metric("Valor bruto pago", fmt_brl(total_val))
    c4.metric("Ticket médio / análise", fmt_brl(ticket))
    st.caption(f"Período coberto: {primeiro} → {ultimo} · {len(df)} competências mensais")

    st.subheader("Valor bruto pago por mês")
    chart_val = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("period_start:T", title="Competência"),
            y=alt.Y("valor_bruto:Q", title="R$"),
            tooltip=[
                alt.Tooltip("period_label:N", title="Período"),
                alt.Tooltip("valor_bruto:Q", title="Valor", format=",.2f"),
                alt.Tooltip("n_analises:Q", title="Análises"),
            ],
        )
        .properties(height=320)
    )
    st.altair_chart(chart_val, use_container_width=True)

    st.subheader("Solicitações vs Análises")
    long = df.melt(
        id_vars=["period_start", "period_label"],
        value_vars=["n_solicitacoes", "n_analises"],
        var_name="serie",
        value_name="qtd",
    )
    long["serie"] = long["serie"].map(
        {"n_solicitacoes": "Solicitações", "n_analises": "Análises"}
    )
    chart_qty = (
        alt.Chart(long)
        .mark_line(point=True)
        .encode(
            x=alt.X("period_start:T", title="Competência"),
            y=alt.Y("qtd:Q", title="Quantidade"),
            color=alt.Color("serie:N", title=""),
            tooltip=["period_label:N", "serie:N", "qtd:Q"],
        )
        .properties(height=320)
    )
    st.altair_chart(chart_qty, use_container_width=True)

with tab_backlog:
    st.markdown(
        "**Backlog** = solicitações entrando − análises saindo. "
        "Acumulado positivo significa fila crescendo; negativo, PGE consumindo estoque represado."
    )
    c1, c2 = st.columns(2)
    c1.metric("Saldo total acumulado", f"{int(df['saldo_mensal'].sum()):+,}".replace(",", "."))
    pico = df.loc[df["backlog_acumulado"].idxmax()]
    c2.metric(
        "Pico do backlog acumulado",
        f"{int(pico['backlog_acumulado']):,}".replace(",", "."),
        help=f"Atingido em {pico['period_label']}",
    )

    st.subheader("Saldo mensal (solicitações − análises)")
    chart_saldo = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("period_start:T", title="Competência"),
            y=alt.Y("saldo_mensal:Q", title="Saldo do mês"),
            color=alt.condition(
                alt.datum.saldo_mensal > 0,
                alt.value("#d62728"),
                alt.value("#2ca02c"),
            ),
            tooltip=["period_label:N", "n_solicitacoes:Q", "n_analises:Q", "saldo_mensal:Q"],
        )
        .properties(height=300)
    )
    st.altair_chart(chart_saldo, use_container_width=True)

    st.subheader("Backlog acumulado ao longo do tempo")
    chart_acum = (
        alt.Chart(df)
        .mark_area(opacity=0.6)
        .encode(
            x=alt.X("period_start:T", title="Competência"),
            y=alt.Y("backlog_acumulado:Q", title="Solicitações pendentes (acumulado)"),
            tooltip=["period_label:N", "backlog_acumulado:Q"],
        )
        .properties(height=300)
    )
    st.altair_chart(chart_acum, use_container_width=True)

with tab_achados:
    st.subheader("Lacunas na série temporal")
    gaps = find_gaps(df)
    if gaps:
        for g_start, g_end in gaps:
            st.warning(
                f"**Mês faltando:** {g_start.strftime('%d/%m/%Y')} a "
                f"{g_end.strftime('%d/%m/%Y')} não está publicado pela PGE."
            )
    else:
        st.success("Série contínua, sem lacunas detectadas.")

    st.subheader("Variações atípicas")
    df_var = df.copy()
    df_var["var_valor"] = df_var["valor_bruto"].pct_change()
    top = df_var.reindex(df_var["var_valor"].abs().sort_values(ascending=False).index).head(5)
    st.dataframe(
        top[["period_label", "n_solicitacoes", "n_analises", "valor_bruto", "var_valor"]]
        .rename(
            columns={
                "period_label": "Período",
                "n_solicitacoes": "Solicitações",
                "n_analises": "Análises",
                "valor_bruto": "Valor (R$)",
                "var_valor": "Δ valor vs mês anterior",
            }
        )
        .style.format(
            {
                "Valor (R$)": "{:,.2f}",
                "Δ valor vs mês anterior": "{:+.1%}",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Ranking dos meses mais caros")
    top_val = df.nlargest(5, "valor_bruto")[
        ["period_label", "n_analises", "valor_bruto", "ticket_medio_analise"]
    ].rename(
        columns={
            "period_label": "Período",
            "n_analises": "Análises",
            "valor_bruto": "Valor (R$)",
            "ticket_medio_analise": "Ticket médio (R$)",
        }
    )
    st.dataframe(
        top_val.style.format({"Valor (R$)": "{:,.2f}", "Ticket médio (R$)": "{:,.2f}"}),
        use_container_width=True,
        hide_index=True,
    )

with tab_dados:
    st.subheader("Tabela bruta")
    show = df[
        [
            "period_label",
            "period_start",
            "period_end",
            "mes_referencia",
            "n_solicitacoes",
            "n_analises",
            "valor_bruto",
            "ticket_medio_analise",
        ]
    ].copy()
    show.columns = [
        "Período",
        "Início",
        "Fim",
        "Competência",
        "Solicitações",
        "Análises",
        "Valor bruto (R$)",
        "Ticket médio (R$)",
    ]
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.download_button(
        "⬇️ Baixar CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name="dativos_es.csv",
        mime="text/csv",
    )
    st.subheader("Fontes (CKAN)")
    st.dataframe(sources, use_container_width=True, hide_index=True)

with tab_sobre:
    st.markdown(
        """
### Sobre

Este dashboard consolida os dados públicos publicados pela
**Procuradoria-Geral do Estado do Espírito Santo (PGE-ES)** no portal de
dados abertos [dados.es.gov.br](https://dados.es.gov.br) sobre as
**solicitações de pagamento de honorários a advogados dativos**.

#### Metodologia
1. **Descoberta**: a cada execução, o ETL consulta `package_search?q=dativo`
   na CKAN API do portal para encontrar todos os datasets relacionados.
2. **Download**: baixa os arquivos XLSX de cada resource.
3. **Parsing**: o cabeçalho temporal (`"16 de abril de 2024 a 15 de maio de 2024"`)
   é parseado pra `(period_start, period_end)`. O CKAN DataStore perde essa
   coluna na importação automática, por isso reprocessamos o XLSX bruto.
4. **Dedupe**: vários arquivos têm intervalos sobrepostos (snapshots
   sucessivos). Mantemos a versão com `last_modified` mais recente por
   competência.
5. **Carga**: SQLite local, atualizado semanalmente via GitHub Actions.

#### Limitações
- Os dados são **agregados mensais**, não por advogado/processo. Não há
  granularidade individual disponível neste portal.
- A janela mensal vai do dia 16 ao dia 15 do mês seguinte
  (competência operacional da PGE).
- O DataStore CKAN do portal perde a coluna de período no upload
  automático — só o XLSX bruto carrega o tempo.

#### Código
[github.com/yurivix/dativos](https://github.com/yurivix/dativos)
        """
    )
