"""Resolve isolated server snapshots, without mutating the application's Settings."""

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from app.config import Settings
from .types import AssessmentConfig, AssessmentConnection


def resolve_assessment_config(settings: Settings) -> AssessmentConfig:
    model = settings.source_assessment_llm_model or settings.llm_chat_model or settings.llm_model
    model_id = (
        model
        if re.fullmatch(r"[\w./:+-]{1,200}", model) and "://" not in model
        else "configured-model"
    )
    fields = {
        name: getattr(settings, "source_assessment_" + name)
        for name in (
            "backend",
            "sample_size",
            "timeout_seconds",
            "max_chars_per_fragment",
            "max_total_chars",
            "max_input_tokens",
            "max_output_tokens",
        )
    }
    config = AssessmentConfig(model_id=model_id, **fields)
    prompt = Path(__file__).resolve().parents[3] / "prompts/source_assessment.md"
    prompt_hash = hashlib.sha256(prompt.read_bytes()).hexdigest() if prompt.exists() else "pending"
    fingerprint = hashlib.sha256(
        json.dumps({**asdict(config), "prompt": prompt_hash}, sort_keys=True).encode()
    ).hexdigest()[:24]
    return AssessmentConfig(**fields, model_id=model_id, config_fingerprint=fingerprint)


def resolve_assessment_connection(settings: Settings) -> AssessmentConnection:
    separate = bool(settings.source_assessment_llm_base_url)
    profile = settings.source_assessment_llm_profile or (
        "standard" if separate else settings.llm_profile
    )
    snapshot = settings.model_copy(
        deep=True,
        update={
            "llm_model": settings.source_assessment_llm_model
            or settings.llm_chat_model
            or settings.llm_model,
            "llm_chat_model": "",
            "llm_base_url": settings.source_assessment_llm_base_url or settings.llm_base_url,
            "llm_api_key": settings.source_assessment_llm_api_key
            if separate
            else settings.llm_api_key,
            "llm_profile": profile,
            "llm_temperature": 0,
            "llm_local_enable_thinking": False,
        },
    )
    return AssessmentConnection(snapshot)
