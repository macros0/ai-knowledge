"""Разбор сохранённых MIME-писем EML в структурированные блоки."""
from __future__ import annotations

import re
from datetime import UTC
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote, urlsplit

from docparser.blocks import Block
from docparser.embedded import (
    MAX_ATTACHMENT_DEPTH,
    MAX_ATTACHMENT_PAYLOAD,
    Attachment,
    AttachmentBudget,
    marker_block,
    process_embedded,
)
from docparser.mail_images import ContentIdImages, escape_mail_text
from docparser.mime_wire import materialize_leaf, parse_wire_message

MAX_MAIL_HEADER_BYTES = 64 * 1024
MAX_MIME_DEPTH = 32
MAX_MAIL_BODY_BYTES = 50 * 1024 * 1024


def parse_eml(
    path: str | Path,
    attachments_dir: str | Path | None = None,
    depth: int = 0,
    budget=None,
    context=None,
    source_id: str = "root",
) -> list[Block]:
    """Извлекает метаданные, одно представление тела и MIME-вложения."""
    budget = budget or AttachmentBudget()
    raw = Path(path).read_bytes()
    message = parse_wire_message(raw, budget, header_limit=MAX_MAIL_HEADER_BYTES, depth_limit=MAX_MIME_DEPTH)
    if not _looks_like_rfc822_message(raw):
        from docparser.parser import ParseError

        raise ParseError("Файл .eml не содержит распознаваемого RFC 5322 сообщения")

    blocks: list[Block] = []
    subject = str(message.get("Subject") or "").strip()
    metadata = _mail_metadata(message)
    if context is not None:
        context.update_metadata(
            source_id,
            metadata | {"subject": subject},
        )
    if subject:
        blocks.append(Block("heading", subject, level=1, meta=metadata))

    images = ContentIdImages(context, source_id)
    encrypted = _is_encrypted_mail(message)
    if encrypted and context is not None and hasattr(context, "warn"):
        context.warn(source_id, "encrypted_mail")
    heading_count = len(blocks)
    for index, part in enumerate(_attachments(message)):
        filename = part.get_filename() or _attachment_name(part, index)
        content_id = str(part.get("Content-ID") or "")
        status = getattr(part, "_wire_status", None)
        if not status and depth + 1 >= MAX_ATTACHMENT_DEPTH:
            status = "skipped_depth"
        elif not status:
            size_bound = _attachment_size_bound(part)
            if size_bound is not None and size_bound > min(MAX_ATTACHMENT_PAYLOAD, budget.remaining):
                status = "skipped_size"
        if status:
            note, warning = {
                "skipped_count": ("превышен лимит количества вложений", "attachment_count_exceeded"),
                "skipped_depth": ("превышена глубина вложенности", "attachment_depth_exceeded"),
                "skipped_size": ("превышен лимит размера вложений", "attachment_size_exceeded"),
                "skipped_parse": ("не удалось извлечь часть письма", "mime_part_parse_failed"),
            }[status]
            att = Attachment(name=filename, prog_id="", caption="", data=b"")
            if context is not None:
                att.source_id = context.add_child(
                    source_id, filename, "mail" if part.get_content_type() == "message/rfc822" else "attachment",
                )
                context.warn(att.source_id, getattr(part, "_wire_warning", warning))
            # The original MIME container remains downloadable. Creating a
            # child file here would require decoding a rejected attachment.
            blocks.append(marker_block(att, note=note, extraction_status=status))
            images.register(content_id, None)
            continue
        payload = _attachment_payload(part)
        if payload is None:
            images.register(content_id, None)
            continue
        extracted = process_embedded(
                payload,
                filename,
                attachments_dir=attachments_dir,
                index=index,
                depth=depth + 1,
                budget=budget,
                context=context,
                parent_source_id=source_id,
                _node_reserved=True,
                inline_image=bool(content_id),
            )
        images.register(content_id, extracted[0] if extracted else None)
        blocks.extend(extracted)
    if not encrypted:
        blocks[heading_count:heading_count] = _body_blocks(message, images=images, context=context, source_id=source_id)
    return blocks


