from app.services.context_builder import drop_unmatched_blocks
from app.services.glossary.types import MatchGroup, MatchSpan
from app.services.glossary.matching import matching_group_keys


def _block(title: str, content: str) -> dict:
    return {
        "title": title,
        "content": content,
        "tags": [],
        "source_filename": "source.md",
        "point_type": "concept",
        "kind": "concept",
        "chunk_index": 0,
    }


def test_domain_match_alone_does_not_drop_semantic_candidates():
    exact_alias = _block(
        "PY-ES: Infotypes, Transactions, and Reports",
        "IT0003 - Payroll Status",
    )
    semantic_variant = _block(
        "Общие сведения",
        "Транзакция массового изменения инфо-типа 0003.",
    )
    group = MatchGroup(
        term_id=1,
        canonical="TERM_IT0003",
        kind="sap_infotype",
        canonical_locale="ru",
        original_name="ИТ 0003",
        term_version=1,
        source_revision=1,
        spans=(MatchSpan(0, 6, "ИТ0003", "alias", "ИТ0003"),),
        matched_forms=("IT0003", "инфотип 0003", "ИТ0003"),
        match_type="alias",
    )

    kept = drop_unmatched_blocks(
        [exact_alias, semantic_variant],
        "ИТ0003",
        match_groups=(group,),
    )

    assert kept == [exact_alias, semantic_variant]


def test_configured_infotype_rule_does_not_infer_unconfigured_inflected_source():
    exact_code = _block("PY-ES", "IT0003 - Payroll Status")
    russian_case = _block(
        "Общие сведения",
        "Транзакция массового изменения инфо-типа 0003.",
    )
    group = MatchGroup(
        term_id=None,
        canonical="IT0003",
        kind="sap_infotype",
        canonical_locale="und",
        original_name="IT0003",
        term_version=0,
        source_revision=0,
        spans=(MatchSpan(0, 6, "IT0003", "structural", "IT0003"),),
        matched_forms=("IT0003",),
        match_type="structural",
        system_rule="sap_infotype",
        structural_code="IT0003",
    )

    kept = drop_unmatched_blocks(
        [exact_code, russian_case],
        "IT0003",
        match_groups=(group,),
    )

    assert kept == [exact_code]


def test_exact_identifier_does_not_match_longer_identifier():
    group = MatchGroup(
        term_id=None, canonical="IT0003", kind="sap_infotype", canonical_locale="und",
        original_name="IT0003", term_version=0, source_revision=0,
        spans=(MatchSpan(0, 6, "IT0003", "structural", "IT0003"),),
        matched_forms=("IT0003",), match_type="structural",
    )
    assert matching_group_keys("IT0003", (group,)) == ("IT0003",)
    assert matching_group_keys("IT00037", (group,)) == ()
    assert matching_group_keys("XIT0003Y", (group,)) == ()
