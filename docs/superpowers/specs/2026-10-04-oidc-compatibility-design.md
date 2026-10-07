# Совместимость OIDC для общего развёртывания

Дата: 04.10.2026. Статус: реализация задачи 5а находится в Kubernetes worktree;
локальная приёмка описана в [отчёте](../reports/2026-10-04-oidc-compatibility-acceptance.md).
Контракт сверён с Authlib 1.7.2; реализация поддерживает confidential и public
профили. [Подробный план](../plans/2026-10-04-oidc-compatibility.md) сохраняет
фактические результаты и незакрытые проверки. Общие шаблоны обезличены;
регистрации, группы, адреса и Secrets задаются в окруженческой копии.

## Цель и границы

По [оценке и критериям обобщения](../../KUBERNETES_PLATFORM_QUESTIONS.md), пункты
7–8, public/confidential/PKCE и authoritative UserInfo выбраны как общий пакет.
Его проектирование не зависит от регистрации конкретной среды; registration,
issuer, claim mapping и применимость профиля уточняются перед её приёмкой.
Расширение не доказывает неприемлемость существующего confidential flow:
он сохраняется совместимым вариантом. Поддержка любых IdP не обещается.

Сохранить существующий confidential Keycloak flow и добавить явную поддержку
public client с PKCE. В DEV/TEST/PROD остаются `ENVIRONMENT=production`, Secure
cookie, CSRF, server-side auth sessions и роли без допуска неопознанного
пользователя. Тип клиента выбирается по регистрации IdP, а не автоматически
по tier. Поддерживаемый public/confidential profile можно использовать в любой
среде при соответствующей регистрации и действующей policy; tier не отменяет
TLS/CSRF/role проверки и не подставляет другой token auth method.

Не входят: новый универсальный OIDC provider, смена внешних API маршрутов,
автоматическая регистрация IdP, refresh/back-channel logout, реплики backend,
LDAP и клиентские интеграции. Локальные launchers и оба режима Compose сохраняются.

## Подтверждённое исходное поведение

Проверено в `codex/kubernetes-rollout`, baseline `f551f5f` с текущими изменениями:

| Участок | Наблюдение по коду | Следствие |
|---|---|---|
| `backend/app/config.py:validate_auth_provider` | Client secret обязателен для любого `keycloak_oidc` | Public без секрета не запускается |
| `backend/app/auth/providers/keycloak_oidc.py:_cached_oauth` | Scope `openid profile email`; тип клиента/PKCE явно не заданы | Нельзя утверждать поддержку public/PKCE по одному redirect |
| Там же `handle_callback` | Непустой `token.userinfo` предотвращает запрос endpoint | Группы только из endpoint могут не попасть в identity |
| Установленный Authlib, `starlette_client/apps.py` | После проверки ID token библиотека помещает его claims в `token.userinfo` | Это поле не доказывает вызов endpoint; snapshot библиотеки сверяется перед реализацией |
| `backend/app/auth/service.py` | Opaque session ID в signed cookie, identity/id_token в `auth_sessions`; TTL приложения | Сохраняем модель серверной сессии |
| `backend/app/auth/identity.py` и `authorizer.py` | Mapping групп, приоритет ролей, snapshot групп в сессии | Пересчёт роли не означает обновление групп из IdP на каждом запросе |
| `frontend/src/app/api/[...path]/route.js` | Forwarded headers удаляются; cookies/stream сохраняются | Callback не должен зависеть от пользовательских host/proto заголовков |

`backend/tests/test_auth_sso.py` проверяет mock flow, TTL, logout и ошибки;
он не является проверкой настоящего public клиента или подписанного ID token.

## Рассмотренные варианты

1. Сделать secret необязательным без новых параметров. Малая правка, но не
   определяет token authentication, PKCE и источник групп. Недостаточно.
2. Добавить явные профили к существующему provider. Рекомендуется: сохраняет
   текущие маршруты/authorizer и позволяет проверить каждый контракт отдельно.
3. Ввести новый универсальный provider или вынести вход в Gateway. Увеличивает
   миграцию identity, logout и доверие к заголовкам; для задачи 5а не требуется.

## Предлагаемый контракт конфигурации

Новые имена ниже — проект интерфейса, пока не действующие env-параметры.

