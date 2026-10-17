# Архив плана до упрощения — 10.10.2026

> Только история требований и проверок. Этот документ не является очередью работ.
> Актуальные объём и критерии завершения: [упрощённый план](2026-10-03-kubernetes-rollout.md).
> Старые REQUIRED, OPEN и «следующий шаг» ниже не назначают новых задач.

---

# Kubernetes Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Делегирование возможно только при отдельном выборе пользователя.

**Goal:** сначала запустить существующее приложение в трёх Kubernetes-средах с внутренними PostgreSQL/Qdrant, затем по отдельным этапам подготовить frontend, API и workers к репликации; каждый этап предварительно проверять дома.

**Architecture:** первая поставка — compact Deployment с одним Pod и двумя контейнерами приложения, отдельные StatefulSets PostgreSQL/Qdrant и постоянные тома. В DEV/TEST используются модельные заглушки, в PROD — реальные API. Далее frontend отделяется от файловой диагностики, фоновая обработка получает долговечную очередь, API и workers масштабируются независимо.

**Tech Stack:** существующие Python/FastAPI, Next.js, PostgreSQL 17 и Qdrant; Helm, Kubernetes, Gateway API/kgateway, Vault Secrets Operator, GitLab/Artifactory и существующий GitHub CI. Домашний первый стенд — kind в Ubuntu VM.

**Spec:** [Архитектура и ограничения](../specs/2026-10-03-kubernetes-deployment-design.md).

## Текущий статус — 10.10.2026

| Область | Фактический статус | Следующий результат |
|---|---|---|
| Принятый код | База `f551f5f`, runtime565 в Kubernetes worktree; private582 не перенесён в runtime/main, рабочие установки не обновлены | Завершённый совместимый compact-кандидат, затем интеграция и доставка |
| Состав кандидата | Private582/source579 map53e5; scope28 актуален на проверенную часть R2 (source pin). Native backup принят на прежнем mapf646; кандидат не перенесён в runtime | Обычный restore и оставшиеся общие lifecycle/release gates |
| Общий lifecycle | Native backup format3 принят; R1 empty-target проверен owning Windows/Linux и actual PG/Qdrant/files. Общий CLI/unlock и restore OPEN | R2: source pin проверен компонентно; следующий результат — настоящий bounded PG import с подтверждением транзакции, затем связанный restore |
| Первая поставка | Текущий общий compact release не прошёл итоговую сквозную приёмку | Штатный CLI, непустой согласованный restore дома, один release snapshot и required checks |
| Последующие этапы | Полный opt-in TLS и реплики остаются отдельными незавершёнными этапами | После compact: TLS T3–T6; frontend → durable очередь → API → workers |

