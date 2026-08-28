# Тестирование с включённым SSO (локальный Keycloak)

Инструкция описывает, как поднять стек с SSO и проверить авторизацию
сквозным сценарием: экран входа → Keycloak → сессия → доступ к API/UI → роли.

---

## 1. Что проверено этим стендом

| Слой | Что работает |
|---|---|
| Backend (FastAPI `:8000`) | `auth_provider=keycloak_oidc`, защита `/api/documents\|search\|chat\|tags\|settings` через `Depends(require_user)`, сессия в signed-cookie |
| Frontend (Next.js `:3000`) | кнопка «Войти», экран «Вход в систему» для анонима, шапка с логином и ролью |
| Keycloak (Docker `:8081`) | realm `myrealm`, confidential client `my-app`, группы→роли, group mapper |
| Роли (4 из Этапа 1) | `viewer / editor / admin / security` из групп `KB_*` через `AUTH_ROLE_GROUPS` |

Тесты ролей и SSO-потока: `backend/tests/test_auth.py`, `backend/tests/test_auth_sso.py`.

---

## 2. Предпосылки

- Docker запущен (Keycloak живёт в контейнере).
- `.env` в корне репозитория уже настроен (см. ниже), менять ничего не нужно.

```ini
# --- Auth ---
AUTH_PROVIDER=keycloak_oidc
APP_SECRET_KEY=dev-secret-change-me
AUTH_SESSION_TTL_SECONDS=28800
AUTH_SESSION_HTTPS_ONLY=false
AUTH_ROLE_GROUPS={"KB_Viewer":"viewer","KB_Editor":"editor","KB_Admin":"admin","KB_Security":"security"}
AUTH_DEFAULT_ROLE=viewer
KEYCLOAK_URL=http://localhost:8081
KEYCLOAK_REALM=myrealm
KEYCLOAK_CLIENT_ID=my-app
KEYCLOAK_CLIENT_SECRET=RypekEVHCWFCEhWlsT0EYNpTtpM8o4hb
SSO_REDIRECT_URI=http://localhost:3000/api/auth/callback
```

> **Порт 8081, а не 8080** — 8080 на этой машине занят `3proxy`. Внутри контейнера Keycloak слушает 8080, наружу проброшен как 8081→8080 (`-p 8081:8080`).

---

## 3. Запуск стека

Поднять все сервисы (Qdrant, Ollama, backend, frontend):

```powershell
.\scripts\start-all.ps1
```

Поднять/проверить Keycloak (идемпотентен, сам убьёт старый контейнер):

```powershell
.\scripts\start-keycloak.ps1
```

Готовность стенда:

```powershell
Invoke-WebRequest http://localhost:3000                 # UI
Invoke-WebRequest http://localhost:8081/realms/myrealm/.well-known/openid-configuration
```

---

## 4. Тестовые пользователи

Пароль у всех: `demo`

| Логин | Группа в Keycloak | Роль | Чем отличается |
|---|---|---|---|
| `demo.user` | `KB_Viewer` | viewer | базовый просмотр |
| `demo.editor` | `KB_Editor` | editor | редактор |
| `demo.admin` | `KB_Admin` | admin | администратор |
| `demo.security` | `KB_Security` | security | роль ИБ (только чтение логов) |

> Группы в Keycloak уже созданы, users привязаны к группам, group mapper на claim `group` включён.

---

## 5. Сквозной сценарий в браузере (главный тест)

1. Откройте `http://localhost:3000` (лучше в инкогнито/без куки).
   ⇒ Шапка: `SSO · Войти через корпоративный вход`, в контенте карточка «Вход в систему».
2. Нажмите «Войти через корпоративный вход».
   ⇒ Браузер уходит на `http://localhost:8081/realms/myrealm/protocol/openid-connect/auth?...&redirect_uri=http%3A%2F%2Flocalhost%3A3000%2Fapi%2Fauth%2Fcallback` — **redirect_uri обязан быть на :3000** (это проверка SSO proxy fix).
3. Введите `demo.user` / `demo` → Sign In.
4. ⇒ Вернётесь на `:3000`, в шапке `demo.user · Viewer`, документы загрузились.
5. Нажмите «Выйти» ⇒ снова карточка входа.

