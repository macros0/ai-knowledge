# Приёмка профилей Kubernetes replicas

Статус на06.11.2026: выбранная учебная приёмка завершена; общая customer/release
qualification **не завершена**, certified-профилей нет.
[Матрица JSON](../deploy/kubernetes/replicas/support-matrix.json) хранит семь
предусмотренных вариантов: compact/local, frontend split, managed/RWX,
managed/S3, all-external, mixed и resilient. Поле `tested` означает наличие
ограниченных фактических испытаний, а не разрешение production-развёртывания.
Реализация адаптера или успешный Helm render не заменяет provider acceptance.

[Финальный CI f1b38e8](superpowers/reports/2026-11-06-replica-s4-final-backend-ci.json):
7137 PASS/221 прежний optional SKIP/0 failures/errors/87,72% coverage.
[Legacy→NAS4.0](superpowers/reports/2026-11-06-replica-s4-rwx-native-closure.json)
подтвердил process exit23, тот же resume без перезаписи, исходный диагностический
cutoff/event, held handoff и работу2/2/2 с download/search/export на четырёх
маршрутах. Стенд выключен/held2/4 PVC сохранены; обе capacity loans возвращены.
[NAS-target restore closure](superpowers/reports/2026-11-06-replica-synology-target-closure.json)
закрывает отдельную репетицию восстановления уже переносимого корпуса.
Серверные capabilities, физические failure domains и доверенный release issuer
остаются привязаны к конкретной поставке. Production backup schedule выключен
по выбору пользователя; производственного стенда нет.

## Frontend-split: допуск живого стенда

Для явного `deployment_mode: frontend-split` общий runner с `--suite frontend
--live` по умолчанию проверяет только topology: две frontend и один combined backend, точный
namespace UID/installation label, controller ownership, Service endpoints,
native image IDs, отсутствие frontend PVC/hostPath/дополнительных credentials.
Manifest дополнительно задаёт `namespace_uid`, `runtime_secret` и
`expected_image_ids: {backend: "sha256:…", frontend: "sha256:…"}`. Значения image
IDs выбирает оператор из проверенных image receipts. Namespace должен иметь
`okf-installation-id`, совпадающий с `installation_id` manifest.

Этот probe ничего не загружает и не удаляет. Явный staged-режим общего runner
использует `--frontend-stage`, защищённый `--frontend-journal` и
`--apply-frontend` для нового действия. При неопределённом ответе используется
`--observe-frontend` с тем же журналом и этапом: POST/DELETE не повторяется.
[Контракт и все 12 этапов](superpowers/plans/2026-11-04-frontend-functional-runner.md)
для `owned-simulation-local-v1` ограничены свежим basic simulation/local fixture с точными namespace UID,
installation, native image IDs и проверенными собственными TLS providers.
Cookies остаются в защищённом каталоге VM. Приём чужих passed receipts запрещён.

[Новый прогон через общий runner](superpowers/reports/2026-11-05-frontend-native-acceptance.json)
прошёл все 12 этапов, включая два NDJSON потока, один model-gated UID DELETE,
замену Pod, SHA/поиск/ZIP и отзыв сессии. `selected_frontend_case_passed=true`,
но в этом simulation-прогоне qualification остаётся false: `frontend.production_csrf` и
`frontend.gateway_sso` pending. Ненулевой код CLI в этом случае означает
незавершённую qualification; проверяйте результат выбранного этапа отдельно.
Прежняя [отдельная репетиция](superpowers/reports/2026-11-04-replica-frontend-split.json)
сохранена неизменной; её отчёт не подменяет новый терминальный native proof.
Simulation исключён из production CSRF guard, поэтому его успех не закрывает
CSRF/SSO gate. На distributed topology общий `--suite all` не запускает
combined-backend frontend-split probe.

Gateway prerequisite теперь [фактически установлен и проверен](superpowers/reports/2026-11-05-replica-gateway-platform.json)
по отдельному допуску пользователя: Gateway API1.6.2/kgateway2.4.5,18 established
CRD, non-root controller/точный image/Accepted GatewayClass. Controller выключен,
capacity loan возвращён; CRD/RBAC/admission policy сохранены. Controller создал
свой namespace OAuth2 HMAC Secret; в отчёте только метаданные. ClusterRole имеет
широкие права, discovery selector не является RBAC-границей.

