"""Тесты дедупликации (Этап 4.2): хеши, MinHash, LSH, поиск кандидатов."""
from app.services.deduplication import (
    banding_schemes,
    bucket_hash,
    content_hash,
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

    def test_bucket_hash_deterministic(self):
        sig = minhash_signature(TEXT_A)
        assert bucket_hash(sig, 0, 16) == bucket_hash(sig, 0, 16)

    def test_banding_schemes_k_consistency(self):
        for scheme in banding_schemes():
            assert scheme["bands"] * scheme["rows"] == 128


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
