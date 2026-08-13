"""Восстановление data/tags.json из OKF-файлов (поле global_tags во frontmatter).

Запуск (при остановленном сервисе):
    python scripts/rebuild_tags.py                # data/ в текущем каталоге
    python scripts/rebuild_tags.py --data-dir /path/to/data

Скрипт независим от приложения: используется только стандартная библиотека и PyYAML.
"""
import argparse
import json
from pathlib import Path

import yaml


def read_global_tags(filepath: Path) -> list[str]:
    text = filepath.read_text(encoding="utf-8")
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text.startswith("---"):
        return []
    try:
        _, fm, _ = text.split("---", 2)
        meta = yaml.safe_load(fm) or {}
    except Exception:
        return []
    tags = meta.get("global_tags", []) or []
    return [str(t).strip() for t in tags if str(t).strip()]


def collect_tags(bundles_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not bundles_dir.is_dir():
        return counts
    for md in sorted(bundles_dir.glob("*/" + "*.md")):
        for tag in read_global_tags(md):
            counts[tag] = counts.get(tag, 0) + 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Восстановить data/tags.json из OKF-frontmatter.")
    parser.add_argument("--data-dir", type=Path, default=Path("./data"), help="Каталог данных (по умолчанию ./data)")
    args = parser.parse_args()

    bundles_dir = args.data_dir / "okf_bundles"
    counts = collect_tags(bundles_dir)
    target = args.data_dir / "tags.json"
    target.write_text(json.dumps(counts, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(counts.values())
    print(f"Восстановлено тегов: {len(counts)} (всего вхождений: {total}) -> {target}")


if __name__ == "__main__":
    main()
