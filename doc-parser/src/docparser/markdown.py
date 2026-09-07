"""Преобразование блоков в Markdown (используется сервисом и CLI)."""
from docparser.blocks import Block
from docparser.paths import portable_name


def blocks_to_markdown(blocks: list[Block]) -> str:
    lines, _ = _render_lines(blocks)
    return "\n".join(lines).strip()


def markdown_attachment_spans(blocks: list[Block]) -> tuple[str, list[tuple[int, int]]]:
    """(markdown, слитые char-диапазоны блоков с meta['from_attachment']).

    Атрибуция происхождения (программный тег «attachment» в бэкенде) работает на
    уровне чанков, а чанки режутся из markdown. Спаны — char-диапазоны НА ФИНАЛЬНОМ
    (stripнутом) тексте, чтобы бэкенд мог считать долю символов вложения в чанке.
    Диапазоны отсортированы и не пересекаются; блоки без from_attachment не входят.
    """
    lines, spans = _render_lines(blocks)
    joined = "\n".join(lines)
    text = joined.strip()
    lead = len(joined) - len(joined.lstrip())  # сколько ведущих символов срезал strip()

    # char-позиция начала каждой строки в ПРЕ-strip тексте
    offsets: list[int] = []
    acc = 0
    for line in lines:
        offsets.append(acc)
        acc += len(line) + 1

    ranges: list[tuple[int, int]] = []
    for b, (start_line, end_line) in zip(blocks, spans):
        if not b.meta.get("from_attachment"):
            continue
        start = offsets[start_line] - lead
        end = offsets[end_line] - 1 - lead  # исключить завершающий "\n" перед сепаратором
        start = max(0, min(start, len(text)))
        end = max(start, min(end, len(text)))
        if end > start:
            ranges.append((start, end))
    return text, _merge_spans(ranges)


def _render_lines(blocks: list[Block]) -> tuple[list[str], list[tuple[int, int]]]:
    """Рендерит блоки в список строк + per-block диапазоны [start, end) индексов.

    Каждый блок даёт k >= 1 строк контента и один пустой сепаратор (""); диапазон
    покрывает ТОЛЬКО строки контента (без сепаратора). Единственный источник строк
    для blocks_to_markdown и markdown_attachment_spans — расхождений быть не может.
    """
    lines: list[str] = []
    spans: list[tuple[int, int]] = []
    for b in blocks:
        start = len(lines)
        if b.type == "heading":
            level = min(max(b.level or 2, 1), 6)
            lines.append(f"{'#' * level} {b.text}")
        elif b.type == "table":
            lines.append(b.text)
        elif b.type == "comment":
            lines.append(_comment_to_markdown(b))
        elif b.type == "code":
            lines.append("```")
            lines.append(b.text)
            lines.append("```")
        elif b.type == "attachment":
            lines.append(f"**{b.text}**")
            saved = b.meta.get("saved_path")
            if saved:
                # Парсеры кладут в meta['saved_path'] АБСОЛЮТНЫЙ путь к файлу на диске
                # (attachments_dir машины обработки). Рендерим только переносимое
                # относительное имя attachments/<файл> (как в _image_to_markdown) —
                # иначе машинно-зависимый локальный путь утекает в контент бандла/
                # чанка/концепта и в индекс (инцидент 2026-09-07).
                lines.append(f"*(файл: attachments/{portable_name(saved)})*")
        elif b.type == "image":
            lines.append(_image_to_markdown(b))
        else:
            lines.append(b.text)
        lines.append("")
        spans.append((start, len(lines) - 1))
    return lines, spans


def _merge_spans(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Сливает отсортированные (start,end) в непересекающиеся диапазоны."""
    if not ranges:
        return []
    merged: list[tuple[int, int]] = []
    cur_start, cur_end = ranges[0]
    for start, end in ranges[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = start, end
    merged.append((cur_start, cur_end))
    return merged


def _comment_to_markdown(b: Block) -> str:
    """Блок-цитата комментария. Тред (вопрос + ответы) — единая цитата с
    контекстом якоря и статусом; формат строк стабилен и распознаётся
    программным экстрактором концептов (backend comment_concepts):
        > **Контекст:** <текст абзаца-якоря>
        > **Комментарий рецензента (автор, дата):** <вопрос>
        > **Ответ (автор):** <ответ>
        > **Статус:** замечание закрыто
    """
    thread = b.meta.get("thread")
    if thread:
        out: list[str] = []
        context = (b.meta.get("context") or "").strip()
        if context:
            out.append(f"**Контекст:** {context}")
        for i, entry in enumerate(thread):
            role = "Комментарий рецензента" if i == 0 else "Ответ"
            author = entry.get("author", "")
            date = (entry.get("date") or "")[:10]
            who = author
            if i == 0 and author and date:
                who = f"{author}, {date}"
            label = f"**{role} ({who}):**" if who else f"**{role}:**"
            out.append(f"{label} {entry.get('text', '')}")
        if b.meta.get("resolved"):
            out.append("**Статус:** замечание закрыто")
        return "\n".join(_quote_line(line) for line in out)
    author = b.meta.get("author", "")
    prefix = f"**Комментарий рецензента ({author}):**" if author else "**Комментарий рецензента:**"
    return _quote_line(f"{prefix} {b.text}")


def _quote_line(text: str) -> str:
    """Префиксует каждую строку текста `>` — многострочные записи остаются
    внутри одной блок-цитаты (важно для программного экстрактора)."""
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def _image_to_markdown(b: Block) -> str:
    """Рендерит image-блок как Markdown-ссылку `![caption](attachments/<файл>)`.

    Путь — относительно корня бандла (attachments/), что сохраняет переносимость
    при локальном просмотре бандла.
    """
    caption = (b.meta.get("caption") or b.meta.get("name") or "изображение").replace("]", "\\]").replace("[", "\\[")
    saved = b.meta.get("saved_path")
    if not saved:
        return f"*(изображение: {caption})*"
    link = f"attachments/{portable_name(saved)}"
    return f"![{caption}]({link})"
