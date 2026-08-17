from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "OKF Knowledge Service"
    api_prefix: str = "/api"
    data_dir: Path = Path("./data")

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "okf_knowledge_base"
    embedding_dimensions: int = 1024

    embedding_provider: str = "http"  # http | fake
    embedding_model: str = "ollama/bge-m3"
    embedding_api_base: str | None = "http://localhost:11434"
    embedding_api_key: str | None = None
    embedding_batch_size: int = 64

    llm_model: str = "ollama/qwen2.5:14b"
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str = "ollama"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 4096
    llm_max_tokens_cap: int = 16384
    llm_truncation_retry_attempts: int = 2
    llm_truncation_max_tokens_multiplier: float = 1.5

    llm_max_concurrency: int = 1
    llm_interactive_concurrency: int = 2
    llm_retry_attempts: int = 5
    llm_retry_backoff_seconds: float = 2.0
    llm_timeout_seconds: float = 120.0

    llm_stream_idle_timeout_seconds: float = 60.0
    llm_max_total_timeout_seconds: float = 600.0

    llm_chunk_retry_attempts: int = 3
    llm_chunk_retry_backoff_seconds: float = 30.0

    okf_max_chunk_chars: int = 8000
    okf_max_concept_chars: int = 4000
    okf_split_on_truncation: bool = True
    okf_split_max_depth: int = 2
    okf_salvage_truncated: bool = True

    @field_validator("data_dir", mode="before")
    @classmethod
    def _resolve_data_dir(cls, value: object) -> Path:
        """Относительный data_dir резолвится от корня проекта (не от CWD).

        Локально (backend/app/config.py) parents[2] — корень репозитория,
        в docker (/app/app/config.py) — '/', т.е. /data = смонтированный том.
        """
        path = Path(str(value)) if value is not None and str(value) else Path("./data")
        if path.is_absolute():
            return path
        return Path(__file__).resolve().parents[2] / path

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def okf_dir(self) -> Path:
        return self.data_dir / "okf_bundles"

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "staging"

    @property
    def prompts_override_dir(self) -> Path:
        return self.data_dir / "prompts"

    def ensure_dirs(self) -> None:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.okf_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.prompts_override_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
