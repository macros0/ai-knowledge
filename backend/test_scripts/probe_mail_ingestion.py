"""Локальный parser-profile для синтетических вложенных EML без БД/LLM/сети.

По умолчанию создаёт 100 детерминированных писем с одним вложенным RFC822,
проверяет source tree и печатает JSON c latency. Скрипт не читает uploads,
не меняет storage и не вызывает Qdrant/LLM; он полезен как воспроизводимый
нижний порог перед отдельной интеграционной приёмкой production-пайплайна.
"""
from __future__ import annotations

import argparse
from email.message import EmailMessage
import hashlib
import json
from pathlib import Path
from statistics import quantiles
from tempfile import TemporaryDirectory
from time import perf_counter

from docparser import parse_document_result


def _mail_bytes(index: int) -> bytes:
    nested = EmailMessage()
    nested["From"] = "approver@example.test"
    nested["Subject"] = f"Решение {index:03d}"
    nested.set_content(f"Лимит согласования для заявки {index:03d}: 12 дней.")

    root = EmailMessage()
    root["From"] = "sender@example.test"
    root["Subject"] = f"Пересылка решения {index:03d}"
    root.set_content("См. вложенное решение.")
    root.add_attachment(nested, filename=f"decision-{index:03d}.eml")
    root.set_boundary(f"mail-parser-profile-{index:03d}")
    return root.as_bytes()


def run(count: int) -> dict:
    if not 1 <= count <= 1_000:
        raise ValueError("count must be between 1 and 1000")
    payloads = [_mail_bytes(index) for index in range(count)]
    latencies: list[float] = []
    source_nodes = 0
    with TemporaryDirectory(prefix="mail-parser-profile-") as temporary:
        root = Path(temporary)
        for index, payload in enumerate(payloads):
            path = root / f"mail-{index:03d}.eml"
            path.write_bytes(payload)
            started = perf_counter()
            parsed = parse_document_result(path)
            latencies.append(perf_counter() - started)
            if [(node.source_id, node.parent_source_id, node.kind) for node in parsed.sources] != [
                ("root", None, "document"),
                ("root/0", "root", "mail"),
            ]:
                raise RuntimeError(f"unexpected source tree for synthetic mail {index}")
            source_nodes += len(parsed.sources)
    ordered = sorted(latencies)
    return {
        "kind": "synthetic_mail_parser_profile",
        "count": count,
        "input_sha256": hashlib.sha256(b"".join(payloads)).hexdigest(),
        "source_nodes": source_nodes,
        "latency_seconds": {
            "min": round(ordered[0], 6),
            "p50": round(quantiles(ordered, n=100, method="inclusive")[49], 6),
            "p95": round(quantiles(ordered, n=100, method="inclusive")[94], 6),
            "max": round(ordered[-1], 6),
            "total": round(sum(latencies), 6),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--report", type=Path, help="optional JSON output path")
    args = parser.parse_args()
    result = run(args.count)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
