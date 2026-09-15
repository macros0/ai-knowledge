# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""CLI экспорта OKF-бандла из PostgreSQL (Этап 2b, Фаза 5).

Генерирует бандл-структуру (YAML/Markdown + `_files.json` + `chunks/` +
`attachments/`) в указанный каталог из БД — тот же путь, что и эндпоинт
`POST /documents/{id}/export-okf`. Бандл — производная проекция БД, не рабочее
состояние; экспорт нужен для переноса/инспекции/обмена.

Запуск (из backend/, при доступной БД):
    python scripts/export_okf.py <doc_id> [--dest <dir>]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.services.export_okf import export_okf_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description="Экспорт OKF-бандла из PostgreSQL.")
    parser.add_argument("doc_id", type=str, help="ID документа")
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="Каталог экспорта (по умолчанию data/export_okf/<doc_id>)",
    )
    args = parser.parse_args()

    dest = args.dest or get_settings().data_dir / "export_okf" / args.doc_id
    try:
        files = export_okf_bundle(args.doc_id, dest)
    except ValueError as exc:
        print(f"Ошибка: {exc}")
        sys.exit(1)
    print(f"Экспортировано файлов: {len(files)} в {dest}")


if __name__ == "__main__":
    main()
