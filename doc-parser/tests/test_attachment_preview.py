import zipfile
from email.message import EmailMessage
from pathlib import Path

import pytest


def preview(path, kind):
    from docparser.attachment_preview import preview_attachment
    return preview_attachment(path, kind=kind)


def test_docx_preview_reads_only_main_text_and_caps_output(tmp_path):
    path=tmp_path/'document.docx'
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('word/document.xml','<document><body>'+''.join('<p><t>'+('Текст '*200)+'</t></p>' for _ in range(50))+'</body></document>')
        archive.writestr('word/embeddings/bad.bin',b'never parse')
    result=preview(path,'docx')
    assert result['kind']=='text' and 'Текст' in result['text']
    assert len(result['text'])<=20000 and result['limited']


def test_docx_preview_prefers_real_core_title(tmp_path):
    path=tmp_path/'object.docx'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('docProps/core.xml','<core xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Настоящий заголовок</dc:title></core>')
        z.writestr('word/document.xml','<document><p><t>First paragraph</t></p></document>')
    assert preview(path,'docx')['title']=='Настоящий заголовок'


def test_excel_title_prefers_workbook_properties_then_named_sheet(tmp_path):
    from openpyxl import Workbook
    path=tmp_path/'object.xlsx';book=Workbook();book.active.title='Расчёт налога';book.active.append(['data'])
    book.properties.title='Регистр налога';book.save(path)
    assert preview(path,'xlsx')['title']=='Регистр налога'
    book.properties.title=None;book.save(path)
    result=preview(path,'xlsx')
    assert result['title']=='Расчёт налога' and result['title_source']=='sheet'
    book.active.title='Лист1';book.save(path)
    assert preview(path,'xlsx')['title'] is None


def test_mail_preview_never_includes_attached_message_body(tmp_path):
    outer=EmailMessage();outer['Subject']='Тема';outer['From']='sender@example.org';outer.set_content('Outer body')
    nested=EmailMessage();nested.set_content('CHILD_BODY_CANARY')
    outer.add_attachment(nested)
    path=tmp_path/'mail.eml';path.write_bytes(outer.as_bytes())
    result=preview(path,'eml')
    assert result['metadata']['subject']=='Тема'
    assert 'Outer body' in result['text'] and 'CHILD_BODY_CANARY' not in result['text']


def test_msg_preview_does_not_extract_attachments():
    path=Path(__file__).parent/'fixtures/mail/synthetic-unicode-attachment.msg'
    result=preview(path,'msg')
    assert result['kind']=='text' and result['metadata']['subject']
    assert isinstance(result['text'],str)


@pytest.mark.parametrize('kind',['zip','7z','rar'])
def test_archive_preview_lists_without_extracting(tmp_path,monkeypatch,kind):
    from docparser.archive_reader import ArchiveReader
    monkeypatch.setattr(ArchiveReader,'extract_selected',lambda *a,**k:pytest.fail('preview must not extract'))
    path=tmp_path/('data.'+kind)
    if kind=='zip':
        with zipfile.ZipFile(path,'w') as z:z.writestr('nested/file.txt','payload')
    elif kind=='7z':
        import py7zr
        with py7zr.SevenZipFile(path,'w') as z:z.writestr(b'payload','nested/file.txt')
    else:
        path=Path(__file__).parent/'fixtures/archives/rar5-solid.rar'
    result=preview(path,kind)
    assert result['kind']=='archive' and result['entries']
    assert all('name' in entry and 'size' in entry for entry in result['entries'])


def test_archive_preview_enforces_safe_names_and_entry_sample(tmp_path):
    safe=tmp_path/'many.zip'
    with zipfile.ZipFile(safe,'w') as z:
        for i in range(120):z.writestr(str(i),'data')
    result=preview(safe,'zip')
    assert len(result['entries'])==100 and result['limited']
    unsafe=tmp_path/'unsafe.zip'
    with zipfile.ZipFile(unsafe,'w') as z:z.writestr('../outside','data')
    with pytest.raises(ValueError):preview(unsafe,'zip')


def test_pdf_preview_caps_pages_without_reading_images_or_attachments(tmp_path,monkeypatch):
    from docparser import pdf_provider
    class Document:
        def page_count(self):return 40
        def extract_text(self,index):assert index<3;return 'page '+str(index)
        def close(self):self.closed=True
    document=Document()
    monkeypatch.setattr(pdf_provider,'get_pdf_provider',lambda:type('Provider',(),{'open':lambda self,path:document})())
    (tmp_path/'unused.pdf').write_bytes(b'%PDF-mocked')
    result=preview(tmp_path/'unused.pdf','pdf')
    assert result['limited'] and 'page 2' in result['text'] and document.closed


def test_unsupported_preview_rejected(tmp_path):
    with pytest.raises(ValueError):preview(tmp_path/'unsafe.exe','exe')
