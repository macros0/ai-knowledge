"""Пересборка коллекции Qdrant из OKF-бандлов на диске (data/okf_bundles/{doc_id}/).

Векторный индекс — производные данные: первоисточник это OKF-файлы с
YAML-фронтматтером. Если коллекция потеряна (например, после апгрейда Qdrant,
когда storage-формат несовместим) — этот скрипт полностью пересобирает индекс
из okf_bundles без пересоздания документов.

Запуск (при остановленном сервисе, из каталога backend):
    python scripts/reindex.py
    python scripts/reindex.py --data-dir /path/to/data

Требует доступный Qdrant и embedding-сервер (или EMBEDDING_PROVIDER=fake).
"""
import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.schemas import OkfDocument
from app.services.embedder import Embedder
from app.services.vector_store import VectorStore


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Пересобрать коллекцию Qdrant из OKF-бандлов.")
    parser.add_argument("--data-dir", type=Path, default=Path("./data"), help="Каталог данных (по умолчанию ./data)")
    args = parser.parse_args()

    bundles_dir = args.data_dir / "okf_bundles"
    if not bundles_dir.is_dir():
        print(f"Каталог не найден: {bundles_dir}")
        raise SystemExit(1)

    bundle_ids = sorted(d.name for d in bundles_dir.iterdir() if d.is_dir())
    if not bundle_ids:
        print("OKF-бандлы не найдены. Индекс будет пересоздан пустым.")
        bundle_ids = []

    vs = VectorStore()
    if vs.client.collection_exists(vs.collection):
        vs.client.delete_collection(vs.collection)
        print(f"Удалена старая коллекция: {vs.collection}")
    vs.ensure_collection()
    print(f"Создана коллекция: {vs.collection}")

    embedder = Embedder()
    total_concepts = 0
    for doc_id in bundle_ids:
        okf_docs = load_bundle(bundles_dir / doc_id)
        if not okf_docs:
            print(f"[{doc_id}] пропущен: OKF-концепты не найдены")
            continue
        vectors = embedder.embed_texts([d.content for d in okf_docs])
        vs.index_concepts(doc_id, okf_docs, vectors)
        total_concepts += len(okf_docs)
        print(f"[{doc_id}] индексировано концептов: {len(okf_docs)}")

    points = vs.client.count(collection_name=vs.collection, exact=True).count
    print(f"Готово: документов {len(bundle_ids)}, концептов {total_concepts}, точек в коллекции {points}")


if __name__ == "__main__":
    main()