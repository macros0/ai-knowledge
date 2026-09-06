"""Sparse-векторы (BM25) для лексического поиска по ключевым словам.

Способ: лёгкий токенайзер без внешних зависимостей — термин маппится на
фиксированное индексное пространство детерминированным хэшем, значение равно
log-шкалированной частоте термина в тексте (TF). IDF-веса на запросе применяет
сам Qdrant (SparseVectorParams(modifier=Modifier.IDF)) — это и есть
BM25-скоринг в Qdrant.

Ограничение: без стемминга словоформы русского языка считаются разными
терминами. Опциональный стеммер (nltk/pymorphy2) можно добавить позже.
"""
import hashlib
import math
import re
from typing import Collection

from qdrant_client.http import models as qm

TOKEN_RE = re.compile(r"[a-zа-яё0-9]+")
# Индексное пространство sparse-вектора. Увеличение снижает коллизии хэшей.
SPARSE_INDEX_DIM = 2**20
# Короткие токены — шум (союзы, частицы, однобуквенные).
MIN_TOKEN_LEN = 2
_STOPWORDS = {
    "и",
    "в",
    "во",
    "не",
    "на",
    "я",
    "он",
    "она",
    "оно",
    "мы",
    "вы",
    "они",
    "это",
    "что",
    "как",
    "так",
    "но",
    "или",
    "а",
    "же",
    "бы",
    "по",
    "от",
    "с",
    "со",
    "у",
    "до",
    "за",
    "об",
    "при",
    "из",
    "к",
    "для",
    "то",
    "чем",
    "если",
    "б",
    "да",
}


def tokenize(text: str, stopwords: Collection[str] | None = None) -> list[str]:
    """Разбивает текст на термины: lowercase, отсев коротких и стоп-слов.

    `stopwords` — опциональный набор стоп-слов запроса (динамический, из БД,
    services/stopwords.py). None → модульный замороженный набор `_STOPWORDS`
    (индексный путь): формула индекса `_sparse_text`/`to_sparse_vector` не должна
    зависеть от рантайм-правок — реиндекс при смене стоп-слов не требуется.
    """
    tokens = []
    sw = _STOPWORDS if stopwords is None else stopwords
    for token in TOKEN_RE.findall(text.lower()):
        if len(token) < MIN_TOKEN_LEN or token in sw:
            continue
        tokens.append(token)
    return tokens


def _term_index(term: str) -> int:
    digest = hashlib.md5(term.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "little") % SPARSE_INDEX_DIM


def to_sparse_vector(
    text: str, stopwords: Collection[str] | None = None
) -> qm.SparseVector:
    """TF-based sparse-вектор: indices — хэш термина, values — log1p(tf).

    Коллизии хэшей (разные термины → одинаковый индекс) агрегируются:
    частоты складываются ДО логарифмирования — вес индекса log1p(sum_tf).
    Qdrant отвергает sparse-вектор с повторяющимися индексами (422
    «indices: must be unique»), поэтому дедуп — обязательный инвариант
    (инцидент 03.09.2026: «обязат»≡«тестировании» валила индексацию).
    indices отсортированы — стабильный порядок для тестов/дампов/сравнения.

    `stopwords=None` — индексный путь (замороженный набор). Query-путь передаёт
    динамический набор (services/stopwords.py).
    """
    tokens = tokenize(text, stopwords=stopwords)
    if not tokens:
        return qm.SparseVector(indices=[], values=[])
    term_counts: dict[int, int] = {}
    for token in tokens:
        idx = _term_index(token)
        term_counts[idx] = term_counts.get(idx, 0) + 1
    indices = sorted(term_counts)
    return qm.SparseVector(
        indices=indices,
        values=[math.log1p(term_counts[idx]) for idx in indices],
    )