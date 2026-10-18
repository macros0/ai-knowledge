"""Bounded, read-only attachment samples. Never traverse embedded payloads."""
import re
import zipfile
from pathlib import Path

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

PREVIEW_KINDS = frozenset({'xlsx', 'docx', 'pdf', 'eml', 'msg', 'zip', '7z', 'rar'})
MAX_TEXT = 20000
MAX_XML_BYTES = 2 * 1024 * 1024


def office_title(archive, kind, *, charge=None):
    """Read bounded metadata only; worksheet names are labeled separately."""
    def read(name):
        data = archive.read(name)
        if charge:
            charge(len(data))
        return data
    try:
        if 'docProps/core.xml' in archive.namelist() and archive.getinfo('docProps/core.xml').file_size <= 65536:
            root = ET.fromstring(read('docProps/core.xml'))
            title = root.find('{http://purl.org/dc/elements/1.1/}title')
            if title is not None and (title.text or '').strip():
                return {'title': ' '.join(title.text.split())[:500], 'title_source': 'properties'}
        if kind == 'xlsx' and archive.getinfo('xl/workbook.xml').file_size <= 1024 * 1024:
            root = ET.fromstring(read('xl/workbook.xml'))
            for element in root.iter():
                name = element.get('name', '').strip()
                if element.tag.rsplit('}', 1)[-1] == 'sheet' and name and not re.fullmatch(r'(?:sheet|лист)\s*\d*', name, re.IGNORECASE):
                    return {'title': name[:500], 'title_source': 'sheet'}
    except (KeyError, ET.ParseError, DefusedXmlException):
        pass
    return {'title': None, 'title_source': None}


class _SampleLimit(ValueError):
    pass


class _XmlSample:
    def __init__(self, stream):
        self.stream, self.remaining = stream, MAX_XML_BYTES

    def read(self, size=-1):
        data = self.stream.read(min(65536, size if size >= 0 else 65536, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise _SampleLimit()
        return data


def _docx(path):
    parts, count, limited = [], 0, False
    with zipfile.ZipFile(path) as archive, archive.open('word/document.xml') as stream:
        metadata = office_title(archive, 'docx')
        try:
            for _, element in ET.iterparse(_XmlSample(stream), events=('end',)):
                if element.tag.rsplit('}', 1)[-1] != 'p':
                    continue
                text = ''.join(e.text or '' for e in element.iter() if e.tag.rsplit('}', 1)[-1] in {'t', 'tab', 'br'})
                if not metadata['title'] and any(e.tag.rsplit('}', 1)[-1] == 'pStyle' and any(str(v).lower().startswith(('heading', 'title', 'заголовок')) for v in e.attrib.values()) for e in element.iter()) and text.strip():
                    metadata = {'title': text.strip()[:500], 'title_source': 'heading'}
                parts.append(text)
                count += len(text) + 1
                element.clear()
                if count > MAX_TEXT:
                    limited = True
                    break
        except _SampleLimit:
            limited = True
    return '\n'.join(parts), limited, metadata


def _pdf(path):
    from docparser.pdf_provider import get_pdf_provider
    document = get_pdf_provider().open(path)
    try:
        count = document.page_count()
        parts = []
        for index in range(min(3, count)):
            parts.append(document.extract_text(index))
            if sum(map(len, parts)) > MAX_TEXT:
                break
        return '\n\n'.join(parts), count > len(parts), {'title': getattr(document, 'document_title', lambda: None)(), 'title_source': 'properties'}
    finally:
        document.close()


def _mail(path, kind):
    from docparser.embedded import AttachmentBudget
    if kind == 'eml':
        from docparser.eml_parser import (
            MAX_MAIL_HEADER_BYTES,
            MAX_MIME_DEPTH,
            _body_blocks,
            _is_encrypted_mail,
        )
        from docparser.mime_wire import parse_wire_message
        message = parse_wire_message(path.read_bytes(), AttachmentBudget(), header_limit=MAX_MAIL_HEADER_BYTES, depth_limit=MAX_MIME_DEPTH)
        if _is_encrypted_mail(message):
            raise ValueError('Encrypted mail')
        metadata = {key: str(message.get(header) or '') for key, header in
                    [('subject', 'Subject'), ('sender', 'From'), ('to', 'To'), ('date', 'Date')]}
        text = '\n\n'.join(block.text for block in _body_blocks(message))
    else:
        import olefile

        from docparser.eml_parser import _MailHTML
        from docparser.msg_parser import _message_metadata, _read_scoped_message
        with olefile.OleFileIO(str(path)) as ole:
            if not ole.exists('__properties_version1.0'):
                raise ValueError('Missing MSG property stream')
            message = _read_scoped_message(ole, (), budget=AttachmentBudget(total=4 * 1024 * 1024))
            properties = message['properties']
            if 'SMIME' in str(properties.get('MessageClass', '')).upper() or str(properties.get('ContentClass', '')).casefold() == 'rpmsg.message':
                raise ValueError('Encrypted mail')
            fields = _message_metadata(properties, message['recipients'])
            metadata = {'subject': str(properties.get('Subject') or ''), 'sender': fields.get('sender', ''),
                        'to': ', '.join(fields.get('to', [])), 'date': fields.get('sent_at') or ''}
            text = properties.get('Body') or ''
            if not text and properties.get('Html'):
                parser = _MailHTML()
                parser.feed(properties['Html']); parser.close()
                text = '\n\n'.join(block.text for block in parser.blocks())
    return text, False, {key: str(value)[:500] for key, value in metadata.items()}


def preview_attachment(path, *, kind, sheet_index=0, helper_path=None):
    path = Path(path)
    if kind not in PREVIEW_KINDS:
        raise ValueError('Unsupported preview format')
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError('Preview input exceeds limit')
    if kind == 'xlsx':
        from docparser.xlsx_preview import preview_xlsx
        result = preview_xlsx(path, sheet_index=sheet_index)
        with zipfile.ZipFile(path) as archive:
            result.update(office_title(archive, kind))
        return result
    if kind in {'zip', '7z', 'rar'}:
        from docparser.archive_reader import open_archive
        from docparser.attachment_policy import TraversalLimits
        with open_archive(path, format=kind, limits=TraversalLimits(), helper_path=helper_path) as archive:
            entries = list(archive.entries())
            return {'kind': 'archive', 'limited': len(entries) > 100,
                    'entries': [{'name': entry.display_path[:1000], 'size': entry.size,
                                 'directory': entry.is_dir, 'encrypted': entry.encrypted} for entry in entries[:100]]}
    if kind == 'docx':
        text, limited, metadata = _docx(path)
    elif kind == 'pdf':
        text, limited, metadata = _pdf(path)
    else:
        text, limited, metadata = _mail(path, kind)
    title = metadata.pop('title', metadata.get('subject'))
    source = metadata.pop('title_source', 'subject' if metadata.get('subject') else None)
    return {'kind': 'text', 'format': kind, 'text': text[:MAX_TEXT], 'metadata': metadata,
            'title': title[:500] if title else None, 'title_source': source,
            'limited': limited or len(text) > MAX_TEXT}
