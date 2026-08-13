"""Преобразование блоков в Markdown (используется сервисом и CLI)."""
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
            author = b.meta.get("author", "")
            prefix = f"**Комментарий рецензента ({author}):**" if author else "**Комментарий рецензента:**"
            lines.append(f"> {prefix} {b.text}")
        elif b.type == "code":
            lines.append("```")
            lines.append(b.text)
            lines.append("```")
        elif b.type == "attachment":
            lines.append(f"**{b.text}**")
            if b.meta.get("saved_path"):
                lines.append(f"*(файл: {b.meta['saved_path']})*")
        else:
            lines.append(b.text)
        lines.append("")
    return "\n".join(lines).strip()
