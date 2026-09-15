# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Программное извлечение концептов из комментариев рецензентов.

Комментарии (вопросы рецензентов и ответы авторов) — самостоятельный слой
знаний о документе: в них фиксируются решения по спорным местам. LLM
обрабатывал их нестабильно: правило промпта требовало «встраивать в
соответствующий концепт», но по факту из 19 комментариев целевого документа
концептами стали только 4, а решения (например, «наибольший табельный —
самый свежий») терялись.

Модуль обходит LLM: блок-цитаты комментариев (формат docparser.markdown)
распознаются regex'ом и превращаются в концепты детерминированно (100%
recall). Один концепт = один тред «вопрос → ответы». LLM получает остаток
чанка с заглушкой — тот же контракт, что у field_table.extract_table_concepts.

Экстрактор запускается ДО табличного (см. okf_generator.generate_chunk):
незакрытая строка markdown-таблицы «заглатывает» последующие строки
(многострочные ячейки), блок-цитата после такой строки уходила бы в ячейку.
На сыром чанке блок-цитаты контигуальны и гарантированно целы.
"""
from __future__ import annotations

import logging
import re

from app.models.schemas import Concept

logger = logging.getLogger(__name__)

# Строка внутри блок-цитаты: "> текст" или ">" (пустая строка внутри цитаты).
_QUOTE_RE = re.compile(r"^>(?:\s(.*))?$")
# Сегмент треда: **Контекст:** / **Комментарий рецензента (автор, дата):** /
# **Ответ (автор):** / **Статус:** — двоеточие внутри жирного (формат
# docparser.markdown), текст до конца строки.
_LABEL_RE = re.compile(
    r"^\*\*(Комментарий рецензента|Ответ|Контекст|Статус)"
    r"(?:\s*\(([^)]*)\))?:\*\*\s*(.*)$"
)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

_STUB_FMT = "[Комментарии извлечены программно: {n}]"

# Длина префикса вопроса в title концепта.
_TITLE_MAX = 60
# Вопрос короче этого числа слов неинформативен как заголовок («Аналогично
# вопросу выше», «А что с датами?») — заголовок дополняется префиксом
# контекста якоря, иначе блок неузнаваем в списке источников/поиска.
_SHORT_QUESTION_WORDS = 5
# Длина префикса контекста якоря в заголовке.
_CONTEXT_TITLE_CHARS = 50


def extract_comment_concepts(chunk: str, chunk_index: int | None = None) -> tuple[list[Concept], str]:
    """Извлечь концепты из блок-цитат комментариев в чанке.

    Возвращает (concepts, remainder): каждый найденный блок-цитатный тред
    заменён в remainder заглушкой `[Комментарии извлечены программно: N]`.
    Обычные блок-цитаты (без меток рецензента) не трогаются.
    """
    concepts: list[Concept] = []
    lines = chunk.split("\n")
    i = 0
    # длина lines меняется после замены run на заглушку — считаем на каждой итерации
    while i < len(lines):
        if not lines[i].startswith(">"):
            i += 1
            continue
        start = i
        while i < len(lines) and lines[i].startswith(">"):
            i += 1
        threads = _parse_quote_run(lines[start:i])
        if not threads:
            continue  # обычная цитата — оставляем LLM/чанкам как есть
        entries_total = 0
        for thread in threads:
            concepts.append(_build_concept(thread))
            entries_total += len(thread["entries"])
        lines[start:i] = [_STUB_FMT.format(n=entries_total)]
        i = start + 1  # список сжался после замены run на заглушку
    if concepts:
        logger.info(
            "Чанк %s: программно извлечено %d концептов-комментариев",
            chunk_index,
            len(concepts),
        )
    return concepts, "\n".join(lines)


# ---------------------------------------------------------------- parsing
def _strip_quote(line: str) -> str:
    m = _QUOTE_RE.match(line)
    if m is None:
        return line
    return m.group(1) if m.group(1) is not None else ""


def _parse_quote_run(run_lines: list[str]) -> list[dict]:
    """Разобрать блок-цитату на треды.

    Возвращает список тредов {context, entries: [{author, date, text}],
    resolved} или [] для цитаты без комментариев. Тред начинается с метки
    «Комментарий рецензента» (вопрос); «Ответ» и «Статус» относятся к текущему
    треду; «Контекст» до вопроса относится к следующему треду.
    """
    segs: list[tuple[str, str, list[str]]] = []  # (kind, who, lines)
    for raw in run_lines:
        text = _strip_quote(raw)
        m = _LABEL_RE.match(text)
        if m:
            segs.append((m.group(1), (m.group(2) or "").strip(), [m.group(3).strip()]))
        elif segs:
            segs[-1][2].append(text)
        # ведущие строки без меток — не комментарий, пропускаем

    threads: list[dict] = []
    cur: dict | None = None
    pending_context: list[str] = []
    for kind, who, seg_lines in segs:
        text = "\n".join(seg_lines).strip()
        if kind == "Контекст":
            if cur is None:
                pending_context.append(text)
            else:
                cur["context"] = text
        elif kind == "Комментарий рецензента":
            author, date = _split_author_date(who)
            cur = {
                "context": "\n".join(pending_context).strip() or None,
                "entries": [{"author": author, "date": date, "text": text}],
                "resolved": False,
            }
            pending_context = []
            threads.append(cur)
        elif kind == "Ответ":
            if cur is None:
                # ответ без вопроса (дефектный блок) — начинаем новый тред
                cur = {"context": None, "entries": [], "resolved": False}
                threads.append(cur)
            cur["entries"].append({"author": who, "date": "", "text": text})
        elif kind == "Статус":
            if cur is not None and "закрыт" in text:
                cur["resolved"] = True
    return [t for t in threads if t["entries"]]


def _split_author_date(who: str) -> tuple[str, str]:
    """'Волкова Анастасия, 2026-06-23' -> ('Волкова Анастасия', '2026-06-23')."""
    if not who:
        return "", ""
    parts = [p.strip() for p in who.split(",")]
    if len(parts) > 1 and _DATE_RE.fullmatch(parts[-1]):
        return ", ".join(parts[:-1]), parts[-1]
    return who, ""


# ---------------------------------------------------------------- concepts
def _build_concept(thread: dict) -> Concept:
    question = thread["entries"][0]
    author = question.get("author", "")
    surname = author.split()[0] if author else ""
    tags = ["review", "comment"]
    if surname:
        tags.append(surname)

    parts: list[str] = []
    if thread.get("context"):
        parts.append(f"**Контекст:** {thread['context']}")
    for i, e in enumerate(thread["entries"]):
        role = "Комментарий рецензента" if i == 0 else "Ответ"
        who = e.get("author", "")
        if i == 0 and e.get("date"):
            who = f"{who}, {e['date']}" if who else e["date"]
        label = f"**{role} ({who}):**" if who else f"**{role}:**"
        parts.append(f"{label} {e['text']}")
    if thread.get("resolved"):
        parts.append("**Статус:** замечание закрыто")

    return Concept(
        id="",
        title=_make_title(question["text"], thread.get("context")),
        type="note",
        tags=tags,
        content="\n\n".join(parts),
        relations=[],
    )


def _make_title(question_text: str, context: str | None = None) -> str:
    """Заголовок концепта-треда. Короткий/неразговорный вопрос (< 5 слов,
    например «Аналогично вопросу выше») сам по себе неузнаваем — дополняется
    префиксом контекста якоря: «Замечание рецензента (Если найдено несколько
    табельных…): Аналогично вопросу выше». Без контекста — только вопрос."""
    flat = " ".join(question_text.split())
    ctx = " ".join((context or "").split())
    if len(flat.split()) < _SHORT_QUESTION_WORDS and ctx:
        prefix = ctx[:_CONTEXT_TITLE_CHARS]
        if len(ctx) > _CONTEXT_TITLE_CHARS:
            if " " in prefix:
                prefix = prefix.rsplit(" ", 1)[0]
            prefix = prefix.rstrip(" ,.;:!?") + "…"
        return f"Замечание рецензента ({prefix}): {flat or '(без текста)'}"
    if len(flat) > _TITLE_MAX:
        cut = flat[:_TITLE_MAX]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        flat = cut.rstrip(" ,.;:!?") + "…"
    flat = flat or "(без текста)"
    return f"Замечание рецензента: {flat}"