Отдельный [OIDC HTTP-компонент](superpowers/reports/2026-11-05-frontend-oidc-http-ci.json)
прошёл99/99 одинаковых Windows/Linux cases с настоящим loopback TLS и HTTP wire
double. Он проверяет bounded transitions/state/issuer/client, secure cookies,
CSRF и logout/unknown-ACK без повтора. Последующая интеграция и
[новый полный прогон](superpowers/reports/2026-11-05-frontend-oidc-native-acceptance.json)
на fdfae89 прошли все12 этапов уже через настоящий собственный Keycloak/Gateway.
В этой выбранной basic/local recipe `frontend.production_csrf` и
`frontend.gateway_sso` passed: проверены secure session/state/issuer, два отказа
CSRF при живой сессии, два frontend slot и обычный Service, SHA/поиск/ZIP,
model-active UID loss без replay, replacement и завершение app/IdP-сессии.
Это native HTTP-проверка выбранного realm, а не приёмка браузера или IdP заказчика.

Для этой recipe задаются `functional_recipe: owned-keycloak-local-v1`,
`oidc_access_namespace`, `oidc_access_namespace_uid`, `oidc_platform_namespace_uid`,
`oidc_controller_uid`, `oidc_gateway_class_uid`, `oidc_username` и
`oidc_expected_images` с точными runtime digests Keycloak/Gateway/controller.
Пароль, cookies и client secret в manifest не передаются: producer читает только
admitted synthetic Secrets внутри VM после проверки namespace/route graph.
Остаются те же12 этапов, `--apply-frontend` для нового действия и
`--observe-frontend` для его неопределённого исхода. Изменение capsule или
binding запрещает продолжение прежнего execution; его результаты сохраняются.

Текущий полный Linux CI:7003 cases/6782 passed/221 неизменный optional skip,
0failures/errors,87,70% coverage. Исходники app/frontend/parser не отличаются
от проверенных runtime-образов65d6ee2. Новый fixture выключен:8 контроллеров0,
3 процесса закрыты,3 PVC и корпус сохранены; discovery-метки сняты. Обе исходные
топологии2/2/2 возвращены с runtime/PVC/9-table hash proof. Общая qualification
и certification остаются отдельными: проверенный native proof закрывает только
выбранные checks, без подписанного доверенным issuer release bundle.

## Привязка доказательств

Для проверенного учебного managed/S3 варианта сохранены точные topology,
ownership/recipe/version/image каждого storage-компонента, фактические runtime
image IDs, common identity и model/auth режимы. `binding_sha256` вычислен как
SHA-256 UTF-8 JSON объекта `binding` с `sort_keys=True,separators=(',',':')`.
Изменение любого из этих входов требует новой приёмки соответствующего профиля.
Матрица пока является инвентарём доказательств: policy validator, проверка
подписанных файлов receipts и read-only интеграция с runner реализованы;
реальные доверенные issuers/producers ещё не подключены. Во всех записях
`certification=null`, `qualification_complete=false`, release остаётся incomplete.

Учебная topology — один kind node на одной VM, local-path/RWO. Она допускает
проверку взаимодействия реплик и восстановления в новые PVC, но не подтверждает
переживание потери физического хоста/диска. Simulation auth, stub LLM и fake
embeddings не подтверждают реальные Gateway/IdP/model providers заказчика.

## Policy validator и доверие к evidence

`backend/test_scripts/replica_acceptance_report.py` проверяет строгую привязку
distributed S3/RWX-профиля к topology, ownership/recipe/version/image и common
runtime identity. Фиксированный набор обязательных checks нельзя сократить
входным списком: missing/pending/skipped/failed или несовпадающие kind/binding
исключают сертификат. Restore дополнительно требует `restore_verified=true`
и live evidence; unit/integration/CI его не заменяют. Дубли checks, неизвестные
поля и неверные hashes отвергаются без вывода исходных секретных значений.
Для external ownership проверяется каждый выбранный внешний компонент;
resilient требует независимых доменов и live node/storage/domain-restore checks.
Kind не получает физический resilient-сертификат даже при заявленных нескольких
узлах. [Policy tests](superpowers/reports/2026-11-02-replica-certification-policy.json):
33 новых случая и14 прежних runner-safety tests прошли на Windows.

