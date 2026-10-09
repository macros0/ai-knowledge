"""Synthetic acceptance corpus for R1; structural limitations remain visible."""
import io
import zipfile

import pytest
from lxml import etree

from docparser.xlsx_parser import parse_xlsx
import tests.fixtures as fixtures


@pytest.mark.parametrize('width', [2, 5, 172, 316])
def test_wide_fixture_keeps_right_edge_and_repeated_rows(tmp_path, width):
    assert hasattr(fixtures, 'make_table_acceptance_xlsx'), 'acceptance workbook fixture is missing'
    source = fixtures.make_table_acceptance_xlsx(tmp_path / 'table.xlsx', width=width)
    tables = [b.text for b in parse_xlsx(source) if b.type == 'table']
    assert len(tables) == 2
    assert f'edge-{width - 1}' in tables[0]
    assert tables[0].count('repeat-key') == 2
    assert '0' in tables[1] and 'False' in tables[1]


def test_fixture_oracle_has_merge_formula_hidden_and_types(tmp_path):
    assert hasattr(fixtures, 'make_table_acceptance_xlsx'), 'acceptance workbook fixture is missing'
    source = fixtures.make_table_acceptance_xlsx(tmp_path / 'table.xlsx', width=172)
    ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(source) as archive:
        sheet = etree.fromstring(archive.read('xl/worksheets/sheet2.xml'))
    assert sheet.find('x:mergeCells/x:mergeCell', ns).get('ref') == 'A1:C1'
    assert sheet.find(".//x:c[@r='A4']/x:f", ns).text == '1+1'
    assert sheet.find(".//x:c[@r='B4']", ns).get('t') == 'e'
    assert sheet.find(".//x:row[@r='5']", ns).get('hidden') == '1'


def test_acceptance_xlsx_inside_docx(tmp_path):
    assert hasattr(fixtures, 'make_table_acceptance_xlsx'), 'acceptance workbook fixture is missing'
    source = fixtures.make_table_acceptance_xlsx(tmp_path / 'table.xlsx', width=316)
    docx = fixtures.make_docx_with_embedded_xlsx(tmp_path / 'embedded.docx', source.read_bytes())
    with zipfile.ZipFile(docx) as archive:
        embedded = archive.read('word/embeddings/embedded.xlsx')
    with zipfile.ZipFile(io.BytesIO(embedded)) as archive:
        assert 'xl/worksheets/sheet2.xml' in archive.namelist()