def _attachment_size_bound(part: EmailMessage) -> int | None:
    """Bound a leaf's decoded bytes without charset or transfer decoding.

    BytesParser stores wire octets as ASCII with surrogate escapes. Even
    get_payload(decode=False) can decode charset, so inspect that stored form.
    RFC822 parts remain wire byte leaves too; no serialization is needed.
    """
    wire = getattr(part, "_wire_body", None)
    if wire is not None:
        # A memoryview keeps the original body without copying/decoding it.
        raw = wire
        raw_bound = len(raw)
    else:
        raw = part._payload
        raw_bound = len(raw) if isinstance(raw, (str, bytes)) else 0
    if isinstance(raw, bytes):
        return len(raw)
    if not isinstance(raw, (str, memoryview)):
        return None
    if isinstance(raw, str) and any(ord(char) > 127 and not 0xDC80 <= ord(char) <= 0xDCFF for char in raw):
        # Defensive bound for non-wire strings: email's decode path may fall
        # back to raw-unicode-escape (up to ten bytes per character).
        return 10 * len(raw)
    if str(part.get("Content-Transfer-Encoding") or "").strip().casefold() != "base64":
        return raw_bound
    count = padding = 0
    for char in raw:
        if isinstance(char, int):
            char = chr(char)
        if char in " \t\r\n":
            continue
        if char == "=":
            padding += 1
        elif padding or char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/":
            return raw_bound  # malformed encoding may return undecoded bytes
        count += 1
    if count % 4 or padding > 2:
        return raw_bound
    return count // 4 * 3 - padding




def _looks_like_rfc822_message(raw: bytes) -> bool:
    """Reject arbitrary bytes named .eml without treating Subject alone as proof."""
    header, separator, _body = raw[:MAX_MAIL_HEADER_BYTES + 4].replace(b"\r\n", b"\n").partition(b"\n\n")
    if not separator:
        return False
    return bool(
        re.search(
            rb"(?mi)^(?:from|to|date|message-id|mime-version|content-type):[^\n]*$",
            header,
        )
    )


def _is_encrypted_mail(message: EmailMessage) -> bool:
    """Recognize opaque S/MIME envelopes without attempting decryption."""
    return message.get_content_type().lower() in {
        "application/pkcs7-mime",
        "application/x-pkcs7-mime",
    }


def _attachment_payload(part: EmailMessage) -> bytes | None:
    """Возвращает байты attachment, включая вложенное `message/rfc822`."""
    materialize_leaf(part)
    return part.get_payload(decode=True)


def _attachment_name(part: EmailMessage, index: int) -> str:
    if part.get_content_type() == "message/rfc822":
        return f"attachment-{index}.eml"
    return f"attachment-{index}"


def _mail_metadata(message: EmailMessage) -> dict:
    sent_at = _sent_at(message.get("Date"))
    # raw_items preserves invalid/unknown-zone dates rather than rewriting them.
    provenance = {}
    fields = {"date": "date_raw", "in-reply-to": "in_reply_to", "references": "references"}
    for name, value in message.raw_items():
        key = fields.get(name.lower())
        if key and key not in provenance:
            provenance[key] = value.strip()
    return {
        "mail": True,
        "sender": str(message.get("From") or "").strip(),
        "to": _addresses(message.get_all("To", [])),
        "cc": _addresses(message.get_all("Cc", [])),
        "sent_at": sent_at,
        "message_id": str(message.get("Message-ID") or "").strip(),
        **provenance,
    }


def _addresses(values: list[object]) -> list[str]:
    return [
        f"{name} <{address}>" if name else address
        for name, address in getaddresses([str(value) for value in values])
        if address
    ]


def _sent_at(value: object) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).isoformat()


def _body_blocks(message: EmailMessage, *, images: ContentIdImages | None = None,
                 context=None, source_id="root") -> list[Block]:
    """Retain independent body parts; choose one representation per alternative."""
    def visit(part, *, root=False):
        content_type = part.get_content_type().lower()
        if not root and (part.get_content_disposition() == "attachment" or part.get_filename()
                         or content_type == "message/rfc822"):
            return [], False
        if part.is_multipart():
            candidates = [visit(child) for child in part.iter_parts()]
            candidates = [(blocks, plain) for blocks, plain in candidates if blocks]
            if content_type == "multipart/alternative":
                if not candidates:
                    return [], False
                selected = next((candidate for candidate in candidates if candidate[1]), candidates[-1])
                if context is not None and any(
                    _body_terms(blocks) != _body_terms(selected[0]) for blocks, _ in candidates
                ):
                    context.warn(source_id, "mail_alternative_mismatch")
                return selected
            return [block for blocks, _ in candidates for block in blocks], any(plain for _, plain in candidates)
        if content_type not in {"text/plain", "text/html"}:
            return [], False
        text = _content_text(part, context=context, source_id=source_id)
        if content_type == "text/plain":
            return [Block("paragraph", escape_mail_text(p)) for p in _split_plain_paragraphs(text)], True
        parser = _MailHTML(images=images)
        parser.feed(text)
        parser.close()
        return parser.blocks(), False

    return visit(message, root=True)[0]


def _body_terms(blocks: list[Block]) -> list[str]:
    # Ignore presentation/link destinations while retaining ordered words,
    # numbers and negation. This is a mismatch diagnostic, not semantic proof.
    text = "\n".join(block.text for block in blocks)
    text = re.sub(r"\]\([^)]*\)", "]", text)
    return re.findall(r"\w+", text.casefold())


def _split_plain_paragraphs(text: str) -> list[str]:
    """Preserve blank-line paragraph boundaries from a plain-text message.

    A mail body used to become one ``Block`` even when it contained several
    paragraphs.  Markdown generation could still show the line breaks, but a
    concept without an LLM quote had no paragraph-sized navigation target.
    Keep single newlines inside a paragraph (common in quoted mail), and split
    only at an empty line.  The email library may still canonicalize CRLF while
    decoding, so this function does not add another normalization step.
    """
    newline = r"(?:\r\n|\r|\n)"
    return [
        paragraph.strip()
        for paragraph in re.split(rf"{newline}[ \t]*{newline}+", text)
        if paragraph.strip()
    ]


def _body_candidates(message: EmailMessage):
    """Yield current-message leaves, never descending into attached messages."""
    yield from _walk_current_message(message, include_attachments=False)


def _attachments(message: EmailMessage):
    """Yield attachment leaves below MIME containers without treating body as data."""
    yield from _walk_current_message(message, include_attachments=True)


def _walk_current_message(message: EmailMessage, *, include_attachments: bool):
    """Walk MIME while retaining the boundary of an attached message/rfc822.

    ``EmailMessage.walk`` descends into an attached RFC822 message. That makes
    its body look like the body of the enclosing message and makes multipart
    wrappers hide their own attachment leaves. This traversal treats every
    attached message as one leaf and otherwise recurses through containers.
    """
    def visit(part: EmailMessage, *, is_root: bool = False, attached: bool = False):
        content_type = part.get_content_type().lower()
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        is_message_attachment = not is_root and content_type == "message/rfc822"
        inline_resource = (
            not part.is_multipart()
            and bool(part.get("Content-ID"))
            and content_type not in {"text/plain", "text/html"}
        )
        is_attachment = attached or disposition == "attachment" or bool(filename) or is_message_attachment or inline_resource
        if is_message_attachment:
            if include_attachments:
                yield part
            return
        if part.is_multipart():
            for child in part.iter_parts():
                yield from visit(child, attached=is_attachment)
            return
        if include_attachments:
            if is_attachment:
                yield part
        elif not is_attachment:
            yield part

    yield from visit(message, is_root=True)


def _content_text(part: EmailMessage, *, context=None, source_id="root") -> str:
    size_bound = _attachment_size_bound(part)
    if size_bound is None or size_bound > MAX_MAIL_BODY_BYTES:
        raise ValueError("MIME body exceeds extraction limit")
    materialize_leaf(part)
    try:
        return str(part.get_content(errors="strict")).strip()
    except (LookupError, UnicodeError):
        if context is not None:
            context.warn(source_id, "mail_decode_recovered")
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "ascii"
        try:
            return payload.decode(charset, errors="replace").strip()
        except LookupError:
            return payload.decode("utf-8", errors="replace").strip()


