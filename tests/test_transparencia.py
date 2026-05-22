"""Tests for the transparencia parser + anonymization."""
from etl.anonymize import normalize_name, pseudonym
from etl.transparencia.parse import HEADER_ALIASES, _norm, extract_ano_processo


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


def test_pseudonym_same_name_same_id_regardless_of_cpf_mask():
    """SEFAZ publishes the same person's CPF with different mask formats
    across yearly files. The pseudonym must NOT depend on the mask, else
    we silently split one lawyer into two ADV_ids (real bug seen on Solange,
    Eliana, Ericka, Joselita, Pablo, Rita, Caroline, Juliano, Keler).
    """
    s = "fixed-salt"
    a = pseudonym("Solange do Nascimento Oliveira Prata", "***42351***", s)
    b = pseudonym("Solange do Nascimento Oliveira Prata", "***423517**", s)
    assert a == b


# ── CNJ year extraction ─────────────────────────────────────────────────
def test_cnj_year_canonical():
    # observed: 0007704-92.2011.8.08.0030 → 2011
    assert extract_ano_processo("0007704-92.2011.8.08.0030") == 2011
    # 2025 PGE example
    assert extract_ano_processo("5000385-59.2024.8.08.0053") == 2024


def test_cnj_year_returns_none_for_non_cnj():
    assert extract_ano_processo(None) is None
    assert extract_ano_processo("") is None
    assert extract_ano_processo("processo antigo 12345/2010") is None
    assert extract_ano_processo("garbage") is None


def test_cnj_year_handles_implausible_years():
    # year < 1980 or > 2100 → None
    assert extract_ano_processo("0007704-92.1850.8.08.0030") is None
    assert extract_ano_processo("0007704-92.2300.8.08.0030") is None
    # boundary 1980 valid
    assert extract_ano_processo("0007704-92.1980.8.08.0030") == 1980


def test_cnj_year_embedded_in_text():
    # extraction should still work if there's surrounding text
    assert extract_ano_processo("Proc 0007704-92.2015.8.08.0030 (extra)") == 2015
