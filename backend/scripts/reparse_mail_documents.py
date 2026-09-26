# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Найти старые документы с почтовыми кандидатами и штатно перегенерировать их.

По умолчанию скрипт ничего не меняет: он только строит компактный JSON-отчёт
по активным документам. Кандидатами считаются самостоятельные ``.eml``/``.msg``
и DOCX с bounded-проверкой OLE-объектов в ``word/embeddings``. Второй вид —
лишь возможное вложенное письмо, поэтому его запуск требует отдельного
``--include-embedded-candidates``. Не найденный кандидат не доказывает, что в
старом документе нет почты: скан намеренно не распаковывает Package/Ole10Native
и не пытается эвристически интерпретировать произвольные OLE-данные.

``--apply`` вызывает только Pipeline.regenerate()/wait_for(). Скрипт не пишет
SQL, Qdrant или файлы напрямую; полноценная регенерация может расходовать LLM
лимит, поэтому сначала следует сохранить и проверить dry-run отчёт.

Запуск из backend/ (при доступной БД, Qdrant и LLM):
    python scripts/reparse_mail_documents.py --report mail-candidates.json
    python scripts/reparse_mail_documents.py --doc-id <id> --apply
    python scripts/reparse_mail_documents.py --apply --include-embedded-candidates
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logger = logging.getLogger("reparse_mail_documents")

_MAIL_EXTENSIONS = {".eml", ".msg"}
_CFB_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
_MAX_ZIP_MEMBERS = 10_000


def _stored_path(upload_dir: Path, doc: dict[str, Any]) -> Path:
    return upload_dir / f"{doc['id']}{Path(doc['filename']).suffix.lower()}"


def _docx_ole_candidate(path: Path) -> bool:
    """Проверяет только сигнатуру bounded DOCX member, не распаковывая вложение.

    Скан специально консервативен: у обычного OLE нет mail-метаданных, поэтому
    это сигнал ``embedded_ole_candidate``, а не заявление, что найден MSG.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > _MAX_ZIP_MEMBERS:
                return False
            for member in members:
                name = member.filename.replace("\\", "/").lower()
                if not name.startswith("word/embeddings/") or member.is_dir():
                    continue
                with archive.open(member) as stream:
                    if stream.read(len(_CFB_SIGNATURE)) == _CFB_SIGNATURE:
                        return True
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return False
    return False


def scan_documents(documents: Iterable[dict[str, Any]], upload_dir: Path) -> list[dict[str, Any]]:
    """Возвращает стабильный, не содержащий почтовых текстов список кандидатов."""
    candidates: list[dict[str, Any]] = []
    for doc in sorted(documents, key=lambda item: str(item["id"])):
        source = _stored_path(upload_dir, doc)
        if not source.is_file():
            continue
        ext = source.suffix.lower()
        if ext in _MAIL_EXTENSIONS:
            confidence = "confirmed_standalone_mail"
        elif ext == ".docx" and _docx_ole_candidate(source):
            confidence = "embedded_ole_candidate"
        else:
            continue
        candidates.append(
            {
                "doc_id": doc["id"],
                "filename": doc["filename"],
                "status": doc.get("status"),
                "confidence": confidence,
                "bytes": source.stat().st_size,
            }
        )
    return candidates


def regenerate_candidates(
    candidates: Iterable[dict[str, Any]],
    *,
    pipeline: Any,
    apply: bool,
    include_embedded_candidates: bool,
    timeout: float,
) -> list[dict[str, Any]]:
    """Запускает штатную очередь только для разрешённых кандидатов.

    При ``apply=False`` эта функция не обращается к пайплайну. В отчёт всегда
    попадает причина, почему потенциально дорогая операция не была запущена.
    """
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        row = dict(candidate)
        if row["confidence"] == "embedded_ole_candidate" and not include_embedded_candidates:
            row["action"] = "skipped_embedded_candidate"
        elif not apply:
            row["action"] = "dry_run"
        else:
            try:
                pipeline.regenerate(row["doc_id"])
                final = pipeline.wait_for(row["doc_id"], timeout=timeout)
                row["action"] = "regenerated"
                row["final_status"] = final.get("status", "unknown")
                if final.get("error_code"):
                    row["error_code"] = final["error_code"]
            except Exception as exc:  # keep the batch report useful after one failure
                logger.exception("Не удалось перегенерировать %s", row["doc_id"])
                row["action"] = "error"
                row["error"] = str(exc)
        results.append(row)
    return results


def _report(results: list[dict[str, Any]], *, applied: bool) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for item in results:
        action = item["action"]
        counts[action] = counts.get(action, 0) + 1
    return {"mode": "apply" if applied else "dry_run", "counts": counts, "documents": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Найти и штатно перегенерировать mail-кандидаты.")
    parser.add_argument("--doc-id", action="append", help="Ограничить конкретным document id; repeatable")
    parser.add_argument("--limit", type=int, default=None, help="Максимум кандидатов после стабильной сортировки")
    parser.add_argument("--apply", action="store_true", help="Запустить штатную regenerate очередь")
    parser.add_argument(
        "--include-embedded-candidates",
        action="store_true",
        help="Разрешить DOCX с OLE-кандидатом; без него они только отражаются в отчёте",
    )
    parser.add_argument("--report", type=Path, help="Путь для JSON-отчёта")
    parser.add_argument("--timeout", type=float, default=3600, help="Ожидание одного запуска, секунд")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit должен быть положительным")
    if args.timeout <= 0:
        parser.error("--timeout должен быть положительным")

    # Импорты приложения создают LLM-клиенты в части окружений. Оставляем их
    # после argparse: ``--help`` и import unit-тестов не должны трогать сеть
    # или конфигурацию production-сервиса.
    from app.config import get_settings
    from app.services.pipeline import get_pipeline
    from app.services.registry import get_registry

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    docs = get_registry().list()
    if args.doc_id:
        requested = set(args.doc_id)
        docs = [doc for doc in docs if doc["id"] in requested]
        missing = sorted(requested - {doc["id"] for doc in docs})
        if missing:
            parser.error(f"Активные документы не найдены: {', '.join(missing)}")

    candidates = scan_documents(docs, get_settings().uploads_dir)
    if args.limit is not None:
        candidates = candidates[: args.limit]
    results = regenerate_candidates(
        candidates,
        pipeline=get_pipeline() if args.apply else None,
        apply=args.apply,
        include_embedded_candidates=args.include_embedded_candidates,
        timeout=args.timeout,
    )
    report = _report(results, applied=args.apply)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
