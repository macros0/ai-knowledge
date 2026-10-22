"""Request-local scenario execution and shared chat admission boundaries."""
from dataclasses import dataclass
from app.api.errors import ApiError
from app.models.answer_scenarios import ScenarioExecution
from app.services.chat_prompt_builder import build_scenario_prompts
from app.services.rate_limiter import RateLimitExceeded,get_rate_limiter
from app.services.authorship_evidence import is_authorship_query


def check_chat_rate(user,settings):
    try:
        get_rate_limiter().check_action(user.user_id,"chat",max_requests=settings.chat_rate_limit_per_minute)
    except RateLimitExceeded as exc:
        raise ApiError(429,"rate_limited","Chat rate limit exceeded",headers={"Retry-After":str(max(1,int(exc.retry_after)))}) from exc


@dataclass
class ChatRunContext:
    scenario: object
    scenario_prompts: object
    persist_history: bool = True

    def execution(self,result,model_id=None):
        status=("no_sources" if not result.sources else
                "assessment_rejected" if result.source_assessment and result.source_assessment.decision=="reject" else
                "protected_authorship" if is_authorship_query(result.query) else
                "applied" if any(source.in_model_context for source in result.sources) else "accepted")
        return ScenarioExecution(status=status,model_id=model_id,prompt_fingerprint=self.scenario_prompts.fingerprint)


def preview_answer(body,user):
    # Import at call time: the API passes its existing evidence pipeline to this
    # boundary; no separate implementation of retrieval or generation is kept.
    from app.api import chat as api
    from app.models.schemas import ChatRequest
    from app.services.answer_scenarios import require_available
    require_available(user)
    settings=api.get_settings()
    req=ChatRequest(**body.request.model_dump(),response_mode="full")
    run=ChatRunContext(body.draft,build_scenario_prompts(req.query,req.locale,settings),persist_history=False)
    check_chat_rate(user,settings)
    slots=api._admit_chat(settings.chat_max_inflight)
    try:
        result=api._answer(req,user,settings,run=run)
        result.preview=True
        result.response_mode="scenario"
        model_id=getattr(api._get_llm(),"model",None) if any(s.in_model_context for s in result.sources) else None
        result.scenario_execution=run.execution(result,model_id)
        return result
    finally:
        slots.release()
