"""Replace only direct polling in the frozen operational monitor.

Whole-window CPU histories, completion audits, their original qualification
formulas and the post-publication idle tail remain in the existing monitor.
"""
import time
from table_acceptance_load import LoadUnknown, foreign_qdrant_requests
from table_acceptance_observer_process import NativeObservationProcess


class IndependentDirectMonitorMixin:
    def __init__(self, settings, protocol):
        super().__init__(settings, protocol)
        environment = protocol['resource_environment']
        description = environment['direct_observation']
        self.model_events_required = 'model_events_identity' in description
        probe_config = {
            'database_url': settings.db_url,
            'slots_url': settings.llm_base_url.removesuffix('/v1') + '/slots',
            'paths': description['paths'], 'windows_identity': environment['windows_identity']}
        if 'model_events_identity' in description:
            probe_config['model_events_identity'] = description['model_events_identity']
        if 'fault_file' in description:
            probe_config['fault_file'] = description['fault_file']
        self.direct = NativeObservationProcess(probe=description['probe'], config=probe_config)
        # Receive source-timestamped samples into the exact lists consumed by the
        # unchanged whole-window evaluator; errors are never discarded.
        self.direct.samples = self.samples
        self.direct.errors = self.errors

    def start(self):
        self.started = time.time()
        self.started_monotonic = time.monotonic()
        self.direct.start()
        self.thread = self.direct.thread

    def model_started(self):
        with self.model_lock:
            if not self.direct.started:
                raise LoadUnknown('Owned model call before independent observer readiness')
            self.model_inflight += 1
            self.direct.update_model(self.model_inflight)

    def model_finished(self):
        with self.model_lock:
            if not self.direct.started or self.model_inflight <= 0:
                raise LoadUnknown('Owned model completion bracket unknown')
            self.model_inflight -= 1
            self.direct.update_model(self.model_inflight)

    def observe(self):
        self.direct.sample()

    def stop(self):
        self.finished.set()
        try:
            self.observe()
            time.sleep(.6)
            closed_qd, closed_embedding = self.boundary.snapshots()
            tail_qd = foreign_qdrant_requests(closed_qd, self.metrics(), 0)
            tail_embedding = self.windows_audit()['completed_requests'] - closed_embedding['completed_requests']
            if tail_qd or tail_embedding:
                raise LoadUnknown('Completed service activity after publication boundary')
            self.tail = {'qdrant_completed': tail_qd, 'embedding_completed': tail_embedding}
            self.observe()
        except Exception as error:
            self.direct.error(error)
        finally:
            try:
                self.direct.close()
            finally:
                self.ended = time.time()
                self.ended_monotonic = time.monotonic()
                self.engine.dispose()
                time.sleep(.65)

    def result(self):
        stopped = self.direct.process is not None and self.direct.process.poll() is not None
        if self.model_inflight or self.direct.model_inflight or not stopped:
            self.direct.error(LoadUnknown('Independent observer or model bracket did not drain'))
        model_proof = getattr(self.direct, 'final_model_reconciliation', None)
        if self.model_events_required and model_proof is None:
            self.direct.error(LoadUnknown('Final model reconciliation absent from complete monitor'))
        result = super().result()
        result['direct_model_reconciliation'] = model_proof
        result['direct_acquisition'] = 'independent-child-process'
        result['direct_observer_stopped'] = stopped
        result['direct_owned_model_inflight'] = self.direct.model_inflight
        result['direct_observer_included_in_rss_tree'] = True
        return result
