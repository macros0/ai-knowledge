"""Атомарная запись JSON-состояния на диск.

Прямой write_text поверх боевого файла оставляет усечённый JSON, если процесс
обрывается посреди записи. Запись во временный файл с последующим os.replace
подменяет файл целиком: читатель видит либо прежнюю версию, либо новую, но
никогда половину.
"""
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, data: Any, *, indent: int | None = 2) -> None:
    """Пишет data в path как JSON атомарной подменой.

    Временный файл создаётся в каталоге назначения — os.replace атомарен только
    в пределах одной файловой системы. Имя уникально (mkstemp), чтобы
    параллельные писатели не затирали временный файл друг друга.

    fsync перед подменой: без него переживается обрыв процесса, но не потеря
    питания — переименование может лечь на диск раньше самих данных.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
