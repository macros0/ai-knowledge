"""Fenced document execution and cold checkpoint recovery."""
from sqlalchemy import select

from app.db.models import DocumentGeneration, DocumentGenerationState
from app.db.session import session_scope
from app.services.generation_store import abandon_generation, begin_generation, lock_document_write
from .context import document_execution as document_execution
from .context import bind_session
from .models import LostOwnership
from .store import assert_owner


def prepare_document_attempt(session, work, settings):
    with document_execution(work):
        bind_session(session, work)
        assert_owner(session, work)
        doc_id = work.spec.doc_id
        if not lock_document_write(session, doc_id, allow_deleted=False):
            raise LostOwnership("Document is missing or deleted")
        previous = session.scalar(select(DocumentGeneration).where(DocumentGeneration.work_id == work.work_id,
            DocumentGeneration.work_fence == work.fence))
        state = session.get(DocumentGenerationState, doc_id)
        if previous is not None:
            if (previous.doc_id != doc_id or previous.phase not in {"preparing", "ready"}
                    or state is None or state.candidate_generation_id != previous.id):
                raise LostOwnership("Work generation is no longer writable")
            return previous
        if state and state.candidate_generation_id:
            candidate = session.get(DocumentGeneration, state.candidate_generation_id)
            if candidate is not None and candidate.work_id not in {None, work.work_id}:
                from app.db.models import WorkItem
                owner = session.get(WorkItem, candidate.work_id)
                if owner is None or owner.status in {"queued", "running"} and not owner.cancel_requested:
                    raise LostOwnership("Document candidate belongs to a different live work item")
            abandon_generation(session, doc_id, state.candidate_generation_id)
        projection = None
        if settings.chunk_index_projection_enabled:
            from app.services.chunk_index_projection import CURRENT_CHUNK_PROJECTION_VERSION
            projection = CURRENT_CHUNK_PROJECTION_VERSION
        generation = begin_generation(session, doc_id, chunk_projection_version=projection)
        generation.work_id, generation.work_fence = work.work_id, work.fence
        from app.db.models import DocumentWorkInput, DocumentProcessingReview
        from .admission import validate_document_scope
        from copy import deepcopy
        frozen = session.get(DocumentWorkInput, work.work_id)
        if frozen is not None:
            if frozen.doc_id != doc_id:
                raise LostOwnership("Document input belongs to a different document")
            validate_document_scope(session, doc_id, frozen.processing_scope_json,
                work.spec.payload.get("source_file_hash"), frozen.scope_request_id)
            generation.processing_scope_json = deepcopy(frozen.processing_scope_json)
            if frozen.processing_scope_json is not None:
                from app.services.processing_scope import scope_summary
                generation.processing_scope_summary_json = scope_summary(frozen.processing_scope_json, generation.id)
                review = session.get(DocumentProcessingReview, doc_id)
                if review and review.id == frozen.processing_scope_json.get("review_id"):
                    review.generation_id = generation.id
        session.flush()
        return generation


def recover_document_attempt(work, settings, *, parser_version, source_file_hash, processing_scope_hash):
    """Rebind accepted checkpoints to a fresh namespace; stream one chunk at a time."""
    from app.services.staging import StagingStore
    from app.services.artifacts.staging import PortableStaging
    from app.services.generation_store import lock_generation_read
    from app.services.generation_artifacts import GenerationIntegrityError
    with document_execution(work):
        staging = PortableStaging(StagingStore(work.spec.doc_id), settings)
        snapshot = staging._snapshot()
        refs = {}
        source_scope, source_projection, source_table_version = None, None, None
        if snapshot:
            expected = dict(parser_version=parser_version, source_file_hash=source_file_hash,
                            processing_scope_hash=processing_scope_hash)
            if (not parser_version or not source_file_hash
                    or any(snapshot[name] != value for name, value in expected.items())):
                raise GenerationIntegrityError()
            with session_scope() as session:
                lock_generation_read(session, [work.spec.doc_id])
                from copy import deepcopy
                source = session.get(DocumentGeneration, snapshot["generation_id"])
                if source is None or work.spec.payload["action"] != "resume" and source.work_id != work.work_id:
                    raise GenerationIntegrityError()
                source_scope, source_projection = deepcopy(source.processing_scope_json), source.chunk_projection_version
                source_table_version = source.table_schema_version
                if (source_scope or {}).get("selection_hash") != processing_scope_hash:
                    raise GenerationIntegrityError()
                for key, info in snapshot["chunks_data"].items():
                    if info:
                        refs[int(key)] = staging._assert_current(session, snapshot, int(key), info, recovery=True)
        with session_scope() as session:
            generation = prepare_document_attempt(session, work, settings)
            new_id = generation.id
            if snapshot and snapshot["generation_id"] != new_id:
                generation.processing_scope_json = source_scope
                generation.chunk_projection_version = source_projection
                generation.table_schema_version = source_table_version
                if source_scope is not None:
                    from app.services.processing_scope import scope_summary
                    generation.processing_scope_summary_json = scope_summary(source_scope, new_id)
                _copy_source_results(session, work.spec.doc_id, snapshot["generation_id"], new_id)
                _copy_table_results(session, settings, work.spec.doc_id, snapshot["generation_id"], new_id)
        if snapshot and snapshot["generation_id"] != new_id:
            class Bodies:
                def items(self):
                    def rebind(refs):
                        return [{**item, "generation_id": new_id} if isinstance(item, dict)
                            and item.get("generation_id") == snapshot["generation_id"] else item for item in refs]
                    for index, ref in sorted(refs.items()):
                        info = snapshot["chunks_data"][str(index)]
                        body = staging._read_ref(snapshot, index, info, ref)
                        table_refs = body["info"].get("table_refs")
                        if isinstance(table_refs, list):
                            body["info"]["table_refs"] = rebind(table_refs)
                        for concept in body["concepts"] or []:
                            if isinstance(concept.get("table_refs"), list):
                                concept["table_refs"] = rebind(concept["table_refs"])
                        yield index, body
            staging._commit(snapshot, Bodies(), generation_id=new_id)
        return new_id


