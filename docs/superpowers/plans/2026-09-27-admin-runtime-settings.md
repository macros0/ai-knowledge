# Настройки сервиса в меню администратора — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Subagent-driven-development — только при отдельно выбранном пользователем способе выполнения. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать администратору управлять поведением поиска, чата и обработки документов через интерфейс с проверкой значений, историей изменений и предсказуемым моментом применения.

**Architecture:** Стартовая конфигурация остаётся в `Settings` и окружении. Разрешённые переопределения хранятся в БД; отдельный сервис формирует неизменяемые снимки настроек для запросов и заданий. Запись переопределений, увеличение ревизии и аудит выполняются одной транзакцией; UI никогда не редактирует `.env`.

**Tech Stack:** FastAPI, Pydantic 2, SQLAlchemy 2, PostgreSQL/SQLite, Alembic, Next.js/React, существующая локализация, pytest, Node test runner.

**Spec:** Разделы 1–6 этого документа фиксируют предлагаемый проект решения на основе обсуждения; разделы 7–12 — реализацию и приёмку. Документ подготовлен по запросу на планирование, реализация ещё не начата. Границы полей и ограничения ниже — проектные решения, а не описание уже работающей возможности.

## Global Constraints

- Production сохраняет ограничение одной реплики и одного backend worker; распределённые очереди и Redis не добавляются.
- Работа с PostgreSQL и SQLite обязательна; миграция не зависит от доступа к реальной пользовательской БД.
- Существующие env-настройки и значения по умолчанию продолжают работать при пустом наборе переопределений.
- Секреты, подключения, параметры авторизации и пути не попадают в таблицы настроек, API, историю, экспорт или аудит изменений настроек.
- Сервер проверяет роль `admin` на чтение административной конфигурации и на изменение; скрытия кнопки недостаточно.
- Существующие auth/CSRF/CSP-механизмы сохраняются; новые роуты подключаются через защищённый router.
- Изменения не запускают перегенерацию, переиндексацию, очистку либо отмену заданий автоматически.
- Технические имена полей допускаются в раскрываемой справке администратора. Основные подписи и ошибки локализованы и объясняют действие.
- Перед реализацией frontend прочитать релевантную локальную документацию `frontend/node_modules/next/dist/docs/`, как требует `frontend/AGENTS.md`.
- Локальные команды Next выполнять через `node node_modules/next/dist/bin/next`, а не сломанные npm/npx shims.
- В общем checkout сохранять посторонние изменения; коммиты только по явному списку относящихся к задаче файлов. Push в этот план не входит.

## Review Focus

1. Два администратора меняют взаимосвязанные поля: второй получает конфликт, значения первого не теряются — задачи 2, 6, 7.
2. Настройки изменились между постановкой задачи в очередь и выполнением: задание использует сохранённый снимок; ограничения допуска и аварийные запреты проверяются отдельно — задачи 4, 9.
3. Перезапуск произошёл после commit до HTTP-ответа: БД содержит ровно одну целую ревизию, повтор не дублирует аудит — задачи 2, 6.
4. Сокращение срока корзины затрагивает старые данные: необходимы предпросмотр последствий и подтверждение; восстановленный объект не удаляется — задача 5.
5. БД недоступна или запись настроек повреждена: нет незаметного возврата к более разрешающим дефолтам, фоновые удаления пропускают цикл — задачи 2, 5, 10.

## 1. Текущее состояние и точки интеграции

Проверено по коду 27.09.2026:

| Область | Текущее состояние | Следствие для реализации |
|---|---|---|
| `backend/app/config.py` | `get_settings()` под `lru_cache`, конфигурация из окружения и `.env` | Одного сброса кэша недостаточно; не превращать базовую загрузку конфигурации в запрос к БД |
| `backend/app/api/settings.py` | GET отдаёт ограниченную проекцию для интерфейса | Сохранить совместимость; добавить отдельный admin API |
| `services/vector_store.py`, `llm_client.py`, `pipeline.py` | Объекты сохраняют `self.settings` | Передавать настройки конкретной операции явно; не менять общий объект на месте |
| `llm_client.py`, `pipeline.py` | Семафоры и executor создаются один раз | Параллельность не объявлять горячей настройкой |
| `services/audit.py` | Есть `record_in_session()` | Использовать для атомарной записи конфигурации и аудита |
| `services/export_queue.py` | Чтение настроек при submit, execute и completion | Зафиксировать параметры сборки архива и TTL при submit |
| `services/trash.py`, `chat_history.py` | Purge-циклы сохраняют часть конфигурации; срок применяется к удалённым объектам | Перечитывать политику между циклами; сохранять проверки восстановления |
| `services/rate_limiter.py` | Счётчики в памяти, лимиты передаются в методы | Менять пороги без сброса накопленных счётчиков |
| `frontend/src/context/ChatContext.js` | Настройки загружаются в состояние контекста | Добавить обновление после сохранения и возврата во вкладку |
| `frontend/src/components/AdminPanel.jsx` | Основная панель показывает задания | Новая страница `/admin/settings`, существующие задания не перемещать |
| `frontend/next.config.js` | Лимит прокси `100mb` | Размер загрузки не делать произвольно увеличиваемым |
| `AUDIT_RETENTION_DAYS` | Найдено объявление, потребитель в backend не найден | Не показывать редактируемое поле до отдельной реализации политики аудита |

Перед выполнением ещё раз проверить эти точки: план описывает текущий checkout, а не гарантирует отсутствие последующих изменений.

## 2. Состав поставок и точный список параметров

### Поставка A: поиск, лимиты, экспорт и корзина

Включает задачи 1–7 и проверки/выпуск задачи 10. Это самостоятельная полезная поставка без смены моделей и структуры обработки документов.

