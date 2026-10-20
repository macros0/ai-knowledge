"""Deterministic Logstash envelope; delivery errors always reach the outbox."""
from functools import partial
import json
import logging

from pythonjsonlogger.json import JsonFormatter

MAX_LOGSTASH_BYTES = 18 * 1024
_LEVELS = {"success": ("INFO", 20000, logging.INFO),
           "denied": ("WARN", 30000, logging.WARNING),
           "failure": ("ERROR", 40000, logging.ERROR)}


class AuditLogstashFormatError(ValueError):
    def __init__(self):
        super().__init__("Invalid audit transport payload")


def _event(payload: bytes) -> dict:
    # Lazy import avoids a module cycle when dispatcher selects this sink.
    from .audit_outbox import _validate_payload
    try:
        if type(payload) is not bytes:
            raise ValueError()
        checked = _validate_payload(payload.decode("utf-8"))
        return json.loads(checked)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise AuditLogstashFormatError() from None


class AuditLogstashFormatter(JsonFormatter):
    def __init__(self):
        super().__init__(json_ensure_ascii=False,
                         json_serializer=partial(json.dumps, allow_nan=False, separators=(",", ":")))

    def add_fields(self, log_data, record, message_dict):
        # Never merge arbitrary LogRecord attributes or the global record factory.
        event = _event(record.msg)
        level, value, _ = _LEVELS[event["outcome"]]
        log_data.update({"@timestamp": event["timestamp"], "@version": "1",
            "message": event["action"], "logger_name": "ai_knowledge.audit",
            "thread_name": "audit-outbox", "level": level, "level_value": value,
            "application": "ai-knowledge", "audit_transport_version": 1})
        log_data.update(event)

    def format(self, record):
        try:
            result = super().format(record)
            if len(result.encode("utf-8")) > MAX_LOGSTASH_BYTES:
                raise AuditLogstashFormatError()
            return result
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise AuditLogstashFormatError() from None


def _record(payload):
    event = _event(payload)
    return logging.LogRecord("ai_knowledge.audit", _LEVELS[event["outcome"]][2],
                             "", 0, payload, (), None)


def format_logstash_payload(payload: bytes) -> bytes:
    return AuditLogstashFormatter().format(_record(payload)).encode("utf-8")


class AuditLogstashHandler(logging.Handler):
    def __init__(self, write_line):
        super().__init__()
        self.write_line = write_line
        self.setFormatter(AuditLogstashFormatter())

    def emit(self, record):
        # No StreamHandler.handleError: the dispatcher owns retries and markers.
        self.write_line(self.format(record).encode("utf-8"))


def make_logstash_sink(write_line):
    logger = logging.Logger("ai_knowledge.audit", level=logging.NOTSET)
    logger.propagate = False
    logger.addHandler(AuditLogstashHandler(write_line))

    def write(payload):
        logger.handle(_record(payload))

    return write
