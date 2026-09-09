"""Юнит-тесты sparse-векторов (BM25): токенизация, структура вектора, детерминизм."""
import math

import pytest

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

    def test_german_umlauts_tokenized(self):
        # 08.09.2026: ä/ö/ü/ß добавлены в алфавит (немецкий корпус). До фикса
        # «Überstunden für» давало токены-обрубки или вообще ничего.
        tokens = tokenize("Überstunden für die Münchener")
        assert "überstunden" in tokens
        assert "für" in tokens
        assert "münchener" in tokens
        assert "die" in tokens

    def test_german_sharp_s(self):
        tokens = tokenize("Straße Maßnahme Größe")
        assert "straße" in tokens
        assert "maßnahme" in tokens
        assert "größe" in tokens

    def test_german_ascii_words_unchanged(self):
        tokens = tokenize("ist das nicht oder und")
        assert tokens == ["ist", "das", "nicht", "oder", "und"]

    def test_german_uppercase_initial_lowered(self):
        # Заглавные буквы немецких существительных нормализуются lower()'ом
        # до findall (Ä→ä, Ü→ü, Ö→ö).
        tokens = tokenize("Änderung Öffnung Übertragung")
        assert "änderung" in tokens
        assert "öffnung" in tokens
        assert "übertragung" in tokens

    def test_ru_en_de_mixed(self):
        tokens = tokenize("привет hello Überstunden 123")
        assert "привет" in tokens
        assert "hello" in tokens
        assert "überstunden" in tokens

    def test_french_accents_tokenized(self):
        # 08.09.2026 (2-я смена формулы): европейская латиница — французские
        # акценты становятся ЦЕЛЫМИ токенами, а не обрубками.
        tokens = tokenize("après le congé, élève à Paris")
        assert "après" in tokens
        assert "congé" in tokens
        assert "élève" in tokens
        assert "paris" in tokens

    def test_spanish_accents_tokenized(self):
        tokens = tokenize("español corazón fácil niño")
        assert "español" in tokens
        assert "corazón" in tokens
        assert "niño" in tokens

    def test_turkish_dotted_capital_i(self):
        # «İ».lower() == "i" + U+0307: combining-точки в алфавите нет, поэтому
        # без normalize_for_tokens токен рвался на «i» (отсеивался по длине) и
        # «stanbul», который не совпал бы ни с «istanbul», ни с «ISTANBUL».
        assert tokenize("İstanbul raporu") == ["istanbul", "raporu"]
        assert tokenize("İSTANBUL") == tokenize("istanbul") == ["istanbul"]

    def test_apostrophe_splits_into_fragments(self):
        # Апостроф — разделитель: слитная французская форма НЕ токенизируется
        # целиком (поэтому как стоп-слово хранить её бесполезно).
        tokens = tokenize("aujourd'hui une belle journée")
        assert "aujourd" in tokens
        assert "hui" in tokens
        assert "aujourd'hui" not in tokens


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

    def test_indices_always_unique_and_sorted(self):
        # Инвариант Qdrant: sparse-вектор с повторяющимися индексами отвергается
        # 422 «indices: must be unique» (инцидент 03.09.2026).
        for text in (
            "обязателен для тестирования при обязательном тестировании",
            "завершения кроме завершения кроме",
            "ru ильиных ильиных ru",
            "смешанный текст 12410 zinfoprovayderalnomer",
        ):
            v = to_sparse_vector(text)
            assert len(v.indices) == len(set(v.indices)), f"дубли индексов: {text!r}"
            assert list(v.indices) == sorted(v.indices), f"индексы не отсортированы: {text!r}"

    def test_colliding_terms_aggregate_tf_before_log(self):
        # Реальная коллизия md5-хэша из инцидента 03.09.2026: «обязат» и
        # «тестировании» дают одинаковый индекс — до фикса такой вектор
        # валил upsert 422 «must be unique».
        t1, t2 = "обязат", "тестировании"
        assert _term_index(t1) == _term_index(t2), "ожидается реальная коллизия хэшей"
        v = to_sparse_vector(f"{t1} {t2} {t1}")
        assert len(v.indices) == len(set(v.indices))
        values = dict(zip(v.indices, v.values))
        # tf складываются ДО логарифма: tf(t1)=2 + tf(t2)=1 → log1p(3)
        assert values[_term_index(t1)] == pytest.approx(math.log1p(3))



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