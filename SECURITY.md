# SECURITY.md — модель безопасности

Справочный документ о том, как устроена безопасность сервиса и какие границы
доверия осознанно выбраны. Не пересказывает реализацию — ссылается на код по
имени файла/функции. Актуализируется вместе с изменениями (правило — в `AGENTS.md`).

Разделение с другими документами:
- **`PRODUCTION_DEPLOYMENT.md`** — операционный чек-лист «что проверить/настроить
  перед запуском в прод» (секреты, fail-fast проверки, сиды БД). Ссылки на него —
  в разделах ниже.
- **`SECURITY.md`** (этот файл) — более широкая модель: угрозы, границы доверия,
  роли, данные, журнал инцидентов.

## 1. Модель угроз и границы доверия

- **Клиент системы** — корпоративный пользователь с ролью (Viewer/Editor/Admin/Security),
  аутентифицированный через корпоративный SSO (Keycloak/IDB). Анонимного доступа в
  production нет.
- **Публичная точка входа — только frontend** (Next.js). Браузер ходит same-origin на
  фронтенд; `/api/*` и `/health` проксируются на бэкенд **серверно** через
  `rewrites()` в `frontend/next.config.js`. Браузер с бэкендом напрямую не контактирует.
- **Не должно быть напрямую достижимо снаружи**: backend (`:8000`), Qdrant (`:6333`/`:6334`),
  PostgreSQL (`:5432`), Ollama (`:12400`). В `docker-compose.yml` наружу публикуется
  только `frontend` (`8080:3000`).
- **Граница доверия** — reverse-proxy + слой аутентификации бэкенда. Всё, что внутри
  compose-сети, считается доверенным (backend ↔ Qdrant ↔ БД ↔ Ollama ходят по HTTP).
- **Основные угрозы**: утечка корпуса документов через открытую сетевую поверхность,
  обход авторизации через слабый/дефолтный секрет или неверный `AUTH_PROVIDER`,
  неавторизованное изменение данных через недостаточную проверку ролей.

## 2. Аутентификация и авторизация

### Провайдеры аутентификации (`AUTH_PROVIDER`, `app/config.py`)

| Значение | Назначение | Допустим в production |
|---|---|---|
| `disabled` | всё открыто (аноним) | **нет** — запрещён валидатором |
| `simulation` | демо-пользователи (`AUTH_SIM_USERS`), `/auth/simulate` | **нет** — запрещён валидатором |
| `keycloak_oidc` | корпоративный SSO через IDB/broker (authlib) | да |
| `direct_ldap` | заглушка, не реализована | нет |
| `custom_client` | заглушка, не реализована | нет |

`validate_auth_provider` в `app/config.py` при `ENVIRONMENT=production` **fail-fast**
отклоняет `disabled`/`simulation` (`ValueError` до поднятия uvicorn), а также слабый
`APP_SECRET_KEY` и `AUTH_SESSION_HTTPS_ONLY=false`.

### Поток проверки

- `require_user` (`app/auth/service.py`) — текущий пользователь или `401`/`403`:
  - `disabled` → анонимный `public_user()`;
  - нет сессии → `401`;
  - роль не опознана и `AUTH_DEFAULT_ROLE` пуст (fail-closed) → `403`;
  - пользователь в активном блоклисте → `403`.
- Роль вычисляется на каждый запрос через `GroupRoleAuthorizer` (`app/auth/authorizer.py`):
  маппинг групп → ролей из `AUTH_ROLE_GROUPS`, приоритет `security > admin > editor > viewer`.
- Все «деловые» роутеры включены под единым guard `APIRouter(dependencies=[Depends(require_user)])`
  в `app/main.py:137` — защита на уровне роутера, плюс явные `Depends(require_user/require_role)`
  на эндпоинтах.

### Матрица ролей

| Роль | Права |
|---|---|
| `viewer` | read-only: список/поиск/чат, просмотр документов, разработок, тегов, атрибутов (`require_user`) |
| `editor` | + изменения контента: загрузка/удаление документа, resume/regenerate, привязка разработки, правка тегов, массовое редактирование тегов, удаление неиспользуемых тегов из справочника, создание/обновление разработок и значений атрибутов (`require_role("editor","admin")`) |
| `admin` | + массовые/деструктивные операции и задачи: bulk-delete, bulk-regenerate, approve/cancel job, удаление разработки (`require_role("admin")`) |
| `security` | блокировка/разблокировка пользователей (`users.py`) и read-only журнал ИБ (`audit.py`, `require_role("security")`) |

Массовое редактирование тегов (`POST /documents/bulk-tags`, Этап 4a) — синхронная
недеструктивная операция, поэтому доступна `Editor`/`Admin`, в отличие от
bulk-delete/bulk-regenerate (только `admin`). Лимит — `BULK_TAGS_MAX_DOCS` (50),
сознательно не ниже порога перегенерации (`bulk_regenerate_max_docs=20`).