| Группа UI | Разрешённые ключи | Когда действуют |
|---|---|---|
| Поиск и чат | `CHAT_TOP_K_MIN`, `CHAT_TOP_K_MAX`, `CHAT_TOP_K_DEFAULT`, `CHAT_TOP_K_PRESETS`, `SEARCH_MODE_DEFAULT` | Новый запрос; пользовательский выбор сохраняется, пока допустим |
| Контекст чата | `CHAT_MAX_CONTEXT_CHARS`, `CHAT_CONCEPT_MAX_CHARS`, `CHAT_CHUNK_MAX_CHARS`, `CHAT_FOCUS_NAMED_OBJECTS` | Новый ответ; уже начатый stream не меняется |
| Качество поиска, расширенный блок | `SEARCH_DENSE_ENABLED`, `SEARCH_BM25_ENABLED`, `SEARCH_GRAPH_EXPANSION_ENABLED`, `SEARCH_PER_BRANCH_TOP_K`, `SEARCH_RRF_K`, `SEARCH_RRF_DENSE_WEIGHT`, `SEARCH_RRF_BM25_WEIGHT`, `SEARCH_RRF_GRAPH_EXPANSION_WEIGHT`, `SEARCH_REVIEW_CONCEPT_RANK_PENALTY` | Новый поиск; без переиндексации |
| Глоссарий | `GLOSSARY_QUERY_EXPANSION_ENABLED` | Новый запрос; пользователь может отключить использование в своём запросе |
| Частота запросов | `SEARCH_RATE_LIMIT_PER_MINUTE`, `CHAT_RATE_LIMIT_PER_MINUTE` | Следующая проверка допуска, без сброса счётчиков |
| Массовые операции | `BULK_DELETE_MAX_DOCS`, `BULK_REGENERATE_MAX_DOCS`, `BULK_RESUME_MAX_DOCS`, `BULK_TAGS_MAX_DOCS`, `BULK_REGENERATE_MAX_OPS_PER_HOUR`, `BULK_REGENERATE_MAX_DOCS_PER_HOUR`, `BULK_RESUME_MAX_OPS_PER_HOUR` | Новая операция; принятые партии не обрезаются |
| Доступность экспорта | `BULK_EXPORT_ENABLED`, `BULK_EXPORT_DOWNLOAD_ENABLED` | При приёме нового экспорта / нового скачивания соответственно |
| Размер экспорта | `BULK_EXPORT_MAX_DOCS`, `BULK_EXPORT_MAX_TOTAL_MB`, `BULK_EXPORT_PART_SIZE_MB`, `BULK_EXPORT_TTL_HOURS` | Снимок при принятии задания; TTL отсчитывается от завершения |
| Квоты экспорта | `BULK_EXPORT_MAX_PENDING`, `BULK_EXPORT_MAX_ACTIVE_PER_USER`, `BULK_EXPORT_MAX_OPS_PER_HOUR`, `BULK_EXPORT_MAX_RETAINED_MB`, `BULK_EXPORT_MIN_FREE_MB` | Новый допуск; свободное место дополнительно проверяется при выполнении |
| Корзина документов | `TRASH_RETENTION_DAYS`, `TRASH_PURGE_ENABLED` | Следующий цикл очистки |
| Корзина чатов | `CHAT_HISTORY_RETENTION_DAYS`, `CHAT_HISTORY_PURGE_ENABLED` | Следующий цикл очистки; только soft-deleted чаты |

Поля `KNOWLEDGE_PROFILE` и `APP_NAME` в первую поставку не включать: имя контура полезно сохранить как идентификатор развёртывания, а имя приложения затрагивает startup metadata.

### Поставка B: новые обработки документов и профили LLM

Задачи 8–9 и повтор приёмки задачи 10. Зависит от готовой поставки A; отдельный релиз.

| Группа | Разрешённые ключи |
|---|---|
| Импорт | `MAIL_IMPORT_ENABLED`, `MAX_UPLOAD_MB` |
| Генерация | `OKF_MAX_CHUNK_CHARS`, `OKF_MAX_CONCEPT_CHARS`, `OKF_TABLE_LLM_CLASSIFY`, `OKF_FIELD_TABLE_MIN_ROWS`, `OKF_COMMENT_CONCEPTS_ENABLED`, `OKF_ATTACHMENT_TAG_ENABLED`, `OKF_ATTACHMENT_TAG_THRESHOLD` |
| Обрезание результата | `OKF_SPLIT_ON_TRUNCATION`, `OKF_SPLIT_MAX_DEPTH`, `OKF_SALVAGE_TRUNCATED`, `LLM_TRUNCATION_RETRY_ATTEMPTS`, `LLM_TRUNCATION_MAX_TOKENS_MULTIPLIER` |
| Модели | `LLM_MODEL`, `LLM_CHAT_MODEL`, `TRANSLATION_MODEL`, `LLM_PROFILE`, `TRANSLATION_PROVIDER` |
| Размер ответа | `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`, `LLM_MAX_TOKENS_CAP`, `LLM_CHAT_MAX_TOKENS`, `LLM_CLASSIFICATION_MAX_TOKENS`, `LLM_TRANSLATION_MAX_TOKENS` |
| Локальный профиль | `LLM_LOCAL_ENABLE_THINKING`, `LLM_LOCAL_TOP_P`, `LLM_LOCAL_TOP_K`, `LLM_LOCAL_MIN_P`, `LLM_LOCAL_REPEAT_PENALTY`, `LLM_LOCAL_PRESENCE_PENALTY`, `LLM_LOCAL_CACHE_PROMPT` |
| Время и повторы | `LLM_TIMEOUT_SECONDS`, `LLM_FIRST_TOKEN_TIMEOUT_SECONDS`, `LLM_CHAT_TOTAL_TIMEOUT_SECONDS`, `LLM_CHUNK_BUDGET_SECONDS`, `LLM_QUEUE_TIMEOUT_SECONDS`, `LLM_MAX_TOTAL_TIMEOUT_SECONDS`, `LLM_STREAM_IDLE_TIMEOUT_SECONDS`, `LLM_INTERACTIVE_STREAM_IDLE_TIMEOUT_SECONDS`, `LLM_RETRY_ATTEMPTS`, `LLM_RETRY_BACKOFF_SECONDS`, `LLM_INTERACTIVE_RETRY_ATTEMPTS`, `LLM_CHUNK_RETRY_ATTEMPTS`, `LLM_CHUNK_RETRY_BACKOFF_SECONDS` |
| Определение разработки | `DEV_DETECTION_ENABLED`, `DEV_LLM_TITLE_PAGE_ENABLED`, `DEV_FUZZY_NAME_THRESHOLD` |

Все generation-поля действуют с новой попытки обработки. Обновление готового корпуса — отдельная явно запускаемая операция. Настройки prompt-текстов, regex определения разработки, расширенные лимиты глоссария и переводческих batch-задач в этот план не входят.

### Оставить в окружении

Подключения БД/Qdrant/LLM/эмбеддингов и их секреты; `AUTH_*`, `KEYCLOAK_*`, `SSO_*`, CORS; пути, порты, `BACKEND_URL`, `API_PREFIX`; коллекция Qdrant, модель/размерность эмбеддингов; параметры сигнатур дедупликации; `SEARCH_INDEX_CHUNKS_ENABLED`, `OKF_MAX_CHUNK_INDEX_CHARS`; `PIPELINE_MAX_*`, параллельность LLM, `CHAT_MAX_INFLIGHT`, `PARSER_*`, batch-настройки инфраструктуры; интервалы фоновой очистки и `AUDIT_RETENTION_DAYS`.

В административном UI можно показать отдельную безопасную проекцию ресурсов: параллельность обработки, лимиты памяти/времени парсера. Не предоставлять универсальный просмотр `Settings.model_dump()` даже администратору.

## 3. Контракт хранения и применения

### 3.1. Приоритеты и базовая конфигурация

Для ключей из списка: **переопределение БД → переменная окружения процесса → runtime env-файл → дефолт кода**. Для остальных ключей сохраняется нынешнее поведение.

В production используется выбранный runtime env-файл существующего deployment-механизма, не запись в корневой `.env`. Админка не читает и не редактирует произвольные файлы. Изменения env становятся базой после перезапуска backend; UI прямо это сообщает.

`get_settings()` продолжает возвращать стартовую конфигурацию без зависимости от БД. Новый `RuntimeSettingsService` читает только разрешённую проекцию базы и накладывает переопределения. Источник UI: `admin`, `environment`, `env_file`, `default`; если deployment передал env-файл как process env, источник — `environment`, без выдуманного имени файла.

### 3.2. Таблицы

- `runtime_settings_state`: единственная строка `id=1`, `revision`, `schema_version`, `overrides` JSON, `updated_at`, `updated_by`.
- `runtime_settings_revisions`: PK `revision`, `schema_version`, `overrides` JSON, `effective_values` JSON разрешённых ключей, `baseline_fingerprint`, `changed_keys`, `created_at`, `created_by`, `reason`.
- Поставка B: `generation_runtime_settings`: PK/FK `generation_id`, `revision`, `schema_version`, `effective_values` JSON, `created_at`. Отдельная таблица не требует добавлять колонки в существующие таблицы dev-БД.

Базовую строку создавать идемпотентно с `revision=0, overrides={}`. Не копировать весь `.env` в БД при первом запуске. Версионирование схемы необходимо для проверки старых снимков при обновлении кода.

Для экспорта параметры снимка хранить в существующем `Job.params.runtime_settings` вместе с ревизией и schema_version; не создавать второй универсальный движок заданий.

### 3.3. Чтение и согласованность

`capture_runtime_settings() -> RuntimeSnapshot` возвращает `revision`, `baseline_fingerprint` и типизированные неизменяемые значения. Вложенные списки тоже неизменяемы; Pydantic `frozen` с mutable-list недостаточен.

Один снимок на входящий запрос, новое задание или цикл очистки. Передавать его дальше явно: поиск → fusion → контекст → LLM. Не вызывать resolver заново между ветками одного запроса. Сетевые клиенты, соединения Qdrant и executor переиспользуются; изменение поискового веса не создаёт их заново.

Для A читать singleton-строку на границе операции; без отдельного долгоживущего кэша и без SQL-запроса на каждый чанк. Измерить накладные расходы; оптимизация кэширования отдельна и не должна нарушать гарантию «после успешного сохранения следующий запрос видит новую ревизию».

При недоступности/повреждении конфигурации новые зависимые операции получают безопасную ошибку `503 settings_unavailable`; операции с уже захваченным снимком могут завершиться. Очистка пропускает цикл. При повреждённых overrides административный GET возвращает `state_valid=false`, ревизию, безопасные field errors и baseline; неверный effective snapshot не возвращается. CLI может удалить неверные overrides, если доступна БД. При недоступной БД admin API тоже возвращает 503; её восстановление остаётся эксплуатационной операцией. Нельзя молча использовать дефолты.

### 3.4. Атомарная запись

Изменение принимает `expected_revision` и `baseline_fingerprint`. В транзакции: загрузить состояние, построить весь итоговый набор, проверить ограничения, обновить строку через compare-and-swap `WHERE revision=:expected`, записать ревизию и `audit.record_in_session()`, commit.

`rowcount=0` → `409 settings_revision_conflict`. Изменение базы после рестарта → `409 settings_baseline_changed`. Ошибка аудита → rollback всей записи. HTTP-успех возвращается только после commit. Общий singleton настроек в памяти не мутировать.

Повтор сохранения со старой ревизией не создаёт вторую запись. Если ответ потерялся, UI перечитывает состояние и показывает фактический результат. Пустой diff не увеличивает ревизию и не засоряет аудит.

Сброс ключа означает удалить override. Восстановление исторической ревизии означает применить её overrides как новую ревизию после проверки на текущей базе; это не откат секретов, env или уже удалённых данных. Preview показывает отличия от исторических effective_values.

## 4. Валидация и особые жизненные циклы

### 4.1. Проверка значений

Каталог каждого поля содержит: ключ, тип, группу, единицу, ограничения, механизм применения, влияние, i18n-ключи, право редактирования. Неизвестные поля и поля вне поставки отклоняются целиком, а не игнорируются.

Обязательные проверки:

- Строгие типы: строки вместо чисел, bool вместо int, `NaN`, бесконечность и JSON null для ненулевых полей отклоняются.
- `min ≤ default ≤ max`; каждый preset находится в диапазоне, пресеты нормализованы, список непустой.
- `SEARCH_PER_BRANCH_TOP_K ≥ CHAT_TOP_K_MAX`; хотя бы dense или BM25 включён; default search mode должен иметь работающую первичную ветку. Пользовательский режим, который стал недоступен, получает понятную ошибку с допустимыми режимами.
- Вес RRF конечный и неотрицательный; для включённых первичных веток есть хотя бы один положительный вес. Нельзя оставить только расширение графа без исходного поиска.
- Лимиты частоты положительные. Размеры партий не превышают существующие ограничения API-схем; UI использует те же пределы.
- Размер части экспорта не больше суммарного лимита; сохранены служебный запас архива и проверки фактических bytes. Квота хранения не отменяет защиту свободного места.
- Поля A без текущих верхних границ получают пределы UI/API: top_k 1–100, per_branch 1–500, RRF k 1–1000, веса 0–10, penalty 0–1000; контекст 1 000–200 000 символов, concept/chunk 100–50 000; сроки корзины 1–3650 дней. Уже существующие более строгие ограничения сохраняются.
- Для bulk-полей без верхней границы: не более 10 000 документов в партии, 1 000 операций/час, 100 000 документов/час. Для export-MB полей: 1–1 048 576 МиБ, min_free может быть 0; действующие ограничения остальных export-полей берутся из `Settings`.
- Новые ограничения должны проверяться и на effective baseline. Если текущая конфигурация вне поддержанного диапазона, до включения функции выполнить preflight и исправить явно; запрещены скрытое ограничение/clamp и самовольная смена существующего значения.
- B: `LLM_MAX_TOKENS ≤ LLM_MAX_TOKENS_CAP`; лимиты задач не превышают проверенный предел выбранной модели; `OKF_SPLIT_MAX_DEPTH` 0–4. Retry-поля проверять с учётом существующей семантики количества попыток, закрепив её тестами до введения UI.
- B: символы контекста не приравнивать к токенам. Проверка модели включает фактический токен-бюджет входа, системного prompt и ответа; при отсутствии совместимого токенизатора использовать проверенный консервативный профиль и не обещать произвольное увеличение окна.
- B: ввод timeouts от 1 до 3600 секунд, backoff от 0 до 300, число попыток в поддержанном текущим потребителем диапазоне до 10. First-token/idle не больше применимого общего бюджета; профильная семантика таймаутов проверяется по `llm_profiles.py` и тестам, а не одной общей формулой для всех режимов.

