"""Офлайн-разбор выбранных свойств Outlook MSG непосредственно из CFB."""
from __future__ import annotations

import codecs
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email import policy
from email.header import decode_header, make_header
from email.parser import Parser
from pathlib import Path
from uuid import UUID

import olefile

from docparser.blocks import Block
from docparser.embedded import (
    MAX_ATTACHMENT_DEPTH,
    MAX_ATTACHMENT_NODES,
    MAX_ATTACHMENT_PAYLOAD,
    Attachment,
    AttachmentBudget,
    attachment_count_marker,
    marker_block,
    process_embedded,
)
from docparser.eml_parser import _MailHTML, _sent_at
from docparser.mail_images import ContentIdImages, escape_mail_text
from docparser.storage_errors import is_storage_full_error


class _MsgSizeLimit(ValueError):
    """A CFB stream cannot be opened within the shared extraction budget."""


@dataclass(frozen=True)
class _MsgAttachment:
    ole: object
    path: tuple[str, ...]
    encoding: str
    named_properties: dict[int, str]


def _read_stream(ole, path, budget: AttachmentBudget, *, consume=True) -> bytes:
    # olefile.openstream itself buffers the stream. Limiting read() afterwards
    # is too late: inspect the directory length BEFORE constructing the buffer.
    size = ole.get_size(path)
    if size > MAX_ATTACHMENT_PAYLOAD or size > budget.remaining:
        raise _MsgSizeLimit("MSG stream exceeds extraction limit")
    if consume:
        budget.consume(size)
    with ole.openstream(path) as stream:
        raw = stream.read(size + 1)
    if len(raw) != size:
        raise ValueError("MSG stream length differs from its directory entry")
    return raw


def parse_msg(
    path: str | Path,
    attachments_dir: str | Path | None = None,
    depth: int = 0,
    budget=None,
    context=None,
    source_id: str = "root",
) -> list[Block]:
    """Извлекает поля и байтовые вложения MSG без Outlook/COM/сети."""
    # MsOxMessage eagerly decompresses RTF and traverses attachments before
    # the service can enforce its policy. Use the same explicit property reader
    # for root and embedded messages; RTF is intentionally unsupported.
    budget = budget or AttachmentBudget()
    with olefile.OleFileIO(str(path)) as ole:
        if not ole.exists("__properties_version1.0"):
            raise ValueError("MSG requires a root MAPI property stream")
        return _parse_message_data(
            _read_scoped_message(ole, (), budget=budget),
            attachments_dir=attachments_dir,
            depth=depth,
            budget=budget,
            context=context,
            source_id=source_id,
        )


def _read_named_properties(ole, budget) -> dict[int, str]:
    """Resolve the selected named property from the root MS-OXMSG map once.

    Embedded messages share this mapping, but read their own property values.
    Unknown property sets/names never become metadata or body text.
    """
    prefix = "__nameid_version1.0"
    if not ole.exists(prefix):
        return {}
    paths = [[prefix, f"__substg1.0_{tag}"] for tag in ("00020102", "00030102", "00040102")]
    if not all(ole.exists(path) for path in paths):
        raise ValueError("Incomplete MSG named property mapping")
    guids, entries, strings = (_read_stream(ole, path, budget) for path in paths)
    if len(guids) % 16 or len(entries) % 8 or len(entries) // 8 > 0x8000:
        raise ValueError("Malformed MSG named property mapping")
    internet_headers = UUID("00020386-0000-0000-c000-000000000046").bytes_le
    selected = {}
    for offset in range(0, len(entries), 8):
        name_offset, guid_and_kind, index = struct.unpack_from("<IHH", entries, offset)
        guid_index = guid_and_kind >> 1
        if index != offset // 8 or not guid_index:
            raise ValueError("Malformed MSG named property index")
        if guid_index < 3:
            continue  # PS_MAPI / PS_PUBLIC_STRINGS are not Internet headers.
        guid_offset = (guid_index - 3) * 16
        if guid_offset + 16 > len(guids):
            raise ValueError("Invalid MSG named property GUID index")
        if not guid_and_kind & 1 or guids[guid_offset:guid_offset + 16] != internet_headers:
            continue
        if name_offset % 4 or name_offset + 4 > len(strings):
            raise ValueError("Invalid MSG named property string offset")
        length = struct.unpack_from("<I", strings, name_offset)[0]
        start = name_offset + 4
        if length % 2 or start + length > len(strings):
            raise ValueError("Invalid MSG named property string length")
        try:
            name = strings[start:start + length].decode("utf-16-le", errors="strict")
        except UnicodeError as exc:
            raise ValueError("Invalid MSG named property string") from exc
        if name.casefold() == "content-class":
            if selected:
                raise ValueError("Ambiguous MSG named Content-Class property")
            selected[0x8000 + index] = "ContentClass"
    return selected


