"""Тесты дедупликации (Этап 4.2): хеши, MinHash, LSH, поиск кандидатов."""
from app.services.deduplication import (
    banding_schemes,
    bucket_hash,
    content_hash,
    file_hash_exists,
    file_hash_in_trash,
    find_duplicates_for_document,
    index_document,
    jaccard,
    minhash_signature,
    normalize_text,
    sha256_bytes,
)
from app.services.registry import get_registry

TEXT_A = "Проверка связи в системе передачи данных о документообороте предприятия."
TEXT_B = "Совершенно другой документ про финансовую отчётность и бухгалтерский учёт компании."


class TestHashes:
    def test_sha256_bytes(self):
        assert sha256_bytes(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

    def test_content_hash_normalizes_whitespace(self):
        assert content_hash("Привет   мир") == content_hash("  привет\nмир ")

    def test_normalize_lowercases(self):
        assert normalize_text("  Аа  Бб ") == "аа бб"


class TestMinHash:
    def test_signature_length(self):
        assert len(minhash_signature(TEXT_A)) == 128

    def test_identical_jaccard_one(self):
        s1 = minhash_signature(TEXT_A)
        s2 = minhash_signature(TEXT_A)
        assert jaccard(s1, s2) == 1.0

    def test_different_jaccard_low(self):
        assert jaccard(minhash_signature(TEXT_A), minhash_signature(TEXT_B)) < 0.5

    def test_empty_text_signature_empty(self):
        assert minhash_signature("") == []
        assert minhash_signature("   ") == []

    def test_shingles_share_tokenizer_normalization(self):
        # Шинглы обязаны нормализоваться ровно как BM25-токены: иначе подписи
        # дедупликации перестают соответствовать поисковому индексу. Турецкая
        # «İ» — единственное расхождение, которое давал голый lower().
        assert minhash_signature("İstanbul raporu " * 20) == minhash_signature(
            "istanbul raporu " * 20
        )

    def test_bucket_hash_deterministic(self):
        sig = minhash_signature(TEXT_A)
        assert bucket_hash(sig, 0, 16) == bucket_hash(sig, 0, 16)

    def test_banding_schemes_k_consistency(self):
        for scheme in banding_schemes():
            assert scheme["bands"] * scheme["rows"] == 128

    def test_german_umlauts_shingled_consistently(self):
        # 08.09.2026: dedup-шинглы используют тот же алфавит, что и BM25-токенайзер
        # (вкл. ä/ö/ü/ß). Две версии немецкого документа — высокая близость,
        # несвязанный документ — почти нулевая (умлауты не ломают MinHash).
        a = (
            "Die Erfassung der Überstunden erfolgt über das Personalzeiterfassungssystem "
            "und wird monatlich mit der Lohnabrechnung vergütet. Zusätzlich werden "
            "Sonderzahlungen und Zuschläge für Nachtarbeit nach dem geltenden Tarifvertrag "
            "ausgewiesen. Auch die Verwaltung der Zeitkonten ist im System abgebildet."
        )
        copy = a + " Das Verfahren ist im Betriebshandbuch geregelt."
        other = "Maßnahmen zur Änderung der Öffnungszeiten im Winter sind mit dem Betriebsrat abzustimmen."
        assert jaccard(minhash_signature(a), minhash_signature(a)) == 1.0
        assert jaccard(minhash_signature(a), minhash_signature(copy)) > 0.8
        assert jaccard(minhash_signature(a), minhash_signature(other)) < 0.4


class TestFindDuplicates:
    def test_exact_content_hash_match(self):
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=[])
        reg.create("bbbbbbbbbbbbbbbb", "b.docx", "x", 10, tags=[])
        index_document("aaaaaaaaaaaaaaaa", TEXT_A)
        index_document("bbbbbbbbbbbbbbbb", TEXT_A)

        result = find_duplicates_for_document("aaaaaaaaaaaaaaaa")
        ids = [c["doc"]["id"] for c in result["level2"]]
        assert "bbbbbbbbbbbbbbbb" in ids
        assert result["level3"] == []

    def test_distinct_docs_no_candidates(self):
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=[])
        reg.create("bbbbbbbbbbbbbbbb", "b.docx", "x", 10, tags=[])
        index_document("aaaaaaaaaaaaaaaa", TEXT_A)
        index_document("bbbbbbbbbbbbbbbb", TEXT_B)

        result = find_duplicates_for_document("aaaaaaaaaaaaaaaa")
        assert result["level2"] == []
        assert result["level3"] == []

    def test_index_is_idempotent(self):
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=[])
        index_document("aaaaaaaaaaaaaaaa", TEXT_A)
        index_document("aaaaaaaaaaaaaaaa", TEXT_A)
        from app.db.session import session_scope
        from app.db.models import DocumentLshBucket

        with session_scope() as s:
            count = s.query(DocumentLshBucket).filter(DocumentLshBucket.doc_id == "aaaaaaaaaaaaaaaa").count()
        # strict 8 + loose 16 = 24 бакета (повторная индексация не плодит дубли).
        assert count == 24