Единственное активное действие роли Security — блоклист (`app/services/blocklist.py`),
проверяется в `require_user` (заблокированный не проходит даже с валидной сессией).

Чтение чужой истории чата (Этап 6) доступно ролям `security` и `admin` через
`/chat/admin/history/*` (`require_role("security","admin")`). Открытие содержимого
чужого треда пишется в журнал ИБ (`chat_history_view`); просмотр только списка
сессий и собственной истории — не логируется. Владелец видит только свою историю:
`session_id` валидируется на бэкенде и привязывается к текущему `user_id`
(`app/services/chat_history.py`), поэтому подменить чужой тред нельзя
(`store_turn`/`get_thread` бросают `ChatOwnershipError`).

## 3. Сетевая топология

- **Наружу публикуется только frontend**: `docker-compose.yml` — `frontend: "8080:3000"`.
  `backend` и `qdrant` host-портов не публикуют (доступны только внутри compose-сети).
- **Qdrant — опциональный сервис compose** (`profiles: ["local-qdrant"]`). По умолчанию
  не поднимается; backend подключается по `QDRANT_URL` из `.env` — либо к внутреннему
  `http://qdrant:6333` (профиль включён, доверенная compose-сеть), либо к внешнему
  корпоративному Qdrant. В последнем случае трафик backend → Qdrant выходит за пределы
  доверенной сети, поэтому **обязателен HTTPS** и, если требует инфраструктурная
  политика, `QDRANT_API_KEY` — тот же принцип, что и для внешних LLM/embeddings.
- **Postgres — опциональный сервис compose** (`profiles: ["local-postgres"]`). По умолчанию
  не поднимается; backend подключается по `DATABASE_URL` из `.env` — либо к внутреннему
  `postgres:5432` (профиль включён, доверенная compose-сеть), либо к
  внешнему корпоративному Postgres. В последнем случае трафик backend → Postgres выходит
  за пределы доверенной сети — сетевое соединение должен защищать сам корпоративный
  контур (TLS/VPN); пароль передаётся в `DATABASE_URL`.
- **Bind backend** (`backend/Dockerfile`): `uvicorn --host ${UVICORN_HOST:-0.0.0.0}` —
  `0.0.0.0` нужен для межконтейнерной связи; изоляция достигается отсутствием
  host-публикации порта, а не выбором host. Локальный запуск (`scripts/start-all.ps1`)
  биндит `127.0.0.1` (loopback, только с той же машины).
- **CORS**: `allow_origins=settings.cors_allowed_origins` (`app/main.py`), дефолт — пустой
  список (`CORS_ALLOWED_ORIGINS`). Браузер не обращается к бэкенду напрямую (server-side
  rewrites), поэтому cross-origin CORS не нужен, а wildcard был бы дырой. Cross-origin
  развёртывание (фронт на домене A, бэк на домене B без прокси) **несовместимо** с
  signed-cookie сессией.
- **TLS**: терминируется на внешнем reverse-proxy (nginx/Ingress/ALB) — не в репозитории
  (отдельный инфраструктурный слой). Внутри compose-сети трафик HTTP (доверенная сеть).

## 4. Секреты и конфигурация

Секреты (не в git, только в секрет-хранилище / `.env` с ограниченными правами):

| Переменная | Что защищает |
|---|---|
| `APP_SECRET_KEY` | подпись сессионной cookie (`TimestampSigner`, Starlette `SessionMiddleware`); дефолт `dev-secret-change-me` — подделка сессии → обход авторизации |
| `KEYCLOAK_CLIENT_SECRET` | конфиденциальный OIDC-клиент |
| `DATABASE_URL` | пароль PostgreSQL |
| `POSTGRES_PASSWORD` | пароль суперпользователя для compose-сервиса `postgres` (профиль `local-postgres`, dev-дефолт `okf_dev_pg`) |
| `QDRANT_API_KEY` | доступ к корпоративному Qdrant (если тот требует авторизации) |
| `LLM_API_KEY`, `EMBEDDING_API_KEY` | доступ к внешним LLM/embedding-провайдерам |

Инварианты, проверяемые `validate_auth_provider` в `app/config.py` (production):
`AUTH_PROVIDER ∉ {disabled, simulation}`; `APP_SECRET_KEY` случайный и `>= 32` симв.;
`AUTH_SESSION_HTTPS_ONLY=true`. Полный чек-лист — `PRODUCTION_DEPLOYMENT.md`.

## 5. Данные и приватность

- **Хранилища**: метаданные — PostgreSQL/SQLite (`app/db/`); бинарники и `.md`-бандлы —
  `data/uploads`, `data/okf_bundles`, `data/staging`; векторы/мини-payload — Qdrant
  (полный текст концепта хранится в `okf_concepts`, а не в payload Qdrant).
