"""Атомарная запись JSON-файлов (tmp + os.replace).

Прямая запись через write_text неатомарна: при падении процесса в момент записи
на диске остаётся обрезанный/повреждённый файл (манифесты, кэш). Хелпер пишет во
временный файл в том же каталоге и переименовывает через os.replace — читатели
видят либо старую, либо новую целую версию, никогда «половину».
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def write_json_atomic(path: Path, data: object, indent: int | None = 2) -> None:
    """Атомарно записывает data как UTF-8 JSON в path (tmp → os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        # Unique files isolate concurrent writers; close before replace on Windows.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.tmp-", delete=False,
        ) as stream:
            tmp = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=indent)
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
