"""One bounded compact writer. Local stdout flush is not a downstream ACK."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
import threading
import time
from sqlalchemy import select, update
from app.db.models import AuditOutbox
from app.db.session import get_session_factory
from .audit_catalog import CATALOG
from .audit_event import AuditContext, MAX_EVENT_BYTES, encode_event
from .diagnostics.paths import canonical_root, assert_no_links

ERROR_CODES = frozenset({"stdout_failure", "marker_failure", "database_failure", "invalid_payload", "restored_backup", "binary_rollback"})


def stdout_line(payload: bytes) -> None:
    # A fence finishes any partial line left by a killed writer. No buffered
    # TextIO object is held across a potentially blocking OS pipe write.
    data = memoryview(b"\n" + payload + b"\n")
    while data:
        written = os.write(1, data)
        if written <= 0:raise OSError("Audit stdout write failed")
        data = data[written:]


def _validate_payload(payload):
    data = payload.encode("utf-8")
    if len(data)>MAX_EVENT_BYTES:raise ValueError()
    event=json.loads(data)
    keys={"schema_version","event_id","timestamp","action","actor","target","outcome","method","reason","request_id","client_ip","ip_source","old_value","new_value","meta"}
    if not isinstance(event,dict) or set(event)!=keys or type(event["schema_version"]) is not int or event["schema_version"]!=1 or event["action"] not in CATALOG:
        raise ValueError()
    from uuid import UUID
    if str(UUID(event["event_id"]))!=event["event_id"]:raise ValueError()
    timestamp=datetime.fromisoformat(event["timestamp"])
    if timestamp.tzinfo is None or timestamp.utcoffset()!=timedelta(0):raise ValueError()
    actor=event["actor"];target=event["target"]
    if not isinstance(actor,dict) or set(actor)!={"type","id","name"}:raise ValueError()
    if not isinstance(target,dict) or set(target)!={"type","id"}:raise ValueError()
    for value,limit in [(actor['id'],255),(actor['name'],255),(target['type'],64),(target['id'],255)]:
        if value is not None and (not isinstance(value,str) or len(value)>limit):raise ValueError()
    AuditContext(actor_type=actor['type'],outcome=event['outcome'],method=event['method'],reason=event['reason'],request_id=event['request_id'],client_ip=event['client_ip'],ip_source=event['ip_source'])
    if event['meta'] is not None:raise ValueError()
    for values in (event['old_value'],event['new_value']):
        if values is None:continue
        if not isinstance(values,dict):raise ValueError()
        for key,value in values.items():
            kind=CATALOG[event['action']].values.get(key)
            if kind=='int' and type(value) is int and 0<=value<=2**63-1:continue
            if kind=='bool' and type(value) is bool:continue
            if kind=='summary' and isinstance(value,dict) and set(value)=={'count','truncated'} and type(value['count']) is int and value['count']>=0 and type(value['truncated']) is bool:continue
            raise ValueError()
    if encode_event(event)!=data:raise ValueError()
    return data


class AuditDispatcher:
    def __init__(self, settings, *, write_line=stdout_line, session_factory=None, clock=None):
        self.settings=settings
        if getattr(settings, "audit_stdout_format", "native") == "logstash":
            from .audit_logstash import make_logstash_sink
            self.write_line = make_logstash_sink(write_line)
        else:
            self.write_line = write_line
        self.sessions=session_factory or get_session_factory()
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self._stop=threading.Event()
        self._thread=None
        self._handle=None
        self._mutex=threading.Lock()
        self._counts=Counter()
        self._state='stopped'
        self._last_error=None
        self._inflight_since=None
        self._database_backoff=0
        self._database_retry_at=0

    def _lock(self):
        root=canonical_root(self.settings.data_dir)
        folder=root/'audit'
        assert_no_links(folder,root)
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/'.writer.lock'
        assert_no_links(path,root)
        fd=os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
        handle=os.fdopen(fd,'r+b',buffering=0)
        try:
            if os.fstat(fd).st_size==0:handle.write(b'0')
            handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except Exception:
            handle.close()
            raise ValueError('Audit writer already active or lock unavailable') from None
        self._handle=handle

    def start(self):
        with self._mutex:
            if self._thread is not None and self._thread.is_alive():
                if self._stop.is_set():raise ValueError('Audit writer is still stopping')
                return
            self._lock()
            self._stop.clear()
            self._state='running'
            self._thread=threading.Thread(target=self._run,name='audit-outbox',daemon=True)
            try:self._thread.start()
            except Exception:
                self._handle.close();self._handle=None
                raise

    def stop(self,timeout_seconds=5):
        self._stop.set()
        thread=self._thread
        if thread is None:return True
        self._state='stopping'
        thread.join(timeout=max(0,timeout_seconds))
        return not thread.is_alive()

    def status(self):
        state=self._state
        if state=='running' and self._inflight_since is not None and time.monotonic()-self._inflight_since>5:state='stalled'
        from .audit_metrics import snapshot
        counters = snapshot()
        counters.update(self._counts)
        for name in ('stdout_failure', 'retry'):
            counters.setdefault(name, 0)
        # Counts failed dispatcher DB cycles, not uncommitted/rolled-back events.
        counters['db_failure'] = counters.get('database_failure', 0)
        return {'state':state,'counters':counters,'last_error_code':self._last_error}

    def _run(self):
        try:
            while not self._stop.is_set():
                self.tick()
                self._stop.wait(.5)
        finally:
            with self._mutex:
                if self._handle is not None:self._handle.close();self._handle=None
                self._state='stopped'

    def _failure(self,audit_id,attempts,code):
        self._counts[code]+=1
        self._last_error=code
        with self.sessions.begin() as session:
            session.execute(update(AuditOutbox).where(AuditOutbox.audit_id==audit_id,AuditOutbox.status=='pending').values(last_error_code=code,next_attempt_at=self.clock()+timedelta(seconds=min(60,2**min(attempts-1,6)))))

    def _mark_written(self,audit_id):
        with self.sessions.begin() as session:
            session.execute(update(AuditOutbox).where(AuditOutbox.audit_id==audit_id,AuditOutbox.status=='pending').values(status='stdout_written',stdout_written_at=self.clock(),last_error_code=None))

    def tick(self):
        if time.monotonic()<self._database_retry_at:return 0
        delivered=0
        try:
            with self.sessions() as session:
                rows=list(session.execute(select(AuditOutbox.audit_id).where(AuditOutbox.status=='pending',AuditOutbox.next_attempt_at<=self.clock()).order_by(AuditOutbox.next_attempt_at,AuditOutbox.audit_id).limit(100)).scalars())
            for audit_id in rows:
                if self._stop.is_set():break
                with self.sessions.begin() as session:
                    row=session.get(AuditOutbox,audit_id)
                    if row is None or row.status!='pending':continue
                    try:payload=_validate_payload(row.payload)
                    except (ValueError,TypeError,KeyError,UnicodeError,AttributeError):
                        row.status='held';row.last_error_code='invalid_payload'
                        self._counts['invalid_payload']+=1
                        continue
                    row.attempt_count+=1
                    attempts=row.attempt_count
                self._inflight_since=time.monotonic()
                try:self.write_line(payload)
                except Exception:
                    self._failure(audit_id,attempts,'stdout_failure')
                    continue
                finally:self._inflight_since=None
                try:self._mark_written(audit_id)
                except Exception:
                    self._failure(audit_id,attempts,'marker_failure')
                    continue
                self._counts['stdout_written']+=1
                if attempts>1:self._counts['retry']+=1
                delivered+=1
            self._database_backoff=0
            self._database_retry_at=0
        except Exception:
            self._counts['database_failure']+=1
            self._last_error='database_failure'
            self._database_backoff=min(60,2**min(self._counts['database_failure']-1,6))
            self._database_retry_at=time.monotonic()+self._database_backoff
        return delivered