def _read_scoped_properties(ole, prefix: tuple[str, ...], header_size: int, budget, encoding="cp1252", named_properties=None) -> dict:
    """Read selected MAPI properties from exactly one storage, retaining binary bytes."""
    from msg_parser.properties import PROPS_ID_MAP

    properties = {}
    fixed_path = [*prefix, "__properties_version1.0"]
    if ole.exists(fixed_path):
        fixed = _read_stream(ole, fixed_path, budget)
        if len(fixed) < header_size or (len(fixed) - header_size) % 16:
            raise ValueError("Malformed MAPI property stream")
        for offset in range(header_size, len(fixed) - 15, 16):
            tag, _flags, value = struct.unpack_from("<II8s", fixed, offset)
            name = PROPS_ID_MAP.get(f"0x{tag >> 16:04X}", {}).get("name")
            if not name:
                continue
            if tag & 0xFFFF == 0x0003:
                properties[name] = struct.unpack_from("<I", value)[0]
            elif tag & 0xFFFF == 0x0040:
                ticks = struct.unpack_from("<Q", value)[0]
                try:
                    properties[name] = datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=ticks // 10)
                except OverflowError:
                    pass
    codepage = properties.get("MessageCodepage") or properties.get("InternetCodepage")
    if codepage:
        candidate = {65001: "utf-8", 28591: "iso-8859-1"}.get(codepage, f"cp{codepage}")
        try:
            codecs.lookup(candidate)
        except LookupError:
            pass
        else:
            encoding = candidate
    selected = {
        "Subject", "Body", "Html", "RtfCompressed", "MessageClass", "TransportMessageHeaders",
        "SenderSmtpAddress", "SenderRepresentingSmtpAddress", "DisplayTo", "DisplayCc",
        "SenderName", "SenderAddressType", "SenderEmailAddress", "SentRepresentingName",
        "SentRepresentingAddressType", "SentRepresentingEmailAddress", "SentRepresentingSmtpAddress",
        "InternetMessageId", "InReplyToId", "InternetReferences", "AttachLongFilename",
        "AttachFilename", "AttachContentId", "DisplayName", "SmtpAddress", "EmailAddress",
        "ContentClass",
    }
    for path in ole.listdir():
        if tuple(path[:-1]) != prefix or not path[-1].startswith("__substg1.0_"):
            continue
        tag = path[-1][12:]
        if len(tag) != 8:
            continue
        name = PROPS_ID_MAP.get(f"0x{tag[:4].upper()}", {}).get("name")
        if named_properties and tag[4:].upper() == "001F":
            try:
                name = named_properties.get(int(tag[:4], 16), name)
            except ValueError:
                continue
        if name not in selected:
            continue
        if name == "RtfCompressed":
            # Presence is enough to diagnose a missing plain/HTML alternative.
            # Do not open or decompress this unqualified binary format.
            properties[name] = True
            continue
        raw = _read_stream(ole, path, budget)
        kind = tag[4:].upper()
        if kind == "001F":
            properties[name] = _decode_property(raw, "utf-16-le", properties).rstrip("\0")
        elif kind == "001E" and name not in properties:
            properties[name] = _decode_property(raw, encoding, properties).rstrip("\0")
        elif kind == "0102":
            properties[name] = _decode_property(raw, encoding, properties) if name == "Html" else raw
    properties["_encoding"] = encoding
    return properties


def _decode_property(raw: bytes, encoding: str, properties: dict) -> str:
    try:
        return raw.decode(encoding, errors="strict")
    except UnicodeError:
        properties["_decode_recovered"] = True
        return raw.decode(encoding, errors="replace")


