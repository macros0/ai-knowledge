"""Frozen evaluation cases and explicit relevance judgments."""
import json
from pathlib import Path


def load_cases(path: Path) -> list[dict]:
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    cases = value.get('cases') if isinstance(value, dict) else value
    if not isinstance(cases, list) or not cases:
        raise ValueError('cases must be a nonempty list')
    required = {'id', 'family_id', 'split', 'group', 'query', 'request', 'judgments',
                'mandatory_sources', 'required_facts', 'forbidden_claims', 'answerable'}
    seen, families, documents = set(), {}, {}
    for case in cases:
        if not isinstance(case, dict) or not required <= case.keys():
            raise ValueError('missing case fields')
        if not isinstance(case['query'], str) or not case['query'].strip():
            raise ValueError('empty query')
        if case['id'] in seen or case['split'] not in {'development', 'holdout'}:
            raise ValueError('duplicate id or invalid split')
        seen.add(case['id'])
        family = case['family_id']
        if families.setdefault(family, case['split']) != case['split']:
            raise ValueError('family crosses splits')
        if not isinstance(case['request'], dict) or not isinstance(case['judgments'], dict):
            raise ValueError('invalid request or judgments')
        for doc_id in case['request'].get('search_doc_ids') or []:
            if documents.setdefault(doc_id, case['split']) != case['split']:
                raise ValueError('document crosses splits')
        if type(case['answerable']) is not bool:
            raise ValueError('answerable must be boolean')
        if any(type(v) is not int or v not in (0, 1, 2) for v in case['judgments'].values()):
            raise ValueError('grades must be 0/1/2')
        for field in ('mandatory_sources', 'required_facts', 'forbidden_claims'):
            if not isinstance(case[field], list):
                raise ValueError('invalid annotations')
    return cases


def validate_judgments(case: dict, keys: list[str]) -> None:
    if set(keys) - case['judgments'].keys():
        raise ValueError('unjudged candidate pool')
    if not set(case['mandatory_sources']) <= case['judgments'].keys():
        raise ValueError('unjudged mandatory sources')
    if any(case['judgments'][k] == 0 for k in case['mandatory_sources']):
        raise ValueError('mandatory source marked irrelevant')
    if not case['answerable'] and any(case['judgments'].values()):
        raise ValueError('no-answer case has relevant sources')
