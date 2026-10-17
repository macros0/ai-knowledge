"""Continuous native RSS observation before Pipeline import through publication.

Shared service RSS is outside this process tree. A resource run needs a frozen
private-environment adapter and independent service counters; sampling alone
cannot assert acceptance. No application import occurs at module import time.
"""
from __future__ import annotations

import queue
import threading
import time

from table_acceptance_rss import PHASES, RssTrace, RssUnknown


class PipelineRssSampler:
    def __init__(self, *, reader=None, join_timeout_seconds=2):
        self.trace = RssTrace()
        self.requests = queue.Queue()
        self.stop = threading.Event()
        self.thread = None
        self.last_sample = None
        self.births = {}
        self.join_timeout = join_timeout_seconds
        self.reader = reader
        self.window_start = None
        self.finished = False
        self.process = None
        self.external = None

    def _observe(self, phase):
        try:
            sample = self.reader()
            self._record(phase, sample, time.monotonic())
        except Exception as error:
            self.trace.error(error)

    def _record(self, phase, sample, at_monotonic):
        births = sample.get('birth_ids')
        if (not isinstance(births, dict) or set(births) != set(sample['pids'])
                or any(type(v) is not int or v <= 0 for v in births.values())
                or any(pid in self.births and self.births[pid] != value for pid, value in births.items())):
            raise RssUnknown('Selected PID birth identity unknown or changed')
        self.births.update(births)
        self.trace.record(phase, sample, at_monotonic=at_monotonic)
        self.last_sample = sample

    def _loop(self):
        phase = 'construction'
        while not self.stop.is_set():
            try:
                phase, done = self.requests.get(timeout=.2)
            except queue.Empty:
                done = None
            self._observe(phase)
            if done is not None: done.set()

    def start(self):
        if self.thread is not None: raise RuntimeError('Sampler already started')
        self.window_start = time.monotonic()
        if self.reader is None:
            from table_acceptance_rss_process import NativeRssProcess
            self.external = NativeRssProcess(self._record, self.trace, self.join_timeout)
            try:
                self.external.start()
            finally:
                self.thread = self.external.thread
                self.process = self.external.process
            return self
        first = threading.Event()
        self.requests.put(('construction', first))
        self.thread = threading.Thread(target=self._loop, name='r2-rss-sampler', daemon=True)
        self.thread.start()
        if not first.wait(self.join_timeout): self.trace.error(RssUnknown('Initial RSS observation timed out'))
        return self

    def phase(self, name):
        if name not in PHASES or self.thread is None or self.finished:
            raise RssUnknown('Invalid sampler lifecycle or phase')
        if self.external is not None:
            self.external.request(name)
            return
        done = threading.Event(); self.requests.put((name, done))
        if not done.wait(self.join_timeout): self.trace.error(RssUnknown('RSS boundary observation timed out'))

    def finish(self, *, publication_completed):
        if self.thread is None or self.finished: raise RuntimeError('Invalid sampler finish')
        self.finished = True
        published = time.monotonic() if publication_completed is True else None
        if self.external is not None:
            self.external.request('publication', finish=True)
            stopped = self.external.close()
            result = self.trace.result(sampler_stopped=stopped, window_start=self.window_start,
                window_end=time.monotonic(), publication_finished_at=published, max_gap_seconds=1.0)
            result.update(rss_acquisition='independent-child-process', rss_observer_included_in_tree=True)
            return result
        # Request a final observation AFTER successful publication, without reading
        # synchronously: a stuck native reader must not block bounded shutdown.
        final = threading.Event(); self.requests.put(('publication', final))
        if not final.wait(self.join_timeout): self.trace.error(RssUnknown('Final RSS observation timed out'))
        self.stop.set(); self.thread.join(timeout=self.join_timeout)
        stopped = not self.thread.is_alive()
        if not stopped: self.trace.error(RssUnknown('RSS sampler did not stop'))
        end = time.monotonic()
        return self.trace.result(sampler_stopped=stopped, window_start=self.window_start,
            window_end=end, publication_finished_at=published, max_gap_seconds=1.0)


from contextlib import contextmanager, ExitStack
from importlib import import_module


