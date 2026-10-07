# OIDC Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Исполнение самим агентом; делегирование и коммит только по отдельному указанию пользователя.

**Goal:** сохранить confidential Keycloak login и добавить public/S256,
явный token authentication и authoritative UserInfo без раскрытия login secrets
в cookie, логах или окруженческой документации.

**Architecture:** существующие provider/authorizer/Next proxy сохраняются.
Login state хранится в реляционной БД и атомарно потребляется до code exchange;
временный Request передаёт серверный payload стандартному Authlib. Application
session по-прежнему серверная; OIDC и аудит имеют отдельные планы.

**Tech Stack:** Authlib **1.7.2**, SQLAlchemy 2, Alembic, PostgreSQL 17 / SQLite,
FastAPI/Starlette, существующие httpx/joserfc, Helm config schema 2,
Gateway API/kgateway и synthetic Keycloak home lab.

**Spec:** [Уточнённый OIDC-контракт](../specs/2026-10-04-oidc-compatibility-design.md).

**Статус 04.10.2026:** реализация находится в `codex/kubernetes-rollout`
поверх `f551f5f`, не интегрирована в main и не закоммичена. Tasks 0–5 исполнены;
локальная OIDC приёмка Task 6 и её ограничения приведены в
[отчёте](../reports/2026-10-04-oidc-compatibility-acceptance.md).
Checkbox означает выполненное действие, а не разрешение production rollout.
`check-project` остаётся failed (npm audit: 5 high). Полный локальный collected
backend suite выполнен partitions: 3309 PASS / 27 SKIP; 11 PG skips отдельно
закрыты disposable сервером. Монолитный CI/coverage и platform fault cases
не подтверждены. Доступ/ресурсы домашней VM, целевая регистрация,
публикация и внешние CI/security gates не подтверждены.

## Global Constraints

- Общая анонимная поставка; реальные регистрации, Secrets, hosts/groups и
  реквизиты остаются в защищённой окруженческой копии.
- `KEYCLOAK_CLIENT_TYPE=confidential`, token auth `client_secret_basic`,
  PKCE `none`, UserInfo `auto` — defaults; public требует явных `none` + `S256`
  и отсутствия client secret. Confidential допускает basic/post и none/S256 PKCE.
- `KEYCLOAK_EXPECTED_ISSUER` по умолчанию — точный URL существующего realm;
  `OIDC_LOGIN_STATE_TTL_SECONDS=600`, диапазон 1–3600 секунд.
- Production: HTTPS, Secure/HttpOnly cookies, действующий CSRF и fail-closed
  роли. Callback остаётся GET на `/api/auth/callback`; form_post не добавляется.
- Discovery `none` исключается до Authlib; none-only profile отвергается до login.
  Authlib закрепляется `==1.7.2`; Redis и новые crypto зависимости не вводятся.
  Никакого decode JWT без verification, fallback PKCE или union групп.
- ID token обязателен и валидируется Authlib; provider отдельно сверяет nonce,
  даже при `nonce_supported=false`. Leeway сохраняется **120 секунд**.
- Access token/code/verifier/nonce не входят в browser session. ID token остаётся
  в существующем DB auth session; выдача id_token_hint при RP logout — узкое
  протокольное исключение (Task 5). DB/backup — приватные.
- DB consume — отдельная завершённая транзакция до сетевых запросов. Ошибка
  последующего exchange/UserInfo/session commit не восстанавливает flow.
- Pending flows очищаются при OIDC backend startup до login; после restart,
  update или restore требуется новый login flow. При обычном restart/update
  auth sessions неизменного профиля сохраняются; restore имеет отдельный gate
  от возвращения отозванных sessions (Task 6). Compact/replicas=1/workers=1 сохраняются.
- Смена issuer/client/UserInfo/mapping требует инвалидизации прежних OIDC
  sessions до открытия трафика; Secret rotation — отдельная проверка cache/flow.
