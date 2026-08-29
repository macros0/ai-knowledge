import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Пресеты режимов поиска: mode (из API/настроек) → набор включённых веток.
# mode в API трактуется как пресет; явные флаги dense/bm25 в запросе
# имеют приоритет над пресетом.
SEARCH_MODE_PRESETS: dict[str, set[str]] = {
    "dense": {"dense"},
    "bm25": {"bm25"},
    "hybrid": {"dense", "bm25"},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "OKF Knowledge Service"
    api_prefix: str = "/api"
    data_dir: Path = Path("./data")

    # Потолок размера загружаемого документа. Без него один большой файл
    # исчерпывает память процесса: раньше тело читалось в память целиком.
    upload_max_size_mb: int = Field(default=100, ge=1)

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "okf_knowledge_base"
    embedding_dimensions: int = 1024

    embedding_provider: str = "http"  # http | fake
    embedding_model: str = "ollama/bge-m3"
    embedding_api_base: str | None = "http://localhost:11434"
    embedding_api_key: str | None = None
    embedding_batch_size: int = 64
    embedding_timeout_seconds: float = 30.0
    embedding_retry_attempts: int = 1
    embedding_retry_backoff_seconds: float = 2.0

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

    # Интерактивный чат: меньше ретраев и короче idle-таймаут, чтобы не висеть
    # на «Думаю...» при недоступности/лимите провайдера LLM.
    llm_interactive_retry_attempts: int = 2
    llm_interactive_stream_idle_timeout_seconds: float = 30.0

    llm_stream_idle_timeout_seconds: float = 60.0
    llm_max_total_timeout_seconds: float = 600.0

    llm_chunk_retry_attempts: int = 3
    llm_chunk_retry_backoff_seconds: float = 30.0

    okf_max_chunk_chars: int = 8000
    okf_max_concept_chars: int = 4000
    okf_split_on_truncation: bool = True
    okf_split_max_depth: int = 2
    okf_salvage_truncated: bool = True
    # Программная экстракция таблиц полей XML-сообщений (обходит LLM для таблиц
    # со спецификацией полей: поле | тип | длина | кратность | описание).
    # Порог минимального числа строк-данных, чтобы таблицу обрабатывать
    # программно (ниже порога — оставляется LLM). 0 или отрицательное значение
    # полностью выключает программную экстракцию (таблицы обрабатывает LLM).
    okf_field_table_min_rows: int = 0
    # LLM-классификатор таблиц-перечней: для каждой markdown-таблицы (≥ порога
    # строк) LLM решает «требует ли таблица построчного анализа» (каждая строка
    # = отдельное понятие: поле, ситуация, определение, элемент справочника) и
    # указывает ключевую колонку. При ошибке/выключении — fallback на XML-
    # эвристику (okf_field_table_min_rows). Кэш на диск: data/cache/table_classify/.
    okf_table_llm_classify: bool = False
    # Собственный порог режима классификатора — намеренно отдельный от
    # okf_field_table_min_rows. Тот выключает XML-эвристику и по умолчанию
    # равен 0; если бы классификатор смотрел на него же, okf_table_llm_classify
    # был бы включаемым no-op. Здесь <= 0 выключает уже сам классификатор.
    okf_table_classify_min_rows: int = 5

    chat_top_k_min: int = Field(default=1, ge=1)
    chat_top_k_max: int = Field(default=30, ge=1)
    chat_top_k_default: int = Field(default=10, ge=1)
    chat_top_k_presets: list[int] = Field(default=[5, 10, 20])

    search_mode_default: str = "hybrid"  # dense | bm25 | hybrid

    # --- Индексация чанков (dual-index: концепты + чанки) ---
    # Чанки индексируются как отдельные точки Qdrant (point_type="chunk") с
    # полным сырым текстом, чтобы поиск находил детали, которые LLM могла
    # уронить при генерации концептов.
    search_index_chunks_enabled: bool = True
    # Сколько текста чанка сохранять в payload и векторизовать.
    okf_max_chunk_index_chars: int = 8000

    # --- Ветки поиска (query-time, не влияют на хранимые данные) ---
    # Каждую ветку можно включать/выключать независимо. Пресеты (mode в API)
    # разворачиваются в наборы этих флагов.
    search_dense_enabled: bool = True
    search_bm25_enabled: bool = True
    search_graph_expansion_enabled: bool = True

    # --- RRF (Reciprocal Rank Fusion) в Python ---
    # Каждая ветка отдаёт per_branch_top_k кандидатов, fusion сливает.
    search_per_branch_top_k: int = 30
    # k=60 — стандарт TREC. Малое k → голосование большинством; большое →
    # игнорирует ранг, учитывает только частоту появления в списках.
    search_rrf_k: int = 60
    # Веса веток: влияют на относительный вклад каждой ветки в fused score.
    # Graph expansion намеренно ниже, чтобы не вытеснять прямые
    # семантические/лексические попадания.
    search_rrf_dense_weight: float = 1.0
    search_rrf_bm25_weight: float = 1.0
    search_rrf_graph_expansion_weight: float = 0.5

    # --- Контекст LLM (форматирование после merge/collapse) ---
    # Жёсткий лимит на суммарный объём контекста, передаваемого в LLM.
    chat_max_context_chars: int = 32000
    # Обрезка отдельного блока в контексте (концепт vs чанк).
    chat_concept_max_chars: int = 4000
    chat_chunk_max_chars: int = 6000

    @field_validator("chat_top_k_presets", mode="before")
    @classmethod
    def parse_top_k_presets(cls, v: object) -> Any:
        if isinstance(v, str):
            tokens = [item.strip() for item in v.split(",") if item.strip()]
            return sorted(set(int(x) for x in tokens))
        if isinstance(v, (list, tuple, set)):
            return sorted(set(int(x) for x in v))
        return v

    @model_validator(mode="after")
    def validate_search_mode(self) -> "Settings":
        valid = SEARCH_MODE_PRESETS.keys()
        if self.search_mode_default not in valid:
            raise ValueError(
                f"search_mode_default must be one of {sorted(valid)}, got '{self.search_mode_default}'"
            )
        return self

    @model_validator(mode="after")
    def validate_top_k_bounds(self) -> "Settings":
        if self.chat_top_k_min > self.chat_top_k_max:
            raise ValueError("chat_top_k_min cannot exceed chat_top_k_max")
        if not (self.chat_top_k_min <= self.chat_top_k_default <= self.chat_top_k_max):
            raise ValueError("chat_top_k_default must be between chat_top_k_min and chat_top_k_max")
        for preset in self.chat_top_k_presets:
            if not (self.chat_top_k_min <= preset <= self.chat_top_k_max):
                raise ValueError(f"Preset {preset} is out of bounds [{self.chat_top_k_min}, {self.chat_top_k_max}]")
        return self

    @model_validator(mode="after")
    def warn_classifier_disabled_by_threshold(self) -> "Settings":
        if self.okf_table_llm_classify and self.okf_table_classify_min_rows <= 0:
            logging.getLogger(__name__).warning(
                "okf_table_llm_classify=True, но okf_table_classify_min_rows=%d (<= 0) — "
                "классификатор таблиц выключен порогом и не будет вызван ни разу",
                self.okf_table_classify_min_rows,
            )
        return self

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

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

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
