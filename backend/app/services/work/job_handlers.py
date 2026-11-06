"""Explicit public-job adapters, loaded without constructing queues."""


def export_job(work):
    from app.services.export_queue import get_export_queue
    get_export_queue().execute_work(work)


def diagnostic_bundle(work):
    from app.services.diagnostics.runtime import entrypoint_bundle_queue
    return entrypoint_bundle_queue().execute_work(work)