def _read_scoped_message(ole, prefix: tuple[str, ...], *, budget, named_properties=None) -> dict:
    if named_properties is None:
        named_properties = _read_named_properties(ole, budget)
    properties = _read_scoped_properties(ole, prefix, 24 if prefix else 32, budget, named_properties=named_properties)
    recipients = {}
    attachments = []
    for path in ole.listdir(streams=False, storages=True):
        if tuple(path[:-1]) != prefix:
            continue
        if path[-1].startswith("__recip_version1.0_"):
            if len(recipients) >= MAX_ATTACHMENT_NODES:
                raise ValueError("MSG recipient count exceeds extraction limit")
            recipients[path[-1]] = _read_scoped_properties(ole, tuple(path), 8, budget, properties["_encoding"])
        elif path[-1].startswith("__attach_version1.0_"):
            # Keep only a handle. No property or payload in this storage may
            # be opened until its node/depth/byte admission has been checked.
            # Keep at most the currently admissible handles plus one sentinel
            # for a bounded remainder marker. Descendants may consume this
            # shared budget before their parent's later siblings are visited.
            if len(attachments) <= budget.remaining_nodes:
                attachments.append(_MsgAttachment(ole, tuple(path), properties["_encoding"], named_properties))
    return {"properties": properties, "recipients": recipients, "attachments": attachments,
            "_body_format": "plain" if properties.get("Body") else "html", "_native_strings": True}


def _parse_native_attachment(handle, *, attachments_dir, index, depth, budget, context, parent_source_id, images=None):
    att = Attachment(name=f"attachment-{index}", prog_id="", caption="", data=b"")
    content_id = ""

    def skipped(status, note, warning):
        if images is not None:
            images.register(content_id, None)
        if context is not None:
            att.source_id = context.add_child(parent_source_id, att.name, "attachment")
            context.warn(att.source_id, warning)
        return [marker_block(att, note=note, extraction_status=status)]

    if not budget.reserve_node():
        return skipped("skipped_count", "превышен лимит количества вложений", "attachment_count_exceeded")
    if depth >= MAX_ATTACHMENT_DEPTH:
        return skipped("skipped_depth", "превышена глубина вложенности", "attachment_depth_exceeded")
    try:
        properties = _read_scoped_properties(handle.ole, handle.path, 8, budget, handle.encoding)
        content_id = str(properties.get("AttachContentId") or "")
        att.name = _attachment_name(properties, index)
        # Filename/property corruption belongs to the containing message;
        # the child source is registered later by its admitted extraction.
        if properties.get("_decode_recovered") and context is not None:
            context.warn(parent_source_id, "mail_decode_recovered")
        method = properties.get("AttachMethod")
        if method not in {None, 1, 5}:
            return skipped("unsupported", "способ хранения вложения не поддерживается", "unsupported_attachment_method")
        child_path = [*handle.path, "__substg1.0_3701000D"]
        if method in {None, 5} and handle.ole.exists(child_path):
            if images is not None:
                images.register(content_id, None)
            return _parse_embedded_message(
                lambda: _read_scoped_message(handle.ole, tuple(child_path), budget=budget, named_properties=handle.named_properties),
                name=att.name, attachments_dir=attachments_dir, index=index, depth=depth,
                budget=budget, context=context, parent_source_id=parent_source_id, _node_reserved=True,
            )
        data_path = [*handle.path, "__substg1.0_37010102"]
        if method == 5 or not handle.ole.exists(data_path):
            return skipped("unsupported", "способ хранения вложения не поддерживается", "unsupported_attachment_method")
        # process_embedded charges actual payload bytes once. The pre-read
        # check still enforces both declared size and the remaining budget.
        payload = _read_stream(handle.ole, data_path, budget, consume=False)
        extracted = process_embedded(
            payload, att.name, attachments_dir=attachments_dir, index=index, depth=depth,
            budget=budget, context=context, parent_source_id=parent_source_id, _node_reserved=True,
            inline_image=bool(content_id),
        )
        if images is not None:
            images.register(content_id, extracted[0] if extracted else None)
        return extracted
    except _MsgSizeLimit:
        return skipped("skipped_size", "превышен лимит размера вложений", "attachment_size_exceeded")
    except (OSError, ValueError, struct.error) as exc:
        if is_storage_full_error(exc):
            raise
        return skipped("skipped_parse", "не удалось извлечь вложение", "attachment_parse_failed")


