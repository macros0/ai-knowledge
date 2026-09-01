"""Построение контекста для LLM из результатов композитного поиска.

Merge/collapse: группировка по (doc_id, chunk_index), слияние концепт+чанк.
XML-формат контекста для LLM.
"""
from app.config import Settings, get_settings
from app.services.fusion import Hit

CONCEPT_TYPE = "concept"
CHUNK_TYPE = "chunk"


def resolve_branches(
    mode: str | None,
    dense: bool | None,
    bm25: bool | None,
    settings: Settings,
) -> set[str]:
    """Разворачивает mode (пресет) или явные флаги в набор веток.

    Приоритет: если хотя бы один из dense/bm25 не None — используются
    флаги; иначе — пресет из mode или search_mode_default.
    """
    if dense is not None or bm25 is not None:
        branches: set[str] = set()
        if dense:
            branches.add("dense")
        if bm25:
            branches.add("bm25")
        return branches or {"dense"}
    from app.config import SEARCH_MODE_PRESETS

    preset_name = mode or settings.search_mode_default
    return SEARCH_MODE_PRESETS.get(preset_name, {"dense", "bm25"})


def merge_and_format(
    hits: list[Hit], settings: Settings | None = None, filename_lookup: dict[str, str] | None = None
) -> list[dict]:
    """Группировка по (doc_id, chunk_index), merge концепт+чанк.

    Возвращает список dict с ключами:
      title, content, tags, filepath, doc_id, score, source_filename,
      point_type, chunk_index

    Итоговый порядок — по fused score (убывание): сиблинг-концепты группы
    эмитятся после первичного блока, но при сортировке занимают место по
    своему собственному score. Без глобальной сортировки 7 сиблингов одной
    посредственной группы вытесняли более релевантные блоки других групп
    за границу top_k.
    """
    if settings is None:
        settings = get_settings()

    groups: dict[tuple[str, int | None], list[Hit]] = {}
    for hit in hits:
        doc_id = hit.payload.get("doc_id", "")
        chunk_idx = hit.payload.get("chunk_index")
        key = (doc_id, chunk_idx)
        groups.setdefault(key, []).append(hit)

    merged: list[dict] = []
    total_chars = 0
    for (doc_id, chunk_idx), group_hits in groups.items():
        if total_chars >= settings.chat_max_context_chars:
            break

        chunks_in_group = [h for h in group_hits if h.payload.get("point_type") == CHUNK_TYPE]
        concepts_in_group = [h for h in group_hits if h.payload.get("point_type") == CONCEPT_TYPE]

        tags: set[str] = set()
        for h in group_hits:
            tags.update(h.payload.get("tags", []))

        # source_filename: из payload (старые чанки) или из filename_lookup (новые)
        source = (chunks_in_group or concepts_in_group)[0].payload.get("source_document", {})
        source_filename = source.get("filename", "")
        if not source_filename and filename_lookup:
            source_filename = filename_lookup.get(doc_id, "")

        # filepath: из payload (концепты/старые чанки) или конструируется
        filepath = (concepts_in_group or chunks_in_group)[0].payload.get("filepath", "")
        if not filepath and chunk_idx is not None:
            filepath = f"{doc_id}/chunks/chunk_{chunk_idx:02d}.md"

        best_score = max(h.score for h in group_hits)

        if chunks_in_group and concepts_in_group:
            chunk = chunks_in_group[0]
            # Несколько концептов могут делить один chunk_index (например,
            # «Перечень: Элемент/Атрибут» и «reason1» из одной таблицы). Склейка
            # заголовков через " / " давала misleading-заголовок и ссылку не на
            # тот концепт — берём один репрезентативный концепт (первый по fused
            # score, он же используется для filepath ниже).
            primary = concepts_in_group[0]
            merged_title = primary.payload.get("title", "")
            content = chunk.payload.get("content", "")[: settings.chat_chunk_max_chars]
            if not merged_title:
                section_title = chunk.payload.get("section_title", "")
                if section_title:
                    merged_title = section_title
                else:
                    merged_title = f"{source_filename} (Раздел {chunk_idx + 1})" if chunk_idx is not None else source_filename
            point_type = CONCEPT_TYPE
            kind = "concept+chunk"
        elif concepts_in_group:
            concept = concepts_in_group[0]
            merged_title = concept.payload.get("title", "Без названия")
            content = concept.payload.get("content", "")[: settings.chat_concept_max_chars]
            point_type = CONCEPT_TYPE
            kind = "concept"
        else:
            chunk = chunks_in_group[0]
            section_title = chunk.payload.get("section_title", "")
            if section_title:
                merged_title = section_title
            else:
                merged_title = f"{source_filename} (Раздел {chunk_idx + 1})" if chunk_idx is not None else source_filename
            content = chunk.payload.get("content", "")[: settings.chat_chunk_max_chars]
            point_type = CHUNK_TYPE
            kind = "chunk"

        total_chars += len(content)
        merged.append(
            {
                "title": merged_title,
                "content": content,
                "tags": sorted(tags),
                "filepath": filepath,
                "doc_id": doc_id,
                "score": best_score,
                "source_filename": source_filename,
                "point_type": point_type,
                "kind": kind,
                "chunk_index": chunk_idx,
            }
        )

        # Сиблинг-концепты группы. Группа (doc_id, chunk_index) может содержать
        # много концептов (например, 18 полей служебной таблицы для ЛК); раньше
        # collapse выбрасывал их контент целиком — сильные bm25-попадания
        # вроде таблицы «Перечень: Наименование поля» не доезжали ни до LLM,
        # ни до sources. Эмитим каждый доп. концепт-хит отдельным блоком.
        siblings = concepts_in_group[1:] if concepts_in_group else []
        for concept in siblings:
            if total_chars >= settings.chat_max_context_chars:
                break
            sibling_content = concept.payload.get("content", "")[: settings.chat_concept_max_chars]
            if not sibling_content:
                continue
            total_chars += len(sibling_content)
            merged.append(
                {
                    "title": concept.payload.get("title", "Без названия"),
                    "content": sibling_content,
                    "tags": sorted(concept.payload.get("tags", [])),
                    "filepath": concept.payload.get("filepath", ""),
                    "doc_id": doc_id,
                    "score": concept.score,
                    "source_filename": source_filename,
                    "point_type": CONCEPT_TYPE,
                    "kind": "concept",
                    "chunk_index": chunk_idx,
                }
            )

    # Глобальный порядок по fused score: сиблинг с низким score не должен
    # стоять выше первичного блока другой группы с более сильным попаданием.
    # Stable sort сохраняет очерёдность равных (группа остаётся компактной).
    merged.sort(key=lambda b: b["score"], reverse=True)
    return merged


