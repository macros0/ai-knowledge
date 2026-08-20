"""Чтение OKF-бандлов с диска (data/okf_bundles/{doc_id}/).

Парсит YAML-frontmatter и тело концепта из .md-файлов. Используется при
переиндексации (per-doc и глобальной): векторный индекс — производные данные,
первоисточник это OKF-файлы.
"""
from pathlib import Path

import yaml

from app.models.schemas import OkfDocument


def parse_okf_file(filepath: Path) -> tuple[dict, str]:
    """Читает OKF-файл: возвращает метаданные (YAML-frontmatter) и тело концепта."""
    text = filepath.read_text(encoding="utf-8")
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text.startswith("---"):
        return {}, _strip_heading(text)
    try:
        _, fm, body = text.split("---", 2)
        meta = yaml.safe_load(fm) or {}
    except Exception:
        return {}, _strip_heading(text)
    if not isinstance(meta, dict):
        meta = {}
    return meta, _strip_heading(body)


def _strip_heading(body: str) -> str:
    """Убирает заголовок '# <title>' из начала тела концепта."""
    lines = body.strip("\n").split("\n")
    while lines and lines[0].strip().startswith("#"):
        lines.pop(0)
    return "\n".join(lines).strip()


def load_bundle(bundle_dir: Path) -> list[OkfDocument]:
    okf_docs: list[OkfDocument] = []
    for f in sorted(bundle_dir.glob("*.md")):
        meta, content = parse_okf_file(f)
        if not content:
            continue
        okf_docs.append(
            OkfDocument(
                filepath=str(f),
                metadata=meta,
                content=content,
                markdown=f.read_text(encoding="utf-8"),
            )
        )
    return okf_docs