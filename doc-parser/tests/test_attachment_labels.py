import struct
import zipfile

import pytest


def emf(*lines):
    header = bytearray(88)
    struct.pack_into('<II', header, 0, 1, 88)
    header[40:44] = b' EMF'
    records = [header]
    for line in lines:
        text = line.encode('utf-16-le')
        record = bytearray(76 + len(text) + (-len(text) % 4))
        struct.pack_into('<II', record, 0, 84, len(record))
        struct.pack_into('<II', record, 44, len(text) // 2, 76)
        record[76:76 + len(text)] = text
        records.append(record)
    return b''.join(records)


def word_icons(path, names):
    objects, rels = [], []
    with zipfile.ZipFile(path, 'w') as archive:
        for i, name in enumerate(names):
            objects.append(f'<w:object><v:shape><v:imagedata r:id="icon{i}"/></v:shape><o:OLEObject r:id="file{i}"/></w:object>')
            rels.append(f'<Relationship Id="icon{i}" Target="media/icon{i}.emf"/>')
            archive.writestr(f'word/media/icon{i}.emf', emf(*name))
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">' + ''.join(objects) + '</w:document>')
        archive.writestr('word/_rels/document.xml.rels', '<Relationships>' + ''.join(rels) + '</Relationships>')
    return path


@pytest.mark.parametrize('suffix', ['xlsx', 'docx', 'pdf', 'msg', 'eml', 'zip', '7z', 'rar'])
def test_icon_labels_are_format_independent_and_join_wrapped_lines(tmp_path, suffix):
    from docparser.attachment_labels import word_attachment_labels
    name = f'Исходное имя.{suffix}'
    path = word_icons(tmp_path / 'root.docx', [('Исходное ', f'имя.{suffix}'), ('Другой файл.docx',)])
    assert word_attachment_labels(path) == {
        'ooxml:word/document.xml:file0:0': name,
        'ooxml:word/document.xml:file1:0': 'Другой файл.docx',
    }


def test_malformed_emf_is_ignored_and_paths_are_not_disclosed(tmp_path):
    from docparser.attachment_labels import emf_caption, word_attachment_labels
    data = bytearray(emf('name.pdf'))
    struct.pack_into('<I', data, 88 + 48, 0xffffffff)
    assert emf_caption(data) is None
    assert emf_caption(emf('name.pdf')[:-1]) is None
    assert emf_caption(b'not EMF') is None
    path = word_icons(tmp_path / 'root.docx', [('C:\\private\\Original.pdf',)])
    assert list(word_attachment_labels(path).values()) == ['Original.pdf']


def test_external_icons_are_never_opened(tmp_path):
    from docparser.attachment_labels import word_attachment_labels
    path = word_icons(tmp_path / 'root.docx', [('Original.pdf',)])
    with zipfile.ZipFile(path, 'a') as archive:
        archive.writestr('word/_rels/document.xml.rels', '<Relationships><Relationship Id="icon0" Target="https://example.org/icon.emf" TargetMode="External"/></Relationships>')
    assert word_attachment_labels(path) == {}


def test_large_word_xml_can_supply_small_icon_labels(tmp_path):
    from docparser.attachment_labels import word_attachment_labels
    path = word_icons(tmp_path / 'root.docx', [('Original.pdf',)])
    with zipfile.ZipFile(path) as archive:
        members = {n: archive.read(n) for n in archive.namelist()}
    members['word/document.xml'] = members['word/document.xml'].replace(b'</w:document>', b' ' * (9 * 1024 * 1024) + b'</w:document>')
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    assert list(word_attachment_labels(path).values()) == ['Original.pdf']