class _MailHTML(HTMLParser):
    """Офлайн-сведение безопасного HTML письма к тексту и Markdown-таблицам."""

    _PARAGRAPH_TAGS: ClassVar[set[str]] = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"}
    _IGNORED_TAGS: ClassVar[set[str]] = {"head", "script", "style", "template", "iframe", "form"}

    def __init__(self, *, images: ContentIdImages | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self._blocks: list[Block] = []
        self._text: list[str] = []
        self._tables: list[dict] = []
        self._ignored_depth = 0
        self._link: tuple[list[str], int, str] | None = None
        self._images = images or ContentIdImages()

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self._IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag in self._PARAGRAPH_TAGS or tag in {"table", "tr", "td", "th", "a"}:
            self._close_link()
        if tag == "img":
            attributes = dict(attrs)
            markdown = self._images.markdown(attributes.get("src") or "", attributes.get("alt") or "")
            if markdown:
                if self._tables:
                    target = self._text_target()
                    if target is not None:
                        target.append(" " + markdown + " ")
                else:
                    # A standalone image paragraph avoids figure-inside-p in
                    # the viewer; its source is the mail, not a second artifact.
                    self._flush_text()
                    if markdown.startswith("!["):
                        self._blocks.append(Block("paragraph", markdown))
                    else:
                        self._text.append(markdown)
                        self._flush_text()
        elif tag == "a":
            href = _safe_mail_href(dict(attrs).get("href"))
            target = self._text_target()
            if href and target is not None:
                self._link = (target, len(target), href)
        elif tag == "table":
            if not self._tables:
                self._flush_text()
            self._tables.append({"rows": [], "row": None, "cell": None})
        elif tag == "tr" and self._tables:
            self._tables[-1]["row"] = []
        elif tag in {"td", "th"} and self._tables and self._tables[-1]["row"] is not None:
            self._tables[-1]["cell"] = []
        elif tag == "br":
            if self._tables and self._tables[-1]["cell"] is not None:
                self._tables[-1]["cell"].append(" ")
            else:
                self._text.append(" ")
        elif tag in self._PARAGRAPH_TAGS and not self._tables:
            self._flush_text()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._IGNORED_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if tag in self._PARAGRAPH_TAGS or tag in {"table", "tr", "td", "th", "a"}:
            self._close_link()
        table = self._tables[-1] if self._tables else None
        if tag in {"td", "th"} and table is not None and table["cell"] is not None and table["row"] is not None:
            table["row"].append(_normalize_text("".join(table["cell"])))
            table["cell"] = None
        elif tag == "tr" and table is not None and table["row"] is not None:
            if table["row"]:
                table["rows"].append(table["row"])
            table["row"] = None
        elif tag == "table" and table is not None:
            completed = self._tables.pop()
            if self._tables and self._tables[-1]["cell"] is not None:
                self._tables[-1]["cell"].append(" " + self._flatten_table(completed["rows"]) + " ")
            else:
                self._flush_table(completed["rows"])
        elif tag in self._PARAGRAPH_TAGS and not self._tables:
            self._flush_text()

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        target = self._text_target()
        if target is not None:
            # HTML text is literal text, not author-supplied Markdown. Escaping
            # also prevents ![remote](...) and entity-decoded raw HTML from
            # becoming active content when the normalized body is rendered.
            target.append(escape_mail_text(data))

    def _text_target(self) -> list[str] | None:
        if self._tables and self._tables[-1]["cell"] is not None:
            return self._tables[-1]["cell"]
        return None if self._tables else self._text

    def _close_link(self) -> None:
        if self._link is None:
            return
        target, start, href = self._link
        self._link = None
        label = "".join(target[start:])
        if label.strip():
            target[start:] = [f"[{label}]({href})"]

    def blocks(self) -> list[Block]:
        self._flush_text()
        while self._tables:
            self._flush_table(self._tables.pop()["rows"])
        return self._blocks

    def _flush_text(self) -> None:
        self._close_link()
        text = _normalize_text("".join(self._text))
        # HTML paragraph text can begin with list/rule punctuation, including
        # across separate text nodes. Preserve it as text after joining them.
        text = re.sub(r"^([-+])(?=\s)|^(-(?:\s*-){2,})$", r"\\\g<0>", text)
        text = re.sub(r"^(\d{1,9})([.)])(?=\s)", r"\1\\\2", text)
        self._text = []
        if text:
            self._blocks.append(Block("paragraph", text))

    def _flush_table(self, rows: list[list[str]]) -> None:
        if not rows:
            return
        width = max(len(row) for row in rows)
        normalized = [row + [""] * (width - len(row)) for row in rows]
        header = normalized[0]
        lines = ["| " + " | ".join(header) + " |", "|" + "---|" * width]
        lines.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
        self._blocks.append(Block("table", "\n".join(lines)))

    @staticmethod
    def _flatten_table(rows: list[list[str]]) -> str:
        return " ; ".join(" | ".join(_normalize_text(cell) for cell in row) for row in rows)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _safe_mail_href(value: str | None) -> str | None:
    """Preserve explicit web/mail links without inheriting the application's URL.

    Relative, protocol-relative, file, data and script targets stay plain labels.
    Percent encoding protects Markdown target boundaries without fetching links.
    """
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    value = value.strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() in {"http", "https"}:
            if not parsed.hostname:
                return None
        elif parsed.scheme.lower() != "mailto" or not parsed.path:
            return None
    except ValueError:
        return None
    # HTMLParser already decoded entities once. Markdown must not decode them
    # again; percent-encoding '&' would instead change query separators.
    return quote(value, safe="/:?#@!$&'+,;=%~.-_").replace("&", r"\&")