- Local launchers и Compose bundled/external сохраняются. Не добавлять refresh,
  back-channel logout, auto-registration, universal IdP, trusted-IP/outbox/HA.
- Отдельный ledger `.superpowers/sdd/2026-10-04-oidc-compatibility/`, baseline
  hashes без Secrets; no commit/push/merge/publication по умолчанию.

## Review Focus

1. Cookie другого браузера / replay того же state после callback или restore:
   token endpoint не вызывается, новая auth session не создаётся. Отдельно старый
   auth cookie не оживает из backup после logout. Tasks 2/3/5/6.
2. Discovery issuer/config изменились между login и callback: отказ до exchange,
   client/cache не смешивают профили и host callback не меняет redirect URI. Tasks 1/3.
3. Непустой Authlib token.userinfo и отсутствующие/конфликтующие endpoint groups:
   endpoint profile не использует ID-token группы; subject всегда совпадает. Task 4.
4. JWT `nonce_supported=false`, missing ID token, none/bad signature, wrong
   aud/iss/exp и key rotation: проверяется настоящее Authlib, не mock callback. Task 3.
5. DB commit/logout failure и IdP body/error query: нет success-cookie/false
   logout success, нет secret/token/verifier/URL query в приложенческих логах. Task 5.
6. Public profile + оставшийся confidential Secret в envFrom, downgrade/restore
   и callback по старой cookie: fail-closed; schema/tools compatibility честная. Task 6.

## Карта файлов

| Область | Файлы и назначение |
|---|---|
| Profiles | `backend/app/config.py`, `backend/requirements.txt`: валидируемые Settings и версия Authlib |
| Flow store | Новый `backend/app/auth/oidc_flow.py`, модель в `backend/app/db/models.py`, additive Alembic migration |
| Provider | `backend/app/auth/providers/keycloak_oidc.py`: URL/exchange/verification/UserInfo, reuse Authlib |
| Errors/session | Новый `backend/app/auth/oidc_logging.py`, `backend/app/auth/api.py`, `backend/app/auth/service.py`, `backend/app/main.py`: безопасные ошибки, commit и startup reset |
| Tests | Новые `backend/tests/test_oidc_flow_store.py`, `test_platform_oidc_contract.py`, fixture `backend/tests/fixtures/oidc/idp.py`; прежние auth suites сохраняются |
| Operator | Новый `backend/scripts/invalidate_oidc_sessions.py`: явная инвалидизация при смене identity profile |
| Deployment | Общие examples/Helm tests, `home_access.py`, `home_probe.py`, CI focused list, OIDC runbook/SECURITY |

## Task 0: Проверить исходный снимок и migration/dependency contract

**Files:** ledger; `backend/requirements.txt`, существующие auth tests — read-only baseline.
**Interfaces:** фактические BASE/source hashes, Alembic head, версии библиотек и
результаты baseline для последующих задач. На момент планирования head
`040b1c2d3e4f`; host Authlib 1.7.2 / joserfc 1.7.4 / SQLAlchemy 2.0.52 /
httpx 0.28.1, SQLite 3.49.1. Версии образов ещё нужно проверить.

- [x] Сверить AGENTS/worktree/spec; записать только scope hashes и текущий head.
  Существующие dirty configuration/model-budget изменения не stage/переписывать.
- [x] Из backend: `python -m pytest tests/test_auth.py tests/test_auth_sso.py tests/test_auth_groups.py tests/test_authz.py tests/test_deployment_profile.py -q`.
  Ожидание PASS/0 skips; записать фактический результат, failures устранить/объяснить до edits.
- [x] Через Alembic ScriptDirectory проверить один head без обращения к рабочей
  БД; read-only получить Authlib/SQLAlchemy/SQLite версии выбранного backend image.
  Ни dev PostgreSQL, ни существующие lab namespaces не мигрировать.

## Task 1: Settings и identity profile

