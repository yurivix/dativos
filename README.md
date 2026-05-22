# dativos

Análise de transparência sobre **pagamentos a advogados dativos pelo Estado do Espírito Santo**.

Consolida ~247 mil pagamentos individuais (2018–presente, R$ ~110 milhões) publicados pelo Portal da Transparência do ES e oferece análises de concentração, distorções e dispersão geográfica num dashboard interativo.

## O que tem aqui

- **Dashboard público anonimizado** com 7 abas: Visão geral, Ranking, Distorções, Drill-down por advogado, Geografia, Reconciliação, Sobre.
- **7 heurísticas de distorção**: Gini, Pareto, dispersão geográfica, picos intra-pessoa, crescimento ano a ano, concentração por vara, ticket atípico, repetência de processo.
- **Anonimização determinística** via hash com salt secreto — análise por advogado continua funcionando, mas o nome real nunca vai pro repo.
- **Refresh automático** diário via GitHub Actions.

## Stack

- **ETL**: Python 3.12 (`requests`, `openpyxl`, `pandas`)
- **Storage**: DuckDB
- **UI**: Streamlit + Altair
- **Refresh**: GitHub Actions, cron diário

## Como rodar local

```bash
# 1. Instalar deps
pip install -r requirements.txt

# 2. Rodar ETL (gera AMBOS os bancos: anonimizado + com nomes)
python -m etl

# 3. App público (anonimizado)
streamlit run app.py

# 4. App privado (com nomes reais, só local)
streamlit run app_private.py
```

O arquivo `data/salt.txt` é criado automaticamente no primeiro `python -m etl`. **Não comite esse arquivo nem o `data/dativos_full.duckdb`** — ambos estão no `.gitignore`.

Pra rodar só o banco anonimizado (sem gerar o full):

```bash
DATIVOS_NO_FULL_DB=1 python -m etl
```

## Deploy público

[Streamlit Community Cloud](https://share.streamlit.io) — conecta o repo `yurivix/dativos`, aponta para `app.py`. O `data/dativos_anon.duckdb` está commitado, então o deploy funciona sem nenhuma configuração extra.

## Estrutura

```
dativos/
├── app.py                          # App público (lê dativos_anon.duckdb)
├── app_private.py                  # wrapper para versão local (lê dativos_full.duckdb)
├── etl/
│   ├── transparencia/              # fonte primária (transparencia.es.gov.br)
│   │   ├── fetch.py
│   │   └── parse.py
│   ├── ckan/                       # fonte secundária (dados.es.gov.br, reconciliação)
│   │   ├── fetch.py
│   │   ├── parse.py
│   │   └── run.py
│   ├── anonymize.py                # hash determinístico ADV_xxxxxxxxxxxx
│   ├── duckdb_loader.py            # escreve ambos os bancos
│   └── __main__.py                 # orquestrador
├── analysis/
│   └── distortions.py              # 8 métricas de distorção
├── data/
│   ├── dativos_anon.duckdb         # commitado (só pseudônimos)
│   ├── dativos_full.duckdb         # GITIGNORED (com nomes reais)
│   ├── salt.txt                    # GITIGNORED (salt da anonimização)
│   └── raw/                        # GITIGNORED (cache XLSX)
├── tests/test_parse.py
└── .github/workflows/refresh.yml
```

## Setup do GitHub Actions

O workflow precisa do **secret** `DATIVOS_SALT` para que o hash dos advogados se mantenha estável entre execuções no CI:

1. Gere um salt local: `python -c "import secrets; print(secrets.token_hex(32))"`
2. Salve em `data/salt.txt` (gitignored) para uso local.
3. Em **Settings → Secrets and variables → Actions → New repository secret**, crie `DATIVOS_SALT` com o mesmo valor.

Se o salt mudar, todos os `ADV_xxxxxxxxxxxx` mudam. Mantenha o mesmo salt pra sempre.

## Fontes

- **Primária**: [Portal da Transparência ES — Advogados Dativos](https://transparencia.es.gov.br/Comum/AdvogadosDativos). 9 XLSX anuais (2018–2026, ~247 mil linhas). API: `/Comum/AdvogadosDativos/Download/<id>`.
- **Secundária**: [dados.es.gov.br — Procuradoria-Geral do Estado](https://dados.es.gov.br/organization/procuradoria-geral-do-estado-do-espirito-santo). Agregados mensais publicados pela PGE-ES, usados apenas para reconciliar totais.

## Limitações

- O CPF vem **pré-mascarado** pela SEFAZ (`***116817**`) — não temos o CPF completo em momento algum.
- O **mês do pagamento** é o mês de processamento financeiro, não o do fato gerador.
- O schema dos XLSX mudou entre 2024 e 2025 (`Valor INSS` → `Conta Judicial`). O parser unifica.
- Heurísticas de distorção são **pontos de partida** para investigação, não prova de irregularidade.
