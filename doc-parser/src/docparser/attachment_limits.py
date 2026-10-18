"""Shared inclusive attachment depth: uploaded root=0, one file edge=one level."""

DEFAULT_ATTACHMENT_MAX_DEPTH = 6
MAX_CONFIGURABLE_ATTACHMENT_DEPTH = 32


def validate_attachment_depth(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_CONFIGURABLE_ATTACHMENT_DEPTH:
        raise ValueError('Attachment depth must be an integer between 0 and 32')
    return value