Чистая policy принимает уже проверенные metadata. Отдельный
`backend/test_scripts/replica_evidence.py` теперь проверяет фактические SHA
ограниченных файлов, строгий формат и Ed25519-подписи issuer с независимо
выбранными полномочиями check/producer. Terminal bundle подписывает весь набор
receipts, а scope включает уникальную acceptance session и namespace UID/installation.
Общий runner поддерживает только явно выбранный read-only режим всех checks без
live/apply действий. [Формат, trust boundaries и команда](REPLICA_EVIDENCE_FORMAT.md).
Подпись удостоверяет harness, а не воспроизводит удалённые native observations;
их достоверность остаётся ответственностью доверенного producer. Реальные
issuers и terminal producers ещё не подключены, исторические отчёты не
подписываются задним числом. `certificate_applies` проверяет лишь актуальность
binding/policy и форму, поэтому не заменяет повторную проверку trust/файлов.
Production-разрешения, автоматическая смена inventory и сетевые действия не
выполняются. Фактическая матрица сохраняет `certification=null` для всех профилей.

[Локальная проверка verifier/runner](superpowers/reports/2026-11-02-replica-trusted-evidence.json):
186 passed,1 POSIX-only skip, включая прежние runtime-контракты; project check
прошёл. После явного разрешения пользователя выполнен
[Linux follow-up](superpowers/reports/2026-11-02-replica-evidence-alerts-linux.json):
295 passed,0 skips/errors/failures, включая FIFO и symlink. Все2395 исходных файлов
снимка остались неизменными; backend/parser/operator code совпадает с788a99b.
Первый read-only checkout дал295 setup errors на создании служебных каталогов;
он сохранён отдельно. Успешный запуск использовал свежую writable-копию того же
разрешённого архива при network-none/read-only rootfs/non-root. Реплики возвращены
к2/2/2,9 canonical tables/PVC/identity сохранены. Это выбранные cohorts,
не полный backend suite и не фактическая сертификация профиля.

## Подтверждённые проверки текущих источников

- [Schema-changing recovery](superpowers/reports/2026-11-02-replica-predecessor-schema-recovery.json):
  реальная rpl04 копия при выключенном источнике, новая schema/identity/scoped
  credentials, отзыв старых сессий, явный release,2/2/2 Ready и семь smoke checks.
  Два прежних документа проходят поиск/download SHA после двух новых загрузок.
- [Parser/frontend CI](superpowers/reports/2026-11-02-replica-current-artifact-ci.json):
  557/557 doc-parser tests без пропусков и новая standalone production-сборка
  frontend с проверенными19 routes, `/health` и revision stamp. Все97 parser и366
  frontend файлов сверены; Git подтверждает неизменность обоих деревьев между
  frozen snapshot и указанной проверяемой revision. Контейнеры network-none,
  read-only, non-root; рабочие каталоги/сервисы не используются.
- [Backend baseline](superpowers/reports/2026-11-02-replica-baseline-backend-ci.json):
  6093 случая старой revision с точным совпадением collected/executed IDs обоих
  shards. Это исторический результат, полный backend CI итоговой версии открыт.

После parser/frontend CI временно остановленные приложения учебного target
возвращены к2/2/2; все9 canonical-table hashes, PVC и runtime identity сохранены.
Сырые CI-логи и build artifacts остаются на учебной VM; публичные отчёты содержат
ограниченные metadata/counts/SHA. Новая frontend-сборка этим тестом не развёртывается.

## Команды и незакрытые gates

Обязательные локальные проверки из корня: `node scripts/check-project.mjs`.
Полный backend suite: из `backend/` — `python -m pytest tests/ -q`; doc-parser —
та же команда из `doc-parser/`, с отдельным `--basetemp` для каждого параллельного
запуска. Production frontend build выполняется из изолированного `frontend/`
прямым `node node_modules/next/dist/bin/next build`. Разрешённый `npm audit`
требует доступного registry; ошибка сети не считается успешной проверкой.

