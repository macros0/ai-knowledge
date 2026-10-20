# Bundled PostgreSQL TLS — проверка этапов

## Обычный restore R2 — source pin проверен, импорт открыт (10.10.2026)

Текущий private582 source579/map
`53e52241d6c18454329e349b87cb2f79896e9f68ff86e5e228c30af431699936`.
Scope28 соответствует этим байтам; runtime565/main и рабочие установки не обновлены.

Проверена только часть R2: restore требует внешний expected manifest SHA256,
повторно проверяет source и отказывает при отсутствующем/неверном pin, замене
manifest/member или появлении лишнего файла. CLI проверяет restore-only параметр
до создания Manager. Owning RED без pin сохранён:1 FAIL,2,16/180с.
Windows24 PASS/11 owned-Linux SKIP,16,02/180с; Linux24 PASS/0 SKIP,35,42/180с,
cleanup штатная, live/unknown actors не оставлены, source unchanged. Linux запуск
исключал11 неизменённых actual R1 read-probes; повторной actual SQL-приёмки R2 нет.
Records и логи находятся в существующем `compact-delivery/`:
`restore-source-pin-windows-red.json`, `restore-source-pin-windows-green.json/.log`,
`restore-source-pin-linux.json/.log`, `restore-source-pin-source.json`.

**Предел:** pin в fixture вычислен для синтетического backup. Hash связывает байты,
но не подтверждает независимого издателя, protected promotion или разрешение
import/start. Контракт независимого expected release pin описан в spec:378–399;
производитель/канал protected source остаётся P2-B. Этот PASS его не закрывает.

