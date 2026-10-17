"""Table endpoints enforce generation visibility and bounded explicit projections."""
import pytest
from openpyxl import Workbook

from app.db.models import Document
from app.db.session import session_scope
from app.services.generation_store import begin_generation, mark_generation_ready, publish_generation
from docparser import parse_document_result
from tests.test_document_sources_api import _client

DOC = 'c123456789abcdef'


def setup_table(tmp_path, width=2, text='<script>alert(1)</script>', next_row=None):
    from app.services.table_store import GenerationTableSink
    with session_scope() as s:
        s.add(Document(id=DOC, filename='source.xlsx', status='done'))
        s.flush()
        generation = begin_generation(s, DOC).id
    wb = Workbook()
    ws = wb.active
    ws.append([f'C{i}' for i in range(width)])
    ws.append([text] * width)
    if next_row is not None:
        ws.append([next_row] * width)
    source = tmp_path / 'source.xlsx'
    wb.save(source)
    result = parse_document_result(source, table_sink=GenerationTableSink(DOC, generation))
    table = next(b.meta['table_descriptor']['table_id'] for b in result.blocks if b.type == 'table')
    return generation, table


def url(generation, table):
    return f'/api/documents/{DOC}/tables/{table}?generation_id={generation}'


def test_candidate_and_deleted_generations_are_unavailable(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    generation, table = setup_table(tmp_path)
    assert client.get(url(generation, table)).status_code == 404
    with session_scope() as s:
        mark_generation_ready(s, DOC, generation)
        publish_generation(s, DOC, generation)
    response = client.get(url(generation, table))
    assert response.status_code == 200
    assert response.json()['quality']['complete'] is True
    assert client.get(url('0' * 32, table)).status_code == 404
    assert client.get(url(generation, table) + '&source_id=root%2Fforbidden').status_code == 404
    from datetime import datetime, timezone
    with session_scope() as s:
        s.get(Document, DOC).deleted_at = datetime.now(timezone.utc)
    assert client.get(url(generation, table)).status_code == 404


def test_rows_limit_coordinates_and_html_are_plain_data(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    generation, table = setup_table(tmp_path)
    with session_scope() as s:
        mark_generation_ready(s, DOC, generation)
        publish_generation(s, DOC, generation)
    base = f'/api/documents/{DOC}/tables/{table}/rows?generation_id={generation}'
    response = client.get(base + '&from_row=2&limit=1')
    assert response.status_code == 200
    assert response.json()['rows'][0]['cells']['1']['address'] == 'A2'
    assert response.json()['rows'][0]['cells']['1']['display_text'] == '<script>alert(1)</script>'
    assert client.get(base + '&limit=101').status_code == 422
    assert client.get(base + '&from_row=999').status_code == 416
    assert client.get(base.replace(table, '..')).status_code in (404, 422)


def test_oversized_row_is_explicit_projection_with_continuation(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    generation, table = setup_table(tmp_path, width=316, text='x' * 10000)
    with session_scope() as s:
        mark_generation_ready(s, DOC, generation)
        publish_generation(s, DOC, generation)
    response = client.get(f'/api/documents/{DOC}/tables/{table}/rows?generation_id={generation}&from_row=2&limit=1')
    assert response.status_code == 200 and len(response.content) <= 1024 * 1024
    value = response.json()
    assert value['projection'] is True and value['continuation']
    assert value['rows'][0]['cells']['1']['display_text'] == 'x' * 4000


def test_cell_continuation_is_scoped_to_one_cell_with_short_neighbours(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    generation, table = setup_table(tmp_path, text="x" * 5000, next_row="end")
    with session_scope() as session:
        mark_generation_ready(session, DOC, generation)
        publish_generation(session, DOC, generation)
    base = f"/api/documents/{DOC}/tables/{table}/rows?generation_id={generation}&source_id=root&columns=1"
    assert client.get(base + "&from_row=2&limit=10&text_start=1000").status_code == 416
    response = client.get(base + "&from_row=2&limit=1&text_start=1000")
    assert response.status_code == 200
    page = response.json()
    assert page["generation_id"] == generation and page["table_id"] == table
    assert len(page["rows"]) == 1 and page["rows"][0]["cells"]["1"]["display_text"] == "x" * 4000
    assert page["rows"][0]["cells"]["1"]["address"] == "A2" and page["next_row"] == 3
    neighbour = client.get(base + "&from_row=3&limit=10")
    assert neighbour.status_code == 200 and neighbour.json()["rows"][0]["cells"]["1"]["display_text"] == "end"
    assert client.get(base + "&from_row=2&limit=1&text_start=5001").status_code == 416
    assert client.get(base.replace(generation, "0" * 32) + "&from_row=2&limit=1").status_code == 404


@pytest.mark.parametrize("enabled, expected", [(False, None), (True, 1)])
def test_admin_runtime_reports_selected_generic_version(tmp_path, monkeypatch, enabled, expected):
    client, settings = _client(tmp_path, monkeypatch)
    settings.chunk_index_projection_enabled = enabled
    response = client.get('/api/documents/table-processing/runtime')
    assert response.status_code == 200
    assert response.json()['chunk_projection_version'] == expected
