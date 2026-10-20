"""Content-free versioned audit event, encoded once inside the DB transaction."""
from dataclasses import dataclass
from datetime import timezone
import json
from uuid import UUID
from .audit_catalog import CATALOG, REASONS

MAX_EVENT_BYTES = 16 * 1024

class AuditEventError(ValueError):
    def __init__(self):
        super().__init__("Invalid audit event")

@dataclass(frozen=True)
class AuditContext:
    actor_type: str = "unknown"
    outcome: str = "success"
    method: str | None = None
    reason: str | None = None
    request_id: str | None = None
    client_ip: None = None
    ip_source: str = "unknown"

    def __post_init__(self):
        if (self.actor_type not in {"user", "system", "anonymous", "unknown"}
            or self.outcome not in {"success", "denied", "failure"}
            or self.method not in {None, "oidc", "simulation"}
            or self.reason is not None and self.reason not in REASONS
            or self.client_ip is not None or self.ip_source != "unknown"):
            raise AuditEventError()
        if self.request_id is not None:
            _uuid(self.request_id)

def _uuid(value):
    try:
        if not isinstance(value,str) or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise AuditEventError() from None
    return value

def _text(value,limit):
    if value is None:return None
    if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 and c not in "\r\n\t" for c in value):
        raise AuditEventError()
    return value

def _values(value,rule):
    if not isinstance(value,dict):return None
    clean={}
    for key,kind in rule.values.items():
        item=value.get(key)
        if kind=="int" and type(item) is int and 0<=item<=2**63-1:clean[key]=item
        elif kind=="bool" and type(item) is bool:clean[key]=item
        elif kind=="summary" and isinstance(item,list):clean[key]={"count":len(item),"truncated":bool(item)}
    return clean or None

def build_event(entry,context:AuditContext)->dict:
    rule=CATALOG.get(entry.action_type)
    if rule is None:raise AuditEventError()
    created=entry.created_at
    if created.tzinfo is None:created=created.replace(tzinfo=timezone.utc)
    return {"schema_version":1,"event_id":_uuid(entry.event_id),
            "timestamp":created.astimezone(timezone.utc).isoformat(),
            "action":entry.action_type,"actor":{"type":context.actor_type,
            "id":_text(entry.user_id,255),"name":_text(entry.username,255)},
            "target":{"type":_text(entry.target_type,64),"id":_text(entry.target_id,255)},
            "outcome":context.outcome,"method":context.method,"reason":context.reason,
            "request_id":context.request_id,"client_ip":None,"ip_source":"unknown",
            "old_value":_values(entry.old_value,rule),"new_value":_values(entry.new_value,rule),"meta":None}

def encode_event(event:dict)->bytes:
    try:data=json.dumps(event,ensure_ascii=False,allow_nan=False,separators=(",",":")).encode("utf-8")
    except (ValueError,TypeError,UnicodeError):raise AuditEventError() from None
    if len(data)>MAX_EVENT_BYTES:raise AuditEventError()
    return data
