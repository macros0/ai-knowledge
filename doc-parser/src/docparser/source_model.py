"""Структурное происхождение извлечённого содержимого."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from docparser.blocks import Block

# v3 additionally preserves blank-line boundaries in plain-text mail bodies.
# v4 makes disabled embedded-mail parsing an explicit checkpoint mode.
# Resume checkpoints produced by an earlier parser must not be combined with
# the new chunk layout or its paragraph anchors.
# v6 retains original mail dates/threading and normalizes MSG dates to UTC.
# v7 preserves binary MSG attachments and diagnoses protected/non-mail objects.
# v8 reads root MSG natively, skips RTF and rejects depth before OLE inspection.
# v9 admits MSG streams and MIME byte leaves before reading/transfer decoding.
# v10 preserves RFC822 wire bytes and bounds MIME headers, tree and body decoding.
# v11 extracts distinct DOCX OLE occurrences from nested tables and story parts.
# v12 excludes administrative attachment markers from canonical backend chunks.
# v13 bounds DOCX OLE admission before resolving blobs and emits one remainder.
# v14 admits XLSX/PDF attachments lazily before payload reads.
# v15 includes all XLSX embedding suffixes and mail in OLE native wrappers.
# v16 diagnoses unsupported text containers instead of silently reporting saved.
# v17 retains safe HTML mail links and escapes literal Markdown syntax.
# v18 binds CID raster images to admitted attachments of the same mail.
# v19 rejects MSG depth before reading and excludes mail subjects from text eligibility.
# v20 records structured depth warnings for bounded Office/PDF original retention.
# v21 escapes plain mail, preserves mixed MIME bodies, diagnoses decoding and
# alternatives, bounds MSG remainder sources and retains native sender identity.
# v22 resolves root named MAPI properties for protected embedded MSG messages.
PARSER_VERSION = "mail-sources-v22"


def parser_version_for_mail_mode(mail_enabled: bool) -> str:
    return PARSER_VERSION if mail_enabled else f"{PARSER_VERSION}-mail-disabled"


@dataclass(frozen=True)
class SourceNode:
    """Один узел исходного файла или вложения внутри корневого документа."""

    source_id: str
    parent_source_id: str | None
    ordinal: int
    kind: str
    display_name: str
    metadata: dict = field(default_factory=dict)
    warnings: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ParseResult:
    """Блоки и дерево происхождения одного корневого документа."""

    blocks: list[Block]
    sources: list[SourceNode]
    warnings: list[dict] = field(default_factory=list)
    parser_version: str = PARSER_VERSION


class ParseContext:
    """Выдаёт детерминированные source_id по пути структурного вхождения."""

    def __init__(self, root_name: str | Path, *, mail_enabled: bool = True):
        self.mail_enabled = mail_enabled
        self.parser_version = parser_version_for_mail_mode(mail_enabled)
        self.sources = [
            SourceNode(
                source_id="root",
                parent_source_id=None,
                ordinal=0,
                kind="document",
                display_name=Path(root_name).name,
            )
        ]
        self._next_ordinal: dict[str, int] = {"root": 0}
        self.warnings: list[dict] = []

    def add_child(self, parent_source_id: str, display_name: str, kind: str) -> str:
        ordinal = self._next_ordinal.get(parent_source_id, 0)
        self._next_ordinal[parent_source_id] = ordinal + 1
        source_id = f"{parent_source_id}/{ordinal}"
        self.sources.append(
            SourceNode(
                source_id=source_id,
                parent_source_id=parent_source_id,
                ordinal=ordinal,
                kind=kind,
                display_name=Path(display_name).name or "вложение",
            )
        )
        return source_id

    def update_metadata(self, source_id: str, metadata: dict) -> None:
        """Заменяет metadata уже зарегистрированного узла без смены его ID."""
        for index, node in enumerate(self.sources):
            if node.source_id == source_id:
                self.sources[index] = replace(node, metadata=dict(metadata))
                return
        raise ValueError(f"Unknown source_id: {source_id}")

    def warn(self, source_id: str, code: str) -> None:
        """Record a stable parse warning on the document and affected node."""
        warning = {"code": code, "source_id": source_id}
        self.warnings.append(warning)
        for index, node in enumerate(self.sources):
            if node.source_id == source_id:
                self.sources[index] = replace(node, warnings=[*node.warnings, warning])
                return
        raise ValueError(f"Unknown source_id: {source_id}")
