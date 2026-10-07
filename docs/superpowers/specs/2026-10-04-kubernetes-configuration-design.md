# Общая конфигурация Kubernetes: контракт ближайшего пакета

Дата: 04.10.2026. Статус: контракт реализован в `codex/kubernetes-rollout` и
проверен локальной synthetic приёмкой; 316 тестов PASS, 0 skips.
[Отчёт](../reports/2026-10-04-kubernetes-configuration-acceptance.md) сохраняет
failed attempts, условия продолжения и границы TLS/rotation/runtime доказательств.
Целевая платформа, live модели, dependency audit и интеграция ветки остаются открытыми.
[Подробный план](../plans/2026-10-04-kubernetes-configuration.md) обновлён по результатам.

## Цель и границы

Одна версия chart, operator tools и одни application image digests должны
работать на двух поддерживаемых обезличенных профилях. Отличаются параметры
установки, ссылки на существующие Secret/PVC/ConfigMap и публикация; исходники
Python/Next и шаблоны не копируются для каждой установки.

Пакет детализирует вопросы 1/3/4/6/14 и документирование части вопроса 15 из
[критериев обобщения](../../KUBERNETES_PLATFORM_QUESTIONS.md) и задачи 3/5/8
[roadmap](../plans/2026-10-03-kubernetes-rollout.md). Сохраняются compact,
Recreate, один backend process, bundled storage, защита PROD и действующий
operator lifecycle. Локальные launchers и Compose bundled/external сохраняются.

Не входят OIDC public/PKCE/UserInfo, audit/outbox, новый `/metrics`, external
storage Kubernetes, реплики, HPA, смена CNI/Gateway и публикация в registry.
Настройка существующего confidential SSO входит только в проверку профилей.
Регистрации, Secrets и сертификаты задаются вне общего репозитория.

## Исходное поведение, проверенное по исходникам

Проверен текущий снимок `codex/kubernetes-rollout`: baseline `f551f5f` плюс
незакоммиченные изменения. Это анализ кода, а не новый runtime-прогон.

| Источник | Наблюдение и следствие |
|---|---|
| `deploy/helm/ai-knowledge/templates/application.yaml` | Backend HTTP probes и frontend TCP probes фиксированы; envFrom ConfigMap → Secret, затем явный env; Secret получает только backend |
| `values.yaml`, `values.schema.json`, `_helpers.tpl` | Ресурсы, scheduling, ссылки на Secret/PVC и Gateway уже параметризованы; schema не запрещает все неизвестные ключи |
| `templates/platform.yaml` | ListenerSet/HTTPRoute/VaultStaticSecret опциональны; TrafficPolicy отсутствует; TLS Secret и общий Gateway chart не создаёт |
| `scripts/kubernetes/lifecycle.py` | Preflight предшествует operation lock/остановке; Jobs наследуют backend env, но пока монтируют только data/work; отдельной команды restart нет |
| `backend/app/config.py` | Один Pydantic Settings loader: process env выше корневого `.env`, затем defaults; production image не должен содержать корневой `.env` |
| `frontend/next.config.js` | При сборке заданы `proxyClientMaxBodySize="100mb"` и `proxyTimeout=300000`; proxyTimeout внешних rewrites не устанавливает deadline fetch внутри API Route Handler |
| `frontend/src/app/api/[...path]/route.js` | Runtime `BACKEND_URL`, потоковое тело upload/ответа и cancellation; входящие forwarded headers удаляются |
| `backend/Dockerfile`, `frontend/Dockerfile` | Python/Node trust env уже заданы в image; mount/env через chart — недостающая возможность |
| `backend/app/api/documents.py` | `MAX_UPLOAD_MB` ограничивает файл в единицах 1024 × 1024 байт; multipart request больше файла |
| `backend/app/services/chat_stream.py` | Browser transport — NDJSON, heartbeat примерно раз в 5 секунд ожидания; upstream LLM SSE — другой участок |

[Portable-приёмка](../reports/2026-10-04-kubernetes-portable-acceptance.md)
подтвердила текущий compact lifecycle. Она не подтверждает новые overrides,
максимальный upload или совместимость новых CA/TrafficPolicy профилей.

## Выбор подхода

1. Копировать chart или пересобирать приложение под каждую платформу — создаёт
   расходящиеся поставки и не выполняет критерий одинаковых артефактов.
2. Разрешить произвольные env/probe/policy YAML — проще добавить, но невозможно
   проверить неизменность health, ownership и production protections.
3. Расширить существующий chart типизированными настройками и одним опциональным
   adapter, используя существующий Settings loader — выбранный вариант.

Поддержка другого Gateway adapter добавляется отдельным контрактом. Наличие
Gateway API не доказывает поддержку ListenerSet, route timeouts или kgateway CRD.

## Матрица и precedence

Базовая схема: один release на namespace, compact с backend:8000/frontend:3000,
PostgreSQL 17/Qdrant и существующие PVC. Модели выбираются явно:
stub/unavailable — только non-PROD, PROD — live. DEV/TEST/PROD сохраняют
`ENVIRONMENT=production`; реальные model endpoints в DEV/TEST не считаются
общим требованием. Existing image-digest guards сохраняются.

Helm: defaults → перечисленные `-f` слева направо → операционный
`application.enabled` в lifecycle. Backend process env: ConfigMap → Secret →
явные chart-controlled env. Pydantic: process env → `.env` → defaults.
Секреты не передаются через `--set`. Приложение не получает новый config loader.

Guards для environment/model/storage/workers сохраняются; controlled параметры
нельзя подменять через Secret. Stub endpoints/profile/search заданы явно и
выигрывают у envFrom. Документация отличает ignored override от принятого.
Frontend не получает backend Secret; SSO callbacks остаются backend Settings.

## Числовые параметры probes

Новый subtree `probes.<backend|frontend>.<startup|liveness|readiness>` допускает
только `initialDelaySeconds`, `periodSeconds`, `timeoutSeconds`,
`failureThreshold`. Первое — integer ≥ 0, остальные — integer ≥ 1.
На всех уровнях нового subtree запрещены дополнительные ключи; null, boolean,
числа в строках, дроби и попытки подменить `httpGet`/`tcpSocket` отклоняются.
`successThreshold=1` фиксирован; нового `enabled` нет.

| Контейнер / probe | initialDelay | period | timeout | failureThreshold | Проверка |
|---|---:|---:|---:|---:|---|
| backend startup | 0 | 5 | 2 | 60 | `/health/live`:8000 |
| backend liveness | 0 | 10 | 2 | 3 | `/health/live`:8000 |
| backend readiness | 0 | 10 | 8 | 3 | `/health/ready`:8000 |
| frontend startup | 0 | 5 | 1 | 60 | TCP:3000 |
| frontend liveness | 0 | 10 | 1 | 3 | TCP:3000 |
| frontend readiness | 0 | 5 | 1 | 3 | TCP:3000 |

Явно записанные Kubernetes defaults сохраняют прежнее поведение отсутствовавших
полей. Не вводится искусственное требование `timeout ≤ period`. Настройка
частоты не доказывает скорость старта на медленном CSI и не превращает TCP check
в проверку upstream/backend. Health paths, порты и команды остаются фиксированными.
Не включать глобальный `additionalProperties=false` без инвентаризации всех
существующих values: строго ограничиваются новые поддеревья.

В первом пакете сохраняются текущие ожидания оператора: application readiness
600 секунд, остановка application 180 секунд. Preflight до acquire проверяет
совместимость желаемых probes с этими бюджетами. Для каждого контейнера берутся
`S = startup.initialDelaySeconds + startup.failureThreshold × max(startup.periodSeconds, startup.timeoutSeconds)`
и аналогичный `R` для readiness. Требование продукта: `max(S + R) + 60 ≤ 600`;
60 секунд — резерв для запуска/планирования, не гарантия CSI или Kubernetes.
Default probes проходят эту проверку. Контейнеры стартуют параллельно, поэтому
их бюджеты не суммируются. Фактическая готовность всё равно проверяется runtime.

Для остановки требуется `terminationGracePeriodSeconds + 30 ≤ 180`; при upgrade
проверяются и текущий workload, и желаемый. Оператор останавливает текущий Pod,
поэтому проверки только нового grace недостаточно. Несогласованные значения
отклоняются с именами полей и требуемым бюджетом; автоматического увеличения
ожиданий или force-delete нет. Поддержка более длительного старта/остановки
потребует отдельного согласованного изменения operator timeouts.

## Gateway timeouts и опциональный adapter

`gateway.requestTimeout` / `backendRequestTimeout` сохраняют defaults `300s`.
Поддерживаемый формат — положительное целое с одной единицей `ms`, `s`, `m`
или `h`, например `300s`, `5m`, а также специальное значение `0s` при явном
разрешении ниже. Если оба timeout положительны, после приведения к миллисекундам
проверяется `backendRequestTimeout ≤ requestTimeout`. Отрицательные, дробные и
составные durations отклоняются с понятным именем поля. Это ограниченный формат
продукта; остальные валидные Gateway API durations не объявляются поддерживаемыми.
Старые нестандартные values явно приводятся без изменения длительности.
Поддерживаемый single-unit token должен также проходить Gateway API schema:
числовой компонент 1–5 цифр, длина до 32 символов. Точная schema установленной
версии проверяется при приёмке; неограниченная длина целого не допускается.

Новый boolean `gateway.allowUnboundedTimeouts` по умолчанию `false`. Любое `0s`
требует его явного включения, `gateway.enabled=true` и включённого ниже
TrafficPolicy с конечным stream-idle timeout. Ноль означает отключение своего
таймера, а не длительность меньше любого положительного значения. При
`requestTimeout=0s` конечный backend timeout допустим; при нулевом backend
timeout конечный request timeout продолжает ограничивать весь запрос;
при обоих `0s` остаются idle timeout, cancellation и ограничения приложения.
Эти сочетания проверяются отдельно. Default `300s/300s` сохраняется.

Это явный профиль длительных потоков. Текущий HTTPRoute использует prefix `/`,
поэтому настройка действует на весь frontend route, а не только чат; оператор
должен видеть эту область действия. Разделение таймеров по API routes в пакет
не входит. [Gateway API описывает `0s`](https://gateway-api.sigs.k8s.io/guides/user-guides/http-timeouts/)
как отключение request timeout; поведение обоих таймеров сверяется с CRD и
фактической версией контроллера при приёмке. Heartbeat не отменяет конечный
общий deadline и не доказывает, что внешний LB допускает столь длинный поток.

Новый `gateway.trafficPolicy`:

| Ключ | Default | Контракт |
|---|---|---|
| `enabled` | `false` | Boolean; включение требует `gateway.enabled=true` |
| `adapter` | `kgateway` | Единственное поддерживаемое значение пакета |
| `streamIdleSeconds` | `60` | Integer ≥ 10; запас относительно heartbeat 5s — решение продукта |

Неизвестные ключи subtree запрещены. Выключенный adapter не рендерит
TrafficPolicy и не проверяет его API/RBAC. Включённый создаёт только namespaced
`gateway.kgateway.dev/v1alpha1` TrafficPolicy `frontend-stream` с targetRef на
HTTPRoute `frontend` в том же namespace и `timeouts.streamIdle: "<N>s"`.
Он не задаёт request timeout повторно, не меняет общий Gateway, identity route,
IP headers, retries или buffering. API version и target не произвольные values.

Основание формы ресурса — официальная документация kgateway 2.4,
[idle stream timeouts](https://kgateway.dev/docs/envoy/latest/resiliency/timeouts/idle-stream/):
route targetRef и `timeouts.streamIdle`. Документация latest не заменяет проверку
schema patch release. Первое лабораторное сочетание — уже проверенный kgateway
2.4.5; CRD schema, admission, attachment status и фактическое поведение
проверяются перед утверждением adapter. Другие версии не включаются автоматически.

Preflight проверяет конкретный resource kind в discovery и необходимые права;
существование API group недостаточно. TrafficPolicy проверяется server-side
dry-run до operation lock/остановки/Helm mutation. Проверка не выводит rendered
Secret, CA bundle, upstream responses или закрытые адреса. Failure оставляет
приложение запущенным; выключенный adapter не зависит от kgateway.

## Upload, streaming и остановка

Документировать каждый слой отдельно: файл в backend, multipart request,
Next config при сборке, Gateway/LB limits, браузерный NDJSON heartbeat,
provider first-token/idle timeout и deadline конкретного chat flow.
Не выводить общий timeout из одного `LLM_MAX_TOTAL_TIMEOUT_SECONDS`: multi-call
flow и очередь имеют собственную семантику. Heartbeat не отменяет total timeout.
`experimental.proxyTimeout` применяется механизмом внешнего rewrite Next
(здесь `/health`); это не автоматически 300-секундный предел `/api` Route Handler,
который сам вызывает `fetch`. Для него отдельно проверяются Node/HTTP client,
Gateway и app deadlines. Применимость body cloning limit также проверяется
на фактическом пути upload, а не по одному комментарию в next.config.js.

Пакет не добавляет runtime override для Next build settings и не обещает upload
выше 100mb изменением backend env. Применимость experimental limits к фактическому
API route подтверждается измерением. На границе файла 100 MiB проверяется полный
multipart request; единицы `100mb` и MiB не считаются эквивалентными без проверки
используемой версии. Если поддерживаемый лимит не проходит, критерий остаётся
failed; отдельное изменение frontend проводится явно, без уменьшения лимита
ради зелёного теста.

`terminationGracePeriodSeconds` сохраняет default 60/minimum 30. Это время для
остановки/recovery, а не обещание завершить любую job/LLM сессию. Проверяются
cancellation и recovery после restart. `Recreate` не заменяет доказанное
завершение старого writer в operator lifecycle.

## Существующие Secret, CA и ротация

Existing Secret/PVC/image mirrors/pullSecrets переиспользуются. BuildKit CA
механизм сохраняется для package registry/build. Registry TLS на node/builder
настраивается независимо от trust внутри application containers.

Для различающегося runtime trust при одинаковых digests добавить subtree
`trust.existingConfigMap` (string, default пусто, DNS-compatible name).
Пустое значение не меняет mounts/env. Непустое ссылается на ConfigMap того же
namespace с фиксированным ключом `ca-bundle.crt`: публичные PEM certificates,
полный trust bundle со стандартными корнями, без private keys. ConfigMap должен
иметь `immutable: true` и версионированное имя; смена содержимого означает новую
ссылку. Других ключей
subtree нет. Chart не создаёт bundle и не читает его содержимое при Helm render;
preflight проверяет immutable, наличие ключа и пригодность PEM без вывода.
При обновлении проверяются также CA references текущего workload, необходимые
для backup. PEM parse сам по себе не доказывает полноту корней и доверие endpoint.

Bundle read-only монтируется по фиксированному пути `/etc/okf/trust/ca-bundle.crt`
в backend/frontend; имя volume — `runtime-trust`. Backend получает явные `SSL_CERT_FILE` и
`REQUESTS_CA_BUNDLE`, frontend — `NODE_EXTRA_CA_CERTS`. При включении trust эти
переменные chart-controlled, их дубликаты в generic runtime config/Secret
отклоняются, чтобы исключить расхождение проверки TLS.
HTTPS через фактические SSO/model adapters проверяется обязательно: наличие env
не доказывает, что библиотека использовала bundle. TLS verification не
отключается. Неправильный тестовый CA должен дать отказ.

Тот же контракт обязателен для migration, backup, restore и служебных Jobs,
создаваемых `Manager.job()`: CA env, фиксированный read-only mount и соответствующий
ConfigMap volume переносятся вместе из выбранного Pod template. Для backup
старого workload используются его image/env/CA reference; для новой миграции —
новые. Нельзя копировать все volumes подряд или добавлять второй шаблон Jobs:
переносится только явно разрешённый trust volume вместе с существующими data/work.
При выключенном trust дополнительного mount/env нет. Job с заданным CA env и
отсутствующим trust mount должен отклоняться до запуска.

Ротация в первом пакете использует существующий `lifecycle.py deploy`:

1. Подготовить новый immutable CA ConfigMap, сохранить старый и обновить ссылку
   в защищённых values. При смене issuer сначала использовать overlap bundle
   со старыми и новыми корнями; старый endpoint должен оставаться пригодным
   для возврата. Проверить новый bundle до остановки приложения; PEM parse
   не заменяет HTTPS-проверку фактических adapters.
2. Выполнить preflight → lock → stop → backup старой версии → штатные
   migration/start с новыми values. Это полноценный deploy с ценой backup и
   миграционного шага; отдельной команды restart пока нет.
3. Проверить HTTPS/SSO/model adapters и Job trust. Старый bundle сохраняется
   как минимум до успешной приёмки и далее на срок поддержки соответствующих
   backup/recovery комплектов. Chart его не удаляет.
4. При неудачной CA-only ротации повторный deploy с прежней ссылкой допустим,
   только если текущий backup path работоспособен и старые endpoints доступны.
   После failed migration/start новая конфигурация уже могла примениться;
   сначала сохранить evidence; если lock остался, проверить отсутствие
   writer/живых Jobs перед штатным recover-lock. При необходимости сначала
   штатно остановить workload с ожиданием. Если безопасный redeploy невозможен, восстановить
   pre-change backup matching комплектом в новый namespace/PVC с прежними
   trust/config; переключать доступ только после проверки копии. Не выполнять
   automatic rollback/unlock, повторный start/миграцию или force-delete для обхода отказа.
   Restore в новый namespace не требует снятия lock исходного окружения.
   Внешний HTTPS smoke может упасть уже после успешного deploy/release-lock;
   отсутствие lock не означает успешную TLS-приёмку.
   Это не автоматический Helm/SQL rollback; при изменении схемы БД действует
   отдельный проверенный restore contract.

Recovery комплект фиксирует CA reference и checksum публичного bundle, а также
способ защищённого хранения/получения прежнего bundle. Restore в новый namespace
заранее получает эквивалентный ConfigMap; сохранённое имя без доступного содержимого
не считается достаточным для восстановления. Private keys и runtime Secret
содержимое в этот манифест не попадают. Смена Secret также применяется через
deploy; ротация пароля существующей БД остаётся отдельной процедурой.
Обновление mounted файла не считается загрузкой новых настроек процессом.
Secret lookup/checksum с переносом содержимого в render/CI не вводится.
EnvFrom ссылки старого Pod не являются снимком содержимого Secret/ConfigMap.
Между preflight и backup их содержимое должно сохраняться: версионированные
ссылки либо окно без параллельной внешней ротации; прежние refs удерживаются
до завершения операции. Восстановление runtime Secrets обеспечивается отдельно.

## Переход release/consumer на schema 2

Новые values выпускаются с `config_schema=2`. Изменение выполняется согласованно
в `scripts/kubernetes/release.py`, `operator_tools.py`, доверенном
`deploy/ci/operator-tools/consume.py` и matching toolbox image. Сейчас bootstrap
в toolbox жёстко принимает schema 1; обновление только архива tools не позволит
загрузить schema 2. Новая версия bootstrap должна поступать из проверенного
toolbox, а не исполняться из ещё не проверенного архива.

Для первого пакета новый комплект объявляет `[2]`; поддержку `[1, 2]` не добавлять
без отдельной проверки. Матрица: старый consumer + release 1 — прежний baseline;
старый + release 2 — отказ до мутаций; новый + release 2 — положительная приёмка;
новый + release 1 — явный отказ. Mismatch chart/tools/bootstrap/toolbox identities
также отклоняется. Прямой CLI `release.py overlay` проверяет config schema
до создания output, а не полагается только на вызов через bootstrap.

Формат backup версионируется независимо от config schema: новые backups имеют
`manifest.format=2`, в том числе при выключенном trust; release manifest сохраняет
свой прежний `format`. Это необходимо, поскольку исторический backup reader
принимает дополнительные файлы format=1 и мог бы проигнорировать новое trust
поле. Комплект 1 отказывает backup format=2; комплект 2 отказывает legacy format=1.
Старые архивы не переписываются. Новый `release.json` внутри backup обязательно
содержит `trust` (identity или null). При identity публичный `trust-bundle.pem`
обязателен и его hash совпадает одновременно с manifest files и trust.sha256;
при null PEM отсутствует. Missing/лишний bundle, противоречивые hashes и invalid
PEM отклоняются до lock/import. Backup format=2 выпускается только вместе с
matching tools/bootstrap, а не отдельным промежуточным обновлением оператора.

Для старого backup сохраняется исходный release 1 комплект (chart, images,
operator tools, trusted bootstrap/toolbox и защищённая конфигурация), им выполняется
restore в новый namespace, затем проверенный upgrade комплектом 2. Для backup
release 2 используется комплект 2 с соответствующим trust. Проверяются оба пути
на непустых данных; наличие старого image без старых tools/конфигурации недостаточно.

## Два обезличенных профиля и критерии завершения

| Параметр | A | B |
|---|---|---|
| Namespace | `okf-profile-a` | `okf-profile-b` |
| Публикация | Gateway on; HTTPS, TrafficPolicy off | Gateway on; другой HTTPS hostname, TrafficPolicy on |
| Артефакты | Одинаковые pinned digests, chart и operator tools | Те же артефакты без пересборки |
| Runtime | Non-PROD stub, production protections | Non-PROD stub, production protections |
| Конфигурация | Default probes/resources; собственные Secret/PVC/callback/CA reference | Другие probes/resources и ссылки на Secret/PVC/callback/CA |

`.invalid` hostnames — только для render. Для runtime lab нужны разрешаемые
локальные имена и сертификат; адреса/учётные данные остаются в игнорируемых
артефактах. A и B используют независимые БД, индексы и PVC. SSO использует существующий confidential
flow, не утверждая готовность public IdP. Оба профиля проверяются на одной
поддерживаемой версии Gateway; это доказательство переносимости настроек,
а не совместимости разных Gateway vendors. Минимальный профиль с `gateway.enabled=false`,
`vault.enabled=false` и `trust.existingConfigMap=""` проверяется отдельно:
render/preflight без platform resources/API requests плюс runtime smoke
startup/ready/upload/поиск через port-forward. Проверяются прежние image trust
defaults и отсутствие новых env/mount; baseline smoke переиспользуется.
Port-forward сам по себе не доказывает Secure-cookie/SSO flow.

Критерии пакета, проверенные в указанном объёме отчёта:

- [x] Defaults и старые профили сохраняют probes и production guards.
- [x] Timing overrides меняют только timing; altered path/port/command/disable,
  неверные типы и неизвестные ключи отклоняются до изменения ресурсов.
- [x] Default probes проходят budget checks; чрезмерные startup/readiness/grace
  отклоняются до остановки. Проверяется grace текущего и желаемого Pod.
- [x] Invalid duration pair, enabled adapter без Gateway/CRD/RBAC и invalid CA
  отклоняются до acquire/stop/apply; disabled adapter не проверяет kgateway API.
- [x] Helm render/tests не выводят секреты; unsupported replicas/workers,
  external storage и PROD synthetic profiles продолжают отклоняться.
- [x] HTTPS с правильным CA проходит, с неправильным падает; backend использует
  trust в SSO/model adapter, Node — в проверяемом HTTPS клиенте. Readiness и
  probe со своим отдельным CA не подменяют этот gate.
- [x] CA mount/env согласованы в application и migration/backup/restore Jobs;
  отсутствующий/изменяемый bundle, private key или потерянный Job mount дают отказ.
- [x] A/B проверены на одном комплекте: upload/поиск, NDJSON heartbeat,
  upstream SSE, отмена и restart/recovery. Для B проверены route/policy conditions
  и Gateway flow; port-forward не подменяет эту проверку.
- [x] Повторный deploy, CA ротация/возврат прежней ссылки и непустой backup/restore
  проходят с включённым trust. После restore повторяются поиск и HTTPS-проверки;
  исходный namespace/данные и необходимые старые bundles сохраняются.
  Проверен отказ HTTPS после apply корректного PEM с неверным issuer и recovery
  из сохранённого backup; зелёная readiness не скрывает этот отказ.
- [x] Минимальный профиль без Gateway/Vault/runtime trust проходит runtime smoke,
  а не только render. Безопасность cookies/SSO оценивается отдельно через HTTPS.
- [x] `0s` без explicit opt-in/idle policy отклоняется; допустимые сочетания
  нулевого/конечного timeout проверены. Активный поток живёт дольше тестового
  конечного request timeout, stalled stream закрывается idle timer, cancel работает.
- [x] Граница upload/multipart измерена; короткий smoke не закрывает maximum
  upload/deadline gate. Неизвестные внешние LB limits отмечены unverified.
- [x] Schema 2 согласована с trusted bootstrap и toolbox; вся матрица consumer
  1/2 × release 1/2 и identity mismatch проверена до мутаций. Непустой backup
  release 1 восстанавливается старым комплектом с последующим upgrade на 2.
  Backup format 1/2 имеет отдельные negative compatibility tests; format=2
  с missing/противоречивым trust отказывает до начала восстановления.
- [x] Сохранены local/Compose regression и lifecycle/backup/restore guards.
  Открытый dependency audit gate не объявляется закрытым этим пакетом.

## Порядок детализации и проверки

После рассмотрения спецификации implementation plan выделяет задачи:
probes/schema/operator budgets → timeout/TrafficPolicy/preflight → runtime trust
application/Jobs и rotation/recovery → schema 2/bootstrap/toolbox compatibility →
документация, минимальный профиль и расширенная A/B acceptance. Точные тесты/команды фиксируются в плане;
отдельная реализация OIDC/audit/external не присоединяется.

Существующие точки изменения: `values.yaml`, `values.schema.json`,
`templates/application.yaml`, `templates/platform.yaml`, `templates/_helpers.tpl`,
`lifecycle.py` (preflight, job, backup/restore metadata),
`scripts/kubernetes/{release,operator_tools}.py`,
`deploy/ci/operator-tools/{consume.py,Dockerfile}`,
`backend/tests/test_kubernetes_chart.py`, lifecycle/release/toolbox tests.
Новый acceptance runner добавляется после проверки
возможностей `home_probe.py`/portable acceptance; второй operator lifecycle
не создаётся. При исполнении trust/policy изменения отражаются в `SECURITY.md`.

Оценка 4–7 ч.д. остаётся предварительной общей оценкой roadmap, а не календарным
сроком или выполненной работой. Пересборка из-за frontend лимита, новый Gateway
adapter и ожидание платформенных согласований учитываются отдельно.
После декомпозиции трудозатраты уточняются с учётом CA для Jobs, ротации,
матрицы bootstrap/toolbox и межверсионного восстановления; исходный диапазон
не служит основанием исключить эти проверки.
Коммит, интеграция ветки, публикация и окруженческий rollout не выполнены.
