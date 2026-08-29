"""Атомарная запись JSON-файлов (tmp + os.replace).

Прямая запись через write_text неатомарна: при падении процесса в момент записи
на диске остаётся обрезанный/повреждённый файл (манифесты, кэш). Хелпер пишет во
временный файл в том же каталоге и переименовывает через os.replace — читатели
видят либо старую, либо новую целую версию, никогда «половину».
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def write_json_atomic(path: Path, data: object, indent: int | None = 2) -> None:
    """Атомарно записывает data как UTF-8 JSON в path (tmp → os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    payload = json.dumps(data, ensure_ascii=False, indent=indent)
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
