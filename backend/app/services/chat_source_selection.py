"""Reload selected search excerpts from canonical storage, without another search."""
from copy import deepcopy
from hashlib import sha256

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
                                            'source_id', 'generation_id')},
        'start': start, 'length': len(excerpt), 'digest': sha256(excerpt.encode()).hexdigest(),
    }


def snapshot_blocks(blocks):
    return [{key: deepcopy(value) for key, value in block.items()
             if key not in {'content', 'concept_content', 'mail_fragment', '_concept_evidence'}}
            for block in blocks]


def _key(ref):
    kind = ref.get('point_type', 'chunk')
    return (ref['doc_id'], kind, ref.get('slug') if kind == 'concept' else ref.get('chunk_index'))


def _changed():
    raise ApiError(status_code=409, code='chat_sources_changed',
                   detail='Выбранные источники изменились. Повторите поиск.')


def restore_blocks(snapshots, *, mail_mode):
    refs = {}
    char_limit = 1
    for block in snapshots:
        evidence = block.get('_evidence')
        if not evidence:
            _changed()
        for ref in [*(block.get('_mail_components') or []), evidence, block.get('_mail_evidence')]:
            if not ref:
                continue
            refs[_key(ref)] = {**ref, 'point_type': ref.get('point_type', 'chunk')}
            char_limit = max(char_limit, ref.get('start', 0) + ref.get('length', 0))
    hits = [Hit(str(i), 1., deepcopy(ref)) for i, ref in enumerate(refs.values())]
    visible, _ = load_visible_retrieval_hits(hits, mail_mode=mail_mode,
                                          max_concept_chars=char_limit, max_chunk_chars=char_limit,
                                          hydrate_all_chunks=True)
    if len(visible) != len(hits):
        _changed()
    payloads = {_key(hit.payload): hit.payload for hit in visible}
    for key, ref in refs.items():
        payload = payloads[key]
        if (payload.get('_canonical_verified') is not True
                or payload.get('source_id') != ref.get('source_id')
                or payload.get('chunk_index') != ref.get('chunk_index')):
            _changed()

    def excerpt(ref):
        payload = payloads[_key(ref)]
        start = ref['start']
        text = payload.get('content', '')[start:start + ref['length']]
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
        block['source_path'] = payloads.get(_key(primary), {}).get('source_path')
        if block.get('_mail_evidence'):
            block['mail_fragment'] = {'chunk_index': block['_mail_evidence']['chunk_index'],
                                      'content': excerpt(block['_mail_evidence'])}
        result.append(block)
    return result
