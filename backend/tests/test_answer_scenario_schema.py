from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.models.schemas import ChatRequest
from app.models.answer_scenarios import ScenarioCreate, ScenarioUpdate


def test_scenario_request_requires_id():
    with pytest.raises(ValidationError):
        ChatRequest(query="q", response_mode="scenario")
    assert ChatRequest(query="q", response_mode="scenario", scenario_id=uuid4()).scenario_id


def test_non_scenario_rejects_id():
    with pytest.raises(ValidationError):
        ChatRequest(query="q", response_mode="full", scenario_id=uuid4())


def test_selected_scenario_requires_session():
    selection={"attempt_id":str(uuid4()), "indexes":[1]}
    with pytest.raises(ValidationError):
        ChatRequest(query="q", response_mode="scenario", scenario_id=uuid4(), source_selection=selection)
    assert ChatRequest(query="q", response_mode="scenario", scenario_id=uuid4(), session_id=str(uuid4()), source_selection=selection)


def test_scenario_limits():
    draft=ScenarioCreate(name=" x ", instructions="  line\n  two {locale}  ")
    assert draft.visibility == "private" and draft.name == "x"
    assert draft.instructions == "line\n  two {locale}"
    assert ScenarioCreate(name="x"*120, description="a"*1000, instructions="b"*12000)
    for values in [dict(name="x"*121),dict(description="x"*1001),dict(instructions="x"*12001),dict(instructions=" "),dict(owner_id="other")]:
        with pytest.raises(ValidationError):
            ScenarioCreate(**({"name":"x","instructions":"y"}|values))
    with pytest.raises(ValidationError):
        ScenarioUpdate(name="x",instructions="y",expected_version=0)
