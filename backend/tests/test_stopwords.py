"""Тесты сервиса стоп-слов (Этап 7, фаза A): сид, объединение активных locales,
кэш/инвалидация, query-путь sparse-токенизации."""
import pytest

from app.services.locale_service import import_stopwords, update_locale
from app.services.sparse import tokenize
from app.services.stopwords import (
    KIND_BM25,
    KIND_MARKER,
    LOCALE_STATUS_DISABLED,
    ensure_seeded,
    get_stopwords,
    invalidate,
)


class _User:
    user_id = "u-admin"
    username = "demo.admin"


@pytest.fixture(autouse=True)
def _seed():
    ensure_seeded()
    yield
    invalidate()


class TestSeeding:
    def test_ensure_seeded_idempotent(self):
        # Фикстура _seed уже засеяла — повторный вызов должен быть no-op.
        assert ensure_seeded() == {"locales": 0, "stopwords": 0}

    def test_first_seed_creates_locales(self):
        # На пустой БД (после очистки) ensure_seeded создаёт ru+en и их стоп-слова.
        from app.db.models import Locale, Stopword
        from app.db.session import session_scope

        with session_scope() as s:
            s.query(Stopword).delete()
            s.query(Locale).delete()
        result = ensure_seeded()
        assert result["locales"] == 2  # ru, en
        assert result["stopwords"] > 0

    def test_default_locales_active(self):
        from app.services.locale_service import active_locale_codes

        codes = active_locale_codes()
        assert "ru" in codes
        assert "en" in codes

    def test_per_pk_seed_not_blanket(self):
        # Ручной INSERT до перезапуска не должен блокировать досев остальных строк:
        # удалим одну строку en и убедимся, что ensure_seeded её восстанавливает,
        # не трогая уже существующие (не «таблица пуста → заново всё»).
        from app.db.models import Stopword
        from app.db.session import session_scope

        with session_scope() as s:
            s.query(Stopword).filter(
                Stopword.locale == "en", Stopword.word == "the", Stopword.kind == KIND_BM25
            ).delete()
        result = ensure_seeded()
        assert result["stopwords"] == 1  # вернулся только "the"


class TestGetStopwords:
    def test_bm25_union_of_active_locales(self):
        words = get_stopwords(KIND_BM25)
        assert "и" in words  # ru
        assert "the" in words  # en

    def test_marker_has_ru_interrogatives(self):
        words = get_stopwords(KIND_MARKER)
        assert "какие" in words

    def test_invalid_kind_raises(self):
        with pytest.raises(ValueError):
            get_stopwords("bogus")

    def test_disabled_locale_excluded(self):
        update_locale("en", status=LOCALE_STATUS_DISABLED)
        words = get_stopwords(KIND_BM25)
        assert "the" not in words
        assert "и" in words  # ru остаётся активной


class TestQueryPath:
    def test_frozen_default_keeps_en_words(self):
        # Индексный путь (None) не знает EN-стоп-слов — формула заморожена.
        assert "the" in tokenize("the certificate")

    def test_dynamic_query_path_filters_en(self):
        tokens = tokenize("the certificate of disability", stopwords=get_stopwords(KIND_BM25))
        assert "the" not in tokens
        assert "of" not in tokens
        assert "certificate" in tokens
        assert "disability" in tokens


class TestInvalidation:
    def test_import_invalidates_synchronously(self):
        before = get_stopwords(KIND_BM25)
        assert "zzztestword" not in before
        result = import_stopwords(
            "ru", ["zzztestword"], KIND_BM25, "merge", confirm=True, user=_User()
        )
        assert result["applied"] is True
        # Синхронно относительно ответа: следующий get_stopwords видит новое слово
        # без ожидания TTL (негативный кэш-тест из приёмки фазы A).
        assert "zzztestword" in get_stopwords(KIND_BM25)

    def test_delete_invalidates(self):
        from app.services.locale_service import delete_stopword

        import_stopwords("ru", ["yyytest"], KIND_BM25, "merge", confirm=True, user=_User())
        assert "yyytest" in get_stopwords(KIND_BM25)
        delete_stopword("ru", "yyytest", KIND_BM25, user=_User())
        assert "yyytest" not in get_stopwords(KIND_BM25)
