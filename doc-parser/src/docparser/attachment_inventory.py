"""Bounded container inspection without blocks, concepts or table rows."""

from __future__ import annotations

import hashlib
import io
import posixpath
import tempfile
import time
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from defusedxml import ElementTree as ET
from docparser.extensions import SUPPORTED_EXTENSIONS

from docparser.attachment_assessment import node_is_heavy
from docparser.attachment_policy import (
    AssessmentLimits,
    AttachmentInventory,
    AttachmentNode,
    TraversalLimits,
    make_source_key,
)


class InvalidRootDocument(ValueError):
    """The root is malformed; it cannot be admitted as an unknown attachment."""


class InspectionBudgetExceeded(ValueError):
    pass


class _CountedReader:
    def __init__(self, stream, inspector):
        self.stream = stream
        self.inspector = inspector

    def read(self, size=-1):
        self.inspector.check()
        remaining = self.inspector.limits.scan_bytes - self.inspector.result.scan_bytes
        data = self.stream.read(min(size if size >= 0 else 65536, 65536, max(1, remaining + 1)))
        self.inspector.check(len(data))
        return data


def identify_payload(data: bytes, name: str) -> tuple[str, bytes]:
    from docparser.embedded import detect_ooxml_ext, unwrap_ole

    _, payload = unwrap_ole(data)
    if payload.startswith(b"PK"):
        return (detect_ooxml_ext(payload) or ".zip")[1:], payload
    if payload.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z", payload
    if payload.startswith(b"Rar!\x1a\x07"):
        return "rar", payload
    if payload.startswith(b"%PDF"):
        return "pdf", payload
    from docparser.embedded import _looks_like_msg
    if payload.startswith(b"\xd0\xcf\x11\xe0") and _looks_like_msg(payload):
        return "msg", payload
    from docparser.eml_parser import _looks_like_rfc822_message

    if _looks_like_rfc822_message(payload):
        return "eml", payload
    return "unsupported", payload


