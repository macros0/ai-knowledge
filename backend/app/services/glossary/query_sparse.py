"""Query-only sparse-vector expansion for glossary retrieval."""
from __future__ import annotations

from typing import Any, Collection

from qdrant_client.http import models as qm

from app.config import get_settings
from app.services.sparse import to_sparse_vector, tokenize
from app.services.glossary.types import QueryPlan


def build_query_sparse(
    plan: QueryPlan,
    *,
    stopwords: Collection[str],
    settings: Any | None = None,
) -> qm.SparseVector:
    """Build the BM25 query vector without changing the index-side formula.

    The original query is built by the existing tokenizer and keeps its exact
    weights. Glossary forms contribute each token once, scaled by the
    query-only expansion weight; an index already present in the original
    vector is deliberately left untouched.
    """
    original = to_sparse_vector(plan.original_query, stopwords=stopwords)
    if not plan.added_sparse_texts:
        return original

    # All forms admitted by the immutable query plan are equivalent search
    # forms.  Their coefficient must not depend on whether the form came from
    # a rule, an explicit alias, or the source name.
    source = settings if settings is not None else get_settings()
    weight = float(getattr(source, "glossary_sparse_expansion_weight", 1.0))
    added_tokens = sorted({
        token
        for text in plan.added_sparse_texts
        for token in tokenize(text, stopwords=stopwords)
    })
    if not added_tokens:
        return original

    additions = to_sparse_vector(" ".join(added_tokens), stopwords=stopwords)
    original_values = dict(zip(original.indices, original.values))
    combined = dict(original_values)
    for index, value in zip(additions.indices, additions.values):
        if index in original_values:
            continue
        combined[index] = value * weight

    indices = sorted(combined)
    return qm.SparseVector(indices=indices, values=[combined[index] for index in indices])
