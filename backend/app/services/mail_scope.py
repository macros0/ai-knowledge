"""Canonical mail ancestry classification without I/O or persistent caches."""
from __future__ import annotations
from typing import Literal

MAIL_SCOPE_VERSION = 1
MailScope = Literal['mail', 'document', 'unknown']
MailMode = Literal['all', 'exclude', 'only']


def build_mail_scope_map(sources: list[dict]) -> dict[str, MailScope]:
    """Validate complete ancestry iteratively in O(S), including broken mail edges."""
    ids = [row.get('source_id') for row in sources]
    valid_ids = {sid for sid in ids if isinstance(sid, str) and sid}
    unknown: dict[str, MailScope] = dict.fromkeys(valid_ids, 'unknown')
    if len(valid_ids) != len(ids):
        return unknown
    nodes = {row['source_id']: row for row in sources}
    if ('root' not in nodes or nodes['root'].get('parent_source_id') is not None
            or sum(row.get('parent_source_id') is None for row in sources) != 1):
        return unknown
    scopes: dict[str, MailScope] = {}
    for sid in nodes:
        chain = []
        seen = set()
        current = sid
        scope: MailScope = 'unknown'
        while current not in scopes:
            if current not in nodes or current in seen:
                break
            seen.add(current)
            chain.append(current)
            if current == 'root':
                scope = 'document'
                break
            parent = nodes[current].get('parent_source_id')
            if not isinstance(parent, str) or not parent:
                break
            current = parent
        else:
            scope = scopes[current]
        for current in reversed(chain):
            row = nodes[current]
            meta = row.get('metadata')
            meta = meta if isinstance(meta, dict) else {}
            if scope != 'unknown' and (row.get('kind') == 'mail' or meta.get('mail') is True):
                scope = 'mail'
            scopes[current] = scope
    return scopes


def classify_mail_scope(source_id: str | None, sources: list[dict]) -> MailScope:
    return build_mail_scope_map(sources).get(source_id, 'unknown')


def mail_scope_allowed(scope: MailScope, mail_mode: MailMode) -> bool:
    if mail_mode not in ('all', 'exclude', 'only'):
        raise ValueError('Invalid mail mode')
    return mail_mode == 'all' or scope == ('mail' if mail_mode == 'only' else 'document')


def build_record_mail_scopes(sources: list[dict], concepts: list[dict],
                            chunks: list[dict]) -> tuple[list[MailScope], list[MailScope]]:
    scopes = build_mail_scope_map(sources)
    chunks_by_index = {row.get('chunk_index'): row for row in chunks}
    concept_scopes = []
    for row in concepts:
        source = row.get('source_id')
        scope = scopes.get(source, 'unknown')
        chunk = chunks_by_index.get(row.get('chunk_index'))
        if chunk and source and chunk.get('source_id') and source != chunk['source_id']:
            scope = 'unknown'
        concept_scopes.append(scope)
    return concept_scopes, [scopes.get(row.get('source_id'), 'unknown') for row in chunks]
