"""Структурное происхождение извлечённого содержимого."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from docparser.attachment_limits import (
    DEFAULT_ATTACHMENT_MAX_DEPTH,
    validate_attachment_depth,
)
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
# v24 retains full canonical text with structural tables; clipped R2 checkpoints are incompatible.
PARSER_VERSION = "mail-sources-v27-tables-v1"


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

    def __init__(self, root_name: str | Path, *, mail_enabled: bool = True, table_sink=None,
                 processing_policy=None, attachment_max_depth=DEFAULT_ATTACHMENT_MAX_DEPTH):
        self.table_sink = table_sink
        self.mail_enabled = mail_enabled
        self.processing_policy = processing_policy
        self.attachment_max_depth = validate_attachment_depth(
            processing_policy.traversal.max_depth if processing_policy is not None else attachment_max_depth)
        self._locator_chains = {'root': ()}
        self.parser_version = parser_version_for_mail_mode(mail_enabled)
        if processing_policy is not None:
            self.parser_version += '-scope-v1'
            expected=processing_policy.snapshot.get('parser_version')
            if expected is not None and expected!=self.parser_version:
                raise ValueError('Processing scope parser version is incompatible; reassess attachments')
        else:
            # Prevent resume from combining chunks parsed with different depth limits.
            self.parser_version += f'-depth-{self.attachment_max_depth}'
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

    def key_for(self, parent_source_id: str, locator: str) -> str:
        from docparser.attachment_policy import make_source_key
        if self.processing_policy is None:
            raise ValueError('Source keys require a processing policy')
        return make_source_key(self.processing_policy.source_file_hash,
                               (*self._locator_chains[parent_source_id], locator))

    def add_child(self, parent_source_id: str, display_name: str, kind: str,
                  *, locator: str | None = None) -> str:
        ordinal = self._next_ordinal.get(parent_source_id, 0)
        self._next_ordinal[parent_source_id] = ordinal + 1
        source_id = f"{parent_source_id}/{ordinal}"
        locator = locator or f'ordinal:{ordinal}'
        self._locator_chains[source_id] = (*self._locator_chains[parent_source_id], locator)
        metadata = {}
        if self.processing_policy is not None:
            key = self.key_for(parent_source_id, locator)
            metadata = {'source_key': key, 'source_locator': locator,
                        'processing_included': self.processing_policy.is_included(key),
                        **self.processing_policy.source_metadata(key)}
        self.sources.append(
            SourceNode(
                source_id=source_id,
                parent_source_id=parent_source_id,
                ordinal=ordinal,
                kind=kind,
                display_name=Path(display_name).name or "вложение",
                metadata=metadata,
            )
        )
        return source_id

    def update_metadata(self, source_id: str, metadata: dict) -> None:
        """Заменяет metadata уже зарегистрированного узла без смены его ID."""
        for index, node in enumerate(self.sources):
            if node.source_id == source_id:
                protected={key:value for key,value in node.metadata.items()
                           if key.startswith('processing_') or key in {'source_key','source_locator'}}
                self.sources[index] = replace(node, metadata={**metadata,**protected})
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