Аналогично проверьте всех 4 юзеров — роль в шапке должна меняться.

---

## 6. Проверка API вручную (PowerShell / curl)

Бэкенд: `http://localhost:8000`. Frontend-прокси: `http://localhost:3000/api/*`.

### 6.1 Анонимные запросы → 401

```powershell
try { Invoke-WebRequest http://localhost:8000/api/documents -UseBasicParsing -TimeoutSec 5 } catch { $_.Exception.Response.StatusCode.value__ }   # 401
try { Invoke-WebRequest http://localhost:8000/api/search -Method Post -Body '{"query":"x"}' -ContentType 'application/json' -UseBasicParsing } catch { $_.Exception.Response.StatusCode.value__ }   # 401
```

Здоровье и auth открыты (не защищены):

```powershell
Invoke-WebRequest http://localhost:8000/api/auth/me -UseBasicParsing
# {"mode":"sso","user":{"user_id":"anonymous",...},"sim_users":null}
```

### 6.2 Старт оказался 400 «sso недоступен»?

Обычно значит, что на конфиг влияет переменная окружения `AUTH_MODE` (имеет приоритет над `.env`).
Убедитесь, что в текущей PowerShell-сессии она не висит:

```powershell
Remove-Item Env:AUTH_PROVIDER -ErrorAction SilentlyContinue
```

---

## 7. Проверка кода-флоу целиком (без браузера)

Кратко показать, что `authorize_redirect` отдаёт правильный `redirect_uri`:

```bash
curl -i "http://localhost:8000/api/auth/login" | grep -i location
# Location: http://localhost:8081/.../auth?response_type=code&client_id=my-app&redirect_uri=http%3A%2F%2Flocalhost%3A3000%2Fapi%2Fauth%2Fcallback&...
```

Полный OIDC (код-обмен) скриптом удобнее всего сделать тестами: `tests/test_auth_sso.py` уже гоняет
login/callback с mock-клиентом и проверяет, что `redirect_uri` совпадает.

---

## 8. Режим fail-closed (нет роли → 403)

Подтверждается настройкой `AUTH_DEFAULT_ROLE=` (пусто) в `.env`. Тогда юзер, не попавший ни в одну
группу маппинга, получает `403` вместо мягкого `viewer`.

```bash
AUTH_DEFAULT_ROLE=   # в .env
# рестарт бэкенда, вход юзером без группы → 403
```

Покрыто юнит-тестами: `test_auth.py::test_fail_closed_403`, `test_auth_sso.py::test_sso_fail_closed_403`.

---

## 9. Запуск автотестов

```powershell
cd backend
.venv\Scripts\python.exe -m pytest -q        # все (254)
.venv\Scripts\python.exe -m pytest tests/test_auth.py tests/test_auth_sso.py -q   # только авторизация
```

> Тесты изолированы от `.env`: `tests/conftest.py` сбрасывает auth-переменные окружения,
> а фикстуры задают `auth_provider="disabled"` явно.

---

## 10. Частые проблемы и диагностика

| Симптом | Причина / решение |
|---|---|
| Бэкенд не стартует (`ModuleNotFoundError: itsdangerous`) | Запускается системный `python` вместо проекта. `start-all.ps1` должен использовать `backend\.venv\Scripts\python.exe` |
| `/health` на `:3000` → 404 | Next.js проксирует только `/api/*`; `/health` добавлен отдельным rewrite в `next.config.js` |
| После логина редиректит на `:8000` вместо `:3000` | Не задан `SSO_REDIRECT_URI`; контролируйте, чтобы authorize-URL содержал `redirect_uri=localhost:3000` |
| Keycloak не поднялся за 120с | Образ не скачан и/или стартует холодный `start-dev`; проверьте `docker logs -f okf-keycloak` |
| Ошибка `invalid_client_credentials` при ручной выдаче токена | Используйте форма `client_id=my-app&client_secret=…&grant_type=password` (form-urlencoded) |
| Консоль браузера: `ApiError: Требуется авторизация` | Это нормально для анонимного посещения; UI теперь показывает экран входа вместо падения списка |

---

## 11. Как добавить/изменить пользователя в Keycloak (админка)

