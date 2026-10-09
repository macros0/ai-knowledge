"""Acceptance checks use coordinates and typed values, never concept counts."""
from copy import deepcopy
import importlib.util
from pathlib import Path


def _probe():
    path = Path(__file__).parents[1] / 'test_scripts' / 'probe_tables.py'
    assert path.is_file(), 'table acceptance probe is missing'
    spec = importlib.util.spec_from_file_location('table_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_fails_on_one_missing_cell():
    baseline = {'schema_version': 1, 'cells': [
        {'source_id': 'root', 'sheet': 0, 'address': 'A2', 'value': '001'},
        {'source_id': 'root', 'sheet': 0, 'address': 'LD2', 'value': 'right edge'},
    ], 'concept_count': 2}
    after = deepcopy(baseline)
    after['cells'].pop()
    after['concept_count'] = 100
    report = _probe().compare_reports(baseline, after)
    assert report['passed'] is False
    assert report['gates']['cell_coverage']['missing'] == 1


def test_probe_preserves_every_failed_gate():
    before = {'schema_version': 1, 'cells': [
        {'source_id': 'root', 'sheet': 0, 'address': 'A2', 'value': 0},
    ], 'headers': [{'source_id': 'root', 'sheet': 0, 'value': 'Code'}]}
    after = {'schema_version': 1, 'cells': [
        {'source_id': 'root', 'sheet': 0, 'address': 'A2', 'value': False},
    ], 'headers': []}
    report = _probe().compare_reports(before, after)
    assert report['passed'] is False
    assert report['gates']['cell_coverage']['changed'] == 1
    assert report['gates']['header_coverage']['passed'] is False


def test_probe_unknown_evidence_is_not_success():
    assert not _probe().compare_reports({'schema_version': 1}, {'schema_version': 1})['passed']


def test_probe_parse_cli_uses_original_coordinates_and_unknown_headers(tmp_path):
    import hashlib
    import json
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(['Code', 'Value'])
    sheet.append(['001', False])
    sheet.append(['002', 0])
    source = tmp_path / 'input.xlsx'
    workbook.save(source)
    workbook.close()
    manifest = {'documents': [{'path': 'input.xlsx', 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                              'source_id': 'root/0', 'expected_cells': [
                                  {'source_id': 'root/0', 'sheet': 0, 'address': 'A2', 'value': '001'},
                                  {'source_id': 'root/0', 'sheet': 0, 'address': 'B2', 'value': 'False'},
                                  {'source_id': 'root/0', 'sheet': 0, 'address': 'B3', 'value': '0'},
                              ], 'expected_headers': [{'source_id': 'root/0', 'sheet': 0,
                                                       'value': ['Code', 'Value'], 'status': 'legacy_unverified'}]}]}
    manifest_path = tmp_path / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    output = tmp_path / 'report.json'
    probe = _probe()
    assert probe.main(['--manifest', str(manifest_path), '--output', str(output), '--mode', 'parse']) == 1
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['gates']['0']['gates']['cell_coverage']['passed']
    assert report['gates']['0']['gates']['header_correctness']['status'] == 'unknown'


def test_probe_cell_identity_includes_document():
    before = {'schema_version': 1, 'cells': [
        {'document_id': 'a', 'source_id': 'root/0', 'sheet': 0, 'address': 'A2', 'value': 'same'},
        {'document_id': 'b', 'source_id': 'root/0', 'sheet': 0, 'address': 'A2', 'value': 'same'},
    ], 'headers': [{'value': 'Code'}]}
    after = deepcopy(before)
    after['cells'].pop()
    report = _probe().compare_reports(before, after)
    assert not report['passed']
    assert report['gates']['cell_coverage']['missing'] == 1


def test_compare_preserves_failed_original_gates():
    before = {'schema_version': 1, 'cells': [
        {'source_id': 'root', 'sheet': 0, 'address': 'A2', 'value': 'same'},
    ], 'headers': [{'value': 'Code'}], 'passed': False,
        'gates': {'original_oracle': {'passed': False, 'missing': 1}}}
    report = _probe().compare_reports(before, deepcopy(before))
    assert not report['passed']
    assert report['no_regression'] is True
    assert report['gates']['source_acceptance']['baseline']['original_oracle']['missing'] == 1


def test_probe_preserves_sheet_identity_with_empty_and_reordered_sheets(tmp_path):
    import hashlib
    import zipfile
    from lxml import etree
    from openpyxl import Workbook
    workbook = Workbook()
    workbook.active.title = 'Empty'
    for name in ('Second', 'Third'):
        sheet = workbook.create_sheet(name)
        sheet.append(['Code', 'Value'])
        sheet.append([name, name + '-value'])
    source = tmp_path / 'input.xlsx'
    workbook.save(source)
    workbook.close()
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    root = etree.fromstring(entries['xl/workbook.xml'])
    sheets = root.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheets')
    third = sheets[-1]
    sheets.remove(third)
    sheets.insert(1, third)
    entries['xl/workbook.xml'] = etree.tostring(root)
    with zipfile.ZipFile(source, 'w') as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    manifest = {'documents': [{'document_id': 'book', 'path': 'input.xlsx',
                              'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'source_id': 'root',
                              'expected_cells': [
                                  {'source_id': 'root', 'sheet': 1, 'address': 'A2', 'value': 'Third'},
                                  {'source_id': 'root', 'sheet': 2, 'address': 'A2', 'value': 'Second'},
                              ], 'expected_headers': []}]}
    report = _probe().parse_manifest(manifest, tmp_path)
    assert report['gates']['0']['gates']['cell_coverage']['passed']
    assert {c['sheet']: c['value'] for c in report['cells'] if c['address'] == 'A2'} == {1: 'Third', 2: 'Second'}