Проверки всего набора выполняются на backend. Frontend повторяет базовые проверки для удобства, но не служит единственной защитой.

### 4.2. Очереди и экспорт

- Лимиты количества/частоты проверять перед принятием новой операции. Уже принятая партия сохраняет состав.
- Параметры упаковки и TTL фиксировать при submit экспорта. Перезапуск и очередь не меняют размер частей или срок готового архива.
- `BULK_EXPORT_ENABLED=false` прекращает принятие новых заданий; принятые завершаются. `BULK_EXPORT_DOWNLOAD_ENABLED=false` запрещает новые скачивания, включая старые артефакты; открытая передача с lease завершается штатно.
- Снижение квоты ниже занятого места не удаляет файлы немедленно: запрещаются новые операции до освобождения места. TTL готовых артефактов не пересчитывается.
- Свободное место проверяется заново при запуске и записи архива. Снимок задания не даёт права игнорировать актуальную защиту диска.
- Снижение rate limit не обнуляет историю. Запросы сверх нового лимита получают штатный `429`/`Retry-After` до освобождения окна.

### 4.3. Автоочистка

Запускать по одному управляемому циклу на тип корзины независимо от значения флага; на каждом проходе получать снимок и проверять enabled. Отключённый цикл ждёт, а не исчезает до перезапуска. Интервалы остаются startup-настройками. Shutdown корректно останавливает цикл.

Уменьшение срока либо включение очистки требует preview числа и объёма уже подходящих объектов с отметкой времени. Сохранение требует отдельного подтверждения с короткоживущим токеном, связанным с пользователем, ревизией, изменениями и составом preview. TTL токена — 5 минут. Если при применении состав изменился, вернуть `409 settings_preview_stale` и новый preview; не применять молча.

Сохранение только меняет политику; не запускает purge синхронно. Следующий цикл использует новую политику. Уже захвативший старый снимок цикл может завершить работу; UI предупреждает, что выключение не возвращает удалённые данные и не прерывает уже начатое удаление.

Сохранить claim-first проверку `deleted_at IS NOT NULL` непосредственно перед удалением. Активные документы и чаты не включать в preview. Откат конфигурации не является восстановлением содержимого.

### 4.4. Модели и генерация (B)

Выбор модели только из административного каталога проверенных моделей текущего подключения. В UI нельзя задавать endpoint, API key или provider-путь, меняющий маршрут. Не считать ответ `/models` доказательством поддержки нужного API.

Добавить env-only каталог `LLM_ALLOWED_MODELS` с записями `id`, допустимые роли задач, профиль, контекстное окно, max output. При отсутствии каталога доступны только уже настроенные модели; новая модель требует записи каталога и проверки подключения инженером. Значения не выводить обычному пользователю как детали провайдера.

Для каждой разрешённой модели: проверки streaming чата, structured JSON для генерации/классификации и перевода по назначению, применимости sampling-параметров. Проверка использует фиксированный несекретный текст, имеет timeout и не отправляет документы. Если модель недоступна, настройка не применяется. Внешняя доступность после проверки не гарантируется — рабочие ошибки обрабатываются штатно.

Новая попытка генерации сохраняет effective snapshot до первой работы и до помещения в очередь. Все её этапы используют его после retry/resume/restart. Regenerate создаёт новый снимок. Для старого checkpoint без снимка допускается сохранение текущей базы один раз с явной отметкой `legacy_adopted`; прежние параметры восстановить достоверно нельзя, это показывается оператору.

Не сохранять секреты в generation snapshot. При изменении идентичности подключения через env во время остановки сравнивать несекретный fingerprint подключения и останавливать resume с понятной причиной несовместимости. Ротация ключа того же подключения допустима.

`MAIL_IMPORT_ENABLED` проверяется на новой загрузке и фиксируется для обработки всего дерева вложений. Его выключение не скрывает уже сохранённые письма и не меняет источники существующего документа. Изменение размера чанков не переписывает опубликованные chunks/source spans.

`MAX_UPLOAD_MB` в B можно менять только в пределах env-only потолка развёртывания `UPLOAD_MAX_MB_CEILING`, согласованного с proxy/body limits. До отдельной настройки инфраструктуры UI разрешает только уменьшение относительно действующего проверенного потолка; проверять полный multipart-request на границе лимита. Если лимиты frontend/reverse proxy неизвестны, увеличение блокируется с объяснением.

## 5. API

Новые schemas в `backend/app/models/runtime_settings.py`; ключи JSON — snake_case по существующим именам `Settings`.

| Метод и путь | Контракт |
|---|---|
| `GET /api/admin/settings` | Ревизия, baseline fingerprint, schema_version; разрешённые поля с effective/base/override, source, bounds, apply_mode; безопасная read-only проекция |
| `POST /api/admin/settings/preview` | `{expected_revision, baseline_fingerprint, set, reset}`; проверяет итоговую конфигурацию, возвращает diff, последствия, confirmation token при необходимости; не пишет настройки |
| `PATCH /api/admin/settings` | То же + `confirmation_token?`, `reason?`; атомарное сохранение, возвращает новую ревизию и применённые значения |
| `GET /api/admin/settings/history?before_revision=&limit=` | История с пагинацией; максимум 100 записей; actor, время, изменения и effective snapshot без секретов |
| `POST /api/admin/settings/rollback-preview` | `{target_revision, expected_revision, baseline_fingerprint}`; строит set/reset относительно текущего состояния; результат применяется обычным PATCH |
| `GET /api/settings` | Существующая пользовательская проекция + `settings_revision`, `settings_baseline_fingerprint`; без каталога admin-полей и истории |
| B: `GET /api/admin/settings/models` | Проверенный env-каталог; без секретов и URL |
| B: `POST /api/admin/settings/models/probe` | Проверка выбранной разрешённой модели и назначения, ограничение частоты 5/мин/admin |