Штатный caller по-прежнему достигает неподдержанного raw `pg_restore` input;
контроль `test_compact_restore_existing_caller_reaches_bound_postgres_import`
подтверждает это, а не восстановление строки или COMMIT. Следующий результат —
fixed bounded PG import через тот же журнал с actual PG17 строкой/revision,
typed подтверждением транзакции и lost-ACK controls. Source-pin этап заново
не выполнять. Полный R2, restore/CLI/handoff/adoption/release остаются OPEN.
[Единственная очередь и критерии](../plans/2026-10-03-kubernetes-rollout.md#карточка-2б33--обычный-restore-активна).

## Обычный restore R1 — fixed empty-target проверен (10.10.2026)

Source579/map `92b9a8f134e204a94effc3292c8f78758b4f77f32a922f481a212f196803333e`, scope28 актуален, private candidate не
принят в runtime/main. Owning `Manager.restore(source)` теперь подтверждает
empty-target через общий journal и достигает следующего неподдержанного
`pg_restore` input. API/Helm/activation в owning fixture остаются fixtures;
native Kubernetes restore/import/start/unlock этим не принят.

Причинный RED `restore-empty-windows-red-r2.json`: штатный caller достиг raw
empty probe, но typed dispatch отсутствовал. Новый fixed read action связан с
Job/Pod/source, exact storage target generation, effect/actor и исходным deadline.
Closed ACK требует пустых PG/Qdrant/files; unknown/dirty/drift не допускает replay,
complete, import или unlock. PG проверяет и views/sequences/functions/types/custom
schemas; соединение read-only, server identity и SQL timeouts проверены.

Настоящий probe выявил отдельный дефект: `get_engine()` → `get_settings()` →
`ensure_dirs()` создавал4 каталога в /data после начальной проверки пустоты.
Прямой RED — `restore-empty-no-write-red.json` и сохранённый unit failure log.
Исправлен только fixed probe: Settings без ensure_dirs/root .env, отдельное
соединение с disabled SSL/GSS и повторная проверка data перед ACK. Обычные
настройки и запуск приложения не менялись.

Итоговый snapshot: Windows10 owning PASS/11 owned-Linux integration SKIP,
8,83/180с; Linux21 PASS/0 SKIP, whole38,55/180с, unit120. Все11 actual cases
выполнены на PG17/Qdrant/data: пустой target, dirty SQL objects/schema, файл,
symlink, Qdrant collection и чужой SQL endpoint; бизнес-данные не изменяются.
Linux cleanup штатная, live/unknown actors не оставлены, source unchanged.
Records: `restore-empty-windows-final.json`, `restore-empty-linux-final.json`,
`restore-empty-component.json`, `restore-empty-source-final.json`.

Shared checks перед последней узкой no-write правкой: Windows133 PASS/44 SKIP
107,58/180с на предыдущем mapeea6. Linux на нём дал150 PASS/12 unchanged PG
integration SKIP и11 failures новых read-probes из-за упавшего Qdrant. Этот прогон
не считается GREEN. Причина Qdrant — отсутствующий writable snapshots path при
read-only rootfs; исправлен только Lab существующим snapshots tmpfs. Первый
Lab также отказал при chmod после chown (cap CHOWN); порядок исправлен без
расширения capabilities. Все FAIL сохранены; общий набор не повторялся, проверен
только owning restore и изменённый actual probe. Required release checks OPEN.

Следующий R2 — protected source origin и bounded PG import через тот же caller.
Byte-valid backup и R1 read ACK не разрешают запись. Полный restore, CLI/handoff,
barriers/adoption/release остаются OPEN; [очередь R1–R6](../plans/2026-10-03-kubernetes-rollout.md#карточка-2б33--обычный-restore-активна).

## Обычный backup format3 — producer/consumer и native проверены (10.10.2026)

Snapshot579/map
`f646a8651ed7232623d58ddeb8d99f97d4c01a6147251192a05269659a7b2521` изменяет
lifecycle и3 owning test modules относительно map32bb. Scope28 hashes актуальны;
runtime/main не обновлены, ordinary restore/CLI/unlock/adoption/release OPEN.

**Причинный RED:** `compact-delivery/ordinary-format3-red-r2.log` —4 failures:
common producer выдавал format2, consumer отказывал3, disabled restore достигал
acquire для unknown legacy. `ordinary-format3-profile-red.log` —2 failures при
saved/desired PG mode mismatch до mutation. Первоначальный launcher отказал до
collection из-за PowerShell basetemp argument; `ordinary-format3-red.log` сохранён
отдельно и не считается причинным RED.

**Исправление:** common compact backup выдаёт format3 с postgres_tls:null лишь
после confirmed fixed plaintext export и matching revision target generation,
exact dump hash/size. Отсутствие chart TLS fields отдельно не является этим
доказательством. Existing strict `_verify_backup_format3` проверяет current
consumer/prepared manifest; pending manifest вынесен из закрытого artifact set
в тот же protected parent, публикация остаётся create-only и fsync. Legacy2
consumer/non-common поведение сохраняется; новый restore отклоняет unknown legacy
и PG profile mode/CA/CRL/policy mismatch до acquire. Enabled TLS backup/export
и protected-origin/import/start authorization этим disabled результатом не приняты.

**Проверки:** focused Windows32 PASS53,06/180с. Shared affected Windows218 PASS/
16 POSIX-only SKIP87,08/180с; Linux232 PASS/2 Windows-only junction SKIP86,19/180с.
Все16 POSIX-only cases выполнены на Linux, оба junction — на Windows. Исходники
не менялись, Linux cleanup штатная. Windows pytest exit0; original wrapper
ошибочно ожидал10 skips и вернул failure. Исправлена только интерпретация его
сохранённого лога, без нового прогона: `ordinary-format3-windows-affected.json`
и `ordinary-format3-windows-affected-verified.json` содержат исходный/проверенный
результаты; log SHA сохранён. Linux — `ordinary-format3-linux-r1.json`.

**Native R4:**250,42/900с, все13 effects confirmed, полный6-artifact format3
комплект с known disabled PG profile, totals1/1/1/2 и revisionb6c7d8e9f0a1.
Перед запуском физически проверены прежние actors; unknown journals/locks и
старый принятый format2 комплект не переиграны/не изменены. Off-VM Windows
consumer и existing format3 byte validator прошли0,33/60с. Независимый observer
3,36/60с подтвердил baseline обоих кластеров, отсутствие operator/toolbox/Job,
13 confirmed effects и неизменность source. Records:
`home-backup-native-format3-r4-{stage,parent-terminal,result-pointer,confirmed-journal,offvm-verification,observer}.json`;
off-VM copy `compact-delivery/home-backup-native-offvm-02a6e2ec2055/`.

**Исторический следующий caller на mapf646:** fixed empty-target probe ещё
не входил в closed exec actions; теперь он проверен в R1 выше. Далее нужны bound PG restore stdin, safe tar/Qdrant import,
totals/integrity/revision и обязательные recovery barriers до start/unlock.
[Активная карточка2б.3.3](../plans/2026-10-03-kubernetes-rollout.md#карточка-2б33--обычный-restore-активна) задаёт R1–R6: import при остановленном приложении является промежуточным результатом; нужные origin/start barriers подключаются до полного restore, а не после CLI. Эта правка порядка не добавляет новых PASS.
Предыдущая запись об отказе format2 при disabled target была гипотезой с неверным
пониманием None; текущий RED исправил этот вывод. Исторические записи ниже
сохраняют свои результаты, но не переопределяют текущую очередь.

## Настоящий backup — native и off-VM приняты (10.10.2026)

**Закрыт 2б.3.2, общий release OPEN.** Snapshot579/map
`32bb648fab4fbfdf87f0b4a9a3e454eab3e997b889cf8986d5b164d645585030` меняет только
lifecycle и owning effect tests относительно предыдущего map2db. Default fixtures
моделируют настоящий ACK Job generation+1/grace0; отрицательные проверки сохраняют
refusal при generation+2/старом generation, grace1/bool и чужом template.

Затронутые проверки: Windows78 PASS/10 Linux-only SKIP,77,03/180с;
Linux88 PASS/0 SKIP,74,53/180с, штатная cleanup. Исходники не изменились во время
обоих прогонов; неизменные общие матрицы не повторялись. Records:
`compact-delivery/server-delete-generation-windows-r2.json`,
`server-delete-generation-linux-r1.json`, `server-delete-generation-component.json`.
Diff27/scope.json hashes обновлены, прежний manifest сохранён; adoption false.

Native R3 прошёл за246,59/900с. Штатный `Manager.backup_stopped` вернул
consumer-verified Path с непустыми1 document/1 chunk/1 concept/2 points,
Alembic head `b6c7d8e9f0a1` и6 артефактами format2. Все13 journal effects
confirmed: Job create,11 exec (включая completion), conditional job-delete.
Перед запуском подтверждены завершение/отсутствие прежних actors; их unknown
journals/locks не продолжались, R5/R1 quarantine не изменены. Источники:
`home-backup-native-completion-r3-stage.json`,
`home-backup-native-completion-r3-parent-terminal.json`,
`home-backup-native-completion-r3-result-pointer.json`, связанный home record,
`home-backup-native-completion-r3-confirmed-journal.json`.

Off-VM transfer и настоящий Windows `verify_backup` прошли за0,31/60с:
6 regular artifacts, exact sizes/hashes, complete format2, одинаковые totals.
Копия: `compact-delivery/home-backup-native-offvm-67b0ee6427a6/`;
record `home-backup-native-completion-r3-offvm-verification.json`.
Независимый observer3,58/60с подтвердил отсутствие operator и временных Job/Pod,
все13 confirmed effects, source hashes и сохранность прежних workloads/PVC/PV/
Node обоих кластеров; новый owned target исключён только по namespace/claim UID.
Record `home-backup-native-completion-r3-observer.json`.

**Предел:** format2 сохраняется как проверенный consumer result, но PG trust
в нём unknown для нового restore. Spec требует для новых backups format3 и
обязательное `postgres_tls` (null для проверенного disabled). Это фактический
producer/restore разрыв: `lifecycle.backup_stopped` пишет format2,
`lifecycle.restore` обоснованно отклоняет такой source до lock/import.
Следующее действие — [2б.3.3](../plans/2026-10-03-kubernetes-rollout.md#карточка-2б33--обычный-restore-активна),
используя existing format3 contract, затем typed imports/полный restore. Не
переписывать закрытый format2 комплект и не ослаблять unknown legacy refusal.
Stopped target был предусловием; `Manager.stop`, ordinary CLI, release_lock,
PG checkpoint/admission, restore, adoption и release этим PASS не подтверждены.
Runtime565/main и рабочие установки не обновлены. Ниже — исторические отказы.

## Настоящий backup — completion подтверждён, retirement открыт (10.10.2026)

Это актуальный checkpoint; разделы ниже сохраняют прежние отказы и не задают
повторную работу. Общий compact release, backup/off-VM/restore/CLI/adoption OPEN.

1. Job controller lag воспроизведён причинным RED:
   `compact-delivery/job-controller-lag-red.log:129` (2 failed).
   Ограниченное ожидание Complete сохраняет exact UID/spec/Node и исходный срок.
2. Настоящий Popen выявил вторую гонку: completion публиковался до actor binding.
   Источник: `compact-delivery/completion-stdin-gate-red-proof.log:91,101`.
   Fixed publisher получил stdin gate; проверки предыдущего snapshot579/map
   `2db6b363abe0d7cbb473d34116be678fbd3549e4146d2e7c47245b9cedaada00`
   сохранены в `completion-stdin-gate-windows-full.json` (462,164,11/180с) и
   `completion-stdin-gate-linux-full-r1.json` (495,114,27/180с, штатная cleanup).
   Эти результаты перечитаны, не запущены заново в текущей проверке плана.
3. Native R2 на этом snapshot закончился отказом за243,70/900с. Job create и11
   exec, включая completion, confirmed; job-delete prepared. Manifest отсутствует,
   operator terminal и его PID отсутствует. Источники:
   `compact-delivery/home-backup-native-completion-r2-result-pointer.json:2`,
   связанный home record, `home-backup-native-completion-r2-failed-journal.json`.
   Это реальное продвижение caller; полный backup ещё не принят.
4. Контрольный owned Job без данных подтвердил настоящий DELETE ACK:
   `compact-delivery/home-job-delete-actual-ack.json`: Job generation2 при
   create generation1, grace0, foregroundDeletion, сохранённый spec. Guard
   требовал прежний generation и отклонял ответ. OpenAPI обещал Status, поэтому
   одного описания схемы недостаточно для изменения validator. Контрольный Job
   и namespace штатно удалены; старый uncertain DELETE не восстановлен.
5. Новый owning RED сохранён в `server-delete-generation-red.log:125` (2 failed).
   В private source добавлена нормализация только доказанного перехода. Правка
   fixtures оборвалась после записи product file, зависимый тест всё же запущен:
   `server-delete-generation-green.log:378`:8 failed,66 passed,10 skipped,
   `server-delete-generation-green.json`:exit1,69,97/180с. Default fixtures
   продолжают выдавать старый generation; прежний отказной ack-generation=2
   теперь совпадает с нормальным native ответом. Это текущий RED, а не acceptance.

**Следующий шаг:** завершить и проверить DELETE fixtures/validator по общей
[карточке 2б.3.2](../plans/2026-10-03-kubernetes-rollout.md#карточка-2б32--полный-backup-format2-принята),
затем affected Windows/Linux GREEN, актуальный source map, один native backup
и off-VM consumer. `compact-delivery/scope.json.current_source_map` остаётся
map639; он не описывает новые правки. Его актуализировать только с проверенными
hashes; не присваивать новым байтам прежние462/495 PASS. Runtime565/main и рабочие
установки не обновлялись. Старые prepared effects/locks остаются без replay/unlock;
физическое отсутствие Job само по себе не подтверждает DELETE ACK.

## Настоящий backup — completion не подтверждён (10.10.2026)

После физического завершения первого exact toolbox выполнен цельный clean
сценарий на прежнем source579/map639, в том же домашнем Lab. Он закончился
отказом за234,89/900с, без увеличения budget/action deadline. Новый journal
подтвердил Job create и10 exec, включая одинаковые before/after totals
1 document/1 chunk/1 concept/2 points и ready `completion_ready:true`.
Последний `toolbox-complete` остался prepared, outcome отсутствует;
job-delete intent и manifest не созданы. `native_backup_success:false`.

**Наблюдаемый разрыв:** контейнер exact Pod завершился с exit0/Completed в
06:04:24 UTC; operator записал отказ retirement в06:04:25,833; Job controller
поставил Complete в06:04:26. Само последующее Complete не подтверждает unknown
effect, не даёт права дописать manifest, повторить completion или снять lock.
Первый toolbox завершился с exit1; прежний after-totals prepared также сохранён.

**Гипотеза, ещё не причинный тест:** текущий
`operation-effects582/source/scripts/kubernetes/lifecycle.py:7958–7960` после
проверки ACK сразу вызывает strict capture; `:7859–7860` для Succeeded требует
полного `recovery_native_terminal`, включая Job Complete. Отдельное ожидание
`:8081–8087` начинается позднее и выходит уже по succeeded1. Fixture
`operation-effects582/source/backend/tests/test_kubernetes_operation_effect_journal.py:1734–1735`
меняет Pod и Job одновременно. Сохранённые времена согласуются с гонкой этого
перехода, но не доказывают получения/валидности ACK на failing вызове: его outcome
не записан. Причину должен подтвердить owning RED, без принятия unknown result.

Доказательства: `compact-delivery/home-backup-native-result-pointer.json:2`,
связанный `home-provider565/home-backup-763ba315f0cb-clean.json` (operator terminal,
failure coordinates, original budget, whole elapsed),
`compact-delivery/home-backup-native-clean-failure-observation.json` (Pod/Job
timestamps), `home-backup-native-clean-failed-journal.json` (effect phases),
`home-backup-native-failed-actor-terminal.json` (старый actor terminal).
Это сохранённые результаты реальных запусков, перечитанные при проверке плана;
новый native запуск и продуктовые тесты в этой проверке не выполнялись.
Product code/runtime и рабочие установки не изменены. Backup/off-VM/CLI/restore/
adoption/release остаются OPEN. Следующее поручение — причинное исправление в
[единственной карточке 2б.3.2](../plans/2026-10-03-kubernetes-rollout.md#карточка-2б32--полный-backup-format2-принята).

## Исторический первый backup — срок истёк, комплект не завершён (10.10.2026)

На source579 `63968a1ddf2d1fea42c5fbc746f4e6688b72d222a82d81129a3a87cc7be2206e`
выполнен настоящий `Manager.acquire` → `backup_stopped` в домашнем Kubernetes.
Working runtime не обновлён. Созданы остановленный application target и
непустой синтетический корпус:1 document,1 chunk,1 concept,2 Qdrant points;
Alembic head `b6c7d8e9f0a1`. Это model-free backup, не оценка качества LLM.

Подготовка выявила реальные расхождения тестового adapter с API: omitted
`initialDelaySeconds:0`, default StorageClass и Python import path seed. Они
исправлены без изменения продукта и без повторных Windows/Linux матриц.
Первый operator отказал до journal/lock из-за запрета доступа к `/proc/1/ns/pid`
под обычным пользователем VM. Второй выполнялся в существующем административном
OS scope, с тем же kubeconfig на VM и сокращённым до остатка исходных900с сроком.
Это доказательство этого OS scope; работу host operator без привилегий не заявляем.

**Подтверждено настоящим journal:** Job create +8 exec effects: PG export,
integrity, before totals, snapshot, data tar, Qdrant tar, totals stream и revision.
Получены postgres.dump223469bytes, data.tar10240, qdrant.tar593920,
totals.json75, release.json477. Полный комплект **не принят**: исходный срок
истёк при after totals, последнее действие осталось prepared; ready/completion/
retirement/финальная публикация не подтверждены. Manifest отсутствует.
Operator terminal и отсутствие его PID подтверждены отдельно. Lock/journal и
частичные файлы сохранены; неизвестное действие не повторялось. Toolbox после
отказа ещё Running и наблюдается по точному UID/Job/Node; одно завершение SSH
не считается его остановкой. R5/R1 quarantine не изменены.

Источники: `compact-delivery/home-backup-native-operator-r1.json`,
`home-backup-native-acquire-diagnostic.json`, `home-backup-native-operator-r2.json`,
`home-backup-native-failed-journal.json`, `home-backup-native-current-actors.json`,
`home-backup-native-attempt-verification.json`; adapter и прежние его версии
сохранены в том же каталоге. Новая baseline-проверка подтвердила сохранность
исходных workloads/PVC/PV/Node в обоих домашних кластерах. Candidate579 совпадает
с seal, product code/runtime не менялись.

На момент первого отказа следующим шагом было дождаться exact toolbox terminal
и выполнить clean сценарий. Эти действия теперь выполнены; новый отказ приведён
в верхнем разделе. Ожидание и подготовка первого запуска не являются повторными
поручениями. Старый uncertain journal не разблокирован и не используется для
автоматического продолжения.

Off-VM consumer verification, full backup/native CLI, restore, adoption и release
остаются OPEN. Повтор обычного caller на старом lock запрещён.


## Текущий статус — 10.10.2026

Действующие статусы и условия выпуска ведутся в
[общем плане](../plans/2026-10-03-kubernetes-rollout.md#текущий-статус--10102026).
Ниже — результаты конкретных snapshot и прогонов. Исторические OPEN/PASS/FAIL
относятся к своему checkpoint и не заменяют текущий реестр.

## Полный backup format2 — caller/API 10.10.2026

`backup_stopped` теперь возвращает полный consumer-verified комплект в owning
API fixture. Fixed data/Qdrant/totals streams проходят bounded spool, точные
size/hash и safe tar/snapshot/JSON validators. Revision берётся из confirmed
result. After totals сравниваются с before. Новый `ready` использует те же
producer complete checks без записи `/work/complete`; существующий `finish_job`
остаётся единственным publisher/retirement. Format2/local/non-common сохраняются.

**Windows449 PASS157,92/180с; Linux480 PASS, whole107,47/180с,
unit120с; 0 applicable SKIP.** Source579 seal
`63968a1ddf2d1fea42c5fbc746f4e6688b72d222a82d81129a3a87cc7be2206e`.
Включены затронутые producer generation/closed actions/result tests.
Actual PG archive/independent restore/lost ACK2 PASS3,39с на том же snapshot.
Полный caller возвращает Path/manifest, один completion и DELETE; unknown,
checksum/truncated/extra/unsafe tar/totals drift не дают manifest/replay/unlock.
Late identity/retirement/ENOSPC publication тоже отказывают без manifest.

Owning complete RED сохранён. R1/R2 выявили недостающие identity GET/timeout в
API fixture; R3 дошёл до verified backup, но последующий unlock требует отдельный
PG checkpoint/admission, который этот fixture не принимает. Unlock assertion
удалён из backup-only теста; общий CLI/unlock gate остаётся OPEN, не обойдён.
Identity durability RED выявил отсутствие fsync release.json; Windows первый
фикс дал EBADF на read-only handle. Собственные staged files теперь открываются
с проверкой inode/link, Windows handle writable только для fsync; RED/GREEN1
сохранены. Не расширялись deadlines и не ослаблялись semantic/unknown guards.

Перед последней no-replace публикацией manifest подтверждены retirement,
проверка staged содержимого и flush/fsync всех artifacts. Настоящий Linux/Windows filesystem
путь публикации проверен caller-тестами, а API target/Pod остаются fixture.
Настоящий producer success и Kubernetes backup пока НЕ приняты. Следующая работа —
ранний тот же backup в домашнем owned Pod, затем restore/migrate/stop. Домашняя VM
ответила на readonly SSH preflight; параметры среды здесь не публикуются.

Scope27/patch apply проверены; нормальная очистка Lab и независимое отсутствие
8 собственных resources за0,48/60с подтверждены. Runtime565/main/рабочие установки
не обновлены. Full2б/2в/2г/home lifecycle/release/TLS/replicas остаются OPEN.
Evidence: `compact-delivery/backup-complete-component.json`,
`backup-complete-windows-final.{json,log}`, `backup-complete-linux-r1.{json,log}`,
`backup-complete-existing-pg-windows-r1.log`, `backup-complete-retirement-verification.json`,
`backup-complete-red.log`, `backup-complete-green{-r2,-r3,}.log`,
`backup-identity-{red,green,green-r2}.log`, `backup-complete-scope-green.log`,
`operation-effects582/backup-complete-source.json`.

## Backup integrity/totals/snapshot — продолжение штатного caller 10.10.2026

Три точных команды `backup_stopped` получили common operation поддержку через
существующие journal/target capture и fixed backup producer primitives. Integrity
исполняет прежние capacity+strict integrity, totals пишет проверенный totals_before,
snapshot использует прежнюю сверку server checksum/size. Ни формат2, ни существующий
producer не менялись; новый controller/store/стенд не создавался. Допускается только
порядок confirmed PG export → integrity → totals → snapshot с тем же target.
UID/source/effect/window/target и закрытый operation_result входят в подтверждение;
unknown, неверный результат и drift не разрешают следующий action/start/unlock.

**Windows368 PASS113.72/180с; Linux399 PASS, whole81.78/180с,
unit120с, 0 applicable SKIP.** Source579 seal `d0c7323d7ff18553ee8f349f83081f08f372e5e966c671e707353341f2330a53`.
Caller-test проходит от `backup_stopped` до первой передачи archive: PG/metadata
receipts confirmed; готового manifest нет. Восемь unknown/refusal controls проверены
через существующий exec. Linux реально исполнил wrapper отказа по UID/expiry;
реальный успех этих трёх producers ещё НЕ подтверждён. Прежние actual PG archive2
также прошли на новом snapshot. Kubernetes API остаётся fixture; это не full backup,
не native Job/CLI и не home/release приёмка.

RED9 прежнего отказа и RED3 Windows argv сохранены. Фиксированный producer оказался
слишком большим для Windows CLI: теперь его bytes сжаты, исходный SHA проверяется
перед исполнением; три argv checks проходят. Producer/capacity/size/time guards не
ослаблены. Первый owning GREEN выявил локальную ошибку tuple unpack и был исправлен.
Windows R1 FAILED1/367 PASS был вызван PG-only fixture, ошибочно считавшим metadata
RPC как completion; fixture теперь явно отказывает этому RPC. Ошибка и исходный
snapshot сохранены; focused13 и final368 прошли. Общие сроки не расширялись.

Scope27/diff обновлены один раз; `git apply --check` прошёл. Normal cleanup и
независимое отсутствие собственных ресурсов подтверждены. Runtime565/main/установки
не обновлены, legacy hold/non-common exec сохранены. Далее data/qdrant/totals transfer
и revision/consistency, затем restore/migration/stop и один связанный native путь,
в котором обязательно проверить настоящий producer success.

Evidence: `compact-delivery/backup-metadata-component.json`,
`backup-metadata-windows-final.{json,log}`, `backup-metadata-linux-r1.{json,log}`,
`backup-metadata-existing-pg-windows-r1.log`, `backup-metadata-red.log`,
`backup-metadata-argv-red.log`, `backup-metadata-windows-r1.{json,log}`,
`backup-metadata-scope-green.log`, `backup-metadata-retirement-verification.json`,
`operation-effects582/backup-metadata-source.json`.

## PostgreSQL backup/export — штатный caller 10.10.2026

Существующие `Manager.backup_stopped`/`Manager.exec` получили узкую поддержку
фиксированного pg_dump. Использованы общий effect journal и прежние validators
полного namespace/storage/runtime/service frame; capture переиспользуется с audit
hold. PG Pod имеет собственные StatefulSet/Pod/Node/PVC/PV bindings; toolbox остаётся
зависимостью operation custody, но его Pod permit не переносится на PostgreSQL.
Фиксированный Bash program использует инструменты PG17 и проверяет TCP SQL origin,
создаёт custom archive и проверяет `pg_restore --list`. Python в PG image не нужен.
Формат2 backup и non-common exec сохранены, TLS-format3 ветка не подключалась.

Передача ограничена 1GiB плюс header4096, в пределах прежних action180/owner900.
Публичный header привязан к effect/PG Pod UID/source/target generation и содержит
размер/hash/list verdict. Общий транспорт получил bounded spool; полные size/hash
проверяются до публикации бинарного файла и confirmed result. Intent и реальный
локальный actor сохраняются до dispatch. Raw data/Secrets не попадают в journal.
Malformed/truncated/extra/empty/checksum/target/effect/UID/list отказ, drift после
actor/ответа и lost ACK не подтверждают результат, не позволяют replay/start/unlock.

**Windows356 PASS109.52/180с; Linux385 PASS, whole67.88/180с,
unit120с. Actual PostgreSQL archive/restore/lost ACK2 PASS3,83с.** Один source579 seal
`5ea13d71ed8b8d2d397a079d26e562b083af5038fb925688a7b93350218e607a`. Оба applicable набора без SKIP. API-caller тест дошёл от `backup_stopped`
до подтверждённого dump, затем отказал на ещё неподдержанном integrity; complete
manifest отсутствует. Настоящий fixed Bash program исполнялся в owned Linux PG17;
его результат потребил существующий Manager на Windows с Kubernetes API fixture.
Непустой архив восстановлен в отдельную тестовую БД; контрольные строки совпали,
источник не изменён. Lost ACK после исполнения сохранил prepared и запрет повтора.
Это компонентная приёмка PG export, не настоящий Kubernetes Job/CLI и не общий
coherent backup PG/Qdrant/files.

Причинный RED12 старого exec и RED3 bounded stream сохранены. Первое GREEN выявило
ошибку exact stream selection для вложенных readonly guard GET; после локализации
stream permit ограничен только exact export argv. Actual R1 FAILED на подготовке
SQL: стенд имеет Unix socket в /tmp, adapter исправлен на explicit TCP127.0.0.1.
Продукт при этой правке не менялся; R1 FAILED сохранён, R2 прошёл. Initial Windows356
до adapter fix остаётся историческим; final Windows выполнен на общем seal выше.
Штатная очистка и независимое отсутствие всех собственных R1/R2 resources подтверждены.

Scope27/diff обновлены один раз и `git apply --check` прошёл. Runtime565/main и
рабочие установки не менялись. Legacy hold/non-common exec AST сохранены. Далее
remaining integrity/totals/Qdrant/files/revision, restore/migration, application
retirement и связанный настоящий путь 2б.4; full2б/home/release/TLS/replicas OPEN.

Evidence: `compact-delivery/postgres-export-component.json`,
`postgres-export-windows-final.{json,log}`, `postgres-export-linux-r2.{json,log}`,
`postgres-export-actual-windows-r2.log`, `postgres-export-red.log`,
`postgres-export-stream-red.log`, `postgres-export-observe.log`,
`postgres-export-retirement-verification.json`, `operation-effects582/postgres-export-source.json`.

## Audit hold — PostgreSQL target binding 10.10.2026

Существующий `Manager.exec` теперь связывает fixed hold с PostgreSQL target до
intent, после actor binding и перед confirmed результатом. Повтор неизвестного
результата остаётся запрещён. Использованы прежние storage/runtime/service
validators и namespace consumer classifier. Из stopped storage view исключён
только точно закреплённый owned toolbox; остальные потребители не скрываются.
Проверяются PG StatefulSet/Pod/Node/Lease, PVC/PV, Service/EndpointSlice и runtime;
в argv/journal входят публичные параметры SQL target и хэш поколения, без Secrets.
SQL до UPDATE проверяет фактические адрес/порт/роль/БД и public search path.

**Windows340 PASS112,86/180с; Linux369 PASS65,92/180с, unit budget120с.**
0 применимых SKIP. Один source579 seal
`e87d357497cfd6a8c05e2e5bc447f1cbc98c53a8636d7b278676a99033b80811`.
В Linux — PG12 (в том числе guarded valid/address/user/database) и actual
Manager.exec/PG2; новые API controls покрывают семь неверных target, две смены
после actor/SQL и неверный target ACK. Причинный RED8 сохранён: прежний caller
допускал семь неверных состояний и не записывал target в intent.

Узкий R9 воспроизвёл отказ допустимого SQL origin. Исправлено сравнение IP через
`host(inet_server_addr())`: [PostgreSQL17](https://www.postgresql.org/docs/17/functions-net.html)
подтверждает, что cast inet→text сохраняет netmask. R7 argv preparation failure
и R8 unit timeout остаются FAILED. В R8 восстановлена прежняя настройка private
bytecode cache, сроки/лимиты/проверки не ослаблены. Терминальный unit R8 и его
ресурсы удалены отдельно по точным IDs после проверки; исходный FAILED не переписан.

Один раз обновлены scope27/diff. Семь существующих методов Manager изменены и
добавлен один capture helper; legacy AUDIT_HOLD_SOURCE/non-common exec сохранены.
Независимое наблюдение подтвердило отсутствие ресурсов R10. **Native Kubernetes
Job/caller и полный шаг2б не приняты**, runtime/main/рабочие установки не обновлены.
Далее fixed pg_dump/export в том же backup CLI, остальные SQL/files/Qdrant,
application retirement и один связанный native путь; этот PASS их не заменяет.

Evidence: `compact-delivery/audit-target-component.json`,
`audit-target-final-windows.{json,log}`, `audit-target-final-r10.{json,log}`,
`audit-target-red.log`, `audit-target-live-r8-failed.log`,
`audit-target-r8-retirement.json`, `audit-target-origin-red-r9.json`,
`operation-effects582/audit-target-final-source.json`.

## Audit hold — actual PostgreSQL и существующий caller 10.10.2026

Реальная PostgreSQL проверка добавлена в owning
`backend/tests/test_kubernetes_operation_effect_journal.py`; продуктовый код
не изменён. Временный PostgreSQL17 использует закреплённый образ, внутреннюю
сеть без host-портов, отдельную тестовую БД и томы существующего Lab.

- SQL R4: **8 PASS20,16/180с**, 0 SKIP. Mixed pending→held, absent outbox,
  UID/expiry, неизвестная schema, UPDATE refusal, настоящий COMMIT refusal
  через deferred constraint trigger и отказ роли без UPDATE permission.
  Независимое чтение подтверждает сохранность AuditLog, payload, предыдущих
  held/stdout_written, timestamps и attempt counters.
- Caller R6: **2 PASS14,56/180с**, 0 SKIP. Существующий `Manager.exec` исполняет
  actual fixed SQL program через реальный Python child. Успешный COMMIT ACK
  подтверждается и допускает управляемое завершение; потеря ACK после COMMIT
  оставляет prepared и запрещает replay/completion/unlock. Kubernetes API
  остаётся fixture: эти два случая не объявляются native Kubernetes приёмкой.
- Независимые Docker owner-label наблюдения подтвердили отсутствие контейнеров,
  томов и сетей обоих завершённых запусков; observer0,48/60с.

Текущий source579 seal
`2a00cbf87e32c9e2f03ea1f82f7fc390a902547fc161448cbc97af5d77a00768`;
от прежнего audit protocol изменён только owning test file. Manifest/diff27
актуализированы один раз; старые Windows329/Linux344 сохраняют свои snapshot,
повтор полной матрицы не выполнялся. Подготовительные permission/HBA отказы
и первоначальный перехват SQL-child API fixture сохранены как FAILED; они
не являются причинными RED продуктового кода.

**Не закрыто:** target Service/EndpointSlice, поколение Pod/Node/PVC/PV,
настоящий Kubernetes Job/caller и полный шаг2б. Кандидат не принят в runtime,
готовность release/T3/home/реплик не объявлена. Следующее действие — target
binding того же rollback caller; нового SQL proof вместо него не требуется.

Evidence: `compact-delivery/audit-postgres-verification.json`,
`audit-postgres-live-r4.{json,log}`, `audit-postgres-live-r6.{json,log}`,
`operation-effects582/audit-postgres-source.json`.

## Audit hold — typed protocol и SQL component 10.10.2026

Source579 seal:
`489a9f9627132a915a48e3dfe766497ba631294a4cf21cdd0dcbfb15f4697df5`.
Изменены только existing lifecycle и owning effect tests; diff27 актуален.
**Windows329 PASS100,53/180с**, **Linux344 PASS46,62/180с**,
independent0,80/60с; 0 применимых SKIP. В новых случаях —17 API controls,
1 bound recipe и7 Linux SQL program tests. Три контейнера/два тома удалены,
отсутствие проверено независимо. Старые toolbox311/319 относятся к своему seal.

Common `Manager.exec` распознаёт только существующую fixed audit hold команду
с reason `binary_rollback`/`restored_backup` и без kwargs. Exact owned toolbox
Ready/Job/Pod/Node проверяются до intent и после actor binding; remote program
проверяет Downward UID до доступа к БД. Подготовленный effect id, source и reason
связаны с closed ACK. Программа отдаёт результат после COMMIT и readback pending0;
UTC/monotonic180с ограничены owner900с. Lost/malformed/неверный ACK сохраняет
prepared и запрещает replay/completion/start/unlock. Generic/raw exec остаётся
закрытым; legacy `AUDIT_HOLD_SOURCE` и non-common caller не изменены.

Причинный API RED13/4controls и отсутствующий bound recipe RED сохранены;
linked hold/toolbox38 GREEN14,46с. Linux исполняет саму программу на временной
SQLite: mixed pending→held с причиной binary rollback, absent outbox, UID/expiry,
unknown schema, UPDATE и COMMIT refusal. AuditLog, payload, прежние held/stdout
строки и остальные поля сохранены. Это реальная SQLite transaction проверка,
не PostgreSQL dialect/permissions и не execution внутри настоящего Kubernetes Pod.

**Не закрыто:** существующая обязанность2б.3 — PG generation/service/storage
binding и фактическая PostgreSQL транзакция до принятия этого caller. Disabled
`begin_postgres_checkpoint` не захватывает storage, а enabled snapshot не даёт
общего допуска. Переиспользовать имеющиеся storage/service validators, без нового
controller/store. Затем остаются другие SQL/files/Qdrant callers, application
retirement, native Job, handoff/activation, домашний restore и required release.
Private-кандидат не принят в runtime/main или рабочие установки.

Evidence: `compact-delivery/audit-hold-component.json`,
`audit-hold-affected-windows.{json,log}`, `job-retirement-audit-hold-{red,recipe-red,green}.log`,
`operation-effects582/audit-hold-source.json`, `linux-compact-audit-hold/`.

## Live toolbox — связанная компонентная приёмка 10.10.2026

Source579 seal:
`cc70824257d48c83e596891d7ce2f570cbd776708f6dacf41624a775efc4bd32`.
Изменены только lifecycle и его owning effect tests; diff27/manifest актуализированы.
**Windows311 PASS95,56/180с**, **Linux319 PASS37,25/180с**,
independent0,80/60с; 0 применимых SKIP. Две группы fixed Linux program неприменимы
Windows и не выбираются её runner. В составе —20 toolbox cases и8 Linux program cases.
Три owned контейнера/два тома удалены, отсутствие подтверждено независимо.

Known completion наблюдается без повторного publisher; сохраняются исходные UTC/
monotonic retirement bounds180с, ограниченные lifetime/custody owner. Receipt и
resume проверяются без локального cache. Duplicate/невалидные bounds отказывают.
Unknown/malformed/lost ACK остаётся prepared и запрещает replay/delete/start/unlock.
Живой toolbox не даёт handoff/start/unlock до собственного terminal retirement.

Причинный retry RED3/1control → GREEN15; финальные5 controls проверили closed
bounds и связь с owner. Реальное Linux исполнение дало7 PASS/1 FAIL: marker был
видим до окончания записи. Исправлен только publisher: exclusive private file →
write/fsync → atomic Linux `renameat2(RENAME_NOREPLACE)` → directory fsync.
Общий waiter не изменён; overwrite/partial fallback не добавлен. Unsupported
kernel/filesystem получает отказ. Linux program tests выполняют реальные syscalls
и child Python, меняя только /work на изолированный каталог; это не target Pod proof.
Первоначальный RED20,34/180с и independent absence0,88/60с сохранены.

**Граница:** это компонентный toolbox protocol, не полный2б и не выпуск.
PG StatefulSet caller, SQL/files/Qdrant import, captured `Manager.stop`, genuine
Job CLI, handoff/activation и домашний непустой restore OPEN. Runtime/main и рабочие
установки не обновлены. Следующий caller — binary rollback audit outbox hold;
SQL COMMIT/result нельзя подменить completion marker или exit0.

Evidence: `compact-delivery/live-toolbox-component.json`,
`live-toolbox-affected-windows.{json,log}`, retry RED/GREEN logs,
`operation-effects582/live-toolbox-source.json`,
`linux-live-toolbox-program-red/`, `linux-compact-live-toolbox/`.
Промежуточный checkpoint ниже исторический; его OPEN не переоткрывает завершённый компонент.

## Live toolbox — незавершённый следующий контракт 10.10.2026

Сохранённый `compact-delivery/job-retirement-live-toolbox-green-r2.log` содержит
**11 passed / 377 deselected**, pytest4,85с. Это узкая Windows-проверка waiter,
pinned Pod/Node и typed completion в API fixture с локальным OS child; не
проверка исполнения remote Python в настоящем Pod и не Linux/native приёмка.
Изначальный RED и первый неудачный GREEN сохранены.

Текущие `operation-effects582/source/scripts/kubernetes/lifecycle.py` и owning
effect tests отличаются от terminal-retirement seal681. В `scope.json` ещё
остаются прежние hashes этих двух файлов; он не является manifest текущего
toolbox-кода. Другие 577 исходников совпадают с seal681. Runtime не обновлён.

По чтению `_operation_toolbox_complete`/`finish_job` обнаружен незакрытый retry:
перед проверкой известного completion требуется Running Pod, publisher выбирается
повторно, а deadline вычисляется на новом входе. Сценарий confirmed exec → отказ
retirement → повторный вызов не покрыт сохранёнными11 тестами. Это вывод из кода,
ещё не воспроизведённый RED и не выполненное исправление. Также остаётся проверка
самого fixed publisher/waiter на Linux и штатных exec/import callers.

Предыдущие Windows/Linux291 подтверждают seal681, а не эти новые байты. Не
переименовывать их в актуальный общий PASS. Следующая работа и единый выход:
[конечный остаток 2б](../plans/2026-10-03-kubernetes-rollout.md#конечный-остаток-2б-и-ближайшая-проверка).
Новые продуктовые тесты ради этой проверки плана не запускались.

## Terminal Job retirement — компонентная приёмка 10.10.2026

Source579 seal:
`681dc311cf889e96eee916e59a71adf49c115cef820eeb4a6292637991cece84`.
Scope27/diff обновлены; изменены только existing lifecycle и owning effect tests.
Runtime565/main и рабочие установки не приняли этот private-кандидат.

Job create на предыдущем seal027 дополнительно проверен Linux26936,22/180с,
independent0,97/60с. Текущая реализация terminal retirement имеет **22 PASS8,65с**;
затронутые **Windows291 PASS94,28/180с**, **Linux291 PASS33,12/180с**,
independent0,86/60с. Native tooling/source/crypto guards сохранены; три owned
контейнера и два тома штатно удалены, отсутствие проверено независимо.
Это API adapter + actual OS child tests, не настоящий Kubernetes Batch DELETE. Дополнительно
Windows3 legacy Job callers PASS2,03с: runtime trust, выбранный TLS client и
временный том. Первый запуск1 PASS/2 SKIP из-за Helm вне PATH не засчитан;
с явным проверенным Windows Helm все3 PASS/0 SKIP. Системный PATH не менялся.

`finish_job` до DELETE фиксирует confirmed create, Job UID/RV/hash, Pod UID/spec/
terminal hashes и Node/Lease binding в durable prepared effect. Используются
существующие строгие native Pod/default/terminal и Node/Lease validators.
Только exact выбранный caller допускается к Batch API; Foreground DELETE содержит
server-side UID/RV preconditions. Outcome требует native ACK с UID/RV/generation/
валидным deletion timestamp и две свежие проверки отсутствия captured Job/Pod,
включая Pod с утраченными labels. После confirmed retirement следующий effect и
conditional unlock доступны; unknown ACK, orphan-live/replacement, stale lease,
post-actor drift и conflict оставляют pending/lock и запрещают replay/start/unlock.

Причинный RED8/5 controls → GREEN14; ACK fields3 RED → GREEN17; drift/late RV
контроли → GREEN22. Локальная диагностика обнаружила byte canonical, ошибочно
переданный JSON hasher, и запрет Batch raw endpoint в общем scope guard. Исправлены
точные owning sites и одно selected исключение; generic permit/journal bypass
не добавлялись. Первоначальные FAILED/диагностика сохранены, лимиты не продлены.

**Граница данного seal681:** live toolbox ещё не имел управляемого выхода и
оставался запрещённым для retirement. Состояние следующего незавершённого
toolbox-контракта приведено выше; typed exec/import остаётся обязательным;
PG Pod для pg_dump/pg_restore — отдельный caller с собственным binding. Полный 2б,
native Job path, handoff/activation, домашний непустой restore и release OPEN.
SQL COMMIT и целостность архива не подтверждаются receipt удаления Job.

Evidence: `compact-delivery/job-retirement-component.json`,
`job-retirement-{red,green,green-r2,green-r3,ack-red,green-r4,green-final}.log`,
`job-retirement-diagnostic*.{json,log}`, `job-retirement-affected-windows.{json,log}`,
`operation-effects582/job-retirement-source.json`, Linux `linux-compact-job-retirement/`.
Следующий результат задан [карточкой 2б](../plans/2026-10-03-kubernetes-rollout.md#карточка-2б-результат-вместо-очередного-эксперимента).

## Job create — компонентная проверка 10.10.2026

Source579 seal:
`027db5a966187920718707c02968b020ff7e09530d59716a804e4adb4b68ecdb`.
Проверены фактические candidate hashes diff27 и сохранность runtime; private582
не перенесён в runtime565/main. Это следующий snapshot после Scale, а не новая
сквозная приёмка старого Scale seal.

Создание через `Manager.job` получает typed ACK/UID/template/outcome и два свежих
кадра. Подтверждённый create не означает завершение Job. Pending remote Job
запрещает нового writer/start/unlock; одно последующее GET absence не даёт unlock.
Windows Job **16 PASS, 339 deselected, 6,86с**; затронутые Windows **269 PASS**,
wrapper **90,05/180с**, pytest89,44с, один configuration warning. Проверка исходников
до/после совпала. На этом checkpoint Linux269 был подготовлен; последующий PASS указан выше;
native Job, conditional deletion, captured Pod retirement и typed exec/import OPEN.

Причинный RED10/6 controls сохранён. Первоначальный GREEN8 failures/8 passes
локализован как ошибка API fixture: deepcopy сохранял YAML alias root/Pod labels,
в отличие от JSON wire boundary. Исправлен только fixture; строгий product compare
не ослаблен. Диагностика global trace оказалась затратной; такой метод не является
шаблоном следующего прогона. Первоначальные FAILED не переписаны.

Evidence: `compact-delivery/job-create-red.log`, `job-create-green.log`,
`job-create-green-r2.log`, `job-create-diagnostic.{json,log}`,
`job-affected-windows.{json,log}`; `operation-effects582/job-progress-source.json`.
Результат следующего native сценария должен подтвердить весь owned Job-путь с
настоящими Pods, а не очередной metadata-only create. Приоритет и критерии перехода:
[карточка 2б](../plans/2026-10-03-kubernetes-rollout.md#карточка-2б-результат-вместо-очередного-эксперимента).

## Scale API — компонентная и нативная приёмка 10.10.2026

Scale source579 seal (исторический после изменения Job):
`caa0f8894e5c22ce75bc76da76edd1fa0786e60ee7381537a01ac4fd4a2e2de1`.
Scope27/patch обновлены; runtime565/main не приняли private-кандидат.
Windows40 PASS13,08с; Linux253 PASS27,78/180с, independent0,78/60с.
Вне Scale helpers AST продукта не изменён; это затронутая приёмка, не полный backend/CI.

**Native Scale R6 PASS72,56/180с**, independent1,36/60с: настоящий chart seed,
conditional UID/RV Scale0/1, generation2/3, confirmed journal, next ConfigMap и
conditional unlock. Normal и independent actor/namespace/Pod/PVC/PV/quota absence,
old quarantine и базовое состояние сохранены. Quota0/noPods: фактический выход
старых application Pods, SQL/HTTP/Ready и непустой restore этим не доказаны.

Три конкретных несовпадения устранены без повторной 806/A матрицы:
Deployment18118 bytes требует object inventory, а не lock16KiB;
[ScaleSpec](https://raw.githubusercontent.com/kubernetes/api/master/autoscaling/v1/types.go)
может опустить integer0; после известного ACK Deployment status может изменить RV.
Последний случай допускается только с точным integer observedGeneration и
неизменными UID/generation/spec/template/metadata. Две свежие полные выборки,
conditional request и actual actor terminality сохранены; original ACK RV остаётся
receipt. Это не восстановление unknown ACK и не новый допуск writer по одному GET.

Причинные проверки: inventory1 RED; zero omission1 RED/6 controls → GREEN;
status progress2 RED/5 controls → GREEN. Native Scale R1–R5 FAILED сохранены:
R3 подтвердил размер ответа и достиг ACK, R4 — omitted zero, R5 — только RV drift
при совпадении UID/generation/spec/metadata. Очистка не повышает failed gate.
Evidence: `compact-delivery/scale-native-closure.json`, `scale-component.json`,
`zero-omission-red.log`, `scale-status-red.log`, `operation-effects582/native-scale-r6.json`
и `native-scale-r6-independent.json`, Linux `linux-compact-scale-status/`.

Следующий конкретный дефект воспроизведён через настоящий `Manager.job` с внешним
API adapter: Job создаётся, effect остаётся prepared. TDD artifact
`compact-delivery/next_job_effect_test.py`:1 causal RED/1 lost-ACK control PASS0,74с.
Первая setup failure (неполный backend fixture) сохранена отдельно и не считается
продуктовым дефектом. Job/exec/import, remote retirement, positive handoff,
origin/admission/activation, home restore и release остаются OPEN.

## Компонентная приёмка и native A R7 — 10.10.2026

Исторический snapshot до правок Scale: source579/map840:
`840178341e1d9c4c9704de6a4e1ba3cd63d538be98a22b4b7ccb9da357ea7604`.
11 modules, **806 PASS на каждой Windows/Linux, 0 SKIP**, disjoint299/137/370.
Windows whole160,39/53,36/106,31с; Linux122,77/52,91/65,70с, independent
1,02/1,12/0,91с. Исходные бюджеты сохранены; это не один union806 за180с.

**Native A R7 PASS138,48/180с**, independent1,55/60с: extracted kit/common acquire,
настоящие Helm install/upgrade, confirmed revision1/2, next ConfigMap и conditional
unlock. Valid raw API List RV, durable terminal watch, normal cleanup, independent
actor/namespace/Pod/PVC/PV/quota absence и R5 quarantine baseline подтверждены.
TLS disabled; quota0/noPods: SQL/HTTP/Ready/data restore этим не проверены.
Install/upgrade stdout JSON-only подтверждён; transport отбрасывает stderr, поэтому
пустой placeholder не доказывает отсутствие реального stderr.

Evidence: `operation-effects582/probe-zero-current-component-closure.json`,
`operation-effects582/native-helm-r7.json`,
`operation-effects582/native-helm-r7-independent.json`.
Предыдущие R1/R6 FAILED сохранены. Private582 не принят в runtime565/main;
общие handoff/admission/activation, C/ordinary restore и release OPEN.
Предыдущие checkpoints ниже фиксируют историю; их OPEN806/A больше не актуальны.

## История компонентных snapshot — перенесена 10.10.2026

Ниже сохранена прежняя длинная сводка из общего плана. Это история отдельных
snapshot, а не текущая очередь или совокупная приёмка release. Колонка «Осталось»
отражает момент соответствующего checkpoint; действующие задачи — только в общем плане.

| Область | Подтверждено | Осталось |
|---|---|---|
| Первая compact-поставка | Один Pod backend/frontend, singleton и Recreate; домашние проверки описаны в отчётах | Итоговый release snapshot, интеграция в main и required CI; целевая приёмка проводится в отдельной окруженческой копии |
| Opt-in PostgreSQL TLS / recovery | В Kubernetes worktree принят candidate563 с дополнением access logs (source565); post-adoption базового snapshot241 PASS без skips; рабочие установки не обновлялись | Полный T3, T4–T6, protected origin/current trust, ordinary operator CLI и восстановление |
| Installed Helm candidate562 | Исправлены external CA, API defaults, selector и init image contract; полный настоящий Helm template, owning155 PASS без skips; независимые ревью исправлений без замечаний | Перенос в main и отдельный release gate; компонентные проверки не разрешают restore |
| Windows / Linux кандидата | Windows4061 PASS / 19 SKIP, wrapper114,75 / 180с; Linux4114 PASS / 5 SKIP, wrapper166,94 / 180с; source/tool guards подтверждены | Skips остаются отдельными непроверенными платформенными сценариями |
| Нативная локальная цепочка | R9: установлен общий Helm chart, настоящий factory ACK, stop → archive → reader → start → completed/unlock; wrapper830,53 / 900с; штатная очистка и независимое отсутствие | Protected origin, ordinary CLI и полный T3/T4 этим proof не подтверждены |
| Домашняя цепочка | Тот же candidate562: wrapper688,83 / 900с; сохранены существующие приложения/replicas и PVC/PV; тестовые ресурсы и временный kubeconfig удалены | Model-free proof на малом корпусе; реальные LLM/embedding, production sizing и физическое отключение питания отдельно |
| Полный backend | Базовый frozen snapshot1752:10120 PASS / 34 SKIP, pytest1908,64с, wrapper1918,51 / 7200с; реальный conftest, source/tools неизменны, собственные ресурсы удалены | Coverage≥85%, frontend/Compose, audit/dependency/standalone-consumer и hosted CI остаются отдельными release gates |
| Безопасные access logs | Query/fragment удаляются до обработчиков; Windows26 PASS / 1 Linux SKIP, Linux27 PASS / 0 SKIP; stability10/10, actual WT post-adoption18 PASS; ревью без замечаний | Настоящие локальный R8 и домашний R7 OIDC login подтверждены ниже; прямой home access-log privacy gate, штатный recovery/start и итоговый release gate остаются открытыми |
| Native OIDC / ротация ключа | Native R8 PASS148,70/900 и home R7 PASS699,98/900: все пять OIDC/antirollback assertions, контрольные business/audit/held outbox, штатная очистка; independent home observer3,53/60 подтвердил отсутствие и оба baseline. Прежние native FAILED сохранены | Штатный protected recovery/start, полный PG/Qdrant/files restore, отдельный R1 retirement и release OPEN |
| Генерация нового ключа | Private компонент567: owning57 PASS; Windows/Linux compatibility128 PASS без skips, Linux wrapper11,8/180; независимое ревью без замечаний | Producer не принят в runtime565/main; private storage реализован отдельно в569. Полная публикация Secret, independent promotion и общий start admission — P1–P3 TLS-плана |
| P1 private storage | Кандидат569: durable intent/material в локальном private SQLite; реальные Windows DACL/junction и process-exit tests. Owning40 без skips; Windows205 PASS/1 SKIP, Linux205 PASS/1 SKIP, wrapper23,19/180 и независимое отсутствие ресурсов | Не принят в runtime565/main. Kubernetes first-dispatch ACK и P2/P3 OPEN; отдельный real-tools bootstrap Windows1 PASS/Linux1 PASS20,02/180 закрыл прежний SKIP на том же source569. Private persistence не выдаёт start/writer/unlock |
| P1 controller / CLI | Frozen570r2: existing Manager/public intent/explicit CLI, общий deadline и исходный TTL исправлены. Windows519 PASS/2 native-symlink SKIP150,81/180; Linux520 PASS/1 Windows-only SKIP97,62/180. Linux symlink cases и Windows junction покрыты, real-tools bootstrap пройден; normal cleanup/independent absence подтверждены | Не принят в runtime565/main. Genuine native first-dispatch, Secret ACK/promotion, новый overlay и общий restore/start остаются OPEN; enabled/native9 не закрывает disabled restore |
| P2 публикация Secret / runtime overlay | Typed VSO defect исправлен, frozen571 owning77 PASS. Frozen572: общий private context, confirmed new Secret → actual rotation decision и SQL Job, protected policy через штатный CLI. Owning113: Windows113 PASS119,03/180, Linux113 PASS60,97/180 без skips; independent Linux absence0,77/60 | Runtime565/main не обновлены. 53 modules/1926 cases проверены группами: Windows1924 PASS/2 symlink SKIP, Linux1925 PASS/1 Windows-only SKIP, все Linux cleanup независимо подтверждены; исходные whole timing FAILED сохранены. Application generation, independent promotion, genuine native/new-process CLI и общий enabled/disabled restore/start OPEN |
| P2-A application ref / chart guard | Frozen573: application-only ref/schema/TLS collisions; current owner, два full runtime/render frames и запрет старых values/live recovery через ordinary chart/start. Windows368 PASS94,73/180 + consumer38 PASS; Linux368 PASS39,00/180 + consumer38 PASS22,09/180, без skips; real bootstrap и independent cleanup | Runtime565/main не обновлены. Actual first new-generation dispatch, independent promotion и общий P3 OPEN; decoder/carry574 проверен частично, см. следующую строку; same-ref image rollback unit proof не заменяет native запуск |
| P2-A persistent generation | Private574: public ConfigMap annotation + matching Helm stored values и перенос через existing Manager.chart; Windows owning157 PASS43,90с, wrapper44,51/180, frozen source unchanged | Linux/affected/consumer/native ещё OPEN. Review воспроизвёл отсутствие отказа для mutable Secret, pending/failed Helm и двойной потери metadata; исправлены в574-r2 ниже. First producer/activation и common admission не реализованы; runtime565/main не обновлены |
| P2-A refusal repair574-r2 | Active Secret immutable True, current Helm deployed/exact positive revision; causal53 FAIL/4 controls →214 owning PASS. Affected471+consumer38:509 distinct PASS/platform, без skips; Windows63,19/180 и8,31/180, Linux60,62/180 и23,06/180; normal+independent cleanup и actors0 | Runtime565/main не обновлены. Double metadata loss/fresh/adoption, native general owner/ACK, completed activation receipt, independent origin/common admission и полный restore/release OPEN |
| Common owner575-r2 | Private existing Manager: create ACK/current Namespace/lock UID/RV и conditional unlock. Frozen575-r2 affected564+consumer38:602 distinct PASS/platform без skips; Windows63,86/180 и8,25/180, Linux60,19/180 и22,56/180; independent cleanup и actors0 | Native/new-process OPEN. Receipt пока process-local; durable resume, fresh/adoption, общий admission и completed activation не закрыты; runtime565/main не обновлены |
| Operation journal576-r4 | Private retained state и безопасная диагностика:673 affected+38 consumer,711 distinct PASS/platform без skips. Windows100,20/180+8,75/180; Linux80,02/180+24,78/180. Actual Kubernetes common owner46,81/180; independent absence0,77/60 | Положительный resume прерванной операции, durable parent lifetime, fresh/adoption и общий admission OPEN. Native proof проверял disabled common lock/ConfigMaps, без DB/key/start. Original576-r3 Linux FAIL сохранён; runtime565/main не обновлены |
| Operation lifetime577 | Исходный срок до preflight исправлен: owning153 PASS5,31/180. Selected692+consumer38:730 distinct PASS/platform без skips; Windows94,27/180+10,83/180, Linux75,73/180+18,78/180. Actual extracted CLI/native expiry81,80/180; independent0,91/60 | Linux62 modules/2075 cases:2072 PASS/3 Windows-only SKIP; все7 групп и independent cleanup подтверждены. Windows g4-R2:331 PASS155,17/180; original timing FAILED сохранён. Windows g5:3 fixture WinError1314 FAIL/345 PASS; применимые native-symlink сценарии OPEN. Положительный interrupted resume, own-CAS refresh, fresh/adoption и common admission OPEN; runtime565/main не обновлены |
| Fixture578 — исторический этап до product diff | Только test helper symlink: Windows36 PASS/3 SKIP9,08/180; Linux39 PASS18,31/180, independent cleanup0,80/60 | На этом этапе product bytes совпадали с577; это другой seal, его proof не переносится на финальный578. Применимый Windows native-symlink acceptance OPEN |
| Own-CAS578 — компонент | Actual Manager обновляет receipt только после native lock ACK и отдельного common journal ACK. Causal24:20 FAIL/4 controls →24 PASS; final affected716+consumer38:754 distinct PASS/platform,0 SKIP. Windows84,66/180+8,31/180; Linux69,30/180+17,08/180. Actual extracted native13 actors/5 namespaces120,83/180, independent1,34/60. Final g5: Windows345 PASS/3 SKIP71,89/180; Linux348 PASS46,62/180, independent0,83/60 | Общий механизм проверен; цепочка всех typed recovery writers, положительный handoff и полный caller closure на final seal OPEN. Три применимых Windows symlink cases OPEN. Native proof без PG/key/start/restore; runtime565/main не обновлены |
| Typed lock RV579 — первый writer | Actual acquire + validated enabled materials + version1 ownership decoder/permit/run:2 causal FAIL/7 controls →9 PASS. Хелпер возвращает подтверждённый native ACK с текущим RV. Affected449+consumer38:487 distinct PASS/platform,0 SKIP; Windows108,66/180+12,45/180+7,39/180; Linux57,84/180+20,19/180+17,59/180, все normal/independent cleanup подтверждены | Это компонент первого typed writer, без native579/full claim/admission/start/restore. Другие3 stale-return helpers, полный typed chain и handoff OPEN. Genuine claim нового процесса требует проверяемой передачи common owner; runtime565/main не обновлены |
| OS custody580 — frozen component | Hidden-proc causal3 FAIL/26 controls →29 PASS; scope causal4 FAIL/30 controls →34 PASS. Final Windows34 custody+38 consumer=72 PASS; Linux72 PASS18,31/180,0 SKIP; independent0,81/60 подтвердил absence3 containers/2 volumes. OS live/exited actors проверены; Manager не изменён | Retained journal custody/original cap/every-effect outcomes, concurrent successors и positive native handoff OPEN. Physical hidepid remount/PID reuse не выполнялись; runtime565/main не обновлены |
| Journal custody581 — component/native | Version3 сохраняет actual OS actor и original monotonic cap до lock create, carry CAS/unlock; causal21 FAIL/4 controls →25 GREEN. Final285 PASS/platform,0 SKIP: Windows79,80/180, Linux25,39/180, independent0,84/60. Native13 actors/5 namespaces125,92/180, independent1,42/60; реальные OS PID/start/terminality и очистка подтверждены | Every-effect intent/outcome, captured child/remote actors и положительный interrupted handoff OPEN. Native disabled common ConfigMaps, без PG/key/start/restore; полный caller closure и release OPEN. Runtime565/main не обновлены |
| Every-effect582 — компонент | Current map840:806 PASS/platform,0 SKIP; native A R7 install/upgrade/revision1/2/next/unlock PASS138,48/180; independent1,55/60 | Общий handoff/admission/activation и рабочий restore ещё OPEN; B enabled отдельно. Нет SQL/HTTP/Ready proof в A. Runtime565/main не обновлены; R5 quarantine и FAILED сохранены |
| Домашний стенд | Выделены 8 vCPU / 10 ГиБ RAM / 100 ГиБ disk, автостарт VM включён; сохранены оба kind-кластера. StorageClass использует WaitForFirstConsumer / Delete | До каждого запуска свежие capacity, Node UID/Ready/Lease и baseline; Исторический адаптер: Windows28/Linux28 PASS без skips; named references, native repository spelling и cryptographic OCI index aliases проверены. R5 FAILED до PG/OIDC и отдельно очищен; короткие команды всех пяти образов exit0, их Pod/namespace удалены с отдельным observer. R6 FAILED900,11/900 перед последним probe; отдельный retirement129,08/180 PASS и независимое отсутствие подтверждены. Текущий адаптер: Windows47/Linux47 PASS без skips, Linux12,88/180; actual readonly input proof — два полных кадра, все11 PVC/PV, оба baseline сохранены,11,75/60. Полный home OIDC R7 PASS699,98/900; независимый namespace/11PV/API Pod/CRI observer3,53/60 PASS. R1 retirement остаётся OPEN |
| Исторические отказы | R6/R7/R8 и прежние timing/fixture FAIL сохранены; R9 подтверждает исправленный snapshot | Не переносить успех нового snapshot на старые failed gates |
| Реплики frontend/API/workers | Порядок этапов согласован | После compact-поставки: frontend → durable очередь/владение задачами → независимые API/workers |

Первый compact-запуск с явно отключённым PostgreSQL TLS сохраняется как отдельный
сценарий исходного плана. Это не разрешает включать TLS или снимать recovery guards
до выполнения соответствующих критериев. Последующие исторические сводки и failed
результаты описывают свои snapshot, а не текущий release.

## Helm582 — coalescing/source guards реализованы, 09.10.2026

В existing `_operation_helm_values` integral floats нормализуются до private pinning;
unsupported/non-finite numbers вне диапазона ±9007199254740991 отказывают до intent.
Native/frame/revision/UID/RV types сохраняются. Wrong positive required-null заменён
на optional cpu; required ephemeral-storage null, fractional timeout, duplicate/merge
keys и non-string keys остаются preflight refusals. Source audit закрывает whitespace/
helper lookup, tpl, dependencies/charts/Chart.lock, CRDs и hooks для общего chart.

Actual **20 causal FAIL/36 controls67,97/180 →56 GREEN58,24/180**. SDK fixture делает
один real Helm для двух независимых user/merged views;10-case equivalence3,11с,
без product builder/comparator и ослабления assertions. Windows R2 whole235 timeout180
и late235 PASS190,65с сохранены; R3 owning235 PASS171,70/180 на прежнем map caa78.
Linux R3 setup16 FAIL/219 PASS87,17/180: copytree перенёс read-only права,16 source
variants не дошли до product. Independent retirement0,84/60; fix затронул только
owned temp clone на POSIX, staged source read-only/512Mi/1CPU/no-net сохранены.

Исторический R4 source579 map
`0538eba4d22a28bda26f4290e282e6b05309c8ad8a473662c031247aa8537815`:
actual11 modules/742 collection3,31/30с. **Linux235 PASS89,06/180 и137 PASS41,48/180**,
0 SKIP; independent0,83/0,89/60,3 containers/2 volumes отсутствуют у каждого proof.
**Windows137 PASS47,12/180;235 функционально PASS185,34с, whole180 FAILED**.
PID12452/children исчезли; source unchanged. Этот отказ не заменяется R3 PASS,
late footer или smaller matrix; неизменный expensive rerun требует новой гипотезы.
Full742/current native Helm/остальные effects/handoff/restore/release остаются OPEN.

Evidence: `operation-effects582/{helm-number-boundaries-characterization.json,
helm-values-source-causal-ruling-r2.json,windows-helm-values-source-red-r2.json,
windows-helm-values-source-green-r2.json,helm-sdk-reference-equivalence-r3.json,
windows-helm-values-owning-r2-post-observer.json,windows-helm-values-owning-r3.json,
helm-template-fixture-ruling-r4.json,windows-helm-values-owning-r4.json,
windows-helm-values-owning-r4-post-observer.json,windows-helm-values-callers-r4.json}`,
`linux-helm-values-owning-r3` failed/retirement и R4 owning/callers normal/independent;
`2026-10-09-helm582-values-source-progress/current-collection.json`.
No source freeze/adoption/commit/push; runtime565/main/рабочие установки прежние.

## Native Helm582 — результаты и оставшийся gate, 10.10.2026

Исторический source579 map
`2d57c8f4cbae3c266b58d24a213649a2cc05eb2296530ca90081618fbdd40280`;
текущий map840 и объём повторной приёмки приведены ниже.
Исправлены scoped empty-egress omission (8 FAIL/14 controls →22 GREEN) и exact
get-manifest printer (10 FAIL/14 controls →24 GREEN). ACK+один LF принимается;
empty/double LF/CRLF/trailing bytes отказ. Прежний Helm4 list fix сохранён.

Сохранённая historical compatibility map2d57:11 modules/**776 PASS на каждой Windows/Linux,
0 SKIP**, exact disjoint269/137/370. Windows124,88/46,61/107,48с; Linux106,84/
40,72/52,77с, каждый original180. Windows owning — группы135/134; Linux independent
0,78/0,78/0,95/60 подтвердили cleanup/source/baseline. Private guards34 Linux
14,34/180, independent1,00/60 на тех же helpers закрывают parity с Windows34.
Не заявлены single union776/180, закрытие прежнего235 timing FAIL или release.

| Native / diagnostic | Functional результат | Retirement / наблюдение |
|---|---|---|
| A R1 | FAILED31,41/180, missing extracted chart | Normal cleanup; independent1,30/60 |
| A R2 | FAILED31,58/180, unsupported list flag | Normal cleanup; independent1,58/60 |
| A R3 | FAILED94,55/180, bundled preview | Normal cleanup; independent2,14/60 |
| Preview diagnostic | Diagnostic complete57,23/180; preview FAILED1,72/30, List12/12, omitted empty NetworkPolicy egress | Normal cleanup; independent1,97/60; API/defaults, не CNI packet test |
| A R4 | FAILED139,30/180, exact manifest printer; install JSON ACK получен | Normal cleanup; independent1,48/60 |
| A R5 | FAILED original observation150 с reserve30 внутри whole180; first revision1 confirmed, second preflight deadline до intent | Post readonly0,78/60: original operator/Helm terminal; watch completion=false, scope retained, retirement OPEN |
| A R6, map2d57 | FAILED68,91/180; revision1 confirmed, второй preview отказал до upgrade intent | Raw-RV watch terminal,24 polls/kind; normal cleanup, independent1,67/60; R5 quarantine сохранён |
| Probe-zero dry-run R3, map840 | PASS1,89/30; diagnostic8,45/120; prospective objects без persistent effects | Watch terminal; старые journal/lock/PVC/quota неизменны; не full A/Ready/restore |

R5 не подтвердил upgrade/revision2/next/unlock. В snapshot no Pods/Jobs, quota same
UID/limit0, четыре unbound PVC; это не история изоляции. Original record не содержит
initial list RV; parent daemon watch не завершён доказанно. No replay/start/unlock/
cleanup. Guarded disposition допускает retained quarantine и отдельный fresh proof;
retirement не обязателен перед новым A. Durable watch/trace component выполнен;
current compatibility и полный новый A/B остаются OPEN.
Raw stderr в предыдущем diagnostic transport не наблюдался достоверно: нулевой
размер его placeholder не означает пустой реальный stderr. Upgrade framing OPEN.

Evidence: `operation-effects582/{helm-printer-current-component-closure.json,
windows-network-egress-red/green.json,windows-helm-printer-red/green.json,
native-preview-diag-r1.json,native-preview-diag-r1-independent.json,
native-helm-r4.json,native-helm-r4-independent.json,native-helm-r5.json,
native-helm-r5-post-observer.json}`; current Windows owning/callers/other records,
`linux-helm-printer-{owning,callers,other}` normal/independent records,
`linux-native-lab-guards` normal/independent. Записи сверены при review; сами тесты
в этом documentation review повторно не запускались.

Private582 не принят в runtime565/main. Remaining effects/remote actors/handoff,
protected origin/admission/activation, C nonempty restore/home/ordinary CLI,
full T3/T4/release и реплики OPEN. No freeze/adoption/commit/push.
[Критерии](../plans/2026-10-05-kubernetes-postgres-tls.md#native-helm582-границы-ближайшего-proof--09102026).

### Исторический probe-zero checkpoint до завершения806/A — 10.10.2026

Source579 map
`840178341e1d9c4c9704de6a4e1ba3cd63d538be98a22b4b7ccb9da357ea7604`.
Collection11 modules/806 exact IDs, groups299/137/370. Owning299 PASS/platform:
Windows160,39/180 (150/149 groups), Linux isolated R2 122,77/180 и independent
1,02/60;0 SKIP. Callers137/other370 на обеих ОС ещё OPEN; collection не execution.
Linux R1 FAILED157,14/180 по unit120, counts потеряны; normal cleanup и independent
absence сохранены. Observer старого R1 выполнен после старта R2; late exit0 не PASS.
Изоляция R2 — контрольная гипотеза; успешный контроль не доказывает причину R1.

Actual API опустил шесть health-probe initialDelaySeconds0. Scoped product repair:
16 causal FAIL/22 controls39,59/180 →38 GREEN28,59/180; chart defaults сохранены.
Boolean/null/nonzero/foreign fields/type drift отказывают. Actual prospective
preview R3 PASS1,89/30 без write/replay/start/unlock/cleanup. Setup/compiler ошибки
diagnostic R1 и подготовки Linux R2 сохранены отдельно и не считаются causal RED.
Known pytest warning cache_dir при отключённом cacheprovider сохранён;0 SKIP не
означает warning-free. Native R6 FAILED не переписан успехом dry-run.

Private watch14 FAIL/34 controls →48 GREEN; scoped trace2 FAIL/48 controls →50 GREEN.
Exact50 Windows1,61/180; Linux21,72/180, independent1,02/60 на тех же helper bytes.
Actual readonly parent-exit7,31/60 сохранил incomplete checkpoint и terminal4 actors.
Readonly profile: old trace14,469 против scoped0,828с, те же1302 product events.
Новый watch требует nonempty raw API List RV; generic printer List RV не годится.
R5 history не реконструирована и retirement OPEN; отдельный fresh A сохраняет старый
scope/baseline. No freeze/adoption/commit/push; full T3/T4/release/replicas OPEN.

Evidence: `operation-effects582/{probe-zero-current-collection.json,
windows-probe-zero-red/green.json,windows-probe-zero-owning-dual.json,
native-helm-r6.json,native-helm-r6-independent.json,native-upgrade-preview-diag-r3.json}`;
`linux-probe-zero-owning` failed/late observer и `linux-probe-zero-owning-r2`
normal/independent; `native-watch-r2` и `linux-native-watch-r3` private50 records.
Документальная проверка сверила эти records/source hashes; дополнительно завершён
independent observer уже закончившегося Linux R2. Полный matrix заново не запускался.

## Helm582 — review checkpoint до реализации, 09.10.2026

После внешней fixture/test доработки прежний316 PASS относится к map f313.
Current579 map
`9a48a9cd61284c1f6c3596c13f4ffe8e98553d6f171097b4540b388262b6eb16`:
actual collection11 modules/698 unique, owning191; pytest1,54с, command3,85/30с.
Collection exit0 не подтверждает functional acceptance. Ошибка parser R1 в prefix
исправлена по тому же capture; первоначальный отказ сохранён.

Fixture хранит user/coalesced values через отдельный настоящий pinned Helm SDK,
без product builder/comparator.8 positive controls PASS17,17/180; новый coalescing12
**4 FAIL/8 PASS20,38/180**, diagnostic trace46,06/180. Product bytes не изменены.
Два integral-number отказа локализованы в ACK.config: SDK превращает8.0 в8,
private compiler оставляет float. Два nested-null отказа — actual template preflight,
не причинный дефект coalescing: schema требует ephemeral-storage. Независимый
SDK reference подтверждает совпадение nested-null helper, но не real-chart schema.

Свежий real-chart characterization0,56с: cpu:null разрешён и поле удалено;
ephemeral-storage:null отклонён;8.0 разрешён и рендерится как int;8.5 отклонён.
План разделяет positive optional-null/negative required-null и ограниченную
нормализацию входных integral values, сохраняя boolean/number strict comparisons.
16 template-source controls только подготовлены вне source, ещё не исполнялись.
Current functional Windows/Linux/native Helm и итоговый release остаются OPEN.

Evidence: `operation-effects582/{windows-helm-sdk-fixture-controls.json,
windows-helm-coalescing-red.json,helm-coalescing-characterization.json,
helm-coalescing-trace.json,windows-helm-coalescing-trace.json}` и
`2026-10-09-helm582-coalescing-plan-review/{actual-chart-characterization.json,
current-collection.json}`. Обновлены только планы/отчёт; runtime565/main не принят.

## Helm582 — исправление типов и прежний316, 09.10.2026

В том же mutable582 исправлена исполнимость fault test; исходный collection ERROR
сохранён как setup failure. Actual34:2 FAIL/32 controls48,52/180с — Python принимал
`true` как `1` в stored values. Расширение before/user/all/defaults types дало
**10 causal FAIL/32 controls60,51/180с**. Новый strict recursive comparison сохраняет
JSON types в private before/ACK/post frames;50 positive/refusal GREEN67,30/180.
Runtime immutable Secret True→1 отдельно воспроизвёл **2 FAIL/44 controls58,70/180**;
full captured runtime Secret теперь сравнивается тем же helper. После исправления
**owning179 Windows PASS113,44/180**,0 SKIP; реальные owner/material validators
сохранены, внешние Kubernetes replies заменяются только на API boundary.

Actual AST и collection пересчитаны:11 modules/686 unique3,38/30с. Для type diff
непосредственно затронутые owning179/runtime-generation99/extracted consumer38:
**316 unique PASS/platform,0 SKIP; Windows isolated R2 165,45/180, Linux102,22/180**.
Same579 file hashes/map
`f31374de005dad3273df4771baf9aafa145a5487bbdbf6bceca0e87826962e7e`.
Linux independent0,86/60 подтвердил absence3 owned containers/2 volumes и сохранность
current source/accepted565/native18/tools. Windows cache_dir warning сохранён.

Исходные timing отказы сохранены. Linux full686 R1: functional/counts не подтверждены
в unit120с; actual unit lifetime145,08с, whole157,55/180, затем retirement;
independent0,94/60 подтвердил отсутствие. Exit0 после observation/retirement не
восстанавливает утраченный итоговый счётчик. Windows316 R1: runner observation
 timeout180; после exact PID absence log содержит316 PASS178,76с, но whole gate
остаётся FAILED. Один isolated R2 проверил заранее записанную гипотезу concurrent
Linux/Docker load, сохранив source/tests/budget; R2 PASS165,45/180. Изменённые условия
коррелируют со временем, причинный диагноз старого таймаута этим не установлен.
Full686 Windows/Linux final gate остаётся OPEN,316 не объявляется его заменой.

Evidence: `operation-effects582/{helm-fault-collection-ruling.json,
windows-helm-outcome-fault-red-r2.json,windows-helm-type-boundary-red.json,
helm-type-causal-ruling.json,windows-helm-type-boundary-green.json,
windows-helm-runtime-type-red-r2.json,helm-runtime-type-ruling.json,
windows-helm-owning-green.json,helm-direct-caller-ast.json,helm-current-collection.json,
windows-helm-typed-compat-green.json,windows-helm-typed-compat-post-observer.json,
windows-helm-typed-compat-r2.json}` и `linux-helm-current-r1`/`linux-helm-typed-r2`
normal/independent records. Wrong parameter node selection/setup patch anchor errors
не causal RED; продукт изменён только после actual RED. Все proof handles terminal.
Coalescing/source-chart boundaries, actual native Helm/schema/API defaults, остальные
effects/remote actors/handoff/origin/admission/activation/nonempty restore/home/release
и реплики OPEN. Runtime565/main product/установки не обновлены; freeze/commit/push нет.

## Helm582 — проверка плана перед исправлением, 09.10.2026

В том же mutable582 уже добавлена product implementation common-owned
`Manager.chart`: **8 positive PASS12,89/180с**, source map
`4a72b5700c716bd348ffa45dcaf0fed49e469591b5d2cb9f6efaf6b4f83f5c47`.
Ownership preview без instance label: **6 PASS5,00/180с**, другой map
`277ccc1d64af13c2254e2f1f2e6d4dc36a5f1198b59219c28d1e3e928114d7c4`.
Это сохранённые component результаты с настоящим Helm template и внешним API
adapter; actual native Helm ACK/current affected/consumer ими не закрыты.

Последняя fault-матрица ожидала34 cases, но завершилась **1 collection ERROR,
0 cases**,2,38/180с, exit4: syntax error `__cause__is None` в тесте.
Source map `b8427242e1090626ce42f6111e91336f2fa87b85631a4b6a965c63c7c5e3f92c`.
При проверке плана compile текущего test повторно подтвердил line921.
Ошибка стенда не считается causal RED и не свидетельствует о покрытии отказов.

Статическая проверка выявила Python dict equality в before/post values сравнениях:
boolean и number могут сравниваться равными. Это подтверждённый риск сравнения,
но product causal FAIL ещё не получен: `post-values-type` сначала должен исполниться.
План дополнен compile/collection, strict typed values/coalescing,34 fault cases,
прямым application-runtime-generation caller, exact native Helm schema/API defaults
и аудитом всего поставляемого chart. Literal `lookup ` не доказывает отсутствие
helper/dependency effects. [Обязательный порядок](../plans/2026-10-05-kubernetes-postgres-tls.md#helm582-проверка-плана-и-порядок-закрытия).

Evidence: `operation-effects582/{windows-helm-outcome-green-r3.json,
windows-helm-outcome-green-r3-source.json,windows-helm-preview-owner-green.json,
windows-helm-preview-owner-green-source.json,windows-helm-outcome-fault-red.json,
windows-helm-outcome-fault-red-source.json,windows-helm-outcome-fault-red.log}`.
При этой проверке изменена только документация. Прежние failed records и точные
maps сохранены; runtime565/main product и установки не обновлялись. Every-effect/
remote actors/handoff, origin/admission/activation, непустое restore/home/release
и этапы реплик OPEN; полного завершения цели нет.

## Helm outcome582 — причинный RED, 09.10.2026

Следующий реальный пробел воспроизведён через actual acquire/run/chart и настоящий
общий Helm template: install/upgrade × application disabled/enabled × genuine PG
TLS disabled/enabled. **8 causal FAIL / 4 controls PASS,11,39/180с**: chart возвращает
неподтверждённый native effect вместо semantic helm outcome. Existing controls
сохраняют отказ replay/next/unlock после unknown; product ещё не изменён.

Source map `d20cc780057c2183d9ee2c9e61fbaf0a47c12078adf35253249fdea3540986dd`,579 files.
К pre-Helm529/native preview map отличается только effect-journal test module;
product lifecycle bytes равны. Это RED, не новый compatibility PASS. Первые8 setup
errors (string chart_path) и R2 fixture ошибки (disabled missing desired/Path values/
absent labels) исправлены только на внешней fixture границе; original records/rulings
сохранены, causal R3 имеет8 FAIL/0 ERROR. Реальные owner/material validators не
подменялись; synthetic lab/API replies не заменяют будущий native Helm proof.

Evidence: `operation-effects582/{windows-helm-outcome-red.json,
helm-outcome-red-setup-ruling.json,windows-helm-outcome-red-r2.json,
helm-outcome-red-fixture-r2-ruling.json,windows-helm-outcome-red-r3.json,
windows-helm-outcome-red-controls.json,windows-helm-outcome-red-controls-source.json}`.
На этом историческом RED snapshot GREEN implementation ещё отсутствовала;
новая mutable реализация и её незакрытые gates описаны выше. Actual native Helm
и final affected/consumer остаются OPEN. Полная цель,
restore/home/release/replicas сохраняются; freeze/adoption/commit/push отсутствуют.

## Server preview582 — компонент и пробелы плана, 09.10.2026

В mutable582 реализован bounded scoped server preview; оба штатных TrafficPolicy
preflight callers используют его. Real owner/material validators и actual run
проверены с подменой только внешнего Kubernetes API. Actual extracted native proof
для built-in objects завершён ниже; TrafficPolicy CRD/admission и Helm semantic
outcome ещё OPEN.

Причинные этапы: feature32 FAIL/63 controls →95 GREEN; actual callers4 FAIL/95
controls →99 GREEN; paginated List2 FAIL/119 controls →121 GREEN. Начальный RED
expected count97 оказался ошибкой harness (actual95); отдельный ruling и исходный
record сохранены. Compatibility R1:1 FAIL/528 PASS — patch получил лишний data=None
kwarg; минимальная условная передача data восстановила прежний CLI contract.
Исходный FAIL сохранён, тестовое ожидание no-stdin не ослаблялось.

**Pre-Helm selected10 modules/529 unique PASS/platform,0 SKIP:** Windows153,25/180с,
Linux69,81/180с; collection3,03/30с. Independent0,83/60 подтвердил absence3 owned
containers/2 volumes и сохранность candidate/source565/native18/tools. Source579
files unchanged; map `b863fc954923b7d8a329f380274e815a71ac32ac2d46554f457d675126e201d1`.
Windows warning cache_dir при отключённом cacheprovider сохранён.

Exact command/input/Namespace/original cap, limits30с/128objects/2МиБ, strict JSON
и finally scope cleanup проверены. Preview после unknown mutation не меняет журнал,
не подтверждает запись и не выдаёт start/writer/unlock. Проспективные UID/RV остаются
только наблюдением. Existing permission/discovery/ownership проверки сохраняются.

**Actual extracted kit + Kubernetes API:65,91/180с**,3 terminal OS actors/2 owned
namespaces. Dry-run ConfigMap/Deployment/Service не создал объектов/Pods и не изменил
journal; реальные Deployment/Service defaults получены. Actual ConfigMap create
имеет отдельный confirmed ACK. Failed Helm остаётся prepared/unknown; следующий
effect/unlock запрещён, новый процесс наблюдает preview, но не получает acquire.
Independent1,28/60 подтвердил OS terminality, Namespace/Pod/PV absence и сохранность
Ready Node UID/baseline/source/tools. Это genuine disabled common path, без PG/key/
start/restore. Synthetic lab identity не independent protected origin.
Evidence: `operation-effects582/native-preview.json`, `native-preview-independent.json`
и native stage records; source map совпадает с selected529.

План дополнен actual native preview и конкретным Helm ACK/revision/values/native
outcome, version-specific запретом automatic rollback/cleanup, late/lost ACK и
отдельным journal ACK. Actual CRD/admission/RBAC, live installed SQL/HTTP, остальные
effect classes/remote actors, positive handoff/full final582/origin/admission/
activation/nonempty restore/home/release остаются OPEN. Runtime565/main/рабочие
установки не обновлены; source не заморожен, commit/push не выполнялись.

Evidence: `operation-effects582/{windows-server-preview-red.json,
server-preview-red-count-ruling.json,windows-server-preview-caller-red.json,
windows-server-preview-boundary-red.json,windows-server-preview-boundary-green.json,
windows-server-preview-compat-r1.json,server-preview-compatibility-ruling.json,
windows-server-preview-compat-r2.json,windows-server-preview-compat-r2-source.json,
server-preview-collection.json}` и `linux-server-preview-r1` normal/independent records.

## Every-effect582 — mutable implementation progress, 09.10.2026

Actual child PID/start/scope записан до byte/source stdin. ConfigMap exact native
ACK + два current frames + terminal child → отдельный confirmed journal ACK → next
effect/conditional unlock. Own lock CAS также имеет distinct prepared intent/actor/
outcome и прежний двухступенчатый lock/journal ACK. Generic exit0 не выдаёт authority.

Resource6 causal FAIL/5 controls →11 GREEN; own-CAS3 FAIL/11 controls →14 GREEN;
реальный source stream до actor binding:1 FAIL/17 controls →18 GREEN. Resource R1
1FAIL был ошибкой внешней frame-drift injection, исправлен только adapter.
Исходный owning R1 **20FAIL/183PASS** сохранён: stale version/legacy fixtures,
untyped positive Helm, ранняя injection вместо post-lock-CAS и ожидание отсутствия
нового prepared intent. Коррекция fixtures сохраняет отказ после unknown ACK,
expired cap, stale owner, malformed/oversized result; product после R1 не изменялся.

**Предыдущий diff:203 unique owning PASS/platform,0 SKIP**: Windows62,59/180; Linux22,02/180.
Те же579 source hashes/manifest bytes; actual collection203/2,16с. Independent0,83/60
подтвердил отсутствие3 containers/2 volumes. Unit transport использует настоящий
OS child; Kubernetes responses заменены внешним API adapter. Это не extracted
native Kubernetes582 proof и не полный affected/standalone consumer gate.

Кандидат остаётся mutable. Остальные semantic classes, remote actors, typed read-only
RPC и positive interrupted handoff ещё OPEN; runtime565/main/установки не обновлены.
[Полные контракты582](../plans/2026-10-05-kubernetes-postgres-tls.md#every-effect582-контракты-и-приёмка)
продолжаются в том же кандидате до final freeze, без переноса native581 приёмки.
Evidence: `operation-effects582/{windows-resource-actor-red.json,
windows-resource-actor-green-r2.json,windows-own-cas-ledger-red.json,
windows-own-cas-ledger-green.json,windows-native-stream-red.json,
windows-native-stream-green.json,windows-owning-compat-r1.json,
windows-owning-compat-r2.json,compatibility-ruling.json,owning-collection-r2.json}`
и `linux-current-progress-r1` normal/independent records. Full T3–T6/release OPEN.

## Installed profile582 — исправленный компонент, 09.10.2026

На том же mutable582 исправлен desired-only getter: non-None installed выбирается
первым, некорректный installed не обходится fallback; desired допустим только при
installed=None и точном совпадении fresh validated material identity с checkpoint.
Сохраняются original cap, full frames/Pod, одноразовость, finally cleanup и запрет
writer/start/unlock после unknown effect.

**Причинный RED:6 FAIL/57 controls → owning63 GREEN29,89/180с.** Оба callers:
running SQL и started SQL+HTTP. Второе trust-поколение содержит реально созданные
и валидированные сертификаты/CRL; supplied identity и owner/admission mocks нет.
Matching desired при installed=None проходит; missing/wrong desired и malformed
installed при правильном desired отказывают до exec/intent.

**Selected9 modules/430 unique PASS/platform,0 SKIP:** Windows127,16/180с,
Linux63,61/180с; actual collection2,75/30с. Same579 files/source map
`a354d5118115a60bc1e7e8f0c46045c8c3baf10a8df55bf1d19f27b18b6b835b`.
Independent0,80/60 подтвердил отсутствие3 owned containers/2 volumes и сохранность
source/tools/runtime565/native18. Один warning Windows — существующая pytest
cache_dir option при отключённом cacheprovider; cases PASS, предупреждение сохранено.
Collection R1 ошибся только в parser prefix backend/tests вместо фактического tests/;
pytest собрал430 cases. Исходный log/ruling сохранены, fresh R2 collection PASS.

Evidence: `operation-effects582/{selected-profile-summary.json,
windows-selected-profile-red.json,windows-selected-profile-green.json,
windows-selected-profile-compat.json,windows-selected-profile-compat-source.json,
selected-profile-collection-r2.json,selected-profile-collection-parser-ruling.json}`
и `linux-selected-profile-r1` records. API replies в component tests заменены только
на внешней границе; живой TLS/HTTP probe не выполнен. Прежний418/pre-fix и
atomic225/native9 сохраняют отдельные maps. Остальные effect classes/remote actors,
positive handoff/full caller/native/home/origin/admission/restore/release OPEN.
Runtime565/main/рабочие установки не обновлены; freeze/commit/push не выполнялись.

## Typed selected probes582 — компонент и обнаруженный пробел, 09.10.2026

Исторический pre-fix snapshot418: профильный пробел исправлен в current430 выше;
его старые source map и результаты сохраняются отдельно.

Проверены сохранённые records и все579 current source hashes: matching source map
`f18ec45326dc30b0b195480d1d747a650bb0d204398768ba80485d355e00cb91`.
**9 modules/418 unique PASS/platform,0 SKIP:** Windows156,19/180с,
Linux83,97/180с. Independent Linux observer1,38/60 подтвердил отсутствие трёх
owned containers/двух volumes и сохранность source/tools/runtime565/native18.
Это actual caller/component проверки с настоящими frame/material validators и
внешним API adapter; живой PostgreSQL/HTTP probe здесь не запускался.

Helper этого snapshot имеет process-local одноразовый scope, fixed command/Pod/full frames,
original cap и finally cleanup. Pure probe не публикует effect intent/outcome и
не восстанавливает receipt после unknown mutation. Actual tests проверяют drift,
nested mutation/replay, fabricated context, timeout и expiry. Прежний native resource9
проверяет atomic ConfigMaps на своём source map, не является native proof нового diff.

**Пробел найден по коду при проверке этого pre-fix snapshot:**
`_operation_selected_probe_take` выбирает только desired;
`begin_backup_installed_operation` задаёт installed/desired=None. Installed-only backup
и различающиеся trust-поколения этой матрицей не проверены. Причинный RED ещё предстоит;
418 PASS сохраняется, но не объявляется closure штатного installed-only пути.
В план первым шагом добавлены RED/GREEN для installed-only, installed + другого desired,
malformed installed без fallback, оба штатных callers и живой final native proof.

Evidence: `operation-effects582/{selected-probe-collection.json,
windows-selected-probe-compat.json,windows-selected-probe-compat-source.json}` и
`linux-selected-probe-r1/{linux-r1-compat.json,linux-r1-independent.json}`.
При этой проверке изменена только документация; runtime code и инфраструктура не
менялись, новых pytest/native/home запусков не было.582 остаётся mutable;
full effect classes/remote actors/handoff/origin/admission/restore/release OPEN.

## Atomic ConfigMap582 — текущий component/native, 09.10.2026

Причинный RED воспроизвёл перезапись конкурентного foreign target после последнего
GET. Actual Manager теперь направляет только validated ConfigMap resource в
create-only либо conditional replace captured UID/RV; prepared context фиксирует
native args. Actor binding/late recheck до stdin, original cap и ACK guards сохранены.
Неизвестный результат/Conflict блокирует повтор, следующий effect и unlock.

R2:1 FAIL/39 controls →40 GREEN19,78/180. R1:3 FAIL/37 PASS был ошибкой ранней
fault injection, исправленной только по exact request metadata.name. Оба original
FAIL сохранены. **Owning225 PASS/platform,0 SKIP:** Windows81,17/180,
Linux24,38/180; identical579 hashes/manifest bytes. Linux independent1,00/60:
3 containers/2 volumes отсутствуют, source/tools/runtime565/native18 сохранены.

Actual extracted operator/native Kubernetes: **5 scenarios/9 terminal OS actors,
165,98/180**; positive create/update/next/unlock и late/absence/RV/UID races.
Четыре новых процесса после отказа не получают effect permission.
Штатная conditional cleanup5 namespaces; independent1,75/60 подтвердил OS terminality,
Namespace/Pod/PV absence, Node Ready и baseline. Lab identity не protected origin;
native common disabled ConfigMaps не дают DB/key/start/restore admission.

Evidence: `operation-effects582/{resource-race-summary.json,
resource-race-injection-ruling.json,windows-resource-race-red-r2.json,
windows-resource-race-green.json,windows-resource-compat-green.json,
windows-resource-compat-green-source.json,native-resource.json,
native-resource-independent.json}` и `linux-resource-race-r1` records.
582 остаётся mutable; typed read-only actual caller, остальные effect classes,
remote actors, positive handoff/full affected/consumer и T3–T6/release OPEN.
Runtime565/main/рабочие установки не обновлены.

## Проверка плана582 после pre-intent/target diff — 09.10.2026

Сверены actual private Manager/helpers, test names и сохранённые RED/GREEN records;
новый runtime или infrastructure proof при этой проверке не запускался.
Causal16 FAIL/19 controls → Windows35 PASS без skips, wrapper15,39/180с,
pytest13,38с. Все579 файлов соответствуют последнему Windows source map.
Отказ некорректных args/request/foreign target выполняется до intent/spawn;
positive controls проверяют исправленный запрос и owned current UID/RV apply.
Linux этого diff, full owning/affected/consumer и extracted native582 OPEN.
Прежний203 PASS/platform относится к предыдущим bytes и не является текущим gate.

В план добавлены конкретные недостающие критерии: late target drift после actor
binding; API-side create-only/UID/RV conditions после последнего GET; одноразовый
read-only permit в actual selected-probe caller; запрет reuse/redirect/drift;
отдельный server dry-run без выдачи persisted outcome. Реализация late target
recheck уже есть, но причинная race/native проверка ещё предстоит. Статический
program match или mocked `Manager.kube` не подтверждает безопасность actual run.
Новые критерии остаются unchecked; эта сверка не исправляет runtime code.

Evidence: `operation-effects582/{windows-preintent-owned-red.json,
windows-preintent-owned-green-r1.json,windows-preintent-owned-green-r1-source.json}`.
Исходные FAIL и deadlines сохранены; runtime565/main/установки не обновлены.
Порядок: закрыть582 на final bytes → protected origin/common admission/activation →
непустой PG/Qdrant/files restore → native/home и release gates. Свежий capacity
замер домашней VM остаётся обязательным перед следующим тяжёлым прогоном.

## Journal custody581 — native/component, 09.10.2026

Actual retained journal version3 сохраняет OS custody и original monotonic cap
до создания lock. Current owner check сверяет actual OS actor/cap; CAS/unlock
сохраняют запись. Legacy1/2 decode/read-only controls прошли; новой takeover
authority не выдано, прошлые effect outcomes не считаются подтверждёнными.

Causal21 FAIL/4 controls →25 PASS; final owning28 — subset union.
**Windows285 PASS79,80/180; Linux285 PASS25,39/180,0 SKIP** на exact same578
source file maps. Independent0,84/60 подтвердил отсутствие3 containers/2 volumes.
Original compatibility10 FAIL/175 PASS сохранён; исправлены только schema version
expectations и legacy fixture с лишним custody. CRLF/LF manifest serialization
различается; actual source hashes/maps равны, original bytes retained.

Actual extracted bootstrap + native Kubernetes: **13 terminal actors/5 namespaces,
125,92/180**, independent1,42/60. Проверены custody carry, conditional unlock,
lost lock/common ACK refusal и readonly CLI original expiry. Каждый base-Python
actor имеет captured PID/start и OS terminal observation до cleanup. Namespace
baseline/Ready Node UID/source580/accepted565/native18 сохранены.

Граница: disabled common ConfigMaps; нет DB/key/start/restore или positive
interrupted handoff. Every-effect intent/outcome и child/remote actor custody —
следующий обязательный deliverable. Full caller/backend/CI/home/release OPEN;
runtime565/main code/рабочие установки не обновлены. Native lab identity — transport
proof, не independently protected release origin.
Evidence: `operation-custody-journal581/{candidate-source581.json,freeze.json,
compatibility-summary.json,final-collection.json,source-map-equivalence.json,
compatibility-fixture-ruling.json,windows-owning-consumer-final.json,native-owner.json,
native-owner-independent.json}` и Linux normal/independent records.

## OS custody580 — frozen component, 09.10.2026

Подтверждённые causal failures исправлены: hidden-proc3 FAIL/26 controls →29 PASS;
user/eUID/procfs PID-view4 FAIL/30 controls →34 PASS. ENOENT не означает absence
до kernel signal0 ESRCH; EPERM/live/unknown остаются отказом. Scope связывает
boot/PID/time/user namespaces/effective UID и совпадение procfs/kernel PID-view.

Final577 source paths: **Windows34 custody+38 consumer =72 PASS,0 SKIP**,
wrappers3,34/180 и10,12/180; **Linux72 PASS18,31/180,0 SKIP** на тех же bytes.
Normal cleanup; independent0,81/60 подтвердил absence3 own containers/2 volumes.
Actual OS live/exited actors проверены; hidden-proc/UID/namespace faults — OS-boundary
tests, без physical hidepid remount или фактического PID reuse. Source578/579,
accepted565/native18/tools сохранены. Original RED/fixture/preflight ошибки удержаны.

Граница: read-only helper; Manager bytes unchanged, product custody callers отсутствуют.
Нет durable journal custody/effect outcomes или положительного native handoff.
Runtime565/main/рабочие установки не обновлены; общий T3/restore/home/release OPEN.
Evidence: `operation-custody580/{candidate-source580.json,freeze.json,
compatibility-summary.json,windows-procfs-red.json,windows-procfs-green.json,
windows-scope-red.json,windows-custody-final.json,windows-consumer-final-r2.json}`,
`linux-custody-consumer/{linux-r1-compat.json,linux-r1-independent.json}`.

## Проверка плана custody580 — история pre-fix, 09.10.2026

Проверены actual private helper/Manager и сохранённый Windows record580:
25 PASS, wrapper3,92/180с,0 SKIP. Исторические RED/launcher fixture errors сохранены.
Snapshot580 mutable; Linux/final seal/native handoff не подтверждены. PID-reuse
control меняет saved start и проверяет модель, фактический PID reuse не воспроизводился.

Выявлен дефект candidate580: ENOENT `/proc/PID/stat` принимается за отсутствие,
хотя procfs может скрывать живой PID. В план добавлены отказные tests, проверка
Linux user/clock/PID scope, versioned journal custody и outcome каждого effect,
actual child ownership и гонка двух successors. Исправление кода ещё предстоит.
`recover_lock` сохраняет unlock-семантику; отдельный explicit resume требует
native transfer ACK и journal ACK до нового receipt. Общий admission остаётся отдельным.
[Исправленный план и порядок проверки](../plans/2026-10-05-kubernetes-postgres-tls.md#p3-o-общий-владелец-и-аварийное-продолжение--09102026).
Evidence: `operation-custody580/{windows-custody-green-r2.json,red-observation.json,
actor-launch-ruling.json,handoff-design.md}` и проверенный actual source.
Эта сверка меняет документацию; runtime565/main code/рабочие установки не обновлены.

## Own-CAS578 — подтверждённый компонент, 09.10.2026

Final578 меняет common CAS в existing Manager; ранняя fixture578 была отдельным
этапом с product bytes577. Causal24:20 FAIL/4 controls →24 PASS. Owning177 входит
в affected716. Final affected716+consumer38: **754 distinct PASS/platform,0 SKIP**;
Windows84,66/180+8,31/180; Linux69,30/180+17,08/180, independent0,83/60+0,91/60.
Final g5: **Windows345 PASS/3 SKIP71,89/180; Linux348 PASS46,62/180**,
independent absence0,83/60. Три применимых Windows symlink cases остаются OPEN;
original fixture/timing/collection failures сохранены.

Actual extracted operator + Kubernetes API: **13 terminal actors/5 namespaces,
120,83/180**, independent1,34/60. Собственный lock CAS и отдельный common state ACK
с повторными frames разрешают scoped effect и conditional unlock. Контролируемая
потеря lock/common ACK блокирует next effect/replay в том же и новом процессе.
Исходный lifetime, baseline и Ready Node UID сохранены; normal cleanup и отсутствие
namespace/Pods/PV подтверждены. Lab identity доказывает транспорт, не protected origin.

Граница: native common disabled ConfigMaps; enabled component валидирует реальные
сертификаты, но не всю typed recovery цепочку. Positive interrupted handoff,
сквозные typed writers/size/RV, final whole caller/backend/CI, full restore/home и
release OPEN. Runtime565/main/рабочие установки не обновлены.
[Следующие критерии](../plans/2026-10-05-kubernetes-postgres-tls.md#own-cas578--компонент-и-границы-приёмки-09102026).
Evidence: `operation-cas578/{candidate-source578.json,stage578-sources.json,
compatibility-summary.json,own-cas-writer-inventory.json,windows-affected-final.json,
windows-consumer-final.json,windows-callers-g5-final.json,native-owner.json,
native-owner-independent.json}` и `cas-linux-affected`/`cas-linux-consumer`/
`cas-linux-callers-g5` normal/independent records. Исторический pending в freeze
расширенной unlock assertion закрыт последующими final affected/native proof.

## Typed lock RV579 — компонент первого writer, 09.10.2026

Actual Manager/validated enabled material/version1 ownership decoder:
**2 causal FAIL/7 controls →9 PASS**. Исправлен возврат старого resourceVersion:
хелпер отдаёт проверенный native ACK, следующий exact CAS использует текущий RV.
Unknown/oversize ACK после commit сохраняет lock и блокирует replay; oversized
request отклонён до dispatch, permit очищается. DB/key/start authority не выдаётся.

На одном frozen579 **487 distinct PASS/platform,0 SKIP**: affected449+consumer38.
Windows108,66/180+12,45/180+7,39/180; Linux57,84/180+20,19/180+17,59/180.
Independent0,86/60+0,80/60+0,83/60 подтвердили отсутствие9 own containers/6 volumes;
source578/accepted565/native18/tools сохранены. Owning9 — подмножество union.
Первоначальная ошибка collection path verifier сохранена отдельно от успешного
pytest collection487 exit0; path-only parser repair не менял тесты/parameter IDs.

Граница: первый typed helper, без full claim CLI/native579/DB/key/start/restore.
Другие3 stale-return helpers и полный typed chain OPEN. Claim нового процесса
зависит от P3-O verified handoff и общего admission; occupied lock не равен fresh.
Runtime565/main/рабочие установки не обновлены; full goal остаётся активной.
[Критерии продолжения](../plans/2026-10-05-kubernetes-postgres-tls.md#typed-lock-rv579--первый-writer-09102026).
Evidence: `operation-typed579/{candidate-source579.json,freeze.json,
compatibility-summary.json,affected-collection.json,windows-typed-red.json,
windows-typed-green.json,collection-verifier-history.json,typed-entrypoint-dependency.json}`
и Windows affected/consumer, Linux normal/independent records.

## Исходный срок577 — история component proof и широкой регрессии, 09.10.2026

Исходный срок захватывается до preflight; shorter parent не меняет issued_at и
не продлевает expiry. Causal RED2 → owning153 PASS5,31/180. Frozen577 selected692 +
consumer38 = **730 distinct PASS/platform без skips**; Windows94,27/180+10,83/180,
Linux75,73/180+18,78/180. Exact730 node IDs/14 modules собраны на том же source.
Оба Linux стенда штатно удалены; independent absence1,08/60 каждого подтверждён.

Actual native/extracted CLI **81,80/180**: positive effect до original25с,
refusal effect/unlock после истечения, неизменные journal/lock/marker и original
expiry в новом readonly CLI процессе. Все9 actors terminal;3 owned namespace
условно удалены, baseline/Node UID сохранены; independent absence0,91/60 PASS.
ACK-loss и next operation after completed проверены отдельно. Это common journal
proof, не positive interrupted resume, DB/key/start/restore или protected origin.

Linux62 modules/2075 cases:2072 PASS/3 Windows-only SKIP; все7 групп и independent cleanup подтверждены. Windows g4-R2:331 PASS155,17/180; original timing FAILED сохранён. Windows g5:3 fixture WinError1314 FAIL/345 PASS; применимые native-symlink сценарии OPEN.
Исторический test-only этап578: Windows36 PASS/3 SKIP9,08/180; Linux39 PASS18,31/180 с настоящими
symlinks, normal cleanup/independent absence0,80/60. Product bytes совпадают с577;
только WinError1314 помечен SKIP. На том этапе весь g5 на578 был OPEN;
последующий final578 приведён выше. Windows native-symlink acceptance OPEN. Контрольный g4-R2 не устанавливает причину прежнего замедления и не меняет
original FAILED. Поздний log первого g4 не даёт captured exit code. Selected730
не является всем scope; на том этапе final affected/consumer после own-CAS ещё OPEN.
Последующий компонент578 не закрывает полные P3-O/admission/activation/home/T3/T4/release; они остаются
OPEN; runtime565/main/рабочие установки не обновлены.
[Действующие критерии](../plans/2026-10-05-kubernetes-postgres-tls.md#исходный-срок577--selected-regression-и-native-expiry-09102026).
Evidence: `operation-lifetime577/{final-freeze.json,coverage-collection.json,
compatibility-summary.json,native-owner.json,native-owner-independent.json,
constructor-caller-review.json,caller-collection.json,windows-g4-r2-hypothesis.json,
windows-callers-g4-r2.json,windows-callers-g4-terminal-observation.json,
windows-callers-g5.json}` и caller Linux normal/independent records.
Fixture: `operation-cas578/windows-recovery-package-fixture.json`,
`fixture-linux/linux-r1-compat.json`, `fixture-linux/linux-r1-independent.json`.

## Проверка плана577 — история до ремонта, 09.10.2026

Review повторил preflight дефект actual Manager: **2 FAIL, whole2,47/180**.
После901с preflight acquire переиздал900с; после5с изменил original issued_at.
На момент этого review фикс ещё предстоял; subsequent repair описан выше. Сохранение PostgreSQL `phase`/`migration` в actual CLI с
вложенным `common` отдельно проверено: **1 PASS, whole2,92/180**.
Это узкая историческая проверка: на тот момент final577 owning/affected
Windows/Linux и native/new-process ещё OPEN. Subsequent PASS приведены выше;
home/full restore/release остаются OPEN. Runtime565/main и рабочие установки не обновлялись.

В плане добавлены проверяемые условия: захват срока до preflight, immutable
published lifetime, version1 observe-only, read-only status после expiry,
переносимость часов и явный observe/repair при истечении. Source565 и frozen576-r4
сохраняются. Планируемый union730 подтвердить final collection; прежний711 PASS
относится только к576-r4. Исторические отказы не переписываются.
[Действующие критерии](../plans/2026-10-05-kubernetes-postgres-tls.md#проверка-плана577--исходный-срок-и-совместимость-cli-09102026).
Evidence: `operation-lifetime577/{windows-plan-review-parent-red.json,
windows-plan-review-parent-red.log,windows-plan-review-status-green.json}`.

## Common journal576-r4 — завершённая затронутая проверка, 09.10.2026

Final frozen576-r4: affected673+standalone consumer38 — **711 distinct PASS/platform,
0 SKIP**. Windows100,20/180+8,75/180; Linux80,02/180+24,78/180. Exact711 node IDs и
14 modules подтверждены collection на той же ревизии. Linux normal cleanup и
independent3-container/2-volume absence0,98/60+0,95/60; source565/native18/tools сохранены.

Исправлены три diagnostic отказа; legacy2 без common owner receipt по-прежнему
не может unlock. 576-r3 causal3 FAIL/65 controls →closure68 PASS и owning135 PASS.
Его Linux1 FAIL/672 PASS77,86/180 был fixture PermissionError: copytree унаследовал
sealed chart0444. Independent failure observer0,80/60 подтвердил отсутствие ресурсов;
original FAILED сохранён. 576-r4 меняет только права собственного temporary Chart.yaml
перед намеренной порчей, продукт byte-identical576-r3, исходные guards не ослаблены.

Actual Kubernetes common journal proof46,81/180: настоящий create ACK, scoped effect,
conditional delete ACK/absence/completed, затем новая операция в отдельном OS process.
После контролируемой потери ответа на boundary реального create/delete следующий
процесс отказал без мутации journal/lock. Все6 processes terminal; owned namespaces
условно удалены, independent API absence0,77/60 подтвердил namespace/Pods/PV и прежний
Ready Node UID. Это disabled common owner proof с ConfigMaps, не PG/key/start/restore.

На момент576-r4 положительный resume прерванной операции, исходный expiry после
перезапуска и own-CAS receipt были OPEN; последующие577/578 proofs описаны выше.
Fresh/adoption, protected origin/common admission, first activation и полный
T3/T4/release остаются OPEN. Runtime565/main
и рабочие установки не обновлены. Общая цель остаётся активной.
[Следующие критерии P3-O](../plans/2026-10-05-kubernetes-postgres-tls.md#p3-o-общий-владелец-и-аварийное-продолжение--09102026).

Evidence: `operation-journal576-r4/{candidate-source576-r4.json,coverage-collection.json,
compatibility-summary.json,windows-affected.json,windows-release.json,
native-owner.json,native-owner-independent.json}` и оба Linux normal/independent records.

## Полнота затронутой регрессии576 — review 09.10.2026

Проверка прямых acquire/unlock callers нашла два пропущенных модуля:
`test_kubernetes_postgres_tls_checkpoint.py` и
`test_kubernetes_backup_standalone_unlock_observation.py` (68 cases).
На575-r2:12 FAIL/56 PASS,16,41/180; на576-r2:16 FAIL/52 PASS,16,55/180.
Поэтому ранее подтверждённые602 cases/platform на575-r2 и643 на576-r2 относятся
только к своим основным наборам, а не полной совместимости всех caller contracts.

Private576-r3 меняет только fixtures: actual Manager/native transport и настоящий
Helm вместо неполного API/высокоуровневых mocks. Windows64 PASS/4 FAIL,18,83/180.
Три оставшихся отказа — ранний common owner guard, скрывший причины TLS/backup3;
четвёртый — legacy2 test с разрешением unlock без common owner. Продукт в576-r3
при этой проверке совпадает с576-r2. Полный новый GREEN, Linux/consumer576-r3,
positive new-OS-process resume и native journal ACK proof отсутствуют; gates OPEN.
Исходные FAILED/логи сохранены, runtime565/main и установки не обновлены.

В план добавлены owning135/closure68 → union673+consumer38 на одном final snapshot,
actual caller/node-ID coverage, безопасная диагностика, явный legacy2 contract и
положительная передача ownership новому процессу. Дополнены parent expiry,
retention/потеря state, запрет замены target journal из старого backup и namespace
scope. Это критерии будущей реализации; плановые711 cases/platform не объявлены PASS.
[Критерии следующего результата](../plans/2026-10-05-kubernetes-postgres-tls.md#проверка-полноты-p3-o--checkpoint576-09102026).

Evidence: `operation-owner575-r2/windows-closure.json`,
`operation-journal576-r2/{windows-affected.json,windows-closure.json,windows-release.json}`,
его Linux records/independent observers и
`operation-journal576-r3/{candidate-source576-r3-fixture.json,windows-closure-fixture.json}`.
В этой проверке изменены только два плана и этот отчёт; тесты продукта заново не
запускались. Сторонние правки и неизменяемые private snapshots сохранены.

## Компонент567 и уточнение очереди — 09.10.2026

Домашний OIDC R7 завершён PASS699,98/900; independent observer3,53/60 подтвердил
отсутствие собственных ресурсов/процессов и оба baseline. Предыдущие записи
о предстоящем home proof относятся к своим checkpoint; R1 retirement остаётся OPEN.

Новый private component567 добавляет только producer и57 owning tests; прежние565
файлов byte-identical. Сохранённый causal RED57 FAIL → owning57 PASS; compatibility
Windows128 PASS1,90с / Linux128 PASS6,04с без skips, Linux wrapper11,8/180.
Normal cleanup и отдельная container/volume absence проверка подтверждены в record.
Первоначальные Windows setup ERROR и Linux R1 exit4/no tests run сохранены отдельно;
они не считаются causal RED или успешной Linux приёмкой.

Independent read-only review:0 Critical/Important/Minor, компонент пригоден для
интеграции. Reviewer проверил exact567 manifest delta, API/validators и сохранённые
logs; tests и инфраструктуру повторно не запускал. Source checksum не аттестует
interpreter/dependencies, supplied material SHA не подтверждает независимость
promotion, immutable Secret object не доказывает API-origin внутри pure helper.

Компонент возвращает private bytes, не пишет файл/Secret/SQL и не выдаёт start/
writer/unlock. Accepted runtime остаётся565; main и рабочие установки не обновлены.
Следующий deliverable — [P1–P3 общего recovery/start](../plans/2026-10-05-kubernetes-postgres-tls.md#долговечная-публикация-ключа-и-допуск-первого-старта).
Durable publication, protected promotion, ordinary CLI, full restore и release OPEN.
Private evidence: `protected-key-publisher566/candidate-source567.json`,
`windows-red2.log`, `windows-green57.log`, `windows-compat.log`, `linux-r2.log`,
`linux-r2.json`, `linux-r2-independent.json`, `review.md`.

## P1 private key persistence — checkpoint569, 09.10.2026

Frozen candidate569 добавляет `session_key_storage.py` и owning tests к567;
единственный изменённый baseline файл — current operator bootstrap allowlist.
Intent до RNG и private material после него сохраняются в локальном SQLite
DELETE/EXTRA; existing incomplete/unknown output никогда не вызывает RNG повторно.
Два настоящих child process exit проверили intent refusal и ready observation.
Windows проверены реальные private DACL, их расширение Everyone, junction и hardlink;
Linux — permissions, symlink/hardlink и process-exit на том же frozen source.

Causal RED29 → GREEN29; strict numeric/monotonic lifetime RED5 FAIL/32 PASS →
GREEN37; operator archive transport RED1 FAIL/39 PASS → owning40 PASS.
Compatibility Windows205 PASS/1 SKIP10,20с; Linux205 PASS/1 SKIP15,54с,
wrapper23,19/180. Inherited pytest cache_dir warning сохранён. Первоначальный setup
отказ на неверном working directory/no tests run сохранён и не считается causal RED.

Linux normal cleanup и отдельный fresh observer подтвердили отсутствие двух owned
контейнеров по name/ID и одного volume; guards569/accepted565/native18 PASS.
После обнаружения прежних tools тот же standalone bootstrap case закрыт отдельно:
Windows1 PASS0,52с; Linux1 PASS на source569, wrapper20,02/180, normal cleanup
и независимое отсутствие трёх контейнеров/двух volumes. Tools bytes и source guards
неизменны; первоначальный SKIP сохранён. Итоговая release consumer/hosted CI
приёмка этим не закрыта. Physical
power-cut, filesystem/CSI, Kubernetes first-dispatch permission, independent
promotion, общий restore/start и full T3/T4/release не проверены этим компонентом.
Whole-branch review ещё OPEN. Runtime565/main/operator installation не обновлены.

Private evidence: `protected-key-storage568/candidate-source569.json`,
`windows-red2.log`, `windows-regression-red.log`, `windows-green37.log`,
`windows-archive-red.log`, `windows-compat.log`, `linux-r1.log`, `linux-r1.json`,
`linux-r1-independent.json`. Следующий эффект — только после confirmed controller
intent ACK и свежих recovery/current-state checks; `create=True` не является ACK.

## P2 execution: frozen572 publication/runtime overlay — 09.10.2026

Typed VSO causal1 FAIL/7 PASS →15 affected PASS; full owning57177 PASS65,47с.
Frozen572 добавляет private overlay file и переиспользует full publication context
для actual rotation inspector/decision и SQL runtime ref, без Secret retry,
PG/PVC/VSO changes или startup permission. Owning overlay13 FAIL/1 PASS →14 PASS;
CLI16 FAIL →15 PASS/1 fixture owner-binding FAIL →30 PASS; invalid parent3 FAIL/3
PASS →owning36 PASS38,62с. Actual CLI → controller unit использует API fixture,
а не MockManager; genuine native process/independent publisher proof ещё OPEN.

Общий113-case publication/overlay на572: Windows113 PASS117,05с,
wrapper119,03/180; Linux113 PASS, wrapper60,97/180 без skips. Штатная очистка и
independent отсутствие3 containers/2 volumes0,77/60. Источники572/571/570r2,
accepted565/native18/tools не изменены; runtime/main не обновлены.

Первоначальный53-module1926-case run FAILED по времени: Windows180,16/180
(timeout124); Linux child120 истёк, wrapper167,27/180, unit оставался живым.
После наблюдения того же captured unit: terminal exit0,1925 PASS/1 Windows-only
junction SKIP325,69с. Это functional result, не bounded acceptance. Original FAILED
не переписан. Отдельный обычный retirement1,33/60, независимое отсутствие3 containers/
2 volumes0,74/60; Windows candidate Python actors0. Hypothesis: суммарная работа
full-frame fixtures не помещается в один unit budget; fresh cache718 pyc существует,
отсутствие cache не установлено. Disjoint groups покрывают все53 modules/1926cases
на572 без исключения tests и увеличения лимита каждого прогона; общий исходный
timing gate остаётся FAILED. Все disjoint groups теперь завершены: Windows1924
PASS/2 native-symlink SKIP, Linux1925 PASS/1 Windows-only junction SKIP.
Windows baseline150,42/180; publication119,03/180; SQL245 cases145,62/180; schema227
cases64,11/180; purge128 controllers81,39/180 и254 contracts38,38/180; absence164
controllers100,94/180 и274 contracts10,24/180. Windows SQL/schema472 original
group FAILED180,16/180 сохранён отдельно; scheduling split не меняет его verdict.
Linux base87,73/180, publication60,97/180, SQL/schema99,88/180, purge72,56/180,
absence84,08/180; каждый child<120. Independent observers0,75/0,77/0,72/0,80/0,77с
в пределах60 подтвердили отсутствие всех owned containers/volumes и source/tools.
Windows exact candidate Python actors0 после terminal coordinator. Native symlink
cases покрыты Linux, Windows junction — Windows. Inherited cache_dir warning
сохранён; новых применимых skips нет. Полный isolated whole timing не объявлен PASS.

Private evidence: `protected-key-secret571/candidate-source571.json`,
`protected-key-overlay572/candidate-source572.json`, `windows-overlay-red.log`,
`windows-overlay-cli-red.log`, `windows-overlay-cli-green.log`,
`windows-overlay-parent-red.log`, `windows-overlay-owning36.log`,
`windows-r1-compat.json`, `linux-r1-compat.json`, `linux-r1-observed-unit.log`,
`linux-r1-retirement.json`, `linux-r1-retirement-independent.json`,
`compatibility-groups.json`, `partition-hypothesis.md`,
`r2-publication/windows-r1-compat.json`, `r2-publication/linux-r1-compat.json`,
`r2-publication/linux-r1-independent.json`, `linux-groups-summary.json`,
`windows-subgroups.json`, `windows-remaining-coordinator.json`,
`compatibility-summary.json`.
Full P2/P3/T3/T4, ordinary enabled/disabled restore/start и release OPEN.

## P2-A: frozen573 application-only ref — 09.10.2026

Chart/schema и existing ordinary Manager.chart guard реализованы в private573:
application выбирает отдельный runtime Secret; storage/VSO и frontend не меняются.
Два свежих real Helm/current runtime frames отказывают для старых refs, foreign
owner, drift и recovery metadata до ordinary Helm effect/start. Same-ref image
rollback и первоначальная установка сохранены. Это unit/consumer proof, actual
recovery generation не переключена, durable active binding после удаления
Deployment/lock ещё отсутствует; independent promotion/common P3 остаются OPEN.

Сохранены RED chart19/1 positive, lifecycle16/2 positive, owner6/3 positive; первый
полный chart1 FAIL/126 PASS установил Helm null-delete schema gap, затем исправлен.
Final573: Windows368 PASS94,73/180, Linux368 PASS39,00/180; operator/standalone consumer
Windows38 PASS и Linux38 PASS22,09/180. Всего406 PASS на каждой платформе, без skips.
Real bootstrap tools включены. Оба Linux lab штатно очищены, независимые observers
0,75/60 и0,77/60 подтвердили отсутствие всех3 containers/2 volumes каждого.
Frozen572/accepted565/native18/tools и прежние FAILED сохранены. Runtime/main и
установки не обновлены, full backend/CI/native/home/release этим не закрыты.
Evidence: `application-generation573/compatibility-summary.json` и source573 manifest;
точный scope/оставшиеся границы — [checkpoint573](../plans/2026-10-05-kubernetes-postgres-tls.md#p2-a-chart-и-ordinary-ref-guard--frozen573-09102026).

## Проверка плана и partial checkpoint574 — 09.10.2026

Сверены неизменяемый source574,157-case Windows owning log и реальные guard/start/
restore paths. Windows157 PASS43,90с; wrapper44,51/180. Decoder и перенос annotation
существующего runtime ConfigMap сверяют Namespace/ConfigMap/Secret и Helm stored
values после удаления Deployment/lock. Это ещё не producer/first activation,
independent origin или общий admission. Linux/affected/operator consumer/native/home
для574 не выполнялись; результаты406 cases573 не переносятся автоматически.

Узкий behavioral review: real Manager + real Helm render, внешние inventory/effects
только через existing fixture. Positive control допустил один записанный fixture
Helm effect. Такой же effect допущен для immutable False/missing/string, Helm
pending-upgrade/failed и отсутствия обеих persistent metadata и Deployment. Это
основание для отказных tests, не native acceptance. Начальный setup отказал также
на control из-за отсутствующего Helm PATH; после исправления только PATH выполнен
валидный повтор. Никаких эффектов в кластере или изменений product source.
Private evidence: local `2026-10-09-plan-review574-current/behavior-setup-r1.json`,
`behavior-review-r2.json`; owning source evidence — `runtime-generation574/`.

В плане исправлены очередь и файлы P2-A, добавлены strict immutable/status/fresh-target
условия, genuine first activation с partial ConfigMap/Helm ACK refusal, dependency
на P2-B/P3-A и отличие original intent TTL от ordinary reuse. Record admission SHA
не аттестует origin и не содержит производных ключа; producer/source proof остаются
обязательны. Требуется один исправленный frozen set с Windows/Linux/consumer перед
общим новым процессом/native/home. Original timing FAILED сохранены; runtime565,
source572/573/574/native18 и сторонние изменения не обновляются этим review.
[Исполнимая очередь](../plans/2026-10-05-kubernetes-postgres-tls.md#действующая-очередь-p2p3-после-сверки572--09102026).

## P2-A отказные границы574-r2 — 09.10.2026

На копии неизменяемого574 добавлены53 negative/4 positive cases до изменения
реализации: actual Windows53 FAIL/4 PASS, whole73,11/180. Минимальный existing
Manager guard требует active Secret `immutable is True`, current Helm deployed
и exact canonical revision1..2147483647. После исправления owning214 Windows
PASS49,12с, whole51,03/180. Legacy/default install и image rollback сохранены.

Один frozen574-r2: Windows471 affected PASS63,19/180 +38 consumer PASS8,31/180;
Linux471 PASS60,62/180 +38 PASS23,06/180. Всего509 distinct cases/platform без skips,
с настоящим extracted bootstrap/tools. Linux unit120/512Mi/1CPU/128Mi и whole180
сохранены, оба lab штатно очищены. Independent observers0,83/60 и0,77/60 подтвердили
отсутствие каждого3-container/2-volume lab; Windows candidate Python actors0.
Inherited cache_dir warning и исходный RED сохранены. Только lifecycle и existing
owning test отличаются от574; source574/573/572/565/native18/tools неизменны.

Private evidence: `runtime-generation574-r2/compatibility-summary.json`, source seal,
Windows logs/records, `r1-linux/` и `r2-release/` normal/independent records.
Это завершённый refusal repair и affected/consumer coverage; не native Secret ACK,
first producer/completed activation, protected origin, common enabled/disabled
admission или full restore/release. Runtime/main/рабочие установки не обновлялись.
Следующий source-bound call map выявляет disabled acquire без полного native ACK/UID
и initial preflight с обязательными precreated PVC: fresh target проверяется по
реальным empty/owned данным, не отсутствию всех claims. Двойная потеря metadata и
сохранённый completed outcome остаются частью следующих P2-A/P3 задач.

## Проверка плана и common owner575-r2 — 09.10.2026

Проверены actual Manager entrypoints и frozen575-r2: owning93 Windows PASS3,41/180;
affected564 Windows PASS63,86/180 и Linux PASS60,19/180; consumer38 Windows PASS8,25/180
и Linux PASS22,56/180. Итого602 distinct cases/platform без skips. Linux normal cleanup
и independent absence0,78/60 и0,80/60 подтверждены; Windows candidate actors0. 575 public
source hashes совпали, прежние575/574-r2/574/573/572/565/native18/tools и original FAILED
сохранены. Evidence: `operation-owner575-r2/compatibility-summary.json`, platform
records/logs/independent observers. Native/new-process остаются OPEN; process-local
receipt не закрывает durable resume или общий admission.

В план добавлен P3-O: отказ новому процессу по одному operation ID, unknown unlock
при исчезнувшем lock, подтверждённое обновление receipt после собственного CAS,
неопределённые native effects и общий deadline, actual owned Pod/Node вместо одного
selector/terminal Job. Исправлен устаревший home/OIDC приоритет. Это критерии предстоящей
реализации и приёмки; новые PASS выше относятся только к575-r2 compatibility/consumer.
В этом продолжении продуктовые исходники, Git index
и рабочие установки не изменялись; обновлены только два плана и этот отчёт.
[Общий owner/resume](../plans/2026-10-05-kubernetes-postgres-tls.md#p3-o-общий-владелец-и-аварийное-продолжение--09102026).

## Проверка действующего плана после572 — 09.10.2026

Сверены actual source572/chart, compatibility-summary и source-bound call map:
`restore()` пока выполняет import/hold/integrity → migrate → start → unlock без
общего key/auth/DB-generation admission. `chart()` использует переданные values;
application, PG password и VSO destination связаны с общим secrets.existingSecret.
Это подтверждённые пробелы реализации, а не отказ уже работающей установки.

Устранены пробелы плана: очередь больше не требует повторной реализации SQL overlay
и завершённых disjoint groups; P2-A задаёт application-only ref/schema и rollback
checks, P2-B — origin/identity/capabilities, P3-A — реальные entrypoints и частичный
импорт, P3-B — процессные аварии, исходный TTL и весь непустой restore. Уточнено,
что start timeout не доказывает остановку Pod, а собственный hash не доказывает
independent origin. Historical timing FAILED сохранены, их final применимость
должна быть явно разрешена по исходному критерию. Точная очередь —
[P2-A–P3-B](../plans/2026-10-05-kubernetes-postgres-tls.md#действующая-очередь-p2p3-после-сверки572--09102026).

Это документальная проверка: новые продуктовые tests/нативные effects не выполнялись,
источники565/572/native18, runtime и установки не изменены. Native, common restore,
whole-branch review и final release остаются OPEN.

## P2 кандидат571: проверка плана и незакрытый VSO defect — 09.10.2026

Сверены сохранённые результаты: causal39 FAIL27,37с →39 PASS35,69с;
следующий набор11 FAIL/19 PASS25,82с →30 PASS26,87с (39 deselected).
После исправления API fixture контрольный
`test_fresh_vso_producer_inventory_blocks_owner_conflict[none]` завершился
1 FAIL1,51с: parser ожидает только generic List и отказывает для действительного
VaultStaticSecretList. Все результаты сохранены отдельно; эти два частичных GREEN
не объявляются общим69 PASS на нынешнем source. Кандидат571 не frozen/не принят;
Linux/compatibility и native API/CLI acceptance ещё не выполнялись.

Новая CLI операция подключена, но positive unit case использует MockManager.
Legacy rotation отказ при наличии publication не доказывает successful old/new
Secret overlay. Public permission fields остаются False. Protected происхождение
material/promotion, общий enabled/disabled restore/start и полный T3/T4/release OPEN.

План дополнен typed list/capability/RBAC gates, independent promotion, положительным
переключением application/maintenance refs без PG/VSO rollout, antirollback старых
Helm values, исходным TTL всей цепочки и новым process/standalone consumer proof.
Никакие runtime/Secret/cluster/Git изменения этой проверкой не выполнялись.
Точные критерии: [P2/P3](../plans/2026-10-05-kubernetes-postgres-tls.md#пробелы-p2p3-после-проверки-кандидата571--09102026).
Private evidence: `protected-key-secret571/windows-secret-red.log`,
`windows-secret-green.log`, `windows-secret-edges-red.log`,
`windows-secret-edges-green.log`, `windows-vso-list-red.log`.

## P1 controller deadline/TTL — frozen570r2, 09.10.2026

Новые причинные tests:5 FAIL/3 PASS →8 PASS. Исправлены transport timeout сверх
parent budget и повторный отсчёт TTL после delayed CAS. Controller90с/optional
parent deadline передаются до private storage/generator; реальное `now` и исходное
`issued_at` разделены, expiry не продлевается. Новые input/validation/observe guards
входят в последующую полную затронутую matrix из521 cases на frozen570r2.

Windows519 PASS/2 SKIP150,11с, wrapper150,81/180. Linux520 PASS/1 SKIP78,03с,
wrapper97,62/180. Windows native-symlink skips покрыты Linux; Windows-only junction
проверен Windows. Real-tools standalone bootstrap PASS в обоих наборах.
Linux normal cleanup и отдельный observer0,89/60 подтверждают отсутствие всех
трёх owned контейнеров по name/cid и двух volumes; source570r2/565/native18/tools
неизменны. Прежние567/569 также остаются отдельными frozen компонентами.

Первоначальный R2 FAILED сохранён: wrapper167,88/180 остановился после unit120
и незавершённого TERM cleanup. Тот же контейнер позже сам завершился exit1:
1 FAIL/519 PASS/1 SKIP206,28с, существующий SQL-revision test не смог создать
`data` внутри readonly source. Отдельный normal retirement1,36/60 и fresh absence
сохранены. R3 исправил только test harness DATA_DIR/DIAGNOSTICS_DIR в tmpfs и
повторную компиляцию больших неизменных модулей через свежий private bytecode
cache. Состав, source pins, 512Mi/1CPU/128Mi tmpfs, whole180/child120 не изменены.
Исходный R2 не переименован в PASS.

Cooperative deadline checks не подтверждают physical fsync bound. Genuine native
first-dispatch/process interruption, Secret publication/promotion/overlay,
обычный enabled/disabled restore/start, full T3/T4 и release OPEN.
Runtime565/main/рабочие установки не обновлялись. Private evidence:
`protected-key-controller570/candidate-source570-r2.json`,
`windows-deadline-red.log`, `windows-deadline-green.log`, `windows-r2-compat.json`,
`windows-r2-compat.log`, `linux-r2-compat.json`, `linux-r2-retained-unit.log`,
`linux-r2-retirement.json`, `linux-r2-retirement-independent.json`,
`linux-r3-hypothesis.md`, `linux-r3-compat.json`, `linux-r3-compat.log`,
`linux-r3-independent.json`.

## P1 controller/CLI — исходный кандидат570, проверка плана 09.10.2026

Свежий owning run завершён exit0: Windows63 PASS40,78с, один inherited
`cache_dir` warning. Проверяется existing Manager, public generation intent v1,
получение fixture CAS ACK перед private RNG и explicit CLI input guards.
Сохранённые причинные результаты: первоначальные32 FAIL →32 PASS,
CLI14 FAIL/48 PASS и loader1 FAIL перед исправлениями. API/источники/drift
проверяются fixture; это не native protected publication или реальный Secret create.
Кандидат ещё не frozen/не принят. Linux/compatibility, propagation общего срока,
process interruption, new Secret overlay/promotion и общий restore/start OPEN.

Проверка плана добавила owning критерии для срока/unknown resume, порядка
initial generation перед completed rotation decision, old/new Secret binding,
chart/VSO ownership и запрета непредусмотренного PG rollout. Общий admission
обязателен для enabled и disabled entrypoints. Исправление документов не закрывает
эти gates и не обновляет runtime565, main code или рабочие установки.
Private evidence: `protected-key-controller570/windows-green63.log`,
`windows-red.log`, `windows-cli-red.log`, `windows-loader-red.log`.
Точная очередь и отказные тесты — в
[уточнениях P1–P3](../plans/2026-10-05-kubernetes-postgres-tls.md#уточнения-p1p3-после-проверки--09102026).

## Native OIDC — checkpoint 09.10.2026

**Актуальный локальный результат R8:** полный сценарий PASS, 148,70 / 900с с
подготовкой и штатной очисткой. Использованы production Settings, реальные
HTTPS/Keycloak authorization code, PG17 verify-full/SCRAM, Alembic и frontend.
Сценарий подтверждает настоящий старый и новый вход; purge всех providers/flows;
после возврата auth-таблиц старая cookie с новым ключом получает401. Отдельный
отрицательный контроль изолированного стенда возвращает старый ключ и наблюдает
возрождение старой сессии; повторный purge блокирует её и в этом контроле.
Возврат старого ключа в рабочей установке этим не разрешается.

Полные snapshots контрольных документов/истории, всех ранее наблюдавшихся audit
строк и held outbox сохранены. Первые четыре `glossary_rule_create` проверяются
ровно в startup-фазе; один `auth_login_success` — в каждой фазе входа. Неизвестные
дополнительные события и изменения существующих строк по-прежнему отклоняются.
Диагностика содержит только известные коды/количества, без private значений.
Windows99 PASS / 2 POSIX SKIP; Linux101 PASS / 0 SKIP, wrapper11,42 / 180с.

R8: `functional_passed=True`, `native_provider_login_verified=True`, все пять
behavior verdicts True; source/tools и whole deadline подтверждены, собственные
контейнеры/тома/сеть штатно удалены, retained live/unknown отсутствуют.
`full_T3=False`, `full_T4=False`, `release_accepted=False`: это auth-only rollback,
а не полный restore PG/Qdrant/files или protected recovery/start приёмка.

**История:** R5 (72,56с), R6 (71,95с) и диагностический R7 (72,41с) FAILED после
успешного первоначального входа, до ротации. R7 показал один login и четыре
`glossary_rule_create`; источник — `main` startup → `ensure_initial_rules` →
`GlossaryRuleRegistry.create`. Исходные отказы сохранены. Phase/count tests:
3 RED / 11 прежних PASS → GREEN; неправильные3/5 seed-событий запрещают вход,
удаление seed audit при purge отклоняется.

**Дом:** проверены доступность, достаточность ресурсов, Ready и прежний UID узла;
сохранён baseline двух кластеров (38/7 workloads,52/4 PVC,51/4 PV). Недостающий
backend565 импортирован в cache с проверкой root/platform/config и sealed archive;
собственный архив удалён. На момент native R8 home coordinator ещё не запускался; его guards
Windows4 PASS / Linux4 PASS, normal cleanup. Домашний behavior proof и штатный
protected controller остаются открытыми. Исторические условия сценария — в
[TLS-плане](../plans/2026-10-05-kubernetes-postgres-tls.md#условия-запуска-домашнего-oidc-сценария).

**Проверка плана 09.10.2026:** live read-only StorageClass —
`WaitForFirstConsumer` / `Delete`. В подготовленном адаптере прямой `nodeName`
обходит scheduler; до запуска требуется исправление. Также открыты точная проверка
source ConfigMap, observe-only обработка потерянного create reply, штатный DELETE
уже terminal Pod и учёт PV при раннем отказе. Добавлены отдельные regression
критерии в [TLS-план](../plans/2026-10-05-kubernetes-postgres-tls.md#условия-запуска-домашнего-oidc-сценария).
Завершённое независимое ревью: 0 Critical / 8 Important / 0 Minor. Дополнительно
подтверждены риск SIGKILL после DELETE живого Pod, обход writer exclusion через
изменённую роль, publisher до проверки PVC/PV и отсутствие финальной проверки
Node UID/Ready обоих кластеров. Все восемь включены в условия запуска с regression
критериями. Это предстоящая работа; статусы home/T3/T4/release не повышены.

Private evidence: `recovery-provider-login563/native-provider-startup-phase-r8.json`,
`native-linux-startup-phase-r8.json`, предыдущие `native-provider-r5/r6` и
`native-provider-audit-delta-r7.json`; home baseline/image/guard records в
`home-provider565/`. Это лабораторные proof, не release/publisher attestation.

## Домашний адаптер — checkpoint R1, 09.10.2026

Исправлен один полный набор из восьми замечаний ревью; исходные четыре файла и
review-source сохранены отдельно. TDD:12 RED / 4 прежних PASS → Windows16/Linux16
PASS; затем две фактические regression RED → Windows18/Linux18 PASS без skips.
Последний Linux wrapper10,12/180, source/tool guards и штатная очистка подтверждены.

Реальный home R1 FAILED53,34/900 до OIDC: все11 PVC стали Bound, но API опустил
пустые env в Job template; backend alias из CRI list не разрешался ImageStatus.
Причины проверены read-only; исправлены narrow defaults и actual CRI resolution.
Это не подтверждает full home, protected recovery/start, T3/T4 или release.

R1 normal cleanup=False: отдельная попытка retirement отказалась на Event для
не captured controller-deleted binder Pod. Свежие API/CRI подтвердили отсутствие
Pod/контейнеров; namespace и11 claims удержаны, исходный FAILED не переписан.
Orphan retirement остаётся отдельным OPEN gate. Повторный сценарий выполняется
только в новом namespace/record после новой проверяемой причины/изменения.
Private evidence: `home-provider565/home-oidc-r1.json`, `home-r1-diagnosis.json`,
`home-r1-retained.json`, `linux-guard-r3/r4.json` и regression logs.

## Домашний адаптер — checkpoints R2/R3, 09.10.2026

R2 FAILED59,25/900 до исполнения application: пустой env опускается и в Pod API;
CRI config ID оставался доступным, но containerd named root отсутствовал.
Отдельный R2 retirement PASS40,25/180: namespace/PV absence, оба baseline сохранены.
Именованный cache восстановлен одним import sealed OCI архива,19,23/180, root/config
и исходный Node UID/Ready подтверждены. Первая попытка подготовки cache отказалась
до import при чтении уже отсутствующего namespace; её record сохранён.

R3 FAILED124,48/900 при Pod contract; фактические binder/publisher Succeeded/exit0.
Секреты подготовлены в собственных PVC, PermissionError отсутствует; capabilities
не расширялись. Причина — API omission readOnly=False, подтверждённый server-side
dry-run. RED → Windows20/Linux20 PASS, без skips, Linux13,49/180 и штатная очистка.
Отдельный R3 retirement PASS41,70/180, namespace/PV absence и оба baseline совпали.
В corrected record снят ошибочный скопированный ярлык unstarted: R3 Jobs фактически
исполнялись и завершились. Оригинальный FAILED не переписан.

Полный OIDC/recovery behavior ещё не принят; R1 namespace/11 claims и orphan gate
остаются удержанными. Full T3/T4 и release OPEN. Private evidence: `home-oidc-r2/r3.json`,
`home-r2-retirement.json`, `home-r3-retirement-corrected.json`,
`named-cache-preparation-r2.json`, `linux-guard-r5/r6.json` и API-default regression logs.

## Домашний адаптер — checkpoint R4 и cache, 09.10.2026

R4 FAILED321,20/900 до запуска основного PostgreSQL: `ErrImageNeverPull`;
source init exit0. Остальные сервисы/OIDC в этом прогоне не проверены.
`source_tools_unchanged=True`; исходный результат и cleanup failure сохранены.

Отдельный R4 retirement41,98/180 принят: namespace/PV absence, отсутствие
процессов, оба исходных кластера сохранены. Основной PG никогда не запускался:
сохранённое наблюдение до DELETE и fresh CRI подтвердили отсутствие container ID;
init завершён. Синтетический `ContainerStatusUnknown/137` после DELETE не принят
как общий normal exit. Observer завершил прежнее намерение по UID/RV, без force.

Подготовка named cache PostgreSQL/Qdrant/Keycloak/frontend PASS45,01/240:
по одному import каждого sealed OCI архива, root/config проверены, исходный узел
и оба baseline сохранены. Backend подготовлен ранее. Cache PASS не подтверждает
запуск приложения. Named-reference preflight исправлен после трёх причинных RED:
Windows23/Linux23 PASS без skips; Linux13,45/180, source guards и normal cleanup.
Свежая привязка digest/config и реально сохранённый named reference обязательны
до создания namespace; tag fallback дополнительно проверяет fresh root. Следующий полный
home case ещё не принят; R1 orphan gate, protected recovery/start, T3/T4 и release
остаются OPEN.

Private evidence: `home-provider565/home-oidc-r4.json`,
`home-r4-stop-diagnosis.json`, `home-r4-retirement.json`,
`all-role-cache-preparation.json`, `stored-image-preflight-red-r2.log`,
`stored-image-preflight-green-r2.log`, `linux-guard-r7.json`.

## Домашний OIDC R6 и input repair — 09.10.2026

R6 FAILED900,11/900 перед последним probe; functional/home behavior/fullT3/fullT4/release остаются false. Проверены old/new login, deny-old после auth-only restore и revive-old отрицательного контроля; итоговый `purge_blocks_revival` verdict ещё не получен. Source565/native18/helper5 во время R6 неизменны. Отдельный retirement129,08/180 PASS, затем независимый observer подтвердил отсутствие namespace, всех11 PV и процессов, оба baseline сохранены. Отказы промежуточных retirement не переписаны; dependency order и bounded confirmed-conflict handling проверены отдельно. Неизвестный ответ не разрешает повтор.

Транспортная гипотеза проверена readonly: одна старая input проверка содержит60 SSH/API вызовов, measured single-object0,234–0,266с и full two-object List0,250с. Новый helper candidate выполняет два независимых полных input кадра и Lease в каждом, без cache/fallback. RED8 → Windows42 PASS0,96с и Linux42 PASS10,11/180, без skips, source/tool guards и normal cleanup. Source/runtime565/native18 не изменены; только private home helper3/5 и owning tests дополнены. Последующее actual readonly input proof подтвердило два полных кадра, identities всех11 PVC/PV и оба baseline,0 effects,11,75/60с; R1 retirement этим не разрешён. Дополнительный DELETE repair сохранил focused4 FAIL/3 PASS и прошёл текущие Windows47 PASS1,34с / Linux47 PASS12,88/180с, без skips; source/tool guards и штатная очистка подтверждены. Known API Conflict допускает bounded retry только с новым RV; unknown reply не повторяется, live Pod delete отказан до эффекта. Это не полный home/T3/T4/release PASS; полный R7 завершился PASS699,98/900 с исходными images/resources и всеми пятью behavioral assertions. Functional/cleanup/baseline/source/deadline flags true; независимый observer3,53/60 подтвердил отсутствие namespace/11PV/API Pod/живых CRI процессов и сохранность обоих baseline. Runtime565/native18/helper5 unchanged. Общий protected recovery/start и полный restore этим не приняты. Исторические42 и FAILED R6 сохранены, R1 retirement отдельно OPEN.

## Домашний адаптер — checkpoint R5 и image access, 09.10.2026

R5 FAILED322,70/900 до PG/OIDC. Named image присутствовал и оба ImageStatus
режима разрешали config, но kubelet record разрешал repository `postgres`,
тогда как Pod использовал полное canonical имя. Сохранено native repository
spelling и прежний digest; kubelet policy/credential records не менялись.
Отдельный retirement PASS47,50/180, namespace/PV/process absence и оба baseline.

Repository regressions:2 RED → Windows25/Linux25 PASS11,11/180. Короткий
image-access Pod фактически завершил команды всех пяти images exit0; controller
FAILED11,70/180 на API serialization и дополнительных OCI imageID indices.
Проверено содержание этих indices: wrapper → sealed root либо исходный выбранный
platform/attestation subset. Три RED → Windows28/Linux28 PASS без skips;
Linux11,30/180, source guards и normal cleanup подтверждены.

Отдельный observe-only exact-frame retirement выполнил normal UID/RV Pod и
namespace delete; отсутствие `results` в его wrapper остановило только запись
итога после удаления. Readonly observer2,97/60 независимо подтвердил отсутствие
namespace/процессов и сохранность двух кластеров. Исходные controller FAILED
сохранены; эти результаты не являются full home OIDC/T3/T4 или release PASS.
Полный R6 завершился FAILED900,11/900; отдельный retirement и следующий input repair описаны ниже. R1 retirement OPEN.

Private evidence: `home-provider565/home-oidc-r5.json`, `home-r5-retirement.json`,
`home-r5-image-diagnosis.json`, `home-r5-kubelet-public-access.json`,
`home-image-access-r6.json`, `image-access-r6-observe-adoption.json`,
`image-access-r6-retirement-finish.json`, `linux-guard-r8/r9.json`.

## Access logs — дополнение 09.10.2026

В Kubernetes worktree принят отдельный source565: базовый candidate563 и защита
access logs. Во всех audit modes Uvicorn сохраняет method/path/status, удаляя
query и fragment до всех обработчиков. Неизвестный формат заменяется безопасной
строкой; специальный formatter исключает logging traceback для такой записи.
Проверены настоящий HTTP callback с контрольными code/state, разделение audit
stdout/access stderr и десять последовательных запусков с новой SQLite/сервером.

Windows26 PASS / 1 Linux-only SKIP; Linux27 PASS без skips, wrapper26,41 / 180с;
stability10 PASS без skips, wrapper87,61 / 180с; actual WT post-adoption18 PASS.
Первичный RED, замечание ревью и отказы подготовки сохранены. Начальный Linux
лимит512МиБ вызвал подтверждённый OOM; реальный server profile использует2ГиБ/2CPU.
Read-only исходники и отдельный временный каталог данных сохранены; собственные
контейнеры/тома удалены с независимой проверкой отсутствия.

Лабораторный образ содержит349 проверенных runtime-файлов, включая исправление.
Он не является опубликованным release или доказательством native provider login.
Базовый full backend1752 предшествует этому дополнению; итоговый release требует
собственного полного gate. Runtime в main и работающие установки не обновлялись.

## T1, 05.10.2026

Реализован bounded static profile/material preflight. Missing profile означает
unknown/error; явно disabled — `None`. Проверяются exact flags/keys, immutable
Secret name/namespace/UID и повторный UID binding, encoded/raw N/N+1 до decoding,
PEM/key match, SAN `postgres`, validity, CA constraints, optional serverAuth EKU,
CRL issuer/signature/time. Ответ содержит только публичные hashes/UIDs.
Полная native chain/revocation проверка остаётся T5.

Producer, direct overlay, trusted bootstrap и toolbox согласованы на schema3;
новый TLS module входит в exact archive allowlist. Сохранены реальные archives
kit1/kit2 и bootstrap fixtures. Матрица3×3: same-version PASS, mismatch отказ
до extraction/mutations. Kit3 не объявляет archive2 совместимым. Backup всё ещё
format2; PG identity/legacy adoption/transition — T4.

| Проверка | Результат |
|---|---|
| Owning TLS/release/toolbox suite, Linux CPython3.12.15 | 120 PASS, 0 skips, 158.36s |
| Owning TLS suite, Windows CPython3.12.10 | 59 PASS, 0 skips, 57.70s |
| Real Dockerfile, pinned base + verified Helm/kubectl | build exit0 |
| Exact toolbox offline, no checkout/network, UID1000/read-only | bootstrap/overlay/TLS import +6 refusals PASS |
| Operator lock audit | exit0, 4 packages, 0 known vulnerabilities |
| Scoped Ruff | PASS |
| Frozen application source | 274 hashes unchanged |

Оба pytest-прогона содержат существующее предупреждение `cache_dir` при
`no:cacheprovider`; оно не означает пропуск тестов. Windows first harness
запуски не дошли до продукта (команда и отсутствующий editable doc-parser path);
исправлены только harness inputs, затем выполнен полный owning suite.

Зафиксированы cryptography50.0.2, cffi2.1.1, pycparser3.0, PyYAML6.0.3 с SHA256
официальных wheels и запретом source fallback. Hash-lock реально установлен в
отдельных Windows и Linux runtimes. Cryptography лицензирована Apache2.0 OR
BSD3-Clause, CFFI MIT-0, pycparser BSD3-Clause, PyYAML MIT; metadata проверена
на установленных пакетах. Версия/исправления сверены с
[PyPI](https://pypi.org/project/cryptography/50.0.2/) и
[официальным changelog](https://cryptography.io/en/latest/changelog/).
Это scoped operator gate; аудит/лицензии всего приложения и hosted CI не закрыты.

Первичные RED сохранены: absent module48 errors; first PKI47PASS/1fixtureFAIL;
malformed metadata58PASS/1FAIL; kit27PASS/10FAIL/1missing-toolsSKIP;
raw-before-decode/optional leaf3FAIL. Окончательные required runs без skips.

Private evidence: plan-owned `.superpowers/sdd/2026-10-05-kubernetes-postgres-tls/`;
там source SHA256, exact local image identity, dependency audit и исходные logs.
Локальный image ID и synthetic release metadata не доказывают публикацию OCI
release либо соответствие payload закоммиченной ревизии.

## Исторические открытые gates после T1 — 05.10.2026

На этом checkpoint T2–T6 были OPEN: server/client chart и Jobs, immutable HBA/initdb/native permissions,
runtime conflict checks, protected lifecycle/recovery, backup3/legacy2/current
trust transition, настоящая PKI в projected Secrets, полная установка/restore,
resource sizing, hosted CI и platform proof. TLS disabled по умолчанию;
первоначальный disabled rollout имеет собственные прежние gates.

## T2, частичный checkpoint 05.10.2026

Chart0.2.0 добавляет `storage.postgresTls` с disabled default и strict flags/refs.
TLS mode задаёт server args, immutable HBA с hash точных LF bytes и keep policy;
PG999 получает только server Secret0440, backend1000/Jobs только client Secret0440.
Frontend не получает PG projection. Client env задаёт verify-full/root/optional
CRL, GSS disable, SCRAM requirement и client-cert disable. Legacy disabled
templates сохраняют прежние storage/Jobs contracts. Runtime config/Secret и
inline Job env не могут переопределить native controls выбранного TLS профиля.

Job helper переносит установленный/желаемый профиль, отклоняет incomplete,
duplicate/valueFrom/extra PG controls, writable/subPath/неверные keys/modes и
нарушение container boundary. Входные templates не меняются при переносе.
HBA hash поправлен после реального RED на CRLF→LF Windows/Helm YAML conversion.

- Final T1+T2 owning/regression suite: **318 PASS, 0 skips, 400.69s**.
- До extra-control fix: chart/temp/lifecycle/runtime-trust197PASS/0SKIP; затем
  отдельный PGSSLKEY case RED и общий финальный GREEN. Initial chart RED26FAIL,
  1PASS (включая две исправленные fixture refs); промежуточный25PASS/2HBAFAIL.
- Runtime controls RED15FAIL; первый harness без Helm пропустил15 и не считается
  proof; required RED/GREEN выполнены с настоящим Helm.
- Real Helm4.3.0 lint TLS+CRL PASS; exact toolbox offline chart0.2.0 smoke PASS.
- Frozen backend Psycopg3.3.6/libpq180006: implicit cert/key/root/CRL отсутствуют
  и в HOME, и в passwd home UID1000; inherited PG controls отсутствуют. Это file
  baseline, а не handshake, SCRAM/revocation или pool-renewal proof.
- Scoped Ruff и diff whitespace PASS; application274source hashes unchanged.
- CI setup разделён: app dependencies отдельно от hash-locked operator install;
  новые owning tests включены в required list; GitLab/Chart versions согласованы.
  YAML consistency PASS, hosted CI не запускался.

T2 целиком остаётся OPEN: initdb и restart на пустом/непустом PVC, реальные
projected permissions/TCP TLS/SCRAM должны пройти изолированную приёмку.
T3–T6 не закрыты: Secrets UID/native preflight до mutations, checkpoint/recovery,
backup3/legacy2/current trust transition, полная установка/restore и platform CI.
Рабочее приложение не обновлялось; commits/push/publication не выполнялись.

## T2 завершена: actual native initdb/restart, 05.10.2026

В отдельном одноразовом Kubernetes namespace применены PostgreSQL StatefulSet,
Service и HBA ConfigMap, отрендеренные настоящим chart0.2.0. Chart server args,
security contexts, resources, volumes и HBA сохранены; imagePullPolicy=Never
используется для заранее проверенного локального OCI import. App release не
обновлялся. Синтетические пароль/key/CA/CRL создавались в памяти и передавались
API через stdin; private key/password не записывались в fixtures или evidence.

Два настоящих Jobs используют `operation_pod_spec` из текущего lifecycle:
первый записывает и commit-ит синтетическую строку после initial initdb,
второй читает её после graceful PG Pod deletion/recreation. Подтверждены новый
Pod UID, прежний PVC UID, fresh connections и видимость commit из другой сессии.
`pg_stat_ssl` подтверждает TLSv1.3/TLS_AES_256_GCM_SHA384; пароль использован,
PGREQUIREAUTH требует SCRAM и verifier проверен как boolean без вывода hash.
SSLRequest + отдельный проверенный handshake подтверждает actual leaf fingerprint.
Actual PG key/cert — root:999:0440/read-only, client CA/CRL — root:1000:0440/read-only.

На каждом этапе отказаны plaintext, untrusted CA, hostname и неправильный пароль
(всего 8 native negative assertions). Проверка требует соответствующую причину
отказа; timeout/refused/network failure не принимается за PKI/auth proof.
Версии: PostgreSQL17.11, Psycopg3.3.6, libpq180006, Python ssl OpenSSL3.5.7.
Для backend проверены все 274 frozen source hashes до/после restart, chart hashes
сверены с текущим кандидатом. Frozen OCI index:
`sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`.
CRI import wrapper, этот index, amd64 manifest и config связаны проверенными
SHA256 content bytes; wrapper ID не подменяется image config digest.

| Проверка | Итог |
|---|---|
| Final operator/profile/chart/lifecycle/trust/release/toolbox, Linux | 349 PASS, 0 skips, 73.44s |
| Final runner/PKI/denial guards, Windows pinned cryptography runtime | 31 PASS, 0 skips, 0.59s |
| Pre-final suite с обычным app conftest | 347 PASS, 0 skips, 436.84s; затем добавлены 2 fixture regressions |
| Actual native seed + restart Jobs | exit0, обе positive сессии и 8 negative assertions PASS |
| Owned cleanup, включая прежние FAIL | 7 namespaces удалены; оставшихся их PV — 0 |
| Scoped Ruff / whitespace / YAML / frozen source | PASS |

Final Linux команда (operator tests не требуют autouse инициализации app/БД):

```bash
python -m pytest backend/tests/test_kubernetes_postgres_tls.py \
  backend/tests/test_kubernetes_postgres_tls_chart.py \
  backend/tests/test_kubernetes_postgres_tls_runner.py \
  backend/tests/test_kubernetes_chart.py \
  backend/tests/test_kubernetes_temporary_storage.py \
  backend/tests/test_kubernetes_lifecycle.py \
  backend/tests/test_kubernetes_runtime_trust.py \
  backend/tests/test_kubernetes_release.py \
  backend/tests/test_kubernetes_toolbox.py -q --noconftest \
  -p no:cacheprovider --basetemp=/tmp/postgres-native-frozen
```

Run выполнен в frozen image с настоящими Helm/kubectl в PATH, PYTHONPATH=backend
и LITELLM_LOCAL_MODEL_COST_MAP=True. Это operator suite, не backend coverage.
Runner CLI для отдельного стенда (защищённый kubeconfig передаётся окружением):

```bash
python scripts/kubernetes/tests/postgres_tls_acceptance.py \
  --context <explicit-lab-context> \
  --backend-image <frozen-repository@sha256:digest> \
  --expected-image-id <verified-CRI-image-digest> \
  --source-manifest <protected-frozen-source-manifest> \
  --evidence-directory <new-owned-directory>
```

Сохранены initial RED16 runner cases; generated inventory RED3 и negative reason
RED8, затем GREEN. Первый actual run не нашёл canonical image ref при Never;
зарегистрирован alias для того же OCI content без изменения application image.
Второй отказал по неверному ожидаемому CRI identity: index/wrapper/config затем
проверены раздельно. Следующие FAIL выявили fixture CA с тем же issuer name и
другим RSA key: OpenSSL возвращал `invalid padding`. Нативный отказ не ослаблен;
untrusted-CA fixture теперь имеет отдельный issuer, RED→GREEN добавлен. Случай
same-subject/wrong-key и полная chain/revocation/rotation матрица остаются T5.
Первоначальная cleanup не приняла generated Endpoints/Events; исправлены guards
по service ownership/UID и исторически захваченным UID, включая deadline-удалённый
Job Pod. Foreign/recreated entities по-прежнему блокируют cleanup. Cleanup failure
не создаёт result.json; recovery первого FAIL выполнено только по captured IDs.

До/после доступны примерно 1.5GiB RAM и 9GiB диска; стенды шли последовательно.
Это не corpus sizing, disk-pressure или historical eviction proof. Проверка
postflight через неподдерживаемый status.reason field selector сохранена как FAIL;
затем inventory JSON подтвердил отсутствие оставшихся owned namespaces/PV/Pods.
Приватные logs/result/identities/source hashes находятся в plan-owned workspace;
generic документы содержат только технические результаты и placeholders.

**Открыто:** T3 static preflight до lock/storage + native gate до migration,
operation checkpoints/recovery; T4 backup3/legacy2/trust transition; остальная T5
матрица chain/expiry/revocation/leaf+CA+CRL rotation/audit; T6 lifecycle/restore,
sizing, dependency/project/coverage/Compose и hosted/platform CI. Проверенный
T2 не разрешает включать TLS в рабочем application release.

## T3 частично реализована: static/SCRAM/native gates, 05.10.2026

В `lifecycle.py` добавлен статический preflight для desired/installed профилей.
Он проверяет реальные server/client projections, immutable HBA/public material
hashes и namespace/Secret/HBA UID; fresh namespace и pending runtime Secret
не требуют SQL. VSO arrival проверяется повторно. Runtime PG controls нельзя
подменить через envFrom. Повторные bindings перед Job/mutation/start отклоняют
замену/expiry и конфликтующие входы; raw private materials не выводятся.

Existing Ready PG Pod связывается с StatefulSet controller UID; до stop выполняется
readonly boolean SCRAM verifier query без вывода password hash. После storage
создаётся новый Job с desired client projection, activeDeadlineSeconds60/backoff0.
Psycopg/libpq18 проверяет TCP+verify-full, password/SCRAM, pg_stat_ssl и отдельный
verified SSLRequest handshake с expected leaf fingerprint. Native verdict ≤1024bytes
имеет exact fields, boolean true и unique JSON keys. Completed Job сохраняется.
Request-timeout30s/client≤60s/controller wait≤90s; неуспех запрещает migration,
start и release lock. Readiness pg_isready не заменяет эту проверку.

Final home proof использовал реальный `Manager.preflight_postgres_tls`,
`check_existing_postgres_scram`, `postgres_tls_gate` через plan-owned adapter.
Job создавался настоящим Manager.apply; адаптер добавлял ownership labels для
existing guarded cleanup и заменял только job image ref/pull policy на проверенный
локальный frozen OCI. PostgreSQL StatefulSet/chart и operator sources сверены
хешами. Seed и restart дали PASS: новый PG Pod UID, тот же PVC, commit visibility,
TLS/SCRAM/actual leaf и 8 native negatives; обе дополнительные Manager Jobs PASS.
Все 274 frozen backend source hashes совпадают до/после restart. Backend OCI index
остаётся `sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`;
import wrapper/config связь — как в T2 evidence. Ключи и пароль синтетические.

| Проверка текущего кандидата | Итог |
|---|---|
| Final operator suite с новым lifecycle модулем, Linux | 398 PASS, 0 skips, 78.64s |
| Из них новый TLS lifecycle module | 49 cases |
| Actual Manager static/SCRAM/native gate, initial + restart | exit0, обе фазы PASS |
| Exact operator/chart/backend hashes | PASS, 274 backend files |
| Cleanup всех T3 запусков | 5 namespaces удалены; оставшихся их PV — 0 |
| Scoped Ruff / YAML / whitespace | PASS |

Команда — final Linux T2 operator suite выше **плюс**
`backend/tests/test_kubernetes_postgres_tls_lifecycle.py`, с теми же frozen image,
настоящими Helm/kubectl, PYTHONPATH и `--noconftest -p no:cacheprovider`.
Это operator-only suite; backend coverage и Windows T3 suite не объявляются
проверенными. Windows31 из T2 сохраняется как историческая отдельная проверка.
Plan-owned actual command использует `t3_native.py` с теми же guarded context,
image/source manifest/evidence-directory аргументами; API операции действительно
выполнялись в одноразовом namespace, а не через mocked kubectl.

Сохранены initial RED25, boundary/revalidation/format guards RED и первый реальный
FAIL. Реальный Kubernetes API добавляет HBA volume defaultMode420 (0644): strict
parser ошибочно отказал. Регрессия 1FAIL/5PASS → fix → GREEN: разрешены только
отсутствующий mode или integer0644; другие modes/types/extra controls запрещены.
Ни TLS-проверки, ни разрешения Secret не ослаблены. Три initial home FAIL сохранены;
run4 PASS, затем strict deadline regression RED1 → GREEN49 и final run5 PASS.
Deadline regression подтверждает: успешный Job response после90s не запускает
новый log read. Финальный оператор совпадает с home по SHA256 обоих модулей.

До/после стенда доступно около1.4–1.5GiB RAM и9GiB диска, четыре CPU. Стенды
запускались последовательно; current cleanup inventory не является доказательством
исторического отсутствия eviction или corpus/disk-pressure sizing. Рабочее app
release не обновлялось. Commit/push/publication не выполнялись. Generic report
не содержит реквизитов среды; private evidence остаётся в plan-owned workspace.

**Временные ограничения:** installed TLS deploy/backup отказывают до lock/stop,
пока T4 не реализует backup3. Format2 restore в TLS target отказывает до import:
старый backup не доказывает PG identity. Fresh install/native gate не разрешает
выпуск TLS. Снятие этих guards требует backup3 и owning tests.

**Открыто:** T3 checkpoint/recovery с controller interruption, сохранением old/new
storage/Pod identities и SQL compatibility decision; T4 backup3/legacy2/current
trust transition; T5 chain/expiry/revocation/leaf+CA+CRL rotation/audit; T6 full
Manager lifecycle, непустой restore/failed rotation, sizing, current dependency,
coverage/project/Compose и hosted/platform CI. Полный VSO flow и failure/lock
retention на настоящем controller также остаются для отдельной приёмки.

## T3 durable checkpoint foundation, 06.10.2026

Добавлен bounded public schema2 journal в отдельном namespaced ConfigMap.
Namespace/lock/checkpoint UID и resourceVersion связывают обновления. Запись
prepared предшествует stop, storage_changing — Helm mutation, migration_started
— writer, start_started — старту app. Переходы монотонны, при interruption после
migration_started фиксируется may_have_started. Повтор из stale памяти после
lost CAS reply отказан; persisted phase можно прочитать новой Manager-инстанцией
или отдельным CLI. Это не автоматическое возобновление операции.

Journal хранит только public Secret refs/UID/hashes, HBA, image refs/known digest,
chart-tree/ordered-values hashes, installed Helm manifest hash, template hashes,
observed PG controller/Pods/PVC. Keys/passwords/raw env/templates отсутствуют.
Exact schema/64KiB/duplicate keys/types/NaN/changed binding guards проверены.
UID replacements, changed chart/values, expired/changed TLS material, unready PG
и старый Pod при новом template запрещают следующую phase mutation. Namespace и
lock повторно сверяются после inventory, перед CAS. После journaling material
validation повторяется непосредственно перед storage/start.

`operation-status` читает только operation/phase/migration и не требует values:

```bash
python scripts/kubernetes/lifecycle.py operation-status \
  --context <explicit-context> --namespace <namespace> --release <release> \
  --operation-id <operation-id>
```

Incomplete TLS lock не снимается recover-lock или прямым release_lock. Удаление
lock pointer при сохранившемся journal не обходит guard; legacy TLS state без
journal требует отдельной recovery процедуры. После успешной terminal phase
lock удаляется через API DeleteOptions UID/resourceVersion, journal сохраняется.
Это protocol guards для maintenance operator; RBAC должен закрывать запись CM от
пользовательских workload, привилегированный администратор не считается недоверенным
владельцем этой записи. Retention не заменяется массовым удалением по labels.

| Проверка current candidate | Итог |
|---|---|
| Final Linux operator suite | 445 PASS, 0 skips, 84.04s |
| Новый checkpoint module | 47 cases |
| Windows parser/CAS/status/lock guards, pinned runtime | 34 PASS, 13 deselected, 0 skips, 0.25s |
| Actual journal schema2/API/CAS/CLI, seed + PG restart | exit0, PASS |
| Frozen source/chart/operator identity | 274 backend files и exact hashes PASS |
| Final owned cleanup | 3 namespaces удалены, оставшихся их PV — 0 |
| Scoped Ruff / CI YAML / whitespace | PASS |

Linux команда — предыдущий final operator suite плюс
`backend/tests/test_kubernetes_postgres_tls_checkpoint.py`, в том же frozen backend
image с actual Helm/kubectl и `--noconftest -p no:cacheprovider`. Полный backend
coverage здесь не запускался. Windows выборка исключает13 Helm/prepared-fixture
cases, все они обязательны и выполнены в Linux47. В pytest остаётся известное
configuration warning cache_dir от отключённого cacheprovider; failures/skips нет.
Новый module включён в required Kubernetes CI list; hosted CI не запускался.

Actual plan-owned `journal_native.py` расширяет PG-only runner: PG уже bootstrapped
перед созданием journal, app release не устанавливается. Journal сохраняется перед
graceful PG Pod replacement; свежая Manager-инстанция и отдельный CLI process
читают storage_changing. Recover-lock отказан до mutation. После нового PG Pod UID,
того же PVC и commit visibility новый native gate PASS; реальные replace/CAS
переводят journal в provision_complete, conditional delete удаляет lock, свежий
reader подтверждает сохранённый journal UID. Это прямые реальные Manager methods,
но **не полный** Manager.provision/deploy/VSO или убийство активного migration
controller. Unit API double отдельно проверяет lost reply и failures каждой фазы.

Все 8 native denial assertions и server/client read-only0440/group999/1000 повторно
пройдены. Backend OCI index остаётся
`sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`;
его CRI graph и все274 source hashes — как в T2 proof. Current operator оба модуля
совпадают с фактически исполненными home bytes. Final capacity: четыре CPU,
около1.4GiB доступной RAM и9GiB диска. Это не sizing/eviction/disk-pressure proof.

Initial RED и fixture failures сохранены. Parser/bindings → pipeline → storage
identity → chart/generation freeze → TLS/values repeat → missing pointer guards
прошли RED→GREEN. Для first unreleased journal1 добавлен отдельный ordered-values
identity в journal2; missing old input identity не домысливается. Первый home
journal1 и следующие journal2 run2/run3 PASS сохранены раздельно. Disabled values
regression RED показала случайный общий gate16files: теперь gate только selected
TLS, disabled path сохраняет существующие inputs. Final445/Windows34 относятся
к последнему source candidate после этого исправления; earlier438/444 не заменяют
его результат. Рабочее home application не обновлялось; commit/push/release нет.

**Открыто:** полное T3 storage recovery по operation ID требует protected old kit,
exact installed chart artifact digest/проверенной API-default нормализации и
pinned image identity. Installed Helm manifest hash не является artifact digest;
image tag даёт digest=null. Native trust/выход нового PG Pod/SQL compatibility
нужно проверить до возврата old writer. После migration_started обычный Helm
rollback запрещён; restore идёт в новые PVC по policy. Сейчас отдельная команда
возврата old storage не реализована, terminal unlock не её замена. Checkpoint
foundation не снимает T4 guards TLS backup/upgrade/legacy2-to-TLS restore. T4–T6,
full interruption/restore/audit/rotation/sizing/project/Compose/coverage и
hosted/platform gates OPEN. TLS release по-прежнему не разрешён.


## T3 package integrity, 06.10.2026

Добавлен offline CLI `release.py verify-recovery-package`: проверка saved schema3
release/chart/kit, expected SHA/revision из protected promotion, exact members,
bounded JSON/tar inputs, pinned images/toolbox и sanitized refusal. Старый код
не исполняется, extraction/SQL/API/mutations/unlock отсутствуют.

```bash
python scripts/kubernetes/release.py verify-recovery-package \
  --release <saved-release.json> --tools-archive <saved-operator-tools.tar.gz> \
  --chart <saved-chart.tgz> --expected-release-sha256 <protected-release-sha256> \
  --expected-revision <protected-full-revision> \
  --toolbox-image <saved-toolbox-repository@sha256:digest>
```

| Проверка текущего candidate | Итог |
|---|---|
| Full focused Linux operator suite | 485 PASS, 0 skips, 32.14s |
| Новый package module | 39 cases, включая symlink и CLI |
| Windows pinned package tests | 34 PASS, 5 deselected, 0 skips, 1.54s |
| Actual Helm package chart0.2.0 + kit offline | PASS, installed generation не проверена |
| Exact Linux snapshot hashes | 340 файлов PASS, включая274 frozen backend files |
| Scoped Ruff / CI YAML / whitespace | PASS |

Все12 operator modules выполнены в том же frozen backend OCI image, с actual
Helm/kubectl, network none, source readonly, временным DATA_DIR, без kubeconfig;
контейнер работает под UID1000 на домашней VM. Это unit/offline artifact acceptance,
не Kubernetes recovery или hosted CI. Backend coverage и полный project/release
suite не запускались. Новый module включён в required Kubernetes CI list.

Сохранён initial RED24 на Windows, дополнительный RED3 выявил bool-format,
fractional-schema и duplicate JSON metadata. Первый Linux focused run39 errors
остановился на неполной test bundle; bundle дополнен до полного allowlist.
Первый полный Linux run: 438 PASS/33 FAIL/13 ERROR — общая fixed-epoch CRL истекла
06.10 и временный DATA_DIR не был задан для readonly source. Lifecycle PKI fixture
теперь создаётся относительно actual clock, отдельный regression подтверждает,
что CRL спустя два дня по-прежнему блокирует next phase без mutation. Product
проверки срока действия не менялись. Известный pytest cache_dir warning остаётся
от отключённого cacheprovider; final failures/skips нет. Исходные результаты не
заменены успешным прогоном.

**Граница:** protected artifact SHA ещё нужно связать с installed journal/template
и image identity, проверить API defaults, current old trust и writer/Pod exit.
Dedicated storage recovery по operation ID не реализован; offline PASS не даёт
права на rollback, TLS downgrade или unlock. Schema2 package recovery отказывает.
Полный T3, T4 backup3/upgrade guards и T5–T6 остаются OPEN; working application
не обновлялось, новых Kubernetes ресурсов не создавалось. Commit/push/release нет.


## T3 saved-generation binding, 06.10.2026

Добавлен readonly `lifecycle.py postgres-recovery-check`: verified schema3 package
связывается с full old render, protected saved Helm manifest и installed template/
image hashes journal. После possible migration и при изменениях binding/input
вернуть старое поколение check не разрешает. При успехе verdict содержит
`storage_recovery_authorized: false`; SQL/storage/unlock не выполняются.

```bash
python scripts/kubernetes/lifecycle.py postgres-recovery-check \
  --context <explicit-context> --namespace <namespace> --release <release> \
  --operation-id <operation-id> --chart <saved-chart.tgz> \
  --values <saved-old-values.yaml> --saved-release <saved-release.json> \
  --saved-kit <saved-operator-tools.tar.gz> --saved-manifest <saved-helm-manifest.yaml> \
  --expected-release-sha256 <protected-release-sha256> \
  --expected-revision <protected-full-revision> \
  --toolbox-image <saved-toolbox-repository@sha256:digest>
```

| Проверка текущего candidate | Итог |
|---|---|
| Full focused Linux operator suite, 13 modules | 520 PASS, 0 skips, 33.39s |
| Новый generation/operation-ID/CLI module | 35 cases |
| Windows pinned runtime | 35 PASS, 0 skips, 0.62s |
| Actual Kubernetes1.37 server-side defaults | exact normalized API template hashes PASS |
| Actual Helm package + render + TLS/HBA binding | PASS, HBA mismatch refused |
| Persistent dry-run proof resources | 0, absence verified for exact names |
| Exact snapshot / frozen source | 341 files / 274 backend files PASS |
| Scoped Ruff / CI YAML / whitespace | PASS |

Full Linux suite выполнялась UID1000/network none/source readonly/temp DATA_DIR
без kubeconfig в прежнем frozen backend image. Новый module включён в required
Kubernetes CI list; hosted CI и полный backend coverage/release suite не запускались.
Сохранены RED25 parser/generation cases и RED7 Manager cases. Первая версия520
suite PASS33.21s, Ruff заметил запись после semicolon; после разбивки строк final
candidate повторно прошёл520/35. Известный cache_dir warning относится к выключенному
cacheprovider; final failures/skips нет. Gates/expiry assertions не ослаблялись.

API proof работает отдельным UID1000 контейнером с explicit home context, server-side
create --dry-run=server и проверкой отсутствия конкретных объектов после ответа.
Ни Deployment, ни StatefulSet не сохранялись. Нормализованные templates текущего
chart точно совпали с API1.37.0; настоящий package с immutable synthetic image refs
был повторно rendered и прошёл binding/HBA denial. **Checkpoint синтетический**,
с template hashes из actual API admission: он не подтверждает исторический
installed release или реальную recovery-операцию. TLS Secret UIDs/CA fingerprints
в этой fixture synthetic; certificate validity/native TLS здесь не проверялись.

**Открыто:** current old trust/native certificate proof, current writer absence,
PG/PVC/Pod UID checks, durable recovery decision/phase journal и graceful возврат
storage; explicit санкционированный downgrade при первой TLS activation. После
may_have_started требуется SQL compatibility policy/restore в новые PVC, не Helm
rollback. Сам mutating recovery пока не реализован; readonly verdict не снимает
lock или T4 guards. Schema2 artifacts отказывают. T3 и T4–T6/TLS release OPEN.
Working home application не обновлялось; commit/push/release нет.


## T3 current old TLS static prerequisite, 06.10.2026

Реализован opt-in `postgres-recovery-check --check-old-tls-materials` после полного
saved-generation gate. По текущему UTC проверяется сохранённая public TLS identity:
UIDs immutable Secrets, CA/CRL/leaf/chain hashes, сроки/key/SAN/EKU/CRL signature.
Добавлен recovery-specific отказ известному отозванному сертификату server chain;
обычный T1 validator не объявлен полным chain/revocation checker. Delta/indirect/
scoped CRL здесь не поддерживаются. Current HBA content/hash/ns/release проверяются
с повторным UID binding, Secrets читаются дважды. Исторический HBA UID journal2
не содержит. Protected files/values и journal/namespace/lock повторяются после
material inspection. Unmanaged old profile отказывает без plaintext inference.

| Проверка текущего изменения | Результат |
|---|---|
| New old-trust26 + saved-generation43, pinned Windows Python3.12.10/cryptography50.0.2 | 69 PASS, 0 skips, 1.19s |
| Scoped Ruff / CI YAML / whitespace | PASS |
| Application source | 274 frozen hashes unchanged |
| Source snapshot для следующего Linux прогона | 342 exact file hashes; локальный архив готов |
| Linux regression текущего изменения, frozen Python3.12.15/cryptography50.0.2 | 554 PASS, 0 skips, 33.35s;14 operator modules |
| Hosted CI / native old-generation recovery | НЕ ВЫПОЛНЕНЫ |

Initial25 missing-functions RED и3 protected-input race RED сохранены. Первые
Windows manager cases получили7 fixture errors из-за repository basetemp вне
существующего каталога; исправлен только путь harness в private directory.
Начальная проверка frozen manifest выбрала неверную относительную базу; повтор
из backend/ подтвердил все274 hashes. Известное предупреждение cache_dir остаётся
при no:cacheprovider, skipped tests нет. Предыдущий Linux520 PASS относится к
прежнему snapshot341. Новый Linux554 проверяет точный snapshot342 до и после
тестов; отдельная локальная повторная проверка подтвердила342 source/274 frozen
backend hashes. Контейнер UID1000/network none/read-only source/temp DATA_DIR,
без kubeconfig, pinned OCI image866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500.
Изменений Kubernetes и работающей установки в этом прогоне нет.

Первоначальная передача была отклонена автоматической проверкой разрешений.
После явного разрешения пользователя передан только подготовленный source/test
bundle на домашний стенд и выполнен этот изолированный unit-прогон. Первоначальный
локальный Docker остаётся недоступен; remote approval block для указанной передачи
снят, но это не approval рабочего deployment/native recovery.

Verdict содержит old_tls_materials_validated=true и old_tls_native_verified=false;
storage_recovery_authorized=false. Проверка не выполняет SQL/Kubernetes mutations
или unlock, не является complete path builder/атомарным API snapshot. Writer absence,
PG/PVC/Pod identity, durable recovery claim/phases и graceful/native возврат storage
ещё OPEN; installed TLS backup3 guard сохраняется. Полный T3 и T4–T6 OPEN.
Работающее приложение, deployment, commit, push и release не выполнялись.


## T3 current-state readonly prerequisite, 06.10.2026

Добавлен postgres-recovery-check --check-current-recovery-state после protected
saved-generation и old TLS gates. Два namespace-wide standard workload/PVC List
без selectors; между ними повторён old trust/HBA, затем files/values и ns/lock/journal.
Current PG UID/template, captured Pod owners, external PVC UID/Bound и отсутствие
application/Job writers проверяются строго. TLS-only change/новый unready PG Pod
разрешён только в storage_changing; остальные PG fields не игнорируются.
Application/model-stub должны отсутствовать или fully stopped, leftover RS0,
Jobs terminal/no active и все Job Pod containers exited. Unknown controllers/Pods,
CronJob/DaemonSet и ephemeral containers отказывают. Stub home recovery требует
дополнительно остановить model-stub. Обычное развертывание не изменено.

Command stdout ограничен N+1 bytes/8MiB в памяти, stderr discarded; timeout60s,
request30s и cleanup1+1s. Parser ограничен256 objects и отказывает duplicate/NaN/
continuation/deep JSON. Полный private inventory/fingerprint не сохраняется и не
выводится: terminal Job specs могут содержать inline private inputs.

| Проверка текущего изменения | Результат |
|---|---|
| Linux frozen Python3.12.15/cryptography50.0.2,15 operator modules | 634 PASS,0 skips,35.83s |
| Windows pinned Python3.12.10/cryptography50.0.2 | 149 PASS,0 skips,2.60s; current-state74/generation48/old-trust27 |
| Actual Kubernetes1.37.0 server dry-run template/Pod defaults | PASS, persistent resources0 |
| Actual bounded namespace-wide kubectl GET/JSON List command | PASS |
| Source before/after | exact343 hashes; frozen application274 unchanged |
| Scoped Ruff/CI YAML/whitespace | PASS |
| Hosted CI / real old-release recovery / physical PG exit / native old TLS after return | НЕ ВЫПОЛНЕНЫ |

API proof использует реальные API-admitted specs, но historical checkpoint UID,
PVC bindings/status и old/new Pod UID синтетические. Pure validator принимает
эти admitted specs, simulated TLS rotation/unready Pod и отказывает writer/PVC
подмене. Namespace List command реально выполнил только GET; не заявляется PASS
фактического исторического installed generation или native TLS/data recovery.

Сохранены RED45 absent function,6 manager,3 terminal Pod loopholes и6 stdout-limit;
первый synthetic fixture имел невозможную одинаковую UID у StatefulSet и PVC —
исправлены fixture UID. Initial625 Linux PASS на предыдущем snapshot сохранён.
Server dry-run выявил отсутствующие в fixture application securityContext и
Qdrant automountServiceAccountToken=false/storage mount; приведены к реальному chart.
Ещё один driver отказ вызван YAML alias от общего объекта selector/labels —
исправлено только copy.deepcopy в proof driver. Product unknown-field/alias guards
не ослаблены. Финальные634/149 относятся к exact343 snapshot2. Cache_dir warning
остаётся при no:cacheprovider, skipped tests нет.

Storage recovery authorization остаётся false; old TLS native proof false.
Это не atomic namespace fence/physical Pod exit и не блокировка будущего внешнего
actor/CRD. Durable recovery claim/phases, original operator/CI остановка и pending
Helm/in-flight mutations, graceful captured PG Pod exit и native old-leaf verification
после возврата ещё OPEN. Migration may_have_started не сбрасывается; backup3 guard
сохранён. Полный T3/T4–T6 OPEN. Рабочее приложение, SQL, Helm upgrade, commit/push
и release не выполнялись.

## Current runtime / selected HTTP trust — 06.10.2026

Реализован отдельный readonly `--check-current-runtime` с автоматическими
protected generation/old TLS/current-state gates и повтором после runtime.
Runtime ConfigMap data exact saved manifest, envFrom/PG password bindings exact;
runtime Secret UID/data/type/namespace и credential/session format проверяются.
Conflicting keys/inline HTTP TLS disable отказывают. HTTP immutable CA bundle
требует независимо сохранённый protected public hash, current CA validity и UID
freeze. Отсутствующий bundle явно отмечен, не означает native HTTPS validation.

| Проверка | Результат |
| --- | --- |
| New runtime tests | 59 PASS |
| Pinned Windows four operator modules | 208 PASS,0 SKIP,5.50s |
| Linux frozen OCI/operator16 modules | 693 PASS,0 SKIP,40.01s |
| Source before/after | exact344; frozen application274 unchanged |
| Ruff/CI YAML/whitespace | PASS |
| Hosted CI / current runtime native API-SQL-HTTPS / full recovery | НЕ ВЫПОЛНЕНЫ |

Linux прогон: отдельный sourceRO контейнер UID1000/networknone/read-only/tmpDATA,
без kubeconfig. Рабочее приложение не изменялось. Red46 missing functions и
inline-disable RED1 сохранены. Начальный Windows direct pytest запуск не нашёл
pytest в pinned venv; итоговый runner добавляет main pytest site-packages после
pinned cryptography50.0.2. Tests не ослаблены, cache_dir warning сохраняется.

Secret/ConfigMap API responses bounded2MiB/N+1/60s/request30s, strict JSON; данные
и UID/type/immutable сравниваются только в памяти, в том числе между полными
passes. ResourceVersion может меняться без изменения data. Содержимое, private
hash, URL/password не экспортируются; ошибки suppress parser cause chains.
Public verdict: current_runtime_validated=true, selected_http_trust_validated
по наличию bundle; old_runtime_native_verified=false/storage_recovery_authorized=false.

Это не исторический Secret baseline: checkpoint2 не содержит runtime private
body/исторический UID. Перед old writer остаётся protected Secret/VSO version
provenance для session key либо отдельно согласованная ротация с проверкой
последствий для сессий. Native old SCRAM подтверждает действующие SQL credentials;
HTTP static CA guard не доказывает HTTPS path. Durable claim/CAS, original
pipeline fencing, graceful PG exit, native old TLS после возврата и T4–T6 OPEN.
Backup3/installedTLS guard сохранён. Release/commit/push/upgrade не выполнялись.

## Recovery claim/CAS и cooperative fence — 06.10.2026

Реализованы explicit postgres-recovery-claim, protected bounded stop record,
полные static gates, pending Helm refusal, UID/RV CAS public marker в maintenance
lock, no-effects claim owner и original operator command fence. Stop/inputs/
ordered values/journal/ns/lock/Helm status повторяются; lost reply не повторяет
запись и не снимает lock. Fresh operation-status показывает recovery ID/claimed,
ordinary unlock отказывает. Checkpoint2/migration остаются неизменными.

| Проверка | Результат |
| --- | --- |
| New claim tests | 62 PASS |
| Pinned Windows five operator modules | 270 PASS,0 SKIP,6.60s |
| Linux frozen OCI/operator17 modules | 755 PASS,0 SKIP,44.30s |
| Source before/after | exact345; frozen application274 unchanged |
| Ruff/CI YAML/whitespace | PASS |
| Kubernetes1.37.0 owned ConfigMap CAS proof | PASS, namespace и две ConfigMap |
| UID/RV/old revision conflict, fresh reader, original command refusal | PASS |
| Owned namespace deletion UID/RV + absence | PASS |
| Hosted CI / actual original CI cancellation / native old PG recovery | НЕ ВЫПОЛНЕНЫ |

API proof использует реальный namespace/lock/checkpoint UID/RV и настоящий
Manager claim/status/dispatch gate. TLS/runtime/generation gates и Helm release
status в этом **ownership-only fixture** — doubles. Это не доказательство
historical old release или фактической остановки pipeline. PostgreSQL/SQL/PVC и
рабочее приложение не затрагивались; storage_recovery_authorized=false.

Сохранены RED36 missing claim, status/CLI/owner RED и5 protected-input race, затем
mutating auth reconcile RED1. В parser tests исправлена ошибка harness: два
динамических imports создавали разные классы ProfileError для pytest.raises.
Windows symlink fallback теперь scoped monkeypatch; native symlink отказ в Linux
без skip. Ruff сначала выявил два unused test imports; удалены. Итоговые числа
относятся к exact345 snapshot. Cache_dir warning сохраняется, skips нет.

Stop record подтверждает действие оператора, не автоматически отключает CI;
CLI принимает его только с exact current journal/operation binding. Original
cooperative dispatch fence не отменяет уже отправленные команды и не покрывает
older kit/external actors/CRDs. В этом этапе record phase только claimed, без
resume/TTL/reset/adoption и без разрешения recovery effects. Durable recovery
phases, graceful actual PG exit, native old TLS/SCRAM, session-key provenance и
T4–T6 OPEN. InstalledTLS backup3 guard сохранён. Release/commit/push не выполнялись.

## Recovery-ID resume / stop_prepared — 06.10.2026

Readonly resume повторяет ownership/protected inputs/Helm и полные static gates.
Prepare-stop пишет public marker2 с captured PG/Pod/PVC binding и HTTP CA hash/null
до каких-либо storage effects. Marker1 остаётся readable; checkpoint2/migration
не меняются. Same storage identity обязательна, Ready не является native proof.
Exact request/bytes CAS permit ограничен одной записью и очищается finally.
Повтор prepare, изменённый owner/storage/input и ambiguous reply не разрешают retry.

| Проверка | Результат |
| --- | --- |
| New resume/intent tests | 42 PASS |
| Pinned Windows six operator modules | 312 PASS,0 SKIP,8.73s |
| Linux frozen OCI/operator18 modules | 797 PASS,0 SKIP,49.69s |
| Source before/after | exact346; frozen application274 unchanged |
| Ruff/CI YAML/whitespace | PASS |
| Real Kubernetes1.37 marker2 CAS/fresh prepared resume | PASS |
| Duplicate prepare/stale-RV/original dispatch refusal | PASS |
| Checkpoint unchanged / UID-RV owned namespace cleanup/absence | PASS |
| Native PG shutdown / native old recovery / hosted CI | НЕ ВЫПОЛНЕНЫ |

API fixture использует реальный namespace/lock/checkpoint UID/RV, shipping
Manager CAS permit/status/resume/prepare. Helm status и static generation/TLS/
writer/storage/runtime gates — synthetic doubles; observed Pod/PVC IDs не являются
реальными storage objects. Это проверка metadata protocol, а не old release/SQL/
physical exit. Working application, Pods/PVC/SQL и Helm upgrade не затрагивались.

RED35 missing resume/prepare/CLI сохранён; intermediate33PASS/2CLI failures затем
GREEN. Один unused test import удалён по Ruff. Дополнительно проверены post-gate
file/values/record races, exact bytes permit rejection и fresh prepared reader.
Cache_dir warning остаётся, skips нет. No release/commit/push.

Stop intent не останавливает PG и не разрешает storage recovery. Следующий этап
stop_requested/conditional scale0 обязан подтвердить выход captured containers;
API absence без termination proof при partition/unreachable/force delete не
считается завершением. Node/lease/CSI fencing и actual graceful exit ещё OPEN.
Native old TLS/SCRAM после возврата, session-key provenance и terminal/unlock
protocol также OPEN; full T3/T4–T6, installedTLS backup3 guard сохранены.


## Captured PostgreSQL graceful stop — 06.10.2026

`postgres-recovery-stop` реализует marker3 requested→completed, captured Pod
finalizer и условную PG replicas1→0. Marker intent сохраняется до effects;
terminal regular/init proof — до owned finalizer cleanup. Original checkpoint и
migration неизменны. Existing marker3 только наблюдает, не повторяет отправку
остановки. Неполный dispatch требует отдельного repair; lock не снимается.

| Проверка | Результат |
| --- | --- |
| New stop/uncertainty/record/CLI/overlay cases | 53 PASS |
| Pinned Windows seven modules | 365 PASS,0 SKIP,15.45s |
| Linux frozen OCI/operator19 modules | 850 PASS,0 SKIP,71.45s |
| Exact source before/after / frozen backend | 347 /274 unchanged |
| Ruff /CI YAML /whitespace | PASS |
| Actual Kubernetes1.37 +PostgreSQL17.11 graceful stop | PASS, regular exit0/signal0 |
| Actual init terminated /successful init ready=true | PASS, exit0/signal0 |
| Actual Node Ready /fresh bound Lease | PASS |
| Native intent before conditional stop /proof before finalizer removal | PASS |
| Fresh observer/no new dispatch /checkpoint unchanged | PASS |
| Three owned attempt namespaces UID/RV cleanup/absence | PASS |
| Old template/PVC TLS recovery /CSI fencing /hosted CI | НЕ ВЫПОЛНЕНЫ |

Native proof использует настоящий PG процесс и shipping Manager stop/CAS/observer
на отдельном emptyDir. Полные saved generation/TLS/runtime/Helm gates — fixture
doubles; это не native TLS/PVC recovery. Synthetic trust-only startup является
параметром disposable lab, не production downgrade. Рабочий release и его
Pods/PVC/SQL не изменялись; native_old_tls_recovery_verified=false и
storage_recovery_authorized=false. Source bundle SHA256:
`1606cb0b1076c89e24a10815246a4e2124968d3f8ac779c73b609ddf50ff500b`.
Frozen operator OCI:
`sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`.

Сохранены RED28 missing stop, затем 353 Windows/838 Linux PASS +8 FAIL:
новый default keyword нарушал прежний gate invocation. Исправлен только opt-in
вызов. Следующие 361 Windows/846 Linux PASS не закрыли native gate: два native
attempt отказали до finalizer, namespaces очищены. Mutation-free dry-run подтвердил
`kubectl patch --patch-file -` → open(-)/file-not-found. Final transport использует
bounded fresh object/local narrow tests +UID/RV `kubectl replace -f -`; body только
stdin, unknown fields сохранены. Дополнительные RED2 подтвердили init readiness
и namespace binding; оба исправлены без ослабления exit proof. Final53 cases,
365/850 PASS относятся к exact347 CAS snapshot. Cache_dir warning1 остаётся;
skips0. No commit/push/release.

Deadline stop observation180s; grace≤150s, storage/Node/Lease commands≤30s и
2MiB read bounds. Полные protected gates имеют отдельные bounds; это не SLA
всего CLI. Lost intent/finalizer/scale/proof/cleanup replies не дают writer permit.
SIGKILL/unknown state/partition/API absence без durable proof оставляют lock и
owned finalizer при его наличии. Explicit pre-dispatch repair, real PVC/CSI/node
failure matrix, old native TLS/SCRAM/SQL, session-key provenance и terminal/unlock
OPEN. Full T3/T4–T6 и installedTLS backup3 guard сохранены.


## Stopped old-template restore — 06.10.2026

`postgres-recovery-restore-template`: durable marker4 requested→restored, exact
protected saved-manifest/installed-template hash и один UID/RV conditional replace
при PG replicas0. Original stop proof/checkpoint/migration сохраняются. Existing
marker4 только наблюдает; timeout/pending dispatch не запускают repeat/start/unlock.

| Проверка | Результат |
| --- | --- |
| New template/uncertainty/parser/overlay/CLI/fresh-reader tests | 28 PASS |
| Pinned Windows eight modules | 393 PASS,0 SKIP,20.93s |
| Linux frozen OCI/operator20 modules | 878 PASS,0 SKIP,91.05s |
| Exact source/frozen application before/after | 348 /274 unchanged |
| Ruff /CI YAML /whitespace | PASS |
| Actual Kubernetes1.37 +PG17.11 graceful stop on disposable PVC | PASS |
| Native old-template UID/RV CAS while PG0 | PASS |
| Same StatefulSet/PVC/PV UID /no new Pod | PASS |
| Fresh completed observer/no redispatch /journal unchanged | PASS |
| Owned namespace/PVC/PV deletion and absence | PASS |
| Native old TLS/SCRAM/SQL integrity /new PG start /hosted CI | НЕ ВЫПОЛНЕНЫ |

Native fixture подтверждает реальную остановку и условное изменение template на
том же disposable PVC/PV. Старый template отличается синтетической annotation;
полные saved package/generation/TLS/runtime/Helm gates — doubles. Это не actual
TLS projection rollback, данные после возврата не импортировались и SQL integrity
не проверялась. Synthetic trust-only startup применяется только к лаборатории.
Working release/Pods/PVC/SQL не затрагивались. `new_pod_started=false`,
`native_old_tls_recovery_verified=false`, `storage_recovery_authorized=false`.

Source bundle SHA256:
`8a713381336fb209b402a77121e9519a47d824cb41ce2aea04afcc31bdcc32cd`.
Frozen OCI:
`sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`.
Сохранены RED21 missing method и intermediate67PASS/7FAIL: fixture изменяла
synthetic checkpoint после того, как reader зафиксировал его SHA. Использован
fresh reader; API-double различает stop и stopped-template mutation. Product
checkpoint guard не ослаблен. Ruff unused fixture variable удалена; final28 cases
и393/878 относятся к exact348. Cache_dir warning1 остаётся, skips0.

Следующий этап — отдельный bounded PG start с новым Pod UID, CSI/Node policy и
native old TLS/SCRAM/SQL proof. Current Node/Lease — reachability check, не
атомарный fence от внешнего оператора. Explicit pre-dispatch repair, session-key
provenance и terminal/unlock остаются OPEN. Full T3/T4–T6 и installedTLS backup3
guard сохранены. No release/commit/push.


## Bounded old-PG start — 06.10.2026

`postgres-recovery-start` добавляет marker5 intent перед единственным UID/RV
replicas0→1, обязательный protected storage-fencing-record и first-observed
Pod/Node identity binding до Ready. Existing marker5/fresh reader только наблюдают;
original checkpoint не меняется, app/import/unlock не разрешены.

| Проверка | Результат |
| --- | --- |
| New start/fencing/lost-reply/UID-node-race/CLI/parser tests | 40 PASS |
| Pinned Windows nine modules | 433 PASS,0 SKIP,30.79s |
| Linux frozen OCI/operator21 modules | 918 PASS,0 SKIP,149.16s |
| Exact source /frozen application before/after | 349 /274 unchanged |
| Ruff /CI YAML /whitespace | PASS |
| Actual1.37/PG17.11 disposable PVC stop→old template→new Pod | PASS |
| Same StatefulSet/PVC/PV /new bound Pod UID /SQL seed42 survives | PASS |
| Intent before start /fresh observer no dispatch /checkpoint unchanged | PASS |
| Owned namespace/PVC/PV deletion and absence | PASS |
| Actual old TLS/SCRAM/application integrity /hosted CI /CSI faults | НЕ ВЫПОЛНЕНЫ |

Native proof использует lab-only trust startup, synthetic old-template annotation,
сгенерированную lab fencing attestation и doubles saved package/TLS/runtime/Helm
gates. Контрольная SQL строка подтверждает сохранение volume между Pod, не полную
целостность приложения и не TCP verify-full/SCRAM. Working release не менялся.
`new_pod_ready=true`, `old_tls_native_verified=false`,
`storage_recovery_authorized=false`; lock сохранён до следующих проверок.

Source bundle SHA256:
`8ac9d45a5787b669e8e850afc26e262ed62a47bf0b1746bf9a07f18e5482b9cb`.
Frozen OCI:
`sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`.
Сохранены RED23 missing method; intermediate3FAIL/48PASS stale ConfigMap fixture
reference исправлен fresh marker read. Отдельные RED1 смены first-unready Pod и
RED1 смены Node после scheduling закрыты durable first-observed UID/node binding;
production guards не ослаблены. Final40/433/918 без skips; cache_dir warning1.

TTL решения≤15min актуален перед новым dispatch; historical observer проверяет
тот же SHA на requested_at и не повторяет scale. Фактическое physical/platform
fencing обязателен оператор подтвердить до выдачи protected файла; наличие JSON
не является проверкой CSI/partition. Native old TLS/SCRAM/SQL compatibility,
platform fault matrix, explicit pre-dispatch repair, session-key provenance и
terminal/unlock OPEN. Full T3/T4–T6 и installedTLS backup3 guard сохранены.
No release/commit/push.


## Native old TLS restoration and read-only SQL — 06.10.2026

Fixed recovery native probe и strict bounded verdict реализованы отдельно от
recovery dispatch permission. Marker5, original checkpoint и ordinary fence
не менялись; native completion/writer/app/unlock не разрешены.

| Проверка | Результат |
| --- | --- |
| New fixed command/verdict negative cases | 26 PASS |
| Pinned Windows10 modules | 459 PASS,0 SKIP,29.80s |
| Linux frozen OCI/operator22 modules | 944 PASS,0 SKIP,154.82s |
| Exact operator source /frozen backend before-after | 350 /274 unchanged |
| Ruff /CI YAML /whitespace | PASS |
| Actual1.37/PG17.11 real server-new→server-old Secret/template recovery | PASS |
| Graceful stop/new Pod/same StatefulSet-PVC-PV/SQL42 preserved | PASS |
| Native old leaf/verify-full/SCRAM/transaction read-only/SELECT1 | PASS |
| Wrong CA/password/expected changed leaf rejected | 3 PASS |
| Source274/image identity/client-server projected permissions | PASS |
| CP/marker5 unchanged /fresh observer no redispatch /owned cleanup | PASS |
| Production native Job/completion protocol /application integrity /hosted CI | НЕ ВЫПОЛНЕНЫ |

Сервер действительно возвращает предыдущий immutable TLS Secret с другой CA/leaf,
а client использует previous CA/CRL. Positive Job фиксированного probe делает
только чтение SQL; отдельный lab socket seed42 показывает сохранение PVC между
Pod. SQL schema compatibility/totals/content/application session-key не доказаны.
Full saved package/generation/runtime/Helm gates Manager остаются fixture doubles,
fencing решение — synthetic lab attestation. No working release/Compose/local
stack upgrade. Marker5 storage_ready неизменен; native completion не journaled.

Source bundle SHA256: `1ac520cf7e8a524a4cacded46324bc0d6586ea32e948ba57465fd7e64bf5c522`.
Frozen Docker OCI index:
`sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`.
CRI repoDigests соответствуют этому index и import_index
`sha256:4216972be8981a892d3d93021b49bbfef3405c9f84ccf357a7dfc7b9596848fc`;
config_digest
`sha256:2d07a2dad16e1e2c1ac99b9a7fd081bda9ac5fd627965cab0f043c22919d9d6b`.
Job использует pinned frozen repoDigest; runtime image и274 source повторно
проверены. Repo/index/config не выдаются за один одинаковый digest.

RED26 missing helper сохранён. Native fixture REDs: required PVC placeholders,
missing image references и ErrImageNeverPull по incorrect repository/index alias.
Read-only Helm validation/CRI metadata локализовали причины; изменены только lab
inputs. Chart/schema/native guard не ослаблены. Failed namespaces/PVC/PV очищены,
каждый повтор изолирован. Cache_dir warning1 сохраняется, final skips0.

Следующий этап — durable native Job intent/UID/template/resume/cleanup protocol
с повторами current-state/trust/runtime; затем SQL compatibility/integrity и
session-key provenance перед terminal/app/unlock. Platform fault matrix,
pre-dispatch repair, full T3/T4–T6 и installedTLS backup3 guard остаются OPEN.
No release/commit/push.


## Durable native Job preparation — 06.10.2026

`postgres-recovery-prepare-native` фиксирует exact proposal marker6 перед будущим
Job create. Сам Job не создаётся. Старые markers1–5 readable; prepared resume
повторяет protected generation, TLS/runtime и PG/node/PVC identity; checkpoint
и migration неизменны. Dispatch fence/no-active-writer inventory не ослаблены.

| Проверка | Результат |
| --- | --- |
| New marker6/Job proposal/parser/uncertainty/no-create/CLI tests | 31 PASS |
| Pinned Windows11 modules | 490 PASS,0 SKIP,41.34s |
| Linux frozen OCI/operator23 modules | 975 PASS,0 SKIP,197.78s |
| Exact source /frozen backend before-after | 351 /274 unchanged |
| Ruff /CI YAML /whitespace | PASS |
| Actual1.37/PG17.11 old TLS recovery/read-only native positive +3 negatives | PASS |
| Actual marker6 UID/RV CAS /fresh prepare+resume /no additional Job | PASS |
| Original checkpoint unchanged /same PVC/PV /SQL42 preserved | PASS |
| Owned namespace/PVC/PV cleanup and absence | PASS |
| Production Job dispatch/UID-observer/cleanup/native completion | НЕ РЕАЛИЗОВАНЫ |

Proposal использует real old client/server TLS projection/CA/CRL и pinned saved
backend. Native lab Manager full saved package/generation/runtime/Helm prerequisites
остаются fixture doubles, physical fencing — lab attestation. Current-state
inventory full acceptance и application SQL compatibility/session-key не доказаны.
Actual4 lab Jobs подтверждают TLS/read-only probe независимо; подготовка marker6
не добавила ни одного Job. `native_job_created=false`, completion/writer/unlock false.

Source bundle SHA256: `5dbea5c36112ceea2fb3336333d0c3657c55a3061e886e2711e0870cf6499135`.
Frozen OCI index:
`sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`;
actual CRI imageRef import index4216972b и frozen source274 повторно проверены.
Сохранены initial fixture7ERROR (ошибочное имя helper), RED7 missing method,
intermediate26PASS/1FAIL Windows symlink privilege1314. Windows symlink refusal
проверяется targeted fixture, Linux использует настоящий symlink; guard не ослаблен.
Additional parser empty-old-pods сразу PASS существующего stop binding, новая
runtime гипотеза не принята. Cache_dir warning1 сохраняется; final skips0.

Следующий этап — one scoped native Job dispatch с durable intent и exact captured
UID, active-readonly overlay, bounded verdict observer и owned cleanup. Нельзя
разрешать active Job по role label/name или переносить past verdict после PG/Job
подмены. Schema/SQL integrity, session-key provenance, platform fault matrix,
pre-dispatch repair, terminal/unlock и full T3–T6/backup3 guard OPEN.
Working release/local/Compose не обновлялись; no commit/push/release.

## Durable native Job dispatch — 06.10.2026

`postgres-recovery-dispatch-native`: marker7 create intent before one scoped
Job create, then first observed UID CAS; fresh observer/read-only namespace
overlay after lost reply. Markers1–6 readable; original checkpoint/migration,
local/Compose and working release unchanged. Native completion/writer/unlock false.

| Проверка | Результат |
| --- | --- |
| New dispatch/UID/lost reply/strict reader inventory/CLI/fencing tests | 42 PASS |
| Pinned Windows12 modules | 532 PASS,0 SKIP,57.82s |
| Linux frozen OCI/operator24 modules | 1017 PASS,0 SKIP,289.82 |
| Exact source /frozen backend before-after | 352 /274 unchanged |
| Ruff /CI YAML /whitespace | PASS |
| Actual1.37/PG17.11 old TLS restore +native positive/3 negatives | PASS |
| Actual one Job create /lost response /fresh UID CAS /no redispatch | PASS |
| Exact actual admitted Job and Pod template /native readonly verdict | PASS |
| Same PVC/PV /SQL42 preserved /original checkpoint unchanged | PASS |
| Owned namespace/PVC/PV normal cleanup and independent absence | PASS |
| Production durable terminal verdict /owned Job cleanup /writer/unlock | OPEN |

Native recovery uses real client/server CA/CRL/leaf/key projections and pinned
backend; fifth Job is created through production dispatch, its lost response
is injected after actual API creation. Fresh Manager journals same Job UID,
then repeated dispatch/resume observes it without a second create. Actual fixed
probe finishes with readonly native TLS/SCRAM/SQL verdict, but production marker7
does not persist native completion. Whole package/generation/runtime/Helm gates
remain doubles; application data emptyDir, PG actual owned PVC, fencing lab-only.
Exact readonly application PVC/unknown writer boundaries covered by unit tests.
No target platform/session-key/application schema compatibility acceptance.

Source bundle SHA256: `6b937fea4926c0aa14be07baf1be8a653327ff3e2cf05db2724fb14d88948208`; frozen OCI index866db1ee and actual
equivalent CRI import index4216972b/source274 verified before-after. Capacity
measured before owned proof; no existing service stopped. Native namespace/PVC/PV
normal UID/RV cleanup and absence PASS.

Initial RED13 then RED14/21PASS retained. Expanded34PASS/1FAIL localized to Python
fixture alias between Job/root template labels; JSON API boundary fixture fixed,
production root-label binding retained. Final42 and Windows532 PASS; original generation Windows530/Linux1015 and native proof retained separately.
Valid replacement fencing record exposed version6 original-body-SHA gap and is
now refused for5/6/7; threshold/policy guards unchanged. Known cache_dir warning1,
skips0. CI module added; hosted CI not run. Full T3/T4–T6/backup3 guard OPEN.

Additional RED1 Job/1 existing PASS Pod: duplicated UID could hide a foreign Job in reader filtering. Complete UID and kind/name uniqueness now checked before filtering; two regression cases added, source re-frozen and final suites/native proof repeated.

## Durable native terminal verdict — 06.10.2026

`postgres-recovery-complete-native`: marker8 captures first reader Pod UID/node/
lease before terminal wait; bounded public native verdict journaled after exact
Job/Pod success and repeated prerequisites. Original checkpoint/migration and
working application unchanged. Job is retained; app writer/unlock false.

| Проверка | Результат |
| --- | --- |
| New native completion/Pod UID/terminal/log limits/late reply/lost CAS/CLI tests | 34 PASS |
| Pinned Windows13 modules | 566 PASS,0 SKIP,87.62s |
| Linux frozen OCI/operator25 modules | 1051 PASS,0 SKIP,382.68 |
| Exact source /frozen backend before-after | 353 /274 unchanged |
| Ruff /CI YAML /whitespace | PASS |
| Actual1.37/PG17.11 old TLS restore/native positive +3 negatives | PASS |
| Actual marker8 Pod/node UID/terminal/public verdict CAS | PASS |
| Actual lost native_verified reply /fresh observer+resume /no replay | PASS |
| Original checkpoint /same PVC/PV /SQL42 continuity | PASS |
| Owned namespace/PVC/PV normal cleanup and independent absence | PASS |
| Production scoped Job cleanup /application SQL/session/terminal-unlock | OPEN |

Production fifth Job runs fixed old-client readonly native command. After actual
Job Complete/Pod Succeeded and native verdict, production completion writes
marker8; test injects lost reply after real CAS. Fresh Manager observes exact
same Pod/Node/Job UID and stored verdict, no second logs/SQL/create. Namespace
cleanup is lab resource cleanup, not yet production native Job cleanup protocol.
Whole saved package/generation/runtime/Helm gates remain doubles, app data
emptyDir/PG real disposable PVC and physical fence lab-only. No full installed
generation, application schema/SQL integrity/session-key or platform fault proof.

Source bundle SHA256: `504fd93849f507c275741f9b84fdc076cc66a90b881adfe2fe29b07e3eeae2bf`; source353/frozen274 and frozen OCI866db1ee/
equivalent CRI import4216972b before-after verified. Initial RED14 missing completion
method; expanded32 PASS. Late-first-Pod additional RED1/unscheduled already PASS1:
bounded observer now waits only not-yet-captured Pod; captured disappearance
still refuses. Missing/running negative fixtures advance clock91s, product
budgets unchanged. Final34 cases pass, cache_dir warning1/skips0 retained.
CI module added, hosted CI not run. Local/Compose/working release not upgraded;
no commit/push/release; full T3–T6/installedTLS backup3 guard OPEN.


## Durable owned native Job cleanup — 06.10.2026

`postgres-recovery-cleanup-native`: marker9 intent before exact conditional Job
DELETE; terminal reader UID/name/node retained, Foreground GC observer waits
for both Job and captured Pod absence before cleaned CAS. Original checkpoint
and lock remain; writer/unlock/storage recovery unauthorized.

| Проверка | Результат |
| --- | --- |
| New cleanup/strict marker/UID-node-RV/lost replies/GC/CLI tests | 31 PASS |
| Pinned Windows14 modules | 597 PASS,0 SKIP,113.3s |
| Linux frozen OCI/operator26 modules | 1082 PASS,0 SKIP,460.73s |
| Exact source /frozen backend before-after | 354 /274 unchanged |
| Ruff /CI YAML /whitespace /three guarded documentation mirrors | PASS |
| Actual1.37/PG17.11 old TLS/native positive +3 negatives/marker8 | PASS |
| Actual marker9 intent/UID-RV Foreground DELETE/lost response | PASS |
| Fresh observer/both Job-Pod absence/no repeated DELETE | PASS |
| Lock/original checkpoint/same PVC-PV/SQL42 continuity | PASS |
| Owned namespace/PVC/PV normal cleanup and independent absence | PASS |
| Application SQL/schema/session-key/platform/terminal-unlock/full T3–T6 | OPEN |

Actual lost response injected AFTER conditional production DELETE, then a fresh
Manager observes exact cleanup intent and GC; no second DELETE. Fresh cleaned
resume checks both absence/full prerequisites and leaves original checkpoint/
lock. Four independent lab probe Jobs remain until normal owned namespace
cleanup, fifth production reader removed by the new protocol. Full saved
package/generation/runtime/Helm native gates doubles, application data emptyDir,
PG real disposable PVC, physical fencing lab-only. No full application integrity,
session-key or platform fault proof. No working release upgrade/commit/push.

Source bundle SHA256: `42d471adcfcb261b2f6205b034d04e36f4b26424ad00be251e4c22202f8102ed`; source354/frozen274/OCI866db1ee and verified
CRI import4216972b checked. RED12 missing cleanup/CLI retained; expanded RED1/
30PASS exposed lost captured node binding in read-only GC helper. Captured name/
UID/node comparison added, then31 PASS. Intermediate Windows563 run omitted
prior34 completion cases due harness rename; result retained and NOT accepted.
Explicit full test lists corrected to include completion and cleanup modules;
Windows597/Linux1082 accepted only after rerun. Private suite harness timeout
420→600s covers additive suite, product90/30/60 budgets unchanged. cache_dir
warning1, skips0. CI module added; hosted CI/full coverage/release not run.


## Protected session rotation readonly prerequisite — 06.10.2026

User explicitly permits rotation with mandatory re-login. New readonly command
checks independently protected public issuer evidence/current runtime Secret,
never performs rotation or grants native session/writer/unlock authorization.

| Проверка | Результат |
| --- | --- |
| New record/mechanism/controller/CLI/decision/late-expiry cases | 93 PASS |
| Pinned Windows18 modules | 690 PASS,0 SKIP,184.0s |
| Linux frozen OCI/operator30 modules | 1175 PASS,0 SKIP,718.37s |
| Exact source /frozen backend before-after | 358 /274 unchanged |
| Ruff /CI YAML /whitespace /3 guarded docs | PASS |
| Synthetic old cookie rejected/new sign-in/OIDC HMAC changes | PASS |
| Actual disposable immutable runtime Secret new UID/key, DB credentials unchanged | PASS |
| Product repeated readonly check/wrong anchor refusal/Secret UID-RV binding | PASS |
| Lock/checkpoint/Job count unchanged; owned namespace/PVC/PV cleanup and absence | PASS |
| Actual provider rollback prevention/native SSO/app SQL/full T3–T6 | OPEN |

Home rotation is performed by synthetic lab issuer via exact UID/RV conditional
DELETE/recreate of ONLY owned runtime Secret in disposable namespace. No working
application, provider or production Secret is touched. Product command is readonly;
full saved package/generation/runtime/Helm gates doubles, app data emptyDir/PG
actual PVC. Actual provider trust/rollback guarantee and native production login
not proven. Public session_rotation_native_verified/storage_recovery_authorized
remain false. Durable session decision is checked below; native login/SQL/integrity/terminal/unlock OPEN.

Source bundle SHA256: `198bcee8fe6a5c7c26d8691eb5ca2332d1bc1ec803713673ff8b095be07e14a7`; original RED37 missing validator32.00s and
RED16 missing controller/CLI13.19s retained; focused40PASS32.64s/16PASS13.50s.
Mechanism initial2PASS/1FAIL was fixture module loading/dataclass sys.modules;
fixed actual import with path assertion. HTTP ASGITransport replaces deprecated
TestClient adapter without dependency changes; mechanism3PASS0.60s. Private CI
append initially failed on backend/tests prefix (workflow runs in backend), then
only tests/ path corrected. Product functions not duplicated. cache_dir warning1/
skips0; budgets unchanged. Hosted CI/full project/coverage/release not run.

### Durable session decision and aggregate verification

| Проверка | Результат |
| --- | --- |
| Separate public decision1/CAS response binding/no overwrite | PASS |
| Actual API lost CAS response/fresh observer/no second write | PASS |
| Native9/checkpoint/runtime Secret/remaining Job count retained | PASS |
| Late issuer expiry after final full prerequisites | RED1 retained, fixed and PASS |
| Recorded historical decision/current Secret/current Lease gates | PASS |
| Original Linux observer600 timeout without captured full result | FAIL retained, not acceptance |
| Final owned container ID/image/mount/labels/command/full logs/state | PASS |
| Final exited-only normal removal; independent namespace/PV absence | PASS |

Dedicated public decision field leaves native marker9/checkpoint2/kit3 unchanged.
Existing decisions are observed without overwrite; expiry is evaluated at the
stored decision time, while current runtime identity and all recovery gates
repeat. Strict bounded CAS response with wrong UID/name/kind/same RV refuses even
if the write happened; fresh observer reads actual persisted decision. No writer
permission follows; native session/login/provider rollback proof remains OPEN.

Expanded decision test26PASS/1FAIL was a stale mocked Lease after clock advance.
The fake Lease lives in raw GET closure; fixture now simulates continuous renewal
of the same captured Lease. Production freshness guard unchanged. Initial RED14
missing decision and RED1 late expiry retained. Intermediate source356/Windows653
and native readonly proof were not accepted because final expiry defect and
missing Linux aggregate evidence remained.

Final Linux detached observer retains exact owned container ID/state/full logs,
image/config/mount/labels/command and exit0/Pid0/OOMfalse/error-empty. Only after
full1175 PASS/0skip is exact exited container normally removed. Observer900s is
private additive-suite infrastructure, not a product or performance budget.
Original timeout600 remains FAIL. No foreign resources or working application
changed; hosted CI/full project/coverage/release remains unverified.

Final source358 first native run failed before CAS: bounded stdout transport refused stdin. Fake unit kube bypassed Manager.run. Actual subprocess RED1/8PASS reproduced it; bounded input<=2MiB is now permitted only for exact active CAS argv+bytes, threaded stdin/stdout, timeout/overflow/error/nonzero refuse, stderr discarded. Nine new real transport cases retained, failed native proof cleaned normally. Windows681 before fix is intermediate, not final acceptance. Product budgets unchanged.

Detached observer first compared Docker Image to config digest and refused. Containerd-backed Docker exposes the exact frozen OCI index instead. Fresh content SHA graph index/platform/config plus strict image descriptor verified it; same captured ID observed without restart and original900s deadline retained. This harness failure is retained.

## Readonly application SQL revision foundation — 06.10.2026

| Проверка | Результат |
| --- | --- |
| New pure/inline execution native failure boundaries | 50 cases |
| Pinned Windows focused module | 50 PASS,0 SKIP,0.15s |
| Frozen Linux focused module | 50 PASS,0 SKIP,0.08s |
| Exact source/frozen backend before-after | 360/274 unchanged |
| Actual old-image graph /equal head /native TLS-SCRAM-readOnly | PASS |
| Actual missing/changed/empty/multiple DB revisions | Refused, expected exit1 |
| Owned Job/Pod terminal UID/image binding/SQL42/journals unchanged | PASS |
| Normal own namespace/PVC/PV cleanup/independent absence | PASS |
| Real application structural schema/data/native login/durable reader/full T3–T6 | OPEN |

The schema version table is synthetic lab data: graph head was read from the
trusted frozen image, then an equal or invalid revision inserted ONLY in disposable
PostgreSQL to exercise the readonly probe. No actual application schema migration
or compatibility acceptance follows. Public schema_structure_verified=false,
storage_recovery_authorized=false. Product foundation has no Job/create/exec/
app/unlock permission; kit/consumer/CI integration and durable reader remain OPEN.

Native generation/runtime/Helm gates are fixture doubles; app data emptyDir and PG
real disposable PVC, issuer/fencing synthetic lab-only. Fixed old image OCI/CRI
identity and source hashes checked. New focused50/50 are additive isolated-module
checks, not a rerun of earlier aggregate source358690/1175. Source bundle SHA256:
3ea8c5fb14713dc31f8f6a794f06ba0d7a86bc75c0cec1660a19616c09b60f48. Initial collection quoting error fixed before RED30 missing
module0.31s; GREEN30/0.14s, expanded50/0.18s. Product deadlines unchanged; warnings1,
skips0. Hosted CI/full coverage/release not verified.

## Separate public SQL reader preparation — 06.10.2026

| Проверка | Результат |
| --- | --- |
| New record phases/bindings/chronology/controller/CAS/CLI | 71 cases |
| Pinned Windows21 modules | 811 PASS,0 SKIP,195.06s |
| Frozen Linux33 modules | 1296 PASS,0 SKIP,797.58s |
| Exact source/frozen backend before-after | 361/274 unchanged |
| Existing schema3 archive member set /kit/release checks | Preserved |
| Actual prepared field CAS /lost reply /fresh observer /no second write | PASS |
| Reserved SQL Job absent /native9-session1-checkpoint unchanged | PASS |
| Actual frozen old-image SQL graph/missing/equal/changed/empty/multi | PASS |
| Own namespace/PVC/PV cleanup /independent absence | PASS |
| Captured Linux container image/mount/label/command/log/state/exited-only removal | PASS |
| Dispatch/terminal/cleanup producer /structural schema/native login/full T3–T6 | OPEN |

Decoder RED53 missing implementation1.32s; GREEN1031.29s. Chronology RED2/1.04s fixed
with unchanged5s skew, GREEN1051.31s. Preparation RED14/15.54s; intermediate10PASS/
4FAIL were stale fixture ConfigMap pointers after CAS; refresh actual object, not
production gates. Positional manifest route RED1/1.51s fixed for actual CLI shape;
CLI RED1/0.35s before routing. Final controller16/19.41s/Ruff PASS. Archive exact
member set unchanged by moving owned standalone SQL code into existing TLS member.
Windows kit/release165PASS/1POSIX SKIP/3.95s was intermediate; final required Linux
includes that skipped case. Aggregate source361 never uses prior counts as new proof.

First native launch after continuation failed before cluster action because /tmp
bundle disappeared. Read-only uptime/boot evidence confirms recent VM reboot; no
claim about its power-cause. Failed runner had no live predecessor, source bundle
SHA was retransferred unchanged to a fresh directory. Repo bind uses --mount,
so missing source fails before launch. FAIL retained, no product deadline change.

Native SQL version table remains synthetic; native generation/runtime/Helm gates
doubles, issuer/fencing lab-only, app data emptyDir and actual PG PVC. Prepared
field creates no Job/SQL/app/unlock permission. Later record phases are readable
only and not producer acceptance. Full structural/schema/data/native login/
T3–T6/backup3 guard remain OPEN. No working release upgrade/commit/push.

Source bundle SHA256: a3e040940a37f2b04e599e25c39f2569ea3c4c2ef0344b336f68e19d12aeb9f2. Private additive-suite observer900s,
product60/90/30 unchanged, cache_dir warning1/skips0. Hosted CI/full project/
coverage/release remains unverified.


## T3 SQL reader dispatch acceptance (06.10.2026)

Windows871 PASS/0skip/268.3s/24 modules; Linux1356 PASS/0skip/
349.67s/36 modules. New60 identity/controller cases, source364 and
frozen backend274. Bundle SHA256 619b7acff3b092e73ea76b610ebace34afa07c375110fe6617cb7a7f17f2acf6. Project Ruff configuration,
CI references and whitespace checked. Identity RED28 missing helpers -> GREEN34;
dispatch RED14 missing method -> GREEN14; expanded21PASS/1CLIRED -> final22PASS.
Initial pytest invocation without explicit basetemp failed in fixture setup and
was not counted as behavioral RED. A root-level Ruff invocation used another
configuration and failed; explicit repository backend configuration PASS.

Actual disposable Kubernetes/PG17 old pinned backend SQL Job: prepared->intent
CAS before create, lost reply after actual create, fresh observer actual Job UID
CAS, subsequent observer no create/CAS, exact owner/proposal/Pod/scheduler/native
terminal/strict readonly revision/leaf proof and original native9/session1/CP/SQL42
continuity PASS. Original six standalone SQL acceptance cases are also exercised.
Public SQL record remains job_observed/verdict=null; no product terminal/schema
or writer authorization is inferred. Normal owned namespace/PVC/PV cleanup and
independent absence PASS; working application unchanged.

Whole native generation/runtime/Helm gates fixture doubles; application data
emptyDir, database version table synthetic, issuer/fencing lab-only. This proves
product dispatch and strict reader identity, not real structural schema/data/FS/
Qdrant/outbox integrity, native login/provider rollback, CSI fencing, full managed
recovery or TLS rollout. Durable SQL Pod/Node/verdict/cleanup, terminal/unlock,
pre-dispatch repair, T3–T6/backup3 guard and hosted CI remain OPEN.


The preceding source363 Linux run passed all1352 cases in1037.40s, but failed the
unchanged900s observer limit. That failure and the original container identity,
full logs and normal exited-only removal remain retained; it is not acceptance.
A single-test Linux cProfile identified138 repeated TLS module loads (1.698s
of6.174s). Loading the protected operator module once per lifecycle instance,
with serialized first initialization and no publication after a failed execution,
reduced that component to0.013s (total4.398s). Cluster reads, runtime/generation
gates and protected evidence verification are not cached. Four regression cases
cover reuse, independent namespaces, concurrent first calls and failed-load retry.
The fresh source364 acceptance still requires the same900s observer limit.


## T3 SQL completion acceptance (06.10.2026)

Source365/frozen274, bundle SHA256 980c48c603037725fd88b743a2bced4f4b6dbf26c85a351930e6cdb93a5b8f5f. Windows899 PASS/0skip/
312.16s/25 modules; Linux1384 PASS/0skip/410.78s/
37 modules. New28 cases: original17 behavioral RED (missing method), nontransition
lock context5RED/1PASS, CLI1RED, then isolated28PASS and worktree84PASS. Project
Ruff/CI refs/whitespace PASS. Independent review no blocking findings; separate
same-Pod unscheduled->scheduled->verified check PASS, one create/one logs, no edits.

Actual disposable old-image Kubernetes SQL reader: first Pod/name/UID and Node/
Lease capture, terminal/read-only equality, verified CAS, lost response and fresh
verified observer without log read/create/replace/delete PASS. Original native9,
session1/checkpoint and SQL42 seed unchanged. Public completion record retained in
private evidence. Normal owned namespace/PVC/PV cleanup plus independent absence
PASS. Full generation/runtime/Helm gates are doubles; issuer/fencing lab-only,
appdata emptyDir and version table synthetic. This does not prove structural schema,
real data/FS/Qdrant/outbox, native login/provider rollback, CSI fencing or full recovery.

SQL product cleanup (not namespace proof cleanup), terminal/unlock, pre-dispatch
repair, full T3-T6/backup3 guard and hosted CI remain OPEN. Source363 observer900
failure and source364 causal fix/accepted new run remain retained unchanged.


## T3 SQL cleanup acceptance (06.10.2026)

Source366/frozen274; bundle SHA256 a2c1863857012f9e92dca027f0f017f6db42d74362b527b971e244d0588e6515. Windows939 PASS/0skip/
340.29s/26 модулей; Linux1424 PASS/0skip/549.73s/
38 модулей. Новые40 cases: initial11 behavioral RED→GREEN, расширенные36 PASS,
CLI1RED→38PASS, затем постоянные сценарии замены Job/Pod после intent и40PASS.
Focused worktree40 PASS/82.60s. Независимый review без blocking findings; отдельная
проверка замены Job после intent PASS, DELETE0, intent/lock сохранены. Ruff/CI refs/
whitespace PASS.

Настоящий Kubernetes: cleanup intent→UID/RV Foreground DELETE→потеря ответа→fresh
observer без повторного DELETE→отсутствие Job и Pod→cleaned CAS PASS. Последующий
observer не выполняет create/delete/logs/CAS. Сохранены native9/session1/checkpoint
и SQL42 seed; public cleaned record сохранён в private evidence. Owned namespace/
PVC/PV очищены штатно; независимая проверка отсутствия PASS. Свежий image content
graph проверен; unit-контейнер удалён только после штатного exit в пределах900s.

Generation/runtime/Helm native gates — doubles; issuer/fencing лабораторные,
appdata emptyDir, version table синтетическая. Структура SQL, данные/FS/Qdrant/
outbox, native login/provider rollback, CSI fencing, terminal/writer/unlock,
pre-dispatch repair и полный T3–T6/backup3 guard/hosted CI остаются OPEN. Исходный
source363 observer900 FAIL и source364 causal fix сохранены без изменения оценки.


## T3 SQL structure foundation acceptance (06.10.2026)

Source367/frozen274; bundle SHA256 561735756d8be820b6ba9ba6e2c455e3014044bc7afe9a4c47ab0a6b1f9b0827. Windows1006 PASS/0skip/
362.76s/27 модулей; Linux1491 PASS/0skip/574.0s/39
модулей. Новые67 cases; reference26 RED, catalog13 RED, fixed probe16 RED,
затем bounded/type/deadline/parity cases PASS. Последний focused67 PASS/0.25s.

Нативная PostgreSQL17.11 в отдельном owned internal Docker network: эталон создан
реальными миграциями старого образа, clone equality и13 структурных повреждений
(включая rewrite rule) проверены read-only — всего14 cases. Фиксированная программа
через настоящие verify-full/SCRAM: equal PASS, wrong leaf/schema/reference/head/
rewrite rule FAIL с точным публичным {"ok":false}, exit1 и пустым stderr —6 cases.
Реальные данные приложения не использовались. Эталон неизменен после каждого case.
PKI volumes, containers и networks удалены штатно; независимое отсутствие всех
созданных proof scopes и свежий OCI content graph PASS.

Сохранены исходные ошибки: reviewer P1 rule omission подтверждён behavioral RED;
исправление с relhasrules сначала ошибочно отвергало схему после DROP RULE.
Нативная диагностика показала relhasrules=true и rewriteCount0; исключён только
исторический lazy flag, фактический pg_rewrite остаётся обязательным. Попытка
cross-schema sequence ownership была отвергнута самой PostgreSQL до collector;
это ограничение эксперимента, а не product behavioral RED. Initial Windows wrapper
FAIL до collection из-за неверного пути теста сохранён; исправлены только пути
harness, использован новый каталог и новый frozen bundle. Пороги не менялись.

Существующий native Kubernetes recovery/cleanup повторно PASS с original
native9/session1/checkpoint/SQL42; полные generation/runtime/Helm gates остаются
doubles, issuer/fencing лабораторные, appdata emptyDir и version table synthetic.
Структура проверена самостоятельным probe, не product Kubernetes structural Job.
Trusted producer/promotion/durable structural controller, роли/grants, данные/FS/
Qdrant/outbox, native login/antirollback, CSI fencing, terminal/writer/unlock и
полный T3–T6/backup3 guard/hosted CI остаются OPEN. Исходный source363 observer900
FAIL и последующая causal correction сохранены без изменения оценки.


## T3 durable structural reader acceptance (07.10.2026)

Source377/frozen274; bundle SHA256 53842ff31251a1dfc21063d0fa2843ce878d83497e2545c3fc8da8044f492ac7.
Windows1258 PASS/0skip/344.45s/36 модулей;
Linux1743 PASS/0skip/714.22s/48 модулей. Новые252 cases
(structural200/cache26/wait26); независимые ревью без блокирующих findings.
Ruff/CI references/whitespace проверены; hosted CI не запускался.

Actual isolated Kubernetes Job на реальной схеме миграций pinned старого образа:
disabled FK trigger отвергнут, exact restoration принята; durable intents,
Job/Pod/Node/Lease, CAS/lost replies, fresh observers без replay, Foreground
UID/RV cleanup и отсутствие Job вместе с Pod PASS. Native9/session1/checkpoint и
старый SQL42 seed сохранены. Fixed offline producer: две независимые fresh PG,
byte-identical references, повтор на непустой БД refused, каталог неизменен.
Owned namespace/PVC/PV и завершившиеся контейнеры очищены штатно;
независимое отсутствие и свежий OCI content graph PASS.

Причины задержек проверены раздельно. cProfile старого unit протокола:
737 YAML parses/7.566 из12.99 CPU секунд; immutable bounded manifest cache
снизил полный протокол до4.924 CPU секунд. Это не решило native600:
source375 late672.32s и source376 late641.98s — функциональные PASS после
непройденного времени. Original FAIL остаются сохранены.

Низконакладная диагностика source376: полный сценарий643.54s, CPU33.507s;
2790 bounded reads189.087s. Отдельное парное измерение одинаковых свежих
readonly Kubernetes чтений:40/40 pairs faster, old median63.995ms/new37.430ms,
UID/resourceVersion/output SHA совпадают. Изменено только ожидание дочернего
процесса. Новый source377 проходит исходный native600 observer за
579.84s (сам сценарий568.56s, штатное удаление контейнера580.92s);
бюджет и runtime guards не увеличены. Количество обычных status polls может
меняться со временем завершения Job; обязательные проверки не сокращены.

Generation/runtime/Helm native gates — doubles; issuer/fencing лабораторные,
appdata emptyDir. Structural verdict касается public catalog, не всех данных.
Generic owned runner/protected promotion, roles/grants/global settings,
FS/Qdrant/outbox, native login/OIDC после явной ротации/antirollback, CSI fencing,
terminal/writer/unlock, pre-dispatch repair и full T3–T6/backup3 guard OPEN.
Working application/local/Compose не обновлялись; commit/push/release не выполнялись.
Source363 observer900 и source375/376 observer600 FAIL сохранены без переоценки.


Проверка package layout, 07.10.2026. После переноса source377 найден collection ERROR: три теста импортировали соседний helper без относительного пути. Исправлены только эти три импорта; существующий tracked пустой tests/__init__.py включён в manifest source378. Runtime-файлы побайтно совпадают с принятой версией377. Windows: collection1258 PASS, smoke58 PASS/3.00s, изменённые модули37 PASS/1.39s; Linux: collection1743 PASS и focused89 PASS/5.33s. Независимое ревью:37 PASS и collection1258 PASS, блокирующих замечаний нет. Завершённый собственный Linux-контейнер удалён штатно, независимое отсутствие ресурсов проверено.

Первоначальный collection ERROR сохранён; ошибочное сообщение wrapper о58 PASS до исправления явно отозвано (фактически0). Полный runtime/native matrix для test-only изменения не повторялся: используется ранее принятый source377. Hosted CI, generic owned builder/protected promotion и полная приёмка T3 остаются OPEN. Разрешение на запуск приложения и снятие запрета записи не выдано этой проверкой.


## T3 owned offline schema builder (07.10.2026)

Общий сборщик принят в source392r5: pinned Linux Docker engine, две новые
собственные tmpfs БД, fixed producer/repeat/read-only catalog, фактическая
root+platform identity, atomic write-once artifacts, bounded CLI и внешний
SHA verifier. Прежний runtime prefix source378 не изменён; allowlist/release
schema3 сохранены. [Операторская инструкция](../../KUBERNETES_SCHEMA_REFERENCE.md)
содержит доверенную границу pipeline и ручное исключение удаления только
never-started own scratch (created/Pid0/нулевые времена/no endpoint/tmpfs).
Это не автоматический delete/replay и не разрешение для ever-started ресурса.

Новая часть406 cases, focused509 PASS/0skip: Windows4.57s/Linux10.64s;
collection Windows1664/50модулей и Linux2149/62модулей. Actual native two-run
23.16s, byte-identical reference, repeat refusal/unchanged catalog/normal cleanup
и independent absence PASS. Reference SHA256
a8dad7c2e7175d877b85f3ee4a0456f3f83b3e498f23a27877cba3b1a59d80fa, build SHA256
e667dbae45c194284815658f7572182f5abc8104bd3c5f036aa385743ad2d67a — функциональные
локальные артефакты unpromoted, а не independently protected expected SHA.

Kit archive воспроизводим, TLSmodule251700bytes, прежний набор12 source members.
Windows/Linux compatibility64 PASS+1skip из-за отсутствия Helm/kubectl в unit
окружении. Отдельно настоящий pinned toolbox bootstrap выполнил consume,
извлечённый release overlay/lifecycle и новый verify-build без source checkout
в рабочем каталоге; неверный SHA отвергнут. Synthetic revision не опубликован.
Первичный kit observer ожидал старые counts2149/509 и завершился assertion:
исходный FAIL сохранён, фактические64 PASS+1skip отражены отдельно; собственный
завершённый контейнер проверен и удалён штатно, отсутствие подтверждено.

Ревью r2 выявило два P2: default network Options и гонка terminal reader/network
inspect. Исправлены с RED→GREEN; реальные следующие r3/r4 выявили root Image
binding и deprecated stop flag stdout. Исправления отдельно RED→GREEN и новый
r5 native PASS; прежние FAIL не переоценены, post-stop network lag не подтверждён.
Actual tests Linux/amd64/API1.54; native arm64/ранние engines не приняты.

Protected promotion/hostedCI/fullT3–T6 OPEN. Roles/grants/settings, реальные
данные/FS/Qdrant/outbox, all-provider sessions/native login/OIDC/antirollback,
terminal/appwriter/unlock и operator pre-dispatch repair остаются следующими
этапами. APP_SECRET_KEY разрешено явно ротировать, все пользователи входят
заново; одного решения о ротации недостаточно для старта приложения.
Полный прежний runtime matrix source377 не повторялся: прежний prefix побайтно
сохранён. Working app/local/Compose, commit/push/release не изменялись.


## T3 all-provider session maintenance foundation (07.10.2026)

- [x] Отдельная backend функция/CLI: preview, strict bool, удаление всех AuthSession
  и OidcLoginFlow одной транзакцией, zero remaining/return after commit, rollback
  при частичном/commit сбое. Старый OIDC-only helper и auth service не изменены.
- [x] Один защищённый runtime-file snapshot, Settings/JSON validation без ambient
  config; PG* и worker hints изолированы через подключение/транзакцию. Cached
  DB engine/factory отвергнут, отсутствующая SQLite не создаётся.
- [x] Windows/Linux43 PASS/0skip, actual15 PG behavior cases+2 SQLite missing-file
  refusals, real PG17.11 CLI с чужими ambient PGHOSTADDR/PGPORT/password/workers,
  readonly preview/lock refusal1.008s, native19.86s/normal cleanup/independent absence.
- [x] Независимый review:29 PASS на исходном396r1; P1 libpq reroute/P2 workers/
  P3 SQLite preview create воспроизведены13RED30PASS и исправлены43GREEN.
  Первая версия/FAIL сохранены; scoped Ruff PASS.
- [x] [Операторская инструкция](../../KUBERNETES_SESSION_RECOVERY.md) объясняет
  разрешённую ротацию, прикладные сессии и ограничения поставки.
- [ ] Durable Kubernetes intent/Job/UID/template/CAS observer/receipt/cleanup
  и binding к rotation decision; native provider login/новая сессия, независимые
  key provenance/Secret и DB antirollback, terminal/writer/unlock/fullT3–T6.

Подписанная cookie при новом ключе отвергнута; возврат старого ключа до удаления
серверной записи восстанавливает доступ, после удаления — нет (четыре providers).
Это не доказательство native login/DB rollback protection: старый backup может
вернуть AuthSession. Нужны отдельные protected key/restore gates перед app start.
Новые backend helper/CLI проверены с read-only source mount и pinned runtime
dependencies, а не новым опубликованным образом. Old image не получает скрипт
автоматически; operator archive schema3 не расширен. Local/Compose/working app
и домашние кластеры не обновлялись; commit/push/release/hostedCI не выполнялись.


### Подготовленное намерение удалить сессии (07.10.2026)

Проверка `decode_session_invalidation_intent` принимает только фазу `prepared`
после `cleaned` проверки структуры БД. Она связывает checkpoint/recovery,
записи native recovery, решение о ротации, runtime Secret UID/RV, SQL/структуру
и независимо заданный SHA эталона. Job UID, результат и cleanup должны отсутствовать;
`all_provider_sessions_invalidated` и `storage_recovery_authorized` равны `false`.
Это только проверка записи: Job, SQL и запуск приложения ею не разрешены.

Windows203 PASS/18.14s и Linux203 PASS/26.79s, без skips; независимое review
203 PASS/17.17s и57 повреждённых записей отклонены. Прежний CLI/main AST сохранён,
schema3 archive воспроизводим. Исходные RED и ошибки test harness сохранены.
Нормализованный maintenance Job, durable CAS/create/observe/commit receipt/cleanup,
реальный повторный вход, Secret/DB antirollback и terminal/writer/unlock остаются OPEN.


### Fixed maintenance SQL program и привязка Job (07.10.2026)

Принят фиксированный self-contained writer/strict commit receipt. Он проверяет
сертификат именно SQL-соединения до записи через PGconn/PQsslStruct/OpenSSL,
поддерживает подтверждённый Linux binary libpq18 runtime и отказывает на
неподдерживаемом ABI. Image graph загружается из установленного `/app`.
Неизвестные PG defaults/URL overrides отвергаются; TLS verify-full/SCRAM и
bounded read-only catalog/head проверяются до отдельной транзакции READ WRITE.
Две auth-таблицы блокируются, удаляются целиком, zero remaining проверяется
до commit; публичный результат выдаётся после commit/закрытия соединения.

`session_binding_sha256` — SHA validated prepared projection в domain
`session-maintenance-binding-1`, исключающий только `job_template_sha256`.
Это позволяет включить binding в command без цикла SHA. Сам template SHA
по-прежнему обязателен в полном intent и проверяется независимо: projection
не разрешает его подмену. Helper проверяет всех родителей прежде вычисления.

Source399: Windows312 PASS/28.36s, Linux312 PASS/39.88s, без skips.
Actual PG17 TLS native53.77s: peer fingerprint/bad leaf/bad CA,
PGHOSTADDR refusal, lock timeout1.82s, catalog drift отказ,
настоящий partial DELETE rollback, все6 provider variants/expired и flows удалены,
контрольный chat сохранён. Fresh read-only same-peer/absence/schema PASS,
own normal cleanup и независимый absence PASS. Fault-trigger reference —
только synthetic test fixture, без protected promotion. Scoped Ruff PASS.

Независимый review исходного398r1:61 PASS, findings none. Native обнаружил
working-directory импорт; regression13 RED→61 GREEN. SHA-cycle устранён
отдельно7 RED→66 GREEN. Исходные native/harness FAIL сохранены; неправильная
гипотеза Config-constructor CWD отозвана. Full runtime/hosted CI не повторялся.
Production dispatch/CAS, normalized Job, receipt persistence/cleanup, caller
fencing/schema writer exclusion, key/DB antirollback, real login и app start
остаются OPEN. Commit ambiguity не разрешает автоматический повтор.


### Durable controller удаления сессий (07.10.2026)

Принят компонент407: нормализованный maintenance Job и журнал
`prepared → create_requested → job_observed → pod_observed → verified →
cleanup_requested → cleaned`. Перед единственным create/Foreground DELETE
сохраняется намерение через exact UID/RV CAS. Потеря ответа переводит следующий
запуск в наблюдение; автоматический повтор SQL/create/delete запрещён.
Полные checkpoint/native/rotation/revision/schema/reference и runtime Secret
UID/RV связаны с командой и template SHA. Проверяется весь inventory до фильтрации,
точные Job/Pod UID/owner и свежая Node Lease. Receipt сохраняется только после
terminal exit0 и commit; cleanup требует повторного отсутствия Job и Pod.
`storage_recovery_authorized=false` сохраняется во всех результатах.

Windows:382 PASS/112.59s, без skips. Linux: те же382 проверки в двух
последовательных наборах —259 PASS/138.48s и123 PASS/57.22s; каждый укладывается
в прежний180s лимит. Это не приёмка одного совмещённого Linux-прогона за180s.
Независимое review:108 PASS/70.81s, подтверждённых замечаний нет.

Отдельный native-тест Kubernetes1.37/PG17 на временном локальном стенде:
541.56s при600s лимите. Каждый прежний родитель создан настоящим controller
по одному разу; новые проверки потери ответов prepared/create/verified/DELETE
и observers без повторных effects сохранены целиком. Actual Job проверил
сертификат своего SQL-соединения, удалил6 provider variants/expired session
и OIDC flow; контрольный chat и данные сохранены. После commit и GC отдельный
read-only Job подтвердил zero sessions/flows, прежние head/catalog и same SQL peer.
Независимо проверено отсутствие собственного namespace/PV/wrapper после cleanup.

Два полных native-прогона превысили первоначальный600s лимит и остаются FAIL.
Поздний второй результат отказал из-за отсутствующего импорта в тестовом fresh
reader; ошибка воспроизведена до SQL и исправлена только в harness. Первый
выделенный прогон отказал на лишней переменной подготовки; результат сохранён.
Отдельная компонентная проверка не заменяет полный native matrix и не закрывает T3.
Прежний Linux406 FAIL был вызван устаревшей Lease в FakeAPI: causal test подтвердил
отказ на31s; fixture моделирует heartbeat, отрицательные31s/+6s и Lease UID cases
сохранены. Production freshness gate не ослаблен.

Публичный operator CLI, независимый durable fresh-empty reader в поставляемом
коде, key/Secret и DB antirollback, реальный provider login, explicit repair
до dispatch/после неопределённого commit и terminal/app-writer/unlock остаются OPEN.
Saved runtime/Helm/external fencing в native-тесте лабораторные; appdata emptyDir,
PostgreSQL использует настоящий выделенный PVC. Домашние кластеры и действующее
приложение не обновлялись. Полный T3–T6, backup3 guard, hosted CI и выпуск OPEN.


### Отдельная fixed read-only проверка auth-таблиц (07.10.2026)

Принята основа408: `session_absence_command` и strict
`session_absence_verdict`. Программа запускается отдельным процессом, открывает
новое SQL-соединение и проверяет сертификат именно его PGconn, TLS/SCRAM,
installed image heads и прежний эталон каталога. В едином repeatable-read
READ ONLY snapshot проверяет zero `auth_sessions`/`oidc_login_flows`.
Оставшиеся записи, включая expired, приводят к отказу. DML/DDL, locks и
READ WRITE отсутствуют; receipt описывает наблюдение, не выполненный commit.
Успех выдаётся после завершения транзакции и закрытия соединения.
Прежние writer/controller/CLI сохранены; `storage_recovery_authorized=false`.

Windows183 PASS/11.82s, Linux183 PASS/13.51s, без skips. В Linux driver
осталось прежнее имя sentinel: исходный wrapper gate FAIL сохранён отдельно
от183 PASS; свежая проверка правильного marker/source/identity и cleanup PASS.
Независимый review:183 PASS/10.37s плюс34 дополнительных fault cases,
подтверждённых замечаний нет. Scoped Ruff PASS.

Actual PG17/TLS native27.98s при180s лимите: same SQL peer/new process,
отказ на nonempty providers, expired session, login flow, wrong leaf/CA,
PGHOSTADDR, head/catalog drift; auth-данные при чтении не менялись,
контрольный chat сохранён. Прежний отдельный writer удалил записи только
в выделенной лабораторной БД. После cleanup независимо проверено отсутствие
созданных PostgreSQL/reader/network. Native fixture использует synthetic
parent journal SHA; это не durable Kubernetes fresh-empty gate или promotion.

Следующий этап: самостоятельный нормализованный read-only Job с полным
cleaned-parent binding, durable prepare/create/observe/verdict/Foreground GC
и строгим inventory scope. Caller обязан независимо проверить весь
родительский journal и fencing до dispatch. Затем нужны CLI/archive integration,
key/Secret и DB antirollback, native provider login, explicit repair,
terminal/app-writer/unlock и полный T3–T6. Прежние полные600s FAIL остаются FAIL.
После интеграции407 дополнительный рабочий suite72 PASS/116.43s.
Основной код, local/Compose и домашние кластеры не менялись; commit/push/release
и hosted CI не выполнялись.


### Durable confirmation of empty auth tables and cleanup boundaries (07.10.2026)

Принят компонент421R3: отдельный read-only Job после `cleaned` удаления сессий,
с фазами `prepared → create_requested → job_observed → pod_observed → verified →
cleanup_requested → cleaned`. Проверяются полная родительская цепочка и SHA
всего cleaned журнала удаления, runtime Secret UID/RV, эталон каталога,
installed image heads и image digests. Receipt подтверждает новое соединение
с тем же TLS/SCRAM SQL peer и нулевые `auth_sessions`/`oidc_login_flows` в
READ ONLY snapshot; наблюдение не подтверждает commit. Результаты обслуживания
сохраняют `storage_recovery_authorized=false`; пользователи входят заново.

Create и UID/RV Foreground DELETE выполняются по одному разу после durable CAS.
Потеря ответа допускает только наблюдение. Перед logs/verdict и перед DELETE
повторяются captured Pod, current Node/Lease, terminal exit0 и protected context.
Во всех четырёх controller cleanup границы проверяют reader Node UID, Lease UID,
Ready и срок Lease после сохранения cleanup intent, прежде первого DELETE.
SQL/schema используют сохранённую verified запись для строгой проверки reader;
актуальные intent, context, полный inventory и metadata/RV проверяются отдельно.
Cleaned требует повторного отсутствия Job и Pod; родительские журналы сохраняются.
Operation Job повторно использует только код закреплённого модуля TLS через
существующий загрузчик. Данные шаблона, Secret, файлов и кластера проверяются заново.

Прежнее independent review:32 PASS и один P2 по пропуску reader Node/Lease
после CAS; исправление нового controller воспроизведено RED → 22 cleanup PASS
на Windows/Linux. Затем аналогичный дефект подтверждён в старых SQL/schema и
session-invalidation controller и исправлен с12 регрессиями. Final review421R3:
69 уникальных PASS, замечаний нет, остальной runtime419 сохранён AST-проекцией.
Windows/Linux покрывают135 уникальных сценариев. Windows подтверждён отдельными
неизменёнными115 сценариями и финальными12 регрессиями+8 CLI; единый зелёный
полный suite этим не заявляется. Linux135 — семь непересекающихся групп, каждая
в прежнем180s лимите; контейнеры удалены штатно, отсутствие проверено отдельно.
Первоначальные ошибки тестового формата и420 SQL180s timeout сохранены в evidence.
Синтетический профиль одного сценария:37.95 →12.42s; compile27.69 →2.46s.
Эти измерения сами по себе не подтверждают время восстановления в инфраструктуре.

Native421R3 component: 477.97s при прежних600s wrapper и
520s cooperative budget. Реальные PG17/TLS, PVC, durable parent Jobs, actual
Job/Pod UID и Node Lease; четыре lost replies, ровно one create/log/delete,
fresh zero counts и контрольные chat/SQL данные сохранены. Собственные namespace,
PV и wrapper удалены штатно и независимо проверены. Runtime/Helm/external
fencing в лабораторном сценарии используют doubles; target acceptance открыта.
Сценарий сохраняет scope прежнего419R4: исключены четыре повторные проверки
старого этапа и дублирующий graph Job, actual revision reader и migration Job
проверяют installed heads. Все проверки нового компонента сохранены.
Native419R2 FAIL464.80s (ошибка чтения краткого ответа),419R3 FAIL534.89s и
419R4 FAIL534.83s (cooperative520) остаются FAILED, как и прежние full600 matrix.
Новый scoped результат не переименовывает их и не закрывает весь T3.

OPEN: protected kit/image/reference promotion и hosted CI; key/Secret и DB
antirollback; настоящий provider login; explicit pre-dispatch repair и
неизвестный commit; terminal/app writer/unlock; полный T3–T6 и целевая приёмка.
Local/Compose, рабочее приложение и домашние кластеры не обновлялись.
Commit/push/release не выполнялись.


### Проверка ключа сессий при восстановлении (07.10.2026)

Кандидат422 добавляет отдельную проверку только для чтения
`validate_recovery_key_provenance`. Прежние v1 rotation decision/журналы и команды
совместимы; их флаги сами по себе не подтверждают смену ключа. Новый gate сверяет
ограниченное закрытое подтверждение с независимыми ожидаемыми SHA, полными native
и session decision, текущим immutable Secret (имя, namespace, UID, RV) и ключом.
Ключ, его SHA и содержимое Secret не возвращаются; writer/start/unlock запрещены.
Каноническая строка ключа сама по себе не доказывает энтропию или выполнение RNG.

Политика восстановления: явная ротация разрешена, все пользователи входят заново.
При известном прежнем ключе независимо переданный SHA должен отличаться от нового.
При недоступном прежнем ключе нужны отдельное явное разрешение и доверенное
подтверждение новой генерации; результат честно сообщает, что сравнение ключей
не выполнено. Ожидаемые SHA получают из защищённого независимого источника,
а не из восстановленной БД, backup, текущего Secret или self-hash файла.
Защищённый publisher и процедура продвижения остаются отдельной границей доверия.

Локальная проверка: 146 Windows и 93 Linux теста прошли; operator-kit format3
сохранился. Независимый review: 48 тестов прошли и 20 дополнительных подмен
отклонены. Native R2 на собственной scratch PostgreSQL17 прошёл за27.45с:
после удаления сессий старый cookie отклонён; настоящий data-only pg_dump/psql
восстановил обе auth-таблицы, и старый ключ снова принял этот cookie. Новый ключ
его отклонил, gate отказал при возврате старого ключа. Полные строки documents,
chat_sessions, chat_messages, audit_log и audit_outbox сохранились. Схема этого
стенда создана из SQLAlchemy-моделей; миграции, production grants и полный backup
приложения этим не проверены. Все временные контейнеры/сеть штатно удалены,
отсутствие проверено отдельно. R1 проверял только sentinel; этот пробел закрыт R2.

Принят компонент426R2, включающий ограниченный provenance gate422 и live CLI.
Полный backend pytest422 завершён: 5823 passed, 9 failed, 154 skipped,
60 errors, 5577.39с. Ошибки окружения не считаются успешной приёмкой:
исходные результаты сохранены. Отдельный профиль с закреплённым Helm4.3.0
и зависимостями дочернего Python, а также исправленной тестовой проверкой
«не создавать новых файлов» прошёл99 тестов за111.61с с полным conftest.
Четыре runtime CA сценария прошли за27.22с. Три исходных Windows symlink
сценария требуют прав symlink; те же неизменённые три сценария прошли на Linux
за1.16с. Windows FAIL сохранены; общий suite не объявлен зелёным. Изменены только тестовый профиль и одна фикстурная проверка, исходный
снимок422 сохранён. Hosted CI, полная T3–T6 и целевая приёмка остаются открытыми.

### Подготовленная проверка ключа через текущий Kubernetes API (07.10.2026)

Компонент426R2 добавляет отдельную команду только для чтения:
`postgres-recovery-check-key-provenance`. Она требует все17 входов recovery
и три дополнительных: `--key-provenance-record`, `--key-provenance-trust`,
`--expected-key-provenance-trust-sha256`. При недоступном прежнем ключе требуется
явный `--allow-unavailable-previous-session-key`; известный прежний ключ вместе
с этим флагом отвергается. Независимый SHA закрепляет закрытый trust manifest,
который содержит SHA подтверждения, источника генератора и прежнего ключа
или null. Ключи и их прямые SHA не передаются через argv и не публикуются. В argv
передаётся только независимо полученный seal закрытого trust manifest.

Трижды читаются вся завершённая цепочка, защищённые файлы и текущий immutable
Opaque Secret. Сравниваются полные наблюдения; смена UID/RV, данных или даже
посторонних metadata приводит к отказу. Файлы ограничены по размеру, symlink,
Windows junction/reparse и замена при чтении отвергаются. Явный пустой SHA
на прежних командах отклоняется до Manager. v1 decision, прежние журналы
и команды сохранены. Результат проверки не разрешает запись, запуск или unlock.

Windows:61 passed/2 platform skips,77.89с. Linux:62 passed/1 platform skip,
98.33с; оба symlink сценария выполнены, контейнер удалён штатно и отсутствие
проверено отдельно. Независимое ревью нашло два дефекта; Windows junction
и пустой legacy SHA воспроизведены RED и исправлены. Повторное ревью:
5 passed/0 skips, блокирующих замечаний в исправлении нет.

Нативный R4 подтвердил компонент за162.86с при прежних cooperative520s и
wrapper600s. Реальные PG17/TLS/SCRAM, PVC, мигрированная схема, завершённые
native/revision/schema/invalidation/absence журналы и immutable Secret.
Проверка ключа и metadata-RV race выполнены через обычный kubectl; стендовый
Unix socket GET proxy использован только для предварительных этапов, без
кэша ответов и открытого TCP-порта. Счётчик9662 чтений proxy не изменился
во время key check/race. Смена RV отвергнута; lock, checkpoint, Secret data
и контрольные chat/SQL данные сохранены. Product mutation count0. Собственные
namespace/PV/контейнер удалены штатно, отсутствие проверено независимо.
Runtime/Helm/external fencing используют лабораторные doubles; issuer в этом
сценарии тестовый. Production publisher, provider login и полный T3 не доказаны.

Исходные FAIL R1(540.89с/cooperative520), R2(21.45с/chunk parser) и
R3(23.31с/fixture inventory bound) сохранены. Для R2/R3 потребовалась отдельная
UID/RV очистка namespace после естественного снятия Job tracking finalizer
контроллером; force/finalizer patch не применялись. Диагностический profiling
FAIL с коллизией имени и отдельная cleanup также сохранены. Hostguard R4
отказал после успешного сценария из-за параллельного независимого коммита
Main; отдельная reconciliation проверила неизменность источников/docs,
новые HEAD до/после и свежую очистку. Исходный отказ guard не переписан.

Открыты: защищённый publisher и независимое
продвижение подтверждения, настоящий provider login,
DB/key antirollback на пути запуска, repair/unknown commit и terminal/writer/
unlock. Проверенный формат ключа не доказывает запуск RNG или доверенное
происхождение подтверждения. Лабораторный issuer не заменяет production issuer.
Local/Compose, рабочие и домашние сервисы не обновлялись. Commit/push не выполнялись.


### Durable запись наблюдения ключа восстановления (07.10.2026)

Принят компонент428R2. Команда `postgres-recovery-record-key-observation`
использует те же17 recovery inputs и три защищённых входа, что readonly
`postgres-recovery-check-key-provenance`; неизвестный прежний ключ требует
явного разрешения ротации. Она добавляет отдельное поле
`postgres_recovery_key_observation` в lock после завершённых native, decision,
revision SQL, schema, invalidation и independent absence этапов. Все шесть
полных родительских записей, Secret UID/RV и identity защищённых артефактов
связаны с операцией. Ключ, его прямой SHA и содержимое защищённых файлов
в публичный журнал не входят.

Три полных одинаковых чтения предшествуют единственной UID/RV CAS; ещё три
подтверждают результат. Потеря ответа разрешает только чтение. Совпадающая
свежая запись проверяется без повторной CAS; чужая, изменённая или истёкшая
запись отвергается. Проверяется deadline перед новым наблюдением и перед
успешным ответом обеих веток. Это запрет поздней записи/успеха, а не гарантия
принудительного прерывания всей вложенной цепочки ровно через90с.

Запись подтверждает наблюдение. `protected_publisher_verified`,
`native_key_rotation_verified`, `db_rollback_verified`, `provider_login_verified`,
разрешения storage recovery, app start, writer и unlock остаются false.
Защищённый publisher/promotion, provider login, DB/key antirollback,
repair/unknown commit и terminal/app/writer/unlock остаются открытыми.


Проверки428R2: Windows73 passed/60.76с, Linux73 passed/76.50с;
operator-kit format3 сохранился. Независимое ревью исходного428 нашло два
нарушения deadline:3 RED при30 PASS. Собственные регрессии подтвердили2 RED;
после исправления отдельно выполнены10 fault cases ревью и19 CLI:29 PASS/38.03с.
Исходные результаты и замечания сохранены, общий backend suite и hosted CI
не объявлены успешными.

Native Kubernetes1.37/PG17/TLS/SCRAM с PVC и мигрированной схемой прошёл
за190.89с при прежних cooperative520s/wrapper600s. Реальная единственная CAS,
потерянный ответ после commit, повтор без записи и отказ после смены Secret RV
проверены обычным kubectl. Proxy применялся только к подготовительным этапам;
его счётчик9664 не изменился во время проверок ключа. Родительские журналы,
checkpoint, Secret data и контрольные chat/SQL строки сохранились. Runtime/
Helm/fencing используют laboratory doubles, issuer тестовый. Собственные
namespace, PV и wrapper удалены штатно; отсутствие подтверждено независимо.
Это приёмка компонента, полная T3–T6 и целевая приёмка остаются открытыми.


### T4: метадатная проверка перехода PostgreSQL trust (07.10.2026)

Принята основа429: `postgres_restore_profile_sha256` и
`validate_restore_profile(..., namespace_uid=...)`. Canonical identity состоит
ровно из mode, CA/CRL SHA и CRL policy. Подтверждённый disabled отличается
от legacy unknown: неизвестный профиль не получает вымышленного hash.
Изменение профиля требует решения ≤16KiB с точными manifest/source/target
hashes, context/namespace/namespace UID и будущим UTC RFC3339 Z expires_at.
Закрытый JSON отвергает дубли, NaN, bool/float format и неизвестные поля.
TLS downgrade и удаление обязательной CRL запрещены даже при наличии решения.

Это проверка метаданных: native trust, защищённое происхождение решения и
разрешение restore остаются false. Backup3/legacy2 adoption, revalidation
Secret UID/RV/валидности/native TLS перед import/migration/start и сохранение
restore evidence ещё не подключены. Полный T4 остаётся открытым.
Windows208 PASS/72.75с; Linux208 PASS/91.81с, kit format3 сохранён.
Независимое ревью:76 новых тестов и68 дополнительных проверок,144 PASS/4.40с,
блокирующих замечаний нет. Нативное восстановление этим компонентом не проверено.


### T4: сохранённые публичные байты PostgreSQL trust (07.10.2026)

Принята основа430 `validate_backup_postgres_profile`. Format3 требует
явный PostgreSQL профиль: null означает подтверждённый disabled, verify-full
содержит ровно mode/CA SHA/CRL SHA/CRL policy. CA и обязательная CRL проверяются
по SHA исходных байтов, закрытым именам, PEM и CA/CRL ограничениям; CRL должна
быть подписана подходящим CA. Format2 без этого поля остаётся unknown.
Истёкший исторический CA/CRL допускается как сохранённый материал; это не
подтверждение текущего доверия и не разрешение восстановления. Приватный ключ
или неожиданный материал отвергается. Каждый публичный файл ограничен256KiB.

Новые40 тестов прошли RED→GREEN. Frozen Windows248 PASS/70.37с,
Linux248 PASS/88.45с; Linux wrapper завершён штатно и отсутствие подтверждено.
Независимое ревью91 PASS/2.61с, блокирующих замечаний нет. Operator-kit format3
сохранён без продвижения release. Проверка всего manifest/release, файловой
системы и потоковых hashes, producer/legacy adoption/controller/native restore
ещё предстоит. `current_trust_verified` и `restore_authorized` остаются false;
полный T4, backend suite и hosted CI остаются открытыми.


### T4: полная проверка байтов format3 (07.10.2026)

Принят компонент431 — отдельный readonly `verify_backup_format3(path)`.
Manifest и release identity имеют закрытые схемы и предел64KiB, публичные
CA/CRL/HTTP bundles —256KiB. Физические файлы должны точно совпасть с manifest;
лишние, отсутствующие, hardlink, symlink/reparse, каталоги и nonregular members
отвергаются. Проверяются исходный путь, root и родители без предварительного
resolve. Dump/tar/totals хэшируются блоками1MiB с проверкой размера и identity
открытого файла. Повторные наблюдения файлов/каталога и контрольных байтов
ловят видимую подмену во время проверки. HTTP и сохранённые PG trust bytes
проверяются отдельно; исторический срок CA/CRL не превращается в current trust.

Успешный результат подтверждает только byte integrity: protected origin, current
trust и restore authorization остаются false. Источник должен быть защищённым
неизменяемым хранилищем, а перед последующим import нужна свежая проверка.
Stat-наблюдения не являются атомарным snapshot или защитой от привилегированного
нарушителя на хосте. Семантика dump/tar/totals, target admission/digests и capacity
проверяются отдельными gates. Старые reader/producer/controller/CLI сохранены
без изменения их определений; новый helper пока не подключён к обычному restore.

Новые97 случаев: Windows91 PASS/6 platform SKIP, Linux95 PASS/2 Windows-only SKIP.
Frozen Windows339 PASS/6 SKIP/88.47с и Linux343 PASS/2 SKIP/104.85с.
Прежние lifecycle/HTTP trust70 PASS на Windows25.51с и Linux31.02с.
Первичный минимальный child dependency profile дал5 FAIL/65 PASS; отказ сохранён,
повтор выполнен с подготовленными зависимостями без изменения продукта/тестов.
Независимое ревью37 PASS/3.94с, блокирующих замечаний нет. Linux wrappers удалены
штатно, отсутствие подтверждено отдельно. Deterministic operator-kit format3
сохранён без продвижения release. Полный T4, T3 и общий backend/hosted CI открыты.


### Native producer format3: внутренний компонент (08.10.2026)

В изолированном worktree принят компонент475: source/lock/checkpoint/PVC/runtime
bindings, durable intent и export proof до completion markers, ограниченные
потоки и SHA-256, оба exit codes0 и неактивный Complete Job, receipt, условное
удаление writer по UID/resourceVersion, отсутствие writers во всём namespace,
публикация manifest без перезаписи и проверка сохранённых байтов после сбоя.
Проверены полный PG17 dump через verify-full/SCRAM, схема/Alembic, integrity,
totals, Qdrant snapshot и data archive для старого документа и опубликованного
поколения. Sizing policy имеет закрытую схему, целочисленные пределы и binding
к source/template; старые intents не переопределяются. Защищённость Windows
каталога и ACL его родителей требуется отдельно; parser её не подтверждает.

Frozen Windows1510 PASS/17 SKIP и Linux1524 PASS/3 SKIP уложились в исходные180с.
Независимое ревью без блокирующих замечаний; оба Minor по focused CI и абсолютному
deadline исправлены через RED/GREEN. Full native721.41с при исходном лимите900с.
Его исходный aggregate guard сохранил FAIL из-за внешнего коммита Main; отдельная
сверка подтвердила сохранность принятых файлов/документации и очистку ресурсов.
Домашний повтор принят отдельно с сохранением первоначального отказа CPU.

Общий TLS backup/deploy сохраняет отказ до остановки приложения: standalone
parent с durable stop/start/status/resume и protected sizing CLI ещё не принят.
Локальный запуск и plaintext/Compose пути сохранены. Работающая установка не
обновлялась. Protected origin, current trust и restore authorization остаются
false. Полные T3/T4, restore/сессии после разрешённой ротации ключа, общая backend
матрица и hosted CI открыты. Release/operator-kit не продвинут; совместимость
его существующего format3 проверена. Будущая parent-заготовка479 не принята.


Проверка после переноса: полная матрица producer + прежние lifecycle/HTTP trust
(50 modules) прошла на Windows и Linux в исходных180с. Первый Windows FAIL
сохранён: пять child tests не видели зависимости минимального приватного
interpreter; подготовлен только его dependency profile, продукт/тесты не менялись.

Следующие обязательные gates parent: durable stop180/start600 deadlines без
сброса после restart; отдельный closed effect record с UID/RV Deployment,
исходными/целевыми replicas и точным sizing policy; namespace-wide fencing
всех consumers; свежие native TLS/runtime/storage proofs; неизменяемая receipt
после публикации; selected-generation readiness + model-free HTTP до unlock.
Неизвестный ответ на effect требует passive reconciliation, без повторного
name-only scale. Metadata journal сам не разрешает stop/start/producer.


### Дополнительные проверки parent restart — 08.10.2026

Последний проверенный неизменяемый кандидат parent: 534; runtime продолжает использовать принятую реализацию producer 475. Проверки кандидата не означают включение штатного TLS backup/recovery или закрытие T3/T4.

| Проверка | Результат | Граница доказательства |
|---|---|---|
| Windows, 108 модулей | 3099 passed, 19 skipped; 83,92 / 180 с | Неизменяемый кандидат, полная проверка SHA исходников |
| Linux, 109 модулей | 3152 passed, 5 skipped; 64,41 / 180 с | Тот же кандидат, источник только для чтения, обычная очистка ресурсов |
| Нативный SQL-reader | PASS; 870,01 / 900 с | Полный реальный producer, новый readonly Job, TLS/SCRAM, сохранение результата UID/RV CAS; подготовительная служебная остановка |
| Завершение reader и запуск приложения | Открыто | Нужны нативное удаление/отсутствие, APP Ready/HTTP/backend SQL, completed/unlock |

Нативный прогон завершил обычную условную очистку namespace/PV/контейнера; отсутствие проверено независимо. Исходные сроки reader 60 с и start effect 600 с сохранены. Предыдущий неуспешный SQL-reader прогон (764,14 с), прежние неуспешные прогоны и полный backend gate не отменены. Успешные компонентные проверки остановки и start-intent учитываются отдельно; сквозная родительская операция, Helm и защищённый источник запуска пока не приняты.

### Кандидат запуска приложения и сохранения журналов — 08.10.2026

| Проверка | Результат | Граница доказательства |
|---|---|---|
| Неизменный 545, Windows | 3400 passed / 19 skipped; wrapper 125,11 / 180 с | Исправлен путь в отдельном проверочном скрипте; первый отказ сохранён. |
| Неизменный 545, Linux | 3453 passed / 5 skipped; wrapper 106,66 / 180 с | Исходники kernel-readonly, без сети, прежние ресурсы; штатная очистка и повторная проверка отсутствия wrapper/тома. |
| Нативный компонент 545, этап подготовки | FAILED: 920,38 / 900 с | Архив и SQL-reader функционально готовы, но исходный лимит превышен. Передача не принята, удаление reader и запуск приложения не выполнялись. |
| Объединённые source-чтения, кандидат 547 | 157 связанных локальных проверок passed | Эквивалентные три снимка: 6 вызовов вместо 30. Полные 547: Windows 3465 passed / 19 skipped, wrapper 127,84 / 180 с; Linux 3518 passed / 5 skipped, wrapper 106,80 / 180 с. Повторный нативный компонент 547 принят: этап подготовки 696,28 / 900 с, удаление reader свежим процессом 104,49 / 180 с. Общая application-return/unlock приёмка ещё открыта. |

Неуспешный нативный запуск очищен условным удалением по сохранённому UID/RV; Namespace/PV, wrapper и собственный том отсутствуют. Набор 475 остаётся принятой реализацией. Новые completed/unlock/status ещё не дают нативной или сквозной приёмки T3/T4. Исходные неуспешные проверки, включая общий backend gate, не пересматриваются по результатам компонентов.

Нативный компонент 547 повторно подтвердил PostgreSQL/Qdrant/PVC, архив с 2 документами, 4 чанками, 4 концептами и 8 точками, фактический SQL-reader и сохранённое наблюдение. Свежий процесс повторно проверил текущие TLS/runtime/namespace/архив, записал delete intent, выполнил один Foreground DELETE по UID/RV и сохранил отсутствие reader через CAS. Временные Namespace/PV, оба wrapper и собственный том удалены штатно; независимое отсутствие проверено. Исходные сроки 600/180 секунд, источники, принятый runtime 475 и Git сохранены. Неуспешный первый запуск 920,38 / 900 с остаётся FAILED; новая проверка не подтверждает запуск приложения, Helm, защищённое происхождение или полную T3/T4.


### Нативный возврат приложения после backup — 08.10.2026

Компонентная проверка неизменяемого кандидата 547 прошла: подготовка producer/архива и передача `backup_complete` — 518,91 / 900 с; свежий процесс выполнил полную цепочку reader → запуск приложения → `completed` → Orphan unlock за 596,66 / 900 с. Первое намерение запуска создано только в новом процессе; права на действия из памяти предыдущего процесса не переносились.

Подтверждены фактический readonly SQL-reader с TLS/SCRAM, его единственное условное удаление и отсутствие Job/Pod, один UID/RV patch приложения 0→1, принадлежность и Ready обоих контейнеров, readonly SQL из backend и HTTP backend/frontend без моделей. `completed` сохранён через CAS до единственного условного Orphan-удаления lock; отсутствие прежнего UID lock, сохранность UID/данных source, terminal producer и parent, архива и исходных storage UID проверены отдельно. Namespace/PV/wrapper/том удалены штатно, их отсутствие подтверждено независимо; исходники, рабочий runtime, документация, HEAD и index во время прогона не менялись.

Это два отдельных компонентных этапа, а не сквозная приёмка parent за 900 с. Подготовка использовала служебную остановку приложения; установленные метаданные Helm и защищённое происхождение не проверялись. Исходные сроки reader 60 с, удаления 180 с, запуска 600 с и каждой source/CAS границы сохранены. Прежние FAILED-результаты, включая 920,38 / 900 с, остаются действующими историческими свидетельствами. Рабочий runtime остаётся на наборе 475; полная T3/T4, общий backend gate, обычный parent controller/CLI, проверки неоднозначных ответов в полной цепочке, Helm, защищённое происхождение и домашний сквозной стенд остаются открытыми.


### Уточнение допуска действий по результатам ревью — 08.10.2026

Независимое ревью кандидата 547 выявило дефект: пассивно найденная после потерянного/невалидного ответа запись CAS могла создать разрешение на следующую первую мутацию. Это затрагивало создание исходного source-журнала, reader-журнала и намерения unlock. Связанный прогон на неизменяемом 547 подтвердил шесть отказавших проверок и три положительных контроля. Кандидат 549 исправил эти границы; Windows прошёл 146,69 / 180 с, но ревью выявило аналогичный случай у stop-effect marker, поэтому 549 не принимается для интеграции.

Кандидат 550 разделяет подтверждённый native ACK и пассивное наблюдение сохранённой записи. Receipt требует точные API/kind/name/namespace/UID/RV, владельцев, labels и данные, а у stop marker — также immutable. Разрешение на первую остановку сохраняется лишь после внутренних metadata fences и обоих внешних полных source guards. Потерянный/невалидный ответ или failed postguard не дают первого действия; известная запись доступна только пассивно, без повторной записи или эффекта. Новые связанные проверки: 35 для phase ACK и 13 для stop ACK; вместе с прежними границами — 216 и 110 passed соответственно. Независимое ревью 550 не обнаружило новых Critical/Important/Minor; это code review компонента, не общая приёмка.

Также добавлены ограниченные чтения выбранных native ресурсов внутри каждого снимка parent. Все три независимых полных source frames, данные, UID/RV, namespace, inventory/consumer проверки, исходные сроки и запрет частичного fallback сохранены; снимки между вызовами не кешируются. Локально проверено 21 новым тестом и прежними связанными проверками.

Полные платформенные проверки 550 прошли в исходных 180 с: Windows 3534 passed / 19 skipped, wrapper 138,78 с; Linux 3587 passed / 5 skipped, wrapper 113,98 с. Нативная проверка изменённого кандидата ещё не принята. Прежние успешные нативные проверки 547 подтверждают свои положительные компонентные сценарии, но не закрывают найденные пути неоднозначного ответа. Рабочий runtime остаётся на 475; обычный TLS backup, сквозной parent/900 с, установленный Helm, защищённое происхождение, домашняя полная цепочка и общие T3/T4 остаются открытыми. Прежние FAILED gate не изменены.


### Единый backup parent: подготовка сквозной проверки

Отдельный внутренний контроллер соединяет prepared/source/stop, создание и публикацию архива, SQL-reader, его удаление, запуск приложения, completed и Orphan unlock. Вся операция получает один исходный срок 900 с; дочерние проверки используют оставшийся срок, а исходные ограничения stop 180 с, start 600 с и Job 60 с сохраняются. Первая мутация каждого этапа требует подтверждённого native ACK после всех проверок источников. Неопределённый ответ не разрешает следующую мутацию или повтор эффекта; отказ сохраняет операцию без автоматического restart/unlock. Ожидание Ready является лишь наблюдением: допуск по источникам и SQL/HTTP проверяется отдельно.

Новые локальные проверки контроллера: 92 passed. Проверка реальных сигнатур воспроизвела и закрыла ошибку передачи срока metadata-helper. Набор также проверяет отсутствие повторных эффектов, поздние неопределённые ответы, неизменность общего срока и реальные состояния остановки/двух контейнеров Ready. Это ещё не нативная приёмка сквозного сценария; ordinary CLI, подтверждение установленного Helm и защищённого происхождения, домашняя полная цепочка, общий backend gate и T3/T4 остаются открытыми. Рабочий runtime сохраняет принятый набор 475.


### Проверка единого контроллера и исправление Orphan GC

Кандидат 551 прошёл полные платформенные наборы в исходных 180 с: Windows 3626 passed / 19 skipped, wrapper 135,81 с; Linux 3679 passed / 5 skipped, wrapper 111,94 с. Первый Linux запуск остановился до тестов из-за устаревшего ожидаемого числа файлов в драйвере; отказ сохранён, его временный том штатно удалён. Исправление драйвера не меняло исходники и критерии.

Независимое ревью 551 обнаружило Important: штатный Orphan GC между двумя readonly кадрами вызывал немедленный отказ координатора. В 551R2 повторяется только наблюдение отсутствия старой блокировки в оставшемся исходном whole900; prepare, delete и restart не повторяются. Постоянный отказ заканчивается по тому же сроку. Проверки сначала дали два отказа и один положительный контроль; после исправления — 95 pipeline и 21 actual unlock, всего 116 passed. Повторное независимое ревью подтвердило исправление, новых Critical/Important/Minor не обнаружено.

Полные платформенные проверки R2 и нативная единая цепочка ещё не приняты. Подготовленный нативный сценарий начинает с работающего приложения и вызывает единый контроллер вместо служебной остановки. Его успешное выполнение само по себе не закроет установленный Helm, защищённое происхождение, домашнюю полную цепочку, общий backend gate и T3/T4. Рабочий runtime остаётся на принятом наборе 475; обычный TLS backup пока закрыт.


### Полный кандидат 551R2 и сохранённый нативный отказ

Полные Windows/Linux551R2 прошли: Windows 3629 passed / 19 skipped, wrapper 134,23 / 180 с; Linux 3682 passed / 5 skipped, wrapper 112,58 / 180 с. Первый Windows-драйвер R2 остановился до коллекции на ошибочно переименованном тестовом пути; его отказ и лог сохранены. Исправлено только имя в драйвере, добавлена проверка наличия всех явных модулей; исходники и критерии не менялись.

Единый native551R2: FAILED, 573,75 / 900 с. Настоящий контроллер выполнил первоначальную условную остановку, source SQL/retirement, архивирование/публикацию и CAS `backup_complete`. Затем дополнительный archive guard стенда вызвал legacy verifier формата 2 для архива формата 3 и отказал до start effect. Обычный legacy verifier сохраняет требуемый отказ; гипотеза следующего прогона — исправить только harness на существующий строгий `verify_backup_format3`, проверяя byte integrity и сохраняя `protected_origin_verified/current_trust_verified/restore_authorized=False`. Повтор использует новый owned namespace и прежние 900/180/600/60 с, ресурсы и тот же неизменяемый source551R2. Первоначальный FAILED не переписывается.

Временные namespace, PVC/PV, wrapper и том native551R2 штатно удалены; независимое отсутствие и сохранность source/tools/docs/runtime475/HEAD/index подтверждены. Сквозной native успех, установленный Helm, защищённое происхождение, домашняя полная цепочка, общий backend gate и T3/T4 остаются открытыми.


### Нативная цепочка 551R2: функциональный результат и неуспешный временной критерий

Повтор R3 с исправленным format3 guard функционально завершил настоящую цепочку: первоначальная условная остановка, source SQL/retirement, публикация архива, start reader SQL/retirement, единственный условный запуск, выбранные backend/frontend Ready и backend SQL/HTTP, completed и единственный Orphan unlock. Архив, source/producer/parent журналы и storage UID сохранены до штатной очистки стенда. Работа контроллера заняла 870,79 с, но общий wrapper с подготовкой и очисткой — 950,64 с: исходный 900-секундный gate FAILED. Этот результат не считается нативной приёмкой; исходный отказ R2 также сохранён. Namespace/PVC/PV/wrapper/том штатно удалены, независимое отсутствие и неизменность source/tools/docs/runtime475/HEAD/index подтверждены.

Зафиксировано 15121 native чтение. Следующая проверяемая гипотеза — транспортное объединение оставшихся точных source/reader metadata запросов: workload/PV/runtime/service/trust чтения в каждом из трёх независимых storage-кадров и пять root/journal чтений в каждом из двух reader-кадров. Сохраняются все проверки полных объектов, UID/RV/namespace/owner/spec/data, приватные сравнения только в памяти, независимость кадров, source fences, ACK/одноразовые эффекты, исходные сроки и ресурсы. Не допускаются кеширование между кадрами, fallback после неполного ответа или расширение бюджета ради зелёного gate. Гипотеза требует owning golden/drift/transport тестов, полного неизменяемого Windows/Linux набора, ревью компонента и нового owned native прогона с теми же 900 с.

Домашняя VM проверена только чтением: 8 CPU, около 4,3 ГиБ доступной памяти, 57 ГиБ свободного места, Kubernetes Ready. Закреплённый frontend digest на домашнем узле отсутствует; перенос проверенного OCI-образа и подтверждение его root/platform/config/layers/CRI ссылки необходимы до полной домашней цепочки. Подготовленный пакет 551R2 не запускается, пока локальный исходный gate не пройден; при новом кандидате пакет должен быть собран заново. Обычный TLS backup, Helm/protected origin, общий backend gate и T3/T4 остаются открытыми, runtime475 не изменён.


### Кандидат 553: полные кадры источников и reader

Точное объединение GET реализовано без кеша между кадрами и без fallback: остаются три независимых полных storage/runtime кадра и два независимых root/journal кадра reader. Сохраняются проверки полных объектов, UID/RV/namespace/owner/spec/data, приватные сравнения в памяти и исходные сроки. Writer policy фиксируется до первого кадра; отдельная проверка старого 551R2 воспроизвела пропуск её изменения, 553 отклоняет изменение. Owning проверки, включая настоящий Manager.run, — 30 passed. Целостное независимое ревью не выявило новых Critical/Important/Minor.

Windows553: 3659 passed / 19 skipped, wrapper 152,55 / 180 с; Linux553: 3712 passed / 5 skipped, wrapper 124,59 / 180 с. Явные наборы — 127/128 модулей; прежнее поле Linux `focus_modules=126` осталось в исходном результате, корректный счётчик записан отдельно. Подготовка Linux 2,94 с учитывается отдельно, source/per-read SHA и readonly/nonroot/no-network ограничения сохранены. Временный wrapper и том штатно удалены, независимое отсутствие подтверждено.

Новый единый native553R1: PASS, общий wrapper 789.02 / 900 с; время контроллера 705.97 с. Первоначальная остановка выполняется контроллером, исходные source/CAS/reader/start/retirement сроки и ресурсы не расширены. Стенд штатно очищен, namespace/PVC/PV/wrapper/том отсутствуют; source/tools/docs/runtime475/HEAD/index сохранены. Прежние FAILED551R2 и FAILED950,64/900 не переписаны. Этот результат не означает принятия установленного Helm, защищённого происхождения, обычного CLI, домашней полной цепочки, общего backend gate или T3/T4. Рабочий runtime остаётся на 475.


### Домашний полный контроллер 553 и оставшаяся интеграция

На домашней VM (8 CPU, 10 ГиБ RAM, диск 100 ГиБ) единая нативная цепочка 553 прошла исходный gate: wrapper 660,36 / 900 с, контроллер 589,65 с, 10309 native чтений. Настоящие условные stop/start, source SQL/retirement, публикация архива, reader SQL/Foreground retirement, выбранные backend/frontend Ready и backend SQL/HTTP, completed и единственный Orphan unlock выполнены в одном контроллере. Данные стенда: 2 документа, 4 чанка, 4 концепта, 8 точек Qdrant; вызовов моделей нет. Это функциональная проверка на малом корпусе, не оценка production нагрузки.

Перед запуском проверены существующие frontend root/platform/config и все 12 слоёв на узле; добавлена только отсутствующая закреплённая canonical CRI ссылка без импорта или перезаписи других ссылок. Все прежние image targets/labels, UID/replicas приложений и PVC сохранены. После теста namespace/PVC/PV, wrapper и owned handoff-том штатно удалены, временный kubeconfig удалён; новым независимым чтением подтверждено отсутствие namespace/wrapper/тома/kubeconfig. Source/tools и архив/журналы/storage UID сохранялись до штатной очистки.

С настоящим общим backend conftest дополнительно прошли 228 проверок на отдельном полном snapshot 1742: новые storage/reader/pipeline и прежние TLS/CA/checkpoint/probe/schema сценарии. В тесте отсутствия новых файлов сравнивается полный набор уже существовавших путей, включая вложенные, вместо предположения пустого tmp_path. Это focused проверка, не полный backend gate и не 180-секундный platform gate. Полный Linux backend набор с production dependencies превысил исходный safety limit 7200 с (FAILED); позднее завершение используется только для диагностики. Прежний общий Windows422 FAILED и ограничения Windows symlink сохраняются.

Установленный Helm, независимое защищённое происхождение, обычный backup CLI и полные T3/T4 остаются открытыми. Следующий интеграционный этап должен читать настоящее Helm поколение, проверять его against текущих полных native templates и отдельно получать genuine ownership ACK до первых эффектов. Текущие byte integrity результаты не подтверждают publisher/restore permission. Рабочий runtime сохраняет принятый набор 475; клиентские credentials или изменения внешних кластеров для этих проверок не использовались.


### Проверка установленного поколения: состояние на 08.10.2026

Первый кандидат 562 не принят: Windows с прежним полным набором 136 модулей и дополнительной проверкой loader превысил исходные 180 с (180,02 с, FAILED). Полный Linux backend snapshot 1742 превысил первоначальный safety limit 7200 с; исходный gate FAILED, позднее завершение оставлено только для диагностики. Прежние FAILED не заменяются частичными результатами.

Независимое целостное ревью обнаружило четыре Important: устаревший остаток deadline после последнего полного fence; выдача первого source receipt из GET после потерянного CREATE ACK; публичный отпечаток потенциально приватных values; неполная проверка native reader CAS ACK. Подготовлены исправления и причинные RED-проверки; связанный focused набор прошёл 626 проверок. Повторное ревью дополнительно проверяет типы ownerReferences и все точки выдачи genuine ACK receipts. Полные новые Windows/Linux gates, actual Helm/native и домашняя цепочка ещё не приняты.

Проверка защищённых values сохраняет точное сравнение файлов, дескрипторов и байтов только в памяти; публичный values_sha256 установлен в null. Потерянный или неподтверждённый ответ оставляет запись и lock для пассивного наблюдения, без повторного эффекта. Исходные deadlines и проверки namespace, UID/RV, TLS, HBA, Pod/PVC/PV и writer policy сохраняются. Рабочий runtime остаётся на 475; обычный TLS backup CLI, archive origin, restore authorization и полный T3/T4 остаются открытыми.


### Кандидат 562: единая проверка ACK и актуальные платформенные результаты

Независимое ревью точного нового кандидата: Critical0/Important0/Minor0; R1–R5 закрыты, 22 причинные проверки passed. Owning набор — 585 passed. Все нативные ConfigMap-подтверждения проверяют типизированные поля, UID и новый RV; свежий финальный кадр должен совпадать. Неизвестный ответ не даёт разрешения на повтор эффекта. Публичный fingerprint приватного values отсутствует; точное сравнение bytes/descriptor выполняется только в памяти.

Linux: 4048 passed / 5 skipped, 137 явных модулей плюс loader; wrapper 167,59 / 180 с, source guard и обычная очистка собственного контейнера/тома подтверждены. Windows: полный непересекающийся набор 136 модулей плюс loader выполнен в двух процессах с отдельными временными каталогами, 4014 собранных cases, wrapper 122,69 / 180 с; оба source guards и неизменность инструментов подтверждены. Старые однопроцессные Windows FAILED180,02 и FAILED180,01 сохранены. Двухпроцессная организация проверяет полный набор; результат однопроцессной производительности остаётся FAILED.

Старый полный Linux1742 поздно завершился: 84 failed / 9629 passed / 34 skipped, 11425,21 с. Исходный safety7200 FAILED не изменён. Собственные контейнер и три тома штатно удалены, независимое отсутствие подтверждено. Причина ошибок spawn доказана: незащищённый тестовый main повторно копировал Helm в дочернем процессе, получая PermissionError. Новый guarded-main стенд на полном snapshot1751 прошёл 3 реальные spawn-проверки за18,09 с (wrapper21,89), с фактическим общим conftest; код парсера не изменён.

Профилирование ещё 11 настоящих citation-тестов с общим conftest: passed; SQLite create_all занял12,738 с, execute12,244 с, около1,2 с на новую схему. Полный новый backend-набор пока не запускался. Следующий узкий этап: проверить атомарное создание отсутствующей SQLite-схемы одной транзакцией, включая rollback при ошибке и сохранность уже существующих таблиц; отдельная БД каждого теста, PostgreSQL, лимиты и свежие проверки сохраняются. После этого — общий backend gate и настоящие installed-entry native/home сценарии.

Рабочий runtime остаётся475; новый кандидат562 не интегрирован. Обычный CLI, защищённое происхождение, restore и полные T3/T4 остаются OPEN. Этот раздел уточняет состояние предыдущих записей, не заменяя исторические FAILED.


### Полный backend и следующий installed Helm proof — 08.10.2026

Полный неизменяемый Linux-набор backend1752 прошёл: **10054 PASS, 34 SKIP**, 142 предупреждения; pytest1922,59 с, wrapper1932,58 с при исходных7200 с,2Gi/2CPU. Использован настоящий общий conftest, свежая отдельная SQLite БД каждого теста, реальные spawn-процессы parser supervisor и прежний pinned backend image. Контейнер и три собственных тома штатно удалены, независимая проверка отсутствия и source/tools SHA PASS. Исторические full1742/Windows422 FAILED сохраняются; Linux PASS не заменяет полный Windows gate или hosted CI.

SQLite init_db создаёт отсутствующие таблицы одной реальной транзакцией: при ошибке новые таблицы откатываются, существующие таблицы/строки сохраняются; повтор идемпотентен. PostgreSQL path не менялся. Независимое ревью:0Critical/0Important/0Minor;5 owning Windows и16 focused Linux PASS. На настоящих citation fixtures время setup снизилось с≈1,2 до≈0,05–0,06 с без кеширования схемы или переиспользования тестовой БД. Изменение пока находится в проверенном кандидате, рабочий runtime475 не обновлён.

Следующий native proof использует настоящий общий chart, Helm install storage→upgrade application и genuine begin_backup_installed_operation ACK перед первым эффектом; затем полный backup/reader/start/completed/unlock. Исходные900 с и ресурсы сохраняются. До запуска найден и воспроизведён один Important в R2-сценарии: сырой container state и произвольный seed JSON попадали в диагностику. R2 не запускался. Отдельный R3 выводит только фиксированные состояния, флаги и проверенные счётчики; неизвестные/неполные записи отклоняются.10 owning tests и фактический синтетический seed protocol PASS; независимое ревью R3 ещё выполняется. Это исправление приёмочного сценария, не изменение рабочего приложения.

Домашняя VM имеет8vCPU/10Gi RAM/100Gi disk и включённый автостарт; при свежей проверке доступно≈4,3Gi RAM и57Gi disk. Оба существующих кластера и приложения сохранены. Native/home installed-entry, защищённое происхождение, ordinary CLI, restore, полные T3/T4 и release acceptance остаются открытыми.


### Installed Helm: внешний CA и контрольные проверки — 08.10.2026

Фактический R3 native остановился до seed: production-конфигурация по умолчанию запрещает отключённую аутентификацию. В отдельной синтетической проверке отказ воспроизведён; корректный confidential OIDC и HTTPS проходят Settings без ослабления production guards. R4 с этой конфигурацией успешно создал тестовые данные и запустил приложение через общий Helm chart, но отказался на genuine installed-entry factory до первого backup-эффекта. Точная причина этого фактического отказа пока не подтверждена. Оба FAILED сохранены; собственные namespace/PV, контейнеры и тома штатно удалены.

Независимо воспроизведён дефект проверки внешнего HTTP CA: при trust.existingConfigMap общий chart ссылается на существующий immutable ConfigMap и не включает его в Helm manifest. Проверка ошибочно требовала встроенный объект. Новый кандидат сохраняет обязательные свежие проверки выбранного внешнего CA, PEM, размера, UID/RV, immutable и ссылок frontend; встроенный CA при наличии продолжает сравниваться. Изменены только runtime-проверка и её тесты; 46 owning tests PASS, независимое ревью 0 Critical / 0 Important / 0 Minor. Рабочий runtime475 не обновлён.

Для этого точного кандидата Windows: 4007 PASS / 19 SKIP, два процесса, полный непересекающийся набор, wrapper130,86 / 180 с. Linux R4: 4060 PASS / 5 SKIP, но wrapper195,38 / 180 с — временной gate FAILED, результат сохранён. Один контрольный Linux R5 выполнен без параллельной тестовой нагрузки с теми же исходниками, инструментами, всеми 137 модулями и исходными512Mi/1CPU/180 с: 4060 PASS / 5 SKIP, pytest166,50 с, wrapper169,19 с; source guard и штатное удаление контейнера/тома подтверждены. Это успешный контрольный прогон, а не доказательство причины прежнего замедления.

Следующий R6 использует тот же проверенный сценарий с ограниченной диагностикой только координат исходников, без locals, сообщений исключений и приватных значений. До выполнения требуется независимая проверка coordinator и свежая готовность локального кластера. Домашний сценарий дополнительно требует фактический R6 PASS, сохраняет все существующие PVC UID/PV bindings и replicas; временный kubeconfig удаляется только после точной проверки собственной файловой идентичности и подтверждения отсутствующего/завершённого тестового контейнера. Неизвестное или живое состояние сохраняет credential и завершает очистку отказом. Домашний запуск ещё не выполнялся. Защищённое происхождение, ordinary CLI, restore, полный T3/T4 и release acceptance остаются OPEN.
