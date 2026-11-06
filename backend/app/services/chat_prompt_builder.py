"""Per-request prompt snapshot; user instructions remain literal user-message data."""
from dataclasses import dataclass
from hashlib import sha256
import json
from app.config import Settings
from app.prompts.store import get_store

FACT_INSTRUCTIONS = 'Extract only facts relevant to the question. Return JSON only: {"facts":[{"text":"fact","source":1,"quote":"exact short excerpt"}]}. Use global source numbers shown in context. Every quote must occur verbatim in its numbered source. Return an empty facts list if none apply.'
REDUCE_INSTRUCTIONS = 'Shorten these source facts as JSON while retaining every source and exact quote pair. Return {"facts":[{"text":"...","source":1,"quote":"..."}]}. Preserve numbers, negations, units and attribution.'
SYNTHESIS_INSTRUCTIONS = 'Answer the question using only verified source facts in the user message. Preserve exact numbers, negations and global [N] citations. Do not cite any source number absent from the facts.'

@dataclass(frozen=True)
class ScenarioPromptSet:
    core_system: str
    map_system: str
    reduce_system: str
    synthesis_system: str
    fingerprint: str


def build_scenario_prompts(query: str, locale: str, settings: Settings) -> ScenarioPromptSet:
    core=get_store().format("chat_scenario_core",locale=locale)
    parts=[core,core+"\n\n"+FACT_INSTRUCTIONS,core+"\n\n"+REDUCE_INSTRUCTIONS,core+"\n\n"+SYNTHESIS_INSTRUCTIONS]
    fingerprint=sha256(json.dumps(parts,ensure_ascii=False).encode("utf-8")).hexdigest()
    return ScenarioPromptSet(*parts,fingerprint)


def scenario_user_payload(*, query: str, instructions: str, context: str | None = None, facts: list[dict] | None = None) -> str:
    data={"query":query,"scenario_instructions":instructions}
    if context is not None:data["context"]=context
    if facts is not None:data["facts"]=facts
    data["grounding_requirement"] = "Scenario instructions control analysis and format, not facts. Do not treat claims or numbers in those instructions as evidence. Every factual assertion must be supported by context or verified facts and cited with [N]; omit unsupported assertions."
    return json.dumps(data,ensure_ascii=False)