**Files:** Modify `backend/app/config.py`, `backend/requirements.txt`;
Test `backend/tests/test_deployment_profile.py`, `test_platform_oidc_contract.py`.
**Interfaces:** новые поля Settings из Global Constraints;
`profile_fingerprint(settings: Settings) -> str` в `oidc_flow.py` — HMAC-SHA256
canonical profile с APP_SECRET_KEY. Включает issuer/URL/realm/client/type/auth method/
PKCE/UserInfo/field mapping/path mode/separator, оба настроенных redirect URI
и secret digest; raw values не логируются. Создать module skeleton здесь,
store/SQLAlchemy часть добавить в Task 2: Task 1 не импортирует будущую модель.

- [x] RED Settings matrix: omitted fields сохраняют confidential/basic/none/auto;
  public+none+S256 без secret проходит, public+secret/basic/none-PKCE и confidential
  без secret не проходят. Unknown enums, bool/fraction/zero/TTL>3600 отклоняются;
  env decimal string `600` принимается существующим Settings loader.
- [x] RED production URI/issuer tests: абсолютные HTTPS issuer/callback/logout URI,
  без credentials/fragment; development HTTP сохраняется. Realm URL формируется
  один раз без изменения trailing-slash семантики ожидаемого issuer. Callback URI
  с OAuth reserved query keys отвергается, разрешённая static query сохраняется точно.
- [x] Выполнить новый Settings subset: ожидаемый RED по отсутствующим полям/guards.
  Реализовать поля/validation и pin Authlib 1.7.2; повторить — GREEN.
  Нет второго Settings loader и автоматического выбора public по пустому secret.
- [x] Fingerprint regression: profile/secret/mapping change меняет binding;
  callback/post-logout URI change тоже; canonical dict order — нет.
  Значения secret/verifier не входят в repr/error.

## Task 2: Одноразовый DB flow и additive migration

**Files:** Create `backend/app/auth/oidc_flow.py`,
`backend/alembic/versions/a5b6c7d8e9f0_oidc_login_flows.py`;
Modify `backend/app/db/models.py`; Test `backend/tests/test_oidc_flow_store.py`.
**Interfaces:** `OidcLoginFlow` / table `oidc_login_flows`: state SHA256 primary key,
browser-binding SHA256, profile fingerprint, JSON payload, created_at/expires_at UTC
(index expires_at). Payload allowlist: redirect_uri, nonce, optional code_verifier.
`LoginFlowData` frozen dataclass: redirect_uri: str, nonce: str,
code_verifier: str | None = None; nonce/verifier исключены из repr;
`create_flow(*, state: str, browser_binding: str, profile: str, data: LoginFlowData,
 ttl_seconds: int) -> None`;
`consume_flow(*, state: str, browser_binding: str, profile: str) -> LoginFlowData | None`;
`purge_expired_flows(limit: int = 100) -> int`; `reset_login_flows() -> int`.
DB failure оборачивается в typed безопасный `OidcFlowUnavailable`.
Синхронные store операции вызываются из async provider через
`starlette.concurrency.run_in_threadpool`; payload возвращается после commit.

- [x] RED: правильная привязка возвращает payload один раз; wrong browser/profile,
  expired/missing state не возвращают его и не потребляют чужой активный flow.
  Два одновременных consume на отдельной file SQLite дают ровно одного победителя;
  никакого select-then-delete и in-memory lock вместо DB атомарности.
- [x] RED: commit failure не возвращает verifier; expired cleanup ограничена 100
  строками, active flows не удалены. Raw state/binding не сохраняются; payload
  отклоняет неизвестные поля, verifier не печатается. Reset удаляет только login
  flows, оставляя непустые auth_sessions/documents неизменными.
- [x] Выполнить flow tests — RED, реализовать `DELETE ... RETURNING` с predicates
  binding/profile/expires, вернуть копию только после выхода из session_scope;
  повторить — GREEN. Не держать DB transaction открытой во время HTTP exchange.
