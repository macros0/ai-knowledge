# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

from functools import lru_cache
import json
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

    # --- Реляционная БД (метаданные: документы, теги, OKF-концепты) ---
    # Прод: PostgreSQL (синхронный драйвер psycopg3). Пример строки:
    #   postgresql+psycopg://postgres:password@127.0.0.1:5432/okf_knowledge
    # Локально (dev) при пустом database_url — SQLite data/app.db (zero-config).
    # Подробности миграции: MIGRATION_PLAN.md.
    database_url: str | None = None
    database_url_dev: str | None = None

    # --- Авторизация ---
    # auth_provider — какой провайдер аутентификации активен:
    #   disabled        — всё открыто (локальная разработка/тесты, по умолчанию)
    #   simulation      — демо-пользователи из AUTH_SIM_USERS
    #   keycloak_oidc   — Keycloak/OIDC через authlib (требует KEYCLOAK_*)
    #   direct_ldap     — прямая интеграция с AD/LDAP (заглушка)
    #   custom_client   — собственная система авторизации клиента (заглушка)
    auth_provider: str = "disabled"
    # Окружение развёртывания: development | production. В production включается
    # fail-fast проверка auth-конфигурации (см. validate_auth_provider):
    # слабый/дефолтный APP_SECRET_KEY или AUTH_SESSION_HTTPS_ONLY=false → ValueError
    # на старте, а не молчаливый запуск в незащищённом режиме.
    environment: str = "development"
    app_secret_key: str = "dev-secret-change-me"
    # Время жизни signed-cookie сессии (секунды).
    auth_session_ttl_seconds: int = 28800
    # Кука только по HTTPS — включать в проде, ложь локально (http://localhost).
    auth_session_https_only: bool = False
    # CORS allow-list (список origin). Пусто по умолчанию: браузер не обращается
    # к бэкенду напрямую — Next.js rewrites проксируют /api серверно (same-origin),
    # поэтому cross-origin доступ бэкенду не нужен и `*` здесь был бы чистой дырой.
    cors_allowed_origins: list[str] = Field(default_factory=list)
    # Маппинг групп → роли (4 роли Этапа 1 роадмапа). Роль считается по первому
    # совпадению с приоритетом security > admin > editor > viewer (authorizer.py).
    # В проде переопределяется под корпоративные группы IDB (JSON-объект группа→роль).
    auth_role_groups: dict[str, str] = Field(default_factory=lambda: {
        "KB_Viewer": "viewer",
        "KB_Editor": "editor",
        "KB_Admin": "admin",
        "KB_Security": "security",
    })
    # Роль по умолчанию, если ни одна группа не совпала с маппингом.
    # None (пусто) = fail-closed: пользователь без опознанной роли получает 403.
    auth_default_role: str | None = None
    # Демо-пользователи для режима simulation: [{user_id, username, email, groups}]
    auth_sim_users: list[dict[str, Any]] = Field(default_factory=lambda: [
        {
            "user_id": "sim-user",
            "username": "demo.user",
            "email": "user@demo.local",
            "groups": ["KB_Viewer"],
        },
        {
            "user_id": "sim-editor",
            "username": "demo.editor",
            "email": "editor@demo.local",
            "groups": ["KB_Editor"],
        },
        {
            "user_id": "sim-admin",
            "username": "demo.admin",
            "email": "admin@demo.local",
            "groups": ["KB_Admin"],
        },
        {
            "user_id": "sim-security",
            "username": "demo.security",
            "email": "security@demo.local",
            "groups": ["KB_Security"],
        },
        {
            "user_id": "sim-guest",
            "username": "demo.guest",
            "email": "guest@demo.local",
            "groups": [],
        },
    ])

    # --- SSO (Keycloak) — активен только при AUTH_PROVIDER=keycloak_oidc ---
    keycloak_url: str | None = None
    keycloak_realm: str | None = None
    keycloak_client_id: str | None = None
    keycloak_client_secret: str | None = None
    # Внешний URL callback. На локальном стенде фронтенд ходит на бэкенд через
    # Next.js-прокси, поэтому Keycloak должен вернуть браузер на порт фронта
    # (http://localhost:3000/api/auth/callback), а бэкенд видит :8000.
    sso_redirect_uri: str | None = None
    # Куда Keycloak вернёт браузер после RP-Initiated Logout (end_session_endpoint).
    # Корень фронтенда (публичная точка входа с login-gate), чтобы не попасть на
    # защищённую страницу. Должен быть зарегистрирован в "Valid post logout
    # redirect URIs" клиента в Keycloak.
    sso_post_logout_redirect_uri: str = "http://localhost:3000/"
    # Частичное переопределение claim-имён из userinfo → поля identity.
    # JSON-объект: {"groups": "roles", "username": "name", "external_id": "sub"}.
    # Незаданные поля остаются стандартными OIDC-именами (sub/preferred_username/
    # email/group). Полезно, когда IDB (broker) отдаёт группы не в claim "group".
    keycloak_field_mapping: dict[str, str] | None = None
    # Форма имён групп в claim: "leaf" — берём текст после последнего "/"
    # (KB_Viewer), "full_path" — оставляем как есть (/IDB/KB_Viewer).
    keycloak_group_path_mode: str = "leaf"
    # Разделитель, если group-claim пришёл строкой, а не списком.
    keycloak_group_separator: str = ","

    qdrant_url: str = "http://localhost:6333"
    # Опциональный API-ключ Qdrant. Локальный compose (профиль local-qdrant) и
    # локальный бинарь без авторизации — не нужен (None). Корпоративный Qdrant,
    # требующий авторизации по инфраструктурной политике, — задать здесь.
    qdrant_api_key: str | None = None
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
    # Модель интерактивного RAG-чата. Пусто → llm_model (единая модель, не
    # ломает существующие установки). Чат отвечает пользователю напрямую —
    # здесь важнее следование инструкциям промпта и синтез нескольких
    # фрагментов, чем себестоимость; OKF-генерация и пакетные задачи остаются
    # на дешёвой llm_model.
    llm_chat_model: str = ""
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
    # Максимальный размер загружаемого файла (МБ). Проверяется и по объявленному
    # content-length, и по факту дочитывания — защита от блокировки event loop
    # гигантской загрузкой и от переполнения диска.
    max_upload_mb: int = 100
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

    chat_top_k_min: int = Field(default=1, ge=1)
    chat_top_k_max: int = Field(default=30, ge=1)
    chat_top_k_default: int = Field(default=10, ge=1)
    chat_top_k_presets: list[int] = Field(default=[5, 10, 20])

    # --- Защита от массовых операций (Этап 2а roadmap) ---
    # Журнал ИБ: срок хранения записей audit_log (дни). Очистка — отдельной
    # задачей; здесь фиксируется только срок для документации/будущей очистки.
    audit_retention_days: int = 365
    # Soft-лимит числа документов на одну массовую операцию.
    bulk_delete_max_docs: int = 50
    bulk_regenerate_max_docs: int = 20
    # Лимит массового редактирования тегов (Этап 4a). Синхронная, недеструктивная
    # операция — лимит НЕ ниже порога более опасной перегенерации
    # (bulk_regenerate_max_docs=20 / approval_threshold_docs_regenerate=15).
    bulk_tags_max_docs: int = 50
    # Per-user лимиты массовой перегенерации (независимо от системного лимита).
    bulk_regenerate_max_ops_per_hour: int = 3
    bulk_regenerate_max_docs_per_hour: int = 50
    # Грубая оценка времени перегенерации одного документа (мин) для предпросмотра
    # масштаба массовой операции в UI (Этап 2а).
    bulk_regenerate_est_minutes_per_doc: float = 5.0
    # Порог four-eyes (второй администратор) — по типу операции, чтобы порог
    # был достижим в пределах soft-лимита соответствующей операции.
    approval_threshold_docs_delete: int = 50
    approval_threshold_docs_regenerate: int = 15
    # Circuit breaker: максимум ожидающих массовых задач в очереди. При
    # превышении новые массовые операции отклоняются (503 «очередь перегружена»).
    job_queue_max_pending: int = 5
    # Максимальное время ожидания завершения обработки одного документа внутри
    # массовой задачи (сек). По истечении документ помечается ошибкой, job идёт дальше.
    job_doc_timeout_seconds: float = 3600.0

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
    # bm25 выше dense: по коротким/аббревиатурным запросам («ЛК», «ЭЛН», коды
    # полей) dense-ветка даёт плоский шум (все скоры в узкой полосе), а bm25
    # разделяет точно; перевес не даёт шуму вытеснять лексические попадания.
    # Полностью query-time: реиндекс не требуется, откат — env SEARCH_RRF_*_WEIGHT.
    search_rrf_dense_weight: float = 1.0
    search_rrf_bm25_weight: float = 1.5
    search_rrf_graph_expansion_weight: float = 0.5

    # --- Контекст LLM (форматирование после merge/collapse) ---
    # Жёсткий лимит на суммарный объём контекста, передаваемого в LLM.
    chat_max_context_chars: int = 32000
    # Обрезка отдельного блока в контексте (концепт vs чанк).
    chat_concept_max_chars: int = 4000
    chat_chunk_max_chars: int = 6000

    # --- Этап 4: справочник разработок + автоопределение ---
    # Автоопределение номера разработки (regex по имени файла + LLM с титула).
    dev_detection_enabled: bool = True
    dev_llm_title_page_enabled: bool = True
    # Сколько первых символов markdown считать «титульным листом» (Block не несёт
    # page-границ — берём голову документа как приближение титула).
    dev_title_page_chars: int = 3000
    # Порог fuzzy-совпадения названия разработки (difflib ratio 0..1).
    dev_fuzzy_name_threshold: float = 0.85
    # Regex для извлечения номера разработки из имени файла (одна группа захвата).
    # lookaround не даёт захватить хвост более длинной цифровой последовательности
    # (например, дату 20240115), а "_" после номера (12010_СЭДО) не является
    # word-boundary — поэтому \b здесь не годится.
    dev_filename_pattern: str = r"(?<!\d)(\d{4,6})(?!\d)"

    # --- Этап 4.2: дедупликация при загрузке ---
    dedup_enabled: bool = True
    dedup_minhash_k: int = 128
    # Две banding-схемы над одной подписью (k = bands * rows):
    #   strict: b=8,  r=16 → Jaccard ~0.88 (почти идентичные);
    #   loose:  b=16, r=8  → Jaccard ~0.71 (ревизии/похожие).
    dedup_strict_bands: int = 8
    dedup_strict_rows: int = 16
    dedup_loose_bands: int = 16
    dedup_loose_rows: int = 8
    # n-грамма (по словам) для shingling MinHash.
    dedup_shingle_n: int = 5
    dedup_jaccard_strict_threshold: float = 0.88
    dedup_jaccard_loose_threshold: float = 0.71

    # --- Корзина / soft delete (Этап 4a.2 roadmap) ---
    # Единое окно хранения в корзине (дней). По истечении документ физически
    # удаляется фоновой задачей (Delete Points в Qdrant + DELETE из БД + файлы).
    trash_retention_days: int = 14
    # Автозапуск фоновой очистки корзины (демон-поток при старте сервера).
    trash_purge_enabled: bool = True
    # Интервал прогона фоновой очистки (секунды).
    trash_purge_interval_seconds: float = 3600.0

    # --- История чата (Этап 6) ---
    # Окно хранения soft-deleted тредов (дней). Активная история хранится
    # бессрочно; после ручного удаления сессии она физически удаляется фоновой
    # задачей по истечении окна (аналог корзины документов, 4a.2).
    chat_history_retention_days: int = 90
    # Автозапуск фоновой очистки удалённых тредов (демон-поток при старте).
    chat_history_purge_enabled: bool = True
    # Интервал прогона фоновой очистки (секунды).
    chat_history_purge_interval_seconds: float = 3600.0

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

    @field_validator("auth_role_groups", mode="before")
    @classmethod
    def _parse_role_groups(cls, v: object) -> object:
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("auth_sim_users", mode="before")
    @classmethod
    def _parse_sim_users(cls, v: object) -> object:
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("keycloak_field_mapping", mode="before")
    @classmethod
    def _parse_field_mapping(cls, v: object) -> object:
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("keycloak_group_path_mode")
    @classmethod
    def _validate_group_path_mode(cls, v: str) -> str:
        if v not in {"leaf", "full_path"}:
            raise ValueError(
                f"keycloak_group_path_mode must be 'leaf' or 'full_path', got '{v}'"
            )
        return v

    @field_validator("environment")
    @classmethod
    def _validate_environment(cls, v: str) -> str:
        if v not in {"development", "production"}:
            raise ValueError(
                f"environment must be 'development' or 'production', got '{v}'"
            )
        return v

    @model_validator(mode="after")
    def validate_auth_provider(self) -> "Settings":
        valid = {"disabled", "simulation", "keycloak_oidc", "direct_ldap", "custom_client"}
        if self.auth_provider not in valid:
            raise ValueError(
                f"auth_provider must be one of {sorted(valid)}, got '{self.auth_provider}'"
            )
        if self.auth_provider == "keycloak_oidc":
            missing = [
                name
                for name, value in (
                    ("KEYCLOAK_URL", self.keycloak_url),
                    ("KEYCLOAK_REALM", self.keycloak_realm),
                    ("KEYCLOAK_CLIENT_ID", self.keycloak_client_id),
                    ("KEYCLOAK_CLIENT_SECRET", self.keycloak_client_secret),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    f"auth_provider='keycloak_oidc' requires {', '.join(missing)} "
                    "to be set (Keycloak подключение из .env)"
                )
        if self.auth_provider == "simulation" and not self.auth_sim_users:
            raise ValueError(
                "auth_provider='simulation' requires at least one user in AUTH_SIM_USERS"
            )
        if self.environment == "production":
            # Fail-closed: в production авторизация не может быть отключена или
            # заменена на демо-провайдер. `disabled` открывает всё без логина,
            # `simulation` выдаёт демо-админа через /auth/simulate без внешней
            # проверки — с точки зрения угрозы это эквивалентно disabled. Один
            # забытый/скопированный из dev .env с AUTH_PROVIDER=disabled|simulation
            # не должен тихо открывать корпоративные документы.
            if self.auth_provider in {"disabled", "simulation"}:
                raise ValueError(
                    f"auth_provider='{self.auth_provider}' недопустим для production: "
                    "требуется внешняя аутентификация (keycloak_oidc). "
                    "disabled/simulation — только для локальной разработки."
                )
            if (
                not self.app_secret_key
                or self.app_secret_key == "dev-secret-change-me"
                or len(self.app_secret_key) < 32
            ):
                raise ValueError(
                    "APP_SECRET_KEY небезопасен для production: задайте случайный "
                    "секрет длиной >= 32 символов "
                    "(например: python -c \"import secrets; print(secrets.token_urlsafe(32))\")"
                )
            if not self.auth_session_https_only:
                raise ValueError(
                    "AUTH_SESSION_HTTPS_ONLY=false недопустимо для production: "
                    "сессионная cookie должна передаваться только по HTTPS"
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
    def db_url(self) -> str:
        """Эффективный URL БД: database_url → database_url_dev → SQLite в data_dir."""
        if self.database_url:
            return self.database_url
        if self.database_url_dev:
            return self.database_url_dev
        return f"sqlite:///{(self.data_dir / 'app.db').as_posix()}"

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
