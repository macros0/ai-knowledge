"""Task contracts and request-scoped budgets for an OpenAI-compatible local server."""
import time
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from typing import Literal

from app.services.llm_scheduler import LLMCancelled


class Relation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    type: Literal["depends_on", "part_of", "example_of", "duplicate_of", "reference_to", "continues"]


class GeneratedConcept(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    title: str
    type: Literal["concept", "procedure", "reference", "example", "note", "table"]
    tags: list[str]
    content: str
    source_quotes: list[str]
    relations: list[Relation]


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    concept_per_row: bool
    title_col: int
    description_cols: list[int]
    concept_type: Literal["concept", "procedure", "reference", "note"]
    extraction_mode: Literal["per_row", "whole"]


class TableClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    concept_per_row: bool
    title_col: int
    concept_type: Literal["concept", "procedure", "reference", "note"]
    extraction_mode: Literal["per_row", "whole"]


class SourceAssessmentItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_index: int
    label: Literal["relevant", "partial", "irrelevant", "uncertain"]
    evidence_quote: str | None


class SourceAssessmentJSON(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[SourceAssessmentItem]


class Development(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    dev_number: str | None
    dev_name: str | None
    module: str | None


ADAPTERS = {
    "source_assessment": TypeAdapter(SourceAssessmentJSON),
    "generation": TypeAdapter(list[GeneratedConcept]),
    "classification": TypeAdapter(Classification),
    "table_classification": TypeAdapter(TableClassificationResult),
    "development": TypeAdapter(Development),
    "translation": TypeAdapter(list[str]),
}
_request = ContextVar("llm_request", default={})


def request_state():
    return _request.get()


@contextmanager
def request_scope(**values):
    token = _request.set({**request_state(), **values})
    try:
        yield
    finally:
        _request.reset(token)


@contextmanager
def generation_budget(settings):
    if settings.llm_profile != "local_qwen" or request_state().get("deadline"):
        yield
    else:
        with request_scope(deadline=time.monotonic() + settings.llm_chunk_budget_seconds):
            yield


def remaining(default):
    state = request_state()
    cancel = state.get("cancel")
    if cancel is not None and cancel.is_set():
        raise LLMCancelled()
    deadline = state.get("deadline")
    if deadline is None:
        return default
    available = deadline - time.monotonic()
    if available <= 0:
        raise TimeoutError("LLM request budget exceeded")
    return min(default, available)


def token_limit(settings, task):
    return {
        "chat": settings.llm_chat_max_tokens,
        "classification": settings.llm_classification_max_tokens,
        "table_classification": settings.llm_classification_max_tokens,
        "development": settings.llm_classification_max_tokens,
        "translation": settings.llm_translation_max_tokens,
    }.get(task, settings.llm_max_tokens)


def completion_options(task=None, settings=None):
    if settings is None:
        from app.config import Settings
        settings = Settings(_env_file=None)
    options = {
        "top_p": settings.llm_local_top_p,
        "presence_penalty": settings.llm_local_presence_penalty,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": settings.llm_local_enable_thinking},
                       "top_k": settings.llm_local_top_k, "min_p": settings.llm_local_min_p,
                       "repeat_penalty": settings.llm_local_repeat_penalty,
                       "cache_prompt": settings.llm_local_cache_prompt},
    }
    if task in ADAPTERS:
        options["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": task, "strict": True, "schema": ADAPTERS[task].json_schema()},
        }
    return options


def validate_result(task, value):
    ADAPTERS[task].validate_python(value, strict=True)
    if task == "generation":
        ids = [item["id"] for item in value]
        if any(not item.strip() for item in ids) or len(ids) != len(set(ids)):
            raise ValueError("Concept ids must be nonempty and unique")
        known = set(ids)
        for item in value:
            if not item["title"].strip() or not item["content"].strip():
                raise ValueError("Concept title and content must be nonempty")
            # A dangling edge is not usable in the stored graph. Preserve the
            # factual concept rather than discard it because of an invalid edge.
            item["relations"] = [r for r in item["relations"]
                                 if r["id"] in known and r["id"] != item["id"]]
    return value


def validate_salvaged_generation(items):
    """Keep only complete, schema-valid concepts; never invent missing fields."""
    kept = []
    known = set()
    for item in items:
        try:
            concept = GeneratedConcept.model_validate(item, strict=True)
        except ValidationError:
            continue
        if (not concept.id.strip() or concept.id in known
                or not concept.title.strip() or not concept.content.strip()):
            continue
        kept.append(item)
        known.add(concept.id)
    return validate_result('generation', kept)