- [x] Migration от перепроверенного head создаёт только таблицу/индекс; downgrade
  удаляет только их. Прогнать upgrade→downgrade→upgrade на новой SQLite БД, затем
  тот же flow concurrency/commit suite на одноразовом PostgreSQL 17. PostgreSQL
  в CI обязателен: отдельный service/container и явно заданный test DB URL,
  недоступный сервер означает FAIL, не skip или переход на SQLite. Existing
  auth sessions/documents переживают migration; create_all не считается Alembic proof.

## Task 3: Authlib adapter, PKCE и signed-token матрица

**Files:** Modify `keycloak_oidc.py`; Create
`backend/tests/fixtures/oidc/idp.py`; Test `test_platform_oidc_contract.py`.
**Interfaces:** действующие `start_login(Request)` / `handle_callback(Request)`;
private `authlib_callback_request(request: Request, state: str, data: LoginFlowData) -> Request`
с копией scope и отдельным session dict, недоступным SessionMiddleware исходного request.
Adapter использует фактический `_state_keycloak_{state}` envelope `data/exp`
Authlib 1.7.2; contract test проверяет чтение/очистку именно библиотекой,
не общим mutable framework/session объектом. Временный exp не продлевает DB TTL.
Fixture `SignedOidcIdP` — ThreadingHTTPServer на loopback ephemeral port, RS256/JWKS,
одноразовые code, счётчики requests и управляемые invalid cases; ключи только tmp.

- [x] RED actual HTTP fixture: basic/post/none token authentication и S256 проверяются
  на принятых HTTP requests; verifier 64 URL-safe символа (random 48 bytes),
  challenge RFC7636 SHA256/base64url, уникальны для каждой попытки. Confidential
  default не добавляет PKCE, явный confidential S256 также проходит.
- [x] RED: missing/bad/duplicate state/code, code+error, чужой browser binding,
  duplicate error, valid OAuth denial и два callback одного state одновременно.
  State/browser binding генерировать через token_urlsafe(32), nonce так же;
  state/binding принимаются только как 43 ASCII base64url символа до DB lookup.
  Проверять число token exchanges
  и auth_sessions, replay old signed cookie не вызывает второй exchange.
- [x] RED signed tokens: missing ID token, bad signature/none/wrong nonce/aud/iss,
  expiry старше leeway 120 s, missing sub; discovery с none+RS256
  не разрешает unsigned token, none-only discovery отклоняется до authorization; wrong nonce с `nonce_supported=false`
  тоже отказ. JWKS новый kid вызывает refresh и затем успешный verified login.
  Ни provider.handle_callback, ни parse_id_token не подменяются этой матрицей.
- [x] Run новый protocol subset — RED. Login: создать authorization URL стандартным
  Authlib, сохранить DB payload и opaque `oidc_browser` binding в signed session,
  redirect только после DB commit. Удалить legacy `_state_keycloak_*` из исходной
  browser session; не вызывать прежний authorize_redirect, сохраняющий payload в cookie.
- [x] Callback: validate query, атомарно consume, затем authorize_access_token с
  ephemeral Request, explicit expected-issuer claims_options и leeway=120.
  Authlib проверяет JWT; provider требует ID token/validated subject и nonce match.
  Временный session очищается в finally. Для OAuth denial consume выполняется
  до безопасного отказа, без exchange. Повторить protocol suite — GREEN.
- [x] RED request ordering: invalid/foreign/expired/replayed state вызывает
  **ноль** discovery/JWKS/token/UserInfo requests. После успешного consume
  загрузить/проверить discovery, затем exchange; mismatch issuer потребляет flow,
  но не вызывает token/UserInfo/JWKS чужого issuer. Login проверяет discovery
  до выдачи authorization redirect. Не объявлять проверку кешированного документа
  обнаружением удалённого изменения: оно видно после cache refresh/TTL или restart.
- [x] Cache key включает все новые settings; проверить discovery issuer точно
  против expected issuer до authorization/exchange, reject без вызова endpoints
  неверного issuer. Config change после login отказывает до exchange; фиксированный
  saved redirect_uri не меняется от Host/X-Forwarded-* callback. Cache TTL 3600 s и
  Authlib JWKS refresh сохраняются; cache не заменяет profile/session invalidation.