@contextmanager
def observe_pipeline(pipeline, sampler):
    """Private-child hooks; restore every application attribute after the run."""
    module = import_module('app.services.pipeline')
    with ExitStack() as stack:
        def replace(target, name, replacement):
            original = getattr(target, name)
            local = name in vars(target)
            setattr(target, name, replacement)
            def restore():
                if local: setattr(target, name, original)
                else: delattr(target, name)
            stack.callback(restore)
            return original
        stage_original = module.start_stage
        def stage(name, *args, **kwargs):
            phase = {'parse': 'parse', 'generate': 'generation', 'publish': 'publication'}.get(name)
            if phase: sampler.phase(phase)
            return stage_original(name, *args, **kwargs)
        replace(module, 'start_stage', stage)
        def wrap(target, name, phase):
            original = getattr(target, name)
            def measured(*args, **kwargs):
                sampler.phase(phase)
                return original(*args, **kwargs)
            replace(target, name, measured)
        wrap(module, 'chunk_blocks_by_source', 'splitting')
        wrap(import_module('app.services.table_pipeline'), 'build_table_source_chunks', 'splitting')
        wrap(pipeline, '_finalize', 'finalization')
        # The diagnostics index stage opens before finalization has indexed anything.
        # These boundaries are real projection embedding/index operations.
        wrap(pipeline.embedder, 'embed_texts', 'indexing')
        for name in ('index_concepts', 'index_chunks', 'delete_orphaned_points'):
            wrap(pipeline.vector_store, name, 'indexing')
        yield


def collect_pipeline(*, factory, run, readiness, completion_close, publication_complete,
                     observation=None,
                     scope='synthetic harness; independent acceptance evaluator required'):
    """Collect one lifecycle; close service counters before post-run DB reads."""
    from run_table_acceptance import _ready
    sampler = PipelineRssSampler()
    pipeline = None
    published = False
    error_type = None
    completion_error_type = None
    if not _ready(readiness):
        return {'status': 'invalid', 'exit_code': 2, 'reason': 'readiness_failed',
                'pipeline_complete': False, 'scope': scope}
    observation_error_type = None
    try:
        if observation is not None: observation.start()
        sampler.start()
        pipeline = factory()  # RSS started before application construction/import.
        if not _ready(readiness): raise RssUnknown('Observer unready before pipeline run')
        with observe_pipeline(pipeline, sampler):
            run(pipeline)
    except Exception as error:
        error_type = type(error).__name__
    finally:
        try:
            completion_close()  # Must precede every output/oracle/snapshot read.
        except Exception as error:
            completion_error_type = type(error).__name__
            if error_type is None: error_type = completion_error_type
        if pipeline is not None and error_type is None:
            try:
                published = publication_complete(pipeline) is True
            except Exception as error:
                error_type = type(error).__name__
        if pipeline is not None:
            try:
                pipeline._executor.shutdown(wait=False, cancel_futures=True)
                pipeline.vector_store.client.close()
            except Exception as error:
                error_type = type(error).__name__
                sampler.trace.error(error)
        if sampler.thread is not None:
            result = sampler.finish(publication_completed=published)
        else:
            result = {'rss_sampling_complete': False, 'rss_coverage': {}}
        if observation is not None:
            try:
                observation.finish(result['rss_coverage'])
            except Exception as error:
                observation_error_type = type(error).__name__
                if error_type is None: error_type = observation_error_type
    complete = published and result['rss_sampling_complete'] and error_type is None
    result.update(status='collected' if complete else 'invalid', exit_code=0 if complete else 2,
                  pipeline_complete=published, error_type=error_type, completion_error_type=completion_error_type, observation_error_type=observation_error_type, scope=scope)
    return result



def _file_sha(path):
    import hashlib
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()


def verify_frozen_inputs(protocol):
    """Verify source and runtime bytes before provider calls and again afterwards."""
    from pathlib import Path
    try:
        source = protocol['source']; runtime = protocol['runtime']
        if _file_sha(Path(source['path'])) != source['sha256']:
            raise RssUnknown('Frozen source changed')
        root = Path(runtime['root']).resolve(); files = runtime['files']
        if not isinstance(files, dict) or not files: raise RssUnknown('Frozen runtime missing')
        for name, expected in files.items():
            candidate = (root / name).resolve()
            if not candidate.is_relative_to(root) or _file_sha(candidate) != expected:
                raise RssUnknown('Frozen runtime changed or escaped its root')
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise RssUnknown('Frozen input verification failed') from error
    return True


def verify_resource_inputs(expected, observed):
    """Empty active generations are a known pre-state, never a fake publication."""
    from benchmark_table_acceptance import VARIANT_FINGERPRINTS, _sha256
    if (not isinstance(expected, dict) or not isinstance(observed, dict)
            or observed.get('snapshot_kind') != 'resource-inputs'
            or type(observed.get('active_generation_count')) is not int
            or observed['active_generation_count'] != 0
            or type(observed.get('point_count')) is not int or observed['point_count'] != 0
            or not isinstance(expected.get('collection_name'), str)
            or not expected['collection_name'].startswith('r2_evaluation_')
            or observed.get('collection_name') != expected['collection_name']
            or type(observed.get('table_structure_enabled')) is not bool
            or observed['table_structure_enabled'] != expected.get('table_structure_enabled')
            or any(not _sha256(expected.get(key)) or observed.get(key) != expected[key]
                   for key in VARIANT_FINGERPRINTS)):
        raise RssUnknown('Private empty resource input snapshot mismatched')
    return True