def _parse_message_data(
    message_data: dict,
    *,
    attachments_dir: str | Path | None,
    depth: int,
    budget,
    context,
    source_id: str,
) -> list[Block]:
    """Рендерит MSG, включая embedded-message substorage без ложного файла.

    В MSG ``AttachMethod=embedded message`` хранит второе сообщение как OLE
    substorage, а не как поток байтов. ``msg_parser`` предоставляет его как
    ``EmbeddedMessage`` (properties/recipients/attachments); разбираем эту
    структуру в том же рекурсивном дереве источников.
    """
    budget = budget or AttachmentBudget()
    properties = message_data.get("properties", message_data)
    body_format = message_data.get("_body_format")
    if body_format is None:
        body_format = "html" if "Html" in properties else "plain"
    body = properties.get("Html" if body_format == "html" else "Body") or ""
    if "Body" in message_data:
        body = message_data["Body"]
    repair_rtf = (not message_data.get("_native_strings") and body_format != "html"
                  and bool(properties.get("RtfCompressed")))
    body = _repair_rtf_mojibake(body, enabled=repair_rtf).strip()
    subject = _decoded_header(properties.get("Subject"), repair_rtf=repair_rtf)
    metadata = _message_metadata(properties, message_data.get("recipients"), repair_rtf=repair_rtf)
    message_class = str(metadata.get("message_class") or "").casefold()
    clear_signed = message_class.endswith(".smime.multipartsigned")
    if clear_signed:
        metadata["signature_verified"] = False
    if context is not None:
        context.update_metadata(source_id, metadata | {"subject": subject})
        if properties.get("_decode_recovered") or any(
            recipient.get("_decode_recovered") for recipient in (message_data.get("recipients") or {}).values()
            if isinstance(recipient, Mapping)
        ):
            context.warn(source_id, "mail_decode_recovered")
    blocks: list[Block] = [Block("heading", subject, level=1, meta=metadata)] if subject else []
    warning = None
    if metadata.get("content_class", "").casefold() == "rpmsg.message" or message_class.endswith(".smime"):
        # IPM.Note.SMIME can also represent opaque signatures; do not label
        # every such object 'encrypted' or treat fallback text as its body.
        warning = "protected_mail"
    elif message_class and not (
        message_class in {"ipm.note", "ipm.post"}
        or message_class.startswith(("ipm.note.", "ipm.post.", "report."))
    ):
        warning = "unsupported_mail_class"
    if warning:
        if context is not None:
            context.warn(source_id, warning)
        for block in blocks:
            block.meta.setdefault("source_id", source_id)
        return blocks
    if not body and properties.get("RtfCompressed") and context is not None:
        context.warn(source_id, "unsupported_rtf_body")
    images = ContentIdImages(context, source_id)
    heading_count = len(blocks)
    attachments = message_data.get("attachments") or []
    if isinstance(attachments, Mapping):
        attachments = attachments.values()
    for index, attachment in enumerate(attachments):
        if budget.remaining_nodes <= 0:
            blocks.append(attachment_count_marker(context, source_id))
            break
        if isinstance(attachment, _MsgAttachment):
            blocks.extend(_parse_native_attachment(
                attachment, attachments_dir=attachments_dir, index=index, depth=depth + 1,
                budget=budget, context=context, parent_source_id=source_id,
                images=images,
            ))
            continue
        payload = attachment.get("AttachDataObject") if isinstance(attachment, dict) else None
        content_id = str(attachment.get("AttachContentId") or "") if isinstance(attachment, dict) else ""
        name = _attachment_name(attachment, index)
        if not isinstance(payload, bytes):
            images.register(content_id, None)
            embedded = attachment.get("EmbeddedMessage") if isinstance(attachment, dict) else None
            if isinstance(embedded, dict):
                blocks.extend(
                    _parse_embedded_message(
                        embedded,
                        name=name,
                        attachments_dir=attachments_dir,
                        index=index,
                        depth=depth + 1,
                        budget=budget,
                        context=context,
                        parent_source_id=source_id,
                    )
                )
            continue
        extracted = process_embedded(
                payload,
                name,
                attachments_dir=attachments_dir,
                index=index,
                depth=depth + 1,
                budget=budget,
                context=context,
                parent_source_id=source_id,
                inline_image=bool(content_id),
            )
        images.register(content_id, extracted[0] if extracted else None)
        blocks.extend(extracted)
    if body:
        if body_format == "html":
            parser = _MailHTML(images=images)
            parser.feed(body)
            parser.close()
            body_blocks = parser.blocks()
        else:
            body_blocks = [Block("paragraph", escape_mail_text(body))]
        blocks[heading_count:heading_count] = body_blocks
    for block in blocks:
        block.meta.setdefault("source_id", source_id)
    return blocks


