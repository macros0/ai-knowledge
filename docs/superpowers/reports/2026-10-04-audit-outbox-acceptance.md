# Audit outbox: локальная приёмка

Дата: 04.10.2026. Ветка `codex/kubernetes-rollout`, изменения не закоммичены. Основной checkout содержит копию документации; runtime проверен из рабочего дерева ветки. [План](../plans/2026-10-04-audit-outbox.md), [runbook](../../AUDIT_DELIVERY.md), [каталог](../../AUDIT_EVENT_CATALOG.md).

## Результат и границы

Локальный пакет durable outbox реализован и проверен. Audit fact и immutable sanitized payload записываются атомарно; один writer повторяет committed pending и отмечает `stdout_written` отдельно. Повтор после аварии сохраняет UUID, время и payload. Это at-least-once локальная запись stdout; подтверждение внешнего индекса отсутствует. Default `disabled` сохраняет прежние local/Compose способы запуска. Новый binary требует additive audit schema даже при disabled.

Реализованы 77 actions, атомарные login/logout/block/unblock, CSRF/OIDC/protected denial, серверная корреляция, ограниченный retry, OS lock, private operator CLI, commit-aware runtime counters и model-free restore hold. Compact/Recreate/replicas=1/workers=1 сохраняются. Trusted-IP relay, мониторный endpoint и распределённые leases не реализованы.

## Итоговые проверки

| Проверка | Результат | Что доказывает |
|---|---|---|
| Полный backend в окончательном образе, восемь непересекающихся shard | **3370 PASS / 36 SKIP / 0 FAIL**, собрано 3406 cases | Полный собранный набор без exclusions; skips включают 9 optional PG cases, запущенных отдельно, и 27 прежних platform/environment skips |
| Aggregate coverage окончательного набора | **88%**, 22909 statements / 2660 missed; gate 85% PASS | Coverage объединён только из восьми final-v5 shards; версии coverage 7.16.1 / pytest-cov 7.1.0 в отдельном test volume |
| Required PostgreSQL 17, текущий local fault прогон | **31 PASS / 0 SKIP**, 29,70 с | Прежние 9 cases плюс 16 denial/security statement/lock/connect/mixed-race и 6 physical-capacity/recovery cases; подробности ниже. Внешняя CI не запускалась |
| Ruff для изменённого audit пакета | PASS | Нет lint ошибок в проверенных audit modules/scripts/tests и изменённых models/main |
| Непустой pg_dump → новая DB → hold в старом образе | PASS | Независимое восстановление и сохранность immutable audit ID/UUID/time/payload; DB proof, не Kubernetes/PVC/Secrets proof |
| Final old-image hold | PASS | 6 immutable rows; digest совпал; held=5, stdout_written=1, pending=0; сохранён checksum backup |
| Operator/lifecycle/chart и Compose contracts | PASS в полном backend наборе | Fixed hold до старта, сохранённая структура старого operator archive; shell fake-Docker proof не заменяет живое восстановление стека |
| Исходный `check-project` (04.10.2026) | **FAIL: 5 high dependencies** | npm audit gate не ослаблен; цепочка braces/micromatch/fast-glob/@next/eslint-plugin-next/eslint-config-next. Выполнение остановилось до frontend lint/tests |

SQLite и PostgreSQL process tests используют настоящие отдельные процессы: crash после stdout до marker, прерванную строку, второй owner и SIGKILL. HTTP test подтверждает ответ 401 при заблокированном sink. Отдельная disposable ASGI fixture проверяет реальные потоки Uvicorn/diagnostic entrypoint при 12 concurrent GET: access/error в stderr, audit в stdout. Это не полный HTTPS flow продукта.

Schema tests отвергают несовместимые типы, FK, CHECK expressions, partial indexes и literal decoy даже при сохранённых именах constraints. UNIQUE допускает legacy NULL и отвергает повторный event UUID; реальная ошибка INSERT outbox откатывает audit. Fixture-only empty downgrade проверен на SQLite, не заявлен как production rollback. Старый binary возвращается без SQL downgrade/stamp.

Preflight вне best-effort init_db проверяет реальный SELECT/INSERT, sequence USAGE и UPDATE всех пяти delivery columns без создания фиктивных событий. Readonly, SELECT-revoked и attempt_count-only UPDATE роли отвергнуты. Dedicated denial engine сохраняет исходные PostgreSQL options/search_path и добавляет ограниченные connection/pool/statement/lock timeouts. Измеренный lock budget не доказывает универсальный deadline при DNS/multihost отказах.

## Точная версия локального образа

Final image: `okf-backend:audit-v5`.

- OCI index / Docker inspect ID: `sha256:5f78c08caf3e01fa6b315617633433db0a2c86e512e6acf1afa1d7937fbe7cd7`.
- Payload manifest: `sha256:e6c5fe9f1013fd4f84fc316e7dfd3c7eafcd7bb698de709f8e803708d6a92c41`.
- Image config: `sha256:77db6e5f6728d78e65bd039199eb094607d137147d11099a5e29bb95c2ba8968`.
- 270 packaged Python files в app/scripts/alembic, включая 180 app files, совпали побайтно с рабочим деревом.
- Git label `f551f5f1457de1a0c073c3df09f9929375c14802` обозначает базовый HEAD. Он не описывает все uncommitted source изменения; хеши файлов сохранены отдельно. Release runtime revision не подтверждён.
- Новый Alembic head `b6c7d8e9f0a1`, parent `a5b6c7d8e9f0`; existing head/model drift suite PASS.

Образ не опубликован. Private evidence, shard logs/coverage и nonempty backup сохранены отдельно от поставляемых файлов. Checksum dump: `6a8df5ae3d92ac25afcfec3f422ec212b6e45be3289af83df1ef323df2a167de`.

## Первоначальные failures и исправления

Сохранены RED и промежуточные неудачи; допуск не получен изменением порогов или пропуском tests.

- Первые security tests: 6 RED; последующая интеграция 42 FAIL / 46 PASS выявила settings alias и NameError error handler, затем focused набор 113 PASS.
- Пойманная owner-ом serialization exception оставляла audit без outbox (2/1 rows). Serialization перенесена до первого INSERT.
- Новый archive member отвергался frozen schema-1 consumer. Fixed model-free hold встроен в существующий allowlisted lifecycle, структура archive сохранена.
- Counter/check/FK/type/literal-decoy fixtures выявили недостающие root-commit counters и слишком мягкую introspection; добавлены rollback-aware counters и строгий общий model-free contract.
- PostgreSQL denial helper перезаписывал search_path, committed denial отсутствовал. Исходные options сохраняются; lock/commit proof прошёл.
- `schema_version=True` ошибочно принимался как 1. Payload validator требует точный integer type.
- Role с UPDATE только attempt_count проходила preflight. Проверяются все delivery columns.

Каждое исправление подтверждено regression test; окончательный полный набор относится к final-v5, результаты предыдущих image версий с ним не объединялись.

## Решения при исполнении

- Сохранён старый operator archive contract; embedding фиксированного hold source вместо нового archive member. Цена: синхронность standalone/embedded source должна оставаться проверяемой contract test.
- Непустой backup и evidence сохраняются до домашней приёмки и интеграции. Одноразовый PostgreSQL контейнер удаляется только после проверки точного ID/owner label и backup checksum; другие сервисы не затрагиваются.
- Составные пункты плана остаются открытыми при непроверенных окруженческих условиях. Локальный PASS не означает external CI, интеграцию main или завершение задачи 5а.

## Открытые gates и следующий шаг

1. Домашние confidential/basic/PKCE none и public/none/PKCE S256/UserInfo-only OIDC приёмки выполнены ниже. Остаются дополнительные комбинации OIDC профилей, fault cases и независимое восстановление без исходной VM с отдельным защищённым источником секретов. Проверенный off-VM backup не заменяет такой прогон.
2. Outbox tablespace exhaustion, statement timeout, refused/handshake-stalled connection и три mixed block/unblock schedules проверены ниже. Полное PGDATA/WAL и выбранные cold-start/lifespan/maintenance/recovery probes дополнительно проверены 05.10.2026 ниже. DNS/multihost deadline воспроизведён ниже и имеет **3 FAIL / 5 PASS**, исправление открыто; реальные StorageClass faults и проверки под production нагрузкой также открыты.
3. Локальное frontend dependency исправление и полный project gate закрыты 05.10.2026 в рабочей ветке (раздел ниже). Повтор required checks на интегрированном release snapshot и внешнем CI остаётся открытым; старый FAIL сохранён как историческое свидетельство.
4. External required CI, publication/registry и целевой cluster; повторная HTTPS проверка выбранного профиля на фактической платформе. Локальная сборка не является публикацией.
5. Trusted-IP/HMAC и credential-protected live metrics adapter; collector parsing/CRI reassembly/event_id dedup/downstream ACK, historical replay и retention; дальнейшие multi-replica claiming/leases.

Durable initial profile не использует hot reload; supported entrypoint запускает один worker. Поддержка reload, если потребуется, требует отдельного lifecycle proof. Runtime CLI сообщает unknown без IPC, не заявляет здоровый writer. Автоматическое удаление pending/held/неподтверждённых downstream events не добавлено.

Commit/merge/push не выполнялись. Общий rollout и задача 5а остаются открытыми.

## Домашний Kubernetes: confidential-профиль 04.10.2026

Домашняя training VM проверена через Proxmox Guest Agent и SSH; старый адрес устарел. SSH host key подтверждён по доверенному каналу Proxmox, без отключения проверки ключей. VM: 4 vCPU / 6 ГиБ RAM / диск около 50 ГиБ; перед прогоном доступны 2701 MiB RAM и 16 ГиБ диска. Автостарт VM включён, Docker enabled, основной kind container имеет restart policy unless-stopped. Это проверка настроек, а не повторный power-loss experiment.

Созданы только отдельные synthetic source/target/access namespaces с recorded UID/owner labels. Существующие демонстрационные releases, остановленный второй kind и тестовый стек сохранены. Источник после backup остановлен; проверенная restored копия и её отдельный IdP/Gateway сохранены. В конце доступно около 1,5 ГиБ RAM / 14 ГиБ диска; MemoryPressure=False, оба прежних application Pods 2/2 Ready. Default kindnet не обеспечивает CNI enforcement; этот прогон не закрывает trusted-IP/network isolation gate.

| Home check | Результат |
|---|---|
| Штатная установка / последующее managed update | PASS, coherent backup перед сменой frontend |
| HTTPS → Gateway → Next → backend → реальный synthetic Keycloak | PASS: confidential/basic, PKCE none; четыре роли, unmapped denial, cookie flags, CSRF, logout |
| DOCX >2 MiB → generation → sources / BM25 / chat SSE | PASS до и после frontend исправления |
| HTTP denial response / DB / stdout | Один server request UUID; exact payload совпал |
| Backup → другой namespace / новые PVC / Secrets | PASS: четыре disjoint PVC UID, новые PostgreSQL password и APP_SECRET_KEY, same public trust bundle |
| Audit integrity после coherent restore | Все 67 audit/outbox rows сохранили ID/UUID/time/payload hash |
| Historical pending | Один pending стал held/restored_backup; исторические payload не появились в новом stdout |
| Старые session cookie / unconsumed login flow | Corpus HTTP 401 / callback session_expired с audit reason invalid_state |
| Новый OIDC login после restore | PASS: новый event UUID, callback request ID совпал с DB/stdout |
| Coherent backup / restore duration | 43,11 с / 80,59 с на малом synthetic corpus; потерянных committed audit facts в этом snapshot нет |
| Независимое хранение backup вне VM | Completed archive скопирован на другую машину; checksum и каждый manifest artifact повторно проверены |

Success и denial получены через настоящий HTTPS/OIDC путь. Pending создан отдельной synthetic store fixture при остановленном writer; это проверяет восстановление committed очереди, не бизнес-атомарность внешних FS/Qdrant операций.

