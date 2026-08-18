"""Юнит-тесты sparse-векторов (BM25): токенизация, структура вектора, детерминизм."""
from app.services.sparse import SPARSE_INDEX_DIM, _term_index, to_sparse_vector, tokenize


class TestTokenize:
    def test_lowercases_and_splits(self):
        assert tokenize("Привет мир") == ["привет", "мир"]
        assert tokenize("Hello World") == ["hello", "world"]

    def test_filters_short_tokens(self):
        assert "и" not in tokenize("и a на")
        assert "a" not in tokenize("и a на")

    def test_filters_stopwords(self):
        tokens = tokenize("и в не на что если")
        assert all(t not in tokens for t in ("и", "в", "не", "на", "что", "если"))

    def test_mixed_russian_english(self):
        tokens = tokenize("API сервис для обработки данных")
        assert "api" in tokens
        assert "сервис" in tokens
        assert "для" not in tokens  # stopword
        assert "обработки" in tokens
        assert "данных" in tokens

    def test_empty_text(self):
        assert tokenize("") == []
        assert tokenize("   ") == []
        assert tokenize("и в не") == []

    def test_numbers(self):
        tokens = tokenize("v2.28 Пр_10 проактив")
        # цифры — часть токена
        assert "v2" in tokens
        assert "28" in tokens or any("28" in t for t in tokens)
        assert "пр" in tokens or any("10" in t for t in tokens)


class TestTermIndex:
    def test_index_in_range(self):
        for term in ("привет", "мир", "сервис", "документооборот"):
            idx = _term_index(term)
            assert 0 <= idx < SPARSE_INDEX_DIM, f"{term}: {idx} вне [0, {SPARSE_INDEX_DIM})"

    def test_deterministic(self):
        assert _term_index("привет") == _term_index("привет")
        assert _term_index("hello") == _term_index("hello")

    def test_different_terms_different_indices_unlikely(self):
        assert _term_index("один") != _term_index("два")  # почти всегда True


class TestToSparseVector:
    def test_empty_text(self):
        v = to_sparse_vector("")
        assert v.indices == []
        assert v.values == []
        v = to_sparse_vector("и в не")
        assert v.indices == []
        assert v.values == []

    def test_nonempty_structure(self):
        v = to_sparse_vector("привет мир привет")
        assert len(v.indices) == 2  # "привет", "мир"
        assert len(v.values) == 2
        assert all(0 <= idx < SPARSE_INDEX_DIM for idx in v.indices)
        assert all(val > 0 for val in v.values)

    def test_tf_reflected_in_values(self):
        v = to_sparse_vector("привет привет мир")
        idx_privet = _term_index("привет")
        idx_mir = _term_index("мир")
        values = dict(zip(v.indices, v.values))
        assert values[idx_privet] > values[idx_mir], "частый термин должен иметь больший value"

    def test_deterministic(self):
        text = "API сервис для обработки данных"
        v1 = to_sparse_vector(text)
        v2 = to_sparse_vector(text)
        assert v1.indices == v2.indices
        assert v1.values == v2.values

    def test_shared_tokens_produce_shared_indices(self):
        v1 = to_sparse_vector("сервис API")
        v2 = to_sparse_vector("API сервис")
        shared = set(v1.indices) & set(v2.indices)
        assert len(shared) == 2


class TestSearchModeValidation:
    def test_valid_modes(self):
        from app.models.schemas import SearchRequest, ChatRequest

        for mode in ("dense", "bm25", "hybrid"):
            r = SearchRequest(query="test", mode=mode)
            assert r.mode == mode
            r = ChatRequest(query="test", mode=mode)
            assert r.mode == mode

    def test_invalid_mode_fails(self):
        from pydantic import ValidationError
        from app.models.schemas import SearchRequest

        import pytest

        with pytest.raises(ValidationError):
            SearchRequest(query="test", mode="invalid")


class TestSettingsSearchModes:
    def test_default_mode_in_settings(self, tmp_path):
        from app.config import Settings

        s = Settings(data_dir=tmp_path, search_mode_default="hybrid")
        assert s.search_mode_default == "hybrid"

    def test_search_modes_export(self):
        from app.services.vector_store import SEARCH_MODES

        assert "dense" in SEARCH_MODES
        assert "bm25" in SEARCH_MODES
        assert "hybrid" in SEARCH_MODES