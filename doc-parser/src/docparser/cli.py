# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""CLI для standalone-изучения парсера: doc-parser parse|info."""
import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from docparser import SUPPORTED_EXTENSIONS, blocks_to_markdown, parse_document
from docparser.parser import ParseError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doc-parser",
        description="Извлечение DOCX (комментарии + вложения), XLSX, PDF в блоки / Markdown.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="Разобрать документ")
    p_parse.add_argument("file", type=Path, help="Путь к документу (.docx / .xlsx / .pdf)")
    p_parse.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Формат вывода (по умолчанию markdown)",
    )
    p_parse.add_argument(
        "--attachments-dir",
        type=Path,
        help="Папка для сохранения вложений (иначе только разбор без сохранения)",
    )
    p_parse.add_argument("-o", "--output", type=Path, help="Записать результат в файл")

    p_info = sub.add_parser("info", help="Статистика по документу")
    p_info.add_argument("file", type=Path, help="Путь к документу")

    args = parser.parse_args(argv)

    try:
        if args.command == "parse":
            return _cmd_parse(args)
        return _cmd_info(args)
    except ParseError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


def _cmd_parse(args) -> int:
    blocks = parse_document(args.file, attachments_dir=args.attachments_dir)
    if args.format == "json":
        out = json.dumps([asdict(b) for b in blocks], ensure_ascii=False, indent=2)
    else:
        out = blocks_to_markdown(blocks)
    if args.output:
        args.output.write_text(out, encoding="utf-8")
        print(f"Записано в {args.output}")
    else:
        print(out)
    return 0


def _cmd_info(args) -> int:
    blocks = parse_document(args.file, attachments_dir=getattr(args, "attachments_dir", None))
    counts = Counter(b.type for b in blocks)
    print(f"Файл: {args.file}")
    print(f"Блоков: {len(blocks)}")
    for btype, n in counts.most_common():
        print(f"  {btype}: {n}")
    comments = [b for b in blocks if b.type == "comment"]
    if comments:
        print(f"Комментарии ({len(comments)}):")
        for c in comments:
            author = c.meta.get("author", "")
            print(f"  - [{author}] {c.text[:120]}")
    attachments = [b for b in blocks if b.type == "attachment"]
    if attachments:
        print(f"Вложения ({len(attachments)}):")
        for a in attachments:
            print(f"  - {a.text} {a.meta.get('saved_path', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
