# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Имена файлов вложений, не зависящие от ОС обработки."""
from pathlib import PureWindowsPath


def portable_name(saved: str) -> str:
    r"""Basename из saved_path — независимо от ОС, на которой путь был создан.

    Path() берёт флейвор ТЕКУЩЕЙ ОС, и это делало защиту от утечки путей
    платформозависимой: на Linux/macOS windows-путь
    C:\Users\alexey\...\embedded-0.pdf разделителей не содержит вовсе, поэтому
    .name вернул бы строку целиком — абсолютный путь машины обработки утёк бы
    в контент бандла/чанка/концепта и в индекс (инцидент 2026-09-07).

    PureWindowsPath разбирает и \, и / на любой платформе, поэтому одинаково
    режет windows- и posix-пути. Это важно, когда бандлы с windows-путями
    обрабатываются в Linux-контейнере (перенос данных, старый staging).
    """
    return PureWindowsPath(saved).name
