"""Deterministic validation and normalization for glossary source data."""
from __future__ import annotations

import re
import unicodedata
from app import error_codes as codes
from dataclasses import dataclass
from functools import lru_cache

SUPPORTED_KINDS = frozenset(
    {"sap_infotype", "sap_transaction", "sap_table", "sap_program", "sap_object", "abbreviation", "business_term"}
)
_CANONICAL_RE = re.compile(r"^[A-Z0-9_/:.\-]{1,128}$")
_CANONICAL_LETTER_RE = re.compile(r"[A-Z]")
_WS_RE = re.compile(r"\s+")
_REFERENCE_LOCALE_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})*$")
_SHORT_DENYLIST = frozenset({"тр", "pa", "py", "ом", "om"})
_IDENTIFIER_EXTRA = "_"


class GlossaryValidationError(ValueError):
    """A glossary value violates a deterministic input rule."""
    code = codes.GLOSSARY_INVALID_ALIAS


class GlossaryMigrationRequiredError(GlossaryValidationError):
    """The glossary namespace has not passed metadata validation yet."""
    code = codes.GLOSSARY_MIGRATION_REQUIRED


class GlossaryRedundantAliasError(GlossaryValidationError):
    """An explicit alias repeats an identity already owned by this card."""
    code = codes.GLOSSARY_REDUNDANT_ALIAS


class GlossaryMultipleInfotypeNumbersError(GlossaryValidationError):
    code = codes.GLOSSARY_MULTIPLE_INFOTYPE_NUMBERS


class GlossaryInvalidRuleError(GlossaryValidationError):
    code = codes.GLOSSARY_INVALID_RULE


class GlossaryInvalidLocaleError(GlossaryValidationError):
    code = codes.GLOSSARY_INVALID_LOCALE


class GlossaryUnsafeAutoExpandError(GlossaryValidationError):
    code = codes.GLOSSARY_UNSAFE_AUTO_EXPAND


def validate_infotype_number(kind: str, number: str | None) -> None:
    if kind == "sap_infotype":
        if not isinstance(number, str) or not re.fullmatch(r"[0-9]{4}", number):
            raise GlossaryValidationError("Номер инфотипа обязателен и должен содержать ровно четыре цифры")
    elif number is not None:
        raise GlossaryValidationError("Номер инфотипа допустим только для вида sap_infotype")


def normalize_alias(text: str) -> str:
    """Return the literal lookup key for an alias.

    This deliberately does not transliterate, remove punctuation, or perform
    fuzzy matching.  The combining dot produced by lower-casing Latin ``İ``
    is removed so case variants have one stable key.
    """
    if not isinstance(text, str):
        raise GlossaryValidationError("Алиас должен быть строкой")
    return _normalize_short_alias(text) if len(text) <= 256 else _normalize_literal(text)


@lru_cache(maxsize=4096)
def _normalize_short_alias(text: str) -> str:
    return _normalize_literal(text)


def _normalize_literal(text: str) -> str:
    value = unicodedata.normalize("NFC", text).strip()
    value = _WS_RE.sub(" ", value).lower().replace("\u0307", "")
    return unicodedata.normalize("NFC", value)


@dataclass(frozen=True)
class NormalizedText:
    """Normalized query plus a mapping back to half-open source offsets."""

    text: str
    original_starts: tuple[int, ...]
    original_ends: tuple[int, ...]

    def source_span(self, start: int, end: int) -> tuple[int, int]:
        if start >= end or start < 0 or end > len(self.text):
            raise ValueError("invalid normalized span")
        return self.original_starts[start], self.original_ends[end - 1]


def normalize_query_with_mapping(text: str) -> NormalizedText:
    """Normalize a query while retaining offsets into the user's text.

    ``str.lower`` may expand a character (Turkish ``İ`` is the important
    example) and whitespace runs collapse.  Storing a source range for each
    normalized character keeps the public match offsets meaningful without
    changing the existing sparse/index normalizer.
    """
    if not isinstance(text, str):
        raise GlossaryValidationError("Запрос должен быть строкой")

    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    index = 0
    while index < len(text):
        cluster_end = index + 1
        while cluster_end < len(text) and unicodedata.combining(text[cluster_end]):
            cluster_end += 1
        cluster = text[index:cluster_end]
        value = unicodedata.normalize("NFC", cluster).lower().replace("\u0307", "")
        if cluster.isspace():
            value = " "
        for output in value:
            if output.isspace():
                output = " "
            if output == " " and chars and chars[-1] == " ":
                ends[-1] = cluster_end
                continue
            chars.append(output)
            starts.append(index)
            ends.append(cluster_end)
        index = cluster_end

    while chars and chars[0] == " ":
        chars.pop(0)
        starts.pop(0)
        ends.pop(0)
    while chars and chars[-1] == " ":
        chars.pop()
        starts.pop()
        ends.pop()
    return NormalizedText("".join(chars), tuple(starts), tuple(ends))