def _copy_source_results(session, doc_id, source_id, target_id):
    """Share immutable source graphs; only their generation-scoped SQL ownership changes."""
    from app.db.models import ArtifactBinding, ArtifactObject, SourceCacheHead
    from app.services.artifacts.repository import _ref, attach_blob
    from app.services.generation_artifacts import GenerationIntegrityError
    heads = {}
    rows = session.scalars(select(SourceCacheHead).where(SourceCacheHead.doc_id == doc_id,
        SourceCacheHead.generation_id.in_(["", source_id])).order_by(SourceCacheHead.generation_id, SourceCacheHead.cache_id))
    for head in rows:
        heads[head.cache_id] = head  # A generation's accepted version takes precedence over the shared cache.
    bindings = []
    for head in heads.values():
        prefix = f"source-cache/{head.cache_id}/{head.object_id}/"
        members = list(session.scalars(select(ArtifactBinding).where(ArtifactBinding.owner_type == "source",
            ArtifactBinding.owner_id == doc_id, ArtifactBinding.generation_id == head.generation_id,
            ArtifactBinding.logical_name.startswith(prefix))))
        if not any(row.logical_name == prefix + "manifest" and row.object_id == head.object_id for row in members):
            raise GenerationIntegrityError()
        bindings.extend(members)
    for binding in sorted(bindings, key=lambda item: (item.object_id, item.logical_name)):
        row = session.get(ArtifactObject, binding.object_id)
        if row is None or row.state != "ready":
            raise GenerationIntegrityError()
        attach_blob(session, ref=_ref(row), owner_type="source", owner_id=doc_id, generation_id=target_id,
                    logical_name=binding.logical_name)
    for head in heads.values():
        target = session.get(SourceCacheHead, (doc_id, target_id, head.cache_id))
        if target is not None:
            if target.object_id != head.object_id:
                raise GenerationIntegrityError()
        else:
            session.add(SourceCacheHead(doc_id=doc_id, generation_id=target_id, cache_id=head.cache_id,
                source_key=head.source_key, content_fingerprint=head.content_fingerprint, object_id=head.object_id))
    session.flush()


def _copy_table_results(session, settings, doc_id, source_id, target_id):
    from app.db.models import DocumentTable
    from app.services.table_store import clone_selected_generation_tables, verify_generation_tables
    from app.services.generation_files import generation_paths
    from app.services.artifacts.publication import canonical_hash
    from app.services.generation_artifacts import GenerationIntegrityError
    source = list(session.scalars(select(DocumentTable).where(DocumentTable.doc_id == doc_id, DocumentTable.generation_id == source_id)))
    target = list(session.scalars(select(DocumentTable).where(DocumentTable.doc_id == doc_id, DocumentTable.generation_id == target_id)))
    if target:
        def inventory(rows):
            return {row.table_id: canonical_hash({"descriptor": row.descriptor, "quality": row.quality,
                                                 "source_id": row.source_id, "schema_version": row.schema_version}) for row in rows}
        if inventory(source) != inventory(target) or verify_generation_tables(session, doc_id, source_id) != verify_generation_tables(session, doc_id, target_id):
            raise GenerationIntegrityError()
    elif source:
        clone_selected_generation_tables(session, doc_id, source_id, target_id,
            generation_paths(settings, doc_id, target_id).bundle, {row.source_id for row in source}, export_artifacts=False)


