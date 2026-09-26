"""Typed disk exhaustion must escape recoverable malformed-child handlers."""
import errno


def is_storage_full_error(exc: BaseException) -> bool:
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and (
            current.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", 122)}
            or getattr(current, "winerror", None) in {39, 112}
        ):
            return True
        current = current.__cause__ or current.__context__
    return False
