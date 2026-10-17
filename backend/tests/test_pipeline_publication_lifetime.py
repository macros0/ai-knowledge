"""Transient generation inputs must not remain live while publication reloads them."""
import weakref
from functools import wraps

import pytest
from pathlib import Path

from tests.test_generation_pipeline import pipeline_env as _pipeline_env
from tests.test_pipeline_index_batches import workbook

pipeline_env = _pipeline_env


class TrackedFragment(dict):
    pass


class TrackedText(str):
    pass


class TrackedRecords(list):
    pass


def test_source_fragments_are_released_before_finalization(pipeline_env, tmp_path, monkeypatch):
    from app.services import table_pipeline
    pipeline, _, _ = pipeline_env
    pipeline.settings.okf_table_llm_classify = False
    source = tmp_path / 'fragments.xlsx'
    workbook(source, 'K')
    refs, live_counts = [], []
    original_build = table_pipeline.build_table_source_chunks
    original_finalize = pipeline._finalize

    def build(*args, **kwargs):
        rows = [TrackedFragment(row) for row in original_build(*args, **kwargs)]
        refs.extend(weakref.ref(row) for row in rows)
        return rows

    def finalize(*args, **kwargs):
        live_counts.append(sum(ref() is not None for ref in refs))
        return original_finalize(*args, **kwargs)

    monkeypatch.setattr(table_pipeline, 'build_table_source_chunks', build)
    monkeypatch.setattr(pipeline, '_finalize', finalize)
    doc = 'source-lifetime'
    pipeline.registry.create(doc, source.name, 'application/octet-stream', source.stat().st_size)
    pipeline._process(doc, source, source.name, [], resume=False)
    assert pipeline.registry.get(doc)['status'] == 'done'
    assert len(refs) == 151
    assert live_counts == [0], 'All source fragments remained live during finalization'


