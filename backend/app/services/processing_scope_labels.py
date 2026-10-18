"""Read-only display enrichment for inventories saved before icon captions existed."""
from pathlib import Path
from tempfile import TemporaryDirectory
from collections import OrderedDict
from threading import Lock

from sqlalchemy import select

from app.db.models import DocumentGenerationState, DocumentProcessingReview, DocumentSource
from app.db.session import session_scope
from app.services.generation_files import GenerationStorageError, _reject_links, processing_review_paths
from app.services.generation_store import lock_document_write
from app.services.parser_supervisor import parse_document_supervised
from app.services.processing_scope import ProcessingScopeConflict, scope_for_generation
from app.services.processing_scope_originals import _copy_original


class LabelCache:
    """Bounded process-local metadata only; callers still verify original bytes."""

    def __init__(self, max_items=32, max_bytes=4 * 1024 * 1024):
        self.items = OrderedDict()
        self.max_items, self.max_bytes, self.size = max_items, max_bytes, 0
        self.lock = Lock()

    def get(self, fingerprint):
        with self.lock:
            entry = self.items.get(fingerprint)
            if entry is None:
                return None
            self.items.move_to_end(fingerprint)
            return dict(entry[0])

    def put(self, fingerprint, value):
        size = 128 + sum(len(key.encode('utf-8')) + len(label.encode('utf-8')) + 256
                         for key, label in value.items())
        if size > self.max_bytes:
            return
        with self.lock:
            old = self.items.pop(fingerprint, None)
            if old is not None:
                self.size -= old[1]
            self.items[fingerprint] = (dict(value), size)
            self.size += size
            while len(self.items) > self.max_items or self.size > self.max_bytes:
                _, removed = self.items.popitem(last=False)
                self.size -= removed[1]


_label_cache = LabelCache()


def scope_attachment_labels(settings, doc_id, document, scope_type, scope_id):
    with TemporaryDirectory(prefix='attachment-labels-') as folder:
        inputs, nodes, total, fingerprints = [], [], 0, {}
        with session_scope() as session:
            if not lock_document_write(session, doc_id, allow_deleted=False):
                raise ProcessingScopeConflict()
            review = session.get(DocumentProcessingReview, doc_id)
            if scope_type == 'pending':
                if not review or review.id != scope_id or review.state in {'published', 'canceled'}:
                    raise ProcessingScopeConflict()
                snapshot = {'inventory': review.inventory_json, 'review_id': review.id}
            else:
                state = session.get(DocumentGenerationState, doc_id)
                if not state or state.active_generation_id != scope_id:
                    raise ProcessingScopeConflict()
                snapshot = scope_for_generation(session, doc_id, scope_id) or {}
            inventory = snapshot.get('inventory') or {}
            nodes = inventory.get('nodes', [])
            parents = {n['parent_key'] for n in nodes if not n.get('display_label')}
            document_root = (settings.uploads_dir / doc_id).resolve()
            published = {
                (s.metadata_json or {}).get('source_key'): s.saved_path
                for s in session.scalars(select(DocumentSource).where(DocumentSource.doc_id == doc_id))
                if s.saved_path
            } if scope_type == 'active' else {}
            review_id = snapshot.get('review_id')
            candidates = []
            if 'root' in parents and Path(document['filename']).suffix.lower() == '.docx':
                candidates.append(('root', settings.uploads_dir / (doc_id + '.docx'), inventory.get('source_file_hash')))
            for node in nodes:
                if node['source_key'] not in parents or node['kind'] != 'docx':
                    continue
                name = node.get('saved_path')
                path = None
                if node['source_key'] in published:
                    path = document_root / published[node['source_key']]
                    _reject_links(path)
                    if not path.resolve().is_relative_to(document_root):
                        raise ProcessingScopeConflict()
                elif review_id and name and Path(name).name == name:
                    path = processing_review_paths(settings, doc_id, review_id) / 'attachments' / name
                if path:
                    candidates.append((node['source_key'], path, node.get('content_fingerprint')))
            for parent, path, fingerprint in candidates[:32]:
                _reject_links(path)
                if not fingerprint or not path.is_file():
                    continue
                total += path.stat().st_size
                if total > 50 * 1024 * 1024:
                    break
                name = f'{len(inputs)}.docx'
                try:
                    _copy_original(path, Path(folder) / name, fingerprint)
                except GenerationStorageError as exc:
                    raise ProcessingScopeConflict() from exc
                inputs.append({'parent_key': parent, 'filename': name})
                fingerprints[parent] = fingerprint
        labels = {}
        by_parent, missing = {}, []
        for item in inputs:
            cached = _label_cache.get(fingerprints[item['parent_key']])
            if cached is None:
                missing.append(item)
            else:
                by_parent[item['parent_key']] = cached
        if missing:
            result = parse_document_supervised(
                Path(folder) / missing[0]['filename'], missing[0]['filename'],
                attachments_dir=Path(folder) / 'worker',
                timeout_seconds=min(10, settings.parser_timeout_seconds),
                max_memory_mb=settings.parser_max_memory_mb,
                max_concurrent=settings.parser_max_concurrent,
                _inspection_options={'attachment_labels': missing},
            )
            for item in missing:
                parent = item['parent_key']
                value = (result.attachment_labels or {}).get(parent)
                if value is not None:
                    by_parent[parent] = value
                    _label_cache.put(fingerprints[parent], value)
        for node in nodes:
            label = by_parent.get(node['parent_key'], {}).get(node['locator'])
            if label:
                labels[node['source_key']] = {'display_label': label, 'content_fingerprint': node.get('content_fingerprint')}
        return {'scope_type': scope_type, 'scope_id': scope_id, 'labels': labels}
