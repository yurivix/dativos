"""Wrapper that runs the BIZÃO against the full (named) database locally.

The full DB is gitignored — this script is intentionally a thin wrapper so
the public app.py never references the file directly.

Use:
  streamlit run app_private.py
"""
from __future__ import annotations

import os
import runpy
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
PRIVATE_DB = ROOT / "data" / "dativos_full.duckdb"

if not PRIVATE_DB.exists():
    st.error(
        "Banco privado não encontrado em `data/dativos_full.duckdb`.\n\n"
        "Rode `python -m etl` primeiro (sem `DATIVOS_NO_FULL_DB=1`)."
    )
    st.stop()

os.environ["DATIVOS_DB"] = str(PRIVATE_DB)
runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
