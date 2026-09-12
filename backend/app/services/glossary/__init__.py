"""Domain glossary services."""

from app.services.glossary.expansion import prepare_query
from app.services.glossary.registry import GlossaryRegistry, get_glossary_registry
from app.services.glossary.types import AppliedTerm, MatchGroup, QueryPlan

__all__ = [
    "AppliedTerm",
    "GlossaryRegistry",
    "MatchGroup",
    "QueryPlan",
    "get_glossary_registry",
    "prepare_query",
]
