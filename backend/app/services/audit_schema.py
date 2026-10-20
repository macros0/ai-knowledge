"""Narrow additive audit upgrade; unknown drift is never stamped away."""
from sqlalchemy import inspect, text
from app.db.models import AuditLog, AuditOutbox
from app.services.audit_hold import validate_outbox_contract

FIELDS = ("event_id", "schema_version", "actor_type", "method", "reason", "outcome", "request_id", "ip_source")


def _compatible_columns(connection, table, expected):
    actual={c["name"]:c for c in inspect(connection).get_columns(table)}
    if set(actual) != set(expected):
        raise ValueError("Unknown audit schema")
    for name,column in expected.items():
        current=actual.get(name)
        if current is None or current["type"].compile(dialect=connection.dialect) != column.type.compile(dialect=connection.dialect):
            raise ValueError("Unknown audit schema")
        length=getattr(column.type,"length",None)
        if length is not None and getattr(current["type"],"length",None)!=length:
            raise ValueError("Unknown audit schema")
        if name not in inspect(connection).get_pk_constraint(table).get("constrained_columns",[]) and current["nullable"] != column.nullable:
            raise ValueError("Unknown audit schema")


def validate_audit_schema(connection):
    i=inspect(connection)
    if not {"audit_log","audit_outbox"}.issubset(i.get_table_names()):
        raise ValueError("Audit migration required")
    _compatible_columns(connection,"audit_log",{c.name:c for c in AuditLog.__table__.c})
    _compatible_columns(connection,"audit_outbox",{c.name:c for c in AuditOutbox.__table__.c})
    unique=i.get_unique_constraints("audit_log")+i.get_indexes("audit_log")
    if not any(c.get("column_names")==["event_id"] and c.get("unique",True) and not any(v is not None for k,v in (c.get("dialect_options") or {}).items() if k.endswith("_where")) for c in unique):
        raise ValueError("Audit unique index required")
    validate_outbox_contract(connection)


def upgrade_audit_schema(connection):
    i=inspect(connection)
    if "audit_log" not in i.get_table_names():raise ValueError("Legacy audit table required")
    present=set(FIELDS)&{c["name"] for c in i.get_columns("audit_log")}
    if present:
        validate_audit_schema(connection)
        return
    if "audit_outbox" in i.get_table_names():raise ValueError("Unknown audit schema")
    legacy={c.name:c for c in AuditLog.__table__.c if c.name not in FIELDS}
    _compatible_columns(connection,"audit_log",legacy)
    for name in FIELDS:
        kind=AuditLog.__table__.c[name].type.compile(dialect=connection.dialect)
        connection.execute(text(f'ALTER TABLE audit_log ADD COLUMN {name} {kind}'))
    connection.execute(text("CREATE UNIQUE INDEX ix_audit_log_event_id ON audit_log(event_id)"))
    AuditOutbox.__table__.create(connection)
    validate_audit_schema(connection)
