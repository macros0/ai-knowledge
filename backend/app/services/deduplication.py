"""Дедупликация документов (Этап 4.2).

Три уровня:
  1. Точное совпадение (file_hash — SHA-256 байтов файла) — блокирующая проверка
     в upload-эндпоинте до создания записи (409 code=duplicate, окно на фронте).
  2. Почти идентичные (content_hash — SHA-256 нормализованного текста, либо
     LSH-strict с Jaccard >= порога).
  3. Ревизии/похожие (LSH-loose, Jaccard в [loose, strict)).

Уровни 2/3 на момент загрузки не проверяются: подпись документа считается в
пайплайне после парсинга (index_document), флаг has_duplicates ставится по
наличию кандидатов. Всплывающего окна при загрузке похожего файла НЕТ — UI
показывает бейдж «Дубликат» на карточке документа и список группы через
GET /documents/{id}/duplicates (DuplicateModal). Документы в корзине в поиск
кандидатов не попадают.

MinHash: word n-граммы, k=128 хешей (mmh3, стабильный и детерминированный),
двойная banding (strict 8×16, loose 16×8) над одной подписью. Бакеты — таблица
document_lsh_buckets; поиск кандидатов по (variant, band_index, bucket_hash),
точный Jaccard — по подписям кандидатов.
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import mmh3
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Document, DocumentLshBucket
from app.db.session import session_scope
from app.services.sparse import TOKEN_RE

logger = logging.getLogger(__name__)


# --- Чистые функции (тестируются без БД) ---

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_text(markdown: str) -> str:
    return re.sub(r"\s+", " ", (markdown or "").lower()).strip()


def content_hash(markdown: str) -> str:
    return hashlib.sha256(normalize_text(markdown).encode("utf-8")).hexdigest()


def _shingles(markdown: str, n: int) -> list[str]:
    # Тот же алфавит, что у BM25-токенайзера (sparse.TOKEN_RE, включая немецкие
    # ä/ö/ü/ß с 08.09.2026). НЕ tokenize() со стоп-словами: стоп-фильтр менял бы
    # шинглы русских документов и сделал бы старые minhash-подписи несравнимыми.
    tokens = TOKEN_RE.findall((markdown or "").lower())
    if not tokens:
        return []
    if len(tokens) < n:
        return [" ".join(tokens)]
    return [" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def minhash_signature(markdown: str, k: int | None = None, shingle_n: int | None = None) -> list[int]:
    settings = get_settings()
    k = k or settings.dedup_minhash_k
    shingle_n = shingle_n or settings.dedup_shingle_n
    shingles = _shingles(markdown, shingle_n)
    if not shingles:
        return []
    sig: list[int] = []
    for seed in range(k):
        sig.append(min(mmh3.hash(s, seed) & 0xFFFFFFFF for s in shingles))
    return sig


def jaccard(sig_a: list[int], sig_b: list[int]) -> float:
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    matches = sum(1 for a, b in zip(sig_a, sig_b) if a == b)
    return matches / len(sig_a)


def banding_schemes() -> list[dict]:
    settings = get_settings()
    return [
        {"variant": "strict", "bands": settings.dedup_strict_bands, "rows": settings.dedup_strict_rows},
        {"variant": "loose", "bands": settings.dedup_loose_bands, "rows": settings.dedup_loose_rows},
    ]


def bucket_hash(sig: list[int], band_index: int, rows: int) -> str:
    band = sig[band_index * rows : (band_index + 1) * rows]
    return hashlib.sha256(",".join(str(x) for x in band).encode("utf-8")).hexdigest()


# --- Работа с БД ---

def _summarize(doc: Document) -> dict:
    # created_at — ISO-строка, а не datetime: этот dict попадает в JSONResponse
    # (409-ответ на дубль) и в FastAPI-ответы; datetime там не сериализуется
    # стандартным json.dumps.
    return {
        "id": doc.id,
        "filename": doc.filename,
        "size": doc.size,
        "status": doc.status,
        "uploaded_by": doc.uploaded_by,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "deleted_at": doc.deleted_at.isoformat() if doc.deleted_at else None,
    }


def file_hash_exists(file_hash: str) -> dict | None:
    """Level 1: АКТИВНЫЙ документ с тем же SHA-256 байтов файла уже существует.

    Близнец в корзине НЕ блокирует загрузку (осознанное решение 2026-09-01,
    см. SECURITY.md §5): пользователь не должен зависеть от невидимого ему
    состояния чужой корзины; конфликт версий решится при восстановлении
    (restore с дедуп-проверкой). Информационно близнец в корзине доступен
    отдельно — file_hash_in_trash.
    """
    with session_scope() as s:
        doc = s.execute(
            select(Document).where(
                Document.file_hash == file_hash, Document.deleted_at.is_(None)
            )
        ).scalars().first()
        return _summarize(doc) if doc else None


def file_hash_in_trash(file_hash: str) -> dict | None:
    """Близнец по SHA-256, лежащий в корзине (информационно, не блокирует)."""
    with session_scope() as s:
        doc = s.execute(
            select(Document).where(
                Document.file_hash == file_hash, Document.deleted_at.isnot(None)
            )
        ).scalars().first()
        return _summarize(doc) if doc else None


def set_file_hash(doc_id: str, file_hash: str) -> None:
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        if doc is not None:
            doc.file_hash = file_hash


def index_document(doc_id: str, markdown: str) -> None:
    """Вычисляет content_hash + MinHash-подпись и заполняет LSH-бакеты документа."""
    settings = get_settings()
    ch = content_hash(markdown)
    sig = minhash_signature(markdown)
    if len(sig) < settings.dedup_minhash_k:
        sig = None
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            return
        doc.content_hash = ch
        doc.minhash = sig if sig else None
        s.query(DocumentLshBucket).filter(DocumentLshBucket.doc_id == doc_id).delete(
            synchronize_session=False
        )
        if sig:
            for scheme in banding_schemes():
                variant = scheme["variant"]
                rows = scheme["rows"]
                for band_index in range(scheme["bands"]):
                    s.add(
                        DocumentLshBucket(
                            doc_id=doc_id,
                            variant=variant,
                            band_index=band_index,
                            bucket_hash=bucket_hash(sig, band_index, rows),
                        )
                    )


def _load_signature(doc_id: str) -> list[int] | None:
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        if doc is None or not doc.minhash:
            return None
        return [int(x) for x in doc.minhash]


def _get_summary(doc_id: str) -> dict | None:
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        return _summarize(doc) if doc else None


def find_duplicates_for_document(doc_id: str) -> dict:
    """Кандидаты-дубликаты для уже проиндексированного документа.

    Использует сохранённую MinHash-подпись и content_hash (а не пересчитывает
    из исходника). Возвращает {"level2": [...], "level3": [...]}:
      level2 — почти идентичные (content_hash точный или LSH-strict >= strict-порога);
      level3 — ревизии/похожие (LSH-loose, Jaccard в [loose, strict)).
    Каждый элемент: {"doc": {...}, "jaccard": float}.
    """
    settings = get_settings()
    result: dict[str, list[dict]] = {"level2": [], "level3": []}
    if not settings.dedup_enabled:
        return result

    with session_scope() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            return result
        ch = doc.content_hash
        sig = [int(x) for x in doc.minhash] if doc.minhash else None
    if not sig:
        return result

    votes: dict[str, dict[str, int]] = {}
    seen: set[str] = set()
    with session_scope() as s:
        # Документы в корзине не загрязняют список дубликатов и флаг
        # has_duplicates: их близнец в корзине — невидимое для пользователя
        # состояние (осознанное решение, см. file_hash_exists).
        exact_docs = s.execute(
            select(Document).where(
                Document.content_hash == ch,
                Document.id != doc_id,
                Document.deleted_at.is_(None),
            )
        ).scalars().all()
        for d in exact_docs:
            result["level2"].append({"doc": _summarize(d), "jaccard": 1.0})
            seen.add(d.id)

        for scheme in banding_schemes():
            variant = scheme["variant"]
            rows = scheme["rows"]
            for band_index in range(scheme["bands"]):
                bh = bucket_hash(sig, band_index, rows)
                others = s.execute(
                    select(DocumentLshBucket.doc_id).where(
                        DocumentLshBucket.variant == variant,
                        DocumentLshBucket.band_index == band_index,
                        DocumentLshBucket.bucket_hash == bh,
                    )
                ).scalars().all()
                for other in others:
                    if other == doc_id:
                        continue
                    votes.setdefault(other, {"strict": 0, "loose": 0})[variant] += 1

    for other_id, _ in votes.items():
        if other_id in seen:
            continue
        other_sig = _load_signature(other_id)
        if not other_sig:
            continue
        jac = jaccard(sig, other_sig)
        summary = _get_summary(other_id)
        if summary is None or summary.get("deleted_at"):
            continue  # отсутствует в БД (гонка purge) или в корзине
        if jac >= settings.dedup_jaccard_strict_threshold:
            result["level2"].append({"doc": summary, "jaccard": round(jac, 4)})
        elif jac >= settings.dedup_jaccard_loose_threshold:
            result["level3"].append({"doc": summary, "jaccard": round(jac, 4)})

    result["level2"] = _dedupe_by_id(result["level2"])
    result["level3"] = _dedupe_by_id(result["level3"])
    return result


def _dedupe_by_id(items: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for item in items:
        out[item["doc"]["id"]] = item
    return list(out.values())


def find_active_duplicates_for_document(doc_id: str) -> dict:
    """Кандидаты-дубликаты ТОЛЬКО среди активных (не удалённых) документов.

    Используется при восстановлении из корзины (Этап 4a.2): если за время
    нахождения документа в корзине кто-то загрузил похожий активный документ,
    восстановление показывает конфликт. Документы в самой корзине исключаются.
    """
    result = find_duplicates_for_document(doc_id)

    def _active(items: list[dict]) -> list[dict]:
        return [it for it in items if not (it.get("doc") or {}).get("deleted_at")]

    return {"level2": _active(result["level2"]), "level3": _active(result["level3"])}