- [x] RED metadata override: discovery содержит неожиданные scalar
  `token_endpoint_auth_method`/`code_challenge_method` с конфликтующими значениями.
  Они не переопределяют Settings через Authlib metadata merge: отклонить такой
  конфликт безопасно до exchange. Проверять фактические token HTTP requests.
  В token exchange нет автоматического retry после timeout/обрыва: state уже
  consumed, результат выдачи токена может быть неизвестен; требуется новый login.
- [x] RED production discovery endpoints: authorization/token/jwks и UserInfo
  при `endpoint` должны быть абсолютными HTTPS URL без credentials/fragment.
  HTTP endpoint в HTTPS discovery отклоняется до отправки code/token/secret.
  Разные разрешённые hosts не запрещать автоматически; endpoint redirect нельзя
  использовать для обхода проверки или пересылки credentials на другой URL.

## Task 4: Authoritative UserInfo и совместимое mapping

**Files:** Modify `keycloak_oidc.py`; Test `test_platform_oidc_contract.py`,
existing `test_auth_groups.py`/`test_auth_sso.py`.
**Interfaces:** `async def select_userinfo(self, token: dict, validated_claims: dict) -> dict`
в provider использует Settings экземпляра и endpoint HTTP call. Returns
один выбранный claims object, не union. `sub` сверяется до mapping external_id.

- [x] RED actual IdP: непустой ID-token userinfo с admin groups, endpoint только
  viewer — `endpoint` даёт viewer и ровно один UserInfo request. `auto` сохраняет
  legacy claims choice и не вызывает endpoint при валидных ID-token claims.
- [x] RED: endpoint sub missing/mismatch/не строка; timeout/401/non-object/invalid
  JSON; missing mapped groups не берутся из ID token и не создают privileged
  session. Применять прежние separator/path/partial-field mapping, не менять
  normalize_groups других providers. External_id после mapping не может быть пустым.
- [x] Run subset — RED; реализовать выбор/subject проверки без silent fallback,
  повторить — GREEN. Проверить no_role: role mapping/default policy прежние,
  production unknown groups не создают новую session. Numeric/bool OIDC sub отказ;
  легальные старые формы group mapping остаются поддержанными.

## Task 5: Safe errors, commit/session rotation и logout

**Files:** Modify `backend/app/auth/api.py`, `service.py`, `main.py`;
Create `backend/app/auth/oidc_logging.py`;
Create `backend/scripts/invalidate_oidc_sessions.py`;
Test existing auth suites + new `test_platform_oidc_contract.py`/`test_oidc_flow_store.py`.
**Interfaces:** `OidcRejected(reason: str)` с allowlisted codes и
`OidcUnavailable` — sanitized provider errors; `AuthSessionStoreUnavailable` для
session commit/delete. Logging: `install_oidc_log_filters() -> None` и
`oidc_log_context()` — ContextVar scope provider HTTP calls; у app-owned handlers
отбрасываются raw Authlib/httpx/httpcore сообщения внутри scope. `uvicorn.access`
для `/api/auth/{login,callback,logout}` удаляет query, сохраняя метод/path/status.
Filter installation идемпотентна, выполняется при startup до любого request,
включая callback/logout без предшествующего login; после подключения динамических
app-owned handlers применяется повторно. Context охватывает discovery и JWT/JWKS тоже;
custom external handlers/Gateway/IdP logs остаются окруженческим gate.
`store_identity` cookie изменяет только после commit,
удаляет прежнюю DB session этого request той же транзакцией; другие sessions сохраняет.
CLI `invalidate_oidc_sessions.py --confirm-invalidate-oidc-sessions`: transaction
удаляет auth_sessions с `identity.provider == "keycloak_oidc"` + login flows,
выводит только counts. SQLAlchemy JSON predicate проверить на обеих БД;
по одному наличию id_token provider не определять. Без confirm — read-only counts.
Запускать с явно выбранным защищённым runtime env выбранной среды, не с dev fallback.

- [x] RED API/caplog: OAuth description/query, URL с code, ID/access token, secret,
  nonce/verifier/claims body не появляются в error response/application logger; только
  fixed unavailable/session_expired/no_role и safe reason/type. Реальный
  uvicorn access record с callback code/query и IdP debug token record также
  не раскрывают значения; outside-scope unrelated httpx record сохраняется.
  Callback первым запросом после startup и поздно подключённый app handler
  проходят ту же проверку. JWT/JSON/DB ошибки
  не проходят в catch-all как traceback с provider response. Логи dependencies
  не включают DEBUG HTTP/token body в штатных профилях; fixtures не публикуются.
- [x] RED: session commit failure не выдаёт новую identity cookie; consumed flow
  не оживает после rollback. Успешный повторный login удаляет старую session
  данного браузера; old signed cookie не работает, другое устройство работает.
  Rotation удаляет session ID из cookie данного request; это не глобальная
  сериализация двух разных login flows. Зафиксировать multi-tab cases отдельно:
  после success cookie очищает binding, следующая вкладка начинает новый login;
  два уже отправленных разных callback могут оба завершиться, видимая cookie
  определяется последним ответом, невидимая DB session ограничена TTL.
  Exactly-once относится к одному state, не к браузеру/пользователю целиком.
- [x] RED logout: локальное DB deletion должно commit до внешнего redirect;
  deletion failure даёт unavailable и не подтверждает logout. После успешного
  deletion внешняя ошибка не возвращает session; no id_token — local logout.
  Проверить base providers/simulation regression при изменении clear_identity.
  Существующий успешный RP logout передаёт id_token_hint браузеру в redirect_url;
  это узкое протокольное исключение, не browser session/log/error response.
- [x] RED startup: pending flow reset выполнен после init_db до OIDC login,
  restored consumed-state snapshot не даёт callback; auth sessions сохраняются.
  OIDC startup DB/reset failure прерывает lifespan до yield; очистка не
  прячется в существующем broad startup exception handler. Disabled/simulation/local
  режимы не получают зависимости от IdP. TTL purge выполняется при create_flow
  ограниченно и при startup; отдельного dispatcher/фонового worker нет.
- [x] Run affected auth/flow suites — RED; реализовать safe exceptions + commit
  order + startup reset; GREEN. Invalidation CLI без confirm ничего не изменяет;
  в отдельной БД сохраняет non-OIDC sessions/documents, удаляет OIDC/flows.
  Проверить legacy OIDC rows без id_token. Runbook смены issuer/client/claims:
  закрыть пользовательский доступ, остановить backend, выполнить CLI с прежним
  runtime env, применить новый профиль, startup+smoke, открыть доступ. При ошибке
  CLI доступ остаётся закрытым; один лишь restart не инвалидирует auth sessions.
  Новые auth audit events/JSON/outbox не добавлять: самостоятельный audit package.

## Task 6: Переносимые profiles и домашняя runtime приёмка

**Files:** Modify `scripts/kubernetes/home_access.py`, `home_probe.py`,
`.github/workflows/kubernetes.yml`, `backend/tests/test_kubernetes_chart.py`,
`test_kubernetes_home_access.py`, `scripts/kubernetes/lifecycle.py`,
`backend/tests/test_kubernetes_lifecycle.py`, дополнительный
`backend/tests/test_oidc_operational_edges.py`, `.env.example` (сверить наличие),
`deploy/production/{bundled,external}.env.example`, `SECURITY.md`,
`docs/KUBERNETES_HOME_ACCESS.md`; Create `docs/OIDC_CONFIGURATION.md` и report
`docs/superpowers/reports/2026-10-04-oidc-compatibility-acceptance.md` после actual run.

