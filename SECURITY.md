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
| `editor` | + изменения контента: загрузка/удаление документа, resume/regenerate, привязка разработки, правка тегов, создание/обновление разработок и значений атрибутов (`require_role("editor","admin")`) |
| `admin` | + массовые/деструктивные операции и задачи: bulk-delete, bulk-regenerate, approve/cancel job, удаление разработки (`require_role("admin")`) |
| `security` | блокировка/разблокировка пользователей (`users.py`) и read-only журнал ИБ (`audit.py`, `require_role("security")`) |

Единственное активное действие роли Security — блоклист (`app/services/blocklist.py`),
проверяется в `require_user` (заблокированный не проходит даже с валидной сессией).

## 3. Сетевая топология

- **Наружу публикуется только frontend**: `docker-compose.yml` — `frontend: "8080:3000"`.
  `backend` и `qdrant` host-портов не публикуют (доступны только внутри compose-сети).
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
- **Soft delete**: не реализован — удаление документа жёсткое (БД + Qdrant) и
  необратимое. «Корзина» (двухслойный soft delete: `deleted_at` в БД + `payload.deleted`
  в Qdrant с обязательным `must_not: {deleted: true}` на всех точках обращения к Qdrant)
  спроектирована, но не реализована — `OKF_Knowledge_Service_Roadmap.md`, Этап 4a.2.
  До внедрения восстановление невозможно; само удаление фиксируется в audit_log
  (`document_delete`), но без окна отмены. Security-нюанс проекта корзины — дисциплина
  фильтра `deleted` на всех путях поиска: пропуск хотя бы одной точки входа даст
  утечку «удалённого» документа в ответы чата.
- **Retention**: `audit_retention_days=365` (`app/config.py`) задаёт срок журнала ИБ,
  но автоматическая очистка не реализована (см. «принятые риски»).
- **Журнал ИБ** (`app/services/audit.py`): append-only (есть только `append`/`query`,
  нет update/delete). Пишутся мутирующие действия (загрузка/удаление/регенерация,
  массовые операции, смена разработки, блокировки, CRUD разработок/атрибутов) с
  `username`, `target_id`, `old_value`/`new_value`, `ip_address`. Читает только роль
  `security` (`app/api/audit.py`). Для PostgreSQL-прода рекомендуется отдельный
  сервисный аккаунт с правами только на INSERT (см. `README.md`, «Хардненинг audit_log»).

## 6. Известные ограничения / принятые риски

- **Шифрование at rest отсутствует** — защита возлагается на инфраструктуру (диск/том/СУБД).
- **Внутренний трафик HTTP** — доверенная compose-сеть; TLS только на внешнем прокси.
- **Нет автоматической очистки audit_log** — срок задан (`audit_retention_days`), очистка
  не автоматизирована.
- **Нет soft delete / корзины** — удаление необратимо.
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

### 2026-08-31 — Открытая сетевая поверхность в production
Найдено: `AUTH_PROVIDER=disabled/simulation` не запрещались в production
(`app/config.py` — валидатор проверял только секрет и HTTPS-cookie, но не провайдер);
backend/Qdrant публиковались наружу (`docker-compose.yml` `ports` для `:8000`, `:6333`,
`:6334`); `CORS allow_origins=["*"]` (`app/main.py`).
Исправлено: fail-fast валидатор запрещает `disabled`/`simulation` в production; убрана
host-публикация backend/Qdrant (наружу только frontend); CORS сведён к пустому
allow-list `CORS_ALLOWED_ORIGINS` (браузер обращается к backend только через
server-side rewrites фронтенда); backend в локальном скрипте биндит `127.0.0.1`.
