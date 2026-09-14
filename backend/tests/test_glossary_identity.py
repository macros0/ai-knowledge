from app.services.glossary.identity import collect_identity_conflicts, identity_keys_for_term
from app.services.glossary.types import InfotypeRuleSnapshot


def test_identity_uses_complete_literal_values_not_substrings():
    terms = (
        {"id": 1, "kind": "business_term", "original_name": "табель", "aliases": []},
        {"id": 2, "kind": "business_term", "original_name": "Учебный табель", "aliases": []},
    )
    assert collect_identity_conflicts(terms) == ()


def test_identity_detects_name_alias_and_infotype_conflicts():
    rules = (InfotypeRuleSnapshot(1, "base", 0, 999, ("IT", "ИТ")),)
    first = {"id": 1, "kind": "sap_infotype", "infotype_number": "0003", "original_name": "Payroll", "aliases": []}
    second = {"id": 2, "kind": "business_term", "original_name": "Другой", "aliases": [{"alias": " payroll "}]}
    third = {"id": 3, "kind": "sap_infotype", "infotype_number": "0003", "original_name": "Other", "aliases": []}
    assert ("literal", "payroll") in identity_keys_for_term(first)
    assert {item["key_kind"] for item in collect_identity_conflicts((first, second, third), rules)} == {"literal", "infotype"}