class _Inspector:
    def __init__(
        self,
        source_hash,
        limits,
        traversal,
        attachments_dir,
        *,
        mail_enabled=True,
        helper_path=None,
    ):
        from docparser.source_model import parser_version_for_mail_mode

        self.result = AttachmentInventory(
            source_file_hash=source_hash,
            parser_version=parser_version_for_mail_mode(mail_enabled) + "-scope-v1",
        )
        self.limits = limits
        self.traversal = traversal
        self.start = time.monotonic()
        self.attachments_dir = Path(attachments_dir) if attachments_dir else None
        self.total_payload = 0
        self.mail_enabled = mail_enabled
        self.helper_path = helper_path

    def check(self, count=0):
        self.result.scan_bytes += count
        if (
            self.result.scan_bytes > self.limits.scan_bytes
            or time.monotonic() - self.start > self.limits.timeout_seconds
        ):
            raise InspectionBudgetExceeded("Attachment inspection budget reached")

    def read(self, stream, limit=None):
        maximum = min(
            limit or self.traversal.max_payload_bytes,
            self.limits.scan_bytes - self.result.scan_bytes,
        )
        if maximum < 0:
            raise InspectionBudgetExceeded("Attachment inspection budget reached")
        chunks = []
        length = 0
        while True:
            data = stream.read(min(65536, maximum - length + 1))
            if not data:
                break
            length += len(data)
            self.check(len(data))
            if length > maximum:
                raise InspectionBudgetExceeded("Attachment payload budget reached")
            chunks.append(data)
        return b"".join(chunks)

    def unknown(self, node, status="assessment_incomplete"):
        self.result.complete = False
        if node is None:
            node = self.add_node(
                "root", (), "uninspected-remainder", "Непросмотренные вложения", "branch"
            )
        node.assessment = "unknown"
        node.children_complete = False
        node.status = status

    def add_node(self, parent, chain, locator, name, kind):
        if len(self.result.nodes) >= self.traversal.max_nodes:
            raise InspectionBudgetExceeded("Attachment node budget reached")
        key = make_source_key(self.result.source_file_hash, (*chain, locator))
        node = AttachmentNode(
            key,
            parent,
            locator,
            name,
            kind,
            metrics={
                "rows": None,
                "cells": None,
                "text_bytes": 0,
                "file_bytes": 0,
                "expanded_bytes": 0,
            },
        )
        self.result.nodes.append(node)
        return node

    def child(self, data, name, locator, parent, chain, depth):
        self.check()
        node = self.add_node(parent, chain, locator, name, "unknown")
        node.metrics["file_bytes"] = len(data)
        if depth > self.traversal.max_depth or len(data) > self.traversal.max_payload_bytes:
            self.unknown(
                node, "skipped_depth" if depth > self.traversal.max_depth else "skipped_size"
            )
            return
        self.total_payload += len(data)
        if self.total_payload > self.traversal.max_total_bytes:
            self.unknown(node, "skipped_size")
            return
        try:
            kind, payload = identify_payload(data, name)
            node.kind = (
                "mail"
                if kind in {"eml", "msg"}
                else "archive"
                if kind in {"zip", "7z", "rar"}
                else kind
            )
            node.supported = ("." + kind in SUPPORTED_EXTENSIONS or kind in {"zip", "7z", "rar"}) and (
                self.mail_enabled or kind not in {"eml", "msg"}
            )
            node.content_fingerprint = hashlib.sha256(payload).hexdigest()
            if self.attachments_dir:
                self.attachments_dir.mkdir(parents=True, exist_ok=True)
                suffix = "." + kind if kind != "unsupported" else ".bin"
                target = self.attachments_dir / (node.source_key + suffix)
                with target.open("xb") as out:
                    out.write(payload)
                node.saved_path = target.name
            if not node.supported:
                node.assessment = "unsupported"
                node.status = "unsupported"
                return
            with tempfile.TemporaryDirectory(prefix="attachment-inspect-") as folder:
                path = Path(folder) / ("source." + kind)
                path.write_bytes(payload)
                self.document(path, kind, (*chain, locator), node.source_key, depth, node)
            if node.assessment != "unknown" and node_is_heavy(node, self.limits):
                node.assessment = "heavy"
        except (InspectionBudgetExceeded, ValueError, OSError, zipfile.BadZipFile) as error:
            from docparser.storage_errors import is_storage_full_error

            if is_storage_full_error(error):
                raise
            self.unknown(node, getattr(error, "status", "assessment_incomplete"))

    def document(self, path, kind, chain, parent, depth, node=None):
        self.check()
        before = len(self.result.nodes)
        if kind in {"docx", "xlsx"}:
            self.office(path, kind, chain, parent, depth, node)
        elif kind == "eml":
            self.eml(path, chain, parent, depth, node)
        elif kind == "msg":
            self.msg(path, chain, parent, depth, node)
        elif kind == "pdf":
            self.pdf(path, chain, parent, depth, node)
        elif kind in {"zip", "7z", "rar"}:
            self.archive(path, kind, chain, parent, depth, node)
        if node and len(self.result.nodes) > before:
            node.metrics["has_children"] = 1

    def office(self, path, kind, chain, parent, depth, node):
        from docparser.archive_guard import validate_zip

        validate_zip(path)
        with zipfile.ZipFile(path) as archive:
            if kind == "xlsx":
                # The root's table size is irrelevant: it is always included.
                if node:
                    self.xlsx_metrics(archive, node)
                for info in archive.infolist():
                    if info.filename.startswith("xl/embeddings/") and not info.is_dir():
                        with archive.open(info) as stream:
                            data = self.read(stream)
                        self.child(
                            data,
                            PurePosixPath(info.filename).name,
                            "ooxml:" + info.filename,
                            parent,
                            chain,
                            depth + 1,
                        )
            else:
                parts = sorted(
                    n
                    for n in archive.namelist()
                    if n == "word/document.xml"
                    or n.startswith(("word/header", "word/footer"))
                    and n.endswith(".xml")
                )
                for part in parts:
                    with archive.open(part) as stream:
                        xml = ET.fromstring(self.read(stream))
                    if node:
                        text_size = sum(
                            len((e.text or "").encode("utf-8"))
                            for e in xml.iter()
                            if e.tag.endswith("}t")
                        )
                        node.metrics["text_bytes"] += text_size
                    relationships = {}
                    rel_path = posixpath.join(
                        posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels"
                    )
                    if rel_path in archive.namelist():
                        with archive.open(rel_path) as stream:
                            rels = ET.fromstring(self.read(stream))
                        for rel in rels:
                            if rel.get("TargetMode") != "External":
                                target = posixpath.normpath(
                                    posixpath.join(posixpath.dirname(part), rel.get("Target", ""))
                                )
                                relationships[rel.get("Id")] = target.lstrip("/")
                    counts = Counter()
                    for element in xml.iter("{urn:schemas-microsoft-com:office:office}OLEObject"):
                        rid = element.get(
                            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                        )
                        locator = f"ooxml:{part}:{rid}:{counts[rid]}"
                        counts[rid] += 1
                        target = relationships.get(rid)
                        if target not in archive.namelist():
                            continue
                        with archive.open(target) as stream:
                            data = self.read(stream)
                        self.child(
                            data, PurePosixPath(target).name, locator, parent, chain, depth + 1
                        )

    def xlsx_metrics(self, archive, node):
        node.metrics["rows"] = node.metrics["cells"] = 0
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            with archive.open("xl/sharedStrings.xml") as stream:
                xml = ET.fromstring(self.read(stream))
            strings = ["".join(e.itertext()) for e in xml if e.tag.endswith("}si")]
        parts = [
            i
            for i in archive.infolist()
            if i.filename.startswith("xl/worksheets/") and i.filename.endswith(".xml")
        ]
        node.metrics["expanded_bytes"] = sum(i.file_size for i in parts)
        if node.metrics["expanded_bytes"] >= self.limits.expanded_bytes:
            node.assessment = "heavy"
            node.precision = "lower_bound"
            return
        for info in parts:
            with archive.open(info) as stream:
                # iterparse avoids iterating formatted empty rectangles.
                row_occupied = False
                for _, element in ET.iterparse(_CountedReader(stream, self), events=("end",)):
                    self.check()
                    if element.tag.endswith("}c"):
                        vals = [
                            e.text or ""
                            for e in element.iter()
                            if e.tag.endswith(("}v", "}f", "}t"))
                        ]
                        if any(value.strip() for value in vals):
                            node.metrics["cells"] += 1
                            row_occupied = True
                            if element.get("t") == "s" and vals and vals[0].isdigit():
                                index = int(vals[0])
                                text = strings[index] if index < len(strings) else vals[0]
                            else:
                                text = "".join(vals)
                            node.metrics["text_bytes"] += len(text.encode("utf-8"))
                        element.clear()
                    elif element.tag.endswith("}row"):
                        node.metrics["rows"] += int(row_occupied)
                        row_occupied = False
                        element.clear()
                    if node_is_heavy(node, self.limits):
                        node.assessment = "heavy"
                        node.precision = "lower_bound"
                        return

    def eml(self, path, chain, parent, depth, node):
        from docparser.embedded import AttachmentBudget
        from docparser.eml_parser import (
            _attachment_payload,
            _attachment_size_bound,
            _attachments,
        )
        from docparser.mime_wire import parse_wire_message

        data = path.read_bytes()
        self.check(len(data))
        message = parse_wire_message(data, AttachmentBudget(), header_limit=65536, depth_limit=32)
        for index, part in enumerate(_attachments(message)):
            if (
                part.get_content_maintype() == "image"
                and part.get_content_disposition() != "attachment"
            ):
                continue
            size = _attachment_size_bound(part)
            if size is not None and size > min(
                self.traversal.max_payload_bytes, self.limits.scan_bytes - self.result.scan_bytes
            ):
                child = self.add_node(
                    parent, chain, f"mime:{index}", part.get_filename() or "attachment", "unknown"
                )
                self.unknown(child, "skipped_size")
                continue
            payload = _attachment_payload(part)
            if payload is not None:
                self.child(
                    payload,
                    part.get_filename() or f"attachment-{index}",
                    f"mime:{index}",
                    parent,
                    chain,
                    depth + 1,
                )
        if node:
            # Conservatively use own non-attachment wire leaves; never inspect child text for body metrics.
            from docparser.eml_parser import _body_candidates

            for part in _body_candidates(message):
                wire = getattr(part, "_wire_body", b"")
                node.metrics["text_bytes"] += len(wire or b"")

    def msg(self, path, chain, parent, depth, node):
        import olefile

        from docparser.embedded import AttachmentBudget
        from docparser.msg_parser import _read_scoped_message

        with olefile.OleFileIO(str(path)) as ole:
            budget = AttachmentBudget(
                total=min(
                    self.traversal.max_total_bytes, self.limits.scan_bytes - self.result.scan_bytes
                )
            )
            message = _read_scoped_message(ole, (), budget=budget)
            self.msg_data(message, chain, parent, depth, node, budget)

    def msg_data(self, message, chain, parent, depth, node, budget):
        from docparser.msg_parser import (
            _attachment_name,
            _read_scoped_message,
            _read_scoped_properties,
            _read_stream,
        )

        if node:
            node.metrics["text_bytes"] = len(
                str(message.get("properties", {}).get("Body") or "").encode("utf-8")
            )
        for index, handle in enumerate(message.get("attachments", [])):
            self.check()
            properties = _read_scoped_properties(
                handle.ole, handle.path, 8, budget, handle.encoding
            )
            name = _attachment_name(properties, index)
            locator = "ole:" + "/".join(handle.path)
            data_path = [*handle.path, "__substg1.0_37010102"]
            mail_path = [*handle.path, "__substg1.0_3701000D"]
            if handle.ole.exists(data_path):
                raw = _read_stream(handle.ole, data_path, budget, consume=False)
                self.check(len(raw))
                self.child(raw, name, locator, parent, chain, depth + 1)
            elif handle.ole.exists(mail_path):
                child = self.add_node(parent, chain, locator, name, "mail")
                if depth + 1 > self.traversal.max_depth:
                    self.unknown(child, "skipped_depth")
                    continue
                nested = _read_scoped_message(
                    handle.ole,
                    tuple(mail_path),
                    budget=budget,
                    named_properties=handle.named_properties,
                )
                self.msg_data(nested, (*chain, locator), child.source_key, depth + 1, child, budget)
                child.metrics["has_children"] = int(bool(nested.get("attachments")))

    def pdf(self, path, chain, parent, depth, node):
        from docparser.pdf_provider import get_pdf_provider

        document = get_pdf_provider().open(path)
        try:
            for index, entry in enumerate(document.iter_attachments()):
                self.check()
                if (
                    entry.declared_size is None
                    or entry.declared_size > self.traversal.max_payload_bytes
                ):
                    child = self.add_node(parent, chain, f"pdf:{index}", entry.name, "unknown")
                    self.unknown(child)
                    continue
                raw = entry.read_bytes()
                self.check(len(raw))
                self.child(raw, entry.name, f"pdf:{index}", parent, chain, depth + 1)
            if node:
                for page in range(document.page_count()):
                    self.check()
                    node.metrics["text_bytes"] += len(document.extract_text(page).encode("utf-8"))
                    if node_is_heavy(node, self.limits):
                        node.assessment = "heavy"
                        node.precision = "lower_bound"
                        break
        finally:
            document.close()

    def archive(self, path, kind, chain, parent, depth, node):
        try:
            from docparser.archive_reader import open_archive
        except ImportError:
            self.unknown(node, "archive_backend_unavailable")
            return
        with open_archive(
            path, format=kind, limits=self.traversal, helper_path=self.helper_path
        ) as reader:
            entries = list(reader.entries())
            for entry in entries:
                if entry.is_dir:
                    continue
                self.check()
                buffer = io.BytesIO()
                reader.extract_selected(
                    frozenset({entry.entry_key}), lambda _, target=buffer: target
                )
                payload = buffer.getvalue()
                self.check(len(payload))
                self.child(
                    payload,
                    entry.display_path,
                    "archive:" + entry.entry_key,
                    parent,
                    chain,
                    depth + 1,
                )


