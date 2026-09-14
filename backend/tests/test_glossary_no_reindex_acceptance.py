"""Query-time CRUD against a real isolated LocalQdrant index prepared once.

This proves local index immutability and production planning/filter/hydration
behavior. It is not remote Qdrant or PostgreSQL runtime acceptance.
"""
import pytest

from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.rule_registry import GlossaryRuleRegistry
from test_scripts.benchmark_glossary_exact import Corpus, forbid_index_writes, measure


@pytest.fixture
def fixed_corpus():
    late = "Neutral historical material. " * 40
    corpus = Corpus.build({
        "transaction": late + "PA30 is the supported transaction.",
        "near-transaction": late + "PA3000 is a different transaction.",
        "old-alias": late + "MaintainPersonnel is the previous name.",
        "new-alias": late + "ManagePersonnel is the current name.",
        "infotype": late + "IT0003 stores payroll control.",
        "near-infotype": late + "IT00037 is a different code.",
        "ru-prefix": late + "ИТ 0003 stores the same object.",
        "de-prefix": late + "Infotyp 0003 stores the same object.",
        "other-number": late + "IT0004 belongs to another number.",
    })
    original = corpus.fingerprint()
    try:
        with forbid_index_writes() as spies:
            yield corpus, spies
            assert corpus.fingerprint() == original, "Stored vectors or payload changed"
    finally:
        corpus.client.close()


def found(corpus, query, *, dense=True):
    plan, hits, blocks, excerpts = corpus.search(query, dense=dense)
    assert plan.status in {"applied", "limited"}
    assert plan.strict_groups
    for hit in hits:
        assert len(hit.payload["content"]) > 300
    assert len(excerpts) == len(blocks)
    assert all(len(excerpt) <= 300 for excerpt in excerpts)
    return {hit.payload["doc_id"] for hit in hits}, excerpts, plan


def test_term_create_rename_disable_and_reenable_reuse_prepared_index(fixed_corpus):
    corpus, spies = fixed_corpus
    assert corpus.search("PA30")[0].status == "no_match"
    registry = GlossaryRegistry()
    term = registry.create(None, "sap_transaction", "PA30")
    assert registry.get(term["id"])["original_name"] == "PA30"
    ids, excerpts, initial_plan = found(corpus, "PA30")
    assert ids == {"transaction"}  # Dense candidates include PA3000 too.
    assert all("PA30 is" in excerpt and "PA3000" not in excerpt for excerpt in excerpts)

    term = registry.update(term["id"], term["version"], original_name="PA3000")
    ids, _, updated_plan = found(corpus, "PA3000")
    assert ids == {"near-transaction"}
    assert updated_plan.glossary_revision > initial_plan.glossary_revision
    assert corpus.search("PA30")[0].status == "no_match"
    term = registry.update(term["id"], term["version"], enabled=False)
    assert corpus.search("PA3000")[0].status == "no_match"
    term = registry.update(term["id"], term["version"], enabled=True)
    assert found(corpus, "PA3000")[0] == {"near-transaction"}
    assert all(spy.call_count == 0 for spy in spies.values())


def test_alias_create_update_delete_changes_real_sparse_retrieval_without_writes(fixed_corpus):
    corpus, _ = fixed_corpus
    registry = GlossaryRegistry()
    term = registry.create(None, "sap_transaction", "PA30")
    assert found(corpus, "PA30", dense=False)[0] == {"transaction"}
    term = registry.add_alias(
        term["id"], term["version"], "MaintainPersonnel", auto_expand=True, search_enabled=True,
    )
    alias_id = next(alias["id"] for alias in term["aliases"] if alias["alias"] == "MaintainPersonnel")
    assert found(corpus, "PA30", dense=False)[0] == {"transaction", "old-alias"}
    assert found(corpus, "MaintainPersonnel", dense=False)[0] == {"transaction", "old-alias"}

    term = registry.update_alias(
        term["id"], term["version"], alias_id, alias="ManagePersonnel",
    )
    assert found(corpus, "PA30", dense=False)[0] == {"transaction", "new-alias"}
    assert corpus.search("MaintainPersonnel", dense=False)[0].status == "no_match"
    term = registry.delete_alias(term["id"], term["version"], alias_id)
    assert term["aliases"] == []
    assert found(corpus, "PA30", dense=False)[0] == {"transaction"}


def test_rule_prefix_range_disable_delete_changes_fulltext_forms_without_reindex(fixed_corpus):
    corpus, _ = fixed_corpus
    terms = GlossaryRegistry()
    terms.create(None, "sap_infotype", "Payroll control", infotype_number="0003")
    rules = GlossaryRuleRegistry()
    assert found(corpus, "Payroll control")[0] == {"infotype"}
    # The descriptive name occurs in the IT0003 record. Other prefix records do not.
    rule = rules.create(name="Personnel", number_from=0, number_to=3, prefixes=["IT"])
    assert found(corpus, "IT0003")[0] == {"infotype"}
    rule = rules.update(rule["id"], rule["version"], prefixes=["IT", "ИТ "])
    assert found(corpus, "IT0003")[0] == {"infotype", "ru-prefix"}
    assert found(corpus, "ИТ 0003", dense=False)[0] == {"infotype", "ru-prefix"}
    rule = rules.update(rule["id"], rule["version"], prefixes=["IT", "Infotyp "])
    assert found(corpus, "IT0003")[0] == {"infotype", "de-prefix"}
    assert corpus.search("ИТ 0003")[0].status == "no_match"
    assert found(corpus, "Infotyp 0003")[0] == {"infotype", "de-prefix"}

    rule = rules.update(rule["id"], rule["version"], number_to=4)
    assert found(corpus, "IT0004")[0] == {"other-number"}  # virtual rule-only identity
    rule = rules.update(rule["id"], rule["version"], number_from=4)
    assert corpus.search("IT0003")[0].status == "no_match"
    assert found(corpus, "Payroll control")[0] == {"infotype"}
    assert found(corpus, "IT0004")[0] == {"other-number"}
    rule = rules.update(rule["id"], rule["version"], enabled=False)
    assert corpus.search("IT0004")[0].status == "no_match"
    rule = rules.update(rule["id"], rule["version"], enabled=True)
    assert found(corpus, "IT0004")[0] == {"other-number"}
    rules.delete(rule["id"], rule["version"])
    assert rules.list() == []
    assert corpus.search("IT0004")[0].status == "no_match"
    assert found(corpus, "Payroll control")[0] == {"infotype"}


def test_metrics_count_fulltext_before_exact_filter_and_exclude_fixture_indexing(fixed_corpus):
    corpus, _ = fixed_corpus
    GlossaryRegistry().create(None, "sap_transaction", "PA30")
    corpus.search("PA30")  # warm the revision snapshot outside timed operations
    baseline = measure(corpus, "PA30", False)
    after = measure(corpus, "PA30", True)
    assert baseline["sql_selects"] == 3  # visibility, concepts, chunks
    assert after["sql_selects"] == 4  # same batched reads plus glossary revision
    assert baseline["hydration_bytes"] == sum(
        2 * len(content[:300].encode("utf-8")) for content in corpus.records.values()
    )
    assert after["hydration_bytes"] == sum(
        2 * len(content.encode("utf-8")) for content in corpus.records.values()
    )
    assert after["result_blocks"] < baseline["result_blocks"]
    assert after["strict_groups"] == 1
    assert after["total_ms"] > 0