Подробные числа старых snapshot вынесены в
[историческую сводку приёмки](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md#история-компонентных-snapshot--перенесена-10102026).
Их PASS относятся только к проверенным исходникам. Обнаруженные пробелы и рекомендации:
[повторная проверка очереди](../reports/2026-10-10-kubernetes-delivery-loop-review.md).

### Карточка исполнения после проверки зацикливания — 10.10.2026

**Принято:** полный native backup format3 и consumer вне VM. Private-кандидат
не принят в runtime/main; restore и release открыты. Доказательства и пределы
ведутся только в [приёмке](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md#обычный-backup-format3--producerconsumer-и-native-проверены-10102026).
**R1 проверен:** штатный restore выполняет fixed empty-target probe и доходит до
следующего неподдержанного PG input. Реальная read-only проверка больше не
инициализирует каталоги приложения; dirty/lost ACK/target drift сохраняют отказ.
[Доказательства R1](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md#обычный-restore-r1--fixed-empty-target-проверен-10102026).
**R2 частично проверен:** обязательный внешний source pin и отказ при подмене
backup проверены на Windows/Linux; это не подтверждение независимого издателя
и не импорт. [Предел проверки](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md#обычный-restore-r2--source-pin-проверен-импорт-открыт-10102026).
**Единственное следующее действие — R2:** подключить fixed bounded `pg_restore`
к уже существующему owning `Manager.restore(source, expected_manifest_sha256=...)`
и журналу, восстановить контрольную строку в PG17 и подтвердить COMMIT/revision.
Тест достижения raw PG input уже есть; заново создавать его или source-pin этап
не нужно. Порядок и критерии — только в [карточке 2б.3.3](#карточка-2б33--обычный-restore-активна).
Принятые backup/producer/completion/DELETE и R1 не реализуются заново.

**Предел расширения:** новый prerequisite добавляется только с указанием
сломавшегося штатного caller, фактического отказа либо точного уже принятого
требования, минимальной правки и её критерия завершения. Остальное — после compact.
Не добавлять обязательные зависимости на основании одной гипотезы.

**Контроль исполнения:** если два последовательных цикла дали только новые
helper/proof/status, но не закрыли критерий caller, перед третьим разобрать
конкретный блокер и сократить изменение до него. Это не повод вновь проверять
весь план, запускать прежнюю матрицу или ослаблять safety gate. Итог продолжения:
закрытый критерий, проверяемый diff, ограничения и следующий штатный сценарий;
рост числа PASS сам по себе не является поставкой.

**Измеритель продвижения:** на каждом продолжении фиксировать один наблюдаемый
результат штатного caller (запись/чтение данных, завершение actor, start или unlock)
либо один воспроизводимый отказ, который мешает этому результату. Pin/helper,
source map, новый отчёт и очистка стенда являются частью работы, но не закрывают
R-пункт. Следующий эксперимент получает конкретную гипотезу и критерий заранее;
повтор без изменения причины/inputs запрещён. После двух продолжений без такого
выхода остановить расширение работ: указать оставшийся вызов, минимальную правку
либо открытый вопрос пользователя, без нового полного аудита. Вопрос обязателен
только при отсутствующем решении; устаревший статус не является вопросом.

### Уточнение текущей очереди — 10.10.2026

**Единственная общая очередь — таблица ниже; порядок внутри restore задан только карточкой 2б.3.3.** Исторические checkpoints,
их next/OPEN и номера экспериментов не являются повторными поручениями.
Цель ближайшего этапа — переносимый compact release candidate с работающим
обычным lifecycle, а не новый private proof. Первая поставка сохраняет local и
Compose bundled/external; replica-этапы выполняются после неё.

Историческая компонентная база до правок Scale: source579/map840
`840178341e1d9c4c9704de6a4e1ba3cd63d538be98a22b4b7ccb9da357ea7604`:
**806 PASS на каждой Windows/Linux, 0 SKIP**, disjoint299/137/370, и **native A R7
PASS138,48/180с**, independent1,55/60с: install/upgrade, revision1/2, next и
conditional unlock. A проверяет метаданные при запрещённых Pods; SQL/HTTP/Ready,
непустой restore и готовность выпуска этим не подтверждены. Ссылки и ограничения:
[актуальная приёмка](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md#компонентная-приёмка-и-native-a-r7--10102026).
Эти проверки завершены; повторять их на неизменных inputs для обновления статуса
не требуется. Runtime565/main/рабочие установки пока не приняли private582.

| Порядок | Результат работы | Критерий завершения |
|---|---|---|
| 1. Состав compact-кандидата — подготовлен | Scope28 актуален на map53e5/source-pin части R2; native format3 backup mapf646 и off-VM consumer сохраняют свою приёмку. Обычный restore и общий шаг 2б OPEN | Не начинать инвентаризацию заново. Перед очередной проверкой обновить manifest/diff на изменённые байты; это ещё не принятие private582 в runtime |
| 2. Общие recovery-пути — активен шаг 2б | Штатный CLI: подтверждённый результат записи, безопасное продолжение новым процессом, origin/admission/activation там, где они необходимы этому пути | Нет второго writer или старта при unknown effect; положительный interrupted resume и отказ небезопасного продолжения на одном кандидате. Из private реализации включается необходимый завершённый набор; общие guards не обходятся |
| 3. Принять compact lifecycle дома | Install → update → migration failure/rollback → backup → согласованный непустой restore PG/Qdrant/files на независимом target | Настоящие Pods, SQL/HTTP/Ready и сохранность данных; новая сессия после разрешённой ротации ключа, durable audit outbox, измеренные downtime/RPO/RTO. Одна replica и Recreate; модельные заглушки допустимы и явно отмечены |
| 4. Выпустить переносимый комплект | Неизменяемые chart/images/operator kit, digests, values-примеры, runbook и отчёт применимости | Required CI, coverage/dependency/license/consumer, frontend, local и Compose gates на итоговой ревизии; reviewable интеграция с учётом авторизации. Точная матрица ниже остаётся обязательной |
| 5. Расширить после compact | Opt-in PostgreSQL TLS T3–T6, затем frontend replicas → durable очередь/владение задачами → API → workers | Собственная приёмка каждого этапа. TLS-specific B/C не становится prerequisite disabled-поставки; если среда требует TLS, для её приёмки enabled gates обязательны |

### Ближайший результат и закрытие шага 2

**Активный результат — 2б: рабочий Job/toolbox/import с безопасным завершением. Scale API 2а проверен на своём snapshot.** Код:
`scripts/kubernetes/lifecycle.py`; регрессия:
`backend/tests/test_kubernetes_operation_effect_journal.py`.

- [x] **2а. Scale API — ранее принятый snapshot.** Сохранены прежние результаты;
  Windows40/Linux253 и native Scale R6 подтвердили scale0/1 → следующий effect →
  conditional unlock. Latest source seal и причинные RED/контроли приведены в
  [отчёте Scale](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md#scale-api--компонентная-и-нативная-приёмка-10102026).
  Известный ACK не восстанавливается matching GET; unknown/replaced target
  отказывает. Unit `stop` сохраняет ожидание старых Pods. Настоящий Pod retirement
  не доказан noPods-сценарием: он остаётся обязательным в 2б и home-шаге 3,
  до допуска нового writer/start. Этот PASS не разрешает принять partial582 в runtime.
- [ ] **2б. Закрыть Job/toolbox/import и retirement.** Состав и зависимости
  уточнены в карточке ниже. Готово только после штатных migration/backup/restore
  callers, подтверждения завершения удалённых процессов и проверки результата
  импорта. Windows PASS создания Job не завершает этот пункт; matching GET,
  exit0 или пустой selector не заменяют результат записи и остановку writer.
- [ ] **2в. Положительное продолжение новым процессом.** Готово: реальный crash и
  новый owner в исходном сроке, подтверждённая terminality прежних actors,
  единственный writer; второй successor и ambiguous effect получают отказ.
- [ ] **2г. Общий допуск восстановленного приложения.** Готово: origin/promotion,
  fresh/adoption, generation/admission/activation и session/outbox barriers
  интегрированы в тот же CLI на disabled-профиле. Изолированный key/OIDC proof
  не разрешает start. Обычный update сохраняет ключ; явный DR допускает ротацию
  и требует нового входа. Enabled-only PKI проверки принадлежат следующему TLS этапу.

#### Карточка 2б: результат вместо очередного эксперимента

**Вход:** один существующий private582-кандидат; scope28 соответствует проверенному
source-pin части R2 map53e5. Native backup mapf646 сохраняет отдельную историческую приёмку. Rollback hold и PG export проверены компонентно; остальные callers и весь
шаг 2б остаются открытыми. **Выход:**
штатные `Manager.job/toolbox/exec/finish_job/migrate` проходят рабочий путь;
неизвестный результат оставляет безопасный отказ. Новый controller/store и новый
кандидат для каждого компонента не нужны. Частичная реализация остаётся private.

| Часть | Состояние и конкретное действие | Проверяемый результат |
|---|---|---|
| 2б.1 — создать Job | Компонент реализован и проверен Windows/Linux269; текущая регрессия Windows/Linux291 включает его. Не повторять неизменный create-набор | Exact create ACK, UID/template и journal подтверждены; malformed/lost ACK не восстанавливается чтением. Native create входит в общий живой Job-сценарий ниже |
| 2б.2 — завершить Job и детей | Terminal Job и управляемый completion toolbox проверены компонентно и native backup; conditional Job retirement принят в этом caller. Application stop и общий CLI OPEN | Own Job удаляется один раз; потеря ACK запрещает replay. Pod с потерянными labels, orphan/replaced UID, неизвестный Node/Lease не дают нового writer/start/unlock; одно GET absence не закрывает retirement |
| 2б.3 — выполнить команду и импорт | Pinned readiness и typed completion готовы компонентно; добавить typed SQL/files/Qdrant callers. PostgreSQL Pod требует отдельный StatefulSet/Pod/Node/storage binding, а не Job permit | Exec допускается внутри намеренно живого owned toolbox только по узкому permit. Его жизнь по-прежнему запрещает handoff/start/unlock. COMMIT, поколение и целостность данных проверяются отдельно; unknown import не повторяется |
| 2б.4 — пройти штатный путь | Проверить `migrate`, toolbox, backup и restore на одном завершённом кандидате; consumer проверяет тот же CLI | Один native сценарий с настоящими Pods подтверждает create → owned exec → terminality → conditional delete → следующий effect/unlock. Отказ миграции и потерянный ACK сохраняют данные и блокировку. Metadata-only/noPods proof этот критерий не закрывает |

**Порядок:** toolbox, rollback hold и PG export приняты компонентно; подробные
результаты — только в acceptance. Metadata-протокол integrity/totals/snapshot
проверен с API fixture. Полный backup API/native caller и off-VM consumer приняты; следующее действие —
обычный restore по единому порядку R1–R6 в 2б.3.3: imports, нужные barriers 2г,
migration/application retirement, native restore, handoff 2в и финальный CLI 2б.4.
Источники — в acceptance; новый
controller/store и повтор прежней матрицы для статуса не нужны. Conditional DELETE сейчас разрешён только после заранее наблюдённого
успешного terminal Pod и свежего Node/Lease; жизнь toolbox не обходится удалением.
Результат миграции/SQL/import проверяется отдельно от прекращения процесса.
Отдельный обязательный остаток 2б.2 — `Manager.stop`: до scale0 фиксировать
UID прежних application Pods/ReplicaSet/Node и подтверждать прекращение их
процессов, включая потерю labels. Receipt Scale API и пустой selector этого не
доказывают; компонент 2а не переоткрывается, noPods proof не повторяется.
После завершения связанной реализации выполнить затронутую регрессию на обеих ОС
и один общий native Job-сценарий. Отдельный native стенд только ради счётчика
создания Job не запускать. До разблокировки exec внутренний отказ сохраняется;
обход guards ради прохождения CLI запрещён.

##### Конечный остаток 2б и ближайшая проверка

- [x] **Completion/retry — компонент проверен.** В
  `scripts/kubernetes/lifecycle.py` методы `_operation_toolbox_complete` и
  `finish_job` сначала различают confirmed/unknown/отсутствующий receipt.
  Confirmed completion только наблюдается: publisher не запускается повторно,
  исходный срок retirement не начинается заново. Unknown по-прежнему запрещает
  replay/delete/start/unlock. В owning effect tests проверен
  `test_compact_live_toolbox_known_completion_retry_observes_without_replay_or_new_deadline`:
  после confirmed exec временный отказ retirement → успешное наблюдение и один
  DELETE без второго exec; после исходного deadline → отказ без записи/DELETE.
  Причинные RED и GREEN сохранены; receipt содержит исходные UTC/monotonic bounds
  в пределах180с и owner lifetime. Duplicate completion отказывает.
- [x] **Реальный completion program — Linux-компонент проверен.** UID/source-bound publisher и
  waiter проверены на Linux: правильный marker завершает waiter, чужой UID/source,
  повторная публикация и symlink не дают ложного результата. Проверяется
  исполняемый фиксированный код с remap только /work в изолированный каталог.
  Публикация теперь атомарна: private file write/fsync → Linux
  `renameat2(RENAME_NOREPLACE)` → directory fsync. Отсутствие поддержки отказывает;
  фактическая применимость в target Pod проверяется в 2б.4. Python/Downward UID
  контракт не переносится автоматически на PostgreSQL container.
- [x] **2б.3.1 — PostgreSQL backup/export, компонент закрыт.** Карточка ниже
  выполнена: настоящий архив восстановлен отдельно, unknown сохраняет отказ.
  Native Kubernetes остаётся в 2б.4; повтор этого компонента не нужен.
- [ ] **Оставшиеся callers 2б.3: активна 2б.3.3 restore.** Backup
  integrity/totals/Qdrant/files/revision приняты компонентно и native в 2б.3.2;
  consumer вне VM проверен. Теперь в том же кандидате закрыть
  restore SQL/files/Qdrant и migration result. Для каждого фиксировать конкретный существующий вызов,
  его target/результат и контроль unknown, используя общий journal и validators.
  Completion marker не разрешает произвольный exec и не доказывает импорт.
- [ ] **Application retirement 2б.2.** Закрыть указанный выше `Manager.stop`:
  captured UID/ReplicaSet/Node, включая потерю labels; старый writer прекращён
  до запуска нового. Это отдельная обязанность, не повтор Scale API proof.
- [ ] **Один интеграционный выход 2б.4.** После связанной реализации сверить
  manifest/diff, один раз проверить затронутый Windows/Linux набор и выполнить
  единый живой Job/caller сценарий. Известный результат позволяет следующий
  effect; lost ACK сохраняет отказ. Этот финальный PASS зависит от нужных restore
  barriers 2г и handoff 2в (R4–R6); не откладывать их за завершение полного CLI.
  Не создавать следующий компонентный checkpoint или повторять 806/A/291 без причины.

##### Карточка 2б.3.2 — полный backup format2 (принята)

**Результат:** `Manager.backup_stopped(root, old) -> Path` возвращает полный
проверенный комплект `postgres.dump`, `data.tar`, `qdrant.tar`, `totals.json`,
`release.json`, optional trust bundle и финальный `manifest.json`.
Положительный полный caller и отказные контроли прошли Windows449/Linux480;
ранний prefix-test отдельно сохраняет отказ неподдерживающего fixture.
Карточка остаётся открытой до настоящего Pod-пути и успеха всех producers.

**Files:** modify `scripts/kubernetes/lifecycle.py`: `Manager.exec`,
`backup_stopped`, outcome validation и существующий completion/retirement;
test `backend/tests/test_kubernetes_operation_effect_journal.py` и
`backend/tests/test_kubernetes_lifecycle.py`. Существующие
`backup_writer_operation_command/result`, safe tar/snapshot validators и
bounded stream transport переиспользуются. Новый executor/store/backup format
и реорганизация большого lifecycle-модуля не входят в этот результат.

**Interfaces:** вход — тот же stopped application/runtime/storage target,
owned toolbox и common journal; PG export и metadata receipts уже confirmed.
Выход — `Path`, пригодный для format2 consumer. Хэш файла не заменяет проверку
содержимого; journal не содержит corpus, сырых Secrets или бинарных файлов.

- [x] **Owning RED:** добавить тест полного `backup_stopped`, который ожидает
  возвращённый `Path` и успешно читает весь комплект через `verify_backup`.
  Использовать непустые валидные tar/JSON fixtures, сохраняя существующий
  отказной тест префикса. Зафиксировать первый неподдержанный fixed вызов;
  не считать ожидаемый `OperationError` положительным backup-сценарием.
- [x] **Передача:** подключить только существующие fixed команды для
  `data.tar`, `qdrant.tar`, `totals.json` и revision к тому же journal.
  Ограниченный spool, size/hash, UID/source/effect/target, исходные сроки и
  fsync сохраняются; весь архив не загружается в RAM. На принятых файлах
  проверить safe tar members, Qdrant snapshot checksum/size и закрытую totals
  schema прежними validators. Truncated/extra/checksum/unsafe tar/invalid JSON
  и lost ACK не подтверждают effect и не разрешают повтор неизвестного действия.
- [x] **Согласованность и finalization:** после выгрузки повторить totals и
  сравнить с before, подтвердить revision и неизменность artifact hashes/target.
  Producer `complete` сам пишет `/work/complete` и завершает waiter: согласовать
  один completion с `finish_job`, без второго publisher и без проверки Running
  после уже разрешённого выхода. Переиспользовать существующую проверку
  готовности без преждевременного completion либо включить её в управляемый
  completion. Known retry наблюдает исходный receipt; unknown и исходный deadline
  сохраняют отказ. Не обходить retirement ради manifest.
- [x] **Публикация:** проверить staged содержимое/identity до готового manifest,
  подтвердить toolbox terminality/retirement и опубликовать финальный manifest
  последним через существующие безопасные file primitives. Ошибка проверки,
  ENOSPC, short write, artifact drift, отказ completion/retirement и потеря ACK
  не оставляют доступный consumer `complete:true`. Проверить этот отказ через
  обычный caller; format2 и non-common/local/Compose поведение сохраняются.
- [x] **Настоящая проверка выполнена; исторический критерий:** на существующем Lab/домашнем стенде
  исполнить этот же caller в одном owned namespace с непустым PG/Qdrant/files
  набором и настоящим toolbox Pod. Проверить успешные producers, полученные
  файлы и согласованные totals/revision, подтвердить прекращение собственных
  actors и сохранность baseline. API fixture и Linux UID/expiry refusal этот
  пункт не закрывают. Если существующий guard блокирует запуск, локализовать
  конкретный вызов и необходимую правку; не запускать новый стенд и не обходить
  guard. Это ранний участок общего 2б.4, а не новая параллельная очередь.

**Приёмка закрыта:** native backup и off-VM consumer прошли на одном map32bb;
временные Job/Pod/operator отсутствуют, все13 effects confirmed, baseline сохранён.
[Подробности](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md#настоящий-backup--native-и-off-vm-приняты-10102026).
Пункты ниже выполнены и остаются доказательством пути, а не поручениями нового
исполнения. Старые uncertain journals/locks не переигрывались и не разблокированы.

**Причинное исправление — выполнено, без нового этапа:**

- [x] **Воспроизвести задержку Job controller и ограниченно дождаться Complete.**
  `test_compact_live_toolbox_waits_for_job_complete_after_valid_ack` проверяет
  Pod exit0 до Job Complete, однократные completion/DELETE и confirmed journal.
  `_operation_toolbox_wait_complete` сохраняет исходный deadline, UID/spec и
  Node/Lease; strict terminal validator не ослаблен. Причинный RED сохранён.
- [x] **Закрыть реальную гонку dispatch до actor binding.**
  `test_actual_toolbox_completion_cannot_publish_before_actor_binding` использует
  настоящий Popen. Fixed publisher ждёт ровно один разрешённый байт и EOF в stdin;
  transport передаёт его только после durable actor binding. Проверены отказные
  входы, unknown ACK, known retry и исходные сроки. Windows/Linux acceptance
  относится к snapshot в приёмке; это не GREEN последующей правки DELETE.
- [x] **Завершить именно доказанный контракт DELETE.** Files:
  `scripts/kubernetes/lifecycle.py`: `_operation_job_deletion_frame`,
  `_operation_effect_job_delete_outcome`, `_operation_job_retirement_absent`;
  test `backend/tests/test_kubernetes_operation_effect_journal.py`:
  `terminal_job_case`, `live_toolbox_case`,
  `test_compact_job_retirement_accepts_server_deletion_generation`.
  Реальный ACK — `batch/v1 Job`, исходный generation+1, deletionTimestamp,
  grace0 и foregroundDeletion. Сохранить exact UID/namespace/name, прежний полный
  spec hash, UID/RV preconditions и строгую terminality. Согласовать оба fixtures
  с этим ответом; отказной `ack-generation` должен проверять generation+2,
  отдельно проверить старый generation, grace1, grace bool и чужой template.
  Не принимать произвольный generation или Status лишь по OpenAPI. Поддержку
  другого ACK вводить только по доказанному контракту поддерживаемой версии.
- [x] **Получить GREEN текущих байтов на Windows/Linux.** Сначала разобрать
  сохранённый failed запуск, не запускать новый native сценарий при красных тестах.
  Run: owning module с
  `-k "compact_job_retirement or compact_terminal_job or compact_live_toolbox or compact_backup_complete or actual_toolbox_completion"`,
  `--confcutdir=backend/tests`, отдельные basetemp/log, прежний budget180с.
  Linux использует существующий Lab и cached tools/images; Linux-only cases
  выполняются без SKIP. Проверить ещё deleting Job до двух наблюдений absence,
  malformed/lost ACK, UID/spec/Node drift, исходный deadline и запрет
  replay/next writer/start/unlock/manifest при unknown. Положительный тест не
  подгонять к коду: он воспроизводит сохранённый настоящий ACK. Прежние462/495
  не повторять без изменения общего guard либо итоговой release-проверки.
- [x] **Зафиксировать проверенный source и выполнить один native backup.**
  После GREEN сохранить новый map всех579 файлов; обновить hashes только
  изменённых файлов в существующем diff27/scope.json, сохранив старый manifest.
  Убедиться, что тесты, staged payload и operator используют один snapshot.
  Проверить terminality/PID absence старых operators и точные старые Job/Pod либо
  их отсутствие, fresh Node/Lease, ownership и доступную ёмкость. Absence старого
  Job не подтверждает unknown DELETE и не разрешает дописывать его manifest.
  Новый owned scope допустим только для изменённых проверенных байтов, без
  обхода прежнего writer/lock. Переиспользовать существующий adapter/подготовку
  и cached images; budget900/action180 не увеличивать. Успех: confirmed
  completion/retirement и consumer-verified Path с непустыми totals/revision.
- [x] **Закрыть результат и перейти к restore.** Перенести полный комплект вне
  VM, проверить его существующим Windows `verify_backup`, сверить hashes,
  totals/revision и сохранность baseline. Записать результат один раз в
  acceptance. При успехе следующая работа — обычный restore в новый пустой
  target, а не новый audit/cleanup/toolbox proof.

**Порядок команд:** после неуспешной правки сначала проверить, какие файлы уже
изменены, затем завершить её; зависимый тест/запуск разрешён только после exit0
предыдущего шага. Живой child наблюдается по исходному handle, без второго caller.
При новом отказе сохранить фазу/лог/journal, исправить причину в том же контракте
и проверить её до следующей мутации. Если два изменённых запуска не продвинули
caller дальше прежнего отказа, разобрать этот transport/guard непосредственно;
не продолжать автоматически серией новых namespaces и не повторять полный аудит.

Привилегированный OS scope operator уже проверен как предусловие домашнего
запуска. Поддержка unprivileged host operator не подтверждена и не добавляется
сейчас отдельным prerequisite. Подготовка StorageClass/probes/seed import уже
исправлена; заново реализовывать её не требуется. Нового решения пользователя
для этой технической правки нет.

**Контракт ближайшего исполнения — один настоящий backup:**

| Шаг | Действие и граница | Доказательство окончания |
|---|---|---|
| Подготовить один раз | Переиспользовать `home_run.Kube` из существующего домашнего harness и зафиксированные cached images. Минимальный adapter в `compact-delivery/home-backup-native.py` только связывает этот harness с текущим `Manager`; не реализует свой controller, journal или validators. Один новый owned namespace внутри существующего кластера допускается для изоляции, создание другого кластера не требуется | Точные source hashes, image identities, namespace/ресурсы/UID и baseline записаны без Secrets. Приложение в тестовом target остановлено, PG/Qdrant Ready, PVC связаны, непустые данные созданы, подготовительные actors завершены |
| Выполнить caller | Запустить текущий `Manager.acquire` и `Manager.backup_stopped(root, old)` с настоящим kubectl transport. Operator работает на существующей Ubuntu VM через имеющийся SSH; kubeconfig не копируется на Windows. Seed/binder и передача исходников входят в эту подготовку, не становятся самостоятельными этапами | Реальные PG dump, integrity, totals, Qdrant snapshot, tar streams, revision, after totals и единственный completion выполнены. Нет mock API, fixture receipts или подмены guards |
| Принять комплект | Существующий `verify_backup` читает полученный `Path`; bytes/checksums и непустые totals/revision согласованы. Сохранить комплект вне VM и повторно проверить consumer. Проверить terminality/retirement именно временных actors, захваченных этой операцией; постоянные PG/Qdrant остаются по lifecycle-контракту | Один пригодный format2 комплект, сохранённые receipts и terminal result, отсутствие второго completion; сохранность baseline. Запись в существующей acceptance закрывает только native backup 2б.3.2 |

**Пределы этой проверки:** stopped target — предусловие `backup_stopped`, а не
доказательство `Manager.stop`. Успех этого метода не доказывает весь `backup` CLI,
`release_lock`, PG checkpoint/admission, restore либо запуск приложения; эти
незакрытые критерии остаются в 2б.2/2б.4/2г. Lock нельзя снимать ради чистого
тестового отчёта. Модельные заглушки допустимы; качество LLM не входит в backup.
Стандартный stub-профиль не менять только для обхода consumer guard: при его
реальном отказе исправлять owning контракт по причинному тесту.

**Условие выхода из подготовки:** уже сохранённый read-only preflight используется
при неизменных inputs; перед мутациями обновляются только необходимые текущие
ownership/capacity/Node/Lease проверки. Следующее продолжение должно дойти до
реальной попытки caller либо зафиксировать конкретную ошибку подготовки с
командой, фазой и причиной. Ещё одна инвентаризация, новый seal или рост PASS
не закрывают эту обязанность. Driver не требует отдельной полной regression
matrix, если продуктовый код не менялся.

**При отказе:** сохранить тот же run/child handle, исходный deadline и journal;
timeout наблюдения не объявлять terminality. Не повторять unknown effect, не
создавать новый namespace ради обхода оставленного writer, не чистить R5/R1
quarantine. Повтор разрешён после локализации причины и изменения соответствующего
входа/кода, с минимальной проверкой; неизменные Windows449/Linux480 не повторять.
Использовать существующий home budget900с и action180с без их продления;
истечение срока остаётся отказом. Техническая ошибка разбирается автономно;
вопрос пользователю нужен только для действительно нового решения/авторизации.

**Окончание:** один полный backup принят consumer; результат настоящего Pod-пути
и ограничения записаны один раз в существующей acceptance. Windows/Linux
проверяют затронутый контракт на одном source snapshot; прежние неизменные
component-наборы не перезапускаются ради нового счётчика. Успех backup ещё не
принимает restore, общий deploy, handoff либо выпуск.

**Следующие конечные результаты 2б:** (1) обычный restore в новый пустой target
с проверкой непустого PG/Qdrant/files результата и unknown-import refusal;
(2) migrate с проверенной revision и безопасным отказом без start на partial;
(3) captured application retirement; (4) общий CLI/create/exec/retire/next/unlock
на одном кандидате. Их карточки уточнять по owning caller при переходе, без новой
полной проверки истории. После двух helper-only циклов вместо закрытия backup
зафиксировать конкретный незавершённый пункт этой карточки и исправлять его;
рост PASS, новый seal и документация не закрывают ни один из перечисленных outputs.

##### Карточка 2б.3.3 — обычный restore (активна)

**Итоговый результат:** штатный `Manager.restore(source)` восстанавливает непустые
PG/Qdrant/files в новый namespace/PVC, подтверждает totals/revision, выполняет
разрешённую DR-ротацию ключа, удаляет сессии/flows и удерживает старый outbox.
Первый start допускается после schema/origin/admission/activation; unlock — после
полного checkpoint. Промежуточный импорт с остановленным приложением полезен для
проверки, но не закрывает restore, общий CLI или release.

**Порядок без обратной зависимости:** нужные restore части 2г подключаются до
разрешения import/start; 2в проверяет handoff после готовности этого пути. Финальный
2б.4 выполняется после этих связок, а не считается завершённым перед ними.
Наличие byte-valid format3 не разрешает запись: protected origin проверяется до
первого импорта, проверка допуска приложения — до start.

**Files:** `scripts/kubernetes/lifecycle.py`: `backup_stopped`, `restore`,
`Manager.exec` и существующие typed permit/outcome helpers;
`backend/tests/test_kubernetes_operation_effect_journal.py`,
`backend/tests/test_kubernetes_lifecycle.py`,
`backend/tests/test_kubernetes_backup_format3_files.py` и existing format3/trust validators.
Использовать существующий private582 кандидат, Lab/home harness и producer;
не добавлять отдельный recovery controller или обход общих guards.

- [x] **Согласовать common compact backup с действующим restore trust контрактом.**
  Owning RED подтвердил format2 producer, отказ current consumer читать format3
  и отсутствие legacy refusal при disabled desired=None. Теперь common producer
  выдаёт format3 с обязательным `release.postgres_tls:null`; null привязан к
  confirmed fixed export с PGSSLMODE=disable, hash/size dump и той же target
  generation последнего revision effect. Existing strict format3 validator
  используется и для prepared/published manifests; atomic staging находится вне
  закрытого artifact set. Unknown legacy и saved/desired PG mode/hash mismatch
  отказаны до acquire. Windows/Linux и native/off-VM приняты на mapf646;
  [подробности](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md#обычный-backup-format3--producerconsumer-и-native-проверены-10102026).
  Принятый format2 комплект не переписан; legacy/non-common consumer сохранён,
  но automatic legacy restore новым kit не разрешён. Enabled producer остаётся
  в отдельном opt-in TLS этапе: его public CA/CRL/native trust нельзя объявлять
  пройденными по disabled PASS. Protected origin/import/start authorization
  также не выводятся из byte-integrity результата.
Оставшиеся подрезультаты выполняются последовательно. Один тест полного caller
сохраняет связь между ними; API fixtures не выдаются за native или SQL-приёмку.

| Порядок | Работа в существующем caller/journal | Проверяемый выход |
|---|---|---|
| R1 — проверен компонентно и actual read-probe | В `test_kubernetes_operation_effect_journal.py` добавить owning `Manager.restore(source)` test и узкий fixed empty-target action в `lifecycle.py` | Причинный RED первого неподдержанного exec, затем подтверждённая пустота PG/Qdrant/data; dirty target и target drift отказывают до записи. Полный caller до следующего неподдержанного действия остаётся незавершённым |
| R2 — активен, pin проверен компонентно | Внешний ожидаемый digest обязателен; подтвердить независимый источник expected pin перед native записью. Подключить bounded `pg_restore` input к exact PG StatefulSet/Pod/Node/storage через существующий журнал | Непустая контрольная строка восстановлена в независимом PG17, hash/size входа совпадают с разрешённым artifact; подтверждены единая транзакция и saved Alembic revision. Lost/malformed ACK не разрешают replay, следующий import, start или unlock; source pin сам по себе не является origin/promotion verdict |
| R3 | Подключить safe data/Qdrant tar input, snapshot restore, audit hold, totals/integrity/revision в bound toolbox | PG/Qdrant/files совпадают с backup по содержимому и totals/revision; traversal/symlink/усечённый stream отказывают; actors физически завершены. Промежуточный результат: приложение остановлено, lock сохранён, полный restore OPEN |
| R4 | В том же restore подключить нужные части 2г: source/target generation, schema, admission/activation, DR key/session/outbox barriers; связать migrate и captured application retirement | Owning caller не обходит guards; отказ любого barrier исключает start/unlock; успешный путь сохраняет outbox hold и требует нового входа. Разрешение ротации уже получено, повторный вопрос не нужен |
| R5 | На одном candidate выполнить Windows/Linux affected checks и настоящий `Manager.restore` в новый namespace/PVC существующего домашнего Kubernetes | Непустые PG/Qdrant/files, разрешённый start/Ready, новый login, подтверждённый checkpoint/unlock и независимый baseline observer. Saved image/model/storage identity совпадает; source и target различаются |
| R6 | Выполнить 2в handoff и завершить 2б.4 через штатный CLI на том же candidate | Реальный crash/new owner в исходном TTL, старый writer terminal, второй successor/unknown effect отказаны; migrate failure и restore success доступны обычному CLI. После этого домашний lifecycle и release checks из шагов 3/4 |

- [x] R1: owning restore test и fixed empty-target probe; actual PG/Qdrant/files
  read-only/dirty controls проверены в Docker Lab. Native Kubernetes restore не принят.
- [ ] R2: независимый источник разрешённого source и настоящий bounded PG import;
  проверен только source-pin/подмена, полный критерий остаётся OPEN.
- [ ] R3: связанный PG/Qdrant/files результат при остановленном приложении.
- [ ] R4: подключённые barriers, migration и application retirement.
- [ ] R5: полный native restore/start/unlock, Windows/Linux affected checks.
- [ ] R6: положительный handoff и итоговый штатный CLI.

**Исполнимая граница R2:**

- [x] `Manager.restore(..., expected_manifest_sha256=...)` и restore-only CLI
  `--expected-backup-manifest-sha256`: отсутствие/несовпадение pin и изменение
  manifest/member/каталога отказывают до import. Windows24 PASS/11 owned-Linux SKIP,
  Linux24 PASS/0 SKIP; проверка текущего source — map53e5. Fixture вычисляет pin
  для синтетического комплекта: это тест целостности, не доказательство издателя.
- [ ] До первой native записи назвать существующего производителя и независимый
  защищённый канал ожидаемого digest точного backup manifest. Значение не брать
  из проверяемого backup самим restore; собственный hash/sidecar не даёт origin.
  Это незавершённая граница P2-B, а не «существующий готовый backup verifier».
  Общий интерфейс уже выбран; новый issuer/controller не добавлять. При отсутствии
  такого источника native import/start остаются OPEN, owning/SQL fixture проверки
  могут продолжаться с явным синтетическим provenance.
- [ ] В `lifecycle.py` связать selected `postgres.dump` с source context и
  стабильным файловым descriptor; private spool проверить по hash/size до записи,
  отклонить replacement, symlink/hardlink и усечённый input. Текущий предел dump
  1 ГиБ — явное ограничение кандидата, не обещание поддержки любого корпуса;
  объём больше предела получает отказ и отдельную оценку до изменения лимита.
- [ ] Добавить один fixed `postgres-import` action в существующий effect journal:
  exact PG StatefulSet/Pod/Node/storage, original expiry и prepared actor до stdin;
  разрешён только выбранный spool, generic exec/source grant не появляется.
  На удалённой стороне проверить полный input/hash/size/archive list до SQL;
  `pg_restore --single-transaction --exit-on-error --no-owner --no-acl` и проверка
  saved Alembic revision предшествуют typed подтверждению результата.
- [ ] Owning test `test_compact_restore_pg_import_commits_before_next_barrier`
  вызывает настоящий `Manager.restore`, пересылает bytes в независимый PG17,
  читает восстановленную контрольную строку и revision, проверяет confirmed import
  в журнале. Kubernetes API fixture назвать fixture; затем native проверка той же
  связки включается в R5, без второго контроллера. Текущий тест лишь достигает
  неподдержанного raw input (`test_compact_restore_existing_caller_reaches_bound_postgres_import`).
- [ ] Fault controls: подмена source/target до и во время input, truncated/hash
  mismatch, PG error/transaction rollback, lost/malformed ACK после записи,
  timeout/live actor. Ни один случай не разрешает replay/start/unlock; после
  успешного импорта следующий action — audit hold R3. Не повторять принятый
  native backup для этих controls. Закрытие R2 требует реального SQL результата,
  полного исходного deadline и завершения actor, а не только нового command helper.

**Ограничение исполнения:** подготовка, source map и запись результата входят в
соответствующий R-пункт, не получают собственной новой очереди. После двух
helper-only циклов фиксировать одну конкретную причину незавершённости этого
caller и исправлять её; новый scope/controller/stand вместо результата запрещён.
Полный рефакторинг `lifecycle.py` не является prerequisite. Уже принятые проверки
повторять только при затронутом контракте, новой воспроизводимой ошибке или в
итоговой release matrix. Бюджеты безопасности сохраняются. Итог каждого R-пункта:
проверяемый diff, его caller-result и явный предел; весь restore закрывает только R5/R6.

##### Карточка 2б.3.1 — штатный PostgreSQL backup/export

**Компонентная приёмка завершена:** Windows356/Linux385, actual PostgreSQL archive2.
Kubernetes API в caller-test остаётся fixture; полный backup и 2б.4 OPEN.

**Owning code:** `scripts/kubernetes/lifecycle.py`: `Manager.backup_stopped`,
`Manager.exec` и существующий effect journal. Проверки:
`backend/tests/test_kubernetes_operation_effect_journal.py`,
`backend/tests/test_kubernetes_lifecycle.py`. Готовые export primitives/tests
format3 переиспользовать только при совпадении контракта; подключение всей TLS
ветки либо миграция backup format2 не являются задачей этой карточки.

- [x] В owning caller-test воспроизвести текущий отказ fixed `pg_dump` с `output`:
  common lock включён, приложение остановлено, target корректен. Проверка должна
  дойти до `backup_stopped`/`exec`, а не только вызвать command factory. Установить
  точную причину отказа, сохранив защищённость неизвестного результата.
- [x] Добавить узкую поддержку этой команды и её бинарного output в существующий
  journal/dispatch. PG Pod допускается по собственным StatefulSet/Pod/Node/storage
  binding, без toolbox Job permit и без предположения о Python в PG image.
  Сохранить fixed argv, исходные budgets, secret redaction и старый non-common path.
- [x] Проверить непустой custom archive на настоящем PostgreSQL17: `pg_restore --list`,
  согласованные размер/hash переданного файла и независимое восстановление
  контрольных строк в отдельную тестовую БД. Источник остаётся неизменным.
  Это приёмка PG-архива; согласованность всего PG/Qdrant/files проверяется позже.
- [x] В том же caller проверить target drift, обрыв/усечение stream и lost ACK:
  нет confirmed/export success и manifest complete, повтор неизвестного effect,
  миграция, start и unlock запрещены. Частичный файл не выдаётся за готовый backup.
- [x] Проверить затронутую область Windows/Linux на одном snapshot и обновить
  существующие diff/manifest и acceptance один раз. Переиспользовать имеющийся Lab,
  без отдельного controller/store/стенда. Native Pod/CLI приёмка остаётся в 2б.4.

**Окончание карточки:** штатный caller получает проверенный непустой PG-архив;
контроли отказов сохраняют данные и блокировку; результат доступен проверяемым diff.
Helper-only PASS и рост общего числа тестов этот результат не закрывают.
**Карточка завершена и не задаёт следующую команду.** Integrity/totals/snapshot
уже имеют API-поддержку. Продолжение определяется только активной карточкой
2б.3.3; исторический переход к `check_integrity.py` удалён. Audit hold/export
не повторяются без изменения их inputs или воспроизводимого нового дефекта.

**Ограничение объёма:** этот остаток не вводит новый controller/store, произвольный
remote executor или новый стенд. Новый prerequisite допускается только с
воспроизводимым отказом штатного caller либо ссылкой на уже согласованное
обязательное требование; предположение записывается как гипотеза, не как новый
release gate. При изменении общего guard проверяется его затронутая область;
узкий RED/GREEN не запускает автоматически полную межплатформенную матрицу.
Оригинальные бюджеты безопасности и required итоговые checks сохраняются.

**Один цикл работы:** выбрать строку этой карточки, воспроизвести конкретный
отказ, исправить owning code, проверить затронутый контракт и перейти к следующей
строке. Проверки caller/consumer относятся к этой же реализации. Запрещено
заменять результат ростом числа артефактов или общей повторной инвентаризацией.
При обнаружении нового обязательного препятствия указать сломанный caller,
контракт и минимальную правку; побочное улучшение уходит в этап после compact.

После R6 (связанные 2б/2в/2г) перейти к шагу 3 и домашнему lifecycle, затем шагу 4.
Partial582 не переносить в runtime: неподдержанные рабочие effects пока блокируют
обычные операции. Каждый подпункт закрывается своим результатом; весь шаг 2
не переоткрывается после изменения одного подпункта.

Средовые DEV/TEST/PROD проверки выполняются в отдельной deploy-копии на тех же
артефактах. Отсутствие параметров конкретной среды не блокирует завершение общего
комплекта, но не позволяет объявить его введённым в этой среде.

**Правила выполнения:** одна незавершённая поставляемая задача за раз. Новая
подзадача должна закрывать конкретный критерий текущего результата; самостоятельное
улучшение harness переносится за его пределы, если оно не требуется для достоверной
проверки. После двух одинаковых отказов прекращать повтор того же эксперимента,
фиксировать причину/новую гипотезу или менять реализацию; это не остановка всей цели.
Повтор expensive matrix — только из-за затронутых inputs, нового дефекта или
обязательной финальной проверки кандидата. Сроки безопасности/изоляцию не ослаблять
ради PASS; разделение групп не превращает их в один прогон в прежнем бюджете.

Перед запуском проверки записать: закрываемый критерий, изменённый вход или
гипотезу, минимальную проверку и следующий продуктовый шаг при PASS/FAIL.
Два отказа в одном месте без новой гипотезы запрещают третий такой же прогон:
сначала локализовать причину коротким наблюдением. Если диагностика не дала новых
данных, зафиксировать технический блокер и работать над независимым критерием;
не превращать этот блокер в вопрос пользователю без необходимого внешнего решения.
Новые результаты записывать один раз в acceptance report/его evidence; план меняется
только при закрытии шага или изменении зависимости. Наблюдение live controller
продолжается по прежнему handle: timeout отдельного ожидания не разрешает новый
запуск. Диагностика начинается с локального поля/кадра или узкого профиля; global
trace всего pytest не используется без конкретной необходимости. Новую общую матрицу и повторное
ревью всего плана не запускать ради обновления счётчика/статуса. Harness менять
только для доказанного препятствия текущей проверке, в исходных бюджетах.

Статус различает «реализовано», «компонент проверен», «сквозная приёмка», «выпущено».
Обновлять эту очередь при закрытии результата или изменении блокера; подробные
эксперименты вести в отчёте, не размножать их по всем планам. Исторические FAILED
сохраняются; R5 quarantine и R1 retirement остаются отдельными эксплуатационными
вопросами и не разрешают delete/reuse/replay/unlock без доказанного допуска.

### Граница первой поставки и открытые вопросы

Ближайшая поставка — общий compact/bundled комплект и домашняя приёмка на
`training-ubuntu`, а не внедрение в конкретный внешний кластер. Шаг 3 сначала
проверяет вместимость VM/диска, доступность Docker/kind и сохранность работающих
сервисов, затем запускает реальные Pods. При нехватке ресурсов действует уже
выданная авторизация обеспечить нужную ёмкость; чужие данные не удаляются.
Артефакты backup находятся вне исходной VM, а restore выполняется на независимом
target. Reboot/power-loss drill проверяет порядок старта и восстановление;
включение VM само по себе не доказывает работоспособность стека.

Неизвестные параметры конкретной платформы (storage/network, IdP, registry,
collector, ресурсы и обязательность PG TLS) фиксируются в окруженческой копии и
проверяются перед её DEV/TEST/PROD приёмкой. Они не требуют доступа или очередного
опроса для завершения общего комплекта. Genuine вопрос пользователю возникает
только при отсутствии решения, влияющего на данные, полномочия или требования;
технический дефект/test failure устраняется в рамках текущей работы.

TLS T3–T6, Kubernetes external, реплики и рефакторинг не добавляются как новые
предпосылки compact/bundled. Local и оба Compose режима сохраняются; shared guards,
аудит с durable outbox, recovery и required release checks остаются обязательными.
Окружение с обязательным TLS принимается лишь после enabled gates.

### Матрица обязательных критериев первого выпуска

Порядок определяет единственная очередь выше; таблица ниже уточняет критерии
выпуска, а не запускает параллельную очередь. Задачи 0–8 сверяются с существующей
реализацией и evidence. Подробная история private575–582 находится в TLS-плане и
отчёте; старые OPEN нельзя автоматически превращать в новую работу.

Первая поставка — compact/bundled, одна application replica, один backend worker,
Recreate. Профиль PostgreSQL TLS фиксируется в release manifest/values явно.
Для disabled-профиля специфичные для PG TLS задачи T3–T6 не блокируют выпуск,
только если это допускает контракт среды. HTTPS/SSO, аудит, безопасное восстановление
сессий и запрет второго writer обязательны в обоих профилях. Disabled не означает
разрешение обойти общий recovery guard: непроверенный общий путь оставляет
соответствующий release gate открытым.

| Критерий / задача | Проверяемый результат | Условие завершения |
|---|---|---|
| C1 — 5а/6: OIDC и сессии | Установлена причина дополнительного audit-события; проверены ротация, новый вход и отказ старых сессий после возврата auth-таблиц | Native R8 завершён; R6 FAILED900,11/900 и отдельно очищен; текущий адаптер Windows47/Linux47 и actual readonly input proof PASS. Home R7 PASS699,98/900, пять assertions/cleanup/baselines и независимое отсутствие подтверждены. Следующий этап — интеграция со штатным protected recovery/start; успех изолированного OIDC-сценария не закрывает этот общий путь. R1 retirement закрывается отдельно до итоговой home/release приёмки. Бизнес-данные, аудит и held outbox сохранены; общие требования применяются и без PG TLS |
| C2 — 7: кандидат выпуска | Один неизменяемый набор исходников, chart, backend/frontend и operator tools; сохранены manifest и digests | Включены последние исправления access logs; известен diff к main, сторонние изменения исключены; сборка и тесты относятся к этому набору |
| C3 — 3/4/6/8: compact lifecycle | Установка, обновление, отказ миграции, прерванная операция и непустое согласованное восстановление PG/Qdrant/files | Штатный операторский CLI на выбранном профиле, максимум один writer, сохранность PVC и безопасный resume; измерены downtime и RPO/RTO. Auth-only dump не заменяет полный restore |
| C4 — 5а/7: регрессия и доставка | Backend с coverage≥85%, frontend/parser/audit, local и Compose bundled/external, Helm/schema и standalone operator consumer | Required checks на итоговой ревизии; skips перечислены с применимостью, применимый SKIP остаётся OPEN; dependency/license audit привязан к lockfiles. Полный backend1752 не закрывает новый release |
| C5 — 1/6/7а/8: эксплуатация | Backup вне исходной VM, восстановление без неё, requests/limits/PVC/retention и порядок старта после перезапуска | Drill на независимом target, проверка свежести копий, владельцы операций и измеренные пределы внесены в runbook |
| C6 — 0/5/7/8: окруженческая приёмка | В отдельной deploy-копии те же digests проходят DEV → TEST → PROD | Подтверждены CSI/CNI/Vault/Gateway/IdP/collector и required CI; live-model smoke выполнен в PROD. Неизвестные параметры остаются открытыми, без реквизитов в общем репозитории |

Различать результаты «общий release candidate проверен» и «введён в конкретной
среде»: внешняя приёмка не мешает закончить переносимый комплект, но без неё
этап 1 не объявляется введённым в PROD. Коммит, перенос в main и публикация
сохраняют установленный порядок авторизации; эта правка плана их не выполняет.

Для enabled-профиля дополнительно обязательны T1–T6 TLS-плана: protected
origin/current trust, recovery/antirollback, restore и PKI/CRL. Общие результаты
переиспользуются только при подтверждённом совпадении проверяемых исходников и
контрактов. Завершение одного профиля не закрывает другой автоматически.

Общие recovery-контракты P1–P3 сохраняются: intent/private output → Secret ACK
и independent promotion → restore/resume/start с key/auth/outbox/generation barriers.
Они применяются к выбранному disabled-профилю в шагах 2б–2г; существующие
компоненты переиспользуются после сверки контрактов, без повторной разработки.
Подробные P2/P3 checkpoints TLS-плана — контракт и история, а не вторая очередь.
Результаты старых snapshot не закрывают текущие native/home/release gates.
После DR Helm rollback/reapply сохраняет новое поколение ключа; rollback образа
не разрешает возврат старого Secret. Обычный reuse активированного поколения
сохраняет ключ после истечения старого intent TTL.

После приёмки compact выполнять frontend → durable очередь/владение задачами →
API replicas → несколько workers. Разделение `lifecycle.py`/`postgres_tls.py`
проводить отдельным изменением после фиксации поведения: сохранить CLI, версии
журналов/архивов, полномочия recovery и отказные сценарии; рефакторинг не является
условием первого выпуска. Новый полный прогон нужен после изменения проверяемого
поведения или состава выпуска; неизменные дорогие матрицы повторно не запускать.

**Историческая сводка 05.10.2026:** compact-реализация подготовлена в ветке `codex/kubernetes-rollout`, базовая поставка — commit `f551f5f`; в `main` она ещё не интегрирована. Дополнительные home, operator tools/toolbox, model-budget, OIDC/audit и dependency изменения пока не закоммичены. Домашние install/update/restart/backup/restore, HTTPS/OIDC и enforcing NetworkPolicy probes пройдены в указанных ниже объёмах; это не закрывает требования задач 5а/7а или платформенную приёмку. Исходный audit file-profile gate: **13 PASS / 3 FAIL / 0 SKIP**. Ранний service/startup guard исправлен в локальном кандидате: исходные16 profiles **14 PASS / 2 FAIL**, расширенная matrix **35 PASS / 2 FAIL / 0 SKIP**; следующий file-material candidate проходит Linux68/68, network24/24, PG31/31, whole-storage2/2 и Windows101PASS/6POSIX SKIP; projected Secret/ACL/file-phase lifecycle/full/home/CI release приёмка открыта. Доступы к целевой платформе уже есть, но здесь выполняется общее решение: окруженческую интеграцию пользователь ведёт в отдельной копии. Этапы 2–5 не реализованы и требуют отдельных подробных планов. [Отчёт приёмки](../reports/2026-10-03-kubernetes-home-acceptance.md).

### Уточнения every-effect582 после последних изменений — 09.10.2026

- Разделить исправимый отказ входных параметров до intent и неопределённый результат
  после intent/spawn. В первом случае журнал и ресурсы не изменены; исправленный
  запрос проходит повторный current owner/cap guard. Во втором сохраняется prepared
  запись, повтор эффекта и следующий effect/unlock запрещены.
- Закрыть гонку target между последним GET и записью: API должен условно менять
  именно captured UID/RV либо создавать только отсутствующий объект. Безусловный
  apply после наблюдения absence не подтверждает защиту чужого ресурса. Нужны
  actual native conflict/race tests, включая позднее изменение после actor binding.
- Read-only SQL/HTTP exec разрешать только из штатного проверенного caller для
  выбранного Pod/container/template; разрешение одноразовое, очищается при любом
  выходе. Подмена команды или повтор использования не получает writer authority.
- После исправления atomic dispatch owning225 пройден на Windows/Linux; native
  resource proof и независимая очистка подтверждены на тех же579 files. Прежние203/35
  и native581 сохраняют свои source maps. До full final582 gate не включать582
  в runtime565/main; ресурсный native proof не закрывает handoff/admission/restore.

Подробные tests и порядок проверки находятся в
[контрактах582](2026-10-05-kubernetes-postgres-tls.md#every-effect582-контракты-и-приёмка).
Действующий порядок определяется карточкой 2б.3.3 (R1–R6); этот исторический
контракт не создаёт отдельной очереди. Ресурсы стенда расширять по свежим peak/capacity замерам; прежнее
разрешение расширения не является свидетельством текущей свободной ёмкости.

### Пробел selected-probe профиля — проверка плана 09.10.2026

- По pre-fix коду `_operation_selected_probe_take` выбирал `desired`, тогда как
  `begin_backup_installed_operation` задаёт `installed` и `desired=None`. Противоречие
  исправлено в том же582: causal6 FAIL/57 controls →63 owning GREEN; current union430
  PASS/platform. Прежний418 PASS не покрывал этот input class и сохранён как история.
- Component выполнен для installed-only, installed с другим genuine desired и
  повреждённого installed при валидном desired. Выбирать installed при его наличии;
  desired разрешён только при installed=None. Ошибка installed не разрешает fallback.
  Проверять реальный текущий материал against identity установленного checkpoint.
- Матрица выполнена для running SQL и started SQL+HTTP через actual Manager.run с
  genuine enabled materials; после unknown effect чтение не меняет журнал, не выдаёт
  writer/start/unlock и не восстанавливает потерянный receipt. Native live PostgreSQL/
  HTTP proof на final source — отдельно от API-adapter тестов и прежнего native9.
- Девять модулей/430 уникальных cases — текущий selected caller/component набор,
  actual collection2,75/30с. После следующего diff пересобрать collection/source map; original
  Windows/Linux180с, independent60с сохраняются. Итоговый full caller/native/home/release
  gate остаётся отдельным, не суммой PASS разных исходников.

### Уточнения проверки плана — 09.10.2026

- Общий срок начинается до первого preflight операции; более короткий parent может
  только сократить его. Проверки, acquire и следующий процесс не выдают новые900с.
  Дефект577 исправлен; полный scope constructor callers остаётся блокирующим
  для допуска финального кандидата. Linux caller closure577 завершён в указанном объёме;
  final578 имеет selected754 и g5, но не весь прежний caller union. Windows
  native-symlink acceptance OPEN; результаты разных seals не суммировать.
- `operation-status` сохраняет PostgreSQL `phase`/`migration`, общий статус идёт
  отдельным `common`. Истёкший и legacy журнал остаются доступны для чтения;
  результат чтения не разрешает writer/start/unlock.
- Для положительного resume отдельно проверить прежних actors, native handoff ACK
  и исходный остаток времени. Истечение сохраняет блокировку и требует явного
  существующего observe/repair пути; автоматические restart, unlock и повтор import
  не допускаются. Готовность Pod не подтверждает завершение прежнего writer.
- Итоговая приёмка577 выполняется после исправления на одном frozen source:
  actual caller union/consumer Windows/Linux, затем extracted CLI/native и home
  по применимости. Результаты576-r4 остаются свидетельством только его ревизии.
- Common own-CAS578 проверен на actual Manager и Kubernetes API; следующий этап —
  цепочка всех существующих typed writers с common acquire и настоящим enabled
  material profile. Lock ACK и common state ACK не атомарны;
  потеря второго ACK запрещает следующий effect и обновление receipt из GET.
  Исходный lifetime и admission не переиздаются. Для растущего lock проверить
  фактический размер body/ACK и свежий RV между successive writers; безопасный
  отказ по лимиту не равен работающему recovery.
- Handoff отдельно подтверждает остановку прежнего OS-процесса оператора,
  а не только Jobs/Pods. PID без идентичности запуска, отсутствие labels или
  недоступность прежнего host не подтверждают отсутствие writer.

### Историческая сводка реализации и приёмки — 05.10.2026

**Уточнения документации 04.10.2026:** [обновлённый список вопросов](../../KUBERNETES_PLATFORM_QUESTIONS.md)
частично раскрывает пункты 1, 3, 4, 6, 10, 12–15. Собственный chart предусмотрен;
обязательный прямой внешний `/api` → backend не установлен. Compact/Next
схема сохраняется предложением, допуск Pod/Jobs и окно обслуживания ещё открыты.
Добавлены проверки namespace `logging.enabled`, collector, доверия PROXY protocol,
component inputs и разделения remote mirrors/release repositories. Пункты
2, 5, 7–9, 11 не закрыты; новые механизмы не следуют автоматически из примеров. OIDC затем реализован
по отдельному утверждённому плану; audit и реплики остаются следующими этапами. Полные ссылки и реквизиты источников остаются вне Git.

Дополнительный [локальный прогон 04.10.2026](../reports/2026-10-04-kubernetes-portable-acceptance.md)
на Docker/kind проверил актуальные незакоммиченные изменения: сборку трёх
образов, минимальный lifecycle с непустым restore и PVC retention; 174
контрактных теста прошли без пропусков. Проверено повторное применение
той же версии, а не upgrade между разными версиями приложения/схемы.
На исходном прогоне npm audit сообщил 5 high в цепочке линтинга. Локальный dependency gate закрыт 05.10.2026 scoped tinyglobby migration; детали и final home proof — в [отчёте](../reports/2026-10-04-audit-outbox-acceptance.md).
Статусы окруженческой приёмки и этапов репликации не меняются.
Последующее [OIDC и release-checks испытание](../reports/2026-10-04-oidc-compatibility-acceptance.md)
подтвердило три актуальных HTTPS профиля, полный partitioned backend набор
3309 PASS / 27 SKIP и отдельное выполнение 11 PG skips. Исторический npm audit
FAIL сохранён; текущий локальный gate PASS, внешний CI/coverage и remaining platform/storage fault cases не закрыты.

| Задача | Подтверждено отчётом 03.10.2026 | Что осталось |
|---|---|---|
| 0 | Обезличенный bundled/stub/live контракт; доступы существуют | В окруженческой копии: quota, CSI/CNI, actual API discovery и владельцы целевых операций |
| 1 | VM 4 vCPU / 6 ГиБ / около 50 ГиБ; Gateway/IdP/HTTPS и отдельный Cilium kind; backup вне kind | Ресурсы для реальной нагрузки, backup вне исходной VM и отдельные VM для отказов |
| 2 | Независимый liveness, профили, HTTP/SSE stub; локальная synthetic матрица documents/fast/full | В окруженческой копии: выбранные DEV/TEST профили и доверенный model/tokenizer контракт PROD |
| 3–4 | Compact chart, bundled StatefulSets, внешние PVC/Secrets, lock/migration Jobs; install/update | Фактические admission/CSI, целевые Secrets и production auth |
| 5 | Home CA/HTTPS/OIDC/CSRF, stream/cancel/logout, enforcing Cilium probes | Целевые маршруты/IdP/CNI, VSO, максимальные upload/stream limits, mirrors и observability |
| 5а | OIDC и durable outbox реализованы в рабочей ветке; выбранные HTTPS/restore, timeout/race и whole PGDATA/WAL/cold-start/maintenance/recovery probes пройдены; [приёмка](../reports/2026-10-04-audit-outbox-acceptance.md) | Linux DNS/multihost/TLS/COMMIT matrix исправлена: 20 PASS/0 SKIP, исходный 3 FAIL/8 сохранён; Task 3a продолжен: Linux24/24 с burst/SIGTERM и native Windows PG35/35; исходный file-profile gate13 PASS/3 FAIL сохранён; candidate service-guard даёт14/2 исходных profiles (расширенная required matrix35/2), file-material repair даёт68/68, FIFO безопасно отказывает; продолжение projected Secret/ACL/file-phase lifecycle3a.5, затем production load/CSI и hosted CI; trusted-IP/live adapters, целевая регистрация, внешний collector/CI и интеграция release snapshot |
| 6 | Непустой согласованный backup/restore, counts/content/BM25, PVC retention | Interruption, rollback/RPO/RTO и восстановление при недоступной исходной VM |
| 7 | Operator archive/bootstrap, consumer template, recipe toolbox и локальный offline container smoke | В окруженческой копии: публикация, общий линтер, реальные pipelines/CI Lint, RC promotion и registry digest |
| 7а | Требования ввода в эксплуатацию внесены в план | Применимость, владельцы и внешние согласования |
| 8 | Домашний synthetic smoke, 93 связанных теста без пропусков | Полный browser flow, DEV → TEST → PROD и live модели |

Все статусы относятся к объёму, указанному в отчёте; неполная задача не отмечается целиком выполненной. Пять high findings исходного dependency audit устранены локальной tinyglobby migration 05.10.2026; прежний FAIL сохранён в истории. Исходный DNS/multihost FAIL сохранён в истории; текущий audit-v7 проходит Linux network/lifecycle 24/24 и native Windows PG 35/35. Полный backend — 3408 PASS/83 SKIP, coverage 88%; новый home upgrade/HTTPS/response→DB→stdout пройден, 102 immutable rows сохранены. Новый home restore audit-v7 подтвердил136 immutable rows/new PVC/keys/no replay и off-VM backups; новая Linux SCRAM/passfile/socket matrix13 PASS/3 FAIL выявила блокирующие file profiles, следующий repair описан в3a.5; production load/CSI и hosted CI остаются открыты, см. [Task 3a audit плана](2026-10-04-audit-outbox.md#task-3a-общий-deadline-postgresql-denial--предлагаемый-bounded-repair). Внешний required CI и повтор всех обязательных checks на фактическом release snapshot остаются открытыми; домашняя приёмка и платформенный линтер их не заменяют. Чекбоксы ниже — оставшиеся и повторяемые критерии поставки, а не инструкция заново создавать уже реализованный код.

### Историческая очередь после проверки 05.10.2026

1. Закрыть решения файлового слоя и выполнить [Task 3a.5 audit-плана](2026-10-04-audit-outbox.md#3a5-файловый-этап-конфигурацииconnect--следующий-scoped-repair): ранний service отказ, сохранение trust/credentials, единый budget и очистка. Старые network PASS не закрывают новый file-profile FAIL.
2. Подтвердить repair через строгий протокол tests, настоящий HTTP и startup, Linux/Windows и новый image freeze. Затем домашние projected Secret/rotation/HTTPS/restore и полный release gate. Файловые probes с обычным connect или внешним kill ребёнка недостаточны; точные критерии и команды находятся в 3a.5.
3. По этому snapshot отдельно подтвердить required CI и согласовать интеграцию/публикацию. GitLab deploy-копия потребляет те же проверенные digests; наличие workflow в исходниках не доказывает выполнение checks или branch protection. Окруженческие collector/trusted-IP/CSI/load и DEV→TEST→PROD остаются отдельными открытыми критериями.
4. К подробным планам frontend/API/workers replicas переходить после компактной поставки и её эксплуатационной приёмки. Реплики и новая очередь не включаются в file repair; текущий singleton/Compact/Recreate контракт сохраняется.

Эта очередь относится к checkpoint 05.10.2026 и заменена действующей очередью в начале документа. Строки отчётов и failed evidence сохраняются; незавершённые общие audit/file-profile проверки сверяются с актуальным audit-планом и остаются частью release gate, пока не подтверждены.

## Global Constraints

- Сохранить локальные `start-all.ps1` / `start-all.sh` и Docker Compose `bundled` / `external`.
- Этап 1: `deployment.mode=compact`, `application.replicas=1`, `UVICORN_WORKERS=1`; HPA отсутствует. Распределённая очередь не блокирует эту поставку.
- DEV, TEST и PROD — независимые кластеры; в каждом собственные PG/Qdrant/PVC, данные, ключи и SSO-конфигурация.
- Во всех трёх средах `ENVIRONMENT=production`. Название среды задаётся отдельно, без ослабления auth/CSRF/TLS.
- Настоящие LLM/embeddings доступны только в PROD. DEV/TEST используют явный stub/fake профиль. Тестовые embeddings, jobs и базы не переносятся в PROD.
- Продвигаются одни и те же application image digests и версия chart; между TEST и PROD нет пересборки. Миграции используют соответствующий backend image.
- Код и целевая доставка находятся в разных репозиториях. Этот checkout хранит переносимые исходники и шаблоны; отдельный deploy-проект потребляет версионированные артефакты и защищённую конфигурацию.
- Проектная policy: сборка `linux/amd64`, разрешённые зеркала и CA; общий линтер блокирует доставку; DEV запускается вручную, TEST/PROD вручную из проверенного release tag. Примеры платформы с `on_success` не отменяют эту policy и не доказывают обязательность manual/CPU architecture во всех проектах. Применимость внешних правил и фактическую архитектуру узлов сверить отдельно.
- Первая поставка поддерживает bundled Kubernetes. Kubernetes external — последующее расширение; Compose external проходит регрессию уже сейчас.
- До разрешения backend replicas нельзя принудительно запускать замену, пока предыдущий процесс может работать. `Recreate` и RWO не подменяют распределённое владение.
- Пользовательские PVC и backup переживают удаление Helm release. Восстановление выполняется в новый namespace/тома/БД, без перезаписи исходной инсталляции.
- Документы и примеры анонимны: никаких названий организаций, внутренних доменов/IP, закрытых ссылок, аккаунтов, токенов или реальных Vault paths. Только технические требования и placeholders.
- При исполнении изменения сетевой модели/секретов/retention отражаются в `SECURITY.md` в том же изменении.
- Текущее уточнение разрешает обновление документов. Ранее разрешённая compact-реализация и домашняя приёмка учтены в статусах; новые auth/audit/delivery/replication требования не объявляются реализованными. Коммит, интеграция ветки и публикация — отдельные действия. Посторонние изменения рабочего дерева сохраняются.

## Review Focus

- Потеря миграционного Job либо повтор pipeline не запускают старый и новый backend одновременно и не повторяют разрушительную операцию: задачи 4 и 6.
- DEV/TEST успешно работают со stub, но PROD случайно получает fake embeddings либо старый тестовый индекс: задачи 2, 3 и 8.
- Работает port-forward, но Gateway обрезает upload/SSE либо не даёт корректный SSO callback: задачи 5 и 8.
- DEV использует public OIDC client, приложение требует secret; группы есть только в userinfo; аудит теряет пользователя/IP или не выходит в stdout: задача 5а.
- CI пропускает общий линтер или считает созданный RC registry доказательством доставки в TEST: задача 7.
- Восстановлен PostgreSQL без соответствующих файлов/Qdrant или потеряны данные после удаления release: задачи 3 и 6.
- Одна домашняя VM проходит smoke, но CSI/CNI/Vault/admission и CI цепочка целевой среды отличаются: задачи 0, 1, 5, 7 и 8.
- Deploy-проект получил chart/images без совместимых operator tools; tracked chat не имеет доверенного token budget; заменённый worker пытается опубликовать старое поколение: задачи 7, 2/8 и этап 3.
- Новая версия worker не читает накопленные jobs либо backup теряется вместе с исходной VM: этапы 3–4 и задача 6.
- Восстановленная копия сама запускает старые задания на PROD endpoints либо несколько API/workers запускают audit dispatcher с локальной блокировкой: задача 6 и этапы 3–5.

## Карта этапов

Зависимость «этап 1 принят» для разработки общего решения означает принятый
переносимый compact release candidate: его lifecycle/recovery, regression и
общие release gates подтверждены на неизменяемом комплекте. Ожидание DEV/TEST/PROD
отдельной deploy-копии не блокирует подготовку подробных планов и лабораторную
разработку следующих этапов. Внедрение реплик в конкретную среду требует её
собственной compact-приёмки и приёмки нового профиля; локальный результат не
переименовывается в PROD acceptance.

| Этап | Выполняемые задачи | Поставляемый результат | Зависимость |
|---|---|---|---|
| 0 | 0–1 | Контракт среды, изолированный домашний kind | Нужен перед функциональными проверками |
| 1 | 2–8, включая 5а и 7а | Общий compact release candidate; отдельная deploy-копия проводит DEV → TEST → PROD | Общие gates закрываются дома/в CI; формальные и инфраструктурные gates конкретной среды — в deploy-копии. Не зависит от будущих реплик |
| 2 | Отделение frontend и его диагностики | 2 frontend, 1 backend | Этап 1 принят |
| 3 | Отделение API/worker и долговечная очередь | 1 API, 1 worker | Этап 1; frontend может внедряться независимо |
| 4 | Общее состояние API и файловый доступ | 2 API, 1 worker | Этап 3 и стенд с несколькими VM |
| 5 | Владение задачами, несколько workers, эксплуатация | Несколько API/workers с проверенными отказами | Этап 4 |

## Структура будущих изменений

### Обобщение для нескольких платформ

Выбранные направления из [оценки и критериев](../../KUBERNETES_PLATFORM_QUESTIONS.md)
включены в roadmap продукта. Открытый вопрос о конкретной платформе не блокирует
проектирование общей функции, но продолжает блокировать неподтверждённую
окруженческую приёмку. Перенос в план не означает реализацию: чекбоксы относятся
к завершённой функции и её проверке.

**Критерий:** следующая инсталляция использует ту же поставку application images,
chart/operator artifacts и исходников, меняя поддерживаемые параметры,
Secret/PVC references и разрешённый adapter. Отдельный deploy-проект хранит
конфигурацию; правки Python/Next и форк общих templates для каждой инсталляции
не являются обобщением. kgateway, VSO, OpenSearch и конкретные GitLab components
не становятся обязательными для всех платформ. Поддержка любых Gateway/IdP/CSI/
CPU не обещается; поддерживаемые сочетания фиксируются явно.

| Вопросы | Общая работа и место в плане | Критерий проверки |
|---|---|---|
| 1, 3, 4, 6 | Пакет конфигурации, задачи 3/5/8 | Два обезличенных поддерживаемых профиля на одной поставке; invalid values отклоняются до изменения ресурсов |
| 5 | Kubernetes external, новая задача 3а | Bundled сохранён; external не создаёт/удаляет чужие БД/тома; явный backup ownership и restore contract |
| 7–8 | Общий OIDC profile, отдельный подробный план задачи 5а | Public/confidential, PKCE/token auth method и authoritative claims проверены тестовым IdP; production protections сохранены |
| 9–10 | Общая audit schema/JSON после commit, задача 5а | Stable event ID/allowlist; DB audit сохранён; rollback не публикует success; collector настраивается снаружи |
| 11 | Выбран durable outbox с повтором, задача 5а | Pending переживает restart; возможный дубль сохраняет event_id, rollback не публикует success; stdout не равен downstream ACK |
| 12 | Отдельный trusted-IP profile при необходимости | Peer/unknown различаются; relay включён только с доказанной upstream boundary; spoof/replay отклоняются |
| 13–14 | CI/Secrets/CA adapters, задачи 5/7 | Общие release/lifecycle команды и digests; config и rotation задаются без форка приложения |
| 15 | Model capabilities и метрики, задачи 2/5/8 | Поддерживаемый provider/counter проверен; scrape/auth явные; отсутствующие metrics/live quality не объявлены подтверждёнными |
| 2 | Recreate первой поставки; zero-downtime в этапах 3–5 | Один writer подтверждённо остановлен; RollingUpdate не разрешён без shared state/ownership/fencing |

**Порядок и предварительные трудозатраты из оценки:**

1. Интеграция уже сделанной ветки — 1–3 ч.д. один раз при отсутствии значимых
   конфликтов/регрессий: review фактического снимка, закрытие dependency gate,
   local/Compose regression. Commit/merge/push не выполняются автоматически
   вследствие добавления оценки в план.
2. Пакет конфигурации — 4–7 ч.д.: reuse готовых values/settings, schema,
   precedence, безопасные probe/traffic overrides, документация и проверки.
   Это пересекающиеся работы 1/4/6/14/15; новые OIDC/audit/metrics не включены.
3. OIDC 7–8 — 6–10 ч.д. общим пакетом. Public/PKCE/UserInfo выбраны как общая
   возможность, применение зависит от регистрации. Refresh/back-channel logout
   и немедленный revoke не входят в пакет.
4. Audit 9–11 — выбран durable outbox с повтором: исходная оценка 12–24 ч.д.
   всего с outbox/recovery, а не оценка оставшейся работы. Ядро уже реализовано
   в рабочей ветке; остаток оценивать по фактическому release diff и открытым gates.
   Trusted IP и downstream ACK в эту оценку не включены.
5. External storage — 5–10 ч.д. отдельным расширением после спецификации
   ownership/backup. Включено в roadmap, не блокирует первую bundled поставку.
   Compose external не является готовым Kubernetes external.
6. Окруженческая работа учитывается отдельно: CI adapter 2–5 ч.д., совокупная
   инфраструктурная приёмка DEV/TEST/PROD 3–6 ч.д., содержательная live-model
   приёмка 3–7 ч.д. плюс API расходы. Работа платформенных администраторов
   и ожидание согласований не включены.

Диапазоны инженерные, не календарный график. Общие пакеты учитываются один раз,
adapter/config и приёмка — для каждой инсталляции. Подробные планы составляются
по одному подпроекту до реализации; roadmap не фиксирует невыбранные интерфейсы.

**Дополнение к задачам 3/5/8 — пакет конфигурации:**

Подготовлен [проект контракта ближайшего пакета](../specs/2026-10-04-kubernetes-configuration-design.md):
probe defaults/overrides, precedence, формат durations, опциональный kgateway
adapter, runtime CA reference и A/B acceptance. Пакет реализован в
`codex/kubernetes-rollout`; [конфигурационная приёмка](../reports/2026-10-04-kubernetes-configuration-acceptance.md)
проверила minimal/legacy/A/B, maximum upload, stream timers и CA recovery.
Целевая платформа и остальные подпроекты roadmap этим не закрываются.
Next build limits и максимальный multipart upload выделены как проверяемые
границы, без обещания runtime override.
После проверки 04.10 добавлены trust для операционных Jobs, immutable CA rotation
через deploy и восстановление прежнего bundle, согласование probes/grace с
operator budgets, опциональный `0s` с idle policy и переход bootstrap/toolbox на
schema 2. `proxyTimeout` внешнего Next rewrite не считается deadline API fetch.
Подготовлен [подробный implementation plan пакета](2026-10-04-kubernetes-configuration.md):
baseline/schema-1 recovery kit, probes/budgets, traffic preflight, application/Job
trust, rotation/restore, bootstrap/toolbox 2 и последовательная runtime приёмка.
Задачи подробного плана 0–6 выполнены; regression 316 PASS/0 skips, fixture
stability 10×43 и 10×12. Приёмка завершена с сохранёнными failures и явным
продолжением; независимый review/полный CI остаются открытыми.
Проверка подробного плана добавила независимый backup
format=2, recovery после failed CA rotation, проверяемый состав двух комплектов,
сохранение полного CI regression и проверку TLS отдельно от readiness.

- [x] Зафиксировать поддерживаемую матрицу и precedence существующих Settings,
  ConfigMap/Secret, chart-controlled env и overlays; не создавать второй loader
  и не разрешать Secret переопределить environment/model/storage identity.
- [x] Добавить только числовые probe timing overrides с schema, сохранив текущие
  defaults, пути `/health/live`/`/health/ready`, порты и frontend TCP probe.
  Произвольные команды/URLs и отключение проверок через values запрещены.
  До acquire проверять budget startup/readiness в пределах 600 секунд и grace
  текущего/желаемого Pod с резервом в пределах 180 секунд; правила заданы в spec.
- [x] Описать согласованность upload/multipart, request/backendRequest,
  stream-idle/heartbeat, LLM timeouts и grace period. TrafficPolicy — опциональный
  adapter конкретной CRD schema; выключенный adapter не требует kgateway API.
  `0s` разрешать только через explicit opt-in с конечной idle policy, корректно
  различать отключение таймера и положительные durations; область действия —
  весь frontend HTTPRoute. Проверить active/stalled stream и cancel.
  Неизвестные внешние лимиты остаются непроверенными.
- [x] Переиспользовать existing Secret/CA/mirrors и управляемый restart/rotation;
  CA env/read-only volume должны переноситься вместе в migration/backup/restore
  Jobs из соответствующего старого или нового Pod template. Ротация использует
  существующий deploy с backup/migration, immutable ConfigMap и сохранением
  предыдущего bundle; recovery metadata и restore в новый namespace включают trust.
  Повторный deploy с old reference допустим при исправном текущем backup path;
  иначе — pre-change backup в новый namespace/PVC, проверка копии, отдельное
  переключение доступа. Старые Secret/ConfigMap refs не менять до конца backup.
  scrape/auth документировать явно. Пример manifest не создаёт отсутствующий
  `/metrics`; новые audit metrics относятся к audit пакету.
- [x] Выпустить schema 2 вместе с release metadata, operator archive, trusted
  consume.py и matching toolbox; проверить матрицу consumer/release 1/2.
  Сохранить старый комплект для restore backup schema-1 release, затем проверить
  upgrade на 2. Новые tools первоначально поддерживают `[2]`, без скрытого `[1,2]`.
  Backup независимо получает `manifest.format=2` с обязательной trust identity
  или null; старый reader должен отказать новому backup, а не игнорировать CA.
  Старые backups остаются format=1 и восстанавливаются исходным комплектом.
- [x] Для недостающих overrides сначала RED assertions в
  `backend/tests/test_kubernetes_chart.py`: invalid timing, altered probe path,
  missing enabled adapter API, unsupported replicas и PROD synthetic profile;
  затем минимальная реализация и GREEN. Прежние защитные проверки сохраняются.
- [x] Проверить два обезличенных профиля A/B на одной неизменной поставке:
  разные namespace, host/callback, Secret/PVC references и поддерживаемые
  resources/timing; независимые БД, индексы и PVC. Upload/NDJSON/upstream SSE/cancel,
  повторный deploy, CA rotation/recovery и непустой backup/restore проходят
  без правок кода и секретов в output. Отдельно выполнить runtime smoke без
  Gateway/Vault/runtime trust. Это не замена live/SSO/platform приёмки.

Сначала сверить перечисленные пути с веткой реализации. Уже существующие chart/scripts/tests дополняются по новым критериям; новые файлы создаются только при отсутствии. Jobs базовой поставки формируются `lifecycle.py` из rendered application contract; не вводить вторую независимую реализацию YAML-шаблонов.

| Файл или каталог | Ответственность |
|---|---|
| `deploy/helm/ai-knowledge/{Chart.yaml,values.yaml,values.schema.json,templates/}` | Приложение, Services, StatefulSets, probes, ConfigMap, NetworkPolicy; references на существующие Secrets/PVC |
| `deploy/kubernetes/profiles/{home,dev,test,prod}.values.example.yaml` | Обезличенные профили, значения ресурсов, режим моделей и публикации |
| `deploy/kubernetes/platform/{listenerset,httproute,traffic-policy,vault-static-secret}.yaml.example` | Необязательные интеграции платформы, без установки операторов/CRD и реальных реквизитов |
| `deploy/kubernetes/storage/claims.yaml.example` | PVC вне lifecycle application release |
| `deploy/kubernetes/lab/{kind.yaml,README.md}` | Воспроизводимый домашний стенд и границы его доказательности |
| `scripts/kubernetes/lifecycle.py`, `backend/scripts/{kubernetes_migrate,backup_totals,create_qdrant_snapshot,restore_qdrant_snapshot,check_integrity}.py` | Разовые Jobs из rendered image/env/PVC contract и существующие backend команды операций |
| `scripts/kubernetes/{preflight,deploy,backup,restore,smoke}.sh` | Одна операционная процедура для домашнего запуска и CI |
| `backend/tests/test_kubernetes_lifecycle.py` | Порядок операций, lock, PVC bindings, ошибки и повторный запуск; использовать существующие Python tests |
| `deploy/ci/gitlab-kubernetes.yml` | Обезличенный шаблон контракта build/deploy проектов; доставка подключается в отдельном deploy-репозитории |
| `.github/workflows/kubernetes.yml` | Рендер/валидация Helm и изолированный kind smoke; текущий CI сохраняется |
| `backend/test_scripts/model_stub/{server.py,fixtures.py,Dockerfile}` | Отдельный HTTP/SSE stub image с синтетическими ответами и сценариями ошибок |
| `backend/app/config.py`, `backend/app/main.py` | Разделение runtime environment/model mode и независимый liveness endpoint |
| `backend/tests/test_deployment_profile.py`, `test_model_stub_contract.py`, `test_kubernetes_chart.py` | Матрица окружений, текущие LLM контракты, смысловые проверки rendered manifests |
| `backend/tests/test_health_readiness.py` | Регрессия разделения liveness/readiness/dependency health |
| `docs/KUBERNETES_DEPLOYMENT.md`, `docs/KUBERNETES_TESTING.md`, `SECURITY.md` | Установка, эксплуатация, критерии приёмки, безопасность |
| `deploy/ci/operator-tools/`, `scripts/kubernetes/release.py`, `backend/tests/test_kubernetes_release.py` | Версионированный комплект operator tools, bootstrap, recipe toolbox и проверки потребления из deploy-проекта |

Не переписывать `Dockerfile` только ради Kubernetes. Для целевой сборки обязательны зеркала base images/зависимостей и CA: добавлять совместимые build args или отдельный тонкий platform build target с сохранением существующих defaults; подтвердить работу Python trust store, Node trust store и сборочных менеджеров пакетов.

## Этап 0 и первая поставка

### Задача 0. Зафиксировать обезличенный контракт трёх сред

**Files:** Create `deploy/kubernetes/profiles/*.values.example.yaml`, `docs/KUBERNETES_DEPLOYMENT.md`.

**Interfaces:** produces deployment contract: Kubernetes/CRD versions, CPU architecture, namespace quota, CSI/accessMode, registry references, Gateway/TLS ownership, Vault references, IdP and model connectivity, maintenance window, backup destination, RPO/RTO. Реальные значения хранятся в защищённой конфигурации оператора, не в Git.

- [ ] Read-only проверить версии/доступы в каждом контексте, allowed Pod Security/UID/fsGroup, PVC provisioning/reattach и возможность StatefulSets. Если доступа ещё нет, записать конкретный непроверенный пункт; не считать документацию проверкой кластера.
- [ ] Зафиксировать выбранный bundled профиль для всех сред; подтвердить ёмкость, backup ownership и защищённое место хранения копий вне исходных PVC.
- [ ] До подготовки новых заявок проверить существующие namespace, registries, Vault и права; отсутствие старой MR-ветки не доказывает отсутствие ресурса. Для kubectl задавать явный context и ограниченный request timeout.
- [ ] Подтвердить размещение продуктивного PostgreSQL: измерить latency/IOPS/fsync выбранного CSI и restore под нагрузкой; согласовать bundled с учётом рекомендации выделенного сервера БД. При отказе остановить PROD-путь и отдельно пересмотреть storage, не менять молча выбранную топологию.
- [ ] Назначить владельцев доступа к логам/метрикам, backup/restore/rollback и сертификатам. Проверить реальные права, индекс логов и безопасное хранение kubeconfig; персональные данные исполнителей оставить в защищённом реестре.
- [ ] Подготовить namespace declarations для DEV/TEST/PROD: pool, team, logging, quota. Никаких создания общих CRD/Gateway/Vault операторов приложением.
- [ ] Проверить actual API schema ListenerSet/HTTPRoute/TrafficPolicy/VSO через API discovery; не выводить поддержку ListenerSet только из версии Kubernetes.
- [ ] Отдельно записать: DEV TLS выпускается платформой; TEST/PROD требуют готовый сертификат. Назначить срок обновления сертификатов и владельца операции.
- [ ] Зафиксировать PROD model API protocol, model IDs, embedding dimension, CA/auth, rate/concurrency limits; эти реквизиты остаются вне документации.

**Приёмка:** заполненный защищённый deployment contract и обезличенный перечень выполненных/невыполненных проверок. Нет обязательного поля, незаметно подставленного от домашнего стенда.

### Задача 1. Подготовить домашний стенд на существующей VM

**Files:** Create `deploy/kubernetes/lab/kind.yaml`, `deploy/kubernetes/lab/README.md`, home values example.

**Interfaces:** consumes contract задачи 0; produces именованный kube-context лаборатории, namespace, test registry/image import, PVC, доступный Gateway и test IdP, адреса stub/live для тестов.

- [ ] Проверить VM `training-ubuntu`, её фактический Proxmox-узел, доступ, свободные CPU/RAM/disk и установленные версии. Не обращаться к VM по одному неподтверждённому номеру.
- [ ] Перед следующим прогоном повторить инвентаризацию и измерить peak потребление. Актуально на 09.10.2026: выделены 8 vCPU / 10 ГиБ RAM / 100 ГиБ disk, автостарт VM включён; последние capacity замеры — около 4,3 ГиБ доступной RAM и 57 ГиБ disk. Старый замер 03.10.2026 (4 vCPU / 6 ГиБ / около 50 ГиБ) исторический. Ориентир 16 ГиБ RAM / 120–160 ГиБ disk для полного стенда остаётся оценкой; дальнейшее расширение определять по замерам.
- [ ] Создать отдельный kind cluster с pinned node image, совместимый с целевой версией; выбрать CIDR без пересечения с LAN/VPN и каталог persistent данных вне удаляемого контейнера kind.
- [ ] Установить Gateway API/kgateway для последующей проверки маршрута, CNI с NetworkPolicy enforcement и storage provisioner. Минимальный smoke допускает временный port-forward с явной отметкой, что Gateway ещё не проверен.
- [ ] Подготовить отдельный realm/client тестового IdP, тестовые роли, TLS/локальный DNS или SSH-туннель. Не менять существующие домашние DNS/VPN/Compose staging/model services.
- [ ] Проверить запись/чтение небольшого PVC после перезапуска Pod, доступ из Pod к IdP и образам. Для домашнего offline cache до создания стенда проверять реально сохранённые named references всех app/storage/IdP образов и root/platform/config, а не только CRI config ID. Прямой containerd import — лабораторная подготовка, не замена registry delivery в deploy-копии. Данные теста отдельные; не использовать рабочий corpus.
- [ ] Выполнять тяжёлые bundled-проверки последовательно в фактическом resource budget. На последней проверке 09.10.2026 оба существующих kind-кластера доступны; прежняя остановка дополнительного кластера была временной и не задаёт текущую процедуру. Сохранить приложения, replicas, PVC/PV обоих кластеров; свежий baseline сравнить после очистки. Результаты и backup сохранять вне kind; до off-VM drill не считать backup на диске этой VM защитой от её потери.

- [ ] Проверить всю цепочку холодного запуска: Proxmox VM onboot/start order → Ubuntu/Docker → существующие node containers kind → API/Node Ready → PG/Qdrant → приложение/Gateway/IdP. Зафиксировать фактические service/restart policies, persistent mounts и ограниченный срок ожидания каждой ступени; onboot VM сам по себе не подтверждает запуск приложения. Проверку выполнять на изолированной копии либо в согласованное окно, после внешнего completed backup; оба действующих кластера автоматически не перезапускать.
- [ ] Разделить `graceful_lab_cold_start` и `unclean_lab_node_restart`: первый подтверждает автозапуск, второй — crash recovery и сохранность данных. Перед вторым сохранить контрольные SQL/FS/Qdrant данные и незавершённую операцию; после возврата проверить один writer, auth/audit/outbox, PVC bindings и безопасный resume без снятия lock по таймеру. Pod restart не подменяет перезапуск узла, а лабораторная имитация — реальное отключение питания физического хоста. Каждый результат имеет отдельный verdict; непроверенный остаётся OPEN.

**Приёмка:** reproducible lab setup и фактический resource budget; при нехватке ресурсов полный тест остаётся невыполненным, лимиты не уменьшаются ради зелёного статуса.

### Задача 2. Создать модельный тестовый контур и разделить health

**Локальный прогресс общего решения (03.10.2026):** дополнены stub профиль
и проверки tracked `documents/fast/full` через настоящий HTTP/SSE адаптер.
`backend/tests/test_kubernetes_chat_modes.py` не подменяет budget или LLMClient;
fixture подменяет retrieval и identity в isolated SQLite. Выбранный synthetic
контракт описан в [KUBERNETES_MODEL_PROFILES.md](../../KUBERNETES_MODEL_PROFILES.md).
Дополнительно проверяются malformed budget, multi-batch full, global citations
после пропуска oversized source и cancelled attempt после позднего ответа.
Реальные provider/template/tokenizer проверяются отдельно в окруженческой копии;
неподдерживаемый live профиль остаётся fail-closed.

**Files:** Create `backend/test_scripts/model_stub/*`, `backend/tests/test_model_stub_contract.py`, `backend/tests/test_deployment_profile.py`; Modify `backend/app/config.py`, `backend/app/main.py`, `backend/tests/test_health_readiness.py`.

**Interfaces:** `GET /health/live` → HTTP 200, `{"status":"alive"}`, без вызова БД/Qdrant/моделей. Existing `/health/ready` unchanged. Settings: `deployment_tier` defaults `local`, allowed local/home/ci/dev/test/prod; `model_mode` defaults `live`, allowed live/stub/unavailable. PROD requires live and non-fake embeddings. Stub exposes `/v1/models` and `/v1/chat/completions` on 8000, no outbound calls.

- [ ] Сначала написать отрицательные tests: `test_liveness_ignores_dependency_outage`, `test_readiness_still_fails_for_storage_outage`, `test_prod_rejects_stub_or_fake`, `test_existing_local_and_compose_defaults_unchanged`. Запустить и подтвердить ожидаемое падение новых assertions.
- [ ] Реализовать независимый liveness и конфигурационные проверки; не менять существующий смысл `ENVIRONMENT` и защит production auth.
- [ ] Проверить текущие `llm_client.py`, `llm_profiles.py`, `embedder.py` и task schemas; fixtures соответствуют generation/classification/development/translation и чатовым ответам. Статические ответы не выдают себя за извлечённые реальные знания.
- [ ] Написать tests: `test_stub_stream_consumed_by_real_llm_client`, `test_stub_generation_matches_current_schema`, `test_stub_translation_preserves_input_count`, `test_stub_429_503_and_interrupted_sse`. Проверять через настоящий адаптер LLM, включая finish_reason и `[DONE]`; GET `/models` недостаточно.
- [x] Локальная матрица `documents/fast/full` для HOME/CI/DEV/TEST stub с явным `local_qwen` transport; `test_tracked_chat_requires_trusted_token_budget` включает missing tokenizer, unknown provider и PROD live против marked synthetic endpoints; `test_cancel_keeps_stopped_history_after_late_result` проходит реальную HTTP/SSE completion. Источник synthetic window/template/counter фиксируется в общей спецификации; guard не отключается.
- [ ] В окруженческой копии для каждого выбранного live профиля подтвердить реальный context window и соответствующий модели template/token counter; при необходимости отдельно спроектировать неподдерживаемый provider. Локальная synthetic матрица не доказывает точность live tokenizer.
- [ ] Home `/props`, `/apply-template`, `/tokenize` и profile `local_qwen` уже проверены со synthetic счётчиком, но не означают поддержку всех DEV/TEST/PROD профилей. Подробный model budget contract фиксировать в `backend/app/services/chat_token_budget.py` и тестах модели; fake счётчик запрещён для PROD и не доказывает точность токенизации.
- [ ] Реализовать stub только для синтетических фикстур, предусмотреть точный выбор error/delay сценариев в test configuration. Запретить внешние egress; image отдельный, не в production application image.
- [ ] В dev/test example задать `MODEL_MODE=stub`, fake embeddings выбранной размерности, BM25 и test model ID; live production example требует реальные endpoints и отдельный пустой индекс. Результат stub содержит явный маркер тестовых данных.
- [ ] Запустить из `backend/`: `python -m pytest tests/test_health_readiness.py tests/test_deployment_profile.py tests/test_model_stub_contract.py -q`. Проверить реальные HTTP/SSE и понятную ошибку в unavailable mode.

**Приёмка:** DEV/TEST могут пройти функциональный цикл без доступа к настоящим моделям; это не отчёт о качестве поиска/генерации. PROD не принимает тестовый профиль, существующие local/Compose конфигурации сохраняются.

### Задача 3. Упаковать compact bundled развёртывание

**Files:** Create chart, PVC examples, `backend/tests/test_kubernetes_chart.py`; references existing `backend/Dockerfile`, `frontend/Dockerfile`, `backend/app/deployment/storage_mode.py`.

**Interfaces:** chart values: `deployment.environment`, `deployment.mode=compact`, `application.enabled`, `application.replicas=1`, `storage.mode=bundled`, image repository/digest per container, `secrets.existingSecret`, `persistence.data.existingClaim`, `persistence.diagnostics.existingClaim`, `models.mode`, optional platform resources. Service names backend/postgres/qdrant scoped by namespace.

- [ ] Сначала tests rendered manifests: 2 application containers, ровно 1 application replica, Recreate, HPA absent, backend:8000, postgres:5432/qdrant:6333, secrets только по reference, PVC не удаляются chart, no hostPath в production.
- [ ] Добавить schema rejection для replicas>1, workers>1, split mode до реализации, prod+stub/fake, отсутствующих digest/claim/secret references и неподдерживаемого storage mode.
- [ ] Создать ServiceAccounts без автоматического API token; ConfigMap с корректными DATA_DIR/diagnostic paths, envFrom Secret, production app mode во всех целевых средах. Не передавать backend secrets frontend контейнеру.
- [ ] Добавить chart contract `imagePullSecrets`: required для DEV registry с auth, пустой список допустим для подтверждённого анонимного TEST/PROD зеркала. Проверить pull всех app/storage/stub/job images из целевого Pod; не переносить DEV токен между средами.
- [ ] Создать непривилегированный initContainer для диагностических подкаталогов, shared mounts с существующими read-only границами; проверить fsGroup на выбранном CSI. Права на `/tmp` и parser subprocess не ломаются.
- [ ] Создать PostgreSQL/Qdrant StatefulSets с сохранением PVC и fixed versions из Compose. PostgreSQL/Qdrant port не публиковать наружу. Credential rotation и major upgrades БД не включать в обычное обновление приложения.
- [ ] Настроить backend direct startup/live/ready и frontend TCP probes. Проверить отсутствие цикла frontend health → backend Service → ещё неготовый Pod.
- [ ] Выполнить `helm lint deploy/helm/ai-knowledge`, render всех примеров с тестовыми placeholders, schema validation против целевых Kubernetes+CRD schemas и pytest chart tests. Не выдавать `helm lint` за кластерную проверку.

**Приёмка:** deployment стартует с разрешённым UID без privileged/root init, Ready зависит от собственных хранилищ, LLM outage не вызывает restart storm. Delete/reinstall release с прежними PVC сохраняет тестовый corpus.

### Задача 3а. Спроектировать Kubernetes external как общее расширение

Статус: включено по пункту 5 списка вопросов; не реализовано, не входит в
пакет небольших settings и не блокирует первую согласованную bundled поставку.

**Files:** Create `docs/superpowers/specs/2026-10-04-kubernetes-external-storage-design.md`
и отдельный подробный план после рассмотрения спецификации. Область будущей
реализации: chart `values/schema`, `templates/{storage,config,application,network}.yaml`,
`scripts/kubernetes/lifecycle.py`, `backend/app/deployment/storage_mode.py`,
backend backup/restore scripts и `backend/tests/test_kubernetes_{chart,lifecycle}.py`.
Здесь перечислены планируемые изменения, новые runtime параметры не объявлены.

**Interfaces:** два явных режима `bundled` и `external`, сохранённый compact
single-writer lifecycle; адреса/TLS/credentials передаются поддерживаемой
конфигурацией и Secret references. External не создаёт PG/Qdrant StatefulSets
или их PVC; Data/diagnostics остаются собственными томами приложения.
Использовать готовый runtime `STORAGE_MODE`, не копировать автоматически
Compose orchestration в Kubernetes.

- [ ] Описать supported external PostgreSQL/Qdrant connectivity/auth/TLS,
  ownership базы/коллекции и миграционных прав; mixed topology не поддерживать
  молча. Scope backup/restore приложения и платформенного storage разделить.
- [ ] Учесть существующие ограничения: chart/schema фиксируют bundled, env
  фиксирует Service endpoints, preflight проверяет bundled credentials,
  backup/restore работают через собственные storage Pods. Замена URL одна
  не делает lifecycle совместимым с external.
- [ ] Утвердить согласованный backup/restore contract SQL + FS + Qdrant:
  остановку writers, версии, totals/content, ответственность и подтверждение
  внешних копий. Если операция не поддержана выбранным внешним сервисом,
  она отклоняется до мутаций; пустой PASS или только FS backup недопустимы.
- [ ] Перед кодом описать failure tests: external создаёт ноль storage workloads,
  отсутствующий TLS/credential contract отклонён, чужой непустой target не
  перезаписан, unsupported backup/restore отказал до действий, bundled unchanged.
- [ ] Для приёмки использовать внешние синтетические PG/Qdrant в отдельном
  лабораторном контуре; приложение не владеет их deployment lifecycle.
  Пройти install/update/restore на новых target данных и проверить bundled,
  Compose external/bundled и local regression. HA/failover и чужие RPO/RTO
  этим сценарием не подтверждаются.

**Результат этой задачи проектирования:** рассмотренная спецификация с
ownership/backup/failure contracts и подробный план. Само включение задачи
не разрешает `storage.mode=external` в текущем chart/schema.

### Задача 4. Обеспечить порядок установки и миграций


**Files:** Create `scripts/kubernetes/preflight.sh`, `deploy.sh`, `scripts/kubernetes/tests/test_lifecycle.sh`, migration Job template.

**Interfaces:** существующий `lifecycle.py` принимает operation `preflight|provision|deploy|backup|restore|recover-lock`, обязательные `--context`, `--namespace`, `--values`, optional `--release`, `--chart`, `--expected-tier`. `deploy`/`backup` требуют `--backup-root`, restore — `--backup`, recovery — `--operation-id`. Shell wrappers передают тот же CLI; отдельного `--operation install|upgrade` нет. Root `.env` не является production configuration. Migration Job и lock содержат operation ID; повтор проверяет реальное состояние предыдущей операции.

- [ ] Написать fake-command tests: `migration_failure_never_starts_app`, `upgrade_waits_for_old_pod_exit`, `parallel_operation_refused`, `wrong_context_refused`, `existing_secret_never_rotated`, `second_backend_never_started_on_unknown_old_pod`.
- [ ] Завершить общий namespace operation lock: process-local acquire/run/conditional unlock реализованы в private575-r2, Windows/Linux affected+consumer602 cases/platform PASS; native/new-process ещё OPEN. Durable owner/resume и исход неопределённого unlock проверяются в [P3-O](2026-10-05-kubernetes-postgres-tls.md#p3-o-общий-владелец-и-аварийное-продолжение--09102026). Прерванная операция остаётся распознаваемой; lock не снимается по одному таймеру без проверки владельца. Новый процесс не получает authority только по operation ID или отсутствию lock.
- [ ] Install: preflight → wait for required Secret/PVC → chart с application.enabled=false → healthy PG/Qdrant → отдельный migrate Job → wait Complete + Alembic head → application.enabled=true → wait/smoke. Storage wait ограничен по времени и выдаёт причину отказа.
- [ ] Upgrade: maintenance → stop existing application and confirm exit → backup задачи 6 → migrate → start → smoke. Не запускать новую версию после partial/unknown migration. Повтор не создает второй активный migrate Job.
- [ ] Preflight проверяет установленные CRD, нужный namespace RBAC, наличие Secret keys без вывода значений, release/storage/profile compatibility, image availability. Bootstrap preflight и проверки уже созданных ресурсов различаются, чтобы install не требовал ещё не созданный Secret от VSO.
- [ ] Ввести stop condition при недоступном старом узле: оператор подтверждает остановку/изоляцию узла по runbook; force-delete не заменяет это подтверждение.
- [ ] Выполнить shell tests и реальный install/update в kind. Проверить не только readiness, но и число действующих backend процессов во время обновления.

**Приёмка:** штатная установка не содержит ручного недокументированного шага; любая ошибка миграции оставляет приложение остановленным, данные и отчёт доступны. На первой поставке измеряется окно обслуживания; нулевой простой не обещается.

### Задача 5. Подключить Gateway, Vault, CA и наблюдаемость

**Files:** Create platform manifest examples; Modify chart optional templates, deployment docs и `SECURITY.md`; при необходимости backward-compatible Dockerfile build args/CA target.

**Interfaces:** platform config с placeholders: Gateway parent reference, hostname, TLS Secret reference, Vault mount/path and existing Secret names. Ни один оператор, общий Gateway или namespace не создаётся application chart автоматически.

- [ ] Написать manifest checks: no Ingress, no TLS Secret templates, same-namespace backendRefs, numeric port, HTTPS Terminate, hostname required, redirect sectionName=http, route request timeout>=backendRequest.
- [ ] Разделить DEV automatic TLS и TEST/PROD existing TLS. Проверять ListenerSet Accepted/Programmed и HTTPRoute Accepted/ResolvedRefs; доступ к cert-manager API не предполагать.
- [ ] Настроить VSO Opaque runtime secrets; ждать их синхронизации до PG startup и миграций. Namespace/team binding находится во внешней подготовке. TLS material не передавать через application values или stdout.
- [ ] Проверить загрузку разрешённого максимального файла, multipart overhead, длинный SSE, cancellation, SSO login/logout/callback и CSRF через Gateway. Не переносить пример buffer 32Mi без проверки текущего upload contract.
- [ ] Различать HTTP idle 180s на порту 80 и HTTPS tunnel idle 1h на 443; зафиксировать также Gateway request/streamIdle timeouts. Проверить SSE с ответом дольше 180s, паузой до первого события и разрывом потока. Не вводить общий 180s deadline для HTTPS по устаревшему пересказу.
- [ ] Проверить DNS и фактический путь нового домена сразу через Gateway. Заявку на переключение балансировщика включать только для существующего Ingress-домена; при такой миграции старый маршрут сохраняется до проверки нового.
- [ ] Добавить CA в нужные trust stores с проверкой TLS без `verify=false`. Сборка и runtime должны работать с разрешёнными registry/package mirrors и без скачивания зависимостей при запуске Pod.
- [ ] Проверить NetworkPolicy реальными разрешёнными/запрещёнными соединениями на CNI с enforcement. DEV/TEST не обращаются к PROD models/data; в PROD stub отсутствует.
- [ ] Подключить платформенные Pod metrics и безопасные logs; приложение не обещает существование своего `/metrics`, если его нет. Проверить вывод request IDs и отсутствие секретов/текстов документов; support bundle по-прежнему доступен штатным admin flow.
- [ ] В отдельной декларации namespace по каждой среде включить сбор через `logging.enabled: true`; не подменять его сокращением `logging: true` или произвольным label application chart. Проверить Logging Operator → OpenSearch, parser/mapping и появление synthetic event UUID; договориться о правах/retention. Для защищённых метрик подтвердить scrape/auth и выбор ScrapeConfig, не копировать примерный interval `30s` как требование.
- [ ] Проверить контролируемую ротацию env Secret: Pod получает новые значения только после предусмотренного lifecycle restart, старый backend подтверждённо остановлен. Пароли и session keys не регенерируются при обычном upgrade.

**Приёмка:** установка через Gateway работает с production auth, а network/TLS policy подтверждена. Домашняя эмуляция не заменяет VSO/admission проверку в DEV.

### Задача 5а. Закрыть контракты OIDC и аудита до платформенной приёмки

До фиксации обязательного объёма уточнить [открытые вопросы](../../KUBERNETES_PLATFORM_QUESTIONS.md).
Chart examples не доказывают несовместимость нашего compact/Next решения.
Public/confidential/PKCE и authoritative claims source выбраны как общий пакет
по пунктам 7–8; его проектирование не ждёт регистрации конкретной среды.
Полезность общего расширения не означает несовместимость текущего confidential
flow. Audit foundation/JSON и durable outbox с повтором выбраны пользователем
по пунктам 9–11 для общего решения. Trusted-IP adapter остаётся отдельным
решением по upstream contract; пример платформы сам по себе не устанавливает
гарантии доставки или доверия к IP.

**Проекты спецификаций 04.10.2026:**
[совместимость OIDC](../specs/2026-10-04-oidc-compatibility-design.md) и
[аудит/доставка событий](../specs/2026-10-04-audit-delivery-design.md).
[OIDC implementation plan](2026-10-04-oidc-compatibility.md) исполнен в рабочей
ветке: Settings, atomic DB state, Authlib signed-token matrix, UserInfo,
commit-safe sessions и home profiles. Итоги и версии images приведены в
[OIDC отчёте](../reports/2026-10-04-oidc-compatibility-acceptance.md); целевая
регистрация и delivery gates остаются открытыми. Изменения не интегрированы.
Для аудита пользователь выбрал durable outbox с повтором. Подготовлен отдельный
[план DB/stdout аудита](2026-10-04-audit-outbox.md): event schema/allowlist,
security transactions, bounded dispatcher, retries и recovery. Ядро audit runtime реализовано в рабочем дереве и проверено локально и на домашних confidential/basic/PKCE none и public/none/S256/UserInfo-only HTTPS путях: [отчёт](../reports/2026-10-04-audit-outbox-acceptance.md). Nonempty PostgreSQL restore/old-image hold и отдельный полный home PVC/Secrets/key restore подтверждены; 67 confidential и 36 public immutable audit rows сохранены, historical pending held, старые cookie/flow отказаны, новый login UUID совпал с DB/stdout. Дополнительный PG fault runner прошёл 31 cases без skips: outbox-only disk-full, denial и security-mutation statement/lock timeout, connect faults, mixed block/unblock и recovery. Подготовлены portable PG и отдельный capacity CI jobs; hosted CI не проверена. Задача 5а целиком остаётся открытой. Trusted-IP relay проектируется отдельно; пока
адрес отмечается peer/unknown, без доверия произвольным forwarded headers. Примеры chart/deploy/logging
проанализированы в [обезличенном отчёте](../reports/2026-10-04-deployment-examples-review.md);
они не заменяют требования проекта и фактическую платформенную приёмку.

**Files:** отдельные подробные спецификации/планы для auth и аудита перед реализацией; область проверки: `backend/app/config.py`, `backend/app/auth/{api.py,service.py,providers/keycloak_oidc.py}`, `backend/app/services/audit.py`, `backend/app/db/models.py`, `backend/app/api/users.py`, `frontend/src/app/api/[...path]/route.js`, `SECURITY.md`. Будущие проверки: `backend/tests/test_platform_oidc_contract.py`, `backend/tests/test_platform_audit_contract.py`, `frontend/test/trustedProxyHeaders.test.mjs`.

**Interfaces:** согласованные public/confidential OIDC profiles, issuer и источник групп; профиль audit event (action, actor ID/name snapshot/type, timestamp, trusted client IP, applicable old/new values, method/reason), committed DB event и соответствующий структурированный stdout event. Точные схемы, доставка при сбое stdout и политика повторов определяются отдельной спецификацией, а не импровизируются в Helm-задаче.

- [x] Воспроизвести несовместимость public client без секрета с исходным валидатором; проверить discovery, callback, PKCE согласно регистрации клиента, confidential authentication, expiry/logout и userinfo-only groups при наличии встроенного token userinfo. Согласовать профиль DEV без отключения production protections.
- [ ] Составить матрицу обязательных событий: успешный вход/метод, отказ/причина, отказ API-аутентификации, изменения учётных записей/прав и доменные create/delete. Разделить события приложения и внешнего IdP; неприменимость account CRUD подтверждать владельцем профиля, не фиктивными событиями.
- [ ] Спроектировать передачу IP только от доверенных прокси; тесты на реальный IP, подставленный X-Forwarded-For и прямой обход Next. Существующее удаление forwarded headers не отменять без замены защитного контракта.
- [ ] Проверить каждый внешний путь, включая возможные legacy Ingress/прямой backend route; исключить обход Gateway sanitization до включения trusted-IP profile. Не считать Next Request источником проверенного TCP peer.
- [ ] Учесть диагностический NodePort самого Gateway и PROXY protocol: подтвердить сетевой допуск и доверенных отправителей, отклонить поддельный PROXY header и spoofed IP. Отличать этот путь от прямого доступа к frontend/backend; наличие протокола не устанавливает доверенную границу.
- [ ] На полном Gateway маршруте проверить buffer maxRequestSize, request timeout отдельно от stream-idle timeout, upload memory и cancellation. Значения и маршруты примера не подменяют лимиты нашего runtime; local Gateway token bucket не заменяет общий admission budget.
- [ ] Проверить новые OIDC/audit параметры от Settings до фактического rendered container env: значение в values не доказывает его передачу через фиксированный ConfigMap/Secret. Не публиковать полный env dump.
- [ ] Проверить БД + JSON stdout на одном event ID, отсутствие токенов/текстов документов, поведение rollback транзакции и ошибки доставки. Проверить сохранение снимка имени после удаления пользователя; текущей модели без FK не добавлять FK только для формального соответствия.
- [ ] Согласовать счётчик событий безопасности и внутреннюю доступность метрик. Проверить отсутствие внешнего неавторизованного доступа, ограниченную cardinality и фактическое появление JSON событий в платформенном сборщике.
- [ ] Для выбранного durable outbox проверить rollback/savepoint, pending после рестарта, crash после stdout до marker и повтор с тем же event_id. Best-effort не заменяет выбранный профиль; stdout flush не считать downstream ACK. Historical replay/retention после restore проверяются отдельно.

**Приёмка:** DEV SSO подтверждён с зарегистрированным типом клиента; принят применимый профиль аудита и проверены его каналы/события/гарантии. DB/stdout и durable retry обязательны по выбранному проектному контракту; доверенный IP проверяется для включённого adapter. Logging Operator сам по себе этих гарантий не устанавливает. Пока применимые подзадачи не реализованы и не проверены, общая платформенная готовность остаётся неподтверждённой.

### Задача 6. Backup, restore и поведение незавершённых задач

**Files:** Create `scripts/kubernetes/backup.sh`, `restore.sh`, backup/restore Job templates; Extend lifecycle tests; reuse reviewed functions/scripts in `backend/scripts/` for manifests, Qdrant snapshots и counts.

**Interfaces:** backup принимает explicit context/namespace/release/backup-root и общий operation ID. Артефакт содержит PostgreSQL dump, Qdrant snapshot, `/data`, checksums, versions/digests/Alembic revision, totals и подтверждение завершения. Restore требует новый target namespace/claims; существующий непустой target отклоняется.

- [ ] Tests: `incomplete_backup_not_restorable`, `checksum_mismatch_refused`, `nonempty_restore_target_refused`, `active_download_or_generation_handled`, `helm_uninstall_preserves_data`, `restore_uses_no_production_credentials`.
- [ ] Остановить приложение, дождаться выхода контейнеров и доступности PVC для Job; только после этого снимать согласованный набор трёх хранилищ. Не запускать два writer Job на одном томе.
- [ ] Записывать completed marker только после всех проверок. Backup не содержит Secret objects/plaintext env; отдельно документируется защищённое восстановление ключей/паролей и ACL резервного хранилища.
- [ ] Перед PROD подтвердить backup destination вне исходной VM/дисков с согласованными ACL, шифрованием и retention. Зафиксировать автоматический или операторский запуск по согласованному расписанию и проверку свежести completed backup; ручной разовый smoke не доказывает соблюдение частоты/RPO.
- [ ] Провести `restore_without_source_vm`: исходная VM недоступна, новый независимый target восстанавливается только из внешнего completed backup и отдельного защищённого источника секретов. Подтвердить decryption/access, согласованность PG/Qdrant/files и функциональный smoke; измерить RPO/RTO. Не считать home backup вне kind выполнением этого сценария.
- [ ] Восстановить непустой corpus в новый target: проверить количество документов/концептов/чанков, исходные файлы, ссылки, BM25, роли и экспорт. Не ограничиваться отсутствием ошибок pg_restore.
- [ ] В обоих PG TLS профилях до первого старта восстановленной копии применить согласованную явную ротацию `APP_SECRET_KEY`, удалить восстановленные сессии и OIDC flows и записать решение без ключа/его производных. Старые cookies не дают доступ; новый настоящий вход работает. Возврат auth-таблиц не восстанавливает доступ со старой cookie. Обычное обновление сохраняет ключ. Проверить общий start/recovery путь, а не только отдельный maintenance helper.
- [ ] Прервать generation/export/download во время обновления и документировать фактические paused/failed/retry состояния. Повтор генерации выполняется только по существующему контракту; не объявлять прозрачное продолжение.
- [ ] В owning backup/restore tests добавить `backup_enospc_leaves_no_completed_marker` и `partial_restore_cannot_start_application`: отказ записи dump/snapshot/files/manifest не создаёт completed backup, а частичный импорт не запускает приложение и не повторяется автоматически после потерянного ACK. Fault injection — отдельный ограниченный тестовый том/fixture; не заполнять диск действующей VM или PVC приложения.
- [ ] В общем restore пути проверить контракт [durable outbox](2026-10-04-audit-outbox.md): исторические pending переводятся в held до первого dispatcher, ранее held/stdout_written и immutable audit сохраняются; новые события остаются доставляемыми. Обычный restart продолжает pending с тем же event_id. Полный restore PG/Qdrant/files должен повторить эти проверки; auth-only dump и старый audit snapshot не закрывают текущую поставку.
- [ ] До первого старта восстановленной копии явно заблокировать автоматическое выполнение восстановленных generation/bulk/export/backfill задач и проверить модельные endpoints/credentials выбранного target. Данные заданий сохраняются; допускается только документированный операторский повтор по поддерживаемому контракту. Tests `restored_jobs_do_not_run_before_target_admission` и `ordinary_restart_preserves_supported_job_recovery` проверяют настоящий startup, а не только restore helper. Восстановленный TEST/home корпус не должен обратиться к PROD моделям. Для будущей durable очереди этапа 3 этот же барьер охватывает queued jobs и lease; обычный restart не превращается в DR.
- [ ] Проверить image-only rollback на совместимой схеме и полный restore для несовместимой; `helm rollback` не подменяет database rollback. Зафиксировать измеренные время и объём потери данных, сравнить с согласованными RPO/RTO.

**Приёмка:** подтверждён restore drill после отказа и отдельный off-VM drill; резервные копии не зависят от доступности исходных PVC/VM, секреты восстанавливаются защищённо, удаление release не уничтожает корпус. Пока независимый backup destination не предоставлен, этот gate остаётся открытым.

### Задача 7. Встроить проверки и цепочку поставки

**Локальный прогресс operator tools (04.10.2026):** реализованы воспроизводимая
упаковка, release identity, доверенный bootstrap и
`deploy/ci/gitlab-kubernetes-consumer.yml` для отдельного deploy-проекта.
`backend/tests/test_kubernetes_release.py` проверяет запуск без исходников,
подмену archive/chart, schema/runtime и небезопасные записи архива.
Описание: `deploy/ci/operator-tools/README.md`. Проверка опубликованной поставки,
реальные платформенные adapters и CI Lint остаются открытыми; задача 7 целиком
не завершена. Новые файлы: `scripts/kubernetes/operator_tools.py`,
`deploy/ci/operator-tools/consume.py`, `deploy/ci/operator-tools/README.md`,
`deploy/ci/gitlab-kubernetes-consumer.yml`, `backend/tests/test_kubernetes_release.py`.
Добавлены `deploy/ci/operator-tools/Dockerfile`, allowlist контекста,
`install_tools.py`, `scripts/kubernetes/tests/toolbox_smoke.py` и
`backend/tests/test_kubernetes_toolbox.py`. Локальная Linux-сборка и offline
nonroot consumer пройдены; 76 focused tests без пропусков. Linux probe подтвердил
доступ UID 1000 к synthetic fixtures после явных 0755/0644 при другом host UID.
GitHub workflow
дополнен обязательным job сборки/приёмки; полный запуск workflow не выполнен.

**Files:** Create `.github/workflows/kubernetes.yml`, `deploy/ci/gitlab-kubernetes.yml`, `deploy/ci/readiness.example.json`, `deploy/ci/sonar-project.properties.example`; preserve `.github/workflows/ci.yml`; update anonymous deployment docs. Фактические include/reference и паспорт по схеме общего линтера подключаются в целевых GitLab проектах; реальные пути и реквизиты сюда не копируются.

**Interfaces:** release manifest содержит app revision, backend/frontend image digests, chart version/digest, stub digest для DEV/TEST, storage images, config schema revision (текущий opt-in PG TLS kit — schema3; исторические kit1/kit2 потребляются только своими поддерживаемыми комплектами) и `operator_tools`: `format=1`, `source_revision`, `archive_reference`, `sha256`, `supported_config_schema_versions`, `toolbox_image_digest`. Комплект `operator-tools.tar.gz` сохраняет структуру `scripts/kubernetes/` и содержит нужные для smoke test fixtures; backend operation scripts исполняются из соответствующего backend image, а не независимой копии. Python/PyYAML/Helm/kubectl берутся из закреплённого toolbox. Protected environment config содержит component references/runner tags/registry/Vault references. Secret values отсутствуют.

- [ ] Проверить актуальные README утверждённых GitLab components и mapping для двух Dockerfile/build contexts; не предполагать корневой Dockerfile или ветку master вместо фактической default branch.
- [ ] Развести версии компонентов в документации и samples: не закреплять примерные pins без README/inputs и проверки артефакта. Различие `v1.0.1`/`1.0.1` не исправлять молча. Собственный chart проходит lint/config scan/package и применимый Security-Pipeline; sample DEV `on_success` не подтверждает TEST/PROD rules или promotion digest.
- [ ] Отдельно зафиксировать разрешённые remote package/image mirrors и release repositories: миграция зеркал не означает переноса всех релизных хранилищ. Сверить доступную версию CA image и механизм BuildKit secrets; содержимое готового ZIP sample не доказывает сборку, provenance или retention нашего operator archive.
- [ ] Разделить build и delivery projects; задать передачу release manifest/chart по версии и digest в deploy-проект. Проверить настоящий default branch в rules semantic-release и commitlint, Conventional Commit/MR headers до 72 символов без конечной точки; не переименовывать main под старый шаблон.
- [x] Локальные tests для `deploy_project_without_source_checkout`, `missing_operator_tools_refused`, `operator_tools_digest_mismatch_refused`, `operator_schema_version_mismatch_refused`: упаковка в `scripts/kubernetes/operator_tools.py`, bootstrap в `deploy/ci/operator-tools/`, расширение `release.py`. Consumer проверяет identities до мутаций и исполняет извлечённый overlay из пустого каталога без checkout. Шаблон подключает извлечённый lifecycle; полный запуск lifecycle из опубликованного комплекта в настоящем deploy-проекте проверяется следующим пунктом. Независимая ручная копия скриптов запрещена.
- [x] Общий рецепт toolbox и локальная container acceptance: Python digest, обязательные SHA/version Helm/kubectl, PyYAML 6.0.3, trusted bootstrap, build secret mounts, UID 1000; consumer без сети/source mount и пять отрицательных сценариев. Публикация и provenance остаются в окруженческой копии.
- [ ] Запустить artifact-consumer тест на опубликованном immutable комплекте, включая отсутствие Python/PyYAML/Helm/kubectl зависимости, несовместимый config schema и подмену scripts. Отсутствие/несовместимость инструмента останавливает deploy до lock, backup и миграции. В air-gap контуре комплект и toolbox проходят тот же разрешённый путь доставки.
- [ ] Подключить общий линтер, job-token allowlist, паспорт применимости по актуальной схеме и исходный снимок долга в накопительном режиме. Подтвердить поддерживаемую ветку обновлений либо согласованный immutable ref; отдельно фиксировать фактически исполненную ревизию правил. Не копировать линтер в продукт и не путать политику его обновления с pinning delivery components.
- [ ] Сделать общий линтер обязательной зависимостью всех deploy jobs, без `allow_failure` и optional зависимости. Проверить GitLab CI Lint и отрицательные сценарии: линтер удалён, упал или пропущен; deploy не становится доступным. Настроить штатный pre-push hook с учётом core.hooksPath; не отключать существующие проверки проекта.
- [ ] Собрать application images один раз; chart и stub проходят собственные проверки. Pin component versions; registry auth через Vault, без токенов в Git.
- [ ] Проверить полностью закрытую сборку `linux/amd64`: registry, PyPI/npm и bootstrap package manager используют разрешённые зеркала, build/runtime доверяют CA, Python HTTP-клиенты получают корректный CA bundle, Node получает доверенный CA. Jobs, клонирующие инструменты, содержат git; общая before_script не ломает чужие component images. Поле registry credential в Vault имеет ожидаемый компонентом ключ `token`, без вывода значения.
- [ ] Подключить SonarQube конфигурацию с UTF-8/LF и разрешение job token для security pipeline; проверить состав проверок образов, chart и архивов. Отдельно подтвердить включённое зеркалирование RC и доступность app/PG/Qdrant/stub/job artifacts из TEST. Успешная сборка и созданный registry этих проверок не заменяют.
- [ ] Добавить CI: Helm render/schema/negative assertions → shell lifecycle tests → kind install → fixture flow → restart → update → nonempty restore. CRD schema validation выполняется со схемами фактической целевой платформы.
- [ ] Сохранить действующие backend/frontend/parser/audit/Compose bundled+external jobs. Перед коммитом при реализации выполнить `node scripts/check-project.mjs` по AGENTS.md; полный CI отдельно от узких тестов.
- [ ] GitLab: проверки/build → manual DEV deploy/tests → release tag, RC security checks и перенос артефактов → manual TEST deploy/restore drill из тега → manual protected PROD deploy из того же релиза → restricted live smoke. Если платформенный cd-helm не поддерживает orchestration задачи 4, использовать последовательные разрешённые jobs; не менять порядок ради удобства компонента.
- [ ] Сериализовать jobs установки, backup и restore одной среды; не отменять миграцию автоматически приходом нового pipeline. При остановленном pipeline повтор проверяет operation lock и реальные Jobs.
- [ ] Проверить одинаковые digests при продвижении, отсутствие PROD secrets в DEV/TEST и запрет deploy stub в PROD. Stub image для TEST тоже должен пройти допустимый путь доставки.

**Приёмка:** успешный настоящий pipeline отдельно от локальных проверок; нет ручной пересборки образа для PROD и публикации из непроверенной ветки. Actual publish/deploy выполняется лишь при исполнении согласованного этапа.

**Миграция tinyglobby (локальная приёмка 05.10.2026):** плановая оценка —
**1–2 человеко-дня**. Адаптер ограничен dependency плагина Next ESLint;
собственный fork правил и изменение runtime framework не требуются.

- [x] Реализовать scoped adapter с сохранением upstream plugin и ESLint rules; clean npm ci и Docker context включают local package до установки.
- [x] Проверить rootDir на Windows/Linux: 18 regressions, включая absolute/relative/drive root, glob/brace/array, backslashes, hidden entries и symlink/junction traversal. Unsupported API останавливает lint явно.
- [x] Удалить старую braces/micromatch chain, повторить полный audit:0vulnerabilities. Windows project check:393PASS/1oldSKIP, Linux frontend:394PASS/0SKIP, lint0errors/40oldwarnings; effective67rules совпали. Runtime dependency entries не менялись.
- [x] Собрать final frontend audit-v3; home managed upgrade с coherent backup, actual running compiled hashes308/308, полный HTTPS/OIDC и response/DB/stdout smoke. Completed backup проверен вне VM, старые images/backups сохранены.
- [ ] Повторить required checks на фактически интегрированном release snapshot и внешнем CI. Локальный PASS не означает commit/merge/publication или completion задачи7. Удалить adapter/override после совместимого upstream исправления только с теми же regressions/audit.

### Задача 7а. Подготовить ввод в эксплуатацию параллельно техническим работам

**Files:** обезличенный checklist в `docs/KUBERNETES_DEPLOYMENT.md`; сами согласования, роли исполнителей с именами, ссылки на заявки и реквизиты хранятся во внешнем защищённом реестре. Начало одновременно с задачей 0, не после реализации задачи 7.

**Interfaces:** перечень применимых документов, их владельцы и статус согласования; явные gates до PROD install, опытной и промышленной эксплуатации. Наличие шаблона не означает согласование.

- [ ] Подтвердить применимость частного ТЗ и согласовать требования, безопасность, лицензии и поддержку до разрешённого PROD-развёртывания.
- [ ] Подготовить ролевую модель с соответствием группам IdP, профиль audit events, регламент backup/restore и архитектурную схему потоков/зон/портов. Использовать действующие утверждённые формы во внешнем контуре; сюда перенести только обезличенный статус и критерии.
- [ ] Назначить исполнителей backup/restore/rollback с подтверждёнными PROD правами, сроки/частоту/retention и проверку копий. Сопоставить требования RPO/RTO с измерениями задачи 6.
- [ ] Отдельно отметить согласования сертификатов, выделенного пула/VM, доступов к метрикам/логам и ИБ-приёмку. Не требовать отдельное согласование на изменение для каждого обычного выката на общий пул, если этого не требует процесс системы.
- [ ] Зафиксировать ограниченный круг пользователей опытной эксплуатации, порядок устранения замечаний и разрешение промышленного запуска. Изменение ролей/событий/архитектуры после согласования требует актуализации пакета.

**Приёмка:** формальные блокеры закрыты соответствующими владельцами; технически работающий Pod не подменяет разрешение эксплуатации.

### Задача 8. Провести домашнюю и окруженческую приёмку первой версии

**Files:** Create `scripts/kubernetes/smoke.sh`, `docs/KUBERNETES_TESTING.md`, report under `docs/superpowers/reports/` без реквизитов.

**Interfaces:** smoke принимает explicit target/environment/model mode и fixture set; результат разделён на infrastructure, auth, model-contract, live-model, restore и regression. Любой skipped gate остаётся непроверенным.

- [ ] Дом: install/upgrade/restart/restore, синтетические DOCX/XLSX/PDF/MSG, источники, поиск BM25, stub SSE/ошибки, диагностика и квоты. Дополнительный прогон на домашних настоящих моделях записать отдельно.
- [ ] DEV: реальная доставка через Artifactory/Vault/Gateway, SSO, writable PVC, TLS/CA, container policy и NetworkPolicy. Проверить model outage и отсутствие restart storm; stub outage не скрывать зелёным полным health.
- [ ] TEST: те же RC app digests; репетиция обновления, отказа миграции, backup/restore на непустом тестовом корпусе. Документировать ограничения модели stub.
- [ ] PROD: предварительно проверить модельные endpoints, размеры embeddings и модельные IDs; на ограниченной синтетической выборке подтвердить parsing→generation→index→dense/hybrid search→chat/citations, SSE, лимиты и таймауты настоящих сервисов. Не переносить индекс DEV/TEST; при несовместимости остановить ввод корпуса и применить согласованный rollback.
- [ ] Через браузер выполнить login/logout, upload, generation, source viewer, export/download, trash, admin diagnostics. Проверить роли, CSRF и отсутствие секретов в диагностике.
- [ ] Для DEV/TEST и отдельно PROD проверить `documents/fast/full`, context-window guard, соответствующий модели token counter, отсутствие/ошибку tokenizer, отмену tracked attempt и поздний ответ после отмены. Home synthetic tokenizer не закрывает PROD gate; неподдерживаемый provider оставляет соответствующий режим явно неподтверждённым, а не зелёным по обычному SSE.
- [ ] Проверить public/confidential OIDC и userinfo groups по задаче 5а, audit DB + JSON stdout и доверенный IP на полном маршруте. Проверить доступ оператора к логам/метрикам и результаты общего линтера, SonarQube и security pipeline.
- [ ] Сопоставить каждый применимый критерий с автоматической проверкой или ручным сценарием с причиной выбора. Новые/изменённые приёмочные сценарии проверить на стабильность десятью последовательными прогонами в изолированном тестовом контуре; нестабильность блокирует приёмку до исправления либо явного карантина с владельцем/причиной. Этот gate не означает десять повторных PROD smoke или всей дорогостоящей regression matrix.
- [ ] В sizing учитывать отдельно постоянные PGDATA/WAL, Qdrant, исходные/staging/export файлы и backup staging, а также `/tmp`, `/work`, container logs и image cache. Проверить, какие лимиты реально обеспечивает выбранный CSI/provisioner: значение PVC capacity само по себе не доказывает квоту. Сохранить peak, inode usage, свободное место, применимые requests/limits и резерв для backup/restore; CPU/RAM одного smoke не являются оценкой полного корпуса.
- [ ] Проверить `bounded_storage_exhaustion_recovery` на отдельном ограниченном томе: при отказе записи нет ложного done/completed, секретов в ошибке или автоматического удаления живых файлов; после освобождения места разрешённый resume восстанавливает работу. Node DiskPressure/eviction проверять только на отдельном лабораторном узле, с отдельным verdict. Определить контролируемые сигналы/алерты свободного места, inode, backup age и pending/held outbox по фактическим доступным метрикам; отсутствующий exporter не считается пройденной проверкой.
- [ ] Зафиксировать requests/limits/PVC по измерениям, startup/shutdown/downtime и backup times; ограничить поддерживаемую матрицу реальными версиями. Обновить `docs/PRODUCTION_DEPLOYMENT.md`, `AGENTS.md`, `SECURITY.md` ссылками на общий Kubernetes runbook без идентифицирующих сведений.

**Gate общего этапа 1:** на одном неизменяемом комплекте chart/operator/images
подтверждены compact install/upgrade/rollback, непустой согласованный restore,
OIDC/audit/outbox, исходные local/Compose способы запуска, Windows/Linux regression
и required CI задачи 7. Применимые общие проверки задачи 7а и пригодность bundled
storage закрыты; model-free домашняя приёмка отделена от реальных моделей.
Opt-in PG TLS имеет отдельную T3–T6 приёмку: его незавершённость сама по себе не
блокирует первый выпуск с явно disabled TLS, но общие lifecycle/recovery guards
и безопасность выбранного disabled профиля обязательны.

**Gate конкретной среды:** отдельная deploy-копия проходит DEV → TEST → PROD,
её RBAC/admission/CSI/network/registry/Vault/IdP/log collector gates и PROD
live-model smoke. Это условие ввода в эксплуатацию этой среды, а не зависимость
разработки общего решения от доступов или реквизитов конкретной организации.
Пока реальные модели не проверены, их интеграция остаётся неподтверждённой;
лабораторный stub PASS не заменяет этот критерий.

## Этап 2. Независимые реплики frontend

**Результат:** отдельный frontend Deployment с двумя replicas; backend пока один. Это самостоятельная поставка после этапа 1.

- [ ] Спроектировать и реализовать внутренний diagnostic control/ingestion API вместо shared filesystem между frontend и backend: instance IDs, авторизация, versioned policy, TTL capture, bounded queues/quotas, sanitization, fail-closed при просрочке разрешения.
- [ ] Область изменений: `frontend/src/lib/diagnosticServer.mjs`, `frontend/scripts/diagnostics-runner.mjs`, `backend/app/services/diagnostics/`, `backend/app/api/diagnostics.py`, chart и `SECURITY.md`.
- [ ] Проверить actual Next caches/Server Actions и единый build/deployment identity; общий cache вводить только при используемых server caches. Тестировать обновление смешанных версий и пользовательское состояние.
- [ ] Убрать frontend shared PVC dependency; включить `deployment.mode=split` для frontend при одном backend. Проверить startup/logout/revocation, SSE и поддержку сбора диагностики со всех экземпляров.
- [ ] Приёмка: поочерёдная остановка каждой frontend replica и rolling update; запросы продолжаются через оставшуюся, support bundle содержит обе, duplicate writers/утечки/потеря сессий отсутствуют. Прерванное активное соединение обрабатывается явно.

## Этап 3. Выделить API и обработчик задач

**Результат:** роли `combined`, `api`, `worker` одного backend image, начально 1 API + 1 worker. Combined сохраняет простой local/Compose запуск.

- [ ] Выполнить полный аудит startup/import-time side effects: pipeline, bulk jobs, exports, backfills, purge, translations, diagnostic bundles и recovery. Область: `main.py`, `job_queue.py`, `pipeline.py`, `export_queue.py`, `registry.py`, `staging.py`, DB models/Alembic.
- [ ] В отдельной спецификации этапа 3 зафиксировать матрицу фоновых владельцев `combined/api/worker`, включая audit dispatcher, generation cleanup, backfill и scheduler. `api` не запускает worker/recovery/cleanup как побочный эффект старта. Для первого split с одним worker определить единственного audit владельца и проверять startup/shutdown всех ролей; режим combined сохраняет прежний local/Compose контракт. Tests `api_start_has_no_background_writers` и `split_roles_start_only_assigned_services` проверяют реальный lifespan. Текущий dispatcher использует файловый `.writer.lock`; его нельзя считать межузловым fencing.
- [ ] Утвердить очередь на PostgreSQL как исходный вариант; сравнить с Redis до добавления новой обязательной зависимости. Зафиксировать claim/lease/heartbeat/owner/fencing/cancel/retry contracts в отдельной спецификации.
- [ ] Реализовать durable admission/claim, владение документом, отмену через общее состояние, recovery только просроченного владельца. Приложение не сбрасывает все running/processing при старте API.
- [ ] Минимальный fencing реализовать уже с первым worker: проверка текущего owner/lease/generation при SQL commit и атомарной публикации FS/Qdrant результата. Если нельзя оградить внешнюю запись, использовать изолированные поколения и CAS публикации; старый owner не пишет активное поколение. На этапе 5 расширяются число workers и отказные сценарии, а не впервые появляется эта защита.
- [ ] До реализации утвердить versioned job payload и таблицу совместимости API/worker/DB/config schema; определить порядок expand → upgrade → contract и окно rollback. Tests `new_worker_reads_queued_old_payload`, `unknown_job_version_not_claimed`, `expired_owner_cannot_publish`, `migration_failure_starts_no_worker` сначала должны падать. Неизвестная версия сохраняет задание и останавливает claim с диагностикой, без сброса/потери данных.
- [ ] До реализации в отдельной спецификации определить ограничения очереди: max attempts и общий deadline задания, backoff/jitter, retryable/permanent errors, terminal failed/cancelled и ручной повтор с новым attempt. Persist состояния и причины не содержит документов/секретов; постоянно падающее задание не блокирует следующие. Проверки `permanent_failure_not_retried`, `retry_budget_survives_restart`, `cancelled_job_not_reclaimed` и `poison_job_does_not_starve_queue` привязать к этому контракту; значения не подставлять произвольно в Helm. Это работа этапа 3, не условие compact-поставки.
- [ ] Согласовать файловый доступ API/worker: RWX на разных узлах либо ограниченное совместное размещение до перехода на storage adapter. Процессный filesystem lock не считать межузловой блокировкой.
- [ ] Приёмка: рестарт API не повреждает живую worker задачу; остановка worker до/после claim и до/после публикации сохраняет восстанавливаемое состояние; duplicate request не публикует дубль. Shared global admission не растёт от числа процессов.
- [ ] Приёмка совместимости: непустая очередь и running attempt переживают управляемое обновление и допустимый rollback; возобновившийся просроченный owner не публикует старое поколение. Несовместимая DB/job schema блокирует запуск новой роли до согласованного drain/миграции/restore.
- [ ] В restore контракт durable очереди включить queued/running/cancelled jobs и lease, totals/checksums и идентичность новой инсталляции. Восстановленный lease не даёт владения в новом target; задания не запускаются до явного допуска его endpoints и политики повторов по задаче 6. Tests `restored_worker_lease_not_reused`, `restored_jobs_wait_for_explicit_admission` и `restored_cancelled_job_stays_cancelled` относятся к этапу 3; наличие этих пунктов не объявляет новую очередь реализованной в compact.

## Этап 4. Разрешить реплики backend API

**Результат:** 2 API + 1 worker, отдельные сервисы и lifecycle. До включения закрыть все типы локального состояния, а не только очередь.

- [ ] Вынести rate/admission/LLM budgets, per-document locks, upload/dedup/staging ownership, chat cancellation, export и diagnostic download leases в общий контур.
- [ ] Добавить согласованное обновление config/cache/словарей и единственного владельца cleanup/purge; проверить auth session keys и отзыв прав на обеих репликах.
- [ ] До нескольких backend процессов закрепить cluster-wide ownership audit dispatcher: отдельный единственный владелец либо проверенный DB claim/lease/fencing контракт из спецификации этапа 3. Файловая блокировка на локальном PVC не разрешает несколько межузловых dispatchers. Tests `second_audit_owner_cannot_dispatch`, `audit_owner_failover_preserves_event_identity`, `audit_crash_after_stdout_repeats_same_event_id` проверяют передачу владения и выбранную at-least-once доставку: возможный повтор имеет прежний event_id, historical held не публикуется, потерянный marker ACK не выдаёт ложное подтверждение. Этот gate повторяется перед несколькими workers этапа 5.
- [ ] Повторить таблицу совместимости этапа 3 на mixed API/worker versions и непустой очереди; rolling допускается только для подтверждённых пар. Неподдерживаемая комбинация требует окна обслуживания. Проверить старый API → новый worker и новый API → старый worker только там, где пара объявлена поддерживаемой; иначе deployment отклоняется до запуска.
- [ ] Устранить зависимость API от локальных файлов конкретной replica; обеспечить чтение source/export и атомарную публикацию независимо от выбранного API Pod.
- [ ] Область: `rate_limiter.py`, `llm_client.py`, `deduplication.py`, `document_tag_service.py`, `chat_history.py`, `trash.py`, `api/chat.py`, `export_queue.py`, diagnostics, storage layer и chart. Аудит может расширить список до составления подробного плана.
- [ ] Приёмка: чередование API replicas и kill одной во время upload/chat/download/delete, конкурентные правки/переиндексация/отмена; права и лимиты общие, старый writer не публикует данные, lease защищает активную загрузку от очистки.
- [ ] SSE при падении обслуживающего Pod может прерваться; UI сообщает об этом, история не повреждается. Прозрачное продолжение генерации — отдельная возможность, не подразумеваемое свойство реплик.

## Этап 5. Несколько workers и проверенная эксплуатация

- [ ] Расширить уже реализованные на этапе 3 at-least-once ownership/fencing/generation guards на несколько workers; проверить идемпотентные видимые результаты SQL/FS/Qdrant. LLM вызовы могут повториться, их стоимость входит в global budget.
- [ ] Проверить потерю lease, длительную паузу/сетевое разделение, возобновление старого worker, отмену одновременно с publish, исчерпание диска и повторное выполнение после сбоя. Просроченный worker не может повредить новое поколение.
- [ ] Начать с ручного числа workers. HPA вводить после измерений по длине/возрасту очереди и ресурсам модельных endpoints, с верхним пределом общей нагрузки.
- [ ] Добавить topology spread/PDB и проверку drain; отдельно проверить аварийный отказ узла и ограничения Deployment rollout. PDB ограничивает voluntary eviction, но не предотвращает потерю узла и не управляет RollingUpdate. Оценить HA PostgreSQL/Qdrant/CSI/модельных API отдельно; реплики приложения не доказывают HA всего сервиса. Основание: [Kubernetes disruptions](https://kubernetes.io/docs/concepts/workloads/pods/disruptions/#pod-disruption-budgets).
- [ ] Повторить backup/restore уже с очередью и worker ownership; восстановленная тестовая копия не запускает production jobs.

## Расширение домашнего стенда для этапов 3–5

До отказных межузловых проверок подготовить 1 control-plane VM (2 vCPU, 4 ГиБ RAM, 32 ГиБ disk) и 2 worker VM (каждая 4 vCPU, 8 ГиБ RAM, 80 ГиБ disk). Итого ориентир 10 vCPU / 20 ГиБ RAM / 192 ГиБ disk плюс общее хранилище. Ресурсы подтвердить инвентаризацией до выделения. Existing training VM можно переиспользовать в одной роли после проверки и сохранения нужных данных; не пересоздавать её автоматически.

Отдельно предусмотреть CSI, при необходимости RWX storage/NAS, backups вне исходных дисков, тестовый Gateway/IdP и метрики. Домашний storage VM может потребовать ещё 2 vCPU / 4 ГиБ / 100–200 ГиБ. Один control-plane достаточен для app failover при его доступности; HA control-plane требует трёх. Несколько VM на одном физическом Proxmox-узле проверяют отказ VM/Pod, но не отказ физического хоста.

## Дополнение проверки — 09.10.2026

Историческое дополнение не задаёт следующую задачу. Home input transport и OIDC R7 уже проверены. Действующий порядок: R1–R6 карточки 2б.3.3 с нужными barriers до import/start, затем домашний lifecycle и release/CI. Конкретное ближайшее действие задано карточкой исполнения в начале плана. Холодный запуск и storage exhaustion остаются отдельными незакрытыми критериями задач 1/6/8. Не повторять неизменный OIDC proof без нового изменения или гипотезы; исходные deadlines сохраняются.

Различать обычный restart и disaster restore: первый сохраняет ключ сессий и продолжает pending outbox, второй выполняет согласованную ротацию ключа, purge сессий/flows и hold исторических pending до запуска. Поддерживаемые schema операторского комплекта, backup format и версия job payload — разные версии; матрица совместимости фиксируется в release manifest и не выводится из совпадения номера.

Технические основания: [Kubernetes resource accounting](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/#local-ephemeral-storage) и [kind persistent host mounts](https://kind.sigs.k8s.io/docs/user/configuration/#extra-mounts). Host mount переживает удаление node container только при соответствующей фактической конфигурации; внешний backup по-прежнему обязателен.

## Проверка плана перед исполнением

- [ ] Получены отсутствующие capabilities и ресурсные ограничения без добавления реквизитов в Git.
- [ ] Зафиксирован объём первого исполнения: этапы 0–1 либо выбранная их часть; реплики не включены случайно вместе с Helm.
- [ ] Для каждого последующего этапа составлен подробный план по текущему коду с миграциями, совместимостью и failure tests.
- [ ] Есть отдельные rollback решения для приложения, схемы и данных; backup drill не заменён snapshot VM.
- [ ] В финальном отчёте разделены «описано», «реализовано», «проверено дома», «проверено в DEV/TEST», «проверено на настоящих PROD моделях».


Checkpoint 05.10.2026 — file-material candidate: audit-v11-files, packaged274/274; Linux required profiles68, network24, PostgreSQL31, whole-storage2 PASS без пропусков; Windows focused101PASS/6POSIX SKIP. Новый helper ограничивает source read/config, snapshots живут до close, incomplete cleanup quarantines admission. Runtime реализован в рабочей ветке, в main синхронизирована документация. Следующий приоритет: file-phase lifecycle и projected Secret/Windows ACL, затем полный release/home/CI. Не закрывать реплики или целевую платформенную приёмку этим scoped proof.


Checkpoint 05.10.2026 — file rotation/lifecycle candidate: audit-v13-file-lifecycle, packaged274/274; Linux required profiles86, network24, PostgreSQL31, whole-storage2 PASS без пропусков; native Windows114PASS/8POSIX SKIP. Missing/generation rotation отказ, protected Windows DACL, exact IPC и injected metadata/read burst/cancel/SIGTERM проверены. Следующий приоритет: bounded temp/owner creation, cleanup/SIGKILL, actual projected Secret/CA/CRL; затем full release/home/CI. Synthetic filesystem fixtures не подтверждают реальную CSI, новый candidate дома ещё не развёрнут. Runtime рабочей ветки и docs mirror в main различать; replicas и target platform acceptance остаются OPEN.


Checkpoint 05.10.2026 — bounded temp/owner candidate: audit-v14-file-creation, packaged274/274; profiles100/network24/PG31/storage2 PASS без пропусков, native Windows127PASS/9POSIX SKIP. Creation и marker I/O перенесены в helper; незавершённый create/prepare/DNS/cleanup сохраняет exact owner и удерживает slot. Следующий приоритет: крайние cleanup/partial marker faults и parent SIGKILL/orphans, actual projected Secret CA/CRL; затем full release/home/CI. Домашний runtime не обновлён, rollout/replica gates не закрыты.


Checkpoint 05.10.2026 — cleanup edges/budget candidate: audit-v16-cleanup-budget, packaged274/274; required Linux profiles120/network24/PG31/storage2 PASS без пропусков, native Windows144PASS/12POSIX SKIP, выбранная stability10×26=260PASS/0SKIP. Marker/IPC/deadline/partial cleanup и controlled SIGKILL witness проверены. V15 cleanup timeout FAIL сохранён; общий cleanup .5s перераспределён .4work/.1reap без повышения общего лимита. Production orphan recovery остаётся OPEN. Следующий приоритет: actual projected passfile/CA/CRL/default/explicit/read-only/nonroot/fsGroup и writable private temp mount (chart пока без explicit /tmp emptyDir), container restart versus Pod delete; затем оставшиеся native/operational gates, полный release/home/CI. Домашний runtime не обновлён; replica/collector/platform gates не закрыты. [Proof](../reports/2026-10-04-audit-outbox-acceptance.md#cleanup-edges-и-budget-05102026--scoped-proof-release-open).


### Selected actual Secret/temp proof (05.10.2026)

На unchanged v16 выполнен отдельный домашний kind Pod proof: actual read-only projected Secret без subPath, UID/GID/fsGroup1000, read-only root/private memory emptyDir64Mi. Explicit/default paths, coherent CA/CRL rotation sampling, old snapshot hashes, CRL removal/reappearance и container-restart versus new-Pod temp lifetime PASS;274 running source hashes matched. Synthetic file bytes — не native TLS/CRL/auth proof. Direct0440 passfile сохраняет ignore semantics; test-only0600 staging принимает bytes, не реализует dynamic synchronizer. Product chart temp/projection/passfile configuration и operational orphan recovery, remaining profiles/full release/home app upgrade/CI OPEN. Работающее приложение не обновлено. [Отчёт](../reports/2026-10-04-audit-outbox-acceptance.md#actual-projected-secret-и-emptydir-05102026--selected-file-proof).

#### Product temp layout 05.10.2026 — selected package

Generic chart получил отдельные bounded disk emptyDir /tmp для backend/frontend,
TMPDIR/TEMP/TMP и ephemeral resources; backend contract наследуется writer Jobs,
legacy backup старым template сохранён. Начальные1Gi/256Mi — не measured sizing.
Password остаётся runtime Secret env. Продуктовый CA/CRL profile application/Jobs/
restore, native TLS/auth/revocation, disk-pressure и orphan ownership остаются OPEN.
App home v7 не обновлено, v16 app sources неизменны. [Приёмка и границы](../reports/2026-10-04-audit-outbox-acceptance.md#product-temporary-storage-05102026).

#### PostgreSQL CA/CRL: найденный prerequisite 05.10.2026

По исходникам bundled PostgreSQL ещё не включает TLS и server cert/key/HBA.
Preflight запрещает query в DATABASE_URL; Jobs не переносят PG trust identity.
pg_dump/pg_restore используют local socket внутри PG Pod и не подтверждают TCP
TLS. Поэтому клиентский CA/CRL mount сам по себе не является работающим профилем.

Подготовлен [проект полного opt-in bundled TLS](../specs/2026-10-05-kubernetes-postgres-tls-design.md):
server certificate/key и hostssl SCRAM, client verify-full и optional CRL,
immutable version-named Secrets, общий app/Job contract, schema3 operator kit
с явным PEM parser, backup format3 и legacy2=unknown с отдельным disabled adoption.
Не объявлять TLS обязательным внешним требованием; disabled сохраняет первый запуск.

Письменный дизайн исправлен при проверке; исполняемый план T1–T6 добавлен.
При реализации пройти последовательно operator validation → chart/server/Jobs →
backup/restore compatibility → valid native crypto/projected Secret proof →
полный одноразовый lifecycle и ресурсную приёмку. File-only v16, prior regular-file
native profiles и локальные render tests не закрывают эти новые gates.
Это проектирование; runtime/chart/toolbox/backup format не изменены этим пакетом.
Home upgrade, целевая платформа, реплики, orphan cleanup и dependency gates OPEN.

#### Проверка плана 05.10.2026 — приоритет и закрытые пробелы проектирования

Подробный [план PostgreSQL TLS](2026-10-05-kubernetes-postgres-tls.md) разделён на
T1 profile/kit, T2 chart/Jobs, T3 lifecycle/recovery, T4 backup/restore,
T5 native projected crypto proof и T6 full release. Это предстоящая реализация.

Opt-in TLS не блокирует первый disabled bundled запуск, если выбранная среда
не требует PG TLS; его собственные audit/backup/resources/dependency/CI gates
остаются открытыми до проверки. TLS release требует весь T1–T6. Неиспользуемые
native auth profiles не добавляются молча в поддерживаемую поставку: зафиксировать
поддерживаемую матрицу и ранний отказ остальных; наличие лабораторного profile
не означает его production support.

Уточнены: legacy backup2=unknown и explicit adoption; restore с истёкшей старой
PKI через привязанный trust transition; actual first initdb/VSO ordering;
immutable versioned HBA; новые Pod/connection proofs при rotation; checkpoint и
ручной recovery до/после migration; отсутствие automatic downgrade; exact schema2/
schema3 recovery kits; PG GSS/SCRAM/defaults; срок жизни ключей/CRL и реальные RPO/RTO.
Runtime в этой проверке не менялся; документирование не закрывает implementation
или platform gates. Предшествующие checkpoint ниже/выше — исторические результаты.