Первый home proof остановился на correlation assertion: Next безусловно заменял backend x-request-id собственным UUID. Это product defect, а не ошибка выбранного критерия. Исправлен route proxy: validated upstream ID сохраняется в response и request_finished diagnostic event; absent/invalid backend header и network failure сохраняют proxy fallback. Browser-controlled ID по-прежнему заменяется до обращения к backend. RED: 2 failures; GREEN focused: 33 PASS / 1 SKIP. Полный frontend: 375 PASS / 1 SKIP, ESLint 0 errors / 40 прежних warnings, production Docker build PASS. Dependency versions и audit gate не ослаблялись.

Final frontend local image `okf-frontend:audit-v1`: OCI index `sha256:be6fff2e63502542c78f291046bbec503f4acc682912fc7d12258d1d8946ce33`, payload manifest `sha256:f953ed78c3250ec007b6edba08f4727e8466384e10d4ebcea889ac3a187cde34`, config `sha256:204889d311232b6a657cfec212648e664224a3eefa26da1cfa53bcf79abb9971`.

Docker save/kind import создаёт другую manifest identity. Actual running backend imageID: `sha256:3315c875a159d6309a34116d053960b6ab42e10b434b1aa31d0d2d9a30b7a346`; frontend: `sha256:a6f17fe396b42786b58a1e08c6c292ce00f7e2942b4cca50b54077332306f423`. Первоначальный export assertion ошибочно требовал исходный manifest digest; несовпадение сохранено, затем содержимое независимо проверено в running Pods: **270 backend Python files и 274 compiled frontend artifacts** побайтно соответствуют проверенным локальным images. Перезапусков этих новых application containers нет. Это local loaded identity, не registry publication.

Off-VM archive SHA256: `443a51c1a71adba12906d49043079b5c8225c76f28fe226a3fa0ed0f26575d9e`; backup manifest SHA256: `a2472e3fd4a460bfd7efddd3bed4fcf464c925ea3916326bb50daa28ca512d76`. Private scripts/credentials/CA keys/cookies/raw logs и dumps не входят в публикацию. Одноразовые probe Pods и restored old-cookie Secret удалены после proof; source/target PVC и backups сохранены. Непроверенными остаются real-data capacity/RPO/RTO, restore_without_source_vm, дополнительные DB/environment faults, downstream/relay/metrics и external CI. Public профиль и выбранная fault matrix проверены следующими прогонами ниже. Полный backend повторно не запускался: все 270 source hashes не изменились; его предыдущие final-v5 результаты остаются применимыми.

## Домашний Kubernetes: public-профиль 04.10.2026

Проверен отдельный fresh public OIDC client: token auth method `none`, PKCE `S256`, группы только из UserInfo endpoint. В runtime Secret client secret отсутствует; confidential-секрет не переносился и в restored target. Использованы те же проверенные `audit-v5` backend и `audit-v1` frontend, без изменения product source. Прогон выполнялся последовательно из-за ограниченной памяти: только предыдущая собственная confidential restored копия и её IdP остановлены после нового coherent backup; их PVC/Secrets и оба backup сохранены. Прежние демонстрационные приложения не останавливались.

| Public home check | Результат |
|---|---|
| HTTPS trust / negative trust / redirect / anonymous denial | PASS на реальном Gateway→Next→backend пути |
| Public OIDC / PKCE S256 / UserInfo-only | PASS: четыре роли, unmapped denial, secure cookies, CSRF, logout |
| DOCX >2 MiB / viewer write denial / generation / sources / BM25 / SSE | PASS через HTTPS; использованы синтетические модели |
| Successful callback и protected denial | Реальные response request ID совпали с committed DB event и exact stdout payload |
| Coherent backup → fresh target | PASS: четыре новых disjoint PVC UID, новые PostgreSQL password и APP_SECRET_KEY |
| Immutable audit facts | Все **36** audit/outbox rows сохранили ID, UUID, UTC time и payload SHA256 |
| Historical pending | Один committed synthetic pending стал held/restored_backup, historical stdout replay отсутствует |
| Old session / old unconsumed flow / new login | HTTP 401 / session_expired с invalid_state / новый callback UUID совпал с DB/stdout |
| Backup / restore duration | **45,42 с / 84,22 с** на малом synthetic corpus; это не production RPO/RTO |
| Running image content | **270** backend Python files и **274** compiled frontend artifacts совпали с frozen images; оба app containers без restart |
| Off-VM completed backup | SHA256 архива и всех **6** manifest artifacts независимо проверены на другой машине |

Public archive SHA256: `836b5266724b811045eca135481ff354aa2615a78515db8af6a10f8de40e7469`; manifest SHA256: `f0066b06f0c8062a0f51f8a4cb2dd1cd0e3c0479e2824a28a0f2a08ee899c416`. Actual kind imageID совпали с указанными для confidential прогона. Source hashes backend вновь проверены: 270/270 без изменений; полный suite и build повторно не запускались. Старые results не выдаются за новый запуск.

После приёмки public source остановлен, public restored target и его IdP/Gateway сохранены работающими; source/target PVC и backup retained. Одноразовые probe Pods, old-cookie Secret и probe ConfigMap удалены. Конечный ресурсный snapshot: доступно **1536 MiB RAM**, около **14 ГиБ** диска, MemoryPressure=False; новый application и оба прежних application Pods 2/2 Ready. Состояние paused confidential копии и порядок возврата сохранены в private kit; две audit копии одновременно не запускаются без повторной capacity проверки.

Проверен HTTP/OIDC сценарий, а не полный browser UI. Pending создан explicit store fixture после остановки writer. Kindnet enforcement, real-data capacity, остальные OIDC комбинации и восстановление без исходной VM этим прогоном не подтверждены. Private CA keys/Secrets/cookies/dumps/raw logs не публикуются. Runtime по-прежнему не интегрирован в main.

Свежий read-only `npm audit --json` от 04.10.2026 подтвердил **5 high / 0 critical**, та же цепочка линтинга. [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) указывает affected `braces <=3.0.3` и отсутствие patched version. Предлагаемый audit downgrade `eslint-config-next` до 14.2.35 не применён к Next 16: совместимость не подтверждена. Dependency gate остаётся FAIL до отдельного исправления и полного project check; severity threshold и состав проверок не менялись.

## PostgreSQL fault matrix: продолжение 04.10.2026

Добавлены два regression modules: `backend/tests/test_audit_postgres_faults.py` и `backend/tests/test_audit_postgres_capacity.py`, а также воспроизводимый runner `scripts/kubernetes/tests/audit_fault_acceptance.py`. Product backend не менялся: повторная сверка **270/270** packaged source hashes с `audit-v5` прошла. Прежний полный backend/coverage результат не включает эти 22 новых cases и повторно не запускался; новый targeted PG набор содержит все прежние 9 cases и новые 22, без exclusions.

Окончательный прогон поставляемого runner: **31 PASS / 0 SKIP / 0 FAIL за 29,70 с**, Ruff PASS. Один warning — прежняя Starlette/httpx TestClient deprecation; он не подавлялся. Первые расширенные прогоны 20 PASS и 21 PASS сохранены отдельно. Lint выявил fixture-shadow F811 и import/check flags нового runner; исправлены только test/tool files. Runtime defect не обнаружен, искусственный RED и изменение production поведения ради новых тестов не создавались.

| Fault | Наблюдение и подтверждённый контракт |
|---|---|
| Statement timeout | BEFORE INSERT trigger вызывает настоящий `pg_sleep(3)`; отдельный denial engine отменил SQL с **57014** примерно за 1,12 с. HTTP 401/WWW-Authenticate сохранены, audit/outbox пусты после rollback, failure_count вырос ровно на один |
| Event loop во время SQL | Пока audit INSERT выполнялся, `/health/live` ответил в пределах 1 с; async request path не ожидал блокирующий SQL в event loop |
| Connect refusal | Локальный reserved/non-listening socket дал настоящую connection error; HTTP 401 сохранён, partial rows отсутствуют, восстановление проверено |
| Connect handshake stall | TCP socket принял соединение и не ответил на PostgreSQL handshake; connect timeout завершил denial примерно за **2,16 с**, в пределах 5 с, без committed audit. После recovery DB event и exact stdout payload совпали |
| Mutation lock / statement timeout | Дополнительно 10 cases: fresh login, session rotation, logout, block и unblock при 55P03 и 57014. Явные PG session timeouts 500/1000 ms дали безопасный 503 в пределах 5 с, сохранили session IDs/identity/cookie и block rows. После release/drop trigger новая операция и DB/stdout прошли; global application timeout не вводился |
| Mixed block/unblock | Три schedules: block first, unblock first, simultaneous overlap. Unblock count **1 или 2** соответствует actual committed rows и JSON audit; итоговый active count равен `2-count`. Это компактный PostgreSQL/API transaction proof, не distributed linearizability или production load matrix |
| Physical disk-full | Outbox table размещена на проверенном **4 MiB tmpfs**, заполненном настоящим SQL COPY. И COPY, и последующий outbox INSERT получили **53100**, без подмены исключения |
| Capacity mutation failures | Fresh login, session rotation, logout, block и unblock вернули безопасный HTTP 503; session IDs/identity, cookie identity, block IDs/state и audit/outbox counts сохранились. Fresh login не выдал session cookie |
| Capacity protected denial | HTTP 401 и WWW-Authenticate сохранены даже при реальном 53100; emergency failure увеличился один раз, fake committed event отсутствует |
| Capacity recovery | После освобождения только synthetic ballast каждая операция прошла; новая session/отмена session/активность block проверены. Новое событие совпало по response request ID и exact dispatcher payload |