def _parse_embedded_message(
    embedded,
    *,
    name: str,
    attachments_dir,
    index: int,
    depth: int,
    budget,
    context,
    parent_source_id: str,
    _node_reserved: bool = False,
) -> list[Block]:
    effective_budget = budget or AttachmentBudget()
    att = Attachment(name=name, prog_id="", caption="", data=b"", kind="mail")
    if context is not None:
        att.source_id = context.add_child(parent_source_id, name, "mail")
    if not _node_reserved and not effective_budget.reserve_node():
        return [marker_block(att, note="превышен лимит количества вложений", extraction_status="skipped_count")]
    if depth >= MAX_ATTACHMENT_DEPTH:
        return [marker_block(att, note="превышена глубина вложенности", extraction_status="skipped_depth")]
    try:
        blocks = _parse_message_data(
            embedded() if callable(embedded) else embedded,
            attachments_dir=attachments_dir,
            depth=depth,
            budget=effective_budget,
            context=context,
            source_id=att.source_id or parent_source_id,
        )
    except (OSError, ValueError, struct.error) as exc:
        if is_storage_full_error(exc):
            raise
        size_limit = isinstance(exc, _MsgSizeLimit)
        if context is not None:
            context.warn(att.source_id, "attachment_size_exceeded" if size_limit else "mail_parse_failed")
        return [marker_block(
            att, note="превышен лимит размера вложений" if size_limit else "не удалось извлечь письмо",
            extraction_status="skipped_size" if size_limit else "skipped_parse",
        )]
    for block in blocks:
        block.meta.setdefault("from_attachment", True)
        block.meta.setdefault("attachment_name", name)
    return [marker_block(att, parsed=True, extraction_status="parsed")] + blocks


def _attachment_name(attachment, index: int) -> str:
    if not isinstance(attachment, dict):
        return f"attachment-{index}"
    return str(
        attachment.get("AttachLongFilename")
        or attachment.get("AttachFilename")
        or attachment.get("DisplayName")
        or f"attachment-{index}"
    )


def _message_metadata(properties: dict, recipients, *, repair_rtf: bool = False) -> dict:
    sender = _string_value(properties.get("Sender") or properties.get("SenderSmtpAddress"), repair_rtf=repair_rtf)
    to = _address_values(properties.get("To") or properties.get("DisplayTo"), repair_rtf=repair_rtf)
    cc = _address_values(properties.get("Cc") or properties.get("DisplayCc"), repair_rtf=repair_rtf)
    if isinstance(recipients, dict):
        if not to:
            to = _recipient_addresses(recipients, "TO", repair_rtf=repair_rtf)
        if not cc:
            cc = _recipient_addresses(recipients, "CC", repair_rtf=repair_rtf)
    headers = Parser(policy=policy.default).parsestr(
        str(properties.get("TransportMessageHeaders") or ""), headersonly=True,
    )
    selected_headers = {}
    for name, value in headers.raw_items():
        if name.lower() in {"date", "in-reply-to", "references", "content-class", "from", "to", "cc"}:
            selected_headers.setdefault(name.lower(), value.strip())
    if not sender:
        sender = _string_value(selected_headers.get("from"), repair_rtf=repair_rtf)
    if not sender:
        sender = _native_sender(properties, "Sender", repair_rtf=repair_rtf)
    if not to:
        to = _address_values(selected_headers.get("to"), repair_rtf=repair_rtf)
    if not cc:
        cc = _address_values(selected_headers.get("cc"), repair_rtf=repair_rtf)
    date = (properties.get("ClientSubmitTime") or properties.get("SentAt")
            or selected_headers.get("date") or properties.get("DeliverTime"))
    content_classes = [properties.get("ContentClass"), selected_headers.get("content-class")]
    content_class = next((value for value in content_classes if value and value.casefold() == "rpmsg.message"),
                         next((value for value in content_classes if value), None))
    provenance = {
        "date_raw": selected_headers.get("date") or (date.isoformat() if isinstance(date, datetime) else date),
        "message_class": _decoded_header(properties.get("MessageClass"), repair_rtf=repair_rtf),
        "in_reply_to": properties.get("InReplyToId") or selected_headers.get("in-reply-to"),
        "references": properties.get("InternetReferences") or selected_headers.get("references"),
        "content_class": content_class,
        "sender_address_type": properties.get("SenderAddressType"),
        "representing_sender": _native_sender(properties, "SentRepresenting", repair_rtf=repair_rtf),
        "representing_sender_address_type": properties.get("SentRepresentingAddressType"),
    }
    return {
        "mail": True,
        "sender": sender,
        "to": [address for address in to if address],
        "cc": [address for address in cc if address],
        "sent_at": _iso_value(date),
        "message_id": str(properties.get("MessageId") or properties.get("InternetMessageId") or "").strip(),
        **{key: str(value).strip() for key, value in provenance.items() if value},
    }


