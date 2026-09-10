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
| 8а | **Ровно одна реплика `backend`** (`deploy.replicas: 1`, без `--scale backend=N`) — см. §3.3 | ✅ блокер |
| 9 | Проверить fail-fast: бэкенд не стартует с дефолтным секретом / без HTTPS / с `disabled|simulation` | ✅ |
| 10 | `alembic upgrade head` + сид модулей (`seed_attribute_values.py`, с учётом модулей клиента) | ✅ |
| 11 | `backfill_dedup.py` при переносе документов, загруженных до Этапа 4 | при миграции данных |
| 12 | `rebuild_sparse.py` один раз после первого наполнения инсталляции реальными данными (миграция BM25-векторов, см. §7.5) | при наполнении данными |
| 13 | (опционально) `backfill_translations.py --locale <код>` — автоперевод справочников для целевого языка (требует `TRANSLATION_PROVIDER=llm`, см. §7.6) | при наличии нескольких языков |

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
| `STOPWORDS_CACHE_TTL_SECONDS` | TTL кэша динамических стоп-слов (сек); по умолчанию `60`. Действуют только на сторону запроса — индексная формула заморожена, реиндекс не нужен |
| `STOPWORDS_MAX_WORDS` | Максимум слов в одном наборе стоп-слов (защита от случайной гигантской вставки); по умолчанию `10000` |
| `TRANSLATION_PROVIDER` | Провайдер автоперевода справочников (теги/разработки/модули): `llm` (через LLM-шлюз) или `off` (прод без интернета — только ручной ввод). По умолчанию `llm` |
| `TRANSLATION_MODEL` | Модель перевода; пусто → `LLM_MODEL` |
| `TRANSLATION_BATCH_SIZE` | Размер пакета текстов на один LLM-вызов бэкфилла; по умолчанию `50` |

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
  `*` был бы чистой дырой.
  Cross-origin развёртывание (фронтенд на домене A, бэкенд на домене B без
  прокси) поддержано, но включается целиком: непустой `CORS_ALLOWED_ORIGINS`
  переводит CORS в режим `allow_credentials=true`, а сессионную cookie — в
  `SameSite=None`, потому что `Lax`-cookie браузер на кросс-сайтовый запрос не
  отправляет вовсе. `SameSite=None` требует `Secure`, поэтому такая
  конфигурация обязана идти с `AUTH_SESSION_HTTPS_ONLY=true`, а `*` в списке
  запрещён (спецификация CORS не допускает его вместе с credentials). Все три
  условия проверяются на старте (`Settings.validate_cors`) — неполная
  конфигурация роняет процесс, а не молча ломает вход в браузере.
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

### 3.3 Одна реплика бэкенда (масштабировать горизонтально нельзя)

`backend` рассчитан ровно на **один процесс**. Это не «пока не проверяли»: часть
состояния живёт в памяти процесса, и вторая реплика не просто не увидит его —
она активно поломает работу первой.

Dockerfile передаёт `UVICORN_WORKERS` в Uvicorn (по умолчанию `1`). В production
не задавайте значение выше `1`: приложение завершит старт с ошибкой single-replica.

Что именно ломается при `--scale backend=2` (или `deploy.replicas: 2`):

| Компонент | Что в памяти | Что будет при двух репликах |
|---|---|---|
| `services/job_queue.py` | `queue.Queue` + единственный worker-поток | Задача видна обеим репликам в БД, но лежит в очереди только одной. Хуже: `recover_after_restart()` на старте помечает **все** `running`-задачи как `failed` — поднявшаяся вторая реплика убьёт задачу, которую прямо сейчас выполняет первая, и вернёт её `queued`-соседей себе в очередь |
| `services/registry.py` | — | `reset_stale_statuses()` на старте переводит все `processing`-документы в `paused` («Сервер был перезапущен») — вторая реплика оборвёт живую обработку первой |
| `services/rate_limiter.py` | `deque` окон на пользователя | Лимит множится на число реплик: 2 реплики — вдвое больше массовых перегенераций в час, чем настроено |
| `services/pipeline.py` | bounded executor, `_abort_events` | `PIPELINE_MAX_WORKERS` и `PIPELINE_MAX_PENDING` действуют на один процесс. Прерывание (удаление, purge) доходит только до той реплики, в которой идёт обработка; общий queue нужен до горизонтального масштабирования |
| `services/trash.py`, `services/chat_history.py` | потоки `trash-purge`, `chat-history-purge` | Оба цикла поднимутся в каждой реплике и пойдут удалять одно и то же параллельно |
| `services/stopwords.py`, `services/ui_dictionary.py`, `api/chat.py`, `api/search.py` | `lru_cache` словарей и клиентов | Инвалидация (`invalidate()` после правки стоп-слов/словаря) действует только внутри своей реплики: пользователь получает то старые, то новые данные в зависимости от того, куда попал запрос |

Практически:

```yaml
# docker-compose.yml / values.yaml — НЕ увеличивать
backend:
  deploy:
    replicas: 1
```

Вертикально масштабировать можно: обработка документов и так идёт в потоках
внутри процесса, а `uvicorn` держит несколько соединений. Чего нельзя — это
`--workers N` у uvicorn/gunicorn: каждый worker — отдельный процесс с теми же
последствиями, что и вторая реплика.

Для admission обычной обработки документов используются безопасные значения
`PIPELINE_MAX_WORKERS=2` и `PIPELINE_MAX_PENDING=8`. При заполнении capacity API
возвращает `503 queue_overloaded`; это ожидаемая защита от исчерпания памяти, а не
ошибка Qdrant/LLM. Увеличение значений требует нагрузочного теста и записи в
`SECURITY.md`.

Если однопроцессности перестанет хватать, переносить в общий стор придётся
всё из таблицы разом: очередь и rate limit — в Redis (или в БД с
`SELECT … FOR UPDATE SKIP LOCKED`), purge-циклы — в лидер-выборы или отдельный
cron-сервис, инвалидацию кэшей — в pub/sub. Половинчатый перенос (например,
только очередь) оставит остальные строки таблицы в силе.

Явный триггер пересмотра: Redis и общий orchestration становятся обязательными
при первом же увеличении `uvicorn --workers`, добавлении второй backend-реплики
или горизонтальном масштабировании из-за нагрузки. До этого момента single-replica
является намеренным архитектурным режимом.

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

## 7. Предзаполнение БД (справочники, дедупликация, многоязычность)

После `alembic upgrade head` в проде обязательны скрипты предзаполнения. Они
**не входят в миграции** — значения привязаны к конкретной инсталляции/клиенту,
поэтому применяются явно при деплое, а не как дефолт схемы.

### 7.1 Миграция схемы

При развёртывании через compose миграция — отдельный одноразовый сервис
`migrate`; он ждёт готовности Postgres, а `backend` стартует только после его
успешного завершения (`service_completed_successfully`), поэтому отдельного
шага не требуется:

```bash
docker compose up -d
```

Применить миграции вручную (повторно, либо к внешней БД) — тем же образом:

```bash
docker compose run --rm migrate
```

Без compose (Python на хосте):

```bash
cd backend
alembic upgrade head
```

Создаёт таблицы/колонки Этапа 4: `developments`, `attribute_values`,
`document_lsh_buckets`, колонки `documents.*` (`development_*`, `file_hash`,
`content_hash`, `minhash`, `has_duplicates`), индекс `documents.development_id`.
Также таблицы Этапа 7 (многоязычность): `locales`, `stopwords` (стоп-слова),
`tags` (surrogate `tag_id`), `tag_translations`, `developments` (колонки
`name`/`module`), `development_translations`, `attribute_values` (колонка
`label`), `attribute_value_translations`, `ui_dictionaries` (UI-словари),
`documents` (колонки `source_locale`, `source_locale_source`).
Приложение на старте тоже выполняет `create_all` (идемпотентно), но
версионированную схему ведёт Alembic — в проде применяйте миграции явно.

### 7.2 Модули разработок (`attribute_values`, ключ `module`) — клиент-специфично

Автоматически не выполняется (значения зависят от клиента), запускается явно.
В образе бэкенда есть и `scripts/`, и `alembic/`, поэтому команда одна и та же
внутри контейнера и на хосте:

```bash
docker compose run --rm migrate python scripts/seed_attribute_values.py
```

Без compose (Python на хосте, из `backend/`):

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

### 7.5 Миграция sparse-векторов (BM25) — rebuild_sparse.py после первого наполнения

До 2026-09-01 `backfill_sparse` при каждом рестарте пересчитывал sparse-векторы
концепт-точек из ПОЛНОГО текста .md-бандлов — frontmatter (теги, `source_document`,
имена файлов) попадал в BM25-индекс, а каждый рестарт перезаписывал правильные
векторы испорченными. Фикс 2.1 код-ревью унифицировал формулу текста
(`vector_store._sparse_text`: title + content, единая для индексации и backfill)
и сделал backfill идемпотентным — но он **не исправляет уже испорченные векторы**
в существующих данных. Симптом, если шаг забыли: деградация качества поиска
(находится по мусорным словам из тегов/имён файлов, BM25-хиты не по делу).

Одноразовая миграция (требует поднятого Qdrant; бэкенд может работать):

```bash
python scripts/rebuild_sparse.py
```

- Пересчитывает sparse всех концепт-точек, найденных в `data/okf_bundles`, по
  канонической формуле (dense-векторы и payload не затрагиваются).
- Идемпотентен; на данных, проиндексированных кодом 2026-09-01+, — no-op
  (векторы уже канонические), поэтому безопасно прогонять «на всякий случай»
  после первого наполнения прод-инсталляции или переноса бандлов из старой —
  этим и закрывается риск «данные занесли до фикса, а шаг забыли задним числом».
- Самопроверка после прогона:

  ```python
  from app.services.vector_store import VectorStore
  assert VectorStore().backfill_sparse() == 0  # все точки скипаются — векторы на месте
  ```

### 7.6 Перевод справочников (опционально, при наличии целевого языка)

```bash
python scripts/backfill_translations.py --locale en
python scripts/backfill_translations.py --locale en --entities tags,developments
```

Автоперевод тегов/названий разработок/модулей через LLM-шлюз (`TRANSLATION_PROVIDER=llm`).
Скрипт идемпотентен: ручные переводы (отмеченные админом как reviewed) не перезаписываются.
Требует поднятого бэкенда (LLM-шлюз) и `TRANSLATION_PROVIDER`, отличного от `off`.
Для прод без интернета — только ручной ввод переводов через Admin UI («Поддержка языков»).
Подробности: `docs/ADD_LANGUAGE.md`.