[PostgreSQL SQLSTATE](https://www.postgresql.org/docs/17/errcodes-appendix.html) использован для различения `disk_full` и `query_canceled`; [connection timeout](https://www.postgresql.org/docs/17/libpq-connect.html) проверен на одном локальном endpoint. Универсальный deadline при DNS/multihost этим не установлен. Outbox tablespace exhaustion не заменяет отказ всего PGDATA/WAL: PostgreSQL продолжал обслуживать rollback и чтение.

Runner создаёт новую internal Docker network без host ports, PostgreSQL DATA tmpfs 256 MiB и отдельный tablespace tmpfs 4 MiB; test container non-root, root FS read-only, `/tmp` 256 MiB. Монтируется только `backend/tests` read-only; `.env`, runtime Secrets и существующие data volumes не монтируются. Версия PostgreSQL image: `sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675`; backend — прежний frozen `audit-v5`. Cleanup проверяет полные container/network IDs и owner labels. Оба собственных containers и network удалены; synthetic evidence сохранён. Домашняя VM, её Kubernetes и действующие локальные службы не изменялись.

Повторяемый запуск из рабочей ветки после сборки проверяемого backend и загрузки указанного PostgreSQL image:

```text
python scripts/kubernetes/tests/audit_fault_acceptance.py --backend-image <tested-local-image> --evidence-directory <new-private-directory>
```

Физические тесты требуют `AUDIT_REQUIRE_CAPACITY=1` и строго именованный bounded tablespace. Без prerequisites required mode завершается ошибкой; ordinary full pytest может явно skip opt-in capacity module. Обычный PG CI теперь включает все **25** переносимых cases без фильтров; отдельный `audit-capacity` job собирает backend и запускает все **31** cases через этот runner. Проверены YAML/команды, но hosted CI, fresh CI build, branch-protection required checks, commit/push/publication не выполнялись. Security boundary дополнен в рабочем `SECURITY.md`. Runtime/CI/test files не интегрированы в main.


## Dependency remediation и окончательный frontend (05.10.2026)

Локальный dependency gate в `codex/kubernetes-rollout` закрыт. Исходный FAIL
04.10.2026 с 5 high сохранён выше; новый PASS не получен изменением severity,
исключением dev dependencies, отключением правил или downgrade Next config.
У стабильного плагина 16.3.8 и проверенного canary осталась цепочка fast-glob;
[advisory](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) ещё не указывает
исправленную braces version. Поэтому реализован выбранный scoped adapter.

`frontend/tooling/eslint-root-glob` — локальный development package с pinned
`tinyglobby@0.2.17`. Корневая dev dependency `fast-glob` указывает на него,
а npm override ограничен `@next/eslint-plugin-next`. Root dependency нужна для
правильного разрешения `file:` при clean npm ci; прямой относительный override
из плагина дал рассинхронизацию lock/ci и не перенесён в рабочий пакет.
Dockerfile копирует каталог до установки. Исходный upstream плагин и
`eslint.config.mjs` сохранены; адаптер поддерживает только применимый
`globSync(pattern, {onlyDirectories: true})` и явно отвергает новые options.

Удалены 15 старых lock nodes, включая braces/micromatch; запись fast-glob заменена
локальным package link, добавлен один local package record. Все существующие
runtime dependency entries семантически совпали с baseline, включая версии,
integrity и platform selectors. Удаление libc selectors при генерации npm 10
обнаружено и исправлено до переноса кандидата. Next/React runtime не обновлялся.
Pre-push по-прежнему проверяет исходящий package/lock snapshot: отдельный audit
только этих двух файлов подтвердил совместимость без checkout адаптера.

18 регрессий проверяют настоящий Next plugin: default cwd, relative/absolute
paths и filesystem root, Windows separators, globs/braces, mixed arrays,
скрытые каталоги, исключение файлов и отсутствующие каталоги, symlink/junction
roots и их traversal, настоящий internal-link diagnostic и unsupported API.
Семантика массива сохранена: upstream обрабатывает entries независимо;
negative entry не вычитает результаты другого entry.

Расширенные проверки обнаружили два отличия tinyglobby: пропуск самого
symlink directory и потерю filesystem root. Для первого добавлен отдельный
non-traversing scan со stat-фильтром; traversal остаётся в основном scan.
Для `/` и Windows drive root применяется direct stat. Исходные RED и
промежуточные failed candidates сохранены приватно. CJS entrypoint оформлен
как `index.js` с `type=commonjs`, чтобы действующий ESLint files matcher
применял тот же plugin config; правила под новое расширение не ослаблялись.

| Финальная проверка | Результат |
|---|---|
| Windows clean npm ci, isolated worktree modules | PASS; удалена только прежняя junction на main modules, основной checkout сохранён |
| Windows штатный `node scripts/check-project.mjs` | PASS: полный audit, lint, **393 PASS / 1 прежний SKIP / 0 FAIL**, backend Ruff |
| Linux final full frontend | **394 PASS / 0 SKIP / 0 FAIL** |
| Effective ESLint config | **67 rules**, plugins/parser/settings/globals совпали с baseline; исходный config file не менялся |
| ESLint Windows/Linux | **0 errors / 40 прежних warnings** |
| npm audit prod/dev/optional/peer | **0 vulnerabilities**, включая **0 high / 0 critical** |
| Production Docker build | PASS, новый frozen frontend `audit-v3` |
| Backend source | Все **270** Python files совпали с прежним frozen backend; full backend/coverage повторно не запускались |

Final frontend OCI index: `sha256:711dd0d4be957e4532c2e933571c150e277431dafb4941390619b2cba511e956`;
payload manifest `sha256:c0db51ee92029ee0906331a0524116d35f1706586735f6d3a22686d6944e6e45`;
config `sha256:7b7be0daa60030c904bbdd4bffcaf9a17c1b3990c7f2055dc04e25502a2b975b`.
После kind import manifest identity отличается:
`sha256:b3957a381192a3bea5bf5837aa7fcb5b33b3fecb32fa41bba8565f108f81d7e2`. Независимое сравнение **308 compiled files**
локального image и actual running Pod прошло; tag-only proof не используется.

Домашний managed lifecycle выполнил backup перед Recreate и запустил финальный
image; весь upgrade занял **66.93 с** на synthetic корпусе.
Сохранены **71** прежних immutable audit/outbox facts,
held rows остались held, historical stdout replay отсутствовал. Полный public
OIDC/none/S256/UserInfo-only HTTPS smoke прошёл: роли/unmapped, cookie/CSRF,
DOCX больше 2 MiB, generation, sources/BM25, SSE/chat и logout. У нового входа
и защищённого отказа response UUID совпали с committed DB и exact stdout.
Temporary probes удалены после UID/owner checks. Running application 2/2 Ready,
прежние demos также 2/2 Ready, Node Ready/MemoryPressure=False.

Completed pre-upgrade backup дополнительно проверен вне VM: archive SHA256
`417adc1c5c29dd1ed87ef7f1823f8a6e73c8a480cb7d6d19735c6de170f32d93`, manifest SHA256 `d8c25a8cdef4953162bcf5482bd3104bfef6220d517535202c059c605294ec61`;
все manifest artifacts совпали. Старые images/backups сохранены, protected
home values фиксируют tested frontend tag. Это не restore_without_source_vm,
production RPO/RTO или внешний protected CI.

В main зеркалированы только эти анонимизированные документы. Runtime/test/
Docker/lock изменения остаются в рабочем дереве; commit, merge, push и
publication не выполнялись. Внешний required CI,
DNS/multihost faults, protected adapters и последующие replica этапы открыты.


## Whole PGDATA/WAL: 05.10.2026

Добавлен [owned cluster fault runner](../../../scripts/kubernetes/tests/audit_cluster_fault_acceptance.py) и его synthetic helper `backend/tests/audit_cluster_probe.py`. Отдельный PostgreSQL 17 имеет **128 MiB PGDATA / 64 MiB WAL tmpfs**, memory limit 512 MiB, internal network без host ports и persistent mounts. Backend probe запускается nonroot/read-only с mount только тестов. Docker socket внутрь не передаётся. Exact IDs/owner labels, реальные mountinfo/tmpfs bounds и ноль свободных blocks проверяются до воздействия; 9 pytest guard cases отвергают чужие IDs/labels, host ports/mounts, volumes и неверные capacity/memory bounds.

Финальный прогон: **два физических fault scenarios — PASS**, **36 HTTP probes**, **24 операторских CLI invocations**, **три отказа реального FastAPI lifespan** на обязательном audit preflight. Каждый fault/cold/recovery этап проверяет fresh/rotated login, logout, block, unblock и protected denial; каждая HTTP операция завершилась в пределах 5 s. Это число проверок runner, не дополнительные pytest cases к прежним 31. Отдельно **9 pytest guards PASS**, Ruff PASS; CI YAML разобран и runner добавлен в существующий capacity job. Внешний CI не запускался.

| Проверка | Наблюдение |
|---|---|
| Whole PGDATA ENOSPC | `dd` исчерпал весь bounded filesystem; allocation SQL получил **53100**. Audit INSERT потребовал расширения heap: пять mutations вернули безопасный **503**, denial сохранил **401/Bearer**, failure signal ровно один. Session IDs/identity/cookie и block rows не изменились |
| Whole WAL ENOSPC | После заполнения всего WAL mount новые сегменты запрошены через `pg_switch_wal()`. Получен настоящий **PANIC / No space left on device**, PostgreSQL перестал принимать соединения. Fresh/rotated login и logout — 503; block/unblock — fail-closed 401 на чтении недоступной server session; denial — 401. Success и новые cookie отсутствуют |
| Cold startup | PID1 supervisor сохраняет tmpfs после выхода postmaster. После immediate stop ballast дополнен: удаление PID-файла могло освободить blocks. Перед обоими cold attempts снова подтверждён zero-free-block ENOSPC; `pg_ctl` exit 1 и отсутствие accepting connections. Это не пересоздание контейнера с потерянным tmpfs |
| Backend startup | Настоящий `TestClient` lifespan дошёл до обязательного audit preflight и завершился SQLAlchemyError до running state: PGDATA cold и WAL fault/cold, всего три проверки |
| Живой full PGDATA / maintenance | Zero-row preflight и status успешно работают; status честно видит **pending=1**, runtime_state=unknown. Идемпотентная schema CLI работает; отдельный nonempty hold изменил **одну** pending row в held/restored_backup. UUID/time/audit fact/payload сохранены. Свободное место preflight этим не гарантирует |
| Недоступная БД / maintenance | Status, schema migration и hold — exit **1**, безопасное сообщение без traceback, URI, event или actor. Ошибка не превращается в фиктивный нулевой backlog; WAL pending сохранился до recovery |
| Recovery | Освобождён только exact synthetic ballast, настоящие PostgreSQL files не удалялись. PostgreSQL завершил crash recovery; исходные auth/block rows и оба audit facts сохранены. Anchor pending доставлен в **exact original stdout bytes**; maintenance допускает только явную смену delivery status. Все шесть операций повторно прошли с верным DB/request UUID и exact stdout; новые auth/block результаты корректны |
| Liveness / cleanup | `/health/live` — 200 в fault/cold/recovery. Удалены только exact owned контейнеры и пустые owned networks; после финального прогона их нет. Домашние VM/кластеры и реальные базы для fault injection не использовались |

Начальные неудачные прогоны сохранены: неверный min_wal_size; read-only import DATA_DIR; cold-start precondition, ослабленный удалением PID-файла; утрата cookie domain в test harness. Исправлены причины тестового запуска. Дополнительный review выявил пустой hold fixture; финальная проверка использует отдельное непустое maintenance schema. Production runtime не изменён, **270/270** packaged backend hashes по-прежнему соответствуют `audit-v5`. Прежние full-suite/coverage/31-case результаты не выдаются за повторный прогон.

[PostgreSQL disk usage](https://www.postgresql.org/docs/17/diskusage.html) описывает различие full data и WAL; [pg_ctl](https://www.postgresql.org/docs/17/app-pg-ctl.html) предупреждает, что timeout не доказывает остановку процесса. Runner проверяет фактическую readiness/status отдельно. Этот tmpfs proof не подтверждает hardware power loss, поведение любого StorageClass, allocation-heavy schema upgrade или production load/RPO/RTO. DNS/multihost, интегрированный release/внешний required CI и следующие replica/adapters этапы остаются открытыми.


## DNS/multihost deadline: RED 05.10.2026

Добавлены [network fault runner](../../../scripts/kubernetes/tests/audit_network_fault_acceptance.py), `backend/tests/test_audit_postgres_network.py` и пять ownership/host-network guards. PostgreSQL 17 запускается в owned internal network без host ports, с 256 MiB PGDATA tmpfs и 512 MiB memory bound. Probe container — nonroot/read-only; помимо тестов подключён только новый generated synthetic resolver config read-only. Host `/etc/resolv.conf`, Windows DNS, VPN и реальные DB volumes не меняются. DNS UDP 53 слушает только loopback внутри контейнера; ответы и реальные запросы контролируются fixture. NSS config `timeout:2 attempts:3` выбран для воспроизводимого задержанного отказа. TCP stalls — три настоящих listening socket, принимающих PostgreSQL handshake без ответа.

Финальный прогон на unchanged audit-v5: **5 PASS / 3 FAIL / 0 SKIP**, runner exit **1**. Это три нарушения исходного <5 s, не ошибки подключения тестовой среды. JUnit содержит длительность и число DNS packets/принятых stalled hosts; внешние реквизиты/event payload туда не входят. Пять guard tests и Ruff PASS. Прогон повторён после проверки fixture prerequisites и добавления fast NXDOMAIN failover; red evidence сохранён. Первый предварительный запуск не дошёл до тестов, поскольку Docker read-only root запретил запись resolver config: исправлен test-only mount нового config, не отключена защита rootfs.

| Case | Время denial | Результат |
|---|---:|---|
| NXDOMAIN | 0.088 s | PASS: 2 настоящих DNS packets, 401/Bearer, один failure signal, audit rows=0; recovery exact DB/stdout |
| DNS без ответа | **6.075 s** | **FAIL budget**: 6 DNS packets; остальные fail-closed/liveness/failure signal/rollback/recovery assertions PASS |
| Успешный DNS | 0.082 s | PASS: 2 packets; правильный committed denial/request UUID и exact stdout |
| Явный hostaddr | 0.069 s | PASS: DNS packets=0; правильный committed denial и stdout. Это отдельный профиль, не решение arbitrary DNS |
| Silent DNS name → healthy IP | **6.080 s** | **FAIL budget**: после 6 packets fallback всё же создал правильный committed denial, failure signal=0; HTTP 401 и exact bytes сохранены |
| NXDOMAIN name → healthy IP | 0.081 s | PASS: fast DNS error не ломает рабочий fallback и audit |
| Три TCP handshake stalls | **6.073 s** | **FAIL budget**: действительно приняты 3 соединения; 401/Bearer, один failure signal, audit rows=0, subsequent recovery PASS |
| Refused IP → healthy IP | 0.071 s | PASS: первый адрес действительно не слушает; второй сохранил denial/request UUID и exact stdout |

`/health/live` отвечал в пределах 1 s, пока DNS/handshake audit ожидал ответа. Это event-loop/liveness proof, а не выполненный request deadline. До итогового budget assertion проверены row counts, единичный failure signal там, где DB write не состоялся, и recovery. Silent-name → healthy case действительно commit-нул audit после долгого ожидания, поэтому он не объявляется rollback или потерей события.

Версии внутри image: **psycopg 3.3.6 / libpq 18.6 / SQLAlchemy 2.1.3**; сервер — выбранный PostgreSQL 17 image. Local venv psycopg 3.3.4 отличается: дополнительное read-only source inspection сделано именно в frozen image, без сетевого доступа. Источники: [libpq connect_timeout](https://www.postgresql.org/docs/17/libpq-connect.html#LIBPQ-PARAMKEYWORDS) применяется отдельно к hosts/IPs; [psycopg concurrency](https://www.psycopg.org/psycopg3/docs/advanced/async.html) описывает DNS/Windows/cancellation caveats. В frozen driver прерывание SQL может дополнительно ждать cancel и завершение запроса; простая async отмена сама по себе не доказывает общий <5 s.

Runner добавлен как обязательный шаг capacity CI job, **без continue-on-error/xfail/ослабления порога**. Hosted CI не запускался; текущий локальный snapshot имеет deadline gate FAIL, хотя прежние storage/atomicity и dependency checks прошли. Новый bounded transport предлагается в Task 3a; product source не менялся, все **270/270** packaged backend hashes совпадают с audit-v5. Исправление, новый source/image freeze и повтор релевантной приёмки остаются предстоящими. Exact owned containers/networks удалены; private evidence retained, commit/merge/push не выполнялись.


## Проверка плана 05.10.2026 — без изменения runtime

Уточнена Task 3a: deadline до admission, ограничение очереди/helpers и их secret-free окружения, capability/configuration proof на packaged driver, сохранение DSN/TLS/failover, общий срок dialect setup/SQL/cleanup и отдельная проверка неизвестного COMMIT. Добавлены owning tests, критерии packaging/resource/cancellation, последовательность нового image freeze/CI/home proof и граница отката. В сводном Kubernetes плане устранено устаревшее утверждение о пяти текущих high findings: их локальное исправление уже подтверждено выше.

Эта правка меняет только документы. Новые runtime tests не выполнялись; **5 PASS / 3 FAIL** DNS/multihost остаются последним результатом соответствующего runner. Новые checkboxes не являются пройденными проверками, внешний CI не подтверждён.


## Task 3a: Linux deadline 05.10.2026

По разрешённому inline продолжению реализованы `audit_denial_transport.py`, изолированный `audit_dns_probe.py`, admission/ownership в `audit_security.py` и scoped durable PG preflight. Schema/catalog/payload/dispatcher и security-mutation transaction contract не менялись. Публичные denial API возвращают None; внутренний cleanup bool управляет только освобождением admission slot и не является commit ACK. Commit/merge/push и изменение домашнего стенда не выполнялись.

**Frozen snapshot:** backend image `audit-v6`, OCI `sha256:fc7671853d8c3899156415b538268239897f52f158e2a4bc5a608e3d66b9ab57`. Все **272 packaged Python files** совпали с worktree source перед full suite и после него. Actual driver: psycopg **3.3.6**, libpq **18.6 (180006)**, SQLAlchemy **2.1.3**. Обычная backend/Dockerfile сборка содержит helper и pin psycopg 3.3.6; overlay image не используется. Frontend в этом шаге не менялся. Source freeze не является commit или опубликованным release.

| Проверка текущего snapshot | Результат и граница |
|---|---|
| Focused transport/HTTP/config | **27 PASS**: 17 transport и 10 HTTP/config. Windows real helper spawn/kill/reap; SQL driver cancellation branches дополнительно проверены unit tests |
| Required real network runner | **20 PASS / 0 SKIP / 0 FAIL**, 28,83 s финальный запуск; исходные 8 cases сохранены, добавлены IPv6/partial-DNS/failover/TLS/COMMIT |
| Required PostgreSQL regression | **31 PASS / 0 SKIP / 0 FAIL**, 29,94 s; atomicity/cookie/session, statement/lock, capacity, race и recovery сохранены |
| Whole PGDATA/WAL | **2 fault scenarios PASS**; 36 HTTP probes, 24 CLI invocations, 3 actual lifespan refusals, cold-start/maintenance/exact recovery повторены на текущем image |
| Full backend, offline frozen image | **3401 PASS / 78 SKIP / 0 FAIL** из **3479** collected; 8 disjoint module partitions без исключений, все exit=0 |
| Coverage | **88%**, 23246 statements / 2779 missed, gate **85%** unchanged; combined coverage exit=0 |
| Общий project gate | PASS: npm audit **0 vulnerabilities**, frontend **393 PASS / 1 SKIP / 0 FAIL**, ESLint/Ruff PASS |

Full suite выполнялся без внешней DB/network конфигурации; 78 SKIP не объявляются PASS. Выбранные audit PostgreSQL/network cases отдельно выполнены в required mode без пропусков; это не закрывает остальные optional integration fixtures или внешнюю CI. Coverage использовал Python tracer из offline wheel: noexec tmpfs не загрузил C tracer, итоговый coverage gate успешно выполнен без изменения порога.

**Budget и ownership:** deadline 4 s до admission/offload, DNS до 1 s внутри него, cleanup до 0,5 s. На процесс максимум 4 admitted workers/helpers; при перегрузке нет очереди/нового SQL writer. Helper принимает до 8 hosts × 8 IP, stdin 4 KiB, protocol stdout 32 KiB, ограниченное параллельное OS resolution и частичные результаты. Env allowlist не наследует application/PG secrets; Python isolated, Windows child скрытый. Новый endpoint получает min(1 s, остаток / оставшиеся кандидаты), без сброса общего срока. Original host/port/TLS identity и SQLAlchemy adapters сохранены; auth refusal не делает failover. target_session_attrs использует bounded read-only probes; prefer-standby имеет fallback any. Отмена запроса не освобождает слот работающего worker; незавершённый cleanup quarantines slot. SQL wait закрывает owned connection без стандартного дополнительного cancel/wait и повторной INSERT.

**Реальные faults:** silent DNS и silent-first → ready-host больше не расходуют весь срок; four-stalls → healthy-fifth фиксирует audit после ~3,28 s. TLS verify-full positive, wrong hostname и untrusted CA проверены без изменения runtime SSL policy. Фактические IPv4/IPv6 и hostaddr paths пройдены. COMMIT-before/ACK-loss в последнем прогоне — **4,070 / 4,074 s**, исходный HTTP denial и один failure signal сохранены. До forwarding COMMIT нет audit/outbox rows; при потере ACK ровно одна committed row, исходный UUID/payload выходит обычным dispatcher. Memory commit counters не увеличены для unconfirmed attempt, повторной INSERT нет; после снятия relay fault серверная session исчезает из pg_stat_activity. Это проверка после восстановления связи, не обещание мгновенного remote rollback при blackhole. Liveness остаётся responsive.

Runner использует newly owned internal dual-stack network, PostgreSQL tmpfs и синтетические TLS материалы внутри disposable containers; только tests и generated resolver mounted read-only, без host DNS/VPN, host ports и существующих данных. Probe nonroot/read-only: 2 CPU / 1536 MiB / 256 PID / tmp 256 MiB. Финальные network/PG/cluster/full containers и networks удалены по exact owner identity; старый read-only binary/evidence volume сохранён. Приватные logs/JUnit/digests/failed attempts сохранены вне tracked docs, секреты сюда не копируются.

**Сохранённые промежуточные failures:** отсутствующие transport/admission дали исходный unit RED; позднее cancellation/cleanup tests обнаружили premature slot release. Первая network candidate неправильно передала SQLAlchemy AdaptContext в libpq (8 FAIL); адаптеры сохранены отдельно. Auth refusal первоначально пробовал следующий host; prefer-standby первоначально неверно определял suitability (18 PASS / 1 FAIL); fair failover с четырьмя stalls первоначально не доходил до healthy fifth (19 PASS / 1 FAIL). Все эти evidence сохранены, faults исправлены до финального freeze. Старый single-stall lower bound 1,5 s кодировал прежний connect_timeout=2 s; нижняя граница приведена к 0,8 s для endpoint budget=1 s, фактический accepted TCP и верхний HTTP <5 s сохранены. Ни upper gate, ни состав real matrix не ослаблялись.

Первый full-suite запуск прерван как setup fault: /tmp 1 GiB совпал с диагностическим reserve floor, что запрещало seed ещё до тестируемой операции. Failed evidence сохранён; изменён только disposable tmpfs size на 4 GiB logical при memory limit 2 GiB и cleanup завершённого owned shard temp. Product/thresholds не менялись; валидный полный повтор прошёл. TLS setup candidate также сохранил failed attempt: docker cp не читает tmpfs mount; ключи передаются напрямую между owned контейнерами, без host key file.

**Незакрытые gates и следующий порядок:**

1. Совместимость durable PostgreSQL на Windows. Отдельная private venv с psycopg 3.3.6 выявила libpq **18.4 (180004)**; текущий scoped preflight разрешает только проверенный Linux 180006 и отказывается до readiness. Main/local venv не изменён. Нужен native Windows PG/DNS/TLS/SQL/cancel proof и явное compatibility решение; не выдавать SQLite/disabled unit PASS за durable Windows acceptance.
2. Дополнительные профили: service сейчас явно отклоняется; service/passfile, GSS/SSPI, mTLS, реальные Unix sockets и потенциально блокирующие local auth/file helpers требуют отдельного proof. Hostaddr или parsed PEM не закрывают эти условия.
3. Admission burst/resource recovery и SIGTERM при DNS/SQL fault: численные attempts/concurrency, HTTP/легкий API/liveness, PID/fd/thread baseline и zero leftovers; карантин cleanup slot и предел configured termination grace. Существующие sequential faults/unit ownership не заменяют этот gate или production load/CSI.
4. Home Kubernetes upgrade на том же snapshot после Guest Agent/resource check, coherent backup/old digests; HTTPS/OIDC/denial/upload и nonempty restore. Старый audit-v5/home proof не подтверждает новый image. Откат без schema change возвращает известный DNS defect, не GREEN release; durable не отключать автоматически.
5. Required hosted CI фактического release snapshot и применимые внешние adapters/collector. stdout_written по-прежнему не downstream ACK; replicas, trusted-IP, retention/replay и production StorageClass/load остаются отдельными пакетами.

Task 3a и общий rollout остаются открытыми по составным условиям; выбранный Linux network deadline gate GREEN. Docs mirror в main не означает интеграцию runtime.


## Task 3a: Windows, lifecycle и home upgrade 05.10.2026

Продолжение inline в той же рабочей ветке, без commit/merge/push. Product change этого этапа — platform/build guard для проверенного native Windows libpq; модель данных, event payload и dispatcher не изменены. Linux deadline proof расширен lifecycle/burst tests. Сведения ниже относятся к новому `audit-v7`, а прежние audit-v5/v6 результаты и failures остаются историческими.

**Frozen image:** штатный backend Dockerfile, psycopg **3.3.6**, Linux libpq **18.6 (180006)**, SQLAlchemy **2.1.3**. OCI index digest `sha256:eb1dcc921cb50e28048562ec1829cbcc0308b7c2e1ba16bebacc3be9d6f90538`; config digest `sha256:0aa3423cadd88970850b8d86cfd7d8afc685cfddca227b5d86c23cfc75a006a0`. Build revision `kubernetes-audit-windows`. Все **272** packaged Python files совпали с source до full suite и с реально работающим домашним backend; runtime distributions v6→v7 не изменились. В kind использован linux/amd64 manifest `sha256:5660c6c62e4371f1214d2b51f816dc1852d616ff67ade92be4680b01cbced1d2`; OCI index/config/manifest — разные уровни identity, не взаимозаменяемые hashes.

| Проверка текущего snapshot | Результат |
|---|---|
| Native Windows PG | **35 PASS / 0 SKIP / 0 FAIL**: 6 store/preflight + 16 fault/security/rollback/race + 13 multihost/TLS/COMMIT/burst |
| Windows focused transport/config/HTTP | **35 PASS**, включая driver capability guards и завершение самого resolver interpreter после timeout |
| Required Linux network/lifecycle | **24 PASS / 0 SKIP / 0 FAIL**, DNS/IPv6/TLS/failover/COMMIT, burst/cancel и SIGTERM |
| Required PostgreSQL fault regression | **31 PASS / 0 SKIP / 0 FAIL** |
| Whole PGDATA/WAL | **2 scenarios PASS**, 36 HTTP/24 CLI/3 real lifespan refusals, exact recovery |
| Full backend | **3408 PASS / 83 SKIP / 0 FAIL**, все **3491** collected в 8 disjoint module partitions без исключений, каждый exit=0 |
| Coverage | **88%**, 23246 statements / 2772 missed; прежний gate **85%**, exit=0 |
| Project/dependency | PASS, npm audit **0 vulnerabilities**, frontend **393 PASS / 1 прежний SKIP**, ESLint/Ruff PASS |
| Home managed upgrade | Coherent pre-upgrade backup, actual running272 hashes/revision, public HTTPS/OIDC/roles/CSRF/upload/generation/search/chat PASS |
| Home audit | Response→DB→stdout equality для новых login/denial events; **102** исторические immutable rows сохранены по event_id/time/payload hash, held не снят, исторического replay нет |
| Backup вне VM | Новый coherent backup уже на audit-v7: приложение остановлено штатно для snapshot, возвращено Ready; архив перенесён и все **6** artifacts проверены вне VM |

Native proof использовал новый owned PostgreSQL **17.11** на loopback/random port, SCRAM/TLS, private venv psycopg3.3.6/libpq**18.4 (180004)**/SQLAlchemy**2.0.52**. Рабочие PG17/PG10 и основной venv не обновлялись. Actual OS localhost resolver→TLS→SQL/COMMIT пройден за **1,140 s**; owned postmaster остановлен. POSIX-only процессные тесты и Linux /etc/resolv.conf/IPv6 fixture явно не запускались на Windows и не включены в эти 35 PASS. Preflight допускает только две проверенные platform/build пары и deadline-capable wait; это не гарантия для произвольной версии Windows, resolver/auth/file profile.

**Burst/cancellation:** два сценария по 3×24 protected requests, в каждом peak admitted/helpers=4; overflow не создаёт очередь/лишний SQL writer. Health и обычный anonymous auth API остаются отзывчивыми, protected 401/Bearer/request UUID сохраняются. Все helper processes завершены, четыре slots возвращены; cancelled worker освобождает slot после cleanup. Общий AnyIO thread pool создаёт workers при первом burst, поэтому исходный холодный baseline отдельно сохранён; последующие два bursts не увеличивают threads/handles. Test instrumentation больше не удерживает Popen handles завершённых детей, tolerance не увеличивался. Linux samples: threads/fds 1/15 → 26/18 → 26/18 → 26/18; native Windows handles после прогрева 714→714→714, финальные threads возвращены к baseline. Это admission/fault proof, не production throughput matrix.

**SIGTERM:** настоящий Uvicorn в Linux завершён во время DNS и подтверждённого server-side SQL PgSleep. Shutdown **1,113/1,107 s** при grace8s; Application shutdown complete, zero owned helper/server sessions. Uvicorn после graceful cleanup повторно поднимает SIGTERM: exit0/-15 допустим только вместе с подтверждённой очисткой. Четыре bootstrap audit факта сохранены; denial fault не создаёт частичную строку. Последующий healthy denial создаёт ровно один новый immutable event, который writer выводит с тем же payload.

83 skips полного offline suite — отдельные условия optional integration fixtures; они не объявлены PASS. Required audit PostgreSQL/network/storage выполнены отдельно без пропусков. Coverage снова использовал Python tracer из offline wheels на noexec tmpfs, порог не менялся. Нового production frontend build не требовалось: deployable frontend audit-v3 этого этапа не менялся, его прежние build/home proofs сохранены.

**Сохранённые промежуточные failures:** RED Windows driver tests до guard change; private-venv dependency path/setup и inherited pipe при запуске owned postmaster; native fixture hardcoded healthy port/CA и неверное ожидание anonymous auth API; холодный pool и удержанные instrumentation handles; preproof SIGTERM exit/bootstrap expectations. Исправления касались fixtures/измерений после чтения actual API/Uvicorn/resource evidence, не deny contract/deadline/coverage thresholds. Первичная сборка с неверным Docker context и initial index-versus-config comparison также сохранены. Home upgrade завершился до сбоя hash-check из-за отступа в private Python script; исправленный verification-only запуск подтвердил snapshot и полный HTTPS proof, повторного deploy не было.

Перед home upgrade заново проверены VM/Guest Agent, свободная RAM/диск, namespace UID/run label и старые image identities. Автостарт VM включён; согласованная тестовая БД уже остановлена. SSH host key получен через доверенный Guest Agent и проверялся в отдельном private known_hosts; глобальное доверие не менялось. Созданные probe Pod/ConfigMap удалены только после сверки UID, действующая home копия оставлена Ready. Coherent backup старой версии и новый completed backup сохранены; checksum текущего backup manifest `52cb138c9ec7f41042d015c00857d7a29bfdb2421cda528225e52c229bda21f6`.

**Открыто после этого этапа:** service/passfile priority и файловые блокировки, GSS/SSPI/mTLS, real Unix-socket профиль; production load/CSI; новый nonempty restore audit-v7 в отдельный namespace/new PVC/Secrets с hold/no replay (старый audit-v5 restore — исторический proof); hosted required CI на release snapshot и внешние adapters/collector. Начинать дополнительную home копию только после capacity check. Не отключать durable автоматически и не считать возврат audit-v5 исправленным DNS gate. Task3a и общий rollout целиком остаются открыты. В main переносится только согласованная анонимная документация; runtime остаётся в рабочей ветке.


## Task 3a: nonempty home restore audit-v7 05.10.2026

Проверка следующего открытого критерия выполнена inline на **том же frozen audit-v7**, без изменений product source/образов, commit/merge/push. Source и фактически восстановленный backend совпали по всем **272** packaged files, build revision и Linux psycopg3.3.6/libpq18.6/SQLAlchemy2.1.3. Полный backend/coverage предыдущего этапа здесь не запускался повторно: source freeze остался272/272; исторические3408 PASS/83 SKIP/88% не выдаются за новый прогон.

| Критерий | Свежий результат |
|---|---|
| Coherent backup | Writer остановлен штатно, добавлено одно synthetic pending; PG/Qdrant/files сохранены согласованно |
| Restore target | Новый namespace, **4 новые PVC**, новый APP_SECRET_KEY и PG password; прежние claims/credentials не переиспользованы |
| Immutable audit | **136 source = 136 restored rows**; ID/event UUID/time/payload SHA-256 совпали |
| Historical outbox | Добавленное **1 pending→held** до первого start, ранее held сохранён; итог **pending=0, held=2**, исторического replay в stdout нет |
| Auth after restore | Старые session cookie и незавершённый flow отказаны; новый public/none/S256/UserInfo-only login принят, request UUID/payload совпали HTTP→DB→stdout |
| Functional smoke | HTTPS, роли/CSRF, viewer denial, DOCX upload/generation, sources/BM25, chat stream и logout PASS на восстановленной копии |
| Backup after restore | Новый coherent completed backup; после restart target Ready, immutable rows/held/no replay повторно проверены |
| Off-VM verification | Source и completed target archives перенесены с VM, каждый checksum и все **6 artifacts** подтверждены |
| Cleanup | Все созданные probe Pod/ConfigMap/old-auth Secret удалены после UID guard, **0 probe resources** осталось |

Время source backup **43.22 s**; `Manager.restore` до Ready **79.88 s**. Это время конкретного synthetic корпуса в домашнем kind, не production RTO или performance acceptance. Restore использовал сохранённые backend/frontend/storage images и прежний model-stub mode; schema/total/content integrity gates не ослаблялись.

Перед работой проверены VM/Guest Agent, автостарт, RAM/диск и source namespace UID/run label. На VM было около1,5 GiB MemAvailable; согласованная тестовая БД уже остановлена. Чтобы не держать две application/storage копии в RAM, source остановлен **после coherent backup**, исходные PVC/маршруты/replica state сохранены приватно. После source pause отдельный capacity guard пройден до создания target. Восстановленная копия оставлена активной; source остаётся paused с PVC и backup для обратного переключения. Shared synthetic Gateway/IdP и остальные стенды не останавливались. Active-copy pointer, защищённые values и точные namespace/UID остаются в private evidence.

Первый private package check отказал до cluster mutation из-за Windows backslashes в operator hash manifest. Файл приведён к POSIX paths, исходный пакет/логи сохранены, продолжение использовало отдельную директорию operator и тот же content checksum. Syntax fault private proof также обнаружен до запуска. Эти ошибки не меняли приложение, критерии restore или audit thresholds.

Checksum source manifest `1434a23a8a09e6c0719186afe631ba1dd1dbb05dc22fe5ffcb831d322fefa9db`; completed restored manifest `f5c14d316cbb881eb3762383cc61c5b2aa4aeb21630711ed234fda3fa20b6ff0`. Архивы/credentials/содержимое audit payload не включены в публичную документацию.

**Остаётся:** дополнительные service/passfile/GSS/SSPI/mTLS/Unix-socket/file-blocking profiles, production load/CSI и hosted required CI/release integration; external collector/adapters, replica architecture и retention/replay. Отдельный `restore_without_source_vm` требует независимой target инфраструктуры и восстановления из внешнего backup/защищённого источника секретов: нынешний source paused в том же kind/VM этот disaster gate не закрывает. Новый v7 home restore criterion закрыт; Task3a/общий rollout целиком остаются открытыми.


## Task 3a: PostgreSQL file/socket profiles 05.10.2026

Новый поставляемый runner `scripts/kubernetes/tests/audit_profile_acceptance.py` и tests `test_audit_postgres_profiles.py` проверили дополнительные profiles на **том же frozen audit-v7**. Product source272/272/image digest не изменились; main runtime и home target не обновлялись. Final default `--scope all`: **13 PASS / 3 FAIL / 0 SKIP**, exit1. Source/runtime версии — Linux psycopg3.3.6/libpq18.6/SQLAlchemy2.1.3, pinned PostgreSQL17 container; native Windows эти profiles не запускал.

**13 PASS:**11 regular-passfile cases (8 успешных profile/precedence вариантов и3 отказа), real Unix socket и отказ service из URL. Проверены SCRAM, password с colon/backslash, original hostname matching при numeric hostaddr, TLS verify-full, first matching line и wildcard order, URL/PGPASSFILE/PGPASSWORD precedence. Numeric hostaddr-only использует явно заданный sslmode=require как отдельный encryption-only profile; это не доказательство hostname identity. Unix socket подтверждён actual inode и inet_server_addr NULL, SCRAM/SQL/COMMIT без DNS. Successful protected denial записывает один sanitized event с response UUID и exact stdout payload; wrong wildcard, loose mode0644 и missing file сохраняют401/Bearer, один failure signal, zero partial facts и healthy recovery. Service из URL явно отказан, не считается поддерживаемой service auth.

**3 FAIL при прежнем<5s:** environment PGSERVICE/PGSERVICEFILE FIFO блокирует `PQconndefaults` прежде существующего service guard; FIFO passfile и TLS CA блокируют native file-read phase. Последние два child attempts не вернулись за5s (около5,007s до test kill); parent kill/reap подтверждён. Config child checkpoint подтверждает начало service config call до watchdog. File scenarios проверяют zero extra PostgreSQL client sessions относительно baseline, в том числе до передачи application_name; это не фиктивное отсутствие сессий из-за неустановленного label. Необработанный native thread не объявляется завершённым по одному HTTP timeout.

Первый full profile attempt с FIFO в HTTP worker исчерпал внешний180s watchdog; exact owned containers/network/socket volume удалены. Исходный FAIL сохранён. В окончательном proof небезопасный file scenario выполняется в отдельном owned process с5s envelope, всё ещё требует прежний общий deadline, поэтому остаётся FAIL после реального kill. Это защита тестового стенда, не исправление приложения. Setup attempts также сохранены: early role setup без полного diagnostics, несовпадение test DB имени с существующим TLS helper; затем nonserializable AdaptersMap в private test protocol. Исправленный proof реконструирует engine/context внутри child из private stdin URL, не удаляет runtime adapters. Final13/3 содержит только реальные profile failures.

Runner использует internal Docker network без host ports/DNS changes, новый labelled socket volume, tmpfs PGDATA256MiB и read-only socket/test mounts для nonroot backend. Backend probe limits:2CPU/1536MiB/PID256/tmp256MiB, PostgreSQL512MiB/1CPU. SCRAM HBA применяется только к synthetic role в новой owned DB, wrong-password refusal реально проверен до matrix. SUPERUSER этого disposable role проверяет transport semantics, не production least-privilege grants. Actual owned child resources reaped, socket volume/containers/network удалены по exact ID/run label. Host files, VPN/DNS, рабочие БД и home workloads не менялись.

Guards нового socket fixture и прежнего network runner: **12 PASS**; Ruff PASS. GitHub audit-capacity job получил обязательный all-scope profile шаг без continue-on-error/xfail/ослабления criterion. `--scope regular` и `blocked` записывают выборку и не закрывают all gate; required step не использует выборку. Hosted CI и branch protection не выполнялись/не подтверждались, workflow не опубликован. Полный backend/coverage этого этапа не запускался повторно; прежние3408 PASS/83 SKIP/88% — исторический frozen-runtime результат, а не свежая полная suite с новыми tests.

[Password-file documentation](https://www.postgresql.org/docs/18/libpq-pgpass.html) задаёт matching/permissions; [официальный fe-connect.c](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/interfaces/libpq/fe-connect.c) показывает `fopen` до проверки обычного файла и service parsing в defaults. Это объясняет наблюдаемые local-I/O gaps; гарантия nonblocking remote polling не заменяет отдельный file/config proof.

План3a.5 теперь содержит scoped file/config repair, secret/IPC ownership, ранний service refusal, preservation TLS/adapters и обязательную новую image/OS/CI/home приёмку. Непроверенные default home passfile, service success, GSS/SSPI/mTLS/system trust/CRL directory и blocked regular filesystem остаются OPEN. Compound Task3a/rollout не закрываются и общий file-profile gate остаётся **RED**. Нельзя сделать его GREEN простым stat/поздним timeout над оставленным worker, отключением TLS/durable или исключением failing cases.


## Task 3a.5: первый service/startup repair 05.10.2026

Локальный candidate `audit-v8-service-startup` исправляет ранний отказ unsupported service: URL object/dialect kwargs и PGSERVICE, в том числе пустые значения, проверяются до libpq defaults. `validate_audit_transport_profile` запускается до init_db/startup seeds в настоящем lifespan. Disabled/SQLite обходят PostgreSQL guard. Общие schema/event/transaction/dispatcher contracts не изменены; environment не очищается и TLS не ослабляется.

TDD: первая fixture попытка дала11 FAIL из-за неполного pq mock и make_client signature; сохранена как non-proof. Исправленный исходный RED10 FAIL/1 PASS; промежуточная проверка43 PASS/4 FAIL выявила пустое значение, потерянное строковым SQLAlchemy URL parser, ошибочную проверку CSRF-cookie и старую main venv waiting API. Fixtures скорректированы: явно сохранённый пустой параметр задаётся через SQLAlchemy URL object, auth cookie проверяется отдельно от CSRF cookie, используется прежняя private Windows psycopg3.3.6. Отдельный lifespan test сначала RED на seed, затем GREEN с ранним guard. Product исправляет присутствующие значения; не пытается восстановить уже удалённый URL parser параметр.

Final focused native Windows: **57 PASS**, actual psycopg3.3.6/libpq180004; main venv не обновлялся. Проверены ранний guard/precedence/неизменность environment, preflight без shared DB connect, настоящий TestClient HTTP401/Bearer/request UUID/no session-cookie/failure+1, настоящий lifespan до init_db/seed/dispatcher и legacy disabled/SQLite. External dependency/diagnostics hooks lifespan подменены для изоляции; это не целиком поднятый production stack. Старые cache-plugin/Starlette warnings сохранены.

Final required Linux `--scope all` на candidate: **35 PASS / 2 FAIL / 0 SKIP**, exit1. Inventory:16 исходных PostgreSQL profiles (14 PASS/2 FAIL),14 service/startup regressions и7 strict-protocol tests. SCRAM/passfile/Unix socket и оба service refusal cases PASS. FIFO passfile/TLS CA всё ещё превышают5s; owned child reaped, extra server sessions0, exact owned Docker containers/network/socket volume удалены. Protocol допускает только expected timeout/exit0/exact terminal frames; unexpected exception/crash/неполный output не дают PASS. Ранний27-case candidate25PASS/2FAIL также сохранён; новое число37 отражает добавленные cases, а не замену исходной matrix.

Image ID локального кандидата: `sha256:d037c81c61ec7d2c3307c923dec7d715c10c78703f180780d3fd118e4cedd3bb`. Изменены три runtime файла: audit_denial_transport/audit_runtime/main. Предыдущий audit-v7 source freeze больше не подтверждает этот candidate; его успешные home136-row restore и full3408/coverage88% остаются историческими. Новый полный backend/coverage/project, required network/storage, Compose/home upgrade/restore и hosted CI не выполнялись. Кандидат не опубликован, runtime в main и домашний кластер не обновлялись, commit/merge/push отсутствуют.

Часть B и строгий E protocol выполнены; составной Task3a.5 открыт. Следующая обязательная зависимость — A: определить bounded file preparation/refusal, inputs/limits/material ownership; затем C/D для passfile/TLS и release checks. Отказ service выполнен отдельно до завершения A по записанному ruling, поскольку не выбирает файловую архитектуру и устраняет независимо воспроизводимый дефект.


Дополнительная регрессия существующих startup/health/HTTP audit contracts: **29 PASS** на том же private Windows окружении, отдельный набор без пересечения с focused57. Ruff и diff-check PASS. Новый packaged-source proof:272/272 текущих backend hashes совпадают с candidate, ровно3 runtime files отличаются от v7; Linux image psycopg3.3.6/libpq180006 проверены. Это scoped verification, не полный backend/coverage или release proof.


### File-material candidate 05.10.2026 — scoped proof, release OPEN

Task3a.5 продолжен в локальном `audit-v11-files`, image `sha256:bde6a8d1b6e7bd133938a5719f6c9281fcc822f936d8ad5aa64422f3b933a4b7`. Подготовка defaults/passfile/CA/CRL выполняется отдельным owned helper; source file I/O не выполняется в denial DB worker. Все фазы используют исходный абсолютный WORK_SECONDS=4s; helper defaults/prepare ограничены min(1s, remaining budget), cleanup укладывается в общий предел0,5s, HTTP target<5s сохранён. IPC32KiB; passfile1MiB, CA/CRL по4MiB, максимум3 copied files/9MiB. Материалы принадлежат attempt до connection.close, источники не удаляются, новый attempt не использует cache.

Поддержаны в выбранном Linux proof: explicit/env/default HOME passfile, original hostname/port при hostaddr, escaped colon/backslash, password precedence/first-match, missing и Unix mode0644 ignore, default HOME CA, реальный SCRAM/SQL/commit/response→DB→stdout и Unix socket. FIFO/nonregular/oversize/обнаруженная rotation получают fixed unsupported-profile отказ до native connect; mTLS/client key/encrypted key, sslrootcert=system, sslcrldir и sslkeylogfile также отказывают заранее, TLS не ослабляется. Штатный TLS verify-full и отрицательные hostname/CA случаи сохранены.

Defaults helper использует опубликованный ABI PQconndefaults/PQconninfoFree из закреплённого psycopg_binary.libs без импорта всего Psycopg; проверяет libpq build и однозначность library, native defaults parity PASS. Четыре параллельных defaults на2CPU: первоначально четыре timeout1.003s, после устранения тяжёлого импорта0.255–0.260s; численные budgets/acceptance thresholds не повышались. SQLAlchemy adapters остаются в parent. Helper не импортирует app settings и не выполняет SQL; PG values передаются только в private stdin, не argv/inherited environment/logs.

SQLAlchemy NullPool может поглотить DBAPI close exception. Поэтому владелец denial attempt отдельно закрывает tracked connection и проверяет cleanup, сигнализируя failure ровно один раз. Incomplete cleanup quarantines slot; quarantined материалы не запускают второй helper. Реальная PG/NullPool regression подтвердила quarantine и сохранение уже committed immutable event после post-commit cleanup failure. Не утверждать rollback подтверждённого commit.

Свежие проверки этого snapshot: required Linux all **68 PASS/0 SKIP**, network/lifecycle **24 PASS/0 SKIP**, PG faults/capacity **31 PASS/0 SKIP**, whole PGDATA/WAL/cold-start **2 PASS/0 SKIP**. Native Windows focused **101 PASS/6 SKIP** (все6 — POSIX FIFO/default HOME cases); Psycopg3.3.6/libpq180004, основной venv не обновлён. Linux Psycopg3.3.6/libpq180006/SQLAlchemy2.1.3; **274/274 packaged source hashes matched**. Scoped Ruff PASS. Required profile runner сохраняет16 исходных cases; теперь19 PG profiles+14service/startup+8protocol+19file-material+8file-lifecycle=68; default all68, regular66 диагностический, blocked2. Неявные source files заменены attempt-private snapshot/missing paths до native connect.

Исходные13/3, service35/2, первый file snapshot Windows failure, network21/3 и22/2, а также NullPool/repeated-cleanup RED66/2 сохранены в private evidence. DNS instrument теперь идентифицирует только exact resolver helper: новые file children не считаются resolver и не могут преждевременно отметить фазу SIGTERM. HTTP<5s, max4, число bursts и fault thresholds не изменены. Owned контейнеры/network/socket volume удалены после проверки identities; runtime вне этих disposable fixtures не обновлён.

**Открыто:** реальный projected Secret/symlink-generation rotation и multi-file consistency; Windows default paths/protected temp ACL и proof actual helper interpreter reap; точные N/N+1/truncated/nonzero/blocked-stdin IPC edges; blocked regular filesystem metadata/read (тест stalled helper подтверждает kill/reap, не NFS/CSI); file-phase burst/cancel/SIGTERM и ресурсы/tmp recovery после SIGKILL; local temp creation/owner marker fault и cleanup deadline extremes; GSS/SSPI/LDAP/OpenSSL-provider profiles. mTLS/system trust/CRL-directory остаются scoped unsupported. Full backend/coverage/project/Compose и dependency gate этого snapshot, новый home projected-Secret/HTTPS/backup/restore и hosted CI **не выполнены**. Домашний audit-v7 не обновлён. Новый freeze не является commit/publication или полной release приёмкой. Составные C/C.1/D/E сохраняются OPEN; дизайн и ранний config/startup path реализованы в указанном объёме.


### File rotation и lifecycle 05.10.2026 — scoped proof, release OPEN

Итоговый candidate `audit-v13-file-lifecycle`: image `sha256:a2b8c4b2021741b08fc36a7d17eaeb4d16ee780ae43dc6841fc5e47cbee7ec2c`, 274/274 packaged Python hashes совпали с исходниками рабочей ветки. Linux Psycopg3.3.6/libpq180006/SQLAlchemy2.1.3; native Windows Psycopg3.3.6/libpq180004 в отдельном private venv. Основной venv и домашний runtime не обновлены.

**Исправления:** snapshot наблюдает весь используемый набор, включая отсутствующие/ignored inputs, до и после копирования. Появление ранее отсутствовавшего CA и изменение resolved symlink generation даже при общем inode вызывают фиксированный отказ. Старый snapshot сохраняет свои bytes до закрытия; новая попытка читает актуальное поколение без cache. Это доказано на owned POSIX fixture с атомарной заменой `..data`, а не на реальном Kubernetes Secret mount. CRL и actual mounted read-only/fsGroup/platform rotation остаются отдельным gate.

Windows helper устанавливает protected DACL до доступа к источникам/копирования: наследуемый доступ только текущему token user, SYSTEM и Administrators. Меняются права только exact owned snapshot directory; source ACL и общий Temp не меняются. Ошибка Win32 API прекращает подготовку, permissive fallback отсутствует. Native GetFileSecurityW проверяет каталог и копию; SID/SDDL не выводятся. Основание: [SetNamedSecurityInfoW](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setnamedsecurityinfow), [GetSecurityDescriptorDacl](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-getsecuritydescriptordacl). Mapping roaming application-data проверен; полноценный Windows service account/default-file/SSPI профиль этим не доказан.

**Проверки итогового образа:** required profile **86 PASS/0 SKIP**, network/lifecycle **24 PASS/0 SKIP**, PG faults/capacity **31 PASS/0 SKIP**, whole PGDATA/WAL/cold-start **2 PASS**. Profile matrix сохраняет исходные16 cases: 19 PG +14service +8profile-protocol +19materials +8lifecycle +4rotation +6file-phase faults +8file-protocol =86. Default `all86`, diagnostic `regular84`, `blocked2`; hosted CI ещё не запускался. Native focused **114 PASS/8 POSIX SKIP**; три Windows-specific cases реально выполнены. Scoped Ruff PASS. Полный backend/coverage этим набором не заменён.

File fault helper инжектирует задержку в metadata/read после наблюдаемого маркера достигнутой фазы. Четыре burst/cancel сценария: по72 запроса, 36 owned helpers, одновременно ≤4; завершение1.372–1.431s, в cancel случаях12 отмен. Проверены соседние healthy endpoints, возврат всех admission slots, отсутствие поздних INSERT, baseline DB sessions/helpers/temp dirs и устойчивость FD/threads после прогрева; recovery даёт exact DB→stdout denial. Два настоящих Uvicorn SIGTERM сценария: shutdown1.072/1.167s при grace8s, четыре bootstrap immutable events сохранены, helpers завершены, следующий denial доставлен. Инъекция на границе syscall не является proof реально заблокированного NFS/CSI I/O или uninterruptible kernel state.

Восемь IPC/cleanup cases проверяют ровно32KiB и32KiB+1 request/response, truncated JSON, nonzero exit, blocked stdin, ошибку старта второй I/O thread и уже истёкший cleanup deadline. Проверены fixed error categories/canary absence, ограниченное чтение, reap/registry и quarantine. Native Windows timeout удерживает handle фактического interpreter, подтверждает его завершение и пустой registry. Expired cleanup case не запускает новый helper. Это не закрывает все filesystem cleanup faults.

**История:** missing-CA regression сначала RED; Windows DACL сначала RED при PASS interpreter reap. Промежуточный v12:71 PASS, затем file lifecycle77 PASS; первый77-case run содержал6 fixture ERROR из-за недостающего fixture import, он сохранён и не принят за bounded refusal. После исправления fixture77 PASS. Итоговый v13 включает все изменения. Ранние v5/v7/v11 failures и результаты сохранены; HTTP<5s, work4s, helper phase1s, cleanup≤0.5s и admission4 не изменены. Private evidence: `rotation-20261005`, предыдущие directories не перезаписаны; disposable resources удалены runners после проверки ownership.

**Остаётся:** bounded temp/owner creation (сейчас parent делает mkdtemp/marker I/O), marker/cleanup I/O faults и безопасная очистка orphan после SIGKILL; actual projected Secret с CA/CRL, read-only/fsGroup и реальный NFS/CSI; дополнительные Windows account/auth/crypto profiles. mTLS/system trust/CRL-directory пока явно unsupported. Полный backend/coverage/project/Compose/dependency check на новом frozen snapshot, capacity/coherent backup и новый home projected-Secret/HTTPS/restore, hosted release CI и platform acceptance ещё OPEN. Старые v7 full/home proofs не переносятся на v13. Runtime реализован в рабочей ветке; в main синхронизирована документация, commit/push/deploy не выполнены.


### Bounded temp creation и cleanup ownership 05.10.2026 — scoped proof, release OPEN

Новый candidate `audit-v14-file-creation`, image `sha256:d4f2e5d08c9b689cbb17122010226970daa022d1409b64c1395d0f4189c8bd0e`; 274/274 packaged source hashes совпали. Linux Psycopg3.3.6/libpq180006/SQLAlchemy2.1.3, native Windows Psycopg3.3.6/libpq180004. Runtime только в рабочей ветке; main получает docs mirror. Основной venv, домашний runtime и целевые среды не обновлены.

Создание каталога, установка Windows DACL и запись32-byte owner теперь выполняются операцией `create` existing file helper. Parent выбирает UUID/path лексически, без mkdtemp/gettempdir/marker write и проверки writable directory. На Unix выбирается первый непустой TMPDIR/TEMP/TMP, иначе `/tmp`; на Windows TEMP/TMP, иначе SYSTEMROOT/Temp. Относительный/пустой/слишком длинный/NUL путь отказывает. Непригодный явно выбранный путь не переключается незаметно на другой temp root. Configure writable temp mount в read-only deployment; глобальные Temp ACL и source files не меняются.

`mkdir` exclusive, mode0700 Unix; marker exclusive, mode0600 и полностью записывается перед success. Только создавший каталог живой helper может убрать собственный незавершённый marker и пустой каталог при обычной ошибке записи/ACL. Новый cleanup helper требует exact marker: отсутствующий каталог безопасно подтверждает отсутствие материалов, markerless/partial/foreign directory отказывает; symlink/junction directory не принимается. Collision не меняет чужой marker. Error cleanup возвращает fixed incomplete status; timeout после mkdir/до marker оставляет exact owner в private quarantine registry и удерживает admission slot. Неподтверждённые пути не удаляются и повторный cleanup не запускается.

Владение также сохраняется при незавершённом prepare или resolver: материалы помещаются в private quarantine registry. Исправлены два отдельных RED, где slot уже удерживался, но ссылка на snapshot owner терялась. Registry не является публичным status API, не сериализует пути/credentials и не служит автоматическим orphan sweeper. После SIGKILL родителя эта память исчезает: безопасное восстановление владения и operational orphan cleanup остаются отдельными gates.

**Приёмка v14:** Linux required profile **100 PASS/0 SKIP**, network/lifecycle **24 PASS/0 SKIP**, PG faults/capacity **31 PASS/0 SKIP**, whole PGDATA/WAL/cold-start **2 PASS**. Native focused **127 PASS/9 POSIX SKIP**; scoped Ruff и diff whitespace check PASS. Profile100 сохраняет предыдущие86 cases и добавляет14 creation/owner cases; diagnostic regular98/blocked2, default all100. Hosted CI пока не выполнен.

Новые сценарии: запрет temp/marker I/O в parent, отказ до spawn при истёкшем deadline, marker write failure и refused rmdir, чужой collision/marker и POSIX directory symlink, missing/markerless cleanup, настоящий HTTP denial во время injected mkdir/marker write stall, cleanup owner-read stall ≤0.5s, unreaped-child registry и сохранение owner при prepare/DNS cleanup failure. HTTP401/Bearer/server UUID/no cookie, ровно один failure signal и прежний HTTP<5s проверены. До mkdir slot возвращается после подтверждения отсутствующего каталога; частичный marker сохраняет owner/slot, не маскируется успешной очисткой. Synthetic fixtures восстанавливают/удаляют только точно захваченные test owners после reap; это не product procedure для orphan recovery. Первые7FAIL/1SKIP, expiry regression1FAIL/37PASS/4SKIP и оба ownership RED сохранены; затем focused47PASS/9SKIP. Failed proofs не заменяются последующим PASS.

Стоимость нового child измерена: по48 owned helpers на каждый72-request burst вместо36 (defaults/create/prepare/cleanup на каждую принятую попытку), одновременно максимум4. Metadata/read burst/cancel теперь1.622–1.679s; FD/threads/temp baseline и no late INSERT/recovery→DB→stdout сохранены. Настоящий SIGTERM read/metadata shutdown1.047/1.069s при grace8s,4 bootstrap events сохранены. Work4s, helper phase1s, cleanup≤0.5s, HTTP<5s/admission4 не увеличены. Это selected injected stalls, а не реальный uninterruptible kernel/NFS/CSI I/O.

**Открыто:** positive short/partial marker write fault, крайние marker/unlink/rmdir/reap cases на файловой системе и operational orphan cleanup после parent SIGKILL с защитой активных/чужих attempts. Actual projected Secret CA/CRL/default/explicit/read-only/fsGroup, NFS/CSI и дополнительные OS/auth/crypto profiles отдельно. Full backend/coverage≥85%, project/Compose/dependency check на новом release snapshot, capacity/coherent backup и home projected-Secret/HTTPS/restore, hosted CI и target platform acceptance не выполнены. V7/v11/v13 proofs исторические и не закрывают release v14. No commit/push/deploy.


### Cleanup edges и budget 05.10.2026 — scoped proof, release OPEN

Candidate `audit-v16-cleanup-budget`, image `sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`; packaged Python source274/274 matched. Linux Psycopg3.3.6/libpq180006/SQLAlchemy2.1.3, native Windows Psycopg3.3.6/libpq180004. Runtime изменения только в рабочей ветке; main содержит docs mirror. Основной venv, домашний v7 и целевые среды не обновлены. Private evidence: `cleanup-20261005`; предыдущие freeze/results сохранены.

Исправлены два owning RED: `close(deadline=0)` больше не сбрасывает абсолютный deadline через truthiness fallback; ошибка закрытия IPC pipe получает fixed cleanup-incomplete category и сохраняет exact process registry/owner quarantine. Marker symlink/junction отказывает до чтения; POSIX open использует O_NOFOLLOW/O_NONBLOCK, fstat допускает только regular32-byte marker, bytes должны точно совпасть с owner. FIFO и alias tests сначала2FAIL, затем PASS. Это проверка выбранных fixtures, не гарантия против произвольного hostile filesystem/TOCTOU на всех ОС.

Новые19 cleanup cases покрывают positive short writes→полные32bytes, partial-positive failure, zero write, owner open/close; marker open/close, file/marker unlink, rmdir failure; нулевой deadline; pipe-close failure; marker symlink/FIFO; реальные owned helper stalls в unlink/rmdir при бюджетах .5/.4s; healthy delayed cleanup .3s. Incomplete cleanup сохраняет owner/slot и не запускает retry. Failure после marker unlink оставляет markerless каталог: продукт не переписывает marker и не объявляет его удалённым. Только test teardown восстанавливает точно захваченный synthetic owner. Source bytes неизменны; stalled helpers reaped, incomplete handles остаются tracked.

Первый Linux v15 required119: **115PASS/4FAIL** — после отмены остались3 snapshot directories, slots quarantined, последующие lifecycle cases получили cascade failures. Diagnostic focused6PASS проблему не закрыл. Instrumented required119: **116PASS/3FAIL**, phase-only observations подтвердили cleanup FileTimeout на .252/.264/.294s. Очистка обрывалась на прежней рабочей доле .25s при общем .5s. Healthy .3s owning regression сначала1FAIL. В v16 бюджет перераспределён на .4s cleanup work/.1s reap, общий .5s сохранён. Defaults/create/prepare1s, work4s, HTTP<5s, admission4/no queue прежние. Сокращение резерва reap явно учитывается: неподтверждённый kill/wait/IPC close оставляет quarantine и registry, без успешного capacity verdict. Instrumentation пишет только phase/error class/duration, не paths/parameters/content. Source-read fault fixture теперь сверяет actual source FD; owner read не выдаётся за source-read stall.

Отдельный POSIX parent SIGKILL test запускает настоящий изолированный parent, передаёт synthetic конфигурацию через stdin и атомарно публикует внешний fixture witness PID/start_ticks/UUID/exact directory. Controlled guard отказывает активному parent, после SIGKILL(-9) подтверждены сохранённые private0700 snapshot/source bytes; неверный owner отказывает, exact fixture cleanup удаляет только свой каталог. Это **test-only ownership proof**, не продуктовый orphan CLI/sweeper, не recovery markerless directory и не реальный Pod/CSI failure. Process-private registry после смерти parent исчезает. Продуктовая процедура восстановления требует отдельного design и owning tests active/foreign/PID reuse/races/partial marker.

**Приёмка v16:** required Linux profiles **120PASS/0SKIP** (88.86s), network/lifecycle **24PASS/0SKIP**, PostgreSQL fault/capacity **31PASS/0SKIP** (33.84s), whole PGDATA/WAL/cold-start **2PASS**. Profile сохраняет исходные16 и прежние100, добавляет19 cleanup+1SIGKILL; default all120, diagnostic regular118/blocked2. Native Windows focused **144PASS/12POSIX SKIP** (112.90s); выбранные Windows-specific checks выполнены, warnings сохранены. Scoped Ruff PASS. Полный backend/coverage этим набором не заменён.

**Повторяемость:** десять последовательных прогонов26 cases (19cleanup+1SIGKILL+6file-phase), тот же immutable image, CPU2/nonroot/read-only-root/private tmpfs; **260PASS/0SKIP**. Каждый прогон — новый pytest process/basetemp; PostgreSQL/TLS fixtures reused только как setup. Все10 required runs сохранены, runner прекращает работу при первом failure. Эта выбранная стабильность не заменяет full120/network24/PG31/storage2, все новые roadmap scenarios, hostedCI и actual home/platform tests. Exact owned Docker containers/network/socket volume удалены после identity checks; private evidence сохранён.

**Дальше:** actual Kubernetes projected passfile/CA/CRL explicit/default/rotation/read-only/nonroot/fsGroup и private writable temp mount с проверкой container-restart/Pod-delete lifetime; operational orphan witness/recovery и оставшиеся OS/auth/crypto. Chart сейчас не задаёт explicit /tmp emptyDir; Docker tmpfs proof не переносится на chart. Real NFS/CSI/uninterruptible kernel I/O отдельно. После runtime freeze — full backend/coverage≥85%, project/Compose/dependency, coherent backup/resource check, home HTTPS/Secret/restore immutable rows/no replay, hosted CI и target platform acceptance. Составные C/C.1/D/E, общий rollout и replica/collector gates OPEN. Исторические RED и v7 home/full не подменены. Commit/push/deploy не выполнены.


### Actual projected Secret и emptyDir 05.10.2026 — selected file proof

Неизменный v16 `sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500` импортирован в домашний kind после текущего Guest Agent/capacity/Ready check. Отдельный synthetic namespace/Pod, работающие приложения/БД не обновлялись. Actual running source274/274 matched; source-manifest/image archive checksum и pinned SSH проверены. Source key/value/namespace/host реквизиты не входят в общую документацию. Private evidence `secret-20261005`; новый lab runner `scripts/kubernetes/tests/audit_secret_acceptance.py` и mounted probe `backend/tests/audit_secret_probe.py` находятся в рабочей ветке.

Pod: UID/GID/fsGroup1000, nonroot, readOnlyRootFilesystem, drop ALL/no escalation, service-account token disabled; actual projected Secret defaultMode0440 без subPath, private memory emptyDir64Mi на/tmp. Requests100m CPU/64Mi RAM/64Mi ephemeral, limits1CPU/256Mi RAM/256Mi ephemeral. Memory-backed emptyDir входит в memory usage, его sizeLimit не заменяет RAM limit. Временные attempt каталоги0700, копии0600/currentUID. Snapshot helper проверял **synthetic bytes**, а не настоящий PEM/CRL или пароль PostgreSQL.

Проверены explicit/default HOME paths, read-only source mutation refusal и исходные bytes; direct projected passfile0440 игнорируется, CA/CRL копируются. Test-only stage0600 принимает passfile bytes. Правила libpq не ослаблены: [Unix password file](https://www.postgresql.org/docs/18/libpq-pgpass.html). FsGroup/read access сам по себе не делает group-readable passfile пригодным. Staging здесь — тестовая операция, не product synchronizer; init-copy на старте не доказывает обновление passfile после Secret rotation. Для эксплуатации нужен отдельно выбранный env-password/private-file layout и его auth/rotation proof.

Kubelet выполнял настоящее обновление projected Secret. Sampling через production bounded helpers увидел обе coherent CA/CRL generations; наблюдавшиеся snapshots не содержали смешанных bytes, rotation_refusals=0. Sampling не детерминирует смену поколения строго посередине copy: owning refusal branch ранее проверен synthetic v13 fixture, actual mid-copy interruption остаётся отдельной проверкой. Сохранённый old attempt snapshot после rotation сохранил исходные hashes и удалён только по захваченному fixture owner. CRL removal и повторное появление подтверждены actual mount и fresh explicit/default snapshots. Source symlinks/..data не переписывались вручную, monkeypatch в mounted sampling отсутствует. Не обещается мгновенная Secret propagation: ожидание до120s относится к стендовому kubelet convergence, work4s/helper1s/cleanup.5s не повышены. [Projected volumes](https://kubernetes.io/docs/concepts/storage/projected-volumes/) поясняют ограничения subPath updates.

Первый actual run прошёл Secret проверки, но FAILED на restart: SIGKILL из sibling kubectl exec не завершил namespace PID1. [Linux PID namespaces](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html) ограничивает такую отправку; этот FAIL/исходные fixture sources сохранены, не выданы за restart. Новый fixture protocol сначала RED(exit2, отсутствующий hold), затем GREEN с actual self-exit73. Итоговый Pod PID1 завершился по собственному тестовому запросу: runner проверил exitCode73, рост restartCount и сохранение Pod UID/emptyDir marker. Это self-exit container-restart proof, **не SIGKILL PID1 proof**. После удаления своего Pod и создания нового UID emptyDir/state/attempt dirs отсутствовали; final snapshot прошёл. Это actual temp lifetime proof, не product orphan sweeper и не audit COMMIT/dispatcher crash proof. [emptyDir lifecycle](https://kubernetes.io/docs/concepts/storage/volumes/#emptydir) различает container restart и Pod removal.

Все выбранные mounted/lifetime checks PASS, exact owned namespace/resources удалены; итоговые hashes/verdicts/UID evidence скопированы на локальную машину. Local ownership tests6PASS: foreign/replaced UID/label/namespace и реальные rotate/cleanup entrypoints отказывают до мутации; scoped Ruff PASS. Initial missing-runner RED,4PASS и6PASS сохранены. Это отдельный lab runner, ещё не hosted CI gate. Прежние v16 profiles120/network24/PG31/storage2/native144 и10×26 остаются предыдущим scoped proof, автоматически не повторялись и не объявлены новой полной приёмкой.

**Открыто:** generic product chart configuration для writable private temp/projection/passfile layout; valid PEM/CRL/native TLS/auth/revocation и permission-change/мульти-Secret divergence matrix; operational orphan recovery с durable ownership/auth/PID reuse/race protection; реальный CSI/NFS и дополнительные OS/auth profiles. Chart пока не добавляет explicit /tmp emptyDir. Full backend/coverage≥85%, project/Compose/dependency на release snapshot, managed home app upgrade/HTTPS/coherent backup/restore immutable rows/no replay, hosted CI и target platform ещё не выполнены. Home приложение остаётся на прежнем образе; v16 присутствует только в node image cache после proof. C/C.1/D/E/rollout/replica/collector gates OPEN. Commit/push/deploy приложения не выполнялись.


Дополнительная сверка image identity: actual kind import index ссылается на exact frozen v16 OCI index; прочитана цепочка linux/amd64 manifest/config, current local source274 unchanged. Не предполагалось равенство import-wrapper/config digest. Первоначальная ошибка diagnostic selector (import index не содержит platform/config непосредственно) сохранена; исправлен только read-only verifier, runtime image не менялся.

### Product temporary storage 05.10.2026

Узкий пакет C/C.1 реализован в `codex/kubernetes-rollout`: отдельные disk emptyDir
backend1Gi/frontend256Mi, explicit TMPDIR/TEMP/TMP, scheduler requests128Mi/64Mi
и limits2Gi/512Mi, строгая integer Mi/Gi schema и отказ size/request>limit.
Runtime config/Secret не могут менять temp paths. Writer Jobs наследуют backend
том/ресурсы; несовместимый volume/mount/env отклоняется. Legacy old-image backup
без нового тома сохранён. CI список контрактов дополнен новым тестовым модулем.

Owning RED: 15 failures/0 skips с настоящим Helm после исправления отдельной
ошибки импорта fixture (обе записи сохранены). Первый общий прогон chart/lifecycle:
120 PASS/0 SKIP,155.45s. Финальный temp/lifecycle/release/runtime-trust:
**120 PASS/1 SKIP**, 161.24s (0:02:41): standalone bootstrap потребовал kubectl,
которого не было в Python image. Kubectl взят из существующего toolbox; отдельный
повтор пропущенного bootstrap дал **1 PASS/0 SKIP**. Все121 выбранных случаев
выполнены; исходный skip сохранён. Один известный pytest warning cache_dir при
disabled cacheprovider. Helm lint PASS, scoped Ruff с backend config и diff-check
PASS. Первоначальный Ruff без backend config показал сторонние style rules;
повтор с проектной конфигурацией прошёл, код вне scope не исправлялся.
Проверки использовали существующие локальные frozen v16 Python и toolbox Helm,
read-only candidate mount и network none;274 runtime source hashes совпали с frozen v16.

Это render/schema/lifecycle proof, не actual product deployment или sizing.
Предыдущий отдельный Secret Pod proof использовал memory64Mi и не доказывает
дисковые размеры/нагрузку нового chart. Работающее home приложение не обновлено.
Открыты corpus sizing (active/quarantined/orphan snapshots, parser/export, Next
cache, job /work+logs), disk-pressure/eviction, durable orphan ownership, единый
PostgreSQL CA/CRL application/Jobs/restore профиль и valid native crypto matrix,
полная release/home upgrade/CI/target-platform приёмка. Password layout выбран
runtime Secret env; passfile0440 не преобразуется в silently accepted credential.
