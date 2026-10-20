"""Payload-free process counters; record counts follow the root DB commit."""
from collections import Counter
import threading
from sqlalchemy import event
from sqlalchemy.orm import Session

_KEY = "audit_commit_counts"
_lock = threading.Lock()
_counts = Counter(recorded=0, denied=0)


def snapshot():
    with _lock:
        return dict(_counts)


def track_record(session, *, denied):
    transaction = session.get_nested_transaction() or session.get_transaction()
    bucket = session.info.setdefault(_KEY, {}).setdefault(transaction, [0, 0, False])
    bucket[0] += 1
    bucket[1] += int(denied)


@event.listens_for(Session, "after_commit")
def _committed(session):
    transaction = session.get_nested_transaction() or session.get_transaction()
    bucket = session.info.get(_KEY, {}).get(transaction)
    if bucket is not None:
        bucket[2] = True


@event.listens_for(Session, "after_transaction_end")
def _ended(session, transaction):
    buckets = session.info.get(_KEY)
    if buckets is None:
        return
    bucket = buckets.pop(transaction, None)
    if bucket is not None and bucket[2]:
        if transaction.parent is not None:
            parent = buckets.setdefault(transaction.parent, [0, 0, False])
            parent[0] += bucket[0]
            parent[1] += bucket[1]
        else:
            with _lock:
                _counts["recorded"] += bucket[0]
                _counts["denied"] += bucket[1]
    if not buckets:
        session.info.pop(_KEY, None)