`python backend/test_scripts/run_replica_acceptance.py --suite all --manifest PATH`
без live inputs проверяет только синтаксическую изоляцию и возвращает failure
при pending gates. Для реальных runtime действий требуются явные `--live`,
разрешённый namespace/context и protected manifest; дополнительные fault stages
имеют собственные operation journals и apply/resume параметры. Все required
checks должны относиться к одной принятой конфигурации. Отдельные успешные
отчёты разных image/identity нельзя объединять в один зелёный полный прогон.

На учебном ПК проверены отдельный защищённый каталог ключа и локальная доставка
backup error/age alerts: [key recovery](superpowers/reports/2026-11-02-replica-backup-key-recovery.json),
[notification drill](superpowers/reports/2026-11-02-replica-local-backup-alerts.json).
Production key custody/monitoring этим не квалифицируются.

Выбранные RWX и mixed NAS application/full-storage/retry проверки завершены;
NAS-source→S3 recovery и полный e42007a backend CI подтверждены в
[сводной матрице](REPLICA_ACCEPTANCE_MATRIX.md). Это отдельные результаты,
а не один полный прогон всех профилей. Открыты остальные external provider и
NAS-target relocation recipes, длительные outages, подключение реальных моделей,
IdP/Gateway и доверенных evidence producers. Для resilient дополнительно обязательны
независимые физические домены, выбранные storage recipes и реальные испытания
потери узла/хранилища. Production backup schedule остаётся выключенным по
решению пользователя; его включение не является автоматическим итогом CI.

Сверка inventory от05.11 закрывает устаревшие записи о непроведённых compact
adoption/independent restore, RWX application/target restore, mixed runtime/
independent restore, S3 application full и full backend CI. Каждая запись матрицы
ссылается на неизменный отчёт с SHA; исторический runtime binding сохраняется.
`tested` у шести вариантов означает ограниченную учебную проверку, а не
qualification всей текущей версии. Resilient остаётся planned.

Текущие PostgreSQL/Qdrant/model interruption checks объединены в
[отдельный нативный отчёт](superpowers/reports/2026-11-05-replica-provider-matrix.json).
Он связывает10/14 исходных этапов с отдельным parent-bound физическим
восстановлением модели. [CI и закрытие прогона](superpowers/reports/2026-11-05-replica-provider-matrix-closure.json)
подтвердили7317 cases/7096 passes/221 прежний optional skip/0 failures,
87,70% coverage на6b41f85, выключенный fixture и возврат обоих займов с
исходными2/2/2/runtime/PVC/девятью таблицами. Истёкшие листья TLS обновлены
с прежними CA и ключами; проверки TLS не отключались. Реальный owned CSRF/Gateway/SSO producer также
[проверен](superpowers/reports/2026-11-05-frontend-oidc-native-acceptance.json).
Доступная дальнейшая работа на учебном стенде — фактическое восстановление в NAS
по [отдельному NFSv4.0-рецепту](superpowers/plans/2026-11-05-synology-target-relocation.md)
и финальный CI/аудит после него. Компонент bbced15 прошёл74 одинаковых
Windows/Linux cases; его будущий target ещё не принят. Строгий NFSv4.1
рецепт сохранён. Для окончательной
приёмки нужны независимо выбранный доверенный issuer и, когда появятся,
customer provider/Gateway/IdP inputs. Физический resilient gate требует отдельных
worker/storage failure domains и не может быть закрыт двумя kind nodes одной VM.

Порядок восстановления и ограничения:
[recovery runbook](KUBERNETES_REPLICAS_RECOVERY.md),
[операции с хранилищами](REPLICA_STORAGE_OPERATIONS.md),
[сводная матрица](REPLICA_ACCEPTANCE_MATRIX.md).


Cross-node VM103 evidence04.11 is recorded in the
[bounded rehearsal report](superpowers/reports/2026-11-04-replica-crossnode-vm103.json).
The successful repeat finished accepted document work on control-plane while
the original logical worker was still off. A preceding failure exposed DNS
co-location and a dead PostgreSQL TCP transaction; retain that unsuccessful
result and require distributed DNS plus verified server dead-client detection
before a comparable run. Managed chart fix `ccc2678` changes only future
PostgreSQL workload args; existing installations need their own planned rollout.
This is before-provider-admission worker loss, on one VM/kernel, with a model
stub. It supplies no physical-domain, active-provider-loss, real IdP/Gateway
or production certification. Production backup schedule remains OFF.
