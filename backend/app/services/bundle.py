"""Чтение OKF-бандлов с диска (data/okf_bundles/{doc_id}/).

Парсит YAML-frontmatter и тело концепта из .md-файлов. Используется при
переиндексации (per-doc и глобальной): векторный индекс — производные данные,
первоисточник это OKF-файлы.
"""
from pathlib import Path

from app.models.schemas import OkfDocument
from app.services.markdown import parse_frontmatter, strip_heading


def parse_okf_file(filepath: Path) -> tuple[dict, str]:
    """Читает OKF-файл: возвращает метаданные (YAML-frontmatter) и тело концепта."""
    meta, body = parse_frontmatter(filepath.read_text(encoding="utf-8"))
    return meta, strip_heading(body)


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