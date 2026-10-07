# Настройка OIDC

Реализация находится в рабочей ветке Kubernetes; перед переносом сверяйте
план и [фактический отчёт приёмки](superpowers/reports/2026-10-04-oidc-compatibility-acceptance.md). Регистрации, URL и Secrets задаются для среды;
примеры здесь обезличены. Keycloak Authorization Code flow использует Authlib 1.7.2.
Неподписанный ID token (`alg=none`) запрещён даже при рекламе этого алгоритма
в discovery. В списке разрешённых алгоритмов остаются подписанные варианты;
none-only discovery отклоняется до authorization redirect.

## Выбор регистрации

| Параметр | Confidential по умолчанию | Public |
|---|---|---|
| KEYCLOAK_CLIENT_TYPE | confidential | public |
| KEYCLOAK_TOKEN_ENDPOINT_AUTH_METHOD | client_secret_basic; возможен client_secret_post | none явно |
| KEYCLOAK_PKCE_METHOD | none; возможен S256 | S256 обязательно |
| KEYCLOAK_CLIENT_SECRET | из защищённого Secret/env | удалить из всех envFrom/env источников |
| KEYCLOAK_USERINFO_SOURCE | auto | auto либо endpoint по claim mapping |

`endpoint` всегда получает UserInfo, сверяет его строковый sub с проверенным
ID token и берёт группы только из выбранного ответа. Ошибка/неполный ответ не
вызывает fallback к группам ID token. Если группы доступны только в UserInfo,
выбирайте endpoint. Mapping и приоритет ролей остаются общими; не назначайте
default role для неопознанных групп в production.

Для production явно задайте SSO_REDIRECT_URI и SSO_POST_LOGOUT_REDIRECT_URI,
KEYCLOAK_GROUP_PATH_MODE (рекомендуется full_path), HTTPS и Secure cookies.
KEYCLOAK_EXPECTED_ISSUER — точная строка ожидаемого issuer; default строится из
KEYCLOAK_URL/REALM. Callback URL допускает обычные static query параметры,
но не state/code/error и прочие OAuth response параметры. Все используемые
production endpoints discovery требуют HTTPS. Trust bundle задаётся существующим
runtime механизмом; не отключайте проверку сертификатов.

## Состояние входа и сессии

OIDC_LOGIN_STATE_TTL_SECONDS=600 (целое 1–3600). DB хранит SHA256 state и browser
binding; nonce/verifier/redirect URI остаются на сервере. После commit consume
flow не возвращается при сбое IdP или session commit. Начните вход заново.
Нужна миграция a5b6c7d8e9f0 поверх 040b1c2d3e4f. Production использует Alembic,
локальная development база поддерживает прежний create_all.

Startup удаляет pending OIDC flows до приёма запросов; сбой очистки останавливает
OIDC startup. Обычный restart сохраняет действующие auth sessions неизменного
профиля. Две уже отправленные разные попытки входа могут обе завершиться;
однократность гарантируется для одного state. Группы — snapshot на момент входа,
их отзыв в IdP не обновляет сессию автоматически. Немедленный refresh/revoke —
отдельное изменение; действует существующий TTL приложения.

Смена issuer/client/источника claims/mapping:

1. Закройте внешний доступ и остановите backend, сохранив прежний runtime env.
2. Запустите новым backend image CLI сначала без confirm для counts:
   `python scripts/invalidate_oidc_sessions.py --runtime-env-file /protected/runtime.env`.
   Файл должен явно задавать DATABASE_URL/DATABASE_URL_DEV; URL/credentials не печатаются.
3. Повторите с `--confirm-invalidate-oidc-sessions`. Удаляются OIDC sessions,
   включая legacy без id_token, и login flows; остальные providers/documents сохраняются.
4. Примените новый профиль, выполните startup/login smoke и откройте доступ.

При ошибке CLI доступ остаётся закрытым. В Kubernetes запускайте CLI в отдельном
контролируемом Job с защищённым env-файлом из Secret; не выполняйте его в Pod,
который остановлен этим же обслуживанием. Secret rotation также меняет flow/cache
binding, но сама по себе не отзывает уже созданные auth sessions.

## Восстановление и rollback

Restore выполняется в новый namespace/Compose project с новым APP_SECRET_KEY
в защищённом runtime Secret **до первого запуска** восстановленного image.
Backup kit не редактируется. Это не даёт отозванной после backup cookie снова
открыть доступ. После upgrade удалите восстановленные OIDC sessions/flows новым
CLI, затем smoke и открытие доступа с новым ключом. Сохранённые images и CA
используются сначала, последующий upgrade — через штатный lifecycle.

Binary rollback: закрыть доступ, остановить backend, новым image выполнить CLI,
вернуть сохранённый confidential profile/image через Recreate. Новая таблица
остаётся; production DB downgrade не выполняется. Старый image не знает новую
Alembic revision: при `lifecycle.py deploy` передайте `--migration-image` с
точным image установленного нового backend (для production — digest).
Preflight разрешает только этот установленный image и отказывает до lock/stop,
если image не совпадает или установки ещё нет. Migration Job использует новый
image с выбранными runtime env/CA; application запускается на сохранённом старом.
Этот параметр поддерживает только deploy и не доказывает совместимость произвольных
схем со старыми версиями. Для текущего additive OIDC изменения есть отдельная
runtime приёмка; остальные rollback требуют собственной проверки.
Backup считывает единственную Alembic revision прямо из БД, поэтому metadata
сохраняется и после совместимого возврата старого image. Public config на старой
версии не является поддерживаемым rollback.

## Проверка и журналы

Проверяйте реальный HTTPS login, роли/no-role, PKCE, UserInfo-only groups,
CSRF, session TTL и RP logout. Unit HTTP IdP не заменяет регистрацию настоящего
Keycloak. Приложение не пишет query auth endpoints и raw HTTP/Authlib messages
в OIDC context. Gateway/IdP/custom collectors проверяются отдельно.
ID token сохраняется серверно; RP logout передаёт id_token_hint браузеру в
redirect_url как узкое протокольное исключение. DB и backups приватны.

См. [план](superpowers/plans/2026-10-04-oidc-compatibility.md) и
[домашний стенд](KUBERNETES_HOME_ACCESS.md).
