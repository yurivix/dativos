"""Unit tests for the period parser.

The PGE spreadsheets use inconsistent punctuation in the period column.
These cases all came from real rows in transparencia-ativa-dativos-pge*.xlsx.
"""
from datetime import date

from etl.ckan.parse import parse_period


def test_canonical_form():
    assert parse_period("16 de abril de 2024 a 15 de maio de 2024") == (
        date(2024, 4, 16),
        date(2024, 5, 15),
    )


def test_trailing_colon_and_space():
    assert parse_period("16 de abril de 2024 a 15 de maio de 2024: ") == (
        date(2024, 4, 16),
        date(2024, 5, 15),
    )


def test_missing_de_before_month():
    # observed: "16 de setembro de 2023 a 15 outubro de 2023"
    assert parse_period("16 de setembro de 2023 a 15 outubro de 2023") == (
        date(2023, 9, 16),
        date(2023, 10, 15),
    )


def test_handles_accents():
    assert parse_period("16 de março de 2023 a 15 de abril de 2023:") == (
        date(2023, 3, 16),
        date(2023, 4, 15),
    )


def test_year_boundary():
    assert parse_period("16 de dezembro de 2023 a 15 de janeiro de 2024") == (
        date(2023, 12, 16),
        date(2024, 1, 15),
    )


def test_returns_none_for_garbage():
    assert parse_period("") is None
    assert parse_period("Total geral") is None
    assert parse_period(None) is None  # type: ignore[arg-type]


def test_returns_none_for_invalid_month():
    assert parse_period("16 de xyz de 2024 a 15 de maio de 2024") is None
