# dativos

Dashboard de transparência sobre **honorários pagos a advogados dativos pelo Estado do Espírito Santo**.

Consolida os dados publicados pela Procuradoria-Geral do Estado (PGE-ES) no portal [dados.es.gov.br](https://dados.es.gov.br), corrige a coluna de período que o CKAN DataStore perde no upload, deduplica os snapshots sobrepostos e expõe tudo num dashboard Streamlit.

## O que tem aqui

- Série temporal de solicitações, análises e valores pagos (mar/2023 → presente).
- Detecção automática de lacunas (meses não publicados pela PGE).
- Backlog: estoque de solicitações pendentes mês a mês.
- Download dos dados consolidados em CSV.

## Stack

- **ETL**: Python (`requests`, `openpyxl`, `pandas`)
- **Storage**: SQLite (`data/dativos.db`, commitado no repo)
- **UI**: Streamlit + Altair
- **Refresh**: GitHub Actions, cron semanal (segundas, 06:00 UTC)

## Rodando local

```bash
pip install -r requirements.txt
python -m etl              # baixa XLSX da CKAN, popula data/dativos.db
streamlit run app.py
```

## Testes

```bash
pip install pytest
python -m pytest -q
```

## Estrutura

```
dativos/
├── app.py                    # Streamlit
├── etl/
│   ├── fetch.py              # CKAN discovery + download
│   ├── parse.py              # parser do período em texto livre
│   ├── load.py               # dedupe + SQLite
│   └── __main__.py           # entrypoint do ETL
├── data/dativos.db           # banco (commitado)
├── tests/test_parse.py
└── .github/workflows/refresh.yml
```

## Fonte dos dados

Procuradoria-Geral do Estado do Espírito Santo, datasets `solicitacao(oes)-de-pagamento-de-honorarios-a-advogados-dativos-*` no portal [dados.es.gov.br](https://dados.es.gov.br/organization/procuradoria-geral-do-estado-do-espirito-santo).

## Limitações conhecidas

- Os dados públicos são **agregados mensais**, não por advogado ou processo.
- A janela mensal da PGE vai do dia 16 ao dia 15 do mês seguinte.
- O DataStore CKAN da PGE perde a coluna de período por causa de cabeçalho mesclado no XLSX — por isso o ETL sempre reprocessa o arquivo bruto em vez de usar `datastore_search`.
