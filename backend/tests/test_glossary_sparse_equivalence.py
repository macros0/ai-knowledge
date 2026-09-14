from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from app.services.glossary.query_sparse import build_query_sparse
from app.services.glossary.types import MatchGroup, QueryPlan
from app.services.sparse import to_sparse_vector


@dataclass(frozen=True)
class _GroupWithMetadata(MatchGroup):
    # The sparse builder must ignore registry metadata and consume only admitted texts.
    resolved_forms: tuple = ()


def _plan(source_kind: str, additions: tuple[str, ...] = ("it0003",), query: str = "other") -> QueryPlan:
    form = SimpleNamespace(
        text="it0003",
        normalized="it0003",
        identity_key="infotype:0003",
        boundary_mode="identifier",
        sources=(SimpleNamespace(kind=source_kind),),
        can_trigger=True,
        can_search=True,
    )
    group = _GroupWithMetadata(
        term_id=None,
        canonical="IT0003",
        kind="sap_infotype",
        canonical_locale="und",
        original_name="IT0003",
        term_version=0,
        source_revision=0,
        spans=(),
        matched_forms=("it0003",),
        match_type="alias",
        resolved_forms=(form,),
    )
    return QueryPlan(
        original_query=query,
        dense_query=query,
        added_sparse_texts=additions,
        match_groups=(group,),
        applied_terms=(),
        status="applied",
    )


def _vector(plan: QueryPlan, settings: object):
    return build_query_sparse(plan, stopwords=frozenset(), settings=settings)


@pytest.mark.parametrize("coefficient", [0.35, 1.0])
@pytest.mark.parametrize(
    "kind", ["sap_infotype", "sap_transaction", "sap_program", "sap_table", "sap_object", "business_term", "abbreviation"]
)
def test_source_and_term_kind_do_not_change_weight(coefficient: float, kind: str):
    settings = SimpleNamespace(glossary_sparse_expansion_weight=coefficient)
    rule = _plan("rule_alias")
    explicit = replace(_plan("explicit_alias"), match_groups=(replace(_plan("explicit_alias").match_groups[0], kind=kind),))
    name = _plan("name")

    assert _vector(rule, settings) == _vector(explicit, settings) == _vector(name, settings)


def test_order_of_shared_tokens_does_not_change_vector():
    settings = SimpleNamespace(glossary_sparse_expansion_weight=0.35)
    first = _plan("rule_alias", ("status it0003", "it0003"))
    second = replace(first, added_sparse_texts=tuple(reversed(first.added_sparse_texts)))

    assert _vector(first, settings) == _vector(second, settings)


def test_default_weight_is_full_for_explicit_alias():
    actual = _vector(_plan("explicit_alias"), SimpleNamespace())

    assert actual == to_sparse_vector("other it0003", stopwords=frozenset())


def test_settings_default_weight_is_full_when_environment_is_unset(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("GLOSSARY_SPARSE_EXPANSION_WEIGHT", raising=False)
    assert Settings(_env_file=None).glossary_sparse_expansion_weight == 1.0


def test_duplicates_do_not_increase_added_tf():
    settings = SimpleNamespace(glossary_sparse_expansion_weight=1.0)
    once = _plan("explicit_alias", ("status it0003",))
    repeated = replace(once, added_sparse_texts=("status it0003", "it0003", "status"))

    assert _vector(once, settings) == _vector(repeated, settings)


def test_original_weights_survive_repeated_additions():
    settings = SimpleNamespace(glossary_sparse_expansion_weight=1.0)
    plan = _plan("explicit_alias", ("base base",), query="base base")

    assert _vector(plan, settings) == to_sparse_vector("base base", stopwords=frozenset())