| Параметр | Default для совместимости | Правило |
|---|---|---|
| `KEYCLOAK_CLIENT_TYPE` | `confidential` | Только `confidential` / `public`; не выводить из наличия secret |
| `KEYCLOAK_TOKEN_ENDPOINT_AUTH_METHOD` | `client_secret_basic` | Confidential: basic или post; public: явно `none` |
| `KEYCLOAK_PKCE_METHOD` | `none` | Public: обязательно `S256`; confidential: `none` или `S256` по регистрации |
| `KEYCLOAK_USERINFO_SOURCE` | `auto` | `auto` сохраняет прежний выбор; `endpoint` всегда запрашивает endpoint |
| `KEYCLOAK_EXPECTED_ISSUER` | URL realm из текущих KEYCLOAK_URL/REALM | Точное ожидаемое значение, не взятое из callback/token |
| `OIDC_LOGIN_STATE_TTL_SECONDS` | 600 | Целое 1–3600 секунд; время одноразового flow, не TTL пользователя |

Public запрещает непустой secret и методы basic/post. Confidential по-прежнему
требует secret. Unknown values и несовместимые сочетания отклоняются до входа.
`SSO_REDIRECT_URI` и post-logout URI задаются явно и используют HTTPS в
production; внешний callback совпадает с зарегистрированным URI. Redirect URI
при code exchange берётся из сохранённого flow, а не реконструируется по Host.

