# Развёртывание в production

Контрольный список шагов, обязательных перед публикацией сервиса в прод.
Локальная разработка описана в `README.md` («Быстрый старт»); интеграция с
Keycloak/IDB — в `SSO_TESTING_GUIDE.md` (разделы 11–12). Здесь — только то, что
отличает прод от локального стенда, и то, что нельзя пропустить.

## 0. Чек-лист

| # | Шаг | Обязательно |
|---|---|---|
| 1 | Сгенерировать `APP_SECRET_KEY` (не дефолт, ≥ 32 симв.) и положить только в секрет-хранилище | ✅ блокер |
| 2 | `ENVIRONMENT=production` в реальном прод-окружении | ✅ блокер |
| 3 | `AUTH_SESSION_HTTPS_ONLY=true` | ✅ блокер |
| 4 | Секреты `KEYCLOAK_CLIENT_SECRET`, БД, LLM/embedding — вне git и README | ✅ блокер |
| 5 | HTTPS/TLS терминируется на reverse-proxy, cookie ходит только по HTTPS | ✅ блокер |
| 6 | Зарегистрировать redirect/post-logout URIs на **прод**-Keycloak (IDB) | ✅ блокер |
| 7 | БД PostgreSQL, Qdrant, LLM/embeddings — прод-эндпоинты | ✅ |
| 8 | Проверить fail-fast: бэкенд не стартует с дефолтным секретом / без HTTPS | ✅ |

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
| `DATABASE_URL` | `postgresql+psycopg://user:pass@host:5432/okf_knowledge` |
| `QDRANT_URL` | прод-эндпоинт Qdrant |
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
  В проде использовать Postgres, а не SQLite-фолбэк.
- **Qdrant**: прод-инстанс с персистентным томом. Версии клиента и сервера
  согласовывать (процедура апгрейда — в `README.md`).
- **LLM / эмбеддинги**: прод-эндпоинты; `LLM_API_KEY` / `EMBEDDING_API_KEY` — из
  секрет-хранилища.
- **HTTPS и reverse-proxy** (обязательно): TLS терминируется на прокси
  (nginx/Ingress/ALB). Сессия работает через same-origin — проксируйте `/api/*` и
  `/health` на бэкенд с одного домена, что и фронтенд.
  В `main.py` CORS задан как `allow_origins=["*"]` **без** `allow_credentials`
  (`backend/app/main.py:124-129`). Это работает только потому, что деплой same-origin:
  браузер ходит на один origin (прокси пересылает на бэкенд), и CORS для таких
  запросов вообще не задействуется. Cross-origin развёртывание (фронтенд на домене A,
  бэкенд на домене B без прокси) **не заработает вовсе**: браузер не отправит
  сессионную cookie на другой origin, а `*` + credentials запрещены спецификацией
  CORS. Это не «не рекомендуется», а несовместимо с текущей схемой сессий.

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
- `APP_SECRET_KEY` пуст / равен `dev-secret-change-me` / короче 32 символов;
- `AUTH_SESSION_HTTPS_ONLY=false`.

```powershell
Set-Location backend                 # чтобы импортировался пакет app
$py = ".venv\Scripts\python.exe"

# Должно УПАСТЬ: дефолтный секрет в production
$env:ENVIRONMENT="production"; $env:APP_SECRET_KEY="dev-secret-change-me"
& $py -c "from app.config import Settings; Settings()"

# Должно ПОДНЯТЬСЯ: валидный секрет + HTTPS-only
$env:APP_SECRET_KEY=(& $py -c "import secrets; print(secrets.token_urlsafe(32))")
$env:AUTH_SESSION_HTTPS_ONLY="true"
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