**Interfaces:** home helpers получают `--client-type confidential|public`
(default confidential), `--token-auth-method client_secret_basic|client_secret_post|none`,
`--pkce-method none|S256`, `--userinfo-source auto|endpoint`; defaults совместимы.
Public требует explicit none/S256. Новые Settings проходят через существующий
runtime.config/envFrom, secret остаётся в Secret, не в values. Home public fixture
включает группы **только** в UserInfo; confidential registration отдельно.
Schema release/config **2** и backup format **2** не меняются от additive runtime
Settings: старые overlays/guards должны продолжить работать. Образы с новой
миграцией выпускаются новой revision; app image match при restore обязателен.

- [x] RED rendered env→Settings: explicit public/PKCE/UserInfo/issuer/TTL доходят
  до application и operation Jobs; hidden inherited client secret вызывает
  Settings refusal. Старые chart overrides, schema1/2 refusal guards не ослабляются.
- [x] RED home helper profile matrix: public registration без secret/direct grants,
  S256 enforced, exact redirect/logout; UserInfo-only mapper; confidential defaults
  прежние. Helpers не печатают synthetic credentials. Run — RED, implement — GREEN.
- [ ] Собрать backend/frontend/stub **один раз** с новой revision; source hashes и
  реальные local/published identities различать. Сначала проверить свободные
  ресурсы домашней VM, существующие стеки не останавливать; при дефиците отдельный
  Docker/kind fallback с точным местом/версиями и owner inventory.
  **Факт:** VM SSH-профиль не найден, VM capacity не проверена. Локальный
  Docker/kind fallback проверен (16 CPU, 15,48 GiB). Backend v1 пересобран как
  v2 после подтверждённого раскрытия secret в Settings ValidationError,
  затем v3 исправляет только код HTTP503 при сбое logout;
  frontend/stub не пересобирались. Все три HTTPS профиля повторены на v2; delta v3 проверена по
  ветке ошибки logout и auth/protocol suites. После воспроизведения принятия
  unsigned token при advertised none собран v4; 472-test packaged suite,
  12-test packaged PG suite, 10×18 критичных повторов и новая матрица фиксируются
  в отчёте, старые результаты не заменяются.
- [x] Последовательно actual HTTPS/Gateway home registrations: confidential basic
  default, confidential post+S256, public none+S256+endpoint. Одинаковые images,
  отдельные app namespaces/Secrets/PVC/callbacks; выбранный runtime CA без rebuild.
  Reuse home_probe: четыре роли, no-role, Secure cookie/CSRF, upload/search/chat,
  TTL/logout. Добавить доказательство PKCE и UserInfo-only groups на настоящем IdP,
  не считать mock fixture доказательством registration.
- [x] Реальный непустой PostgreSQL 17 migration+backup/restore: сохранить schema-2
  baseline kit/images отдельно; restore первым делом saved images с saved CA.
  До первого старта восстановленной копии задать **новый APP_SECRET_KEY** через
  защищённый runtime Secret; содержимое backup kit не переписывать. Доступ снаружи
  закрыт до проверки. Это обязательный gate для всех восстановленных сессий,
  включая restore старого image, в котором ещё нет нового CLI/reset.
  Затем deploy нового OIDC image выполняет additive migration, confidential
  smoke и public profile. Новым CLI удалить восстановленные OIDC sessions/flows
  до открытия трафика; новая signing key после этого сохраняется. RED/actual case:
  создать session+pending flow → backup → logout/consume в исходной копии →
  restore backup; старый auth cookie не даёт доступ, callback не вызывает exchange.
  Обычный restart отдельно сохраняет валидную auth session неизменного профиля.
  Pending flow после restart/restore повторно не принимается;
  копирование cookie/session/flow payload в public report запрещено.
  Rollback binary только через Recreate к сохранённому confidential профилю,
  при закрытом доступе, остановленном backend и после CLI инвалидизации
  OIDC sessions/pending flows новым image; таблица остаётся, production downgrade DB
  не выполняется. Для forward migration Job при binary rollback передать
  `--migration-image` с точным установленным новым backend; этот deploy-only
  параметр запрещает посторонний image и initial install до lock/stop.
  Backup revision читать напрямую из БД, не через граф старого Alembic.
  Public config на старом image не объявлять допустимым rollback.
