"""Bounded MIME structure with original byte ranges and deferred leaf decoding."""
from __future__ import annotations

import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser


def _skipped(status: str, warning: str) -> EmailMessage:
    part = EmailMessage(policy=policy.default)
    part.set_type("application/octet-stream")
    part["Content-Disposition"] = "attachment"
    part._wire_status = status
    part._wire_warning = warning
    return part


def _header_end(raw: bytes, start: int, end: int, limit: int) -> int:
    # Empty MIME headers consist of a single blank line.
    if raw[start:start + 2] == b"\r\n":
        return start + 2
    if raw[start:start + 1] in {b"\n", b"\r"}:
        return start + 1
    match = re.compile(rb"\r\n\r\n|\n\n|\r\r").search(raw, start, min(end, start + limit + 4))
    if match is None:
        if end - start > limit:
            raise ValueError("MIME header exceeds extraction limit")
        # Header-only entities are legitimate. A malformed header is diagnosed
        # by the header parser below instead of becoming an invented body.
        return end
    if match.start() - start > limit:
        raise ValueError("MIME header exceeds extraction limit")
    return match.end()


def _part_ranges(raw: bytes, start: int, end: int, boundary: str):
    try:
        delimiter = boundary.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("Invalid MIME boundary") from exc
    if not delimiter or len(delimiter) > 200:
        raise ValueError("Invalid MIME boundary")
    pattern = re.compile(rb"(?:\A|(?<=\n)|(?<=\r))--" + re.escape(delimiter) + rb"(--)?[ \t]*(?:\r\n|\n|\r|\Z)")
    child_start = None
    for match in pattern.finditer(raw, start, end):
        if child_start is not None:
            child_end = match.start()
            # RFC2046: the CRLF immediately before the boundary belongs to
            # that boundary, not to the preceding body's original bytes.
            if raw[max(child_start, child_end - 2):child_end] == b"\r\n":
                child_end -= 2
            elif raw[max(child_start, child_end - 1):child_end] in {b"\r", b"\n"}:
                child_end -= 1
            yield child_start, child_end
        if match.group(1):
            if child_start is None:
                raise ValueError("MIME multipart has no opening boundary")
            return
        child_start = match.end()
    raise ValueError("MIME multipart has no closing boundary")


def parse_wire_message(raw: bytes, budget, *, header_limit: int, depth_limit: int) -> EmailMessage:
    """Parse headers/containers only. Attached RFC822 messages remain raw leaves.

    Every descendant MIME part consumes the same node budget as MSG/attachments.
    The current message's node was already admitted by its caller (root is free).
    Exhaustion emits one marker for the unvisited remainder, not unlimited stubs.
    """
    def read_part(start, end, depth=0, default_type="text/plain"):
        if depth >= depth_limit:
            return _skipped("skipped_depth", "mime_depth_exceeded")
        header_end = _header_end(raw, start, end, header_limit)
        part = BytesParser(policy=policy.default).parsebytes(raw[start:header_end], headersonly=True)
        if part.defects:
            raise ValueError("Malformed MIME headers")
        part.set_default_type(default_type)
        part._wire_body = memoryview(raw)[header_end:end]
        if part.get_content_maintype() != "multipart":
            # Includes message/rfc822: its body is never parsed/serialized here.
            return part
        boundary = part.get_boundary()
        if boundary is None:
            raise ValueError("MIME multipart has no boundary")
        children = []
        default = "message/rfc822" if part.get_content_subtype() == "digest" else "text/plain"
        for child_start, child_end in _part_ranges(raw, header_end, end, boundary):
            if not budget.reserve_node():
                children.append(_skipped("skipped_count", "mime_count_exceeded"))
                break
            try:
                children.append(read_part(child_start, child_end, depth + 1, default))
            except ValueError:
                children.append(_skipped("skipped_parse", "mime_part_parse_failed"))
        part.set_payload(children)
        return part

    return read_part(0, len(raw))


def materialize_leaf(part: EmailMessage) -> None:
    """Populate email's decoding input only after the caller admitted the leaf."""
    wire = getattr(part, "_wire_body", None)
    if wire is not None and not part.is_multipart() and not getattr(part, "_wire_materialized", False):
        part.set_payload(wire.tobytes())
        part._wire_materialized = True
