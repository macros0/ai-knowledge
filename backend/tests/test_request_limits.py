import pytest
from pydantic import ValidationError

from app.models.schemas import ChatRequest, SearchRequest


@pytest.mark.parametrize("schema", [SearchRequest, ChatRequest])
def test_query_is_trimmed(schema):
    request = schema(query="  табельный номер  ")
    assert request.query == "табельный номер"


@pytest.mark.parametrize("schema", [SearchRequest, ChatRequest])
def test_blank_query_is_rejected(schema):
    with pytest.raises(ValidationError):
        schema(query=" \t\n ")


@pytest.mark.parametrize("schema", [SearchRequest, ChatRequest])
def test_query_length_is_bounded(schema):
    with pytest.raises(ValidationError):
        schema(query="x" * 8193)


def test_filter_values_are_normalized_and_deduplicated():
    request = SearchRequest(
        query="test",
        tags=[" review ", "review", "important"],
        source_locales=[" ru ", "ru"],
    )
    assert request.tags == ["review", "important"]
    assert request.source_locales == ["ru"]


def test_filter_count_is_bounded():
    with pytest.raises(ValidationError):
        SearchRequest(query="test", tags=[str(i) for i in range(51)])