Поле нельзя одновременно передать в `set` и `reset`. Частичный успех запрещён. API не принимает произвольный путь, имя env-файла, Python-атрибут или команду.

Коды: `settings_validation_failed` (422), `settings_revision_conflict` (409), `settings_baseline_changed` (409), `settings_confirmation_required` (409), `settings_preview_stale` (409), `settings_unavailable` (503), `settings_model_probe_failed` (422). Поле ошибки содержит путь поля и i18n code, не полный сериализованный `Settings` или сырой ответ провайдера.

## 6. Интерфейс

- Новая ссылка «Настройки сервиса» в `Nav.jsx`, страница `/admin/settings` под `RequireRole`, отдельный `AdminSettingsPanel`.
- В A группы: «Поиск и чат», «Лимиты операций», «Экспорт», «Корзина», «История». Расширенные поисковые коэффициенты свёрнуты по умолчанию. В B добавляются «Обработка документов» и «Модели».
- Поле показывает понятную подпись, единицу, текущее значение, источник и момент применения. Рядом действие «Использовать настройку окружения»; при отсутствии env объяснять возврат к дефолту.
- Единый черновик страницы, общий preview и атомарное сохранение. Межгрупповые ограничения проверяются вместе. «Отменить изменения» сбрасывает только локальный черновик.
- Несохранённые изменения защищены при уходе со страницы. Обновление внешней ревизии не затирает черновик: баннер и сравнение локального diff с новой базой.
- История предлагает «Подготовить восстановление» и показывает новый diff; не выполняет прямое удаление старых записей.
- При успешном save обновить `ChatContext`; другие вкладки обновляют безопасную проекцию через событие/BroadcastChannel и на focus. Повторно проверять ограничения при submit; не передавать admin-значения через localStorage.
- Если пользовательский top_k стал недопустим, показать изменение диапазона и выбрать ближайшее допустимое значение в UI. Backend устаревший недопустимый запрос отклоняет, не использует скрытый clamp. История уже созданных ответов не меняется.
- Сохранить SSR/hydration: первый render не зависит от localStorage; сетевые данные и браузерные подписки подключаются после mount.
- Доступность: label/input, клавиатура, фокус на ошибке, уведомления для screen reader, узкая ширина без горизонтального прокручивания всей страницы.

## 7. Карта файлов

| Файл | Ответственность |
|---|---|
| Новый `backend/app/services/runtime_settings/catalog.py` | Явный каталог и разрешённые ключи; типизированные значения и ограничения |
| Новый `backend/app/services/runtime_settings/store.py` | Чтение singleton, CAS, история и транзакционный аудит |
| Новый `backend/app/services/runtime_settings/service.py` | Effective snapshot, preview, reset/rollback, baseline fingerprint |
| Новый `backend/app/services/runtime_settings/impact.py` | Read-only подсчёт последствий retention, confirmation token |
| Новый `backend/app/services/runtime_settings/models.py` (B) | Каталог моделей и проверка совместимости |
| Новые `backend/app/models/runtime_settings.py`, `backend/app/api/admin_settings.py` | Pydantic HTTP-контракты и admin endpoints |
| `backend/app/db/models.py`, `db/session.py`, Alembic | Таблицы, bootstrap и версионированная миграция |
| `backend/app/config.py` | Startup-конфигурация, источник базовых значений; B env-only allowlist/ceiling |
| `backend/app/services/audit.py`, `backend/app/api/errors.py` | Типы событий и стабильные коды ошибок |
| `backend/app/main.py`, `api/settings.py`, `models/schemas.py` | Роуты и безопасная пользовательская проекция |
| `api/chat.py`, `api/search.py`, `services/vector_store.py`, `context_builder.py`, `llm_client.py` | Снимок параметров одной интерактивной операции |
| `api/documents.py`, `api/jobs.py`, `services/bulk_generation.py`, `job_queue.py`, `export_queue.py` | Допуск и сохранение параметров принятых задач |
| `services/trash.py`, `chat_history.py` | Перечитывание политики между циклами |
| B: `services/pipeline.py`, `generation_store.py`, `llm_profiles.py`, `llm_scheduler.py`, `field_table.py`, `okf_generator.py` | Снимок генерации и передача параметров вложенным этапам; scheduler остаётся общим, queue timeout передаётся для конкретного вызова |
| Новые `frontend/src/app/admin/settings/page.js`, `components/AdminSettingsPanel.jsx`, `lib/adminSettings.mjs` | Страница, форма, diff/validation helpers |
| `frontend/src/components/Nav.jsx`, `context/ChatContext.js`, `lib/api.js` | Навигация, API, `friendlyApiError` и обновление проекции |
| `frontend/src/i18n/locales/ru.js`, `en.js` | Подписи, пояснения, ошибки |
| Новые `backend/tests/test_runtime_settings_*.py`, `frontend/test/adminSettings.test.mjs` | Контрактные и поведенческие проверки |
| Новые `docs/ADMIN_SETTINGS.md`, `docs/superpowers/reports/2026-09-27-admin-settings-acceptance.md` | Руководство и фактическая приёмка (отчёт заполняется при реализации) |

Новые файлы не подразумевают перенос несвязанных функций из существующих модулей. Проверять текущие имена и соседние инструкции перед изменением.

## 8. Задачи реализации

Каждая задача выполняется циклом: сначала поведенческий тест и ожидаемое падение, затем минимальная реализация, повтор целевых проверок, просмотр diff. Если коммиты разрешены, фиксировать только файлы этой задачи; не делать автоматический push. Команды Python ниже выполняются из `backend` в проектном venv, Node — из `frontend`.

### Задача 1. Каталог и чистая модель effective-настроек

**Files:** создать `services/runtime_settings/catalog.py`, `service.py`, `models/runtime_settings.py`, `tests/test_runtime_settings_catalog.py`; изменить `config.py` только для безопасного определения источника.

**Interfaces:** `SettingDefinition`, `RuntimeValues`, `RuntimeSnapshot`; `resolve_values(base: Settings, overrides: dict) -> RuntimeValues`; `describe_baseline(base: Settings) -> BaselineDescription` (values, sources, fingerprint только разрешённых полей).