- [x] Regression из backend: `python -m pytest tests/test_auth.py tests/test_auth_sso.py tests/test_auth_groups.py tests/test_authz.py tests/test_oidc_flow_store.py tests/test_platform_oidc_contract.py tests/test_oidc_operational_edges.py tests/test_deployment_profile.py tests/test_health_readiness.py tests/test_kubernetes_chart.py tests/test_kubernetes_lifecycle.py tests/test_kubernetes_release.py tests/test_kubernetes_toolbox.py tests/test_kubernetes_home_access.py tests/test_kubernetes_runtime_trust.py tests/test_kubernetes_configuration_acceptance.py -q` — PASS/0 skips.
  CI сохраняет прежние network/model/token-budget suites и добавляет новые OIDC suites.
  Из frontend: `node --test test/api-proxy.test.js`; на Linux existing Compose
  backup/diagnostics contracts. Новые flow/protocol cases — **10 последовательных**
  fixture runs; full home matrix — один actual run, повтор только по новой гипотезе.
- [x] Run `node scripts/check-project.mjs` перед будущим коммитом по AGENTS;
  failed dependency/CI gate остаётся failed. Report: версии, images, counts,
  migration/HTTP-JWT/home результаты, failed attempts, точный stability набор,
  review type и unverified target registration/live/cert renewal/CI/publication.
  Cleanup только собственных UID/run-id resources после backup proof.

## Проверки дополнений review

Cases проверены в указанных test modules; отдельно выполнена runtime restore
sequence. Непосредственные результаты и версии images приведены в отчёте:

| Task | Test name / assertion |
|---|---|
| 1 | `test_profile_fingerprint_binds_redirect_uris`: оба URI меняют fingerprint |
| 3 | `test_invalid_state_performs_no_idp_requests`: все HTTP counters равны 0 |
| 3 | `test_discovery_cannot_override_explicit_profile`: конфликт отказан до exchange |
| 3 | `test_parallel_callbacks_one_exchange_and_one_session`: exchange/session ровно 1 |
| 5 | `test_callback_first_request_logs_no_secrets`: query/token markers отсутствуют |
| 5 | `test_invalidate_oidc_sessions_preserves_other_providers`: включая legacy row без id_token |
| 6 | actual backup/logout/restore sequence: старые auth cookie и callback отклоняются |

Для Tasks 1/3/4/5: из backend `python -m pytest tests/test_platform_oidc_contract.py -q`;
для Task 2 и DB части Task 5: `python -m pytest tests/test_oidc_flow_store.py -q`.
Каждый RED/GREEN прогон ограничить добавленными cases через `-k`, полный набор —
на границе Task 6. Runtime restore case принадлежит home runner, не подменяется
одноимённым unit test. PostgreSQL job дополнительно публикует выполненный count,
версию сервера и отсутствие skips, без URL/credentials.

## Self-review и handoff

Spec coverage: profiles/cache → 1/3; atomic state/migration → 2;
PKCE/signed tokens/issuer → 3; claims/roles → 4; safe errors/session/logout/reset
→ 5; rendered settings, старые режимы и home/restore → 6. Каждый Review Focus
имеет owning tests; никаких undefined API между задачами, новые enum/TTL/issuer
имена совпадают с spec.

Plan и уточнённая spec проходили review до runtime edits. Исполнение включает
self-review, actual runtime proof и дополнительные boundary tests; независимый
agent review не выполнялся без отдельного указания. Сохраняется inline
исполнение; независимая agent review требует отдельного указания. OIDC не закрывает
audit/trusted IP, replicas или целевую платформу. Отдельный audit plan создаётся
после выбора best-effort/outbox/failure policy. Коммит/merge не являются частью
этого handoff.
