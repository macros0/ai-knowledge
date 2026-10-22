"""Access-controlled scenario catalog. No request-time global instruction cache."""
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4
from sqlalchemy import select, update, delete, or_, func
from app.api.errors import ApiError
from app import error_codes as codes
from app.auth.models import User
from app.config import get_settings
from app.db.models import AnswerScenario, AnswerScenarioVersion
from app.db.session import session_scope
from app.models.answer_scenarios import ScenarioCreate, ScenarioUpdate, ScenarioOut, ScenarioSummary, ScenarioListOut, ScenarioSnapshot
from app.services import audit


def scenarios_available(user: User) -> bool:
    settings=get_settings()
    return settings.chat_scenarios_enabled and settings.auth_provider != "disabled" and getattr(user,"user_id","anonymous") != "anonymous"


def require_available(user):
    if not scenarios_available(user):
        raise ApiError(503,codes.SCENARIOS_UNAVAILABLE,"Answer scenarios unavailable")


def _error(code=codes.SCENARIO_NOT_FOUND,status=404):
    return ApiError(status,code,"Scenario unavailable" if status==404 else "Scenario operation rejected")


def _row(session,scenario_id,user,*,lock=False):
    statement=select(AnswerScenario,AnswerScenarioVersion).join(AnswerScenarioVersion,
        (AnswerScenarioVersion.scenario_id==AnswerScenario.id)&(AnswerScenarioVersion.version==AnswerScenario.current_version)).where(
        AnswerScenario.id==str(scenario_id),or_(AnswerScenario.owner_id==user.user_id,AnswerScenario.visibility=="public"))
    if lock:
        # Lock the head without joining a version from the old statement
        # snapshot. A waiting PostgreSQL reader must see the newly committed
        # version in a fresh statement after acquiring the head lock.
        head=session.scalar(select(AnswerScenario).where(AnswerScenario.id==str(scenario_id),
            or_(AnswerScenario.owner_id==user.user_id,AnswerScenario.visibility=="public")).with_for_update())
        if head is None:raise _error()
        version=session.get(AnswerScenarioVersion,(head.id,head.current_version))
        if version is None:raise _error(codes.SCENARIOS_UNAVAILABLE,503)
        return head,version
    row=session.execute(statement).first()
    if row is None: raise _error()
    return row


def _can_manage(head,user):
    return head.owner_id==user.user_id or (head.visibility=="public" and "admin" in user.roles)


def _out(head,version,user):
    return ScenarioOut(id=head.id,owner_id=head.owner_id,owner_username=head.owner_username,
        visibility=head.visibility,version=head.current_version,name=version.name,
        description=version.description,instructions=version.instructions,
        instructions_sha256=version.instructions_sha256,created_at=head.created_at,
        updated_at=head.updated_at,can_edit=_can_manage(head,user))


def _event(session,user,action,head,*,old_version=None,source_id=None):
    audit.record_in_session(session,action_type="answer_scenario_"+action,user_id=user.user_id,
        username=user.username,target_type="answer_scenario",target_id=head.id,
        old_value={"version":old_version} if old_version else None,
        new_value={"version":head.current_version,"public":head.visibility=="public",**({"source_id":source_id} if source_id else {})},
        meta={"source_id":source_id} if source_id else None)


def _version(head,data):
    return AnswerScenarioVersion(scenario_id=head.id,version=head.current_version,
        name=data.name,description=data.description,instructions=data.instructions,
        instructions_sha256=sha256(data.instructions.encode("utf-8")).hexdigest(),visibility=data.visibility)


def _create(session,user,data,*,source_id=None):
    head=AnswerScenario(id=str(uuid4()),owner_id=user.user_id,owner_username=user.username,
        visibility=data.visibility,current_version=1)
    session.add(head);session.flush()
    version=_version(head,data);session.add(version);session.flush()
    _event(session,user,"copy" if source_id else "create",head,source_id=source_id)
    if data.visibility=="public":_event(session,user,"publish",head)
    return _out(head,version,user)


def create_scenario(user: User,data: ScenarioCreate) -> ScenarioOut:
    require_available(user)
    with session_scope() as session: return _create(session,user,data)


def get_scenario(scenario_id: str,user: User) -> ScenarioOut:
    require_available(user)
    with session_scope() as session:return _out(*_row(session,scenario_id,user),user)


