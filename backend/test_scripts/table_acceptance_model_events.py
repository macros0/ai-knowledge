"""Known busy intervals from a pinned llama-server journal; never an idle proof."""
import re


class ModelEventUnknown(ValueError):
    pass


_EVENT = re.compile(r'^(\d+)\.(\d{2})\.(\d{3})\.(\d{3}) I slot\s+(launch_slot_|release)\s*:\s*id\s+(\d+)\s*\|\s*task\s+(\d+)\s*\|')

_SELECTION = re.compile(r'^(\d+)\.(\d{2})\.(\d{3})\.(\d{3}) I slot\s+(get_availabl)\s*:\s*id\s+(\d+)\s*\|\s*task\s+(-?\d+)\s*\|\s*selected slot by (?:LRU|LCP similarity|id)')


class ModelBusyEvents:
    def __init__(self, *, pid, invocation, stream, boot, origin_lower_us, require_reservation=False):
        if type(pid) is not int or pid <= 0 or type(origin_lower_us) is not int or origin_lower_us <= 0:
            raise ModelEventUnknown('Pinned event identity/clock origin missing')
        self.identity={'_PID':str(pid),'_SYSTEMD_INVOCATION_ID':invocation,'_STREAM_ID':stream,'_BOOT_ID':boot}
        if any(not isinstance(v,str) or not v for v in self.identity.values()):
            raise ModelEventUnknown('Pinned journal identity missing')
        self.origin_lower_us=origin_lower_us
        if type(require_reservation) is not bool:
            raise ModelEventUnknown("Reservation policy unknown")
        self.require_reservation=require_reservation
        self.reserved_tasks=0
        self.busy_phase=None
        self.active=None
        self.last_native_us=-1
        self.last_ingested_us=-1
        self.last_cursor=None
        self.timestamp_cursors=set()
        self.started_tasks=0
        self.completed_tasks=0
        self.failed=False

    def feed(self, entry):
        if self.failed:raise ModelEventUnknown('Failed event stream cannot be reused')
        try:
            if any(entry.get(k)!=v for k,v in self.identity.items()):
                raise ModelEventUnknown('Model journal identity changed')
            cursor=entry.get('__CURSOR')
            if not isinstance(cursor,str) or not cursor or len(cursor)>2048 or cursor == self.last_cursor:
                raise ModelEventUnknown('Missing or repeated journal cursor')
            ingested_us=int(entry['__REALTIME_TIMESTAMP'])
            if ingested_us<self.last_ingested_us:
                raise ModelEventUnknown('Journal source timestamp moved backward')
            # journald can timestamp several different lines identically. Keep only
            # that timestamp group; older records fail the clock guard above.
            # Cursors remain opaque: parsing their private encoding is unnecessary.
            if ingested_us>self.last_ingested_us:
                self.timestamp_cursors.clear()
            if cursor in self.timestamp_cursors or len(self.timestamp_cursors)>=4096:
                raise ModelEventUnknown('Journal timestamp cursor group repeated or overflowed')
            self.timestamp_cursors.add(cursor)
            message=entry.get('MESSAGE')
            if not isinstance(message,str):raise ModelEventUnknown('Journal message missing')
            if 'Suppressed' in message or 'missed' in message.lower():
                raise ModelEventUnknown('Journal continuity is unknown')
            match=_EVENT.match(message)
            if match is None and self.require_reservation:
                match=_SELECTION.match(message)
            if not match:
                self.last_cursor=cursor
                self.last_ingested_us=ingested_us
                return
            minutes,seconds,millis,micros=map(int,match.group(1,2,3,4))
            if seconds>=60:raise ModelEventUnknown('Native clock malformed')
            native_us=((minutes*60+seconds)*1000+millis)*1000+micros
            delay_upper=ingested_us-(self.origin_lower_us+native_us)
            if (native_us<self.last_native_us or ingested_us<self.last_ingested_us
                    or not 0<=delay_upper<=1250000):
                raise ModelEventUnknown('Event clock or delivery freshness unknown')
            slot,task=int(match[6]),int(match[7])
            if slot!=0:raise ModelEventUnknown('Pinned single-slot source changed')
            if match[5]=='get_availabl':
                if task != -1 or self.active is not None:
                    raise ModelEventUnknown('Slot reservation is overlapping or not initially idle')
                self.active={'slot':slot,'task':-1}
                self.busy_phase='reservation'
                self.reserved_tasks+=1
            elif match[5]=='launch_slot_':
                if self.require_reservation:
                    if self.busy_phase!='reservation' or self.active!={'slot':slot,'task':-1}:
                        raise ModelEventUnknown('Task launch lacks a matching idle-slot reservation')
                elif self.active is not None:
                    raise ModelEventUnknown('Overlapping task starts')
                self.active={'slot':slot,'task':task}
                self.busy_phase='processing'
                self.started_tasks+=1
            else:
                if self.busy_phase!='processing' or self.active!={'slot':slot,'task':task}:
                    raise ModelEventUnknown('Release without known matching start')
                self.active=None
                self.busy_phase=None
                self.completed_tasks+=1
            self.last_cursor=cursor
            self.last_native_us=native_us;self.last_ingested_us=ingested_us
        except (KeyError,TypeError,ValueError) as error:
            self.failed=True
            if isinstance(error,ModelEventUnknown):raise
            raise ModelEventUnknown('Malformed native journal event') from error

    def snapshot(self, *, read_duration_seconds, observation_age_seconds=None):
        if (self.failed or type(read_duration_seconds) not in (int,float)
                or not 0<=read_duration_seconds<=1.25
                or type(observation_age_seconds) not in (int,float)
                or not 0<=observation_age_seconds<=1.25
                or read_duration_seconds+observation_age_seconds>1.25):
            raise ModelEventUnknown('Event acquisition unavailable')
        return {'busy_confirmed':self.active is not None,'active_task':dict(self.active) if self.active else None,
                'busy_phase':self.busy_phase,'reserved_tasks':self.reserved_tasks,
                'started_tasks':self.started_tasks,'completed_tasks':self.completed_tasks,
                'reservation_required':self.require_reservation,'idle_proven':False,'source':'pinned native task lifetime; release only removes busy evidence'}


    def finish(self):
        """Final reconciliation is mandatory; provisional busy snapshots alone cannot qualify."""
        if (self.failed or self.active is not None or self.started_tasks != self.completed_tasks
                or self.require_reservation and self.reserved_tasks != self.started_tasks):
            self.failed = True
            raise ModelEventUnknown('Model task lifetime incomplete at observation boundary')
        return {'started_tasks': self.started_tasks, 'completed_tasks': self.completed_tasks,
                'idle_proven': False}
