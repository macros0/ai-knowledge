"""Reload selected search excerpts from canonical storage, without another search."""
from copy import deepcopy
from hashlib import sha256
import re

from app.api.errors import ApiError
from app.services.fusion import Hit
from app.services.retrieval_hydration import load_visible_retrieval_hits


def excerpt_reference(hit, excerpt):
    """Persist identity and offsets, never a second copy of the evidence text."""
    payload = hit.payload
    start = payload.get('content', '').find(excerpt)
    if start < 0 or not excerpt or payload.get('_canonical_verified') is not True:
        return None
    return {
        **{key: payload.get(key) for key in ('doc_id', 'point_type', 'slug', 'chunk_index',
                                            'source_id', 'generation_id', 'chunk_fragment', '_mail_chunk')},
        'start': start, 'length': len(excerpt), 'digest': sha256(excerpt.encode()).hexdigest(),
    }


def snapshot_blocks(blocks):
    result = []
    for block in blocks:
        excluded = {'content', 'concept_content', 'mail_fragment', '_concept_evidence'}
        if block.get('table_refs'):
            excluded.remove('content')
        saved = {key: deepcopy(value) for key, value in block.items() if key not in excluded}
        if block.get('table_refs'):
            saved['_table_snapshot_digest'] = sha256(block['content'].encode()).hexdigest()
        result.append(saved)
    return result


def _key(ref):
    if (not isinstance(ref, dict) or not isinstance(ref.get('doc_id'), str)
            or not ref.get('doc_id') or ref.get('point_type', 'chunk') not in ('chunk', 'concept')):
        _changed()
    kind = ref.get('point_type', 'chunk')
    identity = ref.get('slug') if kind == 'concept' else ref.get('chunk_index')
    descriptor = ref.get('chunk_fragment')
    if descriptor is not None:
        from app.services.chunk_index_projection import chunk_fragment_point_id_from_descriptor
        try:
            identity = chunk_fragment_point_id_from_descriptor(ref['doc_id'],ref.get('chunk_index'),
                ref.get('generation_id'),ref.get('source_id'),descriptor)
        except (ValueError, TypeError, KeyError):
            _changed()
    return (ref['doc_id'], kind, identity, ref.get('generation_id'), ref.get('source_id'), ref.get('_mail_chunk') is True)


def _changed():
    raise ApiError(status_code=409, code='chat_sources_changed',
                   detail='Выбранные источники изменились. Повторите поиск.')


def restore_blocks(snapshots, *, mail_mode):
    if not isinstance(snapshots, list) or any(not isinstance(b, dict) for b in snapshots):
        _changed()
    if any(block.get('table_refs') for block in snapshots):
        from app.services.table_retrieval import restore_table_snapshot
        result = []
        for index, block in enumerate(snapshots, 1):
            restored = (restore_table_snapshot(block, mail_mode=mail_mode) if block.get('table_refs')
                        else restore_blocks([block], mail_mode=mail_mode)[0])
            restored['_source_index'] = index
            result.append(restored)
        return result
    refs = {}
    char_limit = 1
    for block in snapshots:
        evidence = block.get('_evidence')
        if not evidence:
            _changed()
        components = block.get('_mail_components') or []
        if not isinstance(components, list) or any(not isinstance(c, dict) for c in components):
            _changed()
        for excerpt_ref in (evidence, block.get('_mail_evidence')):
            if excerpt_ref is None:
                continue
            if (not isinstance(excerpt_ref, dict) or type(excerpt_ref.get('start')) is not int
                    or excerpt_ref['start'] < 0 or type(excerpt_ref.get('length')) is not int
                    or excerpt_ref['length'] <= 0 or not isinstance(excerpt_ref.get('digest'), str)
                    or re.fullmatch(r'[0-9a-f]{64}', excerpt_ref['digest']) is None
                    or (excerpt_ref.get('chunk_fragment') is not None
                        and excerpt_ref['start'] + excerpt_ref['length'] > 2000)
                    or (excerpt_ref.get('_mail_chunk') is True
                        and excerpt_ref.get('point_type') != 'concept')):
                _changed()
        for ref in [*components, evidence, block.get('_mail_evidence')]:
            if not ref:
                continue
            refs[_key(ref)] = {**ref, 'point_type': ref.get('point_type', 'chunk')}
            char_limit = max(char_limit, ref.get('start', 0) + ref.get('length', 0))
    from app.services.chunk_index_projection import chunk_fragment_point_id_from_descriptor
    from app.services.vector_store import chunk_point_id
    hits = []
    for i, ref in enumerate(refs.values()):
        if ref.get('chunk_fragment') is not None:
            point_id = chunk_fragment_point_id_from_descriptor(ref['doc_id'],ref['chunk_index'],
                ref.get('generation_id'),ref.get('source_id'),ref['chunk_fragment'])
        elif ref.get('point_type') == 'chunk':
            point_id = chunk_point_id(ref['doc_id'],ref['chunk_index'],generation_id=ref.get('generation_id'))
        else:
            point_id = str(i)
        hits.append(Hit(point_id, 1., deepcopy(ref)))
    visible, _ = load_visible_retrieval_hits(hits, mail_mode=mail_mode,
                                          max_concept_chars=char_limit, max_chunk_chars=char_limit,
                                          hydrate_all_chunks=True)
    if len(visible) != len(hits):
        _changed()
    payloads = {_key(hit.payload): hit.payload for hit in visible}
    for key, ref in refs.items():
        payload = payloads.get(key)
        if payload is None:
            _changed()
        if (payload.get('_canonical_verified') is not True
                or payload.get('source_id') != ref.get('source_id')
                or payload.get('chunk_index') != ref.get('chunk_index')):
            _changed()

    def excerpt(ref):
        payload = payloads[_key(ref)]
        start = ref['start']
        content = ((payload.get('mail_fragment') or {}).get('content', '')
                   if ref.get('_mail_chunk') is True else payload.get('content', ''))
        text = content[start:start + ref['length']]
        if sha256(text.encode()).hexdigest() != ref['digest']:
            _changed()
        return text

    result = []
    for index, saved in enumerate(snapshots, 1):
        block = deepcopy(saved)
        block['content'] = excerpt(block['_evidence'])
        block['_source_index'] = index
        primary = {'doc_id': block['doc_id'], 'point_type': block['point_type'],
                   'slug': block.get('source_slug'), 'chunk_index': block.get('chunk_index')}
        primary.update({key: block.get(key) for key in ('generation_id','source_id')})
        if block['point_type'] == 'chunk':
            primary['chunk_fragment'] = block['_evidence'].get('chunk_fragment')
        block['source_path'] = payloads.get(_key(primary), {}).get('source_path')
        if block.get('_mail_evidence'):
            block['mail_fragment'] = {'chunk_index': block['_mail_evidence']['chunk_index'],
                                      'content': excerpt(block['_mail_evidence'])}
        result.append(block)
    return result
