"""Model-free restore hold for the exact supported v1 and v2 queue schemas."""
import re

from sqlalchemy import DateTime, Integer, String, Text, inspect, text


def _expression(value):
    parts = []
    for token in re.findall(r"'(?:''|[^'])*'|[^']+", value):
        if token.startswith("'"):
            parts.append(token)
        else:
            token = re.sub(r"\s+", "", token.lower())
            token = re.sub(r"::(?:charactervarying|text)(?:\[\])?", "", token)
            parts.append(token.replace('(', '').replace(')', '').replace('"', ''))
    return ''.join(parts)


def _unconditional(item):
    return not any(value is not None for key, value in (item.get('dialect_options') or {}).items()
                   if key.endswith('_where'))


def validate_outbox_contract(connection):
    """Return the supported version; refuse drift before any restore mutation."""
    inspector = inspect(connection)
    if 'audit_outbox' not in inspector.get_table_names():
        raise ValueError('Unknown audit outbox schema')
    columns = {c['name']: c for c in inspector.get_columns('audit_outbox')}
    version = 1 if 'audit_id' in columns else 2
    identity = 'audit_id' if version == 1 else 'id'
    expected = {
        identity: (Integer(), False), 'payload': (Text(), False),
        'status': (String(16), False), 'attempt_count': (Integer(), False),
        'next_attempt_at': (DateTime(timezone=True), False),
        'last_error_code': (String(32), True), 'stdout_written_at': (DateTime(timezone=True), True),
    }
    if version == 2:
        expected.update(event_id=(String(36), False), created_at=(DateTime(timezone=True), False))
    if set(columns) != set(expected):
        raise ValueError('Unknown audit outbox schema')
    for name, (kind, nullable) in expected.items():
        actual = columns[name]
        if (actual['nullable'] != nullable
                or actual['type'].compile(dialect=connection.dialect) != kind.compile(dialect=connection.dialect)):
            raise ValueError('Unknown audit outbox schema')
    if inspector.get_pk_constraint('audit_outbox').get('constrained_columns') != [identity]:
        raise ValueError('Unknown audit outbox schema')
    keys = inspector.get_foreign_keys('audit_outbox')
    if version == 1:
        if ('audit_log' not in inspector.get_table_names()
                or inspector.get_pk_constraint('audit_log').get('constrained_columns') != ['id']
                or len(keys) != 1):
            raise ValueError('Unknown audit outbox schema')
        key = keys[0]
        options = key.get('options') or {}
        if (key.get('constrained_columns') != ['audit_id'] or key.get('referred_table') != 'audit_log'
                or key.get('referred_columns') != ['id'] or key.get('referred_schema') is not None
                or any(options.get(k) not in (None, 'NO ACTION') for k in ('ondelete', 'onupdate'))
                or options.get('deferrable')):
            raise ValueError('Unknown audit outbox schema')
    elif keys:
        raise ValueError('Unknown audit outbox schema')
    checks = {c.get('name'): _expression(c['sqltext']) for c in inspector.get_check_constraints('audit_outbox')}
    if (set(checks) != {'ck_audit_outbox_status', 'ck_audit_outbox_attempt_count'}
            or checks['ck_audit_outbox_status'] not in {
                "statusin'pending','held','stdout_written'",
                "status=anyarray['pending','held','stdout_written']",
            } or checks['ck_audit_outbox_attempt_count'] != 'attempt_count>=0'):
        raise ValueError('Unknown audit outbox schema')
    indexes = inspector.get_indexes('audit_outbox')
    if not any(c['column_names'] == ['status', 'next_attempt_at', identity]
               and not c.get('unique', False) and _unconditional(c) for c in indexes):
        raise ValueError('Unknown audit outbox schema')
    if version == 2:
        unique = inspector.get_unique_constraints('audit_outbox') + indexes
        if (not any(c.get('column_names') == ['event_id'] and c.get('unique', True)
                    and _unconditional(c) for c in unique)
                or any(c.get('unique', True)
                       and (c.get('column_names') != ['event_id'] or not _unconditional(c))
                       for c in unique)):
            raise ValueError('Unknown audit outbox schema')
    return version


def hold_pending(engine, reason="restored_backup"):
    if reason not in {"restored_backup", "binary_rollback"}:
        raise ValueError("Invalid hold reason")
    with engine.begin() as connection:
        if "audit_outbox" not in inspect(connection).get_table_names():
            return 0
        validate_outbox_contract(connection)
        states = set(connection.execute(text("SELECT DISTINCT status FROM audit_outbox")).scalars())
        if not states.issubset({"pending", "held", "stdout_written"}):
            raise ValueError("Unknown audit outbox state")
        result = connection.execute(text("UPDATE audit_outbox SET status='held',last_error_code=:reason WHERE status='pending'"), {"reason": reason})
        return result.rowcount