def _native_sender(properties: dict, prefix: str, *, repair_rtf: bool) -> str:
    name = _string_value(properties.get(prefix + "Name"), repair_rtf=repair_rtf)
    address = _string_value(properties.get(prefix + "SmtpAddress") or properties.get(prefix + "EmailAddress"),
                            repair_rtf=repair_rtf)
    return f"{name} <{address}>" if name and address else name or address


def _recipient_addresses(recipients: dict, kind: str, *, repair_rtf: bool) -> list[str]:
    """Only explicit To/Cc roles may populate public metadata.

    Embedded-message recipients need not have DisplayTo/DisplayCc. Their table
    may include Bcc, ReplyTo, and untyped rows; absence of a role is not To.
    PidTagRecipientType uses 1=To, 2=Cc, 3=Bcc; msg_parser also emits TO/CC/BCC.
    """
    expected_number = 1 if kind == "TO" else 2
    addresses = []
    for item in recipients.values():
        if not isinstance(item, Mapping):
            continue
        role = item.get("RecipientType")
        matches = (
            (type(role) is int and role == expected_number)
            or (isinstance(role, str) and role.strip().upper() == kind)
        )
        if matches:
            address = _string_value(item.get("SmtpAddress") or item.get("EmailAddress"), repair_rtf=repair_rtf)
            if address:
                addresses.append(address)
    return addresses


def _string_value(value, *, repair_rtf: bool = False) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(_decoded_header(item, repair_rtf=repair_rtf) for item in value if item)
    return _decoded_header(value, repair_rtf=repair_rtf)


def _address_values(value, *, repair_rtf: bool = False) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [_decoded_header(item, repair_rtf=repair_rtf) for item in value if item]
    return [_decoded_header(part, repair_rtf=repair_rtf) for part in str(value or "").split(",") if part.strip()]


def _decoded_header(value, *, repair_rtf: bool = False) -> str:
    raw = _repair_rtf_mojibake(value, enabled=repair_rtf).strip()
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except (LookupError, UnicodeError, ValueError):
        return raw


def _repair_rtf_mojibake(value, *, enabled: bool = False) -> str:
    """Исправляет ошибочную UTF-16 интерпретацию bytes из msg_parser RTF path.

    `compressed_rtf` отдаёт text bytes, а msg_parser присваивает их ``body``
    без нормализации кодировки. В ANSI MSG это выглядит как последовательность
    CJK-символов (``Dear`` → ``敄牡``). Восстанавливаем bytes только если
    исходная строка действительно состоит преимущественно из этого артефакта,
    а восстановленный UTF-8/CP1252 текст печатаем. Настоящий Unicode MSG не
    проходит такую эвристику и остаётся неизменным.
    """
    raw = str(value or "")
    if not enabled:
        return raw
    if not raw:
        return ""
    opaque_ratio = sum(0x3400 <= ord(char) <= 0x9FFF for char in raw) / len(raw)
    if opaque_ratio < 0.3:
        return raw
    encoded = raw.encode("utf-16-le", errors="surrogatepass")
    for encoding in ("utf-8", "cp1252"):
        try:
            candidate = encoded.decode(encoding)
        except UnicodeDecodeError:
            continue
        printable = sum(char.isprintable() or char in "\r\n\t" for char in candidate) / max(len(candidate), 1)
        ascii_ratio = sum(ord(char) < 128 for char in candidate) / max(len(candidate), 1)
        if printable >= 0.95 and ascii_ratio >= 0.7:
            return candidate
    return raw


def _iso_value(value) -> str | None:
    if isinstance(value, datetime):
        # msg_parser returns naive datetime for MAPI FILETIME (defined as UTC).
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    # Header strings without an explicit timezone stay unknown.
    return _sent_at(value)
