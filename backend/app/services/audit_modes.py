from dataclasses import dataclass


@dataclass(frozen=True)
class AuditMode:
    db_enabled: bool
    stdout_enabled: bool

    @property
    def effective_mode(self) -> str:
        return 'both' if self.db_enabled and self.stdout_enabled else 'db' if self.db_enabled else 'external'


def get_audit_mode(settings) -> AuditMode:
    db_enabled = getattr(settings, 'audit_db_enabled', True)
    stdout_mode = getattr(settings, 'audit_stdout_mode', 'disabled')
    if type(db_enabled) is not bool or stdout_mode not in {'disabled', 'durable'}:
        raise ValueError('Invalid audit configuration')
    if not db_enabled and stdout_mode == 'disabled':
        raise ValueError('At least one audit destination is required')
    return AuditMode(db_enabled=db_enabled, stdout_enabled=stdout_mode == 'durable')
