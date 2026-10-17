"""Bounded native journal acquisition; idle remains an independent API proof.

The caller supplies a cursor-inclusive journal query and fresh process identity.
The cursor anchor must be captured before the initial API idle observation.
Busy frames are provisional until complete task and owned-call reconciliation.
"""
import math
import secrets
import time
from table_acceptance_model_events import ModelBusyEvents, ModelEventUnknown


def _bounded_seconds(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1.25


def _single_idle(slots, seconds):
    if (not _bounded_seconds(seconds) or not isinstance(slots, list) or len(slots) != 1
            or not isinstance(slots[0], dict) or slots[0].get('is_processing') is not False):
        raise ModelEventUnknown('Single-slot API idle observation unavailable')


class NativeModelJournal:
    def __init__(self, *, initial_entry, initial_slots, initial_slots_seconds,
                 origin_lower_us, identity, check_identity, query,
                 monotonic=time.monotonic, wallclock=time.time):
        _single_idle(initial_slots, initial_slots_seconds)
        self.clock, self.wallclock = monotonic, wallclock
        self.query, self.check_identity = query, check_identity
        self.process_pin = dict(identity)
        self.state = ModelBusyEvents(pid=identity['pid'],
            invocation=initial_entry.get('_SYSTEMD_INVOCATION_ID'),
            stream=initial_entry.get('_STREAM_ID'), boot=initial_entry.get('_BOOT_ID'),
            origin_lower_us=origin_lower_us, require_reservation=True)
        self.cursor = initial_entry.get('__CURSOR')
        if (not isinstance(self.cursor, str) or not self.cursor
                or any(initial_entry.get(k) != v for k, v in self.state.identity.items())
                or self.check_identity() != self.process_pin):
            raise ModelEventUnknown('Initial native journal or process identity unavailable')
        self.identity = dict(self.process_pin,
            invocation=self.state.identity['_SYSTEMD_INVOCATION_ID'],
            stream=self.state.identity['_STREAM_ID'], boot=self.state.identity['_BOOT_ID'],
            producer_nonce=secrets.token_hex(24), journal_schema_version=2)
        self.last_finished = self.clock()
        self.offset = self.wallclock() - self.last_finished
        self.failed = False

    def read(self):
        if self.failed:
            raise ModelEventUnknown('Failed native journal acquisition cannot be reused')
        try:
            began = self.clock()
            if (not _bounded_seconds(began - self.last_finished)
                    or abs(self.wallclock() - began - self.offset) > .1
                    or self.check_identity() != self.process_pin):
                raise ModelEventUnknown('Native process, poll continuity or clock changed')
            rows = self.query(self.cursor)
            if (not isinstance(rows, list) or not 1 <= len(rows) <= 4096
                    or not isinstance(rows[0], dict) or rows[0].get('__CURSOR') != self.cursor
                    or any(rows[0].get(k) != v for k, v in self.state.identity.items())):
                raise ModelEventUnknown('Previous native journal cursor is absent or changed')
            for row in rows[1:]:
                self.state.feed(row)
            if self.check_identity() != self.process_pin:
                raise ModelEventUnknown('Native process identity changed during read')
            finished = self.clock()
            duration = finished - began
            if (not _bounded_seconds(duration)
                    or abs(self.wallclock() - finished - self.offset) > .1):
                raise ModelEventUnknown('Native journal acquisition exceeded its clock bound')
            snapshot = self.state.snapshot(read_duration_seconds=duration,
                observation_age_seconds=self.clock() - finished)
            self.cursor = rows[-1]['__CURSOR']
            self.last_finished = finished
            return dict(snapshot, known=True, identity=dict(self.identity),
                at_unix=self.wallclock(), acquisition_seconds=duration,
                initial_idle_proven=True, started_tasks=self.state.started_tasks,
                completed_tasks=self.state.completed_tasks)
        except Exception as error:
            self.failed = True
            if isinstance(error, ModelEventUnknown):
                raise
            raise ModelEventUnknown('Native journal acquisition failed') from error

    def finish(self, *, final_slots, final_slots_seconds):
        try:
            _single_idle(final_slots, final_slots_seconds)
            frame = self.read()
            self.state.finish()
            return dict(frame, terminal_idle_proven=True)
        except Exception:
            self.failed = True
            raise