def main(argv=None):
    import argparse
    import importlib.util
    import json
    from pathlib import Path
    from benchmark_table_acceptance import protocol_digest
    from run_table_acceptance import _write
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--variant', choices=('baseline', 'after'), required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        args.output_dir.mkdir(parents=True, exist_ok=False)
    except OSError:
        return 2
    manifest = {'status': 'invalid', 'exit_code': 2, 'variant': args.variant,
                'reason': 'input_or_adapter_invalid'}
    holder = {}
    observation = None
    try:
        protocol = json.loads(args.protocol.read_text(encoding='utf-8'))
        manifest['protocol_sha256'] = protocol_digest(protocol)
        if protocol['rss_sampling'] != {'interval_seconds': .2, 'max_gap_seconds': 1.0}:
            raise RssUnknown('Unfrozen RSS interval or gap')
        verify_frozen_inputs(protocol)
        adapter = protocol['resource_adapter']; adapter_path = Path(adapter['path'])
        if _file_sha(adapter_path) != adapter['sha256']: raise RssUnknown('Resource adapter changed')
        # This adapter imports no application code. Prepare/drain the private
        # transport and observer before sampling actual Pipeline construction.
        spec = importlib.util.spec_from_file_location('r2_frozen_resource_adapter', adapter_path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        observation = module.create_observation(protocol, args.variant)
        def readiness():
            state = json.loads(Path(protocol['runner']['readiness_file']).read_text(encoding='utf-8'))
            fresh = state.get('ready') is True and 0 <= time.monotonic() - state['observed_monotonic'] <= 1
            return fresh and observation.readiness() is True
        def factory():
            # Actual application Settings/Pipeline imports and construction happen
            # only after both continuous load and RSS observers start.
            env = module.create_environment(protocol, args.variant, args.output_dir, observation=observation)
            holder['environment'] = env
            verify_resource_inputs(protocol['variants'][args.variant], env.inputs)
            _write(args.output_dir / 'inputs.json', env.inputs)
            return env.pipeline
        captured = collect_pipeline(factory=factory,
            run=lambda pipeline: holder['environment'].run(pipeline), readiness=readiness,
            completion_close=lambda: holder['environment'].completion_close() if holder else observation.close_counters(),
            observation=observation,
            publication_complete=lambda pipeline: holder['environment'].publication_complete(pipeline),
            scope='whole private pipeline; independent paired resource evaluator required')
        verify_frozen_inputs(protocol)
        if _file_sha(adapter_path) != adapter['sha256']: raise RssUnknown('Resource adapter changed during run')
        _write(args.output_dir / 'rss.json', captured)
        if captured['exit_code'] != 0: raise RssUnknown('Full pipeline observation incomplete')
        details = holder['environment'].measurements()
        if details.get('status') != 'done' or details.get('problem') is not None:
            raise RssUnknown('Publication result is incomplete')
        measured = dict(details, **captured)
        # Run-only time omits imports/construction and owned Pipeline shutdown.
        # Use the same full lifecycle window as the independently sampled RSS.
        measured['run_elapsed_s'] = details['elapsed_s']
        coverage = captured['rss_coverage']
        measured['elapsed_s'] = coverage['window_end'] - coverage['window_start']
        measured.update(status='done', variant=args.variant, fixture_sha256=protocol['source']['sha256'])
        _write(args.output_dir / 'measurements.json', measured)
        manifest.update(status='collected', exit_code=0, reason='measurement_collected',
                        measurement_sha256=_file_sha(args.output_dir / 'measurements.json'))
    except Exception as error:
        manifest['error_type'] = type(error).__name__
    finally:
        if holder:
            partial = getattr(holder['environment'], 'partial_measurements', None)
            if manifest['exit_code'] != 0 and callable(partial):
                try: _write(args.output_dir / 'partial.json', partial())
                except Exception as error:
                    manifest['partial_error_type'] = type(error).__name__
            try: holder['environment'].cleanup()
            except Exception as error:
                manifest.update(status='invalid', exit_code=2, reason='adapter_cleanup_unknown',
                                error_type=type(error).__name__)
        if observation is not None:
            try: observation.cleanup()
            except Exception as error:
                manifest.update(status='invalid', exit_code=2, reason='observation_cleanup_unknown',
                                observation_error_type=type(error).__name__)
        _write(args.output_dir / 'collector-manifest.json', manifest)
    return manifest['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