def run_document_job(work):
    """Execute with no SQL transaction around parser/LLM; publication owns success."""
    from copy import deepcopy
    from app import error_codes as codes
    from app.db.models import DocumentStaging, WorkItem, DocumentWorkInput
    from app.services.artifacts.original import original_path
    from app.services.artifacts.repository import resolve_blob
    from app.services.errors import public_error_code, processing_error_code
    from app.services.generation_artifacts import file_digest, GenerationIntegrityError
    from app.services.pipeline import get_pipeline, ParseContext, set_table_cache_fresh_since
    from docparser.attachment_policy import ParserProcessingPolicy
    from .handlers import TransientWorkError
    pipeline = get_pipeline()
    settings, doc_id = pipeline.settings, work.spec.doc_id
    with document_execution(work):
        try:
            if set(work.spec.payload) - {"action", "doc_id", "source_file_hash"}:
                raise ValueError("Unsupported document work fields")
            metadata = pipeline.registry.get(doc_id)
            if metadata is None or metadata.get("deleted_at") is not None:
                raise LostOwnership("Document is missing or deleted")
            with session_scope() as session:
                ref = resolve_blob(session, owner_type="original", owner_id=doc_id, generation_id=None, logical_name="original")
                if ref is None or ref.sha256 != work.spec.payload.get("source_file_hash"):
                    raise GenerationIntegrityError()
                snapshot = session.get(DocumentStaging, doc_id)
                state = session.get(DocumentGenerationState, doc_id)
                source_id = snapshot.generation_id if snapshot else state.active_generation_id if state else None
                source = session.get(DocumentGeneration, source_id) if source_id else None
                same_work = source is not None and source.work_id == work.work_id
                reuse = snapshot is not None and (work.spec.payload["action"] == "resume" or same_work)
                if reuse and snapshot.checkpoint_version != 2:
                    raise GenerationIntegrityError()
                if not reuse:
                    source = session.get(DocumentGeneration, state.active_generation_id) if state and state.active_generation_id else None
                scope = deepcopy(source.processing_scope_json) if source else None
                frozen = session.get(DocumentWorkInput, work.work_id)
                if frozen is not None:
                    scope = deepcopy(frozen.processing_scope_json)
                elif settings.okf_deployment_schema == "replicas-v1":
                    raise GenerationIntegrityError()
            path = original_path(settings, doc_id, metadata["filename"])
            source_hash = file_digest(path)
            if source_hash != ref.sha256:
                raise GenerationIntegrityError()
            policy = ParserProcessingPolicy(scope) if scope else None
            parser = ParseContext(metadata["filename"], mail_enabled=settings.mail_import_enabled,
                attachment_max_depth=settings.attachment_max_depth, processing_policy=policy).parser_version
            if reuse:
                generation_id = recover_document_attempt(work, settings, parser_version=parser, source_file_hash=source_hash,
                    processing_scope_hash=(scope or {}).get("selection_hash"))
            else:
                with session_scope() as session:
                    generation_id = prepare_document_attempt(session, work, settings).id
            pipeline._process(doc_id, str(path), metadata["filename"], metadata.get("tags") or [], resume=reuse,
                fresh_table_cache=work.spec.payload["action"] == "regenerate" and not reuse,
                processing_scope=scope, admitted_generation_id=generation_id)
        except LostOwnership:
            raise
        except Exception as exc:
            pipeline.registry.update(doc_id, status="paused", error="Обработка прервана", error_code=processing_error_code(exc))
            if public_error_code(exc) in {codes.DEPENDENCY_UNAVAILABLE, codes.TIMEOUT, codes.RATE_LIMITED}:
                raise TransientWorkError("storage_unavailable") from exc
            raise
        finally:
            set_table_cache_fresh_since(None)
    # The document transaction already acknowledged successful publication.
    with session_scope() as session:
        row = session.get(WorkItem, work.work_id)
        if row is None or row.fence != work.fence:
            raise LostOwnership("Document execution was superseded")
        if row.status == "succeeded":
            return
    metadata = pipeline.registry.get(doc_id)
    if metadata and metadata.get("error_code") in {codes.PROCESSING_UNAVAILABLE, codes.GENERATION_TIMEOUT, codes.GENERATION_RATE_LIMITED}:
        raise TransientWorkError("storage_unavailable")
    raise ValueError("Document processing stopped before publication")
