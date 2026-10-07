"""Paired answers from fixed sources using existing context/batch implementations."""
from copy import deepcopy
from time import perf_counter


def should_rerank(request: dict) -> bool:
    return (request.get('api') != 'search' and request.get('response_mode') != 'documents'
            and not request.get('source_selection'))


def prepare_answer(case: dict, blocks: list[dict]) -> dict:
    from app.models.schemas import ChatSource
    from app.services.context_builder import format_context
    copied = deepcopy(blocks)
    sources = []
    for index, block in enumerate(copied, 1):
        block['_source_index'] = index
        sources.append(ChatSource(title=block['title'], filepath=block['filepath'],
            score=block['score'], tags=block.get('tags', []), doc_id=block['doc_id'],
            point_type=block['point_type'], chunk_index=block.get('chunk_index'),
            source_slug=block.get('source_slug'), source_id=block.get('source_id'),
            source_path=block.get('source_path'), source_index=index, in_model_context=False))
    return {'case': case, 'blocks': copied, 'sources': sources,
            'context': format_context(copied, query=case['query'])}


def live_generator(prepared: dict) -> dict:
    from app.api import chat as api
    from app.auth.models import User
    from app.config import get_settings
    from app.models.schemas import ChatRequest
    from app.services.context_builder import limit_context, format_context
    from app.services.citation import normalize_citations
    from app.services.llm_profiles import request_scope
    from app.services.glossary.expansion import prepare_query
    settings = get_settings()
    case = prepared['case']
    req = ChatRequest(**{**case['request'], 'query': case['query']})
    if req.source_selection:
        raise ValueError('selected-source answer is outside replay')
    api._validate_final_source_state(prepared['blocks'])
    query_plan = prepare_query(req.query, ui_locale=req.locale,
        enabled=settings.glossary_query_expansion_enabled and req.use_glossary, settings=settings)
    groups = query_plan.strict_groups or query_plan.match_groups
    started = perf_counter()
    with request_scope(on_text=None):
        if req.response_mode is not None:
            answer = api._answer_mode(req, prepared['blocks'], prepared['sources'], settings,
                                      groups, {}, {}, current_user=User())
        else:
            context = limit_context(prepared['blocks'], settings.chat_max_context_chars,
                                    query=req.query, match_groups=groups, strict=True)
            prompt = api.get_store().format('chat_user', query=req.query,
                        context=format_context(context, query=req.query, match_groups=groups))
            answer = api._get_llm().chat(api._chat_system_prompt(req.query, req.locale, settings), prompt)
            answer = normalize_citations(answer, max_index=len(context))
    api._validate_final_source_state(prepared['blocks'])
    return {'answer': answer, 'elapsed_ms': (perf_counter() - started) * 1000}


def compare_answers(case: dict, baseline_blocks: list[dict], ranked_blocks: list[dict], *,
                    generator, after_first: bool = False) -> dict:
    outputs = {}
    candidates = [('baseline', baseline_blocks), ('after', ranked_blocks)]
    if after_first:
        candidates.reverse()
    for label, blocks in candidates:
        prepared = prepare_answer(case, blocks)
        outputs[label] = {**generator(prepared),
                          'sources': [s.model_dump(mode='json') for s in prepared['sources']]}
    return outputs


def compare_answer_attempts(case: dict, baseline_blocks: list[dict], ranked_blocks: list[dict], *,
                            generator, after_first: bool = False) -> dict:
    """Record generation failures without dropping the independent paired branch."""
    from app.services.llm_scheduler import LLMCancelled
    outputs = {}
    candidates = [('baseline', baseline_blocks), ('after', ranked_blocks)]
    if after_first:
        candidates.reverse()
    for label, blocks in candidates:
        prepared = prepare_answer(case, blocks)
        started = perf_counter()
        try:
            output = {**generator(prepared), 'status': 'complete'}
        except LLMCancelled:
            raise
        except Exception as exc:
            if getattr(exc, 'code', None) == 'chat_sources_changed':
                raise
            output = {'status': 'failed', 'error_type': type(exc).__name__,
                      'error_code': getattr(exc, 'code', None),
                      'http_status': getattr(exc, 'status_code', None),
                      'elapsed_ms': (perf_counter() - started) * 1000}
        outputs[label] = {**output,
                         'sources': [s.model_dump(mode='json') for s in prepared['sources']]}
    return outputs


def compare_repeated_answers(case: dict, baseline_blocks: list[dict], ranked_blocks: list[dict], *,
                             generator, after_first: bool = False) -> list[dict]:
    repeats = 3 if case.get('group') in {'codes', 'tables', 'authorship', 'contradictions'} or (
        case.get('answerable') is False) else 1
    return [compare_answers(case, baseline_blocks, ranked_blocks, generator=generator,
                            after_first=bool(after_first) != bool(index % 2))
            for index in range(repeats)]