# Служебные слова вопроса, бесполезные как маркер выбора блока. В отличие от
# стоп-слов BM25 (services/sparse.py) в поиск не вмешиваются — фильтр работает
# только на этапе подсветки совпадений для LLM. «какие»/«есть» встречаются в
# почти каждом блоке и без фильтра размывают сигнал Matched terms.
_MARKER_STOPWORDS = {
    "какие", "какой", "какая", "каких", "каким", "кто", "где", "когда",
    "почему", "зачем", "сколько", "есть", "нет", "да", "можно", "нужно",
    "должен", "должна", "может", "могут", "будет", "быть", "также", "только",
}


def matched_terms(item: dict, query: str | None) -> list[str]:
    """Значимые термины запроса, буквально встречающиеся в заголовке/тексте блока.

    Тот же токенайзер, что и в BM25 (services/sparse.py), плюс фильтр служебных
    слов вопроса: совпадение по содержательному термину означает, что блок
    нашёлся и лексически, не только семантически. Пустой список — блок не
    содержит терминов вопроса (для LLM это сигнал слабой релевантности,
    выводится в metadata как Matched terms: []).
    """
    if not query:
        return []
    from app.services.sparse import tokenize

    haystack = f"{item.get('title', '')}\n{item.get('content', '')}".lower()
    return [
        t
        for t in tokenize(query)
        if t not in _MARKER_STOPWORDS and t in haystack
    ]


def drop_unmatched_blocks(merged: list[dict], query: str | None) -> list[dict]:
    """Анти-шумовой pre-filter для чата: убирает блоки без лексического совпадения.

    Модели (даже сильные) стабильно затаскивают в ответ семантически-смежный
    блок с пустым Matched terms, приписывая ему тему вопроса — промпт-правила
    этому только вероятностная защита. Детерминированное решение: если хотя
    бы у одного блока есть совпадение терминов запроса, блоки без совпадений
    в контекст не попадают вовсе.

    Если совпадений нет ни у одного блока (парафразный запрос — лексики
    запроса нет в корпусе), фильтр не срабатывает: все блоки остаются,
    ответ строится по смысловым совпадениям.
    """
    if not query or not merged:
        return merged
    matched = [bool(matched_terms(m, query)) for m in merged]
    if not any(matched):
        return merged
    return [m for m, has in zip(merged, matched) if has]


def format_context(merged: list[dict], query: str | None = None) -> str:
    """Форматирует merged-блоки в XML-подобный контекст для LLM.

    При заданном query в metadata каждого блока добавляется Matched terms —
    термины вопроса, найденные в блоке (помощь LLM в выборе релевантных блоков).
    """
    parts = []
    for i, item in enumerate(merged, start=1):
        tags_str = ", ".join(item.get("tags", []))
        source = item.get("source_filename", "")
        kind = item.get("kind", item.get("point_type", "concept"))
        terms = matched_terms(item, query)
        parts.append(
            f'<context_block id="{i}">\n'
            f'  <metadata>Title: {item["title"]} | Type: {kind} | Tags: [{tags_str}] | Source: {source} | Matched terms: [{", ".join(terms)}]</metadata>\n'
            f'  <content>\n{item["content"]}\n  </content>\n'
            f'</context_block>'
        )
    return "\n\n".join(parts) or "Контекст пуст."