- [ ] Написать `test_empty_overrides_preserve_baseline`, `test_admin_overrides_env`, `test_reset_restores_base_source`, `test_forbidden_keys_rejected`, `test_invalid_cross_field_values_rejected`, `test_snapshot_nested_values_immutable`.
- [ ] Запустить `python -m pytest tests/test_runtime_settings_catalog.py -q`; сначала ожидаются ошибки отсутствующей реализации.
- [ ] Реализовать каталог A и строгую пакетную валидацию. Для зависимости `SEARCH_PER_BRANCH_TOP_K ≥ CHAT_TOP_K_MAX` проверить baseline до запуска функции; без автоматической смены пользовательских значений.
- [ ] Повторить тесты; добавить marker-secret в startup Settings и доказать, что он отсутствует во всех сериализуемых описаниях/ошибках.

### Задача 2. БД, ревизии, аудит и восстановление

**Files:** `store.py`, `db/models.py`, `db/session.py`, `services/audit.py`, новая Alembic revision, `tests/test_runtime_settings_store.py`, `tests/test_runtime_settings_migration.py`.

**Interfaces:** `load_state(session) -> SettingsState`; `apply_patch(session, *, actor, expected_revision, baseline_fingerprint, patch, reason) -> RuntimeSnapshot`; `list_revisions(*, before_revision, limit) -> list[SettingsRevision]`. HTTP-уровень открывает транзакцию через существующий session lifecycle; ни один вложенный helper не делает отдельный commit.

- [ ] Тесты: `test_two_writers_one_conflict`, `test_audit_failure_rolls_back_state`, `test_noop_does_not_create_revision`, `test_restart_reads_committed_revision`, `test_old_revision_retry_is_not_duplicated`.
- [ ] Миграционные тесты: свежая БД, upgrade с предыдущего head, повторный `init_db`, SQLite и PostgreSQL; миграция не содержит секретов и не меняет бизнес-данные.
- [ ] Проверить реальные Alembic heads; создать одну revision от актуального head. Не делать `stamp head` на рассинхронизированной dev-БД. Для dev, где create_all уже создаёт таблицы, использовать предусмотренный bootstrap новых таблиц и сверку схемы, не маскировать историю Alembic.
- [ ] Добавить `runtime_settings_update`/`runtime_settings_rollback` в `ACTION_TYPES`; audit old/new содержит только diff разрешённых полей, revision и reason.
- [ ] Запустить `python -m pytest tests/test_runtime_settings_store.py tests/test_runtime_settings_migration.py tests/test_audit.py -q`; подтвердить реальные конкурентные транзакции отдельными сессиями, не только mock rowcount.

### Задача 3. Горячие настройки поиска и чата

**Files:** `service.py`, `api/search.py`, `api/chat.py`, `api/settings.py`, `models/schemas.py`, `services/vector_store.py`, `context_builder.py`, `llm_client.py`; `tests/test_runtime_settings_search.py`, существующий `tests/test_settings.py`.

**Interfaces:** `capture_runtime_settings() -> RuntimeSnapshot`; существующие точки входа поиска/формирования контекста получают keyword-only `runtime: RuntimeSnapshot`. LLM-клиент получает параметры операции, инфраструктурное соединение остаётся startup-объектом.

- [ ] Тесты: `test_next_request_sees_new_revision`, `test_inflight_chat_keeps_snapshot`, `test_search_branches_share_revision`, `test_vector_client_is_reused`, `test_settings_projection_has_no_admin_fields`.
- [ ] Перевести query-time чтения перечисленных в A полей с `self.settings`/повторных `get_settings()` на runtime snapshot. Не менять индексную формулу и embedding-настройки.
- [ ] Для старого пользовательского top_k/mode вернуть локализуемую ошибку с допустимыми значениями. Тесты на обе границы диапазона, 0, отрицательные и очень большие значения.
- [ ] Проверить `503` при невозможности загрузить настройки и отсутствие LLM/Qdrant вызовов после отказа.
- [ ] Запустить `python -m pytest tests/test_runtime_settings_search.py tests/test_settings.py -q`. Выполнить baseline/after `backend/test_scripts/probe_sources.py` на том же корпусе; при пустых overrides результаты должны совпадать, изменение ранжирования проверяется отдельно.

### Задача 4. Лимиты операций и снимок экспорта

**Files:** `api/documents.py`, `api/jobs.py`, `services/bulk_generation.py`, `job_queue.py`, `export_queue.py`; `tests/test_runtime_settings_limits.py`, `tests/test_runtime_settings_export.py`.

**Interfaces:** `ExportRuntimePolicy` — immutable разрешённая проекция; сериализуется в `Job.params.runtime_settings` при submit, читается execute/completion/recovery. Admission-проверки принимают текущий RuntimeSnapshot.

- [ ] Тесты: `test_lower_rate_limit_preserves_usage`, `test_queued_batch_not_truncated`, `test_export_submit_snapshot_survives_restart`, `test_export_ttl_not_changed_while_queued`, `test_download_disable_preserves_active_lease`, `test_disk_guard_uses_current_free_space`.
- [ ] Перевести допуск на effective limits; сохранить действующий учёт user_id и часовые окна. Политика «5 за 3 часа» в эту работу не входит.
- [ ] Параметры архива и TTL читать только из сохранённого снимка. Для legacy export jobs без снимка один раз сохранить текущие параметры до выполнения и отметить это в результате/аудите.
- [ ] Сохранить актуальные проверки разрешения скачивания и защиты диска независимо от снимка задания.
- [ ] Запустить новые тесты и `python -m pytest tests/test_bulk_ops.py tests/test_bulk_resume.py tests/test_export_queue.py tests/test_bulk_export_api.py -q`.

### Задача 5. Политика корзин и предпросмотр последствий

**Files:** `impact.py`, `services/trash.py`, `chat_history.py`, `main.py`; `tests/test_runtime_settings_retention.py`.

**Interfaces:** `preview_retention_change(*, current, candidate, actor) -> RetentionImpact`; `verify_confirmation(*, token, actor, current, candidate) -> None`. RetentionImpact содержит counts, bytes где доступны, observed_at, fingerprint и confirmation token.

- [ ] Тесты: активные данные исключены; сокращение срока требует подтверждения; включение purge тоже; чужой/просроченный токен отклонён; изменение preview-состава даёт 409; восстановление после preview защищает объект от purge.
- [ ] Добавить управляемые однократные циклы с чтением политики на каждом проходе и shutdown event. Тест: off → on → off не создаёт дополнительные потоки; off при старте можно включить без рестарта.
- [ ] Проверить, что PATCH ничего не удаляет, и что отказ БД пропускает цикл. Отдельно закрепить поведение уже начавшегося цикла.
- [ ] Запустить `python -m pytest tests/test_runtime_settings_retention.py -q`; использовать фиктивные часы и временные файлы, не реальную корзину.

