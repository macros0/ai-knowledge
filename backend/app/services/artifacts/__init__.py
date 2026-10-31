"""Portable artifacts. Adapters do not start workers or connect during import."""


def get_blob_store(settings):
    from .factory import get_blob_store as build
    return build(settings)
