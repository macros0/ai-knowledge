"""Table diagnostics persist as safe generation artifacts, including DB-only mode."""
import importlib.util
import json

import pytest

from app.services import gen_quality
from app.services.generation_files import active_bundle_path
from app.services.pipeline import Pipeline, _generation_problem
from app.services.staging import StagingStore
from tests.test_pipeline_integration import isolated_env as isolated_env


def _quality():
    from app.services import table_quality
    return table_quality


def test_row_coverage_does_not_claim_header_correctness():
    assert importlib.util.find_spec('app.services.table_quality'), 'persistent table quality model is missing'
    quality = _quality()
    report = quality.TableQualityReport(
        table_ref=quality.LegacyTableLocator(source_id='root/0', chunk_index=0, table_ordinal=0),
        method='deterministic_rows', cause_code='classifier_output_truncated', input_rows=344, covered_rows=344,
    )
    assert report.header_status == 'unknown'
    assert report.input_cells is None and report.covered_cells is None


def test_report_contains_no_cell_text(tmp_path):
    assert importlib.util.find_spec('app.services.table_quality'), 'persistent table quality model is missing'
    quality = _quality()
    report = quality.TableQualityReport(
        table_ref=quality.LegacyTableLocator(source_id='root', chunk_index=0, table_ordinal=0),
        method='llm_per_row', input_rows=2, covered_rows=2,
    )
    with pytest.raises(ValueError):
        quality.TableQualityReport(**report.model_dump(), cell_text='private canary')
    path = quality.write_table_quality_report(tmp_path, [report])
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['schema_version'] == 1
    assert data['reports'][0]['table_ref']['kind'] == 'legacy'
    assert 'private canary' not in path.read_text(encoding='utf-8')
    assert path.name == 'table-quality.json'


@pytest.mark.parametrize('write_bundles', [False, True])
def test_quality_report_survives_staging_cleanup(isolated_env, monkeypatch, write_bundles):
    assert importlib.util.find_spec('app.services.table_quality'), 'persistent table quality model is missing'
    quality = _quality()
    registry, source = isolated_env
    registry.create('table-quality', 'table.docx', 'application/octet-stream', 4)
    pipeline = Pipeline()
    pipeline.settings.okf_write_bundles = write_bundles
    pipeline.okf_generator.chunk_text = lambda *a, **k: ['table text']
    pipeline.settings.dev_detection_enabled = False
    pipeline.settings.dedup_enabled = False
    def generate(*args, **kwargs):
        from app.models.schemas import Concept
        gen_quality.record_table_report(quality.TableQualityReport(
            table_ref=quality.LegacyTableLocator(source_id='root/0', chunk_index=9, table_ordinal=0),
            method='deterministic_rows', cause_code='classifier_output_truncated', input_rows=2, covered_rows=2,
        ))
        gen_quality.record(gen_quality.CLASSIFIER_FALLBACK)
        return [Concept(id='row', title='Row', content='value')]
    pipeline.okf_generator.generate_chunk = generate
    pipeline._process('table-quality', source, 'table.docx', [], resume=False)
    assert registry.get('table-quality')['status'] == 'done'
    assert registry.get('table-quality')['problem'] == 'llm_classifier_fallback'
    assert not StagingStore('table-quality').exists()
    bundle = active_bundle_path(pipeline.settings, 'table-quality')
    path = bundle / 'table-quality.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['reports'][0]['table_ref'] == {'kind': 'legacy', 'source_id': 'root', 'chunk_index': 0, 'table_ordinal': 0}
    from app.services.generation_artifacts import file_digest
    from app.services.generation_files import generation_paths
    from app.db.models import DocumentGenerationState
    from app.db.session import session_scope
    with session_scope() as session:
        generation = session.get(DocumentGenerationState, 'table-quality').active_generation_id
    paths = generation_paths(pipeline.settings, 'table-quality', generation)
    manifest = json.loads((paths.uploads_root / 'publication.json').read_text(encoding='utf-8'))
    assert manifest['artifacts']['bundle/table-quality.json'] == file_digest(path)


def test_real_omission_precedes_fallback_warning():
    assert importlib.util.find_spec('app.services.table_quality'), 'persistent table quality model is missing'
    events = [{'event': gen_quality.CLASSIFIER_FALLBACK}, {'event': 'table_quality', 'report': {
        'table_ref': {'kind': 'legacy', 'source_id': 'root', 'chunk_index': 0, 'table_ordinal': 0},
        'method': 'heuristic_xml', 'cause_code': 'content_omitted', 'input_rows': 5, 'covered_rows': 4,
        'input_cells': None, 'covered_cells': None, 'header_status': 'unknown', 'schema_version': 1,
    }}]
    assert _generation_problem({'0': {'degradation': events}}) == 'table_content_omitted'