def inspect_attachment_inventory(
    path: Path,
    filename: str,
    *,
    limits: AssessmentLimits,
    traversal: TraversalLimits,
    expanded_source_keys: frozenset[str] = frozenset(),
    attachments_dir: Path | None = None,
    mail_enabled: bool = True,
    helper_path=None,
    branch: dict | None = None,
) -> AttachmentInventory:
    path = Path(path)
    with path.open("rb") as stream:
        source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    inspector = _Inspector(
        branch["source_file_hash"] if branch else source_hash,
        limits,
        traversal,
        attachments_dir,
        mail_enabled=mail_enabled,
        helper_path=helper_path,
    )
    try:
        if branch:
            with path.open("rb") as stream:
                raw = inspector.read(stream)
            inspector.child(
                raw,
                filename,
                branch["locator"],
                branch["parent_key"],
                tuple(branch["parent_chain"]),
                branch["depth"],
            )
        else:
            inspector.document(path, Path(filename).suffix.lower()[1:], (), "root", 0)
    except InspectionBudgetExceeded:
        inspector.result.complete = False
        # A root remainder explicitly represents work we could not enumerate.
        if len(inspector.result.nodes) < traversal.max_nodes:
            inspector.unknown(None)
        elif inspector.result.nodes:
            inspector.unknown(inspector.result.nodes[-1])
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        from docparser.storage_errors import is_storage_full_error

        if is_storage_full_error(exc):
            raise
        raise InvalidRootDocument("Не удалось прочитать структуру исходного файла.") from exc
    inspector.result.elapsed_seconds = time.monotonic() - inspector.start
    return inspector.result
