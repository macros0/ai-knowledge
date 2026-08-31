# Развёртывание в production

Контрольный список шагов, обязательных перед публикацией сервиса в прод.
Локальная разработка описана в `README.md` («Быстрый старт»); интеграция с
Keycloak/IDB — в `SSO_TESTING_GUIDE.md` (разделы 11–12). Здесь — только то, что
отличает прод от локального стенда, и то, что нельзя пропустить.

Модель безопасности (угрозы, роли, границы доверия, журнал инцидентов) — в
`SECURITY.md`. Этот файл — операционный чек-лист деплоя и ссылается на неё.

## 0. Чек-лист

| # | Шаг | Обязательно |
|---|---|---|
| 1 | Сгенерировать `APP_SECRET_KEY` (не дефолт, ≥ 32 симв.) и положить только в секрет-хранилище | ✅ блокер |
| 2 | `ENVIRONMENT=production` в реальном прод-окружении | ✅ блокер |
| 3 | `AUTH_SESSION_HTTPS_ONLY=true` | ✅ блокер |
| 4 | `AUTH_PROVIDER=keycloak_oidc` (НЕ `disabled` и НЕ `simulation` — бэкенд в prod падает с ними на старте) | ✅ блокер |
| 5 | Секреты `KEYCLOAK_CLIENT_SECRET`, БД, LLM/embedding — вне git и README | ✅ блокер |
| 6 | HTTPS/TLS терминируется на reverse-proxy, cookie ходит только по HTTPS | ✅ блокер |
| 7 | Зарегистрировать redirect/post-logout URIs на **прод**-Keycloak (IDB) | ✅ блокер |
| 8 | БД PostgreSQL, Qdrant, LLM/embeddings — прод-эндпоинты | ✅ |
| 9 | Проверить fail-fast: бэкенд не стартует с дефолтным секретом / без HTTPS / с `disabled|simulation` | ✅ |
| 10 | `alembic upgrade head` + сид модулей (`seed_attribute_values.py`, с учётом модулей клиента) | ✅ |
| 11 | `backfill_dedup.py` при переносе документов, загруженных до Этапа 4 | при миграции данных |

---

## 1. Секреты

**`APP_SECRET_KEY`** — ключ подписи сессионной cookie (`TimestampSigner`). Со
значением по умолчанию `dev-secret-change-me` сессию можно подделать (обход всей
авторизации), поэтому в проде это блокер.

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Правила хранения:
- **Никогда** не коммитить в git, не класть в `README.md` / `.env.example`.
- Хранить только в секрет-менеджере прод-инфраструктуры (Vault, K8s Secret,
  AWS Secrets Manager/SSM, CI-секрет) или в `.env` на сервере с правами
  только на чтение владельцу процесса.
- То же касается `KEYCLOAK_CLIENT_SECRET`, пароля БД, `LLM_API_KEY`,
  `EMBEDDING_API_KEY`.

---

## 2. Переменные окружения в проде

Бэкенд читает `.env` и переменные окружения (приоритет у окружения). Полный
список — в `.env.example`; здесь — обязательные для прода:

| Переменная | Значение в проде |
|---|---|
| `ENVIRONMENT` | `production` — включает fail-fast проверку конфигурации |
| `APP_SECRET_KEY` | случайный, ≥ 32 символов (см. §1) |
| `AUTH_SESSION_HTTPS_ONLY` | `true` — cookie только по HTTPS |
| `AUTH_SESSION_TTL_SECONDS` | по политике ИБ (дефолт `28800` = 8 ч) |
| `AUTH_PROVIDER` | `keycloak_oidc` (или ваш корпоративный путь) |
| `AUTH_ROLE_GROUPS` | JSON-маппинг прод-групп IDB → роли |
| `AUTH_DEFAULT_ROLE` | пусто = fail-closed (рекомендуется для контура ИБ) |
| `KEYCLOAK_URL` / `KEYCLOAK_REALM` / `KEYCLOAK_CLIENT_ID` / `KEYCLOAK_CLIENT_SECRET` | указывают на **IDB** (broker), не на AD/IDP |
| `SSO_REDIRECT_URI` | `https://<host>/api/auth/callback` |
| `SSO_POST_LOGOUT_REDIRECT_URI` | `https://<host>/` |
| `DATABASE_URL` | `postgresql+psycopg://user:pass@host:5432/okf_knowledge` (внешний прод-хост; НЕ `postgres:5432` compose-профиля `local-postgres`) |
| `QDRANT_URL` | прод-эндпоинт Qdrant (`https://…:6333`) |
| `QDRANT_API_KEY` | API-ключ Qdrant, если корпоративный Qdrant требует авторизации |
| `LLM_BASE_URL` / `LLM_API_KEY` | прод-провайдер LLM |
| `EMBEDDING_API_BASE` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` | прод-провайдер эмбеддингов |

`ENVIRONMENT=production` должен быть выставлен именно в **прод-инфраструктуре**
(compose/K8s/process env), а не «проверено локально, что без него падает».
В `docker-compose.yml` уже задан дефолт `ENVIRONMENT=${ENVIRONMENT:-production}` —
контейнер не поднимется в незащищённом режиме, если вы не переопределите его явно.

---

## 3. Инфраструктура

- **БД**: PostgreSQL (синхронный драйвер psycopg3). Схема создаётся на старте
  (`init_db`/`create_all`); версионированные миграции — Alembic (`backend/alembic/`).
  В проде использовать Postgres, а не SQLite-фолбэк. Сервис `postgres` в
  `docker-compose.yml` — опциональный (профиль `local-postgres`, по умолчанию не
  поднимается). В проде композ не включает локальный Postgres: backend ходит на
  внешний корпоративный инстанс по `DATABASE_URL` — трафик вне доверенной
  compose-сети, см. `SECURITY.md` §3.
- **Qdrant**: прод-инстанс с персистентным томом. Версии клиента и сервера
  согласовывать (процедура апгрейда — в `README.md`). Сервис `qdrant` в
  `docker-compose.yml` — опциональный (профиль `local-qdrant`, по умолчанию не
  поднимается). В проде композ не включает локальный Qdrant: backend ходит на
  внешний корпоративный инстанс по `QDRANT_URL` (HTTPS) и, при необходимости,
  `QDRANT_API_KEY` — трафик вне доверенной compose-сети, см. `SECURITY.md` §3.
- **LLM / эмбеддинги**: прод-эндпоинты; `LLM_API_KEY` / `EMBEDDING_API_KEY` — из
  секрет-хранилища.
- **HTTPS и reverse-proxy** (обязательно): TLS терминируется на прокси
  (nginx/Ingress/ALB). Сессия работает через same-origin — проксируйте `/api/*` и
  `/health` на бэкенд с одного домена, что и фронтенд.
  CORS в `main.py` задан как `allow_origins=settings.cors_allowed_origins`
  (дефолт — пустой список, `CORS_ALLOWED_ORIGINS` в env). Пустой список — намеренно:
  браузер не обращается к бэкенду напрямую (Next.js rewrites проксируют `/api`
  серверно, same-origin), поэтому cross-origin CORS бэкенду не нужен, а wildcard
  `*` был бы чистой дырой. Если когда-то появится cross-origin развёртывание
  (фронтенд на домене A, бэкенд на домене B без прокси) — оно **несовместимо** с
  текущей схемой signed-cookie сессий: браузер не отправит cookie на другой origin.
- **Публикация портов**: в `docker-compose.yml` наружу публикуется **только
  frontend** (`8080:3000`). `backend` (`8000`), `qdrant` (`6333`/`6334`) и
  `postgres` (`5432`) host-портов не публикуют — они доступны только внутри
  compose-сети (qdrant/postgres при этом вообще опциональны: профили `local-qdrant`
  и `local-postgres`). Единственный путь к данным
  из сети — через frontend (который сам за reverse-proxy). Не возвращайте `ports`
  для backend/qdrant/postgres обратно: это открывает корпус документов напрямую,
  минуя аутентификацию frontend-слоя.

### 3.1 Грабли: `BACKEND_URL` и Next.js rewrites

`frontend/next.config.js` запекает адрес бэкенда в rewrites **на этапе сборки**
(`process.env.BACKEND_URL` читается при `next build`). Значение, переданное только
в runtime-контейнер, на rewrites не влияет (клиентские запросы `/api/*` уйдут на
baked-адрес). Варианты:

- **Рекомендуемый (прод)**: reverse-proxy сам маршрутизирует `/api/*` и `/health`
  на бэкенд — тогда rewrites Next.js для этих путей не задействуются.
- Если без прокси: передавать `BACKEND_URL` как **build-arg** при сборке образа
  фронтенда, а не только в `environment:` контейнера.

Серверные компоненты (`frontend/src/lib/backendFetch.js`) читают `BACKEND_URL` в
runtime — на них это ограничение не распространяется.

### 3.2 Грабли: cookie и reverse-proxy (байт-в-байт)

Сессия — signed-cookie Starlette: в значении есть base64 (`+`, `/`, `=`), и любое
перекодирование ломает подпись → `401`. Один такой баг уже найден и исправлен:
Next.js 16 URL-кодирует значения cookie при `cookieStore.toString()`, поэтому
`backendFetch.js` сериализует cookie вручную через `serializeCookies.mjs`
(`frontend/src/lib/backendFetch.js`, `frontend/src/lib/serializeCookies.mjs`).

При деплое за reverse-proxy проверяйте, что proxy не модифицирует заголовок `Cookie`
(например, перекодирование, нормализация регистра, обрезание по длине, добавление
`HttpOnly`/`Secure` со сменой значения). Симптом несовпадения: вход проходит, но
запросы к `/api/*` возвращают `401`. Тот же класс бага может повториться на уровне
прокси — диагностировать по «cookie доходит до бэкенда байт-в-байт».

---

## 4. Keycloak / IDB (настройки на стороне IDB)

Код не покрывает эти настройки — они делаются в realm-конфиге Keycloak (IDB).
Если прод-Keycloak разворачивается **не из того же экспорта**, что dev, их надо
внести заново (подробно: `SSO_TESTING_GUIDE.md`, разделы 11, 11.1, 12).

- **Redirect URI** клиента: `https://<host>/api/auth/callback` (под `SSO_REDIRECT_URI`).
- **Valid post logout redirect URIs**: `https://<host>/` — обязателен для
  RP-Initiated Logout. Без него «Выйти» в UI завершится ошибкой
  `invalid_redirect_uri`.
- **Client authentication** включён, корректный `client_secret` (совпадает с
  `KEYCLOAK_CLIENT_SECRET`).
- **Group-claim mapper**: `group` (или настроенный через
  `KEYCLOAK_FIELD_MAPPING`/`KEYCLOAK_GROUP_PATH_MODE`).
- Realm/группы/роли перенесены из dev-экспорта или созданы на IDB.

---

## 5. Проверка fail-fast (до и после деплоя)

Бэкенд падает на старте, если в `ENVIRONMENT=production`:
- `AUTH_PROVIDER` равен `disabled` или `simulation` (оба дают доступ без внешней
  проверки; `simulation` выдаёт демо-админа через `/auth/simulate`);
- `APP_SECRET_KEY` пуст / равен `dev-secret-change-me` / короче 32 символов;
- `AUTH_SESSION_HTTPS_ONLY=false`.

```powershell
Set-Location backend                 # чтобы импортировался пакет app
$py = ".venv\Scripts\python.exe"

# Должно УПАСТЬ: дефолтный секрет в production
$env:ENVIRONMENT="production"; $env:APP_SECRET_KEY="dev-secret-change-me"
& $py -c "from app.config import Settings; Settings()"

# Должно УПАСТЬ: AUTH_PROVIDER=disabled запрещён в production
$env:ENVIRONMENT="production"; $env:APP_SECRET_KEY=(& $py -c "import secrets; print(secrets.token_urlsafe(32))")
$env:AUTH_SESSION_HTTPS_ONLY="true"; $env:AUTH_PROVIDER="disabled"
& $py -c "from app.config import Settings; Settings()"

# Должно ПОДНЯТЬСЯ: keycloak_oidc + валидный секрет + HTTPS-only
$env:AUTH_PROVIDER="keycloak_oidc"
& $py -c "from app.config import Settings; print('OK')"
```

Проверять именно в прод-контуре: fail-fast срабатывает только при реально
выставленном `ENVIRONMENT=production`.

## 6. Пост-деплой smoke

1. `GET /health` — 200.
2. Открыть UI → логин-гейт → «Войти через корпоративный вход» → Keycloak → возврат
   в приложение с ролью.
3. «Выйти» → Keycloak logout → возврат на `https://<host>/` (проверяет
   post-logout URI).
4. Cookie `session` в браузере — `Secure` + `SameSite=Lax`.
5. Роль соответствует прод-группам IDB (`/api/auth/me`).
6. **Fail-closed** (негативный сценарий): пользователь без группы из
   `AUTH_ROLE_GROUPS` (или вообще без групп в IDB) не получает доступа — `/api/*`
   отдаёт `403`, а не дефолтную роль. Проверяется только при `AUTH_DEFAULT_ROLE`
   пустом; если в проде задана дефолтная роль — это осознанное решение, зафиксируйте
   его, иначе пункт не выполним.

---

## 7. Предзаполнение БД (справочники и дедупликация)

После `alembic upgrade head` в проде обязательны скрипты предзаполнения. Они
**не входят в миграции** — значения привязаны к конкретной инсталляции/клиенту,
поэтому применяются явно при деплое, а не как дефолт схемы.

### 7.1 Миграция схемы

```bash
cd backend
alembic upgrade head
```

Создаёт таблицы/колонки Этапа 4: `developments`, `attribute_values`,
`document_lsh_buckets`, колонки `documents.*` (`development_*`, `file_hash`,
`content_hash`, `minhash`, `has_duplicates`), индекс `documents.development_id`.
Приложение на старте тоже выполняет `create_all` (идемпотентно), но
версионированную схему ведёт Alembic — в проде применяйте миграции явно.

### 7.2 Модули разработок (`attribute_values`, ключ `module`) — клиент-специфично

```bash
python scripts/seed_attribute_values.py
```

Сидирует значения **`PY,PT,OM,PA` в организацию `SAP HCM`** (дефолт). Это дефолт
**текущего заказчика** — при развёртывании под другого клиента/направление его
нужно пересмотреть и зафиксировать в конфиге деплоя:

```bash
python scripts/seed_attribute_values.py --org "<организация клиента>" --values "PY,PT,OM,PA,ЛК"
```

- Значения `module` — это **данные** (`attribute_values`, привязаны к `org_id`),
  а не CHECK/enum в схеме: смена набора модулей не требует миграции.
- Без сида автокомплит модуля и создание разработки с `module` вернут **422**
  (модуль вне справочника).
- Скрипт идемпотентен (повторный запуск не дублирует). Запускать из НЕ-elevated
  shell (см. ограничение PostgreSQL в `AGENTS.md`).

### 7.3 Отпечатки дедупликации (только при переносе данных до Этапа 4)

```bash
python scripts/backfill_dedup.py
```

Досчитывает `file_hash`/`content_hash`/`minhash`/LSH-бакеты для документов,
загруженных до миграции Этапа 4. Без этого дедупликация (уровни 1–3) по
существующему корпусу не срабатывает — у старых документов отпечатки `NULL`.
На свежей (пустой) БД не нужен. Идемпотентен; `--doc-id <id>` — точечная проверка.

### 7.4 Автоопределение номера разработки — клиент-специфичный regex

`DEV_FILENAME_PATTERN` (дефолт `(?<!\d)(\d{4,6})(?!\d)`) задаёт, как из имени файла
извлекается номер разработки. Дефолт — 4–6-значный код текущего заказчика; при
другой схеме нумерации (другая длина, буквенно-цифровые коды) задайте свой regex.
Автоопределение отключается флагами `DEV_DETECTION_ENABLED` / `DEV_LLM_TITLE_PAGE_ENABLED`.
