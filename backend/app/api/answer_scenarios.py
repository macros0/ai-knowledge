"""Authenticated catalog; ownership remains enforced in the service."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Query,Response
from app.auth.models import User
from app.auth.service import require_user
from app.models.answer_scenarios import ScenarioCreate,ScenarioUpdate,ScenarioCopy,ScenarioOut,ScenarioListOut,ScenarioPreviewRequest
from app.services import answer_scenarios as service

router=APIRouter(prefix="/answer-scenarios",tags=["answer-scenarios"])


@router.get("",response_model=ScenarioListOut)
def list_scenarios(scope: Literal["all","mine","public"]="all",q: str=Query("",max_length=200),
                   limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0),user: User=Depends(require_user)):
    return service.list_scenarios(user,scope=scope,q=q,limit=limit,offset=offset)


@router.post("",response_model=ScenarioOut,status_code=201)
def create(data: ScenarioCreate,user: User=Depends(require_user)):
    return service.create_scenario(user,data)


@router.post("/preview/stream")
def preview(body: ScenarioPreviewRequest,user: User=Depends(require_user)):
    from fastapi.responses import StreamingResponse
    from app.services.chat_execution import preview_answer
    from app.services.chat_stream import stream_chat
    service.require_available(user)
    return StreamingResponse(stream_chat(lambda:preview_answer(body,user),independent_calls=True),media_type="application/x-ndjson",
        headers={"Cache-Control":"no-store","X-Accel-Buffering":"no"})


@router.get("/{scenario_id}",response_model=ScenarioOut)
def get(scenario_id: UUID,response: Response,user: User=Depends(require_user)):
    response.headers["Cache-Control"]="no-store"
    return service.get_scenario(str(scenario_id),user)


@router.put("/{scenario_id}",response_model=ScenarioOut)
def update(scenario_id: UUID,data: ScenarioUpdate,user: User=Depends(require_user)):
    return service.update_scenario(str(scenario_id),user,data)


@router.delete("/{scenario_id}",status_code=204)
def delete(scenario_id: UUID,expected_version: int=Query(ge=1),user: User=Depends(require_user)):
    service.delete_scenario(str(scenario_id),user,expected_version)
    return Response(status_code=204)


@router.post("/{scenario_id}/copy",response_model=ScenarioOut,status_code=201)
def copy(scenario_id: UUID,data: ScenarioCopy,user: User=Depends(require_user)):
    return service.copy_scenario(str(scenario_id),user,data.expected_version)