Проверка issuer из discovery относительно ожидаемого issuer независима от
проверки ID token библиотекой: доверять одному лишь issuer из discovery нельзя.
Discovery/JWKS/token/userinfo используют обычную TLS verification и системный
CA bundle. Перенос на внутренний DNS не меняет ожидаемый публичный issuer.
В production URL authorization/token/jwks и выбранного UserInfo endpoint —
абсолютные HTTPS без credentials/fragment. HTTPS discovery с HTTP token endpoint
отклоняется до передачи секретов. Cross-host endpoints допустимы; HTTP redirects
не должны обходить проверки или пересылать credentials на другой URL.
Metadata не может переопределить выбранные Settings token auth/PKCE через
merge Authlib: конфликтующие scalar overrides отклоняются до exchange.
Issuer проверяется у фактически загруженного документа; удалённое изменение
discovery обнаруживается после refresh/TTL кеша (3600 s) или restart,
а не обязательно на первом callback после изменения у IdP.
Требование совпадения discovery issuer опирается на
[OIDC Discovery, раздел 4.3](https://openid.net/specs/openid-connect-discovery-1_0.html#ProviderConfigValidation).

## Flow, PKCE и identity

Сохраняется Authorization Code flow через Authlib. Для S256 создаётся новый
verifier на каждую попытку; downgrade на plain/none после ошибки не допускается.
Проверки state/nonce, подписи, audience, issuer и срока ID token остаются в
библиотеке с явным ожидаемым issuer; обязательность валидированного ID token
проверяется и на уровне provider. Provider дополнительно сравнивает
валидированный nonce со значением flow: `nonce_supported=false` в claims
не разрешает пропустить сравнение. JWT с `alg=none`, неверной подписью или
claims не создаёт identity; проверки нельзя подменять decode без verification.
Сохраняется текущий Authlib leeway 120 секунд; expired negative tests выходят
за этот допуск. PKCE следует
[RFC 7636, разделы 4.2 и 7.2](https://www.rfc-editor.org/rfc/rfc7636).

Для новых flow предлагается DB-хранилище одноразового состояния: случайный
непрозрачный идентификатор в browser cookie, state/nonce/verifier/redirect URI
на сервере, атомарное потребление callback и очистка по TTL. Verifier, code и
токены не помещаются в browser cookie или логи. Это также исключает повторное
потребление одного state через replay старой signed cookie. Adapter согласован с текущими `create_authorization_url` /
`authorize_access_token` Authlib 1.7.2, без Redis. Пакет закрепляет
`authlib==1.7.2`; повторная смена версии требует signed-token regression.
DB-cache с раздельными get/delete не считается атомарным consume.
DB DELETE с RETURNING потребляет state до token exchange и возвращает
payload только после commit. Временный Request с отдельным in-memory session
адаптирует payload к Authlib; исходный browser session не получает эти поля. Старт login
без доступной БД завершается безопасной ошибкой. Незавершённые старые flow при
обновлении требуют повторного входа; уже созданные auth sessions сохраняются
при обычном restart/update с неизменным identity profile. Restore не относится
к этому исключению: старые auth sessions из backup тоже могут быть отозванными.
При старте backend незавершённые login flows удаляются до приёма OIDC login;
restart/update/restore требуют повторного начала входа. Это предотвращает
возврат ранее потреблённого state из DB backup. Для OIDC ошибка очистки
при старте не допускает открытия входа. Текущий compact/single-owner режим
является условием этого правила; оно не объявляется готовым для реплик.
Browser binding — отдельный случайный opaque ID в существующей signed cookie;
DB хранит его SHA256 и SHA256 state. Callback другого браузера не потребляет
flow. State/browser binding/nonce — token_urlsafe(32); входные state/binding
проверяются как 43 ASCII base64url символа до DB lookup. Verifier —
token_urlsafe(48), 64 символа, новый для каждой S256 попытки.
Две попытки при уже созданном binding имеют разные state; две гоняющиеся первые
попытки без cookie могут потребовать повторного входа для проигравшей.
Успешный login очищает binding в новой cookie, поэтому более поздняя вкладка
начинает новый flow. Если два разных callback уже отправлены, оба могут
завершиться: последняя response cookie определяет видимую session, невидимая
DB session живёт до TTL. Здесь нет гарантии единственного login на браузер:
exactly-once обеспечивается для одного state. Rotation удаляет прежний session ID
из cookie конкретного request, не все параллельно созданные sessions пользователя.
После атомарного consume ни network error, ни rollback последующей auth session
не возвращают state: пользователь начинает новый вход. OAuth denial с валидным
state тоже потребляет его; missing/duplicate state и code+error отвергаются.
При success callback code обязателен и единственный; denial с единственным
error не требует code; duplicate error тоже отклоняется.
Payload имеет только redirect_uri/nonce/optional verifier.
Потребление выполняется через threadpool для синхронного SQLAlchemy; startup
reset при DB ошибке прерывает OIDC startup до lifespan yield.
Порядок callback: query/binding → commit consume → discovery validation →
exchange → JWT/nonce → UserInfo/mapping → commit auth session → cookie.
Невалидный state не вызывает даже discovery/JWKS. Timeout/обрыв exchange
не повторяется автоматически, поскольку результат выдачи токена неизвестен.
Adapter использует отдельный `_state_keycloak_{state}` envelope `data/exp`
Authlib 1.7.2; он не продлевает TTL уже проверенного DB flow.

`endpoint` использует access token только серверно, всегда получает UserInfo
и принимает группы только оттуда. Проверенный ID token сохраняет authority
для subject: `sub` endpoint должен совпасть с ним, отсутствие/mismatch — отказ
без новой auth session. Неполный endpoint, timeout/401/ошибка JSON не вызывают
fallback к группам ID token. Правило subject задано
[OIDC Core, раздел 5.3.2](https://openid.net/specs/openid-connect-core-1_0.html#UserInfoResponse).

В `auto` сохраняется прежний выбор claims, но тот же контроль subject действует
при обращении к endpoint. Нельзя объединять группы двух источников: это может
вернуть устаревшее право. Существующие field mapping, separator и path mode
сохраняются; OIDC `sub` сравнивается отдельно от mapped `external_id`. Не менять
действующие идентификаторы/блокировки молча. Новые профили используют full_path
и явный mapping группы; leaf остаётся только явным выбором для уникальных имён.

Ключ OAuth cache включает новые profile settings, issuer и источник claims.
DB flow привязывается HMAC-SHA256 fingerprint с APP_SECRET_KEY к issuer,
URL/realm/client/type/auth method/PKCE/UserInfo/mapping/path mode/separator,
обоим настроенным redirect URI и digest client secret. Изменение callback URI
после login отклоняет старый flow до exchange; значения не выводятся в логи.
Ротация secret/config не переиспользует старый client; cache TTL и JWKS refresh
проверяются по текущей версии Authlib. Изменение issuer/client или источника
групп в действующей среде сопровождается явной инвалидизацией старых identity
sessions до открытия трафика; автоматическая миграция subject не предлагается.
Порядок смены identity profile: закрыть доступ, остановить backend, выполнить
явный CLI с прежним защищённым runtime env, применить новый профиль, проверить
startup/login, открыть доступ. CLI транзакционно удаляет login flows и строки
auth_sessions с `identity.provider == "keycloak_oidc"`, включая legacy без
id_token; другие providers/documents сохраняются. Без confirm — только counts.

При restore до первого запуска любой сохранённой версии приложения задаётся
новый APP_SECRET_KEY через защищённый runtime Secret; backup kit остаётся
неизменным, внешний доступ закрыт. Старые cookie не должны работать даже при
restore старого image без нового CLI. После upgrade новым CLI удаляются
восстановленные OIDC sessions/flows; затем smoke и открытие доступа с новым
ключом. Отдельная приёмка: backup до logout → logout → restore → отказ старой
auth cookie и callback. Одно лишь удаление login flows этот сценарий не закрывает.
Binary rollback также выполняется при закрытом доступе с инвалидизацией OIDC
sessions/flows новым image перед возвратом сохранённого confidential профиля;
новая таблица остаётся, production DB downgrade не нужен. Старый Alembic не
знает новый head: deploy с `--migration-image` использует точный установленный
новый backend только для forward migration Job. Preflight отказывает до lock/stop
при отсутствии установки или несовпадении image. Backup читает единственную
revision из БД без разрешения графа старого image; формат backup остаётся 2.
Совместимость текущего additive изменения проверяется отдельно от этого guard.

Роль пересчитывается из snapshot групп, блокировка проверяется на запросе.
Отзыв группы в IdP не обновляет уже открытую сессию автоматически: предел —
согласованный TTL/повторный вход. ID token expiry проверяется при login и не
подменяет существующий TTL приложения. Если нужен немедленный отзыв группы,
это отдельный контракт refresh/revalidation, а не обещание этого изменения.

## Ошибки, logout и связь с аудитом

App access logs для auth endpoints сохраняют путь/метод/status без query.
Исходные HTTP/Authlib сообщения внутри OIDC вызова не передаются в настроенные
приложением handlers; безопасные категории пишет provider/API. Downstream
Gateway/IdP collector logging и custom external handlers требуют отдельной
окруженческой проверки; нельзя обещать их sanitization по backend тестам.
Фильтры устанавливаются до первого request при startup и повторно для поздно
подключённых app-owned handlers. Проверяется callback без предыдущего login;
контекст подавления сырых сообщений включает discovery/JWKS, не только exchange.

Публичные ошибки имеют существующие безопасные категории unavailable,
session_expired/no_role и не включают IdP body, error_description, URL query,
code, secret или token. OAuth error/malformed claims не создают сессию.
Logout сначала фиксирует локальное удаление; внешняя ошибка не восстанавливает
сессию. Ошибка commit удаления DB session даёт безопасный unavailable и
не объявляет локальный logout завершённым. После успешного повторного login
прежняя auth session этого браузера удаляется той же транзакцией, что создаёт
новую; сессии других устройств не меняются. При отсутствии server-side id_token
выполняется только локальный logout.
Существующий успешный RP logout передаёт id_token_hint браузеру в redirect_url;
это протокольное исключение из server-only хранения токена, не содержимое
browser session, error response или логов.
Back-channel logout и немедленный внешний revoke не входят в этот flow.

В этом OIDC-пакете auth cookie выдаётся только после commit auth session;
новые audit events/transport не реализуются. Интеграция успеха login после commit
и отказа один раз относится к отдельной [спецификации аудита](2026-10-04-audit-delivery-design.md).
Метод события будет `oidc`, без предположений о пароле/MFA внутри IdP.
Если позднее выбрана обязательная запись аудита, audit/session должны иметь
общую транзакцию; это отдельный prerequisite её rollout, не скрытая зависимость
текущего OIDC-пакета от ещё не выбранного outbox/failure policy.

## Приёмка и последовательность будущей реализации

1. Settings/cache: confidential legacy regression, public без secret,
   rejected mixed profiles, explicit HTTPS URIs, issuer mismatch.
2. Настоящий mock HTTP IdP с подписанными токенами: discovery/JWKS, basic/post/none,
   S256 challenge/verifier, неверные state/nonce/aud/iss/exp, повтор callback,
   JWKS rotation. Не подменять `handle_callback` в этой матрице.
3. UserInfo-only groups при непустом token.userinfo; конфликт групп, subject
   mismatch, missing claims, endpoint failure и отсутствие новой сессии.
4. DB flow TTL/atomic consume, старый cookie replay, две параллельные попытки,
   config change/cache; отсутствие verifier/token в cookie и диагностике.
5. Реальный home IdP: три отдельных тестовых client registrations — confidential
   basic, confidential post+S256, public none+S256+endpoint;
   production auth settings, HTTPS/CSRF/roles/expiry/logout. Настоящая среда
   повторяет эту матрицу со своей регистрацией; реквизиты не попадают в Git.
6. SQLite и отдельный PostgreSQL 17: migration и атомарный consume/commit failure.
   PostgreSQL service обязателен в CI, его отсутствие — failure, не skip/fallback.
   Непустой runtime backup/restore проверяет и отозванные auth cookies, и flow replay;
   обычный restart отдельно подтверждает сохранение действующей auth session.

[Подробный TDD план](../plans/2026-10-04-oidc-compatibility.md) подготовлен для
совместного рассмотрения с уточнённым контрактом; теперь реализован в рабочей
ветке без merge/commit. Фактические результаты и незакрытые delivery gates
приведены в отчёте. В пакет входят
миграция flow store, provider/settings, fixtures/tests, обезличенные profiles,
SECURITY.md и проверки старых local/Compose режимов. Новая регистрация IdP и
проверка окружения не считаются выполненными по mock-тестам.

## Дополнение реализации: алгоритмы discovery

`id_token_signing_alg_values_supported` может рекламировать none для других
регистраций. Для этой реализации none исключается из кешированного metadata
перед использованием Authlib; none-only/некорректный список отвергается до login.
JWT claims по-прежнему декодирует и проверяет только Authlib. Приёмка включает
unsigned token с рекламируемым none и успешный RS256 из смешанного списка.