- **Шифрование at rest**: не используется — полагается на шифрование диска/тома хоста
  и СУБД (см. «принятые риски»).
- **Шифрование in transit**: только на внешнем reverse-proxy (HTTPS). Внутренняя сеть —
  HTTP (доверенная).
- **Soft delete / корзина**: реализован (Этап 4a.2) — двухслойная модель:
  `deleted_at`/`deleted_by` в `documents` (Postgres) + payload-флаг `deleted=true`
  на всех точках документа в Qdrant (через `set_payload`, без физического
  `Delete Points`). Все пути поиска к Qdrant (основной RAG-поиск из чата,
  `/search`, graph-expansion) обязаны добавлять `must_not: {deleted: true}`
  через единую обёртку `vector_store._not_deleted()` / `_build_search_filter()` —
  дисциплина фильтра в одном месте, чтобы новый сценарий поиска не забыл
  условие. Дополнительная защита: `/chat` и `/search` отбрасывают хиты, чей
  документ помечен удалённым в БД (закрывает гонку между `deleted_at` и ещё не
  синхронизированным payload). Восстановление из корзины снимает оба флага без
  пере-эмбеддинга. Окончательное физическое удаление (Qdrant `Delete Points` +
  `DELETE` из БД + файлы) — только фоновой задачей после истечения
  `trash_retention_days` (14 дней); до этого восстановление доступно в любой
  момент. Автоочистка пишется в audit_log как системное действие
  (`document_auto_delete`, `user_id="system"`), пользовательские удаление/
  восстановление — `document_delete` / `document_restore` /
  `document_bulk_restore`. При восстановлении прогоняется дедупликация против
  активных документов (`find_active_duplicates_for_document`) — конфликт
  возвращает 409 `code=duplicate`; force-восстановление доступно по явному
  `?force=true`.
- **История чата** (Этап 6): хранится в `chat_sessions`/`chat_messages`
  (PostgreSQL/SQLite). Активная история хранится бессрочно; ручное удаление треда —
  soft delete (`deleted_at`/`deleted_by`), физическая очистка — фоновой задачей
  (`app/services/chat_history.py:start_chat_purge_loop`) после истечения
  `chat_history_retention_days` (90 дней), с записью `chat_history_auto_delete`
  (`user_id="system"`). Сообщения хранят снапшот источников (`sources`) на момент
  ответа — не «протухает» при переиндексации.
- **Retention**: `audit_retention_days=365` (`app/config.py`) задаёт срок журнала ИБ,
  но автоматическая очистка не реализована (см. «принятые риски»).
- **Журнал ИБ** (`app/services/audit.py`): append-only (есть только `append`/`query`,
  нет update/delete). Пишутся мутирующие действия (загрузка/удаление/регенерация,
  массовые операции, смена разработки, правки тегов — включая каждую запись
  массовой правки тегов на затронутый документ, удаление/очистку тегов
  справочника (`tag_delete`/`tag_cleanup`), блокировки, CRUD разработок/
  атрибутов, просмотр чужой истории чата (`chat_history_view`) и автоочистку
  истории чата (`chat_history_auto_delete`)) с `username`, `target_id`,
  `old_value`/`new_value`, `ip_address`.
  Читает только роль `security` (`app/api/audit.py`). Для PostgreSQL-прода
  рекомендуется отдельный сервисный аккаунт с правами только на INSERT
  (см. `README.md`, «Хардненинг audit_log»).

## 6. Известные ограничения / принятые риски

- **Шифрование at rest отсутствует** — защита возлагается на инфраструктуру (диск/том/СУБД).
- **Внутренний трафик HTTP** — доверенная compose-сеть; TLS только на внешнем прокси.
- **Нет автоматической очистки audit_log** — срок задан (`audit_retention_days`), очистка
  не автоматизирована.
- **Провайдеры `direct_ldap` / `custom_client`** — заглушки; в проде использовать нельзя
  (валидатор разрешает их формально, но они не реализованы — единственный рабочий
  прод-провайдер `keycloak_oidc`).
- **`AUTH_DEFAULT_ROLE`** при непустом значении отдаёт дефолтную роль незнакомым
  пользователям — в проде рекомендуется пустое значение (fail-closed).
- **Rate limiter** (`app/services/rate_limiter.py`) — in-memory sliding window; для
  multi-worker прода нужен внешний бэкинг (Redis/БД).
- **In-memory секреты сессии** — сессия в signed-cookie (не хранится на сервере):
  компрометация `APP_SECRET_KEY` = компрометация всех сессий.

## 7. Журнал security-изменений