### Задача 6. Административный HTTP API

**Files:** `api/admin_settings.py`, `models/runtime_settings.py`, `api/errors.py`, `main.py`; `tests/test_runtime_settings_api.py`.

**Interfaces:** endpoints из раздела 5; `SettingsPatch` со строгой схемой, запрещёнными extra-полями и явным set/reset.

- [ ] Контрактные тесты: anonymous 401 при включённой auth; viewer/editor/security 403; admin читает/сохраняет; прямой URL не обходит запрет. Dev-disabled auth проверять отдельно по существующему контракту, не выдавать его за production-защиту.
- [ ] Тесты CSRF существующего механизма, set/reset пересечение, запрещённые ключи, пакетная атомарность, 409 revision/base conflicts, rollback через новую ревизию, недоступная БД.
- [ ] Подключить роут к protected router с `require_role("admin")`. GET admin/history/preview — `Cache-Control: no-store`; пользовательскую проекцию тоже не кэшировать общим CDN.
- [ ] Проверить сообщения исключений: payload с неизвестным secret-like ключом не отражается целиком в detail/logs.
- [ ] Запустить `python -m pytest tests/test_runtime_settings_api.py tests/test_settings.py -q`.

### Задача 7. Страница настроек и обновление пользовательской проекции

**Files:** frontend-файлы из раздела 7; `frontend/test/adminSettings.test.mjs`, тесты API errors/i18n при расширении контрактов.

**Interfaces:** `buildSettingsPatch(base, draft) -> {set, reset}`; `mergeSettingsConflict(base, draft, latest) -> {draft, conflicts}` в `adminSettings.mjs`; `refreshSettings()` в ChatContext, вызываемый после успешного save/focus.

- [ ] Node-тесты: diff set/reset; числовые пустые поля; источник после reset; 409 не теряет черновик; ошибка API не показывает сырой detail; обновление диапазона не меняет допустимый выбор пользователя.
- [ ] Реализовать страницу и форму A без универсального JSON-редактора. Перед опасным сохранением показывать preview и отдельное подтверждение; для обычного изменения достаточно review diff и «Сохранить».
- [ ] Добавить историю, восстановление через preview, RU/EN строки, защиту несохранённого черновика и состояния loading/error/retry.
- [ ] Обновить пользовательскую проекцию в текущей и другой вкладке. Ошибка обновления не затирает последний UI-снимок дефолтами; server остаётся источником допуска.
- [ ] Запустить `node --test test/adminSettings.test.mjs`, затем `node --test`, `node node_modules/eslint/bin/eslint.js .`, `node node_modules/next/dist/bin/next build`.
- [ ] Выполнить браузерные сценарии раздела 9 для A; приложить фактические результаты в отчёт.

### Задача 8. Каталог моделей и формы обработки (B)

**Files:** `config.py`, runtime catalog/models/service, API models, `llm_profiles.py`, AdminSettingsPanel, i18n; `tests/test_runtime_settings_models.py`, `tests/test_runtime_settings_generation_policy.py`.

**Interfaces:** `list_allowed_models() -> tuple[AllowedModel, ...]`; `probe_model(*, model_id, purpose, actor) -> ModelProbeResult`; `GenerationPolicy` — типизированная immutable проекция B. Probe evidence привязан к модели, назначению, fingerprint подключения и актуален 5 минут при сохранении модельных изменений.

- [ ] Сначала тесты allowlist, подмены endpoint/provider через model id, отклонения несовместимых параметров, пустого fallback chat_model, недоступной модели и безопасного probe payload.
- [ ] Добавить B-поля и профильные ограничения; UI показывает только совместимые параметры, backend проверяет даже скрытые поля.
- [ ] Реализовать проверки моделей с фиксированным текстом, ограничением частоты и таймаутом. Не отправлять документы и не писать токены/ответы провайдера в журнал конфигурации.
- [ ] Реализовать upload ceiling и проверку полной цепочки browser → Next proxy → backend. Тесты размера около границы с multipart overhead и согласованной ошибкой UI.
- [ ] Запустить `python -m pytest tests/test_runtime_settings_models.py tests/test_runtime_settings_generation_policy.py -q`; сетевые проверки разрешённых моделей выполнять отдельно от hermetic CI.

### Задача 9. Снимок новой генерации и совместимость resume (B)

**Files:** новая таблица/миграция `generation_runtime_settings`, `generation_store.py`, `pipeline.py`, `llm_client.py`, `field_table.py`, реальные модули генерации/перевода и очередей; `tests/test_runtime_settings_generation.py`.

**Interfaces:** `ensure_generation_snapshot(session, *, generation_id, runtime) -> GenerationPolicy`; `load_generation_snapshot(generation_id) -> GenerationPolicy`. Снимок сохраняется до первой постановки работы, не по первому LLM-вызову.

- [ ] Тесты: save во время документа не меняет следующие чанки; queued generation сохраняет прежние параметры; restart/resume использует снимок; regenerate использует новый; legacy adoption происходит один раз; неизвестная schema_version не обходится дефолтами.
- [ ] Тесты независимых документов с разными снимками и одним LLM-клиентом: параметры не перетекают между потоками. Проверить, что общий `self.settings` не переназначается даже временно.
- [ ] Передать policy через весь generation-путь, классификацию таблиц, retries и фоновые переводы. Для принятых batch-переводов сохранять снимок в их существующих job params.
- [ ] Включить все параметры, влияющие на результат классификации, в идентичность кэша либо обеспечить проверенное разделение по fingerprint policy; сохранить документную границу regenerate, не очищать общий каталог кэша.
- [ ] Проверить checkpoint parser_version/mail flag, fingerprint подключения, отмену обновления и сохранение опубликованной версии. Никакого изменения offsets/source provenance при редактировании настроек.
- [ ] Запустить `python -m pytest tests/test_runtime_settings_generation.py tests/test_generation_pipeline.py tests/test_generation_store.py tests/test_generation_publication_service.py -q` и профильные тесты LLM/таблиц, найденные по потребителям B-полей.

### Задача 10. Документация, эксплуатация, CI и выпуск

**Files:** `docs/ADMIN_SETTINGS.md`, `docs/LIMITS.md`, `docs/PRODUCTION_DEPLOYMENT.md`, `.env.example` если он существует, CI workflow(s), новый `backend/scripts/runtime_settings.py`, отчёт приёмки.

