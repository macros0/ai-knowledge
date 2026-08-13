from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "OKF Knowledge Service"
    api_prefix: str = "/api"
    data_dir: Path = Path("./data")

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "okf_knowledge_base"
    embedding_dim: int = 1024

    embedding_provider: str = "http"  # http | fake
    embedding_model: str = "bge-m3"
    embedding_base_url: str = "http://localhost:11434/v1"
    embedding_api_key: str = "ollama"

    llm_model: str = "ollama/qwen2.5:14b"
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str = "ollama"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 4096

    okf_max_chunk_chars: int = 8000
    okf_max_concept_chars: int = 4000

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def okf_dir(self) -> Path:
        return self.data_dir / "okf_bundles"

    def ensure_dirs(self) -> None:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.okf_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