Консоль: `http://localhost:8081/` , user `admin` / пароль `admin`, realm **myrealm**.

- **Юзер**: Users → Add user (username/email), потом Credentials → Set password (нулевое число «temporary»), затем Groups → группа.
- **Группы**: Groups → Create group (`KB_Viewer`, `KB_Editor`, …).
- **Клиент**: Clients → `my-app` → Settings (redirect URI `http://localhost:3000/api/auth/callback`, Client authentication on), Credentials (client secret), Client scopes → `group` mapper.
- Смена маппинга групп прода → только в `.env`: `AUTH_ROLE_GROUPS={"corp_group_admins":"admin", ...}`.

---

## 12. Интеграция с реальным IDP + IDB (корпоративная схема)

Целевая архитектура — двухуровневая: **IDP Keycloak** (федерируется с AD по LDAP+Kerberos)
→ **IDB Keycloak (broker)** (забирает identity у IDP по OIDC) → **наше приложение** по OIDC.

Приложение **не знает** ни про AD, ни про IDP: оно разговаривает только с **IDB**, поэтому
настройка сводится к тому, чтобы указать IDB и корректно прочитать group-claim.

### 12.1 Направьте приложение на IDB

```bash
AUTH_PROVIDER=keycloak_oidc
KEYCLOAK_URL=https://idb.corp.local          # IDB (broker), НЕ IDP и НЕ AD
KEYCLOAK_REALM=<realm на IDB>
KEYCLOAK_CLIENT_ID=<клиент, зарегистрированный на IDB>
KEYCLOAK_CLIENT_SECRET=<client secret IDB>
SSO_REDIRECT_URI=https://<наш-host>/api/auth/callback
```

### 12.2 Выясните, как IDB отдаёт группы

Запросите userinfo и посмотрите, в каком claim лежат группы:

```bash
curl -s -H "Authorization: Bearer $TOKEN" https://idb.corp.local/realms/<realm>/protocol/openid-connect/userinfo
```

Варианты и как их настроить:

| Что видите в userinfo | Настройка |
|---|---|
| `"group": ["KB_Viewer", ...]` | ничего — это стандарт |
| `"roles": ["KB_Viewer", ...]` | `KEYCLOAK_FIELD_MAPPING={"groups":"roles"}` |
| `"groups": ["..."]` или иное имя | `KEYCLOAK_FIELD_MAPPING={"groups":"<имя claim>"}` |

`KEYCLOAK_FIELD_MAPPING` — частичный override: незаданные поля (`sub`/`preferred_username`/
`email`) остаются стандартными OIDC-именами.

### 12.3 Заполните маппинг ролей под группы IDB

```bash
AUTH_ROLE_GROUPS={"KB_Viewer":"viewer","KB_Editor":"editor","KB_Admin":"admin","KB_Security":"security"}
AUTH_DEFAULT_ROLE=            # пусто = fail-closed (403), для контура ИБ
```

### 12.4 Форма имён групп: `leaf` vs `full_path`

По умолчанию `KEYCLOAK_GROUP_PATH_MODE=leaf` — от group-claim берётся текст после последнего
`/` (`/IDB/KB_Viewer` → `KB_Viewer`). Ключи в `AUTH_ROLE_GROUPS` короткие.

**Симптом перехода на `full_path`**: после интеграции group-claim приходит вида
`/realm/subgroup/RoleName`, и несколько разных ролей схлопываются в один leaf (ложные
совпадения / коллизии имён). Тогда ставьте:

```bash
KEYCLOAK_GROUP_PATH_MODE=full_path
AUTH_ROLE_GROUPS={"/IDB/KB_Admin":"admin", ...}   # ключи уже с полным путём
```

### 12.5 Автологин по Kerberos/SPNEGO — не трогает приложение

Бесшовный вход без пароля (браузер отдаёт Kerberos-ticket → IDP логинит сам) целиком
настраивается на стороне IDP (SPNEGO/Kerberos), приложение продолжает работать по
стандартному Authorization Code Flow и ничего менять не должно.

Покрыто тестами: `backend/tests/test_auth_groups.py` (нормализация групп, partial override
`field_mapping`, `leaf`/`full_path`, end-to-end full-path).