"""Read Word's embedded-object icon captions without rendering or opening payloads."""
from __future__ import annotations

import posixpath
import struct
import zipfile
from collections import Counter

from defusedxml import ElementTree as ET

_RID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
_OLE = '{urn:schemas-microsoft-com:office:office}OLEObject'
_OBJECT = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}object'
_IMAGE = '{urn:schemas-microsoft-com:vml}imagedata'
_ICON_LIMIT = 512 * 1024


def emf_caption(data: bytes) -> str | None:
    """Only bounded literal text records; no drawing interpretation or OCR."""
    if len(data) < 88 or len(data) > _ICON_LIMIT or data[40:44] != b' EMF':
        return None
    if struct.unpack_from('<I', data)[0] != 1:
        return None
    offset, texts, chars = 0, [], 0
    for _ in range(4096):
        if offset == len(data):
            label = ' '.join(''.join(texts).split())
            # A caption may contain a source path. Never expose its directories.
            label = label.replace('\\', '/').rsplit('/', 1)[-1]
            return label if label and len(label) <= 500 else None
        if offset + 8 > len(data):
            return None
        kind, size = struct.unpack_from('<II', data, offset)
        if size < 8 or size % 4 or offset + size > len(data):
            return None
        if kind in {83, 84}:
            if size < 76:
                return None
            count, start = struct.unpack_from('<II', data, offset + 44)
            width = 2 if kind == 84 else 1
            chars += count
            if chars > 1000 or start < 76 or start + count * width > size:
                return None
            try:
                text = data[offset + start:offset + start + count * width].decode(
                    'utf-16-le' if width == 2 else 'cp1251')
            except UnicodeError:
                return None
            if any(ord(c) < 32 and c not in '\t\r\n' for c in text):
                return None
            texts.append(text)
        offset += size
    return None


def part_attachment_labels(archive, xml, relationships, part, *, charge=None):
    """Use the same locator/order as inventory, including repeated OLE rIds."""
    captions = {}
    for obj in xml.iter(_OBJECT):
        images = list(obj.iter(_IMAGE))
        objects = list(obj.iter(_OLE))
        # An ambiguous shared drawing must not assign another object's name.
        if len(objects) != 1 or len(images) != 1:
            continue
        target = relationships.get(images[0].get(_RID), '')
        if not target.startswith('word/media/') or '..' in target.split('/'):
            continue
        try:
            info = archive.getinfo(target)
            if info.file_size > _ICON_LIMIT:
                continue
            with archive.open(info) as stream:
                data = stream.read(_ICON_LIMIT + 1)
        except (KeyError, ValueError, OSError, zipfile.BadZipFile):
            continue
        if charge:
            charge(len(data))
        caption = emf_caption(data)
        if caption:
            captions[id(objects[0])] = caption
    counts, result = Counter(), {}
    for obj in xml.iter(_OLE):
        rid = obj.get(_RID)
        locator = f'ooxml:{part}:{rid}:{counts[rid]}'
        counts[rid] += 1
        if id(obj) in captions:
            result[locator] = captions[id(obj)]
    return result


def word_attachment_labels(path) -> dict[str, str]:
    remaining = 32 * 1024 * 1024

    def charge(count):
        nonlocal remaining
        remaining -= count
        if remaining < 0:
            raise ValueError('Label scan limit reached')

    def read(archive, name):
        info = archive.getinfo(name)
        if info.file_size > remaining:
            raise ValueError('Label scan limit reached')
        with archive.open(info) as stream:
            data = stream.read(remaining + 1)
        charge(len(data))
        return data

    result = {}
    with zipfile.ZipFile(path) as archive:
        parts = sorted(n for n in archive.namelist() if n == 'word/document.xml' or
                       n.startswith(('word/header', 'word/footer')) and n.endswith('.xml'))
        for part in parts[:32]:
            xml = ET.fromstring(read(archive, part))
            rel_path = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
            relationships = {}
            if rel_path in archive.namelist():
                for rel in ET.fromstring(read(archive, rel_path)):
                    if rel.get('TargetMode') != 'External':
                        target = posixpath.normpath(posixpath.join(posixpath.dirname(part), rel.get('Target', '')))
                        relationships[rel.get('Id')] = target.lstrip('/')
            result.update(part_attachment_labels(archive, xml, relationships, part, charge=charge))
    return result
