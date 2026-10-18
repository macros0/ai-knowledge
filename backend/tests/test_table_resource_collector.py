"""Native lifecycle sampling tests; synthetic allocations are harness evidence only."""
import importlib.util
import mmap
from pathlib import Path
import subprocess
import sys
import time

import pytest


@pytest.fixture(autouse=True)
def _collector_import_path(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'test_scripts'))


def api():
    path = Path(__file__).parents[1] / 'test_scripts/collect_table_resources.py'
    assert path.exists(), 'full-window resource collector is missing'
    spec = importlib.util.spec_from_file_location('r2_collector', path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def complete(sampler):
    for phase in ('construction', 'parse', 'splitting', 'generation', 'finalization', 'indexing', 'publication'):
        sampler.phase(phase)
    return sampler.finish(publication_completed=True)


def test_sampler_starts_before_pipeline_factory_and_requires_publication():
    m = api(); sampler = m.PipelineRssSampler()
    sampler.start()
    assert sampler.trace.count > 0
    result = complete(sampler)
    assert result['rss_sampling_complete'] and result['rss_coverage']['sample_times'][0] <= result['rss_coverage']['publication_finished_at']


def test_sampler_rejects_unpublished_pipeline_even_with_all_phase_labels():
    sampler = api().PipelineRssSampler(); sampler.start()
    for phase in ('construction', 'parse', 'splitting', 'generation', 'finalization', 'indexing', 'publication'):
        sampler.phase(phase)
    result = sampler.finish(publication_completed=False)
    assert not result['rss_sampling_complete']


def test_native_sampler_detects_retained_32mib_child_during_parse():
    sampler = api().PipelineRssSampler(); sampler.start()
    baseline = sampler.trace.peak
    child = subprocess.Popen([sys.executable, '-u', '-c',
        'import sys; retained=bytearray(32*1024*1024); print("ready", flush=True); sys.stdin.readline()'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert child.stdout.readline().strip() == b'ready'
        sampler.phase('parse'); time.sleep(.45)
        observed = sampler.trace.peak
        assert observed >= baseline + 32 * 1024 * 1024
        assert child.pid in sampler.last_sample['pids']
        # Stop sampling only after a final sample while the child is still retained.
        result = complete(sampler)
        assert result['rss_sampling_complete']
    finally:
        child.stdin.write(b'done\n'); child.stdin.flush(); child.wait(timeout=5)
        child.stdin.close(); child.stdout.close()


def test_periodic_sampler_detects_peak_inside_finalization_not_only_boundaries():
    sampler = api().PipelineRssSampler(); sampler.start()
    try:
        sampler.phase('finalization'); baseline = sampler.trace.peak
        # A bytearray can reuse already-resident heap pages after earlier tests.
        # Fresh mapped pages must be written to become a real new RSS increase.
        with mmap.mmap(-1, 32 * 1024 * 1024) as retained:
            for offset in range(0, len(retained), mmap.PAGESIZE):
                retained[offset] = 1
            deadline = time.monotonic() + 5
            while sampler.trace.peak < baseline + 24 * 1024 * 1024 and time.monotonic() < deadline:
                time.sleep(.01)
            # No boundary request occurs while the transient allocation exists.
            assert sampler.trace.peak >= baseline + 24 * 1024 * 1024
            assert retained[0] == 1
        assert complete(sampler)['rss_sampling_complete']
    finally:
        if not sampler.finished:
            sampler.finish(publication_completed=False)


def test_changed_selected_pid_birth_invalidates_sampling():
    observations = iter([{'rss_bytes': 100, 'pids': [10], 'birth_ids': {10: 1}},
                         {'rss_bytes': 100, 'pids': [10], 'birth_ids': {10: 2}}])
    sampler = api().PipelineRssSampler(reader=lambda: next(observations))
    sampler.start(); sampler.phase('parse')
    result = sampler.finish(publication_completed=False)
    assert result['rss_sampler_error_count'] > 0 and not result['rss_sampling_complete']


def test_hung_sampler_shutdown_is_bounded_and_invalid():
    calls = 0
    def reader():
        nonlocal calls
        calls += 1
        if calls > 1: time.sleep(3)
        return {'rss_bytes': 100, 'pids': [10], 'birth_ids': {10: 1}}
    sampler = api().PipelineRssSampler(reader=reader, join_timeout_seconds=.2)
    sampler.start(); time.sleep(.25)
    started = time.monotonic()
    result = sampler.finish(publication_completed=False)
    assert time.monotonic() - started < 1
    assert not result['rss_sampling_complete'] and result['rss_sampler_error_count'] > 0


@pytest.mark.parametrize('enabled', [False, True])
def test_real_synthetic_pipeline_all_phases_and_successful_publication(tmp_path, monkeypatch, enabled):
    from importlib import import_module
    from openpyxl import Workbook
    from qdrant_client import QdrantClient
    from app.config import Settings
    from app.db.models import DocumentGenerationState, DocumentTableRow, DocumentChunk
    from app.db.session import session_scope
    from app.models.schemas import Concept
    from app.services.registry import DocumentRegistry
    from app.services.pipeline import Pipeline
    m = api()
    source = tmp_path / 'control.xlsx'
    workbook = Workbook(); ws = workbook.active
    ws.append(['Code', 'Value', 'Enabled']); ws.append(['A', 0, False]); workbook.save(source)
    settings = Settings(_env_file=None, data_dir=tmp_path / 'private-data',
        embedding_provider='fake', embedding_dimensions=8, llm_model='test/harness',
        dedup_enabled=False, dev_detection_enabled=False, parser_supervisor_enabled=False,
        table_structure_enabled=enabled, okf_table_llm_classify=False)
    for name in ('app.config', 'app.services.pipeline', 'app.services.staging',
                 'app.services.embedder', 'app.services.llm_client', 'app.services.okf_generator',
                 'app.services.vector_store', 'app.services.table_classification', 'app.services.field_table'):
        monkeypatch.setattr(import_module(name), 'get_settings', lambda: settings)
    def forbid_shared_client(*args, **kwargs):
        raise AssertionError('Synthetic harness must never create a shared-service client')
    monkeypatch.setattr('app.services.vector_store.shared_qdrant_client', forbid_shared_client)
    registry = DocumentRegistry()
    monkeypatch.setattr('app.services.pipeline.get_registry', lambda: registry)
    events = []
    original_finish = m.PipelineRssSampler.finish
    def finish_after_cleanup(sampler, **kwargs):
        events.append('sampler_stopped')
        return original_finish(sampler, **kwargs)
    monkeypatch.setattr(m.PipelineRssSampler, 'finish', finish_after_cleanup)
    doc_id = 'f123456789abcdef'
    def factory():
        pipeline = Pipeline()
        pipeline.vector_store.client = QdrantClient(location=':memory:')
        original_close = pipeline.vector_store.client.close
        def cleanup():
            events.append('cleanup')
            original_close()
        monkeypatch.setattr(pipeline.vector_store.client, 'close', cleanup)
        pipeline.okf_generator.generate_chunk = lambda text, *a, **kw: [Concept(id='control', title='Control', content=text)]
        registry.create(doc_id, 'control.xlsx', 'application/octet-stream', source.stat().st_size)
        return pipeline
    def publication_complete(pipeline):
        events.append('output_read')
        with session_scope() as session:
            state = session.get(DocumentGenerationState, doc_id)
            assert state.active_generation_id
            assert session.query(DocumentChunk).filter_by(doc_id=doc_id).count() > 0
            assert bool(session.query(DocumentTableRow).filter_by(doc_id=doc_id).count()) == enabled
        return registry.get(doc_id)['status'] == 'done'
    result = m.collect_pipeline(factory=factory,
        run=lambda pipeline: pipeline._process(doc_id, source, 'control.xlsx', [], resume=False),
        readiness=lambda: True, completion_close=lambda: events.append('counter_closed'),
        publication_complete=publication_complete)
    assert result['pipeline_complete'] and result['rss_sampling_complete']
    assert set(result['sampled_phases']) == set(m.PHASES)
    assert events == ['counter_closed', 'output_read', 'cleanup', 'sampler_stopped']
    assert result['scope'] == 'synthetic harness; independent acceptance evaluator required'


def test_collector_prevents_provider_calls_when_readiness_is_unknown():
    calls = []
    result = api().collect_pipeline(factory=lambda: calls.append('provider'),
        run=lambda pipeline: None, readiness=lambda: False, completion_close=lambda: None,
        publication_complete=lambda pipeline: True)
    assert not calls and result['status'] == 'invalid'



def frozen_protocol(tmp_path):
    import hashlib
    source = tmp_path / 'source.xlsx'; source.write_bytes(b'synthetic source')
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    app = runtime / 'app.py'; app.write_text('synthetic frozen runtime')
    return {'source': {'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()},
            'runtime': {'root': str(runtime), 'files': {'app.py': hashlib.sha256(app.read_bytes()).hexdigest()}}}


@pytest.mark.parametrize('fault', ['source', 'runtime', 'traversal', 'missing_runtime'])
def test_resource_frozen_inputs_reject_changed_source_or_runtime(tmp_path, fault):
    protocol = frozen_protocol(tmp_path)
    if fault == 'source': Path(protocol['source']['path']).write_bytes(b'changed')
    elif fault == 'runtime': (Path(protocol['runtime']['root']) / 'app.py').write_text('changed')
    elif fault == 'traversal': protocol['runtime']['files'] = {'../source.xlsx': protocol['source']['sha256']}
    else: protocol['runtime']['files'] = {}
    with pytest.raises(api().RssUnknown): api().verify_frozen_inputs(protocol)


def test_resource_frozen_inputs_are_checked_from_actual_bytes(tmp_path):
    protocol = frozen_protocol(tmp_path)
    assert api().verify_frozen_inputs(protocol) is True


@pytest.mark.parametrize('fault', ['post_state', 'foreign_collection', 'published', 'fingerprint'])
def test_resource_input_snapshot_is_empty_private_prestate(fault):
    m = api()
    import hashlib
    hashes = {name: hashlib.sha256(name.encode()).hexdigest() for name in
              ('database_snapshot_sha256', 'index_snapshot_sha256', 'active_generations_sha256', 'cache_manifest_sha256')}
    expected = dict(hashes, table_structure_enabled=True, collection_name='r2_evaluation_synthetic')
    state = dict(expected, snapshot_kind='resource-inputs', active_generation_count=0, point_count=0)
    if fault == 'post_state': state['snapshot_kind'] = 'resource-outputs'
    elif fault == 'foreign_collection': state['collection_name'] = 'okf_knowledge_base'
    elif fault == 'published': state['active_generation_count'] = 1
    else: state['database_snapshot_sha256'] = '0' * 64
    with pytest.raises(m.RssUnknown): m.verify_resource_inputs(expected, state)


def test_resource_cli_missing_adapter_cannot_silently_exit_success(tmp_path):
    import json
    protocol = tmp_path / 'protocol.json'; protocol.write_text('{}')
    out = tmp_path / 'output'
    done = subprocess.run([sys.executable, str(Path(__file__).parents[1] / 'test_scripts/collect_table_resources.py'),
        '--protocol', str(protocol), '--variant', 'baseline', '--output-dir', str(out)],
        capture_output=True, timeout=15)
    assert done.returncode == 2 and not done.stderr
    assert json.loads((out / 'collector-manifest.json').read_text())['status'] == 'invalid'


def test_factory_error_is_not_masked_by_completion_callback_failure():
    m=api()
    def factory():raise ValueError('private factory failed')
    def close():raise KeyError('environment not constructed')
    result=m.collect_pipeline(factory=factory,run=lambda p:None,readiness=lambda:True,
        completion_close=close,publication_complete=lambda p:True)
    assert result['error_type']=='ValueError'
    assert result['completion_error_type']=='KeyError' and result['exit_code']==2


def test_cli_elapsed_covers_construction_through_cleanup_not_only_run(tmp_path,monkeypatch):
    import json,time,hashlib
    m=api();source=tmp_path/'source';source.write_text('control')
    runtime=tmp_path/'runtime.py';runtime.write_text('value=1')
    observed=dict(snapshot_kind='resource-inputs',collection_name='r2_evaluation_fixture',table_structure_enabled=False,
        active_generation_count=0,point_count=0,**{k:'1'*64 for k in ('database_snapshot_sha256','index_snapshot_sha256','active_generations_sha256','cache_manifest_sha256')})
    adapter=tmp_path/'adapter.py'
    adapter.write_text("class Env:\n    pipeline=object()\n    def __init__(self,p):self.inputs=p['variants']['baseline']\n    def measurements(self):return {'status':'done','problem':None,'elapsed_s':5}\n    def cleanup(self):pass\ndef create_environment(p,v,o,**kw):return Env(p)\nclass Observation:\n    def readiness(self):return True\n    def cleanup(self):pass\ndef create_observation(p,v):return Observation()\n".replace('\\n','\n'))
    ready=tmp_path/'ready.json';ready.write_text(json.dumps({'ready':True,'observed_monotonic':time.monotonic()}))
    def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    protocol={'rss_sampling':{'interval_seconds':.2,'max_gap_seconds':1.0},'source':{'path':str(source),'sha256':sha(source)},
        'runtime':{'root':str(tmp_path),'files':{'runtime.py':sha(runtime)}},'variants':{'baseline':observed},
        'resource_adapter':{'path':str(adapter),'sha256':sha(adapter)},'runner':{'readiness_file':str(ready)}}
    file=tmp_path/'protocol.json';file.write_text(json.dumps(protocol));output=tmp_path/'output'
    def collect(**kwargs):
        kwargs['factory']()
        return {'status':'collected','exit_code':0,'rss_coverage':{'window_start':1,'window_end':10}}
    monkeypatch.setattr(m,'collect_pipeline',collect)
    assert m.main(['--protocol',str(file),'--variant','baseline','--output-dir',str(output)])==0
    measured=json.loads((output/'measurements.json').read_text())
    assert measured['elapsed_s']==9 and measured['run_elapsed_s']==5


@pytest.mark.parametrize('failure', [False, True])
def test_load_observation_covers_factory_and_owned_cleanup(monkeypatch, failure):
    from contextlib import contextmanager
    from types import SimpleNamespace
    m=api();events=[]
    class Sampler:
        thread=True
        trace=SimpleNamespace(error=lambda e:None)
        def start(self):events.append('rss-start')
        def finish(self,**kwargs):
            events.append('rss-stop')
            return {'rss_sampling_complete':not failure,'rss_coverage':{'window_start':1,'window_end':5}}
    class Observation:
        def start(self):events.append('load-start')
        def finish(self,coverage):
            assert coverage=={'window_start':1,'window_end':5}
            events.append('load-stop')
    @contextmanager
    def observed(*args):yield
    def factory():
        events.append('factory')
        if failure:raise ValueError('construction failed')
        return SimpleNamespace(_executor=SimpleNamespace(shutdown=lambda **kw:events.append('executor')),
            vector_store=SimpleNamespace(client=SimpleNamespace(close=lambda:events.append('client'))))
    monkeypatch.setattr(m,'PipelineRssSampler',Sampler)
    monkeypatch.setattr(m,'observe_pipeline',observed)
    result=m.collect_pipeline(factory=factory,run=lambda p:events.append('run'),readiness=lambda:True,
        completion_close=lambda:events.append('counter-close'),
        publication_complete=lambda p:events.append('publication') or True,observation=Observation())
    assert events[:3]==['load-start','rss-start','factory']
    assert events[-2:]==['rss-stop','load-stop']
    if failure:assert result['error_type']=='ValueError' and result['exit_code']==2
    else:
        assert events.index('counter-close')<events.index('publication')<events.index('client')<events.index('rss-stop')
        assert result['exit_code']==0


def test_load_shutdown_error_invalidates_successful_rss(monkeypatch):
    from types import SimpleNamespace
    m=api()
    class Sampler:
        thread=True
        def start(self):pass
        def finish(self,**kw):return {'rss_sampling_complete':False,'rss_coverage':{}}
    def failed():raise ValueError('factory')
    def stop(coverage):raise RuntimeError('observer stop')
    monkeypatch.setattr(m,'PipelineRssSampler',Sampler)
    result=m.collect_pipeline(factory=failed,run=lambda p:None,readiness=lambda:True,
        completion_close=lambda:None,publication_complete=lambda p:True,
        observation=SimpleNamespace(start=lambda:None,finish=stop))
    assert result['error_type']=='ValueError' and result['observation_error_type']=='RuntimeError'
    assert result['exit_code']==2