def list_scenarios(user: User,*,scope="all",q="",limit=50,offset=0) -> ScenarioListOut:
    require_available(user)
    if scope not in {"all","mine","public"} or not 1<=limit<=100 or offset<0 or len(q)>200:
        raise ApiError(422,"invalid_request","Invalid catalog parameters")
    with session_scope() as session:
        stmt=select(AnswerScenario,AnswerScenarioVersion).join(AnswerScenarioVersion,
            (AnswerScenarioVersion.scenario_id==AnswerScenario.id)&(AnswerScenarioVersion.version==AnswerScenario.current_version))
        visibility=(AnswerScenario.owner_id==user.user_id if scope=="mine" else
            AnswerScenario.visibility=="public" if scope=="public" else
            or_(AnswerScenario.owner_id==user.user_id,AnswerScenario.visibility=="public"))
        stmt=stmt.where(visibility)
        if q.strip():
            pattern="%"+q.strip().replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%"
            stmt=stmt.where(or_(AnswerScenarioVersion.name.ilike(pattern,escape="\\"),AnswerScenarioVersion.description.ilike(pattern,escape="\\")))
        total=session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows=session.execute(stmt.order_by(AnswerScenario.updated_at.desc(),AnswerScenario.id).limit(limit).offset(offset)).all()
        items=[ScenarioSummary.model_validate(_out(h,v,user).model_dump()) for h,v in rows]
        return ScenarioListOut(items=items,total=total,limit=limit,offset=offset)


def _require_manage(head,user):
    if not _can_manage(head,user):raise _error("forbidden",403)


def _expected(head,expected):
    if head.current_version!=expected:raise _error(codes.SCENARIO_VERSION_CONFLICT,409)


def update_scenario(scenario_id: str,user: User,data: ScenarioUpdate) -> ScenarioOut:
    require_available(user)
    with session_scope() as session:
        head,version=_row(session,scenario_id,user,lock=True);_require_manage(head,user);_expected(head,data.expected_version)
        if all(getattr(version,key)==getattr(data,key) for key in ("name","description","instructions","visibility")):
            return _out(head,version,user)
        old_visibility=head.visibility
        result=session.execute(update(AnswerScenario).where(AnswerScenario.id==head.id,
            AnswerScenario.current_version==data.expected_version).values(current_version=data.expected_version+1,
            visibility=data.visibility,updated_at=datetime.now(timezone.utc)).execution_options(synchronize_session=False))
        if result.rowcount!=1:raise _error(codes.SCENARIO_VERSION_CONFLICT,409)
        session.refresh(head)
        version=_version(head,data);session.add(version);session.flush()
        _event(session,user,"update",head,old_version=data.expected_version)
        if old_visibility!=data.visibility:_event(session,user,"publish" if data.visibility=="public" else "unpublish",head,old_version=data.expected_version)
        return _out(head,version,user)


def copy_scenario(scenario_id: str,user: User,expected_version: int) -> ScenarioOut:
    require_available(user)
    with session_scope() as session:
        head,version=_row(session,scenario_id,user,lock=True);_expected(head,expected_version)
        return _create(session,user,ScenarioCreate(name=version.name,description=version.description,
            instructions=version.instructions),source_id=head.id)


def delete_scenario(scenario_id: str,user: User,expected_version: int) -> None:
    require_available(user)
    with session_scope() as session:
        head,_=_row(session,scenario_id,user,lock=True);_require_manage(head,user);_expected(head,expected_version)
        _event(session,user,"delete",head)
        session.execute(delete(AnswerScenarioVersion).where(AnswerScenarioVersion.scenario_id==head.id))
        result=session.execute(delete(AnswerScenario).where(AnswerScenario.id==head.id,AnswerScenario.current_version==expected_version))
        if result.rowcount!=1:raise _error(codes.SCENARIO_VERSION_CONFLICT,409)


def resolve_scenario_snapshot(session,scenario_id: str,user: User) -> ScenarioSnapshot:
    require_available(user)
    head,version=_row(session,scenario_id,user,lock=True)
    return ScenarioSnapshot(id=head.id,version=head.current_version,name=version.name,description=version.description,
        instructions=version.instructions,instructions_sha256=version.instructions_sha256,
        owner_id=head.owner_id,owner_username=head.owner_username)
