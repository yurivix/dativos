"""Tests for the transparencia parser + anonymization."""
from etl.anonymize import normalize_name, pseudonym
from etl.transparencia.parse import HEADER_ALIASES, _norm


def test_norm_strips_accents_and_punct():
    assert _norm("Nome do Beneficiário") == "nome do beneficiario"
    assert _norm("Vara/Nome") == "vara/nome"
    assert _norm("CPF") == "cpf"


def test_header_aliases_cover_both_schemas():
    # 2018-2024 schema
    assert HEADER_ALIASES["beneficiario"] == "nome"
    assert HEADER_ALIASES["valor inss"] == "valor_inss"
    assert HEADER_ALIASES["vara/nome"] == "vara_nome"
    # 2025+ schema
    assert HEADER_ALIASES["nome do beneficiario"] == "nome"
    assert HEADER_ALIASES["conta judicial"] == "conta_judicial"
    assert HEADER_ALIASES["retencao irrf"] == "valor_irrf"
    assert HEADER_ALIASES["varanome"] == "vara_nome"


def test_normalize_name_collapses_whitespace_and_accents():
    assert normalize_name("José  da  Silva ") == "jose da silva"
    assert normalize_name("MARIA OLIVEIRA") == "maria oliveira"


def test_pseudonym_is_deterministic_with_same_salt():
    s = "fixed-salt"
    a = pseudonym("João Silva", "***123456**", s)
    b = pseudonym("joão  silva", "***123456**", s)  # extra space + lowercase
    assert a == b, "normalization should make these collide"
    assert a.startswith("ADV_")
    assert len(a) == len("ADV_") + 12


def test_pseudonym_diverges_with_different_salt():
    a = pseudonym("João Silva", "***123456**", "salt-1")
    b = pseudonym("João Silva", "***123456**", "salt-2")
    assert a != b


def test_pseudonym_distinguishes_different_cpf():
    s = "fixed-salt"
    a = pseudonym("João Silva", "***123456**", s)
    b = pseudonym("João Silva", "***999999**", s)
    assert a != b