### 2026-08-31 — История чата (Этап 6)
Изменение: введена персистентная история чата (`chat_sessions`/`chat_messages`) с
мягким удалением и автоочисткой. Приватность: владелец видит только свою историю,
`session_id` привязывается к `user_id` на бэкенде (`ChatOwnershipError` при попытке
подменить чужой тред); чужую историю читают только `security`/`admin`, открытие
треда логируется (`chat_history_view`), а список сессий и собственная история — нет.
Retention: активная история бессрочна, удалённые треды физически чистятся фоном после
`chat_history_retention_days` (90), с записью `chat_history_auto_delete` (system).
Причина: история сохранялась только в памяти клиента; нужен управляемый доступ к
ней (владелец + аудируемый просмотр для ИБ) с той же моделью retention, что и у
корзины документов (4a.2).

### 2026-08-31 — Корзина / soft delete (Этап 4a.2)
Изменение: удаление документа больше не необратимое — введена двухслойная
корзина (`documents.deleted_at`/`deleted_by` + payload Qdrant `deleted=true`),
обязательный фильтр `must_not: {deleted: true}` на всех путях поиска через
единую обёртку, восстановление без пере-эмбеддинга и фоновая автоочистка после
`trash_retention_days`. Новые audit-типы: `document_restore`,
`document_bulk_restore`, `document_auto_delete` (системное). Причина: ранее
`DELETE` жёстко удалял документ без окна отмены; риск «случайного» удаления
снимается управляемой корзиной. Security-нюанс — дисциплина фильтра `deleted`
(утечка «удалённого» документа в чат при пропуске условия), закрыт централизованной
обёрткой + DB-side отсечкой в `/chat` и `/search`.

### 2026-08-31 — Qdrant опциональный (compose-профиль) + единый QDRANT_URL
Изменение: сервис `qdrant` в `docker-compose.yml` помечен профилем
`local-qdrant` (по умолчанию не поднимается), хардкод `QDRANT_URL=http://qdrant:6333`
в `environment` backend'а убран — URL приходит только из `.env`. Добавлена
опциональная `QDRANT_API_KEY` (передаётся в `QdrantClient(api_key=...)` только
если задана). Код приложения не различает локальный и корпоративный Qdrant.
Безопасностный нюанс: при внешнем Qdrant трафик выходит за доверенную
compose-сеть — обязателен HTTPS и (по политике) `QDRANT_API_KEY`; зафиксировано
в §3 и §4. Host-порт Qdrant по-прежнему не публикуется.

### 2026-08-31 — Postgres опциональный (compose-профиль) + единый DATABASE_URL
Изменение: в `docker-compose.yml` добавлен сервис `postgres` (`postgres:17`) с профилем
`local-postgres` (по умолчанию не поднимается). Backend подключается по `DATABASE_URL`
из `.env` — либо к внутреннему `postgres:5432` (профиль включён, доверенная
compose-сеть), либо к внешнему корпоративному Postgres; код приложения не различает
эти случаи. `DATABASE_URL_DEV` (SQLite) сохранён как третий zero-config вариант.
Пароль суперпользователя для compose-сервиса — `POSTGRES_PASSWORD` (dev-дефолт
`okf_dev_pg`). Безопасностный нюанс: при внешнем Postgres трафик выходит за доверенную
compose-сеть — сетевая защита возлагается на корпоративный контур (TLS/VPN); пароль в
`DATABASE_URL`. Host-порт Postgres не публикуется. Симметрично Qdrant (§3, §4) и
позволяет поднять полностью локальный стек одной командой
(`docker compose --profile local-qdrant --profile local-postgres up`).

### 2026-08-31 — Привязка разработки на upload (Этап 4a.1)
Изменение: `POST /documents` принимает необязательный form-параметр `development_id`
(пред-привязка разработки при загрузке из контекста чата). В журнал ИБ попадает в
существующую запись `document_upload` — поле `new_value.development_id` добавляется
только при явной привязке (форма `new_value` без привязки не меняется). Нового
action_type не вводилось; права — прежние `require_role("editor","admin")`. Влияния
на границы доверия/сетевую топологию нет.

### 2026-08-31 — Открытая сетевая поверхность в production
Найдено: `AUTH_PROVIDER=disabled/simulation` не запрещались в production
(`app/config.py` — валидатор проверял только секрет и HTTPS-cookie, но не провайдер);
backend/Qdrant публиковались наружу (`docker-compose.yml` `ports` для `:8000`, `:6333`,
`:6334`); `CORS allow_origins=["*"]` (`app/main.py`).
Исправлено: fail-fast валидатор запрещает `disabled`/`simulation` в production; убрана
host-публикация backend/Qdrant (наружу только frontend); CORS сведён к пустому
allow-list `CORS_ALLOWED_ORIGINS` (браузер обращается к backend только через
server-side rewrites фронтенда); backend в локальном скрипте биндит `127.0.0.1`.