class TestDuplicateBadges:
    def _twins(self, count=2):
        reg = get_registry()
        ids = [letter * 16 for letter in "abc"[:count]]
        for did in ids:
            reg.create(did, f"{did}.docx", "x", 10)
            index_document(did, TEXT_A)
        return reg, ids

    def test_index_marks_both_documents(self):
        reg, ids = self._twins()
        assert all(reg.get(did)["has_duplicates"] for did in ids)

    def test_delete_last_twin_clears_badge(self):
        reg, (a, b) = self._twins()
        # Reproduce persisted flags created by the old pipeline.
        for did in (a, b):
            reg.update(did, has_duplicates=True)
        reg.soft_delete(b, "editor")
        assert not reg.get(a)["has_duplicates"]
        assert not reg.get(b)["has_duplicates"]

    def test_delete_keeps_badges_while_another_twin_remains(self):
        reg, (a, b, c) = self._twins(3)
        for did in (a, b, c):
            reg.update(did, has_duplicates=True)
        reg.soft_delete(c, "editor")
        assert reg.get(a)["has_duplicates"]
        assert reg.get(b)["has_duplicates"]
        reg.soft_delete(b, "editor")
        assert not reg.get(a)["has_duplicates"]

    def test_restore_recomputes_both_badges(self):
        reg, (a, b) = self._twins()
        reg.soft_delete(b, "editor")
        for did in (a, b):
            reg.update(did, has_duplicates=False)
        reg.restore(b)
        assert reg.get(a)["has_duplicates"]
        assert reg.get(b)["has_duplicates"]

    def test_reindex_clears_former_twin_badges(self):
        reg, (a, b) = self._twins()
        for did in (a, b):
            reg.update(did, has_duplicates=True)
        index_document(b, TEXT_B)
        assert not reg.get(a)["has_duplicates"]
        assert not reg.get(b)["has_duplicates"]


class TestDuplicateSummarySerialization:
    def test_file_hash_exists_returns_json_serializable(self):
        """Регрессия: datetime в summary ломал JSONResponse 409 на дубле (500)."""
        import json

        from app.services.deduplication import file_hash_exists, set_file_hash
        from app.services.registry import get_registry

        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=[])
        set_file_hash("aaaaaaaaaaaaaaaa", "abc123def456")

        existing = file_hash_exists("abc123def456")
        assert existing is not None
        json.dumps(existing)  # не должен бросать TypeError (Object of type datetime)
        assert isinstance(existing["created_at"], str)


class TestTrashTwinPolicy:
    """Осознанное решение 2026-09-01 (SECURITY.md §5): близнец в корзине
    не блокирует загрузку (file_hash_exists — только активные), а кандидаты
    дедупликации не загрязняются документами из корзины."""

    def _twin_docs(self):
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10)
        reg.create("bbbbbbbbbbbbbbbb", "b.docx", "x", 10)
        get_registry().update("bbbbbbbbbbbbbbbb", file_hash="hash-1")
        return "hash-1"

    def test_file_hash_exists_ignores_trash(self):
        file_hash = self._twin_docs()
        get_registry().soft_delete("bbbbbbbbbbbbbbbb", "u1")
        assert file_hash_exists(file_hash) is None
        assert file_hash_in_trash(file_hash)["id"] == "bbbbbbbbbbbbbbbb"

        # Вернули из корзины — снова блокирует.
        get_registry().restore("bbbbbbbbbbbbbbbb")
        assert file_hash_exists(file_hash)["id"] == "bbbbbbbbbbbbbbbb"

    def test_file_hash_in_trash_none_for_active(self):
        self._twin_docs()
        assert file_hash_in_trash("hash-1") is None
        assert file_hash_exists("hash-1")["id"] == "bbbbbbbbbbbbbbbb"

    def test_find_duplicates_excludes_trash(self):
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10)
        reg.create("bbbbbbbbbbbbbbbb", "b.docx", "x", 10)
        reg.create("cccccccccccccccc", "c.docx", "x", 10)
        index_document("aaaaaaaaaaaaaaaa", TEXT_A)
        index_document("bbbbbbbbbbbbbbbb", TEXT_A)
        index_document("cccccccccccccccc", TEXT_A)
        reg.soft_delete("cccccccccccccccc", "u1")

        result = find_duplicates_for_document("aaaaaaaaaaaaaaaa")
        ids = [it["doc"]["id"] for it in result["level2"]]
        assert "bbbbbbbbbbbbbbbb" in ids
        assert "cccccccccccccccc" not in ids
