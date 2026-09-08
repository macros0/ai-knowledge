"""Sparse-векторы (BM25) для лексического поиска по ключевым словам.

Способ: лёгкий токенайзер без внешних зависимостей — термин маппится на
фиксированное индексное пространство детерминированным хэшем, значение равно
log-шкалированной частоте термина в тексте (TF). IDF-веса на запросе применяет
сам Qdrant (SparseVectorParams(modifier=Modifier.IDF)) — это и есть
BM25-скоринг в Qdrant.

Ограничение: без стемминга словоформы русского языка считаются разными
терминами. Опциональный стеммер (nltk/pymorphy2) можно добавить позже.

Алфавит токенов — ASCII-латиница, кириллица, цифры и `TOKEN_EXTRA_LETTERS`
(европейская латиница: de/fr/es/pt/it/скандинавские/pl/cz/hu/ro/tr с 08.09.2026).
Апостроф и дефис остаются разделителями (не буквами) — слова со слитными формами
не токенизируются целиком. Расширение набора = смена индексной формулы +
rebuild_sparse.
"""
import hashlib
import math
import re
from typing import Collection

from qdrant_client.http import models as qm

# Строчные буквы европейских языков вне базового набора (латиница без диакритик +
# кириллица). 08.09.2026: немецкие ä/ö/ü/ß; затем расширено до европейской латиницы
# (французский/испанский/португальский/итальянский/скандинавские/польский/чешский/
# венгерский/румынский/турецкий) одним набором:
#   ß          — U+00DF (немецкий eszett, вне диапазона à-ö);
#   à-ö        — U+00E0–U+00F6 латиница-1 (без ÷ U+00F7);
#   ø-ÿ        — U+00F8–U+00FF латиница-1;
#   ā-ž        — U+0101–U+017E Latin Extended-A (обе регистры в диапазоне);
#   ș ț        — U+0219/U+021B румынские comma-below.
# Текст ниже приводится к lower(), поэтому достаточно строчных; диапазоны,
# включающие заглавные (ā-ž), безвредны.
# Расширение набора — СМЕНА ИНДЕКСНОЙ ФОРМУЛЫ: точки, построенные по старому
# набору, пересчитываются только явным прогоном rebuild_sparse.py (force).
# Единый источник и для валидатора стоп-слов (locale_service._WORD_RE) — класс не
# должен расходиться с токенайзером.
TOKEN_EXTRA_LETTERS = "ßà-öø-ÿā-žșț"

# Заглавные европейские буквы для регексов по СЫРОМУ тексту (field_table._FIELD_NAME_RE,
# где lower() не применяется): латиница-1 À-Ö, Ø-Þ + ẞ + ȘȚ. Заглавные Latin
# Extended-A уже покрыты диапазоном ā-ž из TOKEN_EXTRA_LETTERS.
TOKEN_EXTRA_LETTERS_UPPER = "À-ÖØ-ÞẞȘȚ"

TOKEN_RE = re.compile(rf"[a-zа-яё0-9{TOKEN_EXTRA_LETTERS}]+")
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