"""Преобразование блоков в Markdown (используется сервисом и CLI)."""
from pathlib import Path

from docparser.blocks import Block


def blocks_to_markdown(blocks: list[Block]) -> str:
    lines: list[str] = []
    for b in blocks:
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
            if b.meta.get("saved_path"):
                lines.append(f"*(файл: {b.meta['saved_path']})*")
        elif b.type == "image":
            lines.append(_image_to_markdown(b))
        else:
            lines.append(b.text)
        lines.append("")
    return "\n".join(lines).strip()


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
    link = f"attachments/{Path(saved).name}"
    return f"![{caption}]({link})"
