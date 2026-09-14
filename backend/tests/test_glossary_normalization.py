import pytest

from app.services.glossary.normalization import (
    GlossaryValidationError,
    normalize_alias,
    normalize_canonical,
    validate_alias_options,
)
from app.services.glossary.rules import match_infotypes
from app.services.glossary.types import InfotypeRuleSnapshot


def test_normalize_alias_is_unicode_literal_normalization():
    assert normalize_alias("  ИТ\u00a00003  ") == "ит 0003"
    assert normalize_alias("İT /ABC/XYZ") == "it /abc/xyz"
    assert normalize_alias("Ёлка") == "ёлка"
    assert normalize_alias("PA-20") == "pa-20"


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        ("sap_infotype", " it0003 ", "IT0003"),
        ("sap_transaction", " /npa20 ", "/NPA20"),
        ("business_term", " /abc/xyz ", "/ABC/XYZ"),
    ],
)
def test_normalize_canonical_applies_kind_rules(kind, value, expected):
    assert normalize_canonical(value, kind) == expected


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("sap_infotype", "IT12345"),
        ("sap_infotype", "IT 0003"),
        ("sap_infotype", "XIT0003"),
        ("business_term", "12345"),
        ("business_term", "WITH SPACE"),
        ("unknown", "ABC1"),
    ],
)
def test_normalize_canonical_rejects_unsafe_values(kind, value):
    with pytest.raises(GlossaryValidationError):
        normalize_canonical(value, kind)


@pytest.mark.parametrize("alias", ["ТР", "PA", "PY", "ОМ", "OM", "pa"])
def test_short_denylist_alias_cannot_get_automatic_rights(alias):
    with pytest.raises(GlossaryValidationError):
        validate_alias_options(alias, kind="business_term", auto_expand=True, search_enabled=False)


def test_four_digit_numeric_alias_is_only_searchable_for_infotypes():
    validate_alias_options("0003", kind="sap_infotype", auto_expand=False, search_enabled=True)
    with pytest.raises(GlossaryValidationError):
        validate_alias_options("0003", kind="business_term", auto_expand=False, search_enabled=True)


@pytest.mark.parametrize(
    "query",
    ["IT0003", "ИТ 0003", "Infotyp 0003", "инфо-типа 0003"],
)
def test_user_infotype_rule_accepts_only_configured_prefixes_with_four_digits(query):
    rules = (InfotypeRuleSnapshot(1, "Configured", 0, 9999, ("IT", "ИТ ", "Infotyp ", "инфо-типа ")),)
    matches = match_infotypes(query, rules)

    assert len(matches) == 1
    assert matches[0].number == "0003"


@pytest.mark.parametrize(
    "query",
    [
        "IT3",
        "IT003",
        "инфотип 3",
        "Infotyp 003",
        "IT00037",
        "XIT0003Y",
    ],
)
def test_user_infotype_rule_rejects_short_codes_typos_and_embedded_identifiers(query):
    rules = (InfotypeRuleSnapshot(1, "Configured", 0, 9999, ("IT", "ИТ", "Infotyp", "инфо-типа")),)
    assert match_infotypes(query, rules) == ()