**Interfaces CLI:** `runtime_settings.py check` — read-only проверка схемы/базы/overrides; `export --output PATH` — только разрешённая конфигурация; `reset --keys ... --expected-revision N --reason TEXT --apply` — контролируемый сброс с CAS и аудитом. По умолчанию reset показывает preview без записи; никаких произвольных SQL или путей конфигурации.

- [ ] CLI-тесты: экспорт без секретов, stale revision, dry-run без изменений, восстановление после invalid overrides, сбой аудита. Для invalid state разрешать удаление неверных overrides, не обходя validation оставшегося набора.
- [ ] Описать источник значений, сброс/восстановление, отличия A/B, сроки применения, сохранение снимков и ограничения отката. Не обещать смену startup-параллельности без рестарта.
- [ ] Добавить PostgreSQL-интеграционные проверки CAS/миграций в CI и сохранить SQLite-проверки. Контентные проверки CI использовать через `git grep`, не требовать `rg` на runner.
- [ ] Backend: `python -m ruff check .`, затем существующий полный pytest с coverage gate 85%; frontend: полные Node tests, lint, build. Не ослаблять существующие security/dependency gates.
- [ ] Провести браузерную приёмку и rehearsal релиза/отката на тестовом контуре, записать команды, версии, PASS/FAIL и внешние блокеры. Не обозначать непроверенные сценарии пройденными.

## 9. Приёмочные сценарии в браузере

1. Admin видит страницу; editor/viewer/security не видят пункт и получают отказ на прямой URL/API. RU/EN одинаково покрывают поля и ошибки.
2. Изменить top_k default, сохранить, открыть чат другой вкладкой: новый default виден, допустимый существующий выбор сохранён. Текущий stream завершается со старым снимком.
3. Изменить связанный набор min/default/max/presets одной операцией. Некорректное сочетание не меняет ни одного поля.
4. Два admin окна: A сохраняет, B сохраняет старый черновик — конфликт и сравнение; черновик B не теряется.
5. Переопределить поле, изменить env на тестовом контуре и перезапустить: override имеет приоритет, base обновлён; reset возвращает новую базу, история объясняет различие.
6. Уменьшить лимит операций после его частичного расходования: счётчик сохраняется, видна штатная ошибка ожидания.
7. Поставить экспорт в очередь, поменять part size и TTL, перезапустить: задание использует исходные значения. Новое задание — новые.
8. Запретить скачивание готового экспорта: новая загрузка отклонена сервером; уже начатая передача не ломается.
9. Сократить срок корзины: preview показывает только удалённые объекты; отмена ничего не меняет; подтверждение сохраняет политику, а очистка выполняется своим циклом.
10. Восстановить объект между preview и purge: он не удаляется. При недоступной БД никакой purge не переходит к дефолтам.
11. Reload страницы и backend restart сохраняют настройки и историю. После потерянного HTTP-ответа повтор не создаёт новую ревизию.
12. B: новая модель проверяется фиксированным тестом; произвольный model/provider identifier отвергается. Документ до изменения и после него имеют разные сохранённые снимки.
13. B: pause/restart/resume сохраняет прежний generation snapshot, а regenerate создаёт новый. Публикация и source viewer работают после обоих сценариев.
14. B: письмо со вложениями при включённом импорте проходит всё дерево; выключение запрещает новые загрузки, не скрывает прежние источники. Проверить точную границу размера через реальный proxy.

## 10. Выпуск и откат

1. До релиза снять backup БД и конфигурации стандартной процедурой, выполнить read-only `check`; зафиксировать baseline и схему. На пользовательских данных не испытывать сокращение retention.
2. Проверить upgrade на копии production-like БД и fresh install. При dev-рассинхронизации Alembic сначала сверить существующую схему; не запускать слепой upgrade/stamp.
3. Развернуть A с пустыми overrides: поведение должно совпасть с текущим. Проверить здоровье сервисов, поиск, чат, допуск задач, экспорт и отсутствие утечки секретов.
4. Сначала изменить обратимый параметр поиска, проверить API/UI, рестарт и reset. Только после этого включать эксплуатационную настройку лимитов и retention.
5. При дефекте отдельных значений применить новую ревизию reset/rollback; запреты безопасности и валидация остаются действующими.
6. При откате кода A остановить новые операции и штатно завершить/остановить workers; сохранить export конфигурации. Вернуть предыдущую версию приложения, новые таблицы оставить. Старый код использует env и не знает overrides — перед запуском явно согласовать env с необходимой политикой. Не делать downgrade с удалением истории.
7. Перед B закончить либо зафиксировать активные старые попытки; проверить legacy adoption на копии данных. Выпустить отдельно от A.
8. При откате B не позволять старому коду автоматически возобновить задания с неподдерживаемыми snapshots. Остановить/вывести из очереди такие задания штатно до запуска старой версии; сохранить их состояние для возвращения на совместимую версию. Откат кода не отменяет уже опубликованные результаты.
9. Экспорт настроек не заменяет backup содержимого, секретов и storage. Откат срока корзины не восстанавливает физически удалённые документы/чаты.

## 11. Зависимости и порядок

`1 → 2 → 3 → 4 → 5 → 6 → 7 → 10(A)` — первая поставка.

`8 → 9 → 10(B)` — вторая поставка после приёмки A.

UI-контракты задаются до frontend. Параллельную реализацию здесь не планируем: API, каталог и снимки имеют общие интерфейсы, а изменения затрагивают одни и те же сервисы. Независимый review полезен на границах A/B, если пользователь выберет такой способ выполнения.

## 12. Критерии готовности

- Все ключи разрешённого списка имеют работающего потребителя, тест фактического поведения и понятный apply_mode; «сохраняется, но не применяется» не считается готовностью.
- При пустых overrides нет изменения поведения сервиса; существующие API и сохранённые пользовательские данные совместимы.
- После успешного save новый запрос получает новую ревизию; уже начатая операция сохраняет свою.
- Конкурентные изменения не теряются; изменение и аудит атомарны; reset/rollback проходят ту же валидацию.
- Секреты и настройки авторизации недоступны через новые API, таблицы, логи и CLI export; права проверяются на сервере.
- Настройки переживают рестарт, upgrade проверен на PostgreSQL и SQLite; откат отрепетирован.
- A: поиск/чат, лимиты, экспорт, корзины и браузерные сценарии 1–11 пройдены. B: дополнительно пройдены сценарии 12–14 и совместимость generation snapshots.
- CI остаётся зелёным без снижения существующих порогов. Отчёт содержит фактические результаты и явно перечисляет оставшиеся ограничения.

**Результат текущей работы:** подготовлен план. Продуктовый код, `.env`, БД и работающие сервисы не изменялись.
