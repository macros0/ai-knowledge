"""Factory is called only after the deployment/user/empty-source guards."""

from .types import AssessmentConfig, AssessmentConnection, SourceAssessor


def get_assessor(config: AssessmentConfig, connection: AssessmentConnection) -> SourceAssessor:
    if config.backend != "llm":
        raise ValueError("Unsupported source_assessment_backend")
    from .llm import LLMSourceAssessor

    return LLMSourceAssessor(config, connection)
