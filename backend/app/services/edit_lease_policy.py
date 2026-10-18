"""Shared values and deadline calculation; contains no domain access or SQL."""
from datetime import timedelta, timezone

LEASE_SECONDS = 180
IDLE_SECONDS = 600
HEARTBEAT_SECONDS = 60
WARNING_SECONDS = 60


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def compute_deadlines(now, idle_expires_at, *, record_activity):
    idle = now + timedelta(seconds=IDLE_SECONDS) if record_activity else utc(idle_expires_at)
    return min(now + timedelta(seconds=LEASE_SECONDS), idle), idle
