"""Разбор markdown-текста: YAML-фронтматтер и заголовки секций.

Листовой модуль: не импортирует другие сервисы. Благодаря этому им могут
пользоваться и pipeline, и vector_store — раньше vector_store тянул
_extract_section_title из pipeline внутри функции, обходя цикл импортов.
"""
from pathlib import Path

import yaml


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Делит текст на YAML-фронтматтер и тело.

    Битый или отсутствующий фронтматтер — не ошибка: возвращается пустой dict
    и текст целиком. BOM в начале файла срезается.
    """
    if text.startswith("﻿"):
        text = text[1:]
    if not text.startswith("---"):
        return {}, text
    try:
        _, fm, body = text.split("---", 2)
        meta = yaml.safe_load(fm) or {}
    except Exception:
        return {}, text
    if not isinstance(meta, dict):
        return {}, body
    return meta, body


def read_frontmatter(path: Path) -> dict:
    """Только метаданные .md-файла. Нечитаемый файл — пустой dict."""
    try:
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return meta


def strip_heading(body: str) -> str:
    """Убирает заголовок '# <title>' из начала тела концепта."""
    lines = body.strip("\n").split("\n")
    while lines and lines[0].strip().startswith("#"):
        lines.pop(0)
    return "\n".join(lines).strip()


def extract_section_title(chunk_text: str) -> str:
    """Извлекает ближайший предшествующий заголовок секции из текста чанка.

    Ищет последний '# heading' в первых 50 строках чанка. Если чанк начинается
    с заголовка — возвращает его. Заголовок даёт семантический якорь для
    dense-эмбеддинга и отображается в UI как title чанка.
    """
    last_heading = ""
    for line in chunk_text.split("\n")[:50]:
        stripped = line.strip()
        if stripped.startswith("#"):
            last_heading = stripped.lstrip("#").strip()
    return last_heading