def test_publication_releases_generated_objects_and_serialized_records_before_reload(pipeline_env, tmp_path, monkeypatch):
    import app.services.pipeline as module
    pipeline, _, _ = pipeline_env
    pipeline.settings.okf_table_llm_classify = False
    source = tmp_path / 'publication.xlsx'
    workbook(source, 'K')
    active = {'reading': False}
    doc_refs, text_refs, payload_refs, observations = [], [], [], []
    read_text, build_docs = Path.read_text, pipeline.okf_generator.build_okf_docs
    write_json, publish = module.write_json_atomic, pipeline._publish_prepared_generation
    finalize = pipeline._finalize

    def read(path, *args, **kwargs):
        result = read_text(path, *args, **kwargs)
        if active['reading'] and path.name.startswith('chunk_') and path.suffix == '.md':
            result = TrackedText(result)
            text_refs.append(weakref.ref(result))
        return result

    def build(*args, **kwargs):
        documents, manifest = build_docs(*args, **kwargs)
        doc_refs.extend(weakref.ref(item) for item in documents)
        return documents, manifest

    def write(path, data, *args, **kwargs):
        if path.name == 'publication.json':
            for key in ['concepts', 'chunks']:
                data[key] = TrackedRecords(data[key])
                payload_refs.append(weakref.ref(data[key]))
        return write_json(path, data, *args, **kwargs)

    def final(*args, **kwargs):
        active['reading'] = True
        return finalize(*args, **kwargs)

    def publication(*args, **kwargs):
        observations.append({'documents': sum(ref() is not None for ref in doc_refs),
                             'chunks': sum(ref() is not None for ref in text_refs),
                             'payload_arrays': sum(ref() is not None for ref in payload_refs)})
        return publish(*args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(pipeline.okf_generator, 'build_okf_docs', build)
    monkeypatch.setattr(module, 'write_json_atomic', write)
    monkeypatch.setattr(pipeline, '_finalize', final)
    monkeypatch.setattr(pipeline, '_publish_prepared_generation', publication)
    doc = 'publication-lifetime'
    pipeline.registry.create(doc, source.name, 'application/octet-stream', source.stat().st_size)
    pipeline._process(doc, source, source.name, [], resume=False)
    assert pipeline.registry.get(doc)['status'] == 'done'
    assert len(doc_refs) == len(text_refs) == 151
    assert len(observations) == 1
    assert observations[0]['documents'] <= 64, observations
    assert observations[0]['chunks'] <= 64, observations
    assert observations[0]['payload_arrays'] == 0, observations


@pytest.mark.parametrize('supervised', [False, True])
def test_parser_graph_is_released_before_finalization(pipeline_env, tmp_path, monkeypatch, supervised):
    import app.services.pipeline as module
    from app.services.parser_supervisor import SupervisedParseResult
    pipeline, _, _ = pipeline_env
    pipeline.settings.okf_table_llm_classify = False
    pipeline.settings.table_structure_enabled = False
    pipeline.settings.parser_supervisor_enabled = supervised
    source = tmp_path / 'parser-lifetime.xlsx'
    workbook(source, 'P', rows=3)
    refs, observations = [], []
    parse, context = module.parse_document, module.ParseContext
    markdown, indexable = module.markdown_attachment_spans, module.indexable_blocks
    finalize = pipeline._finalize

    def track(value):
        refs.append(weakref.ref(value))
        return value

    class Context(context):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            track(self)

    @wraps(parse)
    def parser(*args, **kwargs):
        return track(TrackedRecords(parse(*args, **kwargs)))

    def supervise(path, filename, *, attachments_dir, **kwargs):
        ctx = Context(filename, mail_enabled=kwargs['mail_enabled'])
        blocks = parser(path, filename, attachments_dir=attachments_dir, context=ctx)
        return track(SupervisedParseResult(blocks, ctx.sources, ctx.warnings, ctx.parser_version))

    def render(*args, **kwargs):
        text, spans = markdown(*args, **kwargs)
        return track(TrackedText(text)), spans

    def filtered(*args, **kwargs):
        return track(TrackedRecords(indexable(*args, **kwargs)))

    def final(*args, **kwargs):
        observations.append(sum(ref() is not None for ref in refs))
        return finalize(*args, **kwargs)

    monkeypatch.setattr(module, 'ParseContext', Context)
    monkeypatch.setattr(module, 'parse_document', parser)
    monkeypatch.setattr(module, 'parse_document_supervised', supervise)
    monkeypatch.setattr(module, 'markdown_attachment_spans', render)
    monkeypatch.setattr(module, 'indexable_blocks', filtered)
    monkeypatch.setattr(pipeline, '_finalize', final)
    doc = f'parser-lifetime-{supervised}'
    pipeline.registry.create(doc, source.name, 'application/octet-stream', source.stat().st_size)
    pipeline._process(doc, source, source.name, [], resume=False)
    assert pipeline.registry.get(doc)['status'] == 'done'
    assert len(refs) >= 4
    assert observations == [0], 'Caller-owned parser graph survived into finalization'


@pytest.mark.parametrize('ready', [False, True])
def test_resume_checkpoint_graph_is_released_before_finalization_or_publish(pipeline_env, tmp_path, monkeypatch, ready):
    from app.services.errors import VectorStoreError
    from app.services.staging import StagingStore
    pipeline, _, _ = pipeline_env
    pipeline.settings.okf_table_llm_classify = False
    source = tmp_path / 'checkpoint-lifetime.xlsx'
    workbook(source, 'R', rows=3)
    doc = f'checkpoint-lifetime-{ready}'
    pipeline.registry.create(doc, source.name, 'application/octet-stream', source.stat().st_size)
    target = '_publish_prepared_generation' if ready else 'index_chunks'
    owner = pipeline if ready else pipeline.vector_store
    original = getattr(owner, target)

    def failure(*args, **kwargs):
        raise VectorStoreError('injected resumable failure')

    monkeypatch.setattr(owner, target, failure)
    pipeline._process(doc, source, source.name, [], resume=False)
    assert pipeline.registry.get(doc)['status'] == 'paused'
    monkeypatch.setattr(owner, target, original)
    load = StagingStore.load
    refs, observations = [], []
    tracking = {'caller': True}

    def checkpoint(stage):
        data = load(stage)
        if stage.doc_id == doc and tracking['caller'] and data is not None:
            data = TrackedFragment(data)
            refs.append(weakref.ref(data))
            for info in data['chunks_data'].values():
                if 'table_refs' in info:
                    info['table_refs'] = TrackedRecords(info['table_refs'])
                    refs.append(weakref.ref(info['table_refs']))
        return data

    method = '_publish_prepared_generation' if ready else '_finalize'
    finalize = getattr(pipeline, method)

    def final(*args, **kwargs):
        observations.append(sum(ref() is not None for ref in refs))
        tracking['caller'] = False
        return finalize(*args, **kwargs)

    monkeypatch.setattr(StagingStore, 'load', checkpoint)
    monkeypatch.setattr(pipeline, method, final)
    pipeline._process(doc, source, source.name, [], resume=True)
    assert pipeline.registry.get(doc)['status'] == 'done'
    assert len(refs) == 5
    assert observations == [0], 'Hydrated caller checkpoint survived into publication'