def is_identifier_char(value: str) -> bool:
    """Return whether a character belongs to a domain identifier."""
    return bool(value) and (value.isalnum() or value in _IDENTIFIER_EXTRA)


def is_technical_identifier_char(value: str) -> bool:
    return is_identifier_char(value) or (bool(value) and value in "/.:-")


def technical_span_boundary(text: str, start: int, end: int) -> bool:
    """Reject embedded codes while allowing sentence punctuation at their edges.

    A slash belongs to SAP namespaces. A dot, colon or hyphen only joins a
    neighbouring identifier when there is another identifier part beyond it.
    """
    def continues(index: int, direction: int) -> bool:
        while 0 <= index < len(text) and text[index] in ".:-":
            index += direction
        return 0 <= index < len(text) and (
            is_identifier_char(text[index]) or text[index] == "/"
        )

    return not continues(start - 1, -1) and not continues(end, 1)


def literal_matches(normalized: NormalizedText, form: str, *, boundary_mode: str = "phrase") -> tuple[tuple[int, int], ...]:
    """Find literal, identifier-boundary-safe occurrences of ``form``."""
    key = normalize_alias(form)
    if not key:
        return ()
    result: list[tuple[int, int]] = []
    for match in _literal_pattern(key).finditer(normalized.text):
        start, end = match.span()
        if boundary_mode == "identifier":
            if technical_span_boundary(normalized.text, start, end):
                result.append((start, end))
            continue
        if start and is_identifier_char(normalized.text[start - 1]) and is_identifier_char(key[0]):
            continue
        if end < len(normalized.text) and is_identifier_char(normalized.text[end]) and is_identifier_char(key[-1]):
            continue
        result.append((start, end))
    return tuple(result)


@lru_cache(maxsize=4096)
def _literal_pattern(key: str) -> re.Pattern:
    return re.compile(re.escape(key))


def normalize_canonical(value: str, kind: str) -> str:
    if kind not in SUPPORTED_KINDS:
        raise GlossaryValidationError(f"Неподдерживаемый вид термина: {kind}")
    if not isinstance(value, str):
        raise GlossaryValidationError("Канонический код должен быть строкой")
    canonical = value.strip().upper()
    if kind == "sap_infotype":
        if not re.fullmatch(r"IT[0-9]{4}", canonical):
            raise GlossaryValidationError("Инфотип должен иметь формат IT и четыре цифры")
        return canonical
    if not _CANONICAL_RE.fullmatch(canonical) or not _CANONICAL_LETTER_RE.search(canonical):
        raise GlossaryValidationError("Канонический код имеет недопустимый формат")
    return canonical


def validate_locale(locale: str, *, allow_und: bool = True) -> str:
    if not isinstance(locale, str):
        raise GlossaryInvalidLocaleError("Язык должен быть строкой")
    value = locale.strip()
    if allow_und and value == "und":
        return value
    if not _REFERENCE_LOCALE_RE.fullmatch(value):
        raise GlossaryInvalidLocaleError("Недопустимый код языка")
    return value


def validate_alias_options(
    alias: str,
    *,
    kind: str,
    auto_expand: bool,
    search_enabled: bool,
) -> str:
    normalized = normalize_alias(alias)
    if not normalized or len(normalized) > 256:
        raise GlossaryValidationError("Алиас должен быть непустым и не длиннее 256 символов")

    if normalized in _SHORT_DENYLIST and (auto_expand or search_enabled):
        raise GlossaryUnsafeAutoExpandError("Короткая форма запрещена для автоматических прав поиска")

    letters_only = normalized.isalpha()
    if letters_only and len(normalized) < 3 and (auto_expand or search_enabled):
        raise GlossaryUnsafeAutoExpandError("Слишком короткий алиас нельзя включить автоматически")

    if normalized.isdigit():
        if auto_expand:
            raise GlossaryUnsafeAutoExpandError("Числовой алиас не может быть триггером")
        if search_enabled and (kind != "sap_infotype" or len(normalized) != 4):
            raise GlossaryUnsafeAutoExpandError(
                "Числовой алиас можно включить в поиск только для четырёхзначного инфотипа"
            )
    return normalized
