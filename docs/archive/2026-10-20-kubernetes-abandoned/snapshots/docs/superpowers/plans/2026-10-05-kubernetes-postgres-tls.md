# Bundled PostgreSQL TLS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.
> Выполнять задачи последовательно в существующем `codex/kubernetes-rollout`.

**Goal:** добавить проверенный opt-in TLS для bundled PostgreSQL, сохранив
первоначальный запуск без TLS и восстановление существующих данных.

**Architecture:** один PG StatefulSet, immutable version-named TLS Secrets и HBA
ConfigMap, общий client profile для backend/Jobs. Operator kit проверяет профиль,
управляет переходами и записывает backup3; прежний kit2 сохраняется отдельно.

**Tech Stack:** Helm, Kubernetes, PostgreSQL17, Psycopg3.3.6/libpq18,
Python3.11+, PyYAML6.0.3 и явно зафиксированный cryptography.

**Spec:** [дизайн и решения проверки](../specs/2026-10-05-kubernetes-postgres-tls-design.md).

## Текущий статус — 10.10.2026

Единый реестр готовности выпуска и действующая очередь находятся в
[общем плане](2026-10-03-kubernetes-rollout.md#текущий-статус--10102026).
T1/T2 имеют компонентную приёмку; полный T3 и T4–T6 остаются OPEN. Принятый
runtime worktree — source565; main и рабочие установки им не обновлялись.
Обычный TLS operator CLI и restore не разрешаются компонентными PASS.

### Очередь и границы TLS-этапа

Исполнять [единую очередь compact-поставки](2026-10-03-kubernetes-rollout.md#уточнение-текущей-очереди--10102026).
Этот план задаёт контракты opt-in TLS и общих recovery-механизмов. Исторические
checkpoints ниже не переопределяют очередь. TLS-specific T3–T6 не блокируют
первый disabled-профиль, если он допустим в целевой среде; общие safety gates
обязательны независимо от профиля.

Историческая база до правок Scale: source579/map840,806 PASS/platform,0 SKIP; native A R7 с disabled TLS
завершил install/upgrade/revision1/2/next/unlock за138,48/180с, independent1,55/60с.
Это metadata-only proof на указанных исторических байтах. Не повторять compatibility/A
без изменения затронутых inputs. B enabled, общий interrupted handoff,
origin/admission/activation, C nonempty restore/ordinary CLI и release OPEN.
Private582 ещё не принят в runtime565/main.

Native backup 2б.3.2 и off-VM consumer приняты; источник результатов —
[приёмка](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md#настоящий-backup--native-и-off-vm-приняты-10102026).
Ближайшая работа — [обычный restore 2б.3.3](2026-10-03-kubernetes-rollout.md#карточка-2б33--обычный-restore-активна).
Common disabled producer/consumer и known PG identity уже проверены на native
format3 backup; unknown legacy и target PG mismatch отказаны до lock.
[Результаты](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md#обычный-backup-format3--producerconsumer-и-native-проверены-10102026).
R1 fixed empty-target probe проверен; ближайший незавершённый контракт —
R2: source pin/подмена проверены компонентно, настоящий typed PG import ещё OPEN.
Ожидаемый digest не подтверждает независимого издателя; его защищённый источник
нужно установить перед native import, как требует P2-B. [Предел проверки R2](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md#обычный-restore-r2--source-pin-проверен-импорт-открыт-10102026).
Enabled TLS source/public materials/native trust остаются
отдельным этапом; disabled PASS их не закрывает. Не повторять принятые producer/
completion/DELETE без изменения затронутых inputs.

Порядок restore задаётся только R1–R6 общей карточки: нужные
origin/admission/activation barriers подключаются до import/start, handoff и
финальный 2б.4 — после связанного пути. Полный restore не зависит от будущего
неподключённого допуска запуска. Подробные результаты snapshot
сохраняются только в acceptance; исторические next/OPEN ниже не являются
поручениями нового исполнения. Enabled TLS и реплики остаются после compact.


**Исправленная зависимость B/C:** B с quota count/pods=0 проверяет genuine TLS
materials, Helm metadata/ACK и ожидаемый отказ unlock при незавершённом checkpoint.
Успешный enabled unlock проверяется только в работающем C после настоящего
PostgreSQL checkpoint `completed`/`provision_complete`. Он не является prerequisite
своей собственной activation. Запрещены фиктивный checkpoint, обход guard или
подмена полного enabled lifecycle одним ожидаемым отказом B.

**История изолированного OIDC-компонента (не следующая задача):**

**Состояние:** полный локальный R8 прошёл за 148,70 / 900с, включая подготовку и
штатную очистку. Проверены настоящий новый OIDC-вход, отказ старой cookie после
возврата auth-таблиц с новым ключом и сохранность business/audit/held outbox.
Диагностический R7 установил четыре штатных `glossary_rule_create` при первом
старте: теперь отдельно проверяются ровно четыре seed-события, затем один login.
Windows99 PASS / 2 POSIX SKIP; Linux101 PASS / 0 SKIP. Прежние FAIL сохранены.
Домашний R7 PASS699,98/900 со штатной очисткой; независимый observer3,53/60
подтвердил отсутствие ресурсов и сохранность обоих baseline. Интеграция со
штатным recovery/start и отдельный retirement удержанного R1 остаются OPEN.
[Точные результаты и ограничения](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md#native-oidc--checkpoint-09102026).

**Область:** существующий private harness `recovery-provider-login563/native-tools/`
(`native_protocol.py`, `native_oidc_run.py`, `probe_stage.py` и их tests), затем
штатные recovery/start paths в `scripts/kubernetes/lifecycle.py`,
`scripts/kubernetes/postgres_tls.py` и существующая session maintenance операция.
Изменение приложения допустимо только при подтверждённом дефекте приложения.

- [x] Сохранить безопасную диагностику audit delta до очистки: фаза, известный
  код события из каталога и количество; неизвестное значение — `unclassified`.
  Не выводить actor, metadata, cookies, токены, session IDs или производные ключей.
  Проверить canary redaction и отказ для неизвестного действия.
- [x] Установить источник дополнительного события по фактической фазе и коду.
  Если оно штатное, добавить точное фазовое ожидание с обоснованием и regression;
  если ошибочное — устранить причину. Не ослаблять immutable audit/business/held
  outbox сравнения и не разрешать произвольные дополнительные события.
- [x] Выполнить полный native сценарий в исходном общем бюджете 900с, включая
  подготовку и очистку: старый вход/session/pending flow → остановка → all-provider
  purge и явная ротация → новый вход/отказ старой cookie → возврат auth-таблиц при
  остановленном приложении → повторный отказ старой cookie с новым ключом.
  Возврат старого ключа проверять только как отрицательный контроль изолированного
  стенда; purge должен блокировать возрождение сессии даже в этом контроле.
- [x] На frozen runtime565/native18/helper47 выполнен полный домашний R7:
  реальный Secret projection/UID, Jobs, stop/start и штатная очистка. Все пять
  assertions, исходный deadline900с и оба baseline подтверждены; независимое
  отсутствие записано отдельно. Это не full restore или protected publisher proof.

#### Условия запуска домашнего OIDC-сценария

Исторические условия и отказы адаптера. Актуальный behavioral результат — R7
PASS, приведённый ниже; отдельный R1 retirement ещё OPEN. Незакрытые общие
recovery/start и release gates перечислены в следующем deliverable.

**Состояние 09.10.2026:** замечания независимого ревью (0 Critical / 8 Important /
0 Minor) исправлены в private adapter; 12 причинных RED + 4 прежних PASS →
Windows16/Linux16 PASS. Первый реальный R1 привязал все11 PVC, но FAILED53,34/900
до OIDC: API опустил пустые env, а listed cache alias не разрешался CRI. Две новые
RED → Windows18/Linux18 PASS, Linux wrapper10,12/180 со штатной очисткой.
Исправления используют только известные API defaults. Проверка разрешённого CRI
config ID оказалась недостаточной для запуска: требуется существующий named
object, привязанный к той же root/platform/config цепочке; full home ещё OPEN.

Ресурсы R1 удержаны: API/CRI подтверждают отсутствие Pod/контейнеров, но namespace
и11 PVC не удалены из-за Event, ссылающегося на незафиксированный исчезнувший Pod.
Это не normal cleanup. Новый сценарий имеет отдельный namespace/record; исходный
FAIL неизменен. До итоговой home/release приёмки закрыть этот orphan gate через
проверяемый ownership/retirement protocol; отсутствие Pod не разрешает обходить
unknown-resource guard. StorageClass WFFC/Delete подтверждён live read-only.

**Files:** private `home-provider565/home_run.py`, `home_contract.py`,
`test_home_contract.py`; переиспользуются неизменные `native-tools/native_protocol.py`
и существующий `scripts/kubernetes/tests/postgres_tls_acceptance.py`.
Перед итоговой поставкой воспроизводимый harness и необходимые tests перенести
в версионированный общий тестовый комплект; private evidence остаётся вне Git.

**Последующий checkpoint:** R2 FAILED59,25/900 до application: Pod env omission
и dangling containerd named object при существующем CRI config ID. R2 retirement
40,25/180 принят отдельно. Именованный cache object восстановлен из sealed OCI
архива с проверкой root/config и сохранности исходного узла/двух кластеров,
19,23/180. R3 FAILED124,48/900 на `readOnly=False` API omission; сам publisher
Succeeded/exit0, capability profile CHOWN-only сохранён. Server dry-run и RED
подтвердили default; Windows20/Linux20 PASS, Linux13,49/180. R3 отдельно очищен
за41,70/180 с namespace/PV absence и baseline equality. Исходные FAIL сохранены;
исправления helpers и отдельный retirement не закрывают полный home сценарий.

**Новый checkpoint R4:** FAILED321,20/900 до запуска основного PG контейнера:
`ErrImageNeverPull` при доступном CRI config ID и отсутствующем named image object.
Source init завершился exit0. Отдельный retirement41,98/180 подтвердил отсутствие
namespace/PV/процессов и сохранность обоих кластеров; исходный FAILED сохранён.
После DELETE kubelet сформировал `ContainerStatusUnknown/137` для основного
контейнера без container ID и времён запуска. Это не доказательство завершения
процесса и не общее разрешение exit137; отдельный observer опирался на сохранённое
наблюдение never-started до DELETE и свежий CRI, в котором init уже завершён.

PostgreSQL/Qdrant/Keycloak/frontend подготовлены одним import каждого sealed OCI
архива с отдельным именованным reference:45,01/240с, root/config и оба baseline
подтверждены. Backend подготовлен ранее. Это cache preparation PASS, не home PASS.
Три причинных RED tests named-reference → полный Windows23/Linux23 PASS без
skips; Linux13,45/180, source guards и штатная очистка подтверждены. Preflight
отвергает config-only/stale alias/root/config drift; tag fallback требует свежей
root/config привязки. Archive root/platform/config проверяются по SHA до namespace.
Private evidence: `stored-image-preflight-red-r2.log`,
`stored-image-preflight-green-r2.log`, `linux-guard-r7.json`.

**Порядок продолжения:** исправить named-reference preflight → Windows/Linux
regression без применимых skips → один новый home case в900с → штатный protected
recovery/start. R1 retirement вести отдельно; итоговая home/release приёмка
требует его закрытия. Cache preparation имеет свой явно записанный budget и не
увеличивает900с или временные критерии ранее сохранённых прогонов.

- [ ] Сначала RED → GREEN tests `wffc_uses_scheduler_and_exact_node`,
  `source_configmap_uid_or_content_drift_refused`, `lost_create_reply_observe_only`,
  `terminal_pod_still_requires_delete`, `early_failure_discovers_owned_pv` и
  `failed_init_or_unknown_cleanup_cannot_pass`, `slow_stop_never_dispatches_live_delete`,
  `writer_relabel_cannot_bypass_maintenance`, `publisher_waits_for_claim_identity` и
  `node_replacement_or_notready_prevents_final_pass`; затем Windows/Linux guard suite.
- [ ] До создания namespace сверить все пять image roles и source-check init:
  archive root → выбранный platform manifest → config, реально сохранённый named
  object на исходном узле и fresh ImageStatus именно этого reference. Config ID
  без named object, stale alias, wrong root/config и same-tag replacement дают
  отказ до первого эффекта; одного server dry-run для этого недостаточно.
  Добавить `test_preflight_requires_stored_named_image_not_cached_config_only`
  и отрицательные cases root/config drift; native cache-only fallback не переносить
  в Kubernetes adapter. После неясного ответа import сначала observe того же
  намерения/reference; не повторять импорт вслепую.
- [ ] Для WFFC использовать scheduler с `nodeSelector` исходного hostname вместо
  прямого `nodeName`; до первого эффекта проверить hostname label и свежие
  Node UID/Ready/Lease, после scheduling — назначенный узел и PVC/PV UID bindings.
  Замена узла или неполная привязка запрещают старт/maintenance.
- [ ] До каждого запуска/maintenance проверять исходный immutable ConfigMap:
  namespace/owner/UID, точное содержимое и ссылки Pod; mounted source сверять
  доверенным launcher с исходным snapshot до выполнения. Аналогично Secret/PVC/PV.
  Одной проверки имени/label недостаточно. Private Secret bytes сравнивать только
  в памяти, без публичных значений или производных fingerprints.
- [ ] Создание выполнять один раз после подтверждённого отсутствия. При потерянном
  ответе наблюдать тот же объект и точный ожидаемый frame, зафиксировать UID;
  не повторять create и не усыновлять чужой объект. Неизвестный исход создания
  namespace не может приводить к `normal_cleanup_verified=True`.
- [ ] Отделить безопасное provision/binding PVC от publisher, который пишет данные:
  до первой записи проверить каждый ранее captured claim UID/spec, выбранный
  PV UID/claimRef/reclaim policy и свежесть целевого binding. Same-name replacement
  или чужой PV запрещают запись, даже если publisher ещё не запускался.
- [ ] Исключение writers проверять по сохранённым ролям/UID и точному Pod frame,
  а не только по текущей метке `app-role`; изменение метки и неизвестный живой
  процесс запрещают второй start, purge и auth restore.
- [ ] Сначала штатно сигнализировать подтверждённый процесс и наблюдать завершение
  всех regular/init контейнеров. До этого не отправлять DELETE живому Pod:
  kubelet после grace способен выполнить SIGKILL; один finalizer это не исключает.
  Таймаут оставляет live/unknown состояние и запрещает maintenance.
- [ ] Штатный UID/RV DELETE необходим и для уже завершённого standalone Pod.
  Собственный finalizer снимать только после подтверждения deletionTimestamp,
  тех же UID/RV и завершения всех regular/init процессов; чужой не снимать.
  Успех Jobs требует exit0 всех контейнеров; TERM допустим только в подтверждённой
  фазе штатной остановки сервиса. OOM/KILL/restart не являются штатной очисткой.
- [ ] Отдельно проверить never-started cleanup: до DELETE сохранить UID/frame,
  waiting reason, отсутствие container ID/start times/restart, свежую API/CRI
  картину основного и всех init контейнеров. После DELETE синтетический unknown/137
  не считать normal exit. Observer может закрыть только прежнее подтверждённое
  never-started намерение с тем же Pod/Node UID и независимым отсутствием процесса;
  живой, ранее запускавшийся или неизвестный контейнер остаётся OPEN.
  Regression: `never_started_synthetic_status_observe_only`,
  `exit137_after_actual_start_refused`, `unknown_cri_prevents_finalizer_release`.
- [ ] Очистка после раннего отказа должна обнаруживать собственные PVC→PV bindings,
  включая тома, созданные после последнего наблюдения, и независимо подтверждать
  отсутствие namespace/PV. Неизвестные или чужие ресурсы сохранять с OPEN verdict.
  Собственный staging-каталог удалять по сохранённым UID/inode/device и после
  проверки пустоты; временные файлы и private kubeconfig учитывать отдельно.
- [ ] Заморозить source/tool/image identities до первого эффекта. Общий бюджет900с
  включает preflight и cleanup; истечение не расширяет его и не разрешает force.
  Сравнить workloads/replicas/PVC/PV и Node UID/Ready обоих кластеров с исходным
  baseline; исходный узел повторно проверять до каждого consumer/effect boundary.
  Home PASS требует одновременно пяти behavior verdicts, guards, штатной очистки,
  отсутствия retained live/unknown и сохранности существующей инфраструктуры.

Image pull policy и локальный cache — [Kubernetes: images](https://kubernetes.io/docs/concepts/containers/images/#image-pull-policy).
Техническое обоснование размещения — [Kubernetes: volume binding mode](https://kubernetes.io/docs/concepts/storage/storage-classes/#volume-binding-mode);
ограничение DELETE — [Kubernetes: Pod termination flow](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination-flow).

#### Checkpoint R5 и следующий R6 — 09.10.2026

R5 FAILED322,70/900 до PG/OIDC: stored object и ImageStatus были корректны,
но kubelet credential record связывал доступ с repository `postgres`, а новый
запрос использовал `docker.io/library/postgres`. По тем же config IDs PostgreSQL,
Qdrant и Keycloak уже имели node-wide access; Secret/ServiceAccount credentials
не требовались. Native repository spelling сохранён с тем же digest и canonical
stored object. Kubelet policy/records не изменялись. R5 retirement47,50/180 PASS,
исходный FAILED сохранён, оба baseline совпали.

Две причинные RED → Windows25/Linux25 PASS; Linux11,11/180. Короткий Pod без
Secret/PVC/БД выполнил все пять images exit0. Его controller FAILED11,70/180:
CPU `0.25` сериализован в `250m`, пустые mounts опущены, а containerd показал
дополнительные OCI index aliases. Три новых RED → Windows28/Linux28 PASS,
Linux11,30/180: alias допускается только после hash check и связи с sealed root
либо исходным linux/amd64 platform/attestation subset. Чужой digest/platform,
неизвестная attestation или отсутствие исходного platform отказывают.

Отдельный exact-frame observe-only retirement удалил Pod/namespace штатно;
запись результата остановилась из-за missing `results` attribute после удаления.
Независимый readonly observer2,97/60 подтвердил namespace/process absence и оба
baseline. Этот результат не превращает controller FAILED в PASS и не является
полным home OIDC proof. Полный отдельный R6 запущен в900с, его verdict ещё OPEN.
Private evidence: `home-oidc-r5.json`, `home-r5-retirement.json`,
`home-r5-kubelet-public-access.json`, `native-repository-spelling-red/green.log`,
`reported-index-alias-red/green.log`, `linux-guard-r8/r9.json`,
`home-image-access-r6.json`, `image-access-r6-retirement-finish.json`.

- [ ] Перед полным запуском сохранить repository spelling принятого native
  reference; canonical stored object и fresh config/root проверяются отдельно.
  `ErrImageNeverPull` может означать отказ доступа к существующему образу.
  Не отключать credential verification и не удалять kubelet records ради запуска.
- [ ] Для reported imageID проверить raw content SHA; OCI wrapper должен вести
  к sealed root либо содержать исходный выбранный linux/amd64 descriptor и только
  связанные исходные attestations. Нельзя добавлять все CRI repoDigests в allowlist
  без проверки. Передавать quantities в canonical форме в новых fixtures;
  API omissions не оправдывают общую нормализацию неизвестных полей.

#### R6: временной отказ, отдельная очистка и input frames — 09.10.2026

R6 FAILED900,11/900 на `inputs` перед последним `deny-old` probe. Настоящие старый/новый вход, отказ старой cookie с новым ключом после возврата auth-таблиц и отрицательный контроль со старым ключом завершились; полный behavioral verdict не получен. Срок и результаты исходного R6 не изменяются. Actor завершён; отдельный retirement129,08/180 PASS и независимый observer подтвердили namespace, все11 PV и process absence, оба baseline сохранены. Предыдущие retirement отказы сохранены: неверный порядок остановки PG перед backend исправлен; только подтверждённый CAS conflict допускает bounded retry с новым RV, unknown reply не повторяется. PG SIGTERM — smart shutdown, ожидающий завершения существующих сессий: [PostgreSQL17](https://www.postgresql.org/docs/17/server-shutdown.html).

Исходный `inputs` выполнял60 отдельных API/SSH вызовов. Readonly transport probe: четыре отдельные полные чтения0,234–0,266с, объединённое полное Node/Namespace чтение0,250с. Это подтверждает транспортные издержки, а не завершение функционального сценария. Восемь причинных RED → Windows42/Linux42 PASS без skips; Linux10,11/180, source/tool guards и normal cleanup. Новая проверка использует два независимых полных кадра namespace/Node/source/Secrets/PVC/PV и отдельное свежее Lease чтение в каждом: четыре transport вызова, без cache между admissions и без fallback. Размер каждого JSON≤2MiB, duplicate keys/objects, missing/foreign objects, Node/Lease/Secret/PV drift и изменённый binding отклоняются. Private bytes сравниваются только в памяти.

- [x] Сохранить original FAILED R6 и отдельно подтвердить owned retirement/оба baseline; исходная home приёмка остаётся FAILED.
- [x] Закрыть RED8 и полный owning Windows/Linux42 на точном helper5 candidate; runtime565/native18 не изменены.
- [x] Реальная readonly проверка: два полных input кадра, текущие identities всех11 PVC/PV, Node/Lease/source/Secret fences и оба baseline сохранены;0 effects,11,75/60с. Для чтения использованы retained R1 ресурсы; это не разрешение на их retirement.
- [x] Дополнительные causal DELETE tests: исходный focused4 FAIL/3 PASS сохранён; текущие Windows47 PASS1,34с и Linux47 PASS12,88/180с без skips. Только подтверждённый API Conflict допускает до трёх попыток с новым RV; unknown reply не повторяется, живой Pod не удаляется. Source/tool guards и штатная очистка Linux подтверждены.
- [x] Полный R7 PASS699,98/900: настоящий старый/новый вход, отказ старой cookie после возврата auth-таблиц с новым ключом, отрицательный контроль старого ключа и final purge/no revival; business/audit/held outbox сохранены. Functional/cleanup/baseline/source/deadline flags true. Независимый observer3,53/60 подтвердил отсутствие namespace, всех11 PV, API Pod и живых CRI процессов; оба baseline сохранены. Полный T3/T4/release остаётся OPEN.

Private evidence: `home-oidc-r6.json`, `home-r6-retirement.json`, `home-r6-retirement-independent.json`, `home-r6-readonly-transport-profile.json`, `input-frames-red/green42.log`, `linux-guard-r10.json`, `input-frames-r1-source/`. Дополнение: `input-frames-actual-readonly-r1.json`, `delete-conflict-red.log`, `delete-conflict-green47.log`, `linux-guard-r11.json`; source42 сохранён отдельно от текущего47. Полный R7 завершён на том же frozen helper47/runtime565/native18 за699,98/900с; `home-oidc-r7.json` и `home-r7-independent.json` сохраняют terminal/observer verdict. Исходный FAILED R6 не изменён.

#### Закрытие удержанного R1: отдельная операция
- [ ] Сохранить исходный FAILED и readonly inventory: namespace/Job UID,
  unresolved create intent, все11 claim UID/PV claimRef и исходный Node UID.
  Для Event с неизвестным Pod UID искать независимую связь с owned Job UID в
  сохранившемся API audit/CRI/controller evidence. Имя, labels и сам Event не
  являются достаточной привязкой; Event не удалять для обхода проверки.
- [ ] Если связь доказана, отдельный retirement record перечисляет источник
  доказательства, captured identities и допустимые UID/RV действия. Перед каждым
  эффектом повторить fresh inventory/no actors, затем подтвердить namespace/PV
  absence, отсутствие собственных staging/private файлов и сохранность baseline
  обоих кластеров. Lost reply обрабатывается observe-only; force запрещён.
- [ ] Если независимого evidence нет, сохранить ресурсы и причину OPEN; не
  переписывать исходный результат и не объявлять отсутствие Pod доказательством
  ownership. Следующий отдельный сценарий возможен после fresh capacity/baseline,
  включающего retained ресурсы; итоговый cleanup/release gate остаётся OPEN.

#### Границы доказательства и следующий deliverable — 09.10.2026

Домашний R6 завершился FAILED900,11/900 перед последним probe; отдельная штатная очистка129,08/180 и независимое отсутствие подтверждены. Причинные input-frame/DELETE checks и реальная readonly проверка приняты в указанных выше объёмах. R7 прошёл полный home OIDC scope699,98/900 и независимый observer3,53/60; source/capacity/Node/Lease/baseline guards сохранены. При следующем сценарии живой controller наблюдать до терминального результата и очистки. Превышение срока отдельного наблюдения не означает завершение controller и не разрешает второй запуск. Итог R7 записан отдельно от native R8 и прежних home FAILED. PASS требует всех пяти OIDC/antirollback/data assertions, исходного общего срока900с, неизменности source/images, штатной очистки собственных ресурсов и сохранности обоих baseline; terminal behavior без cleanup не является PASS. Удержанный R1 имеет отдельный retirement gate.

- [ ] После успешного home proof в существующих T3 tests и `lifecycle.py`/`postgres_tls.py` закрепить общий путь `protected_recovery_start`: независимое доверенное происхождение ключа/решения, exact operation/namespace/DB/Secret UID binding, all-provider purge и independent absence, запрет старта writer до завершения всей цепочки, terminal/unlock после свежих подтверждений. Сначала сверить уже принятые foundations; не создавать второй issuer или второй recovery controller.
- [ ] В owning recovery tests проверить `unknown_commit_observation_does_not_repeat_purge`, `ordinary_upgrade_preserves_session_key` и `restored_database_cannot_start_before_key_and_outbox_barriers`. Использовать фактические state/journal версии из spec; потерянный ответ допускает observation, но сам по себе не разрешает новый SQL/Secret/start/unlock эффект. Windows/Linux checks и native/home общий CLI proof привязываются к одному новому frozen snapshot.
- [ ] Отдельно закрыть полный непустой PG/Qdrant/files restore из T4/T6. Изолированная ротация с возвратом auth-таблиц не доказывает full restore, protected publisher или штатный operator CLI. До соответствующих assertions полный T3/T4 и release OPEN.
- [ ] В общем recovery/start повторить задачу 6 [rollout-плана](2026-10-03-kubernetes-rollout.md): восстановленные фоновые задания не выполняются до допуска нового target, historical outbox held и auth/key barriers проверяются до startup effects. Тесты используют настоящий startup и оба профиля PG TLS; ordinary restart сохраняет поддерживаемое восстановление очередей. Новый queued/lease restore контракт реализуется в этапе 3, а не объявляется существующим свойством compact.

#### Долговечная публикация ключа и допуск первого старта

**Найденный разрыв:** обычный `Manager.restore()` после импорта, hold outbox,
проверки totals/integrity и migrate вызывает `start()` и `release_lock()`.
В принятом source565 этот путь не связывает допуск старта с protected key
publication и всей session-maintenance цепочкой. Закрыть разрыв и для disabled,
и для enabled PG TLS; один лишь OIDC harness или provenance observer его не закрывает.

**Компонент567:** private кандидат добавляет только
`scripts/kubernetes/session_key_publisher.py` и
`backend/tests/test_kubernetes_session_key_publisher.py`; базовые565 файлов не
изменены. Generator создаёт private material один раз в памяти; builder связывает
его с existing native marker9, decision1 и immutable Secret через provenance1.
Сохранённые owning57 PASS и Windows/Linux compatibility128 PASS без skips;
Linux wrapper11,8/180 со штатной очисткой и отдельной проверкой отсутствия.
Независимое ревью:0 Critical/Important/Minor, компонент пригоден для интеграции.
Runtime worktree, main, operator archive и рабочие установки им не обновлены.
Durable publication, independent promotion и full recovery/start остаются OPEN.

**P1 private persistence — checkpoint569:** реализован
`scripts/kubernetes/session_key_storage.py` с API
`create_private_session_key_directory(path)` и
`publish_session_key_material_once(..., create: bool) -> bytes`. Private SQLite
`DELETE`/`synchronous=EXTRA` сохраняет intent до единственного RNG и material после.
Existing/incomplete/unknown store не разрешает повтор; observe не создаёт отсутствующий
файл. Root0700/file0600 либо проверенная Windows DACL; drift, junction/symlink,
hardlink, loose numeric types и expiry с учётом monotonic elapsed отказывают.
SQLite/journal приватны; приложение не переводится на SQLite. Механизм commit
опирается на [SQLite atomic commit](https://www.sqlite.org/atomiccommit.html) и
проверенные local filesystem locking/flush; physical power-cut gate этим не закрыт.

Новый operator archive включает generator/storage, current consumer читает полный
прежний schema3 состав и новый полный состав, отвергает частичное расширение.
Кит1/2 не изменены. Causal29 RED→29 PASS, дополнительные5 FAIL/32 PASS→37 PASS,
archive1 FAIL/39 PASS→40 owning PASS. Windows/Linux compatibility205 PASS/1 SKIP;
Linux23,19/180 и независимое container/volume absence, source565/native18 guards
подтверждены. SKIP — существующий standalone bootstrap с отсутствующим реальным
Helm на момент compatibility run. Затем тот же case отдельно выполнен с реальными
ранее сохранёнными tools: Windows1 PASS0,52с, Linux1 PASS0,65с, wrapper20,02/180;
source569/tools неизменны, независимое отсутствие трёх контейнеров/двух volumes
подтверждено. Этот case закрыт; исходный SKIP не переписан. Итоговый release
consumer/hosted CI на final snapshot остаётся отдельным gate.

Кандидат569 frozen отдельно; accepted runtime565 и main не обновлены. До закрытия
**всего P1** существующий recovery controller должен журналировать и проверять
подтверждённый first-dispatch ACK, затем вызывать create лишь для нового намерения.
Frozen controller570r2 имеет затронутую Windows/Linux compatibility, однако
genuine native ACK/process interruption ещё OPEN. После possible/unknown dispatch
или потери private store
разрешено только observe;
local flag не заменяет Kubernetes admission. Independent promotion/P2/P3, общий
CLI proof, full T3/T4/release и whole-branch review остаются OPEN.

#### Уточнения P1–P3 после проверки — 09.10.2026

**Исходный checkpoint P1 controller570:** расширен существующий `Manager` и explicit CLI
`postgres-recovery-generate-key-material`; public generation intent v1 хранится
в том же lock. Windows owning63 PASS40,78с после causal RED; сохранены проверки
invalid prerequisites, потерянного/неверного CAS ACK, drift, missing/incomplete
private material, строгого decoder и private CLI inputs. Это тесты с API fixture,
не настоящий Kubernetes ACK proof. На этом checkpoint кандидат ещё не был frozen/принят; результаты следующего
570r2 приведены ниже. Genuine native admission и следующий Secret effect OPEN.

**Checkpoint570r2:** полный затронутый набор из521 cases: Windows519 PASS/2 SKIP,
pytest150,11с, wrapper150,81/180; Linux520 PASS/1 SKIP, pytest78,03с,
wrapper97,62/180. Windows skips — native symlink privilege, Linux cases PASS;
Linux skip — Windows-only junction, Windows case PASS. Standalone bootstrap
включён с реальными tools. Frozen570r2 и принятые565/569/native18 не изменены;
штатная очистка и независимое отсутствие трёх контейнеров/двух volumes подтверждены.

Causal deadline/TTL regression5 FAIL/3 PASS →8 PASS: дочерний transport теперь
использует остаток controller90с/parent deadline, поле budget снимается в finally;
private storage получает реальное `now` отдельно от исходного `issued_at` и
проверяет deadline перед RNG/ready commit. Generator проверяет оставшийся срок
после input validation и после RNG. Это cooperative checks, не доказательство
ограниченного времени физического fsync при неисправном хранилище.

Linux R2 сохранён FAILED: дочерний срок120с, wrapper167,88/180, cleanup ещё не
завершился; поздний terminal pytest1 FAIL/519 PASS/1 SKIP206,28с выявил read-only
`data` в unit fixture. Отдельный штатный retirement1,36/60 и независимое отсутствие
подтверждены. R3 исправил только harness: writable tmpfs DATA_DIR/DIAGNOSTICS_DIR
и свежий private bytecode cache неизменных публичных исходников. Те же521 cases,
512Mi/1CPU/128Mi tmpfs, whole180/child120; пороги и исходный отказ не изменены.
Native ACK/process interruption, protected promotion/P2/P3 и выпуск остаются OPEN.

**Порядок:** confirmed generation intent → private material → confirmed new Secret
и independent promotion → completed rotation decision → SQL/schema/session purge
и independent absence → общий start/terminal/unlock. Existing rotation attestation
с `key_rotation_completed=True` не разрешает первоначальную генерацию.

- [ ] **P1: завершить native аварийный resume.** Deadline/TTL unit boundary
  уже реализован и проверен на frozen570r2 в общей matrix521 cases. Не создавать
  повторно `test_expired_intent_before_private_write_has_no_rng`,
  `test_slow_resume_inspection_cannot_extend_parent_deadline`,
  `test_lost_cas_reply_before_write_cannot_replay_on_same_manager` и
  `test_new_manager_existing_intent_is_observe_only` из
  `test_kubernetes_recovery_key_generation_controller.py`.
  Осталось проверить настоящий API ACK и потерю operator процесса до/после него
  и private commit в штатном CLI: новый процесс наблюдает прежнее намерение,
  не повторяет RNG/CAS/create и не получает startup permission. Controller90с
  и исходный intent/material TTL≤900с используют остаток parent budget без
  продления. Unit fixture response не заменяет этот native/process proof.
- [ ] **P1: отказ без автоматического repair.** Отсутствующий/неполный store,
  истёкший intent или неизвестный ACK сохраняют stopped/lock retained и фиксированную
  безопасную причину. Не удалять intent/store и не обходить отказ новым rotation ID
  в той же операции. Если штатный resume невозможен, repair выделяется в отдельное
  подтверждённое действие с независимым evidence; до его реализации автоматический
  repair запрещён. Сохранить private output для наблюдения; cleanup разрешается
  только по подтверждённому terminal состоянию и точной owned файловой identity.
- [ ] **P2: старый и новый runtime Secret.** Frozen572 уже связывает новый Secret
  с actual rotation decision и SQL Job через private overlay; старый `envFrom`
  native Job/reader provenance сохранён. Остаются настоящий API/new-process proof
  downstream schema/purge/absence и application generation, без повторной реализации
  проверенного old/new name+UID/RV binding.
  Проверить `new_secret_overlay_cannot_change_database_or_trust` и
  `rotation_decision_cannot_bind_old_secret_after_publication` в owning recovery tests.
  Все runtime поля кроме `APP_SECRET_KEY` должны совпасть с проверенным старым
  источником; сравнения остаются приватными. Первый Secret create требует ACK,
  unknown reply допускает только наблюдение; найденный объект не даёт новое
  dispatch/promotion permission.
- [ ] **P2: chart/VSO и поколения Pod.** `secrets.existingSecret` используется
  application, PG StatefulSet и VSO destination. Привязать overlay к выбранной
  application/maintenance generation без неразрешённого PG rollout, смены PVC или
  повторной записи VSO в immutable Secret. Проверить
  `runtime_secret_switch_preserves_postgres_template_and_pvc` и
  `secret_publisher_owner_conflict_refused`; один явно выбранный владелец Secret,
  актуальные application/Job refs и старое storage поколение проверяются через API.
- [ ] **P3: оба entrypoint и профиля.** Общий barrier должен вызываться настоящими
  `Manager.restore()` и recovery/resume/start до любого startup/writer effect.
  Enabled/native9 и ordinary disabled restore используют существующие собственные
  bindings; disabled не получает фиктивный TLS marker. Отрицательные тесты реального
  call path обязаны доказать, что нет обхода через migrate/start/recover-lock,
  reimport или рестарт оператора. Auth/outbox/jobs проверяются до startup hooks.
- [ ] **Итоговый gate:** один frozen интеграционный snapshot, owning и затронутая
  compatibility на Windows/Linux, standalone consumer; затем штатный CLI native/home
  с исходными budgets и independent cleanup. Один whole-branch review перед
  интеграцией; неизменные дорогие матрицы не повторять без причины. Непустой
  PG/Qdrant/files restore и требуемый CI остаются отдельными обязательными gates.

**Files / interfaces:** переиспользовать два API компонента567
`generate_session_key_material(...) -> bytes` и
`build_session_key_provenance(...) -> bytes`; существующие CLI, validators и
controller в `scripts/kubernetes/{lifecycle.py,postgres_tls.py}`; private persistence
в `scripts/kubernetes/session_key_storage.py`. Расширить
`backend/tests/test_kubernetes_{recovery_key_files,recovery_key_durable,recovery_key_durable_cli,lifecycle}.py`;
создать owning `backend/tests/test_kubernetes_recovery_key_publication.py`,
`backend/tests/test_kubernetes_recovery_key_generation_controller.py` и
`backend/tests/test_kubernetes_recovery_key_secret_publication.py`.
Controller API — `Manager.generate_postgres_recovery_key_material(...) -> dict`,
public validator — `decode_recovery_key_generation_intent(...) -> dict`; private
policy file передаётся только CLI/controller, public intent не содержит его hash.
Их текущие signatures закреплены в frozen570r2; controller/generator/storage
принимают optional `_deadline`, storage также `issued_at` отдельно от реального
`now`. Прежние callers, TTL и refusal semantics сохранены.
Не создавать второй recovery controller. Existing marker9/decision1/provenance1
и прежние archives остаются читаемыми; новый durable public record получает
явную версию и ограниченную схему до изменения writer, без private ключа/hash.

- [ ] **P1 — durable intent/private material.** Сначала RED
  `crash_after_key_intent_cannot_regenerate`, `lost_private_write_reply_is_observe_only`,
  `private_material_symlink_or_unsafe_acl_refused`, `expired_material_cannot_extend_ttl`.
  До RNG записать подтверждённый intent с operation/checkpoint/recovery/rotation
  binding; private output публиковать без overwrite в защищённый owned path,
  с проверкой файловой identity, Linux permissions / Windows DACL и durable write.
  После сбоя или unknown reply только наблюдать прежний intent/output; отсутствие
  проверяемого output после возможного RNG не разрешает повторную генерацию.
  TTL material≤900с сохраняется; expiry оставляет app stopped/lock retained,
  не обновляет timestamps и не создаёт новый ключ под прежним rotation ID.
- [ ] **P2 — Secret ACK и independent promotion.** RED
  `lost_secret_create_reply_cannot_republish`, `secret_uid_or_key_drift_refused`,
  `self_generated_trust_cannot_authorize_start`. Публиковать immutable version-named
  Secret только после current-state/fence проверок; требовать фактический ACK с
  UID/RV, затем повторные полные API чтения. Unknown create не повторять.
  Material/proof expected SHA получать из отдельного защищённого источника;
  self-hash и restored DB/Secret не выдают trust. Зафиксировать весь operator
  archive/toolbox/interpreter/dependency source set: checksum одного модуля не
  подтверждает среду исполнения. Private bytes и производные ключа не попадают
  в public evidence/stdout/errors. Ordinary operator consumer включает новый файл.
- [ ] **P3 — общий restore/start admission.** RED на настоящем `Manager.restore()`
  и resume/start: `restore_without_key_barrier_cannot_start_or_unlock` для обоих
  PG TLS profiles, `unknown_commit_observation_does_not_repeat_purge`,
  `database_reimport_invalidates_prior_start_admission`,
  `ordinary_restart_preserves_key_and_pending_sessions`.
  Допуск связан с текущей восстановленной DB generation, exact namespace/Secret
  UID и всей цепочкой rotation/purge/independent absence/outbox hold/owned Job
  cleanup. Изменение DB/Secret или потерянный COMMIT отзывает допуск; не повторять
  purge/import и не выполнять start/unlock по одному сохранённому PASS.
  После намеренного auth purge сравнивать бизнес-данные/audit/held outbox отдельно
  от ожидаемо удалённых sessions/flows: исходный totals не подменять новым baseline.
  Terminal/unlock требует свежих bindings, отсутствия maintenance writers и
  готовности выбранной application generation. Обычный upgrade/restart сохраняет
  ключ и pending sessions; новая ротация выполняется только при явном DR.
- [ ] **Проверка P1–P3:** owning RED→GREEN на Windows/Linux, один frozen source set
  и самопроверка изменённого diff; whole-branch review перед интеграцией; затем
  штатный общий CLI native и home с interruption
  до/после ACK, private write, COMMIT, start и unlock. Общий срок включает подготовку
  и cleanup, дочерние операции используют оставшийся budget. Изолированные R7/R8
  переиспользуются как behavioral evidence, но не заменяют эти CLI assertions.
  Полный непустой PG/Qdrant/files restore, R1 retirement и required release matrix
  закрываются своими gate; до этого full T3/T4/release OPEN.

#### Исполнение P2: frozen572, 09.10.2026

Typed VSO list defect закрыт causal1 FAIL/7 PASS →15 affected PASS;
полный owning57177 PASS65,47с. Root kind/apiVersion и item kind связаны с
фактически запрошенными producer resources; неизвестный CRD/version и отказ
доступа не считаются отсутствием VSO. CSISecrets пока явно unsupported.

Frozen572 переиспользует единый `_inspect_recovery_key_secret_context(...)` для
первой публикации и downstream readonly overlay, без повторного Secret create.
Actual rotation inspector принимает подтверждённый новый Secret только после
сравнения с сохранённым private material, исходным Secret и current owner/fence.
`recovery_application_sql_job_plan(...)` берёт runtime ref из проверенного decision;
его наследуют schema/purge/absence builders. Old native Job/PG template не меняются.
Успешное переключение application generation и общая цепочка ещё не реализованы.

Private CLI transport: `--recovery-key-overlay-file` и
`--expected-recovery-key-overlay-sha256` только для rotation/maintenance/provenance
операций. Closed policy v1 ограничен8192 bytes, source/material pins, абсолютными
private paths, проверенными POSIX modes/Windows DACL, rotation/name/TTL. Policy
остаётся входной конфигурацией, не независимым issuer или startup permission.
Positive CLI test использует actual controller с API fixture; genuine new-process
Kubernetes CLI не подтверждён. Новая owning file:
`backend/tests/test_kubernetes_recovery_key_runtime_overlay.py`.

Сохранены causal overlay13 FAIL/1 PASS →14 PASS; CLI16 FAIL →15 PASS/1 FAIL
из-за неверного recovery ID в fixture, затем30 PASS. Parent3 FAIL/3 PASS →
owning36 PASS38,62с. Общий publication/overlay113 без skips: Windows113 PASS
117,05с, wrapper119,03/180; Linux113 PASS, wrapper60,97/180. Normal cleanup и
independent3-container/2-volume absence0,77/60 подтверждены; frozen572/565/native18
и tools неизменны. Legacy path не выдаёт publisher/start/writer/unlock.

Полный53-module1926-case run НЕ прошёл временной критерий: Windows timeout180,16/180,
Linux child120 elapsed и cleanup initially retained, wrapper167,27/180. Тот же
captured Linux unit позже terminal exit0:1925 PASS/1 Windows-only SKIP325,69с.
Исходный FAILED сохранён; отдельный normal retirement1,33/60 и independent
absence0,74/60. Windows exact candidate Python actors отсутствуют. Functional
terminal PASS не отменяет timing failure. Выполнены disjoint base521,
publication113, SQL/schema472, purge382, absence438 на том же source572;
каждый wrapper180/child120 и512Mi/1CPU/128Mi tmpfs остаются прежними. Их результаты
не закрывают исходный общий1926-case timing gate; это отдельно проверяемое покрытие.
Все disjoint groups завершены: Windows1924 PASS/2 native-symlink SKIP, Linux1925
PASS/1 Windows-only junction SKIP; применимые symlink/junction cases покрыты на
другой платформе. Каждый Linux cleanup независимо подтверждён; Windows actors0.
Windows SQL/schema472 group также сохранён FAILED180,16/180: затем те же
modules отдельно SQL245 PASS145,62/180 и schema227 PASS64,11/180. Остальные
Windows groups: purge controllers128 PASS81,39/180, contracts254 PASS38,38/180;
absence controllers164 PASS100,94/180, contracts274 PASS10,24/180. Baseline519
PASS/2 SKIP150,42/180. Linux groups whole: base87,73, publication60,97, SQL/schema
99,88, purge72,56, absence84,08; каждый <180 и child<120. Это функциональная
совместимость на одном snapshot, не PASS исходного общего timing gate.

Runtime565/main/рабочие установки не обновлены. Native ACK/promotion, application
switch, общий disabled/enabled restore/start, непустой restore и выпуск OPEN.

#### P2-A: chart и ordinary ref guard — frozen573, 09.10.2026

Реализовано `application.runtimeSecretName: ""` с прежним fallback; только application
получает новый ref, полные PG/Qdrant/VSO templates и frontend сохранены. Schema
проверяет имя/тип/размер и наличие поля в объединённых values: Helm null удаляет
optional field, поэтому после наблюдённого1 FAIL/126 PASS поле стало required при
сохранении default empty. TLS server/client names не допускаются как application runtime.

Existing `Manager.chart()` перед эффектом дважды читает свежий настоящий Helm render,
current Deployment/runtime Secret/lock. Старый runtime ref, foreign operation/release/
namespace/UID/RV, дрейф объектов/values и live recovery metadata отказывают с безопасной
фиксированной ошибкой; same-ref ordinary image rollback и первоначальная установка
с существующим Secret сохранены. Public permission не добавлено; selected ref не
означает независимый origin или разрешение первого recovery start. Guard не доказывает
защиту прямого внешнего Helm/API управления и пока не хранит active binding после
удаления Deployment/lock: это требуется в общей P3 цепочке до native приёмки P2-A.

Causal chart19 FAIL/1 PASS; lifecycle16 FAIL/2 positive controls; owner6 FAIL/3 controls.
Final frozen573: восьмимодульный набор368 PASS на Windows94,73/180 и Linux39,00/180,
без skips. Operator archive/standalone bootstrap с реальными tools: Windows38 PASS,
Linux38 PASS22,09/180, без skips. Оба Linux lab штатно удалены; independent observers
0,75/60 и0,77/60 подтвердили отсутствие трёх контейнеров/двух volumes каждого lab.
Общий coverage этой затронутой области406 cases на каждой платформе; это не full
backend/CI/release gate. Frozen572, accepted565/native18/tools и original FAILED
сохранены; runtime/main/рабочие установки не обновлены.

Private evidence: `application-generation573/candidate-source573.json`, `brief.md`,
`windows-chart-red.log`, `windows-chart-green.log` (первый null FAIL сохранён),
`windows-generation-red.log`, `windows-owner-red.log`, `windows-final.json`,
`windows-release.json`, `r1-linux/linux-r1-compat.json`, `r2-release/linux-r1-compat.json`,
оба `linux-r1-independent.json`, `compatibility-summary.json`.
Next: actual new-generation dispatch и durable active binding в existing controller,
independent promotion/common admission, genuine new-process native/home полный restore.
P2-A целиком, P1/P2/P3/T3/T4 и release остаются OPEN.

#### P2-A: сохранение active binding — frozen574 и проверка плана, 09.10.2026

В private574 реализованы closed public record v1≤4096 bytes в annotation
`okf-knowledge/runtime-generation` существующего Helm-owned ConfigMap `runtime`,
decoder `decode_active_runtime_generation(body, expected_binding, *, now)` и его
сохранение через `application.runtimeGeneration`. Ordinary `Manager.chart()`
сверяет Namespace/ConfigMap/Secret identities и stored Helm values, несёт прежнюю
запись явно и отказывает при односторонней потере/конфликте metadata. Deployment и
lock могут отсутствовать. Запись не содержит ключа/его производных и не выдаёт
origin, first-start или writer permission. Её первого производителя ещё нет.

Сохранён owning Windows результат:157 PASS43,90с, wrapper44,51/180, source unchanged.
Linux, полный затронутый набор, operator consumer и native/home для574 ещё OPEN.
Прежние573406-case Windows/Linux результаты относятся к573, а не к574. Первоначальные
RED/fixture ERROR и FAILED572 не заменяются новым PASS. Evidence:
`runtime-generation574/candidate-source574.json`, `brief.md`,
`windows-owning-final.json`/`.log`.

Review на настоящем Manager/Helm render с существующей native API/effect fixture:
положительный контроль прошёл; guard также допустил Helm effect при immutable
False/отсутствующем/строковом значении Secret, Helm pending-upgrade/failed и
одновременном отсутствии runtime ConfigMap, Helm history и Deployment. Последний
случай не доказывает свежую установку: предыдущие данные/поколение могли сохраниться.
Это воспроизведённые пробелы кандидата; кластерных эффектов не было. Первая попытка
fixture без Helm на PATH отказала также на positive control и исключена из evidence;
исправлен только PATH, source574 не менялся. Private record:
`2026-10-09-plan-review574-current/behavior-review-r2.json` в локальном review каталоге.

До первой активации закрыть: immutable/current Helm status, explicit fresh-install
vs metadata-loss, настоящий protected producer/admission и crash-safe запись
ConfigMap+Helm values. На ordinary reuse уже активированного поколения исходный
TTL intent не проверяется как срок жизни ключа: запись сохраняется без переиздания
TTL. Исходный TTL≤900с обязателен для первой recovery активации и ещё не отправленного
start; истечение не разрешает новую запись/ключ под прежней операцией.

#### P2-A: отказные границы — frozen574-r2, 09.10.2026

Исправлены два воспроизведённых пробела574 в existing `Manager.chart()`:
при active binding runtime Secret требует строго `immutable is True`; текущий
Helm release — deployed и canonical positive revision1..2147483647, строка или
exact int, без bool/float/leading zero. В обоих кадрах сравнивается полный текущий
history/values/object; malformed/pending/failed не разрешает новый Helm effect.
Первоначальный legacy/default путь без active record сохранён. Другие recovery
наблюдатели могут читать failed; такой readonly результат не разрешает startup.

Causal53 FAIL/4 positive PASS → owning Windows214 PASS49,12с, wrapper51,03/180.
На одном frozen574-r2: affected471 Windows63,19/180 и Linux60,62/180; extracted
operator/archive consumer38 Windows8,31/180 и Linux23,06/180. Итого509 distinct
cases на каждой платформе, без skips, включая real Helm/kubectl bootstrap. Прежний
cache_dir warning сохранён. Оба Linux lab штатно очищены; independent observers
0,83/60 и0,77/60 подтвердили отсутствие3 containers/2 volumes каждого. Windows
candidate Python actors0. Исходные180/child120/512Mi/1CPU/128Mi tmpfs не менялись.

Изменены только `lifecycle.py` и его существующие owning tests относительно574;
record/schema/render изменения574 вошли в эти affected/consumer проверки. Frozen574,
573/572, accepted565/native18/tools и прежние FAILED сохранены. Runtime/main и
рабочие установки не обновлены. Это приёмка refusal repair, не first activation,
native ACK/origin, общий admission, весь P2-A или full T3/T4/release.
Evidence: `runtime-generation574-r2/{candidate-source574-r2.json,brief.md,
windows-refusal-red.log,windows-owning-green.json,windows-affected.json,
windows-release.json,compatibility-summary.json}`, оба Linux record/independent,
`windows-actors-independent.json`, source-bound `next-common-admission-call-map.json`.

Очередь на checkpoint574-r2 (актуальное продолжение приведено ниже): двойная потеря
durable metadata, explicit fresh/adoption и
настоящий общий owner/admission. Начальный preflight требует предварительно созданные
PVC: запрет всех существующих claims сломал бы контракт. Нужны actual empty/owned
данные и PV bindings; отсутствие workloads не доказывает пустоту томов. Disabled
`acquire()` пока не связывает native ACK/UID как полноценный общий owner; enabled
checkpoint/native9 нельзя использовать для подмены этой проверки. Затем P2-B/P3-A
и первый controller activation/completion, новый process/native/home full restore.

#### Common owner: private575-r2, 09.10.2026

Existing `Manager.acquire/run/release_lock` проверяют native create ACK, полный
Namespace/lock frame и канонические context/namespace/release/operation перед effects.
Unknown ACK не разрешает повтор; unlock использует UID/RV preconditions и проверяет
native reply, затем отсутствие объекта. Same-process receipt хранится в памяти:
это ещё не durable resume, admission или разрешение старта после перезапуска.

Causal71 FAIL/6 controls →77 PASS; последующая scope regression8 FAIL/85 PASS →93 PASS.
Frozen575-r2: Windows owning93 PASS, wrapper3,41/180; affected564 PASS без skips,
pytest62,12с, wrapper63,86/180. Linux affected564 PASS60,19/180; extracted consumer38
Windows PASS8,25/180, Linux PASS22,56/180. Итого602 distinct cases/platform без skips
с real Helm/kubectl bootstrap. Linux lab штатно очищены, independent absence0,78/60
и0,80/60 подтвердили отсутствие каждого3 containers/2 volumes; Windows actors0.
Source565/native18/tools и original FAILED сохранены. Native API transport в owning
тестах fixture; consumer bootstrap не выполняет настоящую recovery/start операцию.
Evidence: `operation-owner575-r2/{candidate-source575-r2.json,windows-scope-red.json,
windows-owner-green.json,windows-affected.json,windows-release.json,
compatibility-summary.json}`, оба Linux normal/independent records и Windows actor
observer. Actual575 файлов совпали с seal при
этой проверке; source575/574-r2/574/573/572 сохранены. Runtime565/main не обновлены.

#### Проверка полноты P3-O — checkpoint576, 09.10.2026

Private576 добавляет отдельный retained ConfigMap `okf-operation-state` с фазами
`acquire_prepared → owned → unlock_prepared → completed`. Записи связываются с
context/namespace UID/release/operation и lock UID/RV; неизвестный ответ create/delete
не разрешает новую операцию. Это основа отказа, ещё не успешное аварийное продолжение.

На576-r2 основной набор605 и standalone consumer38 PASS без skips на Windows/Linux:
Windows67,28/180 и7,47/180; Linux59,16/180 и22,52/180. Дополнительные direct callers
в двух модулях дали16 FAIL/52 PASS; на исходном575-r2 тот же набор дал12 FAIL/56 PASS.
Private576-r3 исправляет только transport/Namespace/real Helm fixtures:64 PASS/4 FAIL,
wrapper18,83/180 на Windows. Три оставшихся отказа относятся к порядку безопасной
checkpoint-диагностики, четвёртый — к прежнему разрешению legacy2 unlock без common
owner receipt. Успех основного набора не закрывает общую совместимость; original
FAIL сохранены. Для576-r3 Linux и consumer на этой ревизии ещё не проверены.
Runtime565/main и рабочие установки не обновлены.

**Следующий самостоятельный результат — законченная регрессия общего владельца:**

- [x] Сохранить read-only причины отказа `release_lock()` для отсутствующего TLS
  checkpoint/удалённого pointer и backup3 до обязательного отказа без owner receipt.
  Диагностика не выдаёт разрешение на delete/start и не ослабляет own-ACK проверки.
  RED→GREEN: существующие `test_unrecorded_tls_lock_cannot_be_released`,
  `test_removed_lock_pointer_cannot_bypass_retained_checkpoint[release]`,
  `test_ordinary_unlock_cannot_bypass_backup3_journal_preservation[3]`.
- [x] Явно закрепить legacy2 контракт: без подтверждённого common owner обычный unlock
  запрещён. Изменить старое positive ожидание в версии2 на отказ/no native mutation,
  сохранить separate positive acquire → completed checkpoint → conditional unlock.
  Adoption/repair старой установки остаётся явным путём, а не delete по operation ID;
  это намеренное изменение прежнего разрешения, не «исправление» защиты ради теста.
- [x] Построить список затронутых тестов по actual callers `acquire/recover_lock/
  release_lock`, включая checkpoint и standalone backup unlock, затем проверить
  косвенные вызовы из restore/upgrade/provision и fixtures. Files:
  `test_kubernetes_postgres_tls_checkpoint.py`,
  `test_kubernetes_backup_standalone_unlock_observation.py`, owning owner/journal,
  lifecycle и текущие affected modules. После правки продукта owning135 и closure68
  должны пройти; полный текущий union —673 cases, отдельно consumer38. Это711
  различных cases/platform, не сумма owning с тем же affected набором. Сохранить
  collected node IDs и список модулей; число само по себе не доказывает полноту.
  Новые resume cases расширяют union, их нельзя исключать ради прежнего числа.
- [x] На одном окончательном frozen candidate выполнить affected/consumer Windows
  и Linux, real Helm/standalone bootstrap, source guards и independent cleanup в
  исходных бюджетах. Fixtures576-r3 не переносят PASS576-r2 на новую ревизию.
  После зелёной совместимости переходить к положительному resume ниже; нельзя
  интегрировать отказный компонент как законченный аварийный operator CLI.

Evidence: `operation-owner575-r2/windows-closure.json`,
`operation-journal576-r2/{windows-affected.json,windows-closure.json,windows-release.json}`,
Linux platform/independent records и
`operation-journal576-r3/{candidate-source576-r3-fixture.json,windows-closure-fixture.json}`.

#### Common journal576-r4 — регрессия и native ACK, 09.10.2026

Предыдущие576-r2/r3 результаты выше сохранены. В576-r3 исправлен порядок read-only
checkpoint/backup3 диагностики, legacy2 unlock без common owner остаётся запрещён.
Causal3 FAIL/65 controls →closure68 PASS14,69/180; owning135 PASS4,42/180.
Windows673+38 PASS. Linux673 дал1 FAIL: временная копия chart унаследовала0444,
и test получил PermissionError до обращения к guard. Original FAILED77,86/180
сохранён; independent absence0,80/60 подтвердил очистку3 containers/2 volumes.

Frozen576-r4 меняет только этот fixture: chmod0600 у собственного временного
`Chart.yaml` перед намеренной порчей. Продукт byte-identical576-r3; readonly source,
UID1000, limits/budgets и требуемый отказ не менялись. Final affected673+consumer38:
**711 distinct PASS/platform, 0 SKIP**. Windows100,20/180+8,75/180;
Linux80,02/180+24,78/180. Exact711 node IDs/14 modules собраны на этом же seal.
Linux normal cleanup и independent absence0,98/60+0,95/60 подтверждены.

Actual Kubernetes common owner proof46,81/180 выполнял actual Manager в отдельных
OS processes: create ACK/current binding →scoped ConfigMap effect →conditional
unlock →retained completed; затем следующий процесс начинает новую операцию после
completed. Контролируемая потеря ответа на native boundary после реального lock
create/delete сохраняет `acquire_prepared`/`unlock_prepared` и запрещает следующему
процессу acquire. Два собственных namespace удалены условно после terminal всех
шести child processes; независимый API observer0,77/60 подтвердил отсутствие
namespace/Pods/PV и прежний Ready Node UID. Namespace baseline не изменился.

**Граница:** этот proof — disabled common owner/ConfigMaps; он не восстанавливал
PG/Qdrant/files, не публиковал Secret и не запускал приложение. Новая операция после
completed не является resume прерванной операции. Original expiry, explicit
ownership handoff/own-CAS receipt refresh, fresh/adoption, independent origin,
common admission и first activation остаются OPEN. Runtime565/main/рабочие установки
не обновлены; полный backend/CI и release не закрыты.

Evidence: `operation-journal576-r4/{candidate-source576-r4.json,
compatibility-summary.json,coverage-collection.json,windows-affected.json,
windows-release.json,native-owner.json,native-owner-independent.json}`, оба Linux
records/independent observers. Original failure:
`operation-journal576-r3/r1-linux/linux-r1-compat.json` и отдельный failure observer.

#### Исходный срок577 — selected regression и native expiry, 09.10.2026

Frozen577 исправляет захват исходного срока: создание Manager до preflight;
более короткий caller parent только сокращает expiry/monotonic cap, `issued_at`
сохраняется. Published lifetime не переиздаётся. Causal preflight RED2 → owning153
**PASS5,31/180**. Предыдущие RED16/status RED1/RED2 и historical GREEN151 сохранены.

Selected affected692 + standalone consumer38: **730 distinct PASS/platform,
0 SKIP**, exact730 node IDs/14 modules на том же final seal.
Windows94,27/180+10,83/180; Linux75,73/180+18,78/180. Linux normal cleanup и
independent absence1,08/60 каждого3-container/2-volume стенда подтверждены.

Actual native81,80/180: настоящий package-tools/stdlib bootstrap извлёк тот же
operator source; mutation paths вызывали actual Manager из архива, новый процесс
вызывал штатный extracted `operation-status` до/после expiry. Positive effect
до истечения прошёл; после original25с effect и unlock отказали, journal/lock/marker
не изменились, readonly status сохранил прежний expiry и не выдал authority.
Поздний caller deadline срок не продлил. Повторены create/delete ACK-loss refusals
и новая operation после completed; это не положительный interrupted resume.
Все9 процессов terminal;3 собственных namespace условно удалены, baseline прежних
namespace и Ready Node UID сохранён. Independent API observer0,91/60 подтвердил
namespace/Pods/PV absence. Lab release identity подтверждала транспорт архива,
не независимое происхождение и не разрешение DB/key/start/restore.

**Constructor closure:** standalone `run/kube/apply` до acquire расширили scope:
дополнительно62 modules/**2075** distinct cases в7 заранее объявленных группах,
max420 cases/max10 modules; прежние whole180/child120/resources сохранены.

- Linux: **2072 PASS/3 Windows-only SKIP**. Все7 групп прошли normal cleanup и
  independent absence собственных3 контейнеров/2 томов. Original g1 FAILED из-за
  ошибочного ожидаемого SKIP count сохранён; g1-R2 исправляет только driver expectation.
- Windows g1–g3/g6–g7 завершены. Original g4 **FAILED180с**; поздний log331 PASS/
  pytest180,46с не закрывает gate: exit code после wrapper timeout не зафиксирован.
  PID absence подтверждена, процесс не убивался/не перезапускался. Контрольный
  g4-R2 на прежнем source, в одиночку с новым cache/tmp/data: **331 PASS155,17/180**.
  Гипотеза и durations сохранены; один PASS не устанавливает причину замедления
  и не переписывает original FAILED.
- Windows g5: **3 FAIL/345 PASS76,28/180**, создание настоящего symlink в fixture
  завершилось WinError1314. Это не подтверждение поведения продукта на Windows.

**Исторический test-only этап578 до product diff:** изменён только `test_kubernetes_postgres_tls_recovery.py`:
пропуск исключительно фактического Windows WinError1314 с явным незакрытым
acceptance; другие ошибки остаются FAIL. Product/operator/chart bytes совпадают
с577. Модуль: Windows36 PASS/3 SKIP9,08/180; Linux39 PASS18,31/180 с настоящими
symlinks, normal cleanup и independent absence0,80/60. Это не приёмка всего g5
или нового product snapshot. Применимые Windows native-file-symlink cases OPEN
до Windows runner с доступным созданием symlink; права машины автоматически
не менять. Linux proof не заменяет Windows acceptance.

Selected730 не является всем caller scope. Итоговый union использует disjoint
node IDs, без двойного учёта owning/consumer или объединения разных source.
Original whole1926 timing FAILED и collection-verifier error сохранены: parser
исправлен только для пути модуля, parameter ID с slash не нормализуется.
На момент этого checkpoint не были закрыты Windows caller/fixture acceptance и
final affected/consumer после own-CAS; subsequent578 component proof приведён ниже.
Применимые Windows symlink cases, positive handoff, clock contract другого host, explicit expired repair,
fresh/adoption, independent origin, common admission, first activation и
home/full T3/T4/release остаются OPEN. Runtime565/main/рабочие установки не обновлены.
Evidence: `operation-lifetime577/{candidate-source577.json,final-freeze.json,
windows-preflight-owner-green.json,windows-affected-final.json,windows-release-final.json,
coverage-collection.json,compatibility-summary.json,native-owner.json,
native-owner-independent.json,constructor-caller-review.json,caller-collection.json,
windows-callers-g4.json,windows-callers-g4-terminal-observation.json,
windows-g4-r2-hypothesis.json,windows-callers-g4-r2.json,windows-callers-g5.json}`;
`callers-linux-g1-r2` и `callers-linux-g2`–`g7` normal/independent records;
`operation-cas578/windows-recovery-package-fixture.json` и `fixture-linux` records.

#### Own-CAS578 — компонент и границы приёмки, 09.10.2026

Финальный frozen578 содержит product diff в `scripts/kubernetes/lifecycle.py`
и два test diff; это другой seal, чем ранняя test-only fixture578. Existing Manager
переносит common owner receipt через собственный lock CAS только после exact native
ACK, двух полных Namespace/lock frames и отдельного ACK/repeat frames common state.
Потеря любого ACK, foreign drift или expiry запрещают следующий effect/replay/unlock;
исходный lifetime сохраняется. Passive GET не выдаёт authority.

- Causal24: **20 FAIL/4 controls →24 PASS**; owning177 PASS6,39/180 — подмножество,
  не дополнительные177 cases. Ошибочный первый owning driver с nonexistent node ID
  завершился collection ERROR до тестов; исправленный driver и original ERROR сохранены.
- Final affected716+consumer38: **754 distinct PASS/platform,0 SKIP**.
  Windows84,66/180+8,31/180; Linux69,30/180+17,08/180; independent cleanup0,83/60 и0,91/60.
- Final g5 отдельно: Windows**345 PASS/3 SKIP71,89/180**, Linux**348 PASS46,62/180**,
  independent0,83/60. Пропуски только actual WinError1314; Windows native-symlink
  acceptance OPEN. Original577 g5 FAIL, g4 timing FAIL и whole1926 FAIL сохранены.
- Actual extracted operator/native API: **13 terminal OS actors,5 owned namespaces,
  120,83/180**, independent1,34/60. Проверены own lock CAS → common state ACK →
  scoped ConfigMap effect → conditional unlock; потерянные lock/common ACK блокируют
  same/new process. Namespace/Pod/PV absence, baseline и Ready Node UID подтверждены.

Enabled component использует actual certificate parse/validation; native proof —
общий disabled owner/ConfigMaps. Не проверены все typed recovery writers,
непустой PG/Qdrant/files restore, key publication, DB admission или first start.
Новая operation после completed не является handoff прерванной операции.
Ближайший deliverable: typed writer chain/size/RV acceptance → positive handoff
с проверкой прежнего OS owner → fresh/adoption → independent origin/common admission.
Full constructor caller closure на final578, home/full T3/T4/backend/CI/release OPEN;
runtime565/main/рабочие установки не обновлены. До итоговой поставки воспроизводимые
private harness/tests включить в версионированный общий тестовый комплект.

Evidence: `operation-cas578/{stage578-sources.json,candidate-source578.json,
final-freeze.json,own-cas-writer-inventory.json,compatibility-summary.json,
windows-affected-final.json,windows-consumer-final.json,windows-callers-g5-final.json,
native-owner.json,native-owner-independent.json}`; `cas-linux-affected`,
`cas-linux-consumer`, `cas-linux-callers-g5` normal/independent records.
`final-freeze.json` сохраняет исторический pending расширенной unlock assertion
на момент freeze; последующие final affected и native records подтверждают её
выполнение, не изменяя запись freeze.

#### Typed lock RV579 — первый writer, 09.10.2026

`replace_postgres_recovery_lock` возвращал desired со старым RV21 при native ACK22;
следующий exact CAS отказал. Actual Manager.acquire, валидированный enabled материал,
version1 ownership parser и штатный permit/run: **2 causal FAIL/7 controls →9 PASS**.
Минимальная правка возвращает уже проверенный native ACK; guards не ослаблены.
Версия1 здесь — публичные ownership данные, не native9 или DB/key/start permission.

Frozen579: **affected449+consumer38 =487 distinct PASS/platform,0 SKIP** в12 modules.
Windows:368 PASS108,66/180;81 PASS12,45/180;38 PASS7,39/180.
Linux:368 PASS57,84/180;81 PASS20,19/180;38 PASS17,59/180; independent0,86/60,
0,80/60,0,83/60. Все9 owned containers/6 volumes штатно удалены; independent absence,
source578/accepted565/native18/tool guards сохранены. Owning9 входит в union.
Collection487 прошёл exit0; первоначальный path verifier ожидал `backend/tests/`
вместо pytest `tests/` и отказал. Исправлен только prefix модуля, parameter suffix
сохранён; collection не перезапускался, original verifier error записан отдельно.

Граница: вызван первый typed helper, не full claim CLI или actual native579 proof.
Три других helpers с `return desired` и полная recovery цепочка ещё OPEN.
`claim_postgres_recovery` не выполняет common acquire/handoff: новый Manager не
может acquire уже занятый lock. Поэтому приёмка claim/resume из нового процесса
зависит от утверждённого P3-O verified handoff; passive GET/старый ACK не заменяют его.
Дальнейшие компонентные writer tests возможны независимо, но full new-process
chain требует собственного common owner ACK и общего admission до effects.
Runtime565/main/домашнее приложение не обновлены; full restore/CI/release OPEN.
Evidence: `operation-typed579/{candidate-source579.json,freeze.json,
recovery-lock-call-map.json,affected-collection.json,compatibility-summary.json,
windows-typed-red.json,windows-typed-green.json,collection-verifier-history.json,
typed-entrypoint-dependency.json}` и Windows groups/Linux normal+independent records.

#### Проверка плана577 — исходный срок и совместимость CLI, 09.10.2026

**История review до ремонта:** private577 добавляет lifetime version2 и read-only
common status; на момент этой проверки он ещё не завершён и не принят в runtime565/main. Повтор review на actual Manager дал
**2 FAIL, whole2,47/180**: после preflight901с `acquire()` выдал новый900с, а после
preflight5с и более короткого parent переиздал `issued_at`. Это воспроизведённый
продуктовый дефект кандидата, не timing-only отказ стенда. Сохранить original RED.
Отдельный actual CLI test сохранения PostgreSQL `phase`/`migration` с вложенным
`common` — **1 PASS, whole2,92/180**; он не закрывает срок, resume или полный CLI.

**План исправления на момент review:** закрыть initial lifetime до preflight в
existing `Manager.__init__`/`_operation_parent_start`; поздний shorter parent только
сокращает unpublished expiry и monotonic cap, не меняет исходный `issued_at`.
После публикации lifetime неизменен. Успешные576-r4 проверки не переносить на577.
Owning set с новыми cases планируется153, affected692 + consumer38 =730 различных
cases/platform; окончательное число подтвердить collection после final freeze.
Не складывать owning с тем же affected набором. На момент review Windows/Linux и native577 ещё OPEN; subsequent repair/приёмка описаны выше.

Evidence: `operation-lifetime577/{windows-plan-review-parent-red.json,
windows-plan-review-parent-red.log,windows-plan-review-status-green.json}`;
предшествующие RED16, status-phase RED1 и GREEN151 сохранены отдельно. Main и
рабочие установки этой проверкой не менялись.

#### Действующая очередь P2/P3 после сверки572 — 09.10.2026

Сверены frozen572, действительный chart и вызовы `Manager.restore/start/migrate/
recover_lock/release_lock`. Publication/rotation/SQL overlay и полное затронутое
функциональное покрытие572 завершены в указанном выше объёме; native ACK, application
switch, independent promotion и общий admission не реализованы/не приняты целиком.
Frozen573 добавил chart/ref guard;574 — decoder/carry,574-r2 — strict current
Secret/Helm refusal с affected+consumer Windows/Linux. Private575-r2 — process-local
common owner; owning/affected и consumer Windows/Linux602 cases/platform проверены;
Common ACK/unlock576-r4 проверен actual Kubernetes API,711 distinct PASS/platform;
положительный interrupted resume и полный caller closure OPEN. Компоненты577–579
проверены в указанном выше объёме; custody580 и journal custody581 component/native пройдены. Следующие
deliverable учитывают эти границы;
исторические результаты ниже сохраняются. Это план работ, а не разрешение обновить
runtime или кластер. Порядок: fresh/adoption P2-A и durable owner P3-O → P2-B protected origin → P3-A общий admission → завершение
P2-A первой активацией → P3-B native/home. Decoder и
дополнительные standalone проверки не заменяют настоящего producer/start пути.

- [ ] **P2-A — application ref, persistent binding и управляемая активация.** Files:
  `deploy/helm/ai-knowledge/{values.yaml,values.schema.json,templates/application.yaml,
  templates/config.yaml}`, `scripts/kubernetes/{lifecycle.py,postgres_tls.py}`.
  Tests: `test_kubernetes_chart.py`, `test_kubernetes_application_runtime_generation.py`,
  `test_kubernetes_runtime_generation_binding.py`, существующие overlay/lifecycle/release
  tests. Consumes: confirmed Secret ACK, independently protected promotion и полный
  P3-A admission. Produces: одна выбранная generation, согласованные ConfigMap annotation
  и Helm stored values; обычный `Manager.chart()` только переносит готовую запись.
  - [x] Application-only ref/schema/default fallback и ordinary reapply guard573;
    maintenance использует verified decision, PG/Qdrant/VSO refs не изменены.
  - [x] Decoder/carry574 после удаления Deployment/lock: owning Windows157 PASS;
    это частичная проверка, первая активация и межплатформенная приёмка OPEN.
  - [x] Causal RED→GREEN `active_generation_requires_immutable_secret` на574-r2: False,
    missing, null, string и numeric не допускают `chart(True/False)`/`start()`;
    допустимо только `immutable is True` и тот же exact Secret name/UID/RV/body.
    Сохранить положительный legacy/default install и same-ref image rollback.
  - [x] `pending_or_failed_helm_release_cannot_start_active_generation` на574-r2: обычный
    active путь требует deployed и положительную целую revision, одинаковые в
    обоих кадрах. Pending/failed/uninstalling/unknown/malformed требуют отдельного
    явного recovery; read-only наблюдение failed не становится startup permission.
  - [ ] `lost_generation_and_helm_history_cannot_be_assumed_fresh_install`: отсутствие
    обеих записей и Deployment не даёт начального допуска. Fresh install требует
    явно выбранной начальной операции и фактического подтверждения пустых owned
    DB/Qdrant/files/PVC в новом namespace UID; предварительно созданные PVC допустимы
    только с verified ownership/PV/empty-data proof, отсутствие Pod не означает пустоту.
    Существующая legacy установка проходит
    отдельный проверяемый adoption/repair, без обнуления защиты по исчезновению metadata.
  - [ ] Первая запись только после P2-B/P3-A: existing recovery controller сохраняет
    activation intent exact lock UID/RV CAS, один раз отправляет controlled Helm
    update selected ref+record при отключённом приложении, требует native ACK и
    повторного совпадения ConfigMap/Helm values/Secret. Затем отдельный confirmed
    start intent разрешает один dispatch. Готовая annotation до первого start
    ещё не доказывает completed activation: перед ordinary reuse нужен durable
    completion receipt, связанный с тем же admission/DB/Secret/application generation,
    сохранённый в existing runtime ConfigMap/Helm record до unlock. Потеря временного
    lock не превращает prepared/unknown start в completed. Test:
    `prepared_generation_cannot_reuse_after_lock_loss_or_expiry`.
    ConfigMap и Helm history не атомарны:
    interruption/unknown ACK/односторонний update допускает только observation,
    сохраняет lock и запрещает ordinary start, повтор upgrade и автоматический repair.
    Tests: `activation_metadata_partial_write_cannot_start_or_replay`,
    `confirmed_new_secret_reaches_application_generation`,
    `helm_reapply_old_values_cannot_restore_previous_key`,
    `application_image_rollback_preserves_active_key_generation`.
  - [ ] Final activation frozen set: owning/affected Windows и Linux + real-tools
    extracted operator consumer, source guards и independent cleanup. Refusal repair
    и inherited574 contracts уже проверены на574-r2:509 cases/platform; повтор нужен
    после следующих связанных изменений. Дорогие
    неизменные1926 cases не повторять без новой зависимости/гипотезы; consumer573
    не закрывает изменённые574 modules. Полные rendered PG/Qdrant/VSO templates и
    native UID/PVC/PV bindings сохраняются. Прямой внешний Helm/API требует
    RBAC/admission-контракта среды; Python guard не доказывает его предотвращение.

##### P3-O: общий владелец и аварийное продолжение — 09.10.2026

**Files/interfaces:** existing `scripts/kubernetes/lifecycle.py`; owning
`backend/tests/test_kubernetes_operation_owner.py`,
`backend/tests/test_kubernetes_operation_journal.py`, existing PostgreSQL TLS
claim/session/SQL/schema tests, lifecycle/durable CLI и release
consumer tests. Сохранить `Manager.acquire()`, `run(...)`, `release_lock()` и
`recover_lock(operation)`; общий versioned intent/outcome в существующем operation
journal, отдельный от удаляемого lock и восстановленной БД. Новый controller не нужен.
Receipt575-r2 в памяти — только подтверждение владения текущего процесса.
Retained state576-r4 прошёл affected/consumer Windows/Linux и actual native
common ACK/unlock; см. [результат576-r4](#common-journal576-r4--регрессия-и-native-ack-09102026).
Это отказный фундамент, успешный interrupted resume остаётся OPEN. Current577 исправил preflight lifetime/status на selected730 и actual native proof;
Linux62 caller modules завершены на577. Final578 own-CAS имеет selected754, g5 и
actual native13; это не full caller closure или сквозная typed recovery приёмка.
См. [результат578](#own-cas578--компонент-и-границы-приёмки-09102026).

**OS custody580 — компонент принят, 09.10.2026.** Final frozen577 source paths,
base579 + lifecycle helper/new custody tests; manifest
`6889d39323ec894e1fc70b520ebf1fb550fbff84520d31ef94b7368716a23a61`.
Hidden-proc causal3 FAIL/26 controls →29 PASS; user/PID-view causal4 FAIL/30 controls
→34 PASS. Windows custody34 PASS3,34/180 + consumer38 PASS10,12/180;
Linux72 PASS18,31/180,0 SKIP на тех же bytes. Independent0,81/60 подтвердил
absence3 own containers/2 volumes после normal cleanup. Actual OS live/exited
actors проверены. Hidden-proc/scope faults подменяют только OS boundaries;
physical hidepid remount и фактический PID reuse не выполнялись.
Manager class bytes579 не изменены; внутренних product custody callers нет.
Journal/original cap/every-effect outcomes/handoff/admission/restore/release OPEN.
Original RED/launcher fixture и ошибочный consumer module preflight сохранены;
последний не dispatch subprocess. Accepted565/source578/579/native18/tools сохранены.
Evidence: `operation-custody580/{candidate-source580.json,freeze.json,
compatibility-summary.json,windows-procfs-red.json,windows-procfs-green.json,
windows-scope-red.json,windows-custody-final.json,windows-consumer-final-r2.json,
consumer-preflight-history.json}` и `linux-custody-consumer` normal/independent records.

**Journal custody581 — native/component, 09.10.2026.** Existing retained journal
version3 добавляет closed `custody {actor, monotonic_deadline}`: OS actor580 и
canonical finite positive `float.hex` cap. Actual acquire сохраняет их до lock
create; подтверждённые phase/CAS/unlock transitions сохраняют тот же cap/actor.
Current owner check сравнивает actual OS identity и original cap; legacy1/2 decode
и read-only status сохранены. Version3 **не доказывает прошлые effect outcomes**
и не разрешает interrupted takeover; occupied acquire по-прежнему refused.
Causal21 FAIL/4 controls →25 GREEN; +3 cap/scope/unknown-capture controls.
Final frozen578 paths: **285 distinct PASS/platform,0 SKIP**; Windows79,80/180,
Linux25,39/180; independent0,84/60 подтвердил absence3 containers/2 volumes.
Owning28 входит в union285. Original compatibility10 FAIL/175 PASS сохранён:
исправлены только current-version expectations и version1 fixture, carrying
лишнее custody; lifetime/ACK/refusal assertions не ослаблены. Windows CRLF и
root/Linux LF manifest bytes различаются; exact file maps и actual578 source
hashes равны, оба original manifest сохранены в `source-map-equivalence.json`.
Root manifest SHA `5cd82a39ffc452bdc88bc7934d85f5ded5609089cdbe5f887be824d2f7d3099c`.
Actual extracted bootstrap/operator/Kubernetes API: **13 terminal actors/5 namespaces,
125,92/180**, independent1,42/60. Native create/CAS/unlock custody carry,
lost lock/common ACK refusal и новый read-only CLI с original expiry проверены.
Base interpreter устраняет Windows venv redirector PID ambiguity: captured
PID/start и OS terminality подтверждены для каждого actor до cleanup; namespace
baseline/Ready Node UID сохранены. Native common disabled ConfigMaps без PG/key/
start/restore; lab identity не independent protected origin. Full caller closure,
every-effect outcomes, positive handoff/admission/home/whole CI/release OPEN.
Evidence: `operation-custody-journal581/{candidate-source581.json,freeze.json,
compatibility-summary.json,final-collection.json,source-map-equivalence.json,
compatibility-fixture-ruling.json,windows-owning-consumer-final.json,native-owner.json,
native-owner-independent.json}` и `linux-owning-consumer` normal/independent records.

**Every-effect582 — mutable progress, 09.10.2026.** Source579 paths, не frozen.
Actual Popen PID/start/scope привязан к prepared intent до byte/source stdin.
ConfigMap outcome требует exact native ACK/data/target, двух одинаковых свежих frames
и terminal child; отдельный journal ACK разрешает следующий effect/conditional unlock.
Own-CAS имеет distinct prepared intent/child/outcome; lock ACK и отдельный journal ACK
предшествуют обновлению owner receipt. Generic exit0 не подтверждает результат.

Resource6 causal FAIL/5 controls →11 GREEN; own-CAS3 FAIL/11 controls →14 GREEN;
actual source stream до binding:1 FAIL/17 controls →18 GREEN. Первый resource R1
1FAIL и owning R1 **20FAIL/183PASS** сохранены; corrected fixtures оставляют отказные
assertions: version4, closed legacy1, positive validated ConfigMap вместо untyped Helm,
fault injection после exact lock CAS, prepared intent/actor при oversized ACK.
До последнего pre-intent/target diff owning union: **203 unique PASS/platform,0 SKIP**; Windows62,59/180,
Linux22,02/180 на тех же579 file hashes/manifest bytes. Independent0,83/60 подтвердил
absence3 containers/2 volumes; actual collection203/2,16с. Настоящий OS transport
проверен, Kubernetes API в owning tests заменён только внешним adapter.

Остальные resource/Helm/scale/Job/exec/import semantic contracts, remote actors,
typed read-only RPC, positive handoff и full affected/consumer/extracted native582
остаются OPEN. Runtime565/main не обновлены; native581 PASS не переносится на582.
Evidence: `operation-effects582/{windows-resource-actor-red.json,
windows-resource-actor-green-r2.json,windows-own-cas-ledger-red.json,
windows-own-cas-ledger-green.json,windows-native-stream-red.json,
windows-native-stream-green.json,windows-owning-compat-r1.json,
windows-owning-compat-r2.json,compatibility-ruling.json,owning-collection-r2.json}`
и `linux-current-progress-r1` normal/independent records.

**Pre-intent/target diff — предыдущая локальная проверка Windows.** Actual Manager
отклоняет несовместимые stream arguments и некорректные ConfigMap requests до
intent/spawn; existing target требует release ownership и captured UID/RV, create
отказывает при занятом имени. Causal16 FAIL/19 controls → **35 PASS,0 SKIP**,
wrapper15,39/180с (pytest13,38с). Все579 file hashes совпадают с
`windows-preintent-owned-green-r1-source.json` при данной сверке. Positive controls
проверяют исправленный запрос, owned apply, следующий create и conditional unlock.

На этом checkpoint поздний target recheck был реализован без отдельного causal race
test; atomic/native proof отсутствовал. Следующее подтверждение приведено ниже. Linux,
повтор полного owning/actual affected/consumer и native proof изменённых bytes OPEN.
Прежние203 Windows/Linux относятся к своему source map;35 не прибавляются к ним
как итоговая приёмка. Evidence: `operation-effects582/{windows-preintent-owned-red.json,
windows-preintent-owned-green-r1.json,windows-preintent-owned-green-r1-source.json}`.
Runtime565/main/рабочие установки не обновлены.

**Atomic ConfigMap582 — current component/native, 09.10.2026.** Сохранён тот же
mutable кандидат. Actual ordinary ConfigMap apply после validated target scope
нормализуется в create-only при absence либо conditional replace с captured UID/RV.
Native args связаны с prepared context; original cap, binding до stdin и separate
outcome ACK сохранены. API Conflict не повторяется с новым RV и блокирует next/unlock.

Причинный R2:1 FAIL/39 controls →40 GREEN19,78/180. R1:3 FAIL/37 PASS был ранним
срабатыванием byte-substring injection на common journal; исправлена только внешняя
injection по metadata.name, original FAIL сохранён. **Current owning225 PASS/platform,
0 SKIP:** Windows81,17/180, Linux24,38/180; independent1,00/60 подтвердил absence
3 containers/2 volumes. Те же579 file hashes/manifest bytes.

Actual extracted bootstrap + Kubernetes API:5 resource scenarios/9 terminal OS
actors/5 owned namespaces, **165,98/180**. Проверены positive create→conditional
update→next→unlock, конкурентное появление отсутствующего target, изменение RV,
замена UID и drift до stdin; отдельные новые процессы сохраняют refusal.
Independent1,75/60 подтвердил OS terminality, Namespace/Pod/PV absence, Node Ready
и исходный baseline. Synthetic lab release identity — transport binding, не
independent protected origin. Native scope — genuine disabled common ConfigMaps,
без DB/key/start/restore; full typed/handoff/affected/consumer/release OPEN.
Runtime565/main/рабочие установки не обновлены;582 остаётся mutable.
Evidence: `operation-effects582/{resource-race-summary.json,
resource-race-injection-ruling.json,windows-resource-race-red-r2.json,
windows-resource-race-green.json,windows-resource-compat-green.json,
windows-resource-compat-green-source.json,native-resource.json,
native-resource-independent.json}` и `linux-resource-race-r1` records.

- [ ] **OS custody до изменения authority.** Files: `scripts/kubernetes/lifecycle.py`,
  `backend/tests/test_kubernetes_operation_custody.py`. Interfaces:
  `capture_operator_custody(deadline) -> dict`,
  `operator_custody_terminal(previous, deadline) -> bool` либо sanitized `OperationError`.
  Closed record: `version/platform/scope_sha256/pid/start`; запись доверяется только
  после проверки происхождения и binding retained journal. PID/start без этого
  допуска не является доказательством владельца. Linux scope включает boot,
  PID/time/user namespaces и effective UID; raw IDs в публичный record не попадают.
  Проверить соответствие procfs PID-view kernel PID-view и неизменность scope
  перед/после наблюдения; mismatch/неизвестность оставляют observe-only.
  Pre-fix private580 ошибочно принимал `/proc/PID/stat` ENOENT за отсутствие;
  исправлено в final580, causal/component proof выше:
  `hidepid=2/4` может скрывать живой процесс, см.
  [Linux procfs](https://www.kernel.org/doc/html/latest/filesystems/proc.html#mount-options).
  RED: hidden proc + successful signal0 → unknown; hidden proc + EPERM → unknown;
  hidden proc + ESRCH → absence; обычный permission error → unknown без второго probe.
  `os.kill(pid, 0)` только проверяет существование/права и не посылает сигнал, см.
  [kill(2)](https://man7.org/linux/man-pages/man2/kill.2.html); nonzero signals запрещены.
  Затем actual live/exited child Windows/Linux, cross-UID/user/PID/time scope,
  procfs mismatch, malformed/start reuse и expiry; не менять системные права/mount.
  Отсутствие доступа не превращать в PASS или автоматическую остановку процесса.
- [x] Read-only custody helpers final580: causal procfs/scope и actual live/exited OS
  actors + standalone consumer на Windows/Linux,72 distinct PASS/platform. Это
  component eligibility; общий OS/journal/native handoff критерий выше ещё OPEN.
- [x] Component/native custody journal581: actual acquire до lock create, custody/cap
  carry through CAS/unlock, current OS owner/clock validation; legacy1/2 read-only
  controls. Final285/platform и extracted native13 приняты в границах выше.
- [ ] **Versioned journal до разрешения takeover.** Files: existing lifecycle,
  `test_kubernetes_operation_journal.py`, durable CLI tests. Сохранить original
  custody/monotonic cap и UTC lifetime до dispatch, closed versioned decoder.
  Version1/2 unfinished не повышать автоматически до resumable нового формата;
  version3 custody581 также не даёт outcome прошлых generic effects:
  старые записи не содержат полный custody/effect contract; для нового процесса доступны только
  observe/explicit repair. Текущий владелец не теряет подтверждённый legacy допуск.
  Смена boot/host/clock scope не переносит monotonic budget; прежний UTC сам по себе
  не даёт resume. Tests: `legacy_owned_without_custody_cannot_resume`,
  `changed_clock_scope_cannot_reissue_parent`, `custody_journal_ack_unknown_blocks_handoff`.
- [ ] **Каждый effect имеет результат, а не только фазу `owned`.** Files: existing
  `Manager.run`/native writers и owning journal/owner tests. Durable intent до dispatch,
  отдельный outcome после exact typed native ACK и повторных frames. Crash между
  intent/spawn/сохранением child identity/ACK остаётся unknown без повторного запуска.
  Отслеживать actual child/Job/Pod identity: exit родителя не доказывает завершение
  дочернего CLI или удалённого writer. Проверить `operator_exit_with_live_child_blocks_handoff`,
  `crash_after_spawn_before_child_binding_blocks_replay`, same-Manager catch/new-process
  refusal. Unknown не превращается в confirmed по exit0, GET или ручной записи outcome.

###### Every-effect582: контракты и приёмка

Продолжить тот же mutable582 до рабочего положительного протокола. Отказное ядро
не является завершённым deliverable. Не создавать очередной кандидат только для
отдельного простого PASS; freeze выполняется после всей реализации ниже. Existing
`lifecycle.py`, owner/journal/custody/typed recovery/lifecycle tests и новый
`backend/tests/test_kubernetes_operation_effect_journal.py`; новый controller,
хранилище или issuer не требуются.

| Вызов | Что подтверждает результат | Что ещё требуется для следующей фазы |
|---|---|---|
| Resource create/apply/CAS, включая own lock CAS | Native create-only или conditional update captured UID/RV; exact ACK, kind/name/Namespace/UID/RV и ожидаемое тело, затем два согласованных свежих кадра | Distinct intent/outcome для own-CAS; lock ACK и отдельный journal ACK обязательны. Создание объекта не доказывает завершение выполняемой им работы |
| Helm upgrade/install | Structured ACK фактического вызова, ожидаемая новая revision/status, private stored values и identities установленного поколения в повторных текущих кадрах | Exit0, preview и matching GET после потерянного ACK не подтверждают effect; deployed не означает Ready или допуск DB/key/start. Автоматический rollback/cleanup запрещён |
| Scale/stop/start | Exact Deployment UID/generation/desired replicas и штатный native ответ | Scale0 не доказывает остановку writer; captured прежние Pods/Node/Lease проверяются фактически. Для start отдельно нужны activation/admission/Ready/HTTP |
| Job create и exec в toolbox | Подтверждённый Job UID/template и captured Pod UID/ownerReferences/Node; exec имеет собственный тип результата | Подтверждённый create может разрешить exec в намеренно живом owned toolbox. Его жизнь не равна неизвестному effect, но запрещает handoff/start/unlock до предусмотренного retirement |
| Job/Pod delete | Условный DELETE по captured UID/RV и подтверждённый ответ | Нужна фактическая остановка/отсутствие captured детей, включая потерявшие labels; terminal Job или пустой selector недостаточны |
| Exec, SQL и потоковый импорт PG/files/Qdrant | Типизированный контракт конкретного caller, durable phase/generation и фактическая проверка результата | Exit0, окончание локального потока и объектный UID/RV не доказывают COMMIT/целостность/остановку удалённого writer; unknown import не повторяется |
| Acquire/journal/transfer/unlock | Их существующие отдельные intent/ACK/outcome transitions и exact permits | Служебная запись журнала не рекурсивно создаёт новую запись о себе; исключение ограничено exact текущим переходом и не обходит журналирование own-CAS/других изменений |

- [ ] **Полная карта dispatch до объявления closure.** Пересчитать AST на final582:
  исходная карта581 содержит125 `run`/`kube` call sites в105 Manager methods,64
  dynamic args. Для каждого site записать класс read-only/control-write/effect,
  actual caller, permit, validator результата и positive/refusal test. Для dynamic
  args перечислить реальные варианты. Число статических sites не является числом
  отправленных эффектов. Неизвестный вариант запрещён до intent/dispatch; непокрытый
  site оставляет closure OPEN. Это навигационный реестр, не самостоятельная приёмка.
- [ ] **Отказ до intent и исправимый запрос.** Files: existing `lifecycle.py`,
  `test_kubernetes_operation_effect_journal.py`. Сохранить actual
  `_operation_stream_arguments(...)`, `_operation_effect_resource_request(args,data)`
  и `_operation_effect_resource_scope(desired,args,deadline)`: известные несовместимые
  args, неверный request, чужой target/занятое create имя не создают intent/actor,
  не меняют ресурс и не требуют ручного снятия lock. Исправленный запрос вновь
  проверяет actual owner/current state/original cap; этот путь не восстанавливает
  receipt, потерянный из-за неизвестного native ACK. Windows35 — компонентная
  проверка; current owning225 Windows/Linux проверяет исправленные запросы. Final
  freeze/полный caller closure остаются общим gate.
- [ ] **Последний target recheck и атомарная native запись.** Existing helpers
  `_operation_effect_resource_before_check(context)` и transport binding: после
  intent/actor ACK, до stdin повторить target scope. Любое drift оставляет prepared
  запись без следующего dispatch. Отдельно закрыть интервал ПОСЛЕ последнего GET:
  captured existing объект меняется только с server-side UID/RV conditions;
  absent target создаётся create-only, без unconditional apply, который может
  перезаписать появившийся чужой объект. Conflict не повторяется автоматически
  с новым RV и не переводится в confirmed по matching GET. Не применять этот
  ресурсный контракт как замену отдельному Helm/SQL/import validator.
  Component RED→GREEN и extracted native Kubernetes выполнены выше. Actual tests:
  `test_resource_drift_after_actor_binding_blocks_stdin_and_next_effect`,
  `test_absent_or_changed_resource_at_native_dispatch_never_overwrites_target`
  (absence/revision/replacement),
  `test_resource_apply_to_absence_creates_one_confirmed_object`; native modes
  positive/absent/revision/replacement/late + blocked new process. Final582 freeze
  и остальные semantic classes остаются общим gate.
  API-side conditional writes обоснованы
  [Kubernetes API updates](https://kubernetes.io/docs/reference/using-api/api-concepts/#updates-to-existing-resources).
- [ ] **Типизированные read-only RPC через настоящий caller.** Preserve interfaces
  `_probe_backup_parent_running_application(checkpoint, *, _deadline=None)` и
  `_probe_backup_parent_started_application(checkpoint, *, _deadline=None)`.
  В actual caller выдать scoped process-local разрешение ровно на один вызов после
  настоящих application frame/Pod checks: checkpoint/binding, Namespace, Pod UID/RV,
  backend container и ожидаемое template/image/environment, fixed program/argv,
  original deadline. Для этих probes input/source/output отсутствуют,
  timeout≤30с и stdout≤1024 bytes; повторный вызов требует новой проверки. Exact
  bytes программы, `_postgres_recovery_cas_permit`, `_backup_writer_rpc=True` или
  caller-флаг сами по себе разрешением не являются. Очищать разрешение в finally;
  nested/reused/другая команда или Pod запрещены. Вердикт не выдаёт start/writer/
  unlock admission и не подтверждает unknown effect. Pure probe доступен для
  наблюдения после отказа без новой pending mutation; legacy/expired status остаётся
  read-only. Остальной exec сохраняет effect contract.
  Уже выполнены actual tests в `test_kubernetes_operation_effect_journal.py`:
  `test_actual_selected_readonly_probe_creates_no_effect_intent`,
  `test_actual_selected_probe_after_unknown_effect_remains_observe_only`,
  `test_selected_probe_scope_rejects_nested_mutation_and_replay`,
  `test_selected_probe_current_binding_drift_refuses_before_exec`,
  `test_fabricated_readonly_caller_context_is_refused_before_intent` и
  `test_selected_probe_cannot_renew_expired_original_parent`. Pre-fix union9 modules/
  418 cases PASS/platform,0 SKIP; current profile union430 указан ниже. Это proof через external API adapter,
  не живой PostgreSQL/HTTP и не закрытие final582. Использовать actual acquire и
  genuine materials; подменять только внешнюю API/transport границу, не
  `_capture_backup_parent_application` или owner/admission validators. Прежние
  tests с mocked `Manager.kube` остаются semantic units, не closure actual run.
- [ ] **Installed-only и разные installed/desired — component выполнен, final native OPEN.**
  Files: existing `scripts/kubernetes/lifecycle.py`,
  `backend/tests/test_kubernetes_operation_effect_journal.py` и оба standalone probe
  модуля. Interfaces сохраняются: `_operation_selected_probe_take(...)` возвращает
  только одноразовый `_SelectedProbeDispatch`, без writer/start/unlock receipt.
  Current installed factory устанавливает `postgres_tls_profiles={'installed':selected,
  'desired':None}`; pre-fix helper читал только desired. Дефект воспроизведён:
  causal6 FAIL/57 controls →63 owning GREEN, оба callers. Текущий9-module union430
  PASS/platform на source579 files; прежний418 относится к pre-fix map. Live native
  installed-only/trust-rotation и final every-effect gate остаются OPEN.
  - [x] RED: actual tests `test_selected_probe_accepts_installed_only_profile`,
    `test_selected_probe_uses_installed_when_desired_differs`,
    `test_selected_probe_rejects_invalid_installed_without_desired_fallback`.
    Параметризовать running SQL и started SQL+HTTP; использовать actual common acquire,
    genuine validated material и public checkpoint, не подменять профильный validator.
    После unknown effect сохранить exact journal/effect count/receipt=None; positive
    observation не разрешает next effect/unlock. Некорректный installed отказывает до
    exec/intent, даже если desired валиден. Старый helper должен воспроизвести отдельный
    causal FAIL —6 ожидаемых failures сохранены; fixture/setup error не считать таким RED.
  - [x] GREEN: выбирать installed при любом non-None installed record; desired — только
    при installed=None. Некорректный/пустой installed record не разрешает fallback.
    Проверять selected profile штатным fresh material validator и точное равенство
    `parent['installed']['tls']['identity']`; новый desired trust не подменяет старое
    установленное поколение. Сохранить finally cleanup, full Pod/template/image/frame,
    original cap, одноразовость и прежние unknown-result guards.
  - [x] Проверить позитивный путь без installed, но с валидным desired того же checkpoint
    identity; wrong desired identity и отсутствие обоих profiles отказывают до exec.
    Эти cases включены в owning матрицу; genuine disabled gate выполняется своим
    штатным путём, без искусственных enabled materials или native9 admission.
  - [x] Пересобрать фактический selected collection и affected/consumer union после diff; проверить
    Windows/Linux на точных текущих bytes, original180с, independent60с и отсутствие
    owned ресурсов. Actual collection430 unique; Windows127,16/180, Linux63,61/180,
    independent0,80/60:3 containers/2 volumes absent. Предыдущие418 и atomic225/native9
    остаются proof своих maps. Это selected union, не full final caller closure.
    Evidence: `operation-effects582/selected-profile-summary.json`, RED/GREEN/compat
    records и `linux-selected-profile-r1` normal/independent records.
  - [ ] На final source из extracted operator kit выполнить genuine installed-only
    SQL/HTTP через реальный backend Pod и PostgreSQL TLS; отдельно различающиеся
    installed/desired поколения и malformed-installed refusal до dispatch. После
    unknown mutation чтение не подтверждает её outcome и не даёт новую authority.
    Existing protected-origin/admission safeguards сохраняются; synthetic lab origin
    не закрывает P2-B. Этот native gate не заменяется ответом external API adapter.
- [ ] **Server dry-run — component и built-in native выполнены на pre-Helm map; CRD/current final gate OPEN.**
  Files: existing `lifecycle.py`, `test_kubernetes_operation_effect_journal.py` и
  `test_kubernetes_lifecycle.py`. Interface: `Manager.preview_objects(documents,
  *, _mode='apply', _resource=None, _deadline=None) -> dict`; результат содержит
  objects/preview_only=True/writer_authorized=False/start_authorized=False.
  - [x] Одноразовый process-local scope связывает exact argv/input, release/Namespace,
    два свежих Namespace frames и original cap. Только namespaced kinds текущего
    chart, без hooks; apply/create stdin и TrafficPolicy merge patch сохраняют свои
    действительные CLI contracts. Пределы:30с,1–128 objects,2МиБ input/output.
  - [x] Actual run/caller tests с genuine disabled/enabled owner/material validators:
    `test_server_dry_run_never_confirms_resource_effect`,
    `test_server_dry_run_after_unknown_effect_remains_observe_only`,
    `test_missing_server_dry_run_flag_is_not_readonly`,
    `test_actual_platform_preflight_uses_scoped_server_preview` (create/patch).
    Проверены nested/reused/fabricated scope, expiry, drift, malformed/oversized JSON,
    incomplete/paginated List, изменение ожидаемого тела и foreign target.
    При отказе scope очищается в finally; journal/effect count не меняются.
  - [x] Actual collection10 modules/529 unique; Windows153,25/180 и Linux69,81/180,
    0 SKIP; independent0,83/60 подтвердил отсутствие3 containers/2 volumes.
    Source map `b863fc954923b7d8a329f380274e815a71ac32ac2d46554f457d675126e201d1`.
    RED32/4/2 и compatibility R1 1FAIL/528PASS сохранены; assertions не ослаблены.
    [Точные результаты](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md#server-preview582--компонент-и-пробелы-плана-09102026).
  - [x] Extracted kit + настоящий API: dry-run ConfigMap/Deployment/Service не создаёт
    объектов/Pods, не меняет journal; обычный подтверждённый ConfigMap create имеет
    собственный ACK. После неизвестного результата Helm и в новом процессе preview
    остаётся observe-only; следующий effect/acquire/unlock запрещён. Native3 actors/
    2 owned namespaces65,91/180, independent1,28/60: terminality, Namespace/Pod/PV
    absence, Ready Node UID/baseline/source/tools сохранены. Synthetic lab identity
    не independent protected origin. Evidence: `operation-effects582/native-preview.json`
    и `native-preview-independent.json`, same529 source map; runtime565 не обновлён.
  - [ ] Проверить реальные create/patch TrafficPolicy при наличии CRD/admission/RBAC;
    отсутствие CRD не заменять успешным synthetic native verdict.
    UID/RV/defaults из preview не сохранять как authority; allocated Service fields
    и мутации admission нельзя автоматически считать идентичными реальному вызову.
    Refusal RBAC/webhook — ошибка preflight, не отсутствие и не разрешение повторить
    запрос без dry-run. Секретные bodies/values остаются только в private memory.
    Основание: [Kubernetes dry-run](https://kubernetes.io/docs/reference/using-api/api-concepts/#dry-run).
- [ ] **Следующий deliverable: semantic outcome штатного Manager.chart.**
  Files: existing `lifecycle.py`, effect journal/owner/lifecycle/release tests и
  текущий общий chart. Сохранить `Manager.chart(enabled: bool) -> None`,
  `_inspect_chart_runtime_generation(deadline)` и общий `_operation_effect_confirm`.
  Новый validator относится к existing effect kind helm; controller/store/issuer
  не создавать. Preview — prerequisite, не outcome.
  - [x] Causal RED actual acquire/run/chart + настоящий общий Helm template:8 FAIL
    для install/upgrade × application disabled/enabled × genuine PG TLS disabled/enabled.
    Все8 остановились на отсутствующем confirmed helm outcome;4 unknown-result
    controls PASS. Whole11,39/180; source map
    `d20cc780057c2183d9ee2c9e61fbaf0a47c12078adf35253249fdea3540986dd`.
    Product не менялся; current diff к pre-Helm529 — только этот test module.
    Первые setup/R2 fixture ошибки сохранены отдельно и не считаются causal RED.
    Evidence: `operation-effects582/windows-helm-outcome-red-controls.json`.
  - [ ] Закрыть actual acquire/run/chart: install и upgrade, enabled=False/True,
    сохранение runtime generation. До dispatch зафиксировать текущую revision,
    expected values/rendered objects и original cap; при install доказать actual
    отсутствие release, при upgrade ожидать ровно следующую revision. Unknown
    discovery, pending/failed release и concurrent revision отказывают.
  - [ ] Из одного реального Helm вызова получить bounded structured ACK (`--output json`),
    проверить release/namespace/version/info.status, private config/manifest/chart.
    Для pinned Helm4.3 JSON выдаётся и install, и upgrade без table preamble; schema
    подтверждена upstream кодом, но actual runtime ACK ещё требуется в native proof.
    [Upgrade](https://github.com/helm/helm/blob/v4.3.0/pkg/cmd/upgrade.go),
    [JSON printer](https://github.com/helm/helm/blob/v4.3.0/pkg/cmd/status.go) и
    [release fields](https://github.com/helm/helm/blob/v4.3.0/pkg/release/v1/release.go).
    Затем два согласованных свежих кадра
    metadata/stored values/native identities и separate common journal ACK.
    Values сравнивать privately; в public outcome только разрешённые поля и identities.
    Согласовать decoder для helm outcome с writer/consumer; ручной dict/GET не issuer.
    Original parent cap покрывает render/preview/Helm/post-frames/journal;90с preflight
    и10m Helm timeout не добавляют времени к нему. До каждого dispatch пересчитать
    остаток; поздний ответ остаётся unknown, без автоматического повторного Helm.
  - [ ] Negative controls: exit0 при старой/wrong revision; другая namespace/release;
    drift values/native UID/generation; потеря Helm ACK и отдельно journal ACK;
    truncation/oversize; crash с живым local/remote actor. Matching GET не исправляет
    потерянный ответ, следующий effect/unlock запрещён. Deployed не выдаёт Ready,
    DB/key/start admission и не разрешает откат SQL или запуск старого writer.
  - [ ] Зафиксировать фактические argv/version выбранного Helm. Запретить automatic
    rollback/cleanup/force replacement: Helm3 --atomic, Helm4 --rollback-on-failure,
    --cleanup-on-fail и force flags не добавляются скрытым default/override.
    Проверить отсутствие hooks/lookup/неучтённых cluster-scoped effects текущего chart;
    появление такого поведения оставляет dispatch OPEN до отдельного контракта.
    Основание: [Helm upgrade](https://helm.sh/docs/helm/helm_upgrade/) и
    [revision metadata](https://helm.sh/docs/helm/helm_get_metadata/).
  - [ ] Owning causal RED/GREEN → actual affected/consumer Windows/Linux на тех же
    bytes → extracted native install/upgrade/next effect/conditional unlock.
    Original180с/independent60с component budgets сохраняются; полный final582
    gate остаётся отдельным. Synthetic lab origin не закрывает P2-B, metadata update
    с отключённым приложением не заменяет activation/start и полный restore.
###### Helm582: проверка плана и порядок закрытия

Исполнять следующие шаги внутри того же582. Новые исправления реализации не
заменяют прежние результаты; один итоговый набор исходников проверяется после
закрытия owning defects, до native dispatch. Полный every-effect/handoff gate
остаётся отдельным. Код приложения при этой проверке плана не изменяется.

- [x] **Восстановить исполнимую fault-матрицу.** В existing
  `backend/tests/test_kubernetes_operation_effect_journal.py`, test
  `test_actual_helm_fault_never_confirms_or_repairs_unknown_generation`, исправить
  `error.value.__cause__is None` на `error.value.__cause__ is None`.
  Перед runner проверить `compile` изменённых Python-файлов без записи pyc;
  PowerShell обязан прекратить следующий шаг при ненулевом exit code.
  Затем `pytest --collect-only` указанного test обязан собрать17 faults ×2 profiles
  =34 unique cases. Сохранить1 collection ERROR/0 cases как ошибку стенда; не
  засчитывать её causal RED. Исправлено; initial34 collection подтверждена.
  После расширения actual fault collection42 cases; owning179 весь PASS.
- [x] **Строгие типы — компонент на map f313.** В `_operation_effect_helm_outcome` и всех
  before/post-frame comparisons не использовать Python dict equality как доказательство
  JSON identity: `True == 1` и `False == 0`. В private comparison сохранять типы
  boolean/number/string/null/object/array, различать missing/null и порядок arrays.
  Строгая каноническая JSON-сериализация допустима; всякое допустимое числовое
  преобразование отдельно фиксируется реальным Helm, а не общим ослаблением equality.
  Existing `post-values-type` обязан отказать в обоих профилях до confirmation.
  Вложенные boolean/number before/ACK/post/defaults проверены этим компонентом.
  На map f313 null/missing/lists, repeated keys и несколько values-файлов ещё
  не были закрыты. Их последующая компонентная проверка описана ниже; этот
  strict-type checkbox сам по себе её не подтверждает.
  Product FAIL воспроизведён: initial2 FAIL/32 controls; expanded10 FAIL/32 controls.
  Existing `_operation_helm_same(left,right)` сохраняет точные recursive types
  before/ACK/post frames и ordinary inspections. Runtime immutable True→1 отдельно
  дал2 FAIL/44 controls; full captured Secret теперь сравнивается этим же helper.
  Owning179 Windows PASS113,44/180 относится к map f313. Этот checkbox закрывает
  strict-type компонент; coalescing и final current compatibility закрываются ниже.
- [x] **Независимый SDK и корректные null cases.** В existing
  `test_actual_chart_preserves_independent_sdk_values_boundary` использовать
  actual pinned Helm для user values и defaults, не product merge/comparator.
  Fixture уже исправлена;8 install/upgrade controls PASS17,17/180. Новый12-case
  прогон4 FAIL/8 PASS20,38/180 и диагностический trace46,06/180 сохраняются.
  На map `9a48a9cd61284c1f6c3596c13f4ffe8e98553d6f171097b4540b388262b6eb16`
  nested-null2 отказали при actual template preflight; integral-number2 — при
  ACK.config. Нельзя объявлять все4 причинными product FAIL.
  Actual real-chart characterization0,56с подтвердил: необязательный
  `resources.backend.requests.cpu: null` успешно удаляется; обязательный
  `ephemeral-storage: null` отклоняется schema. Исправить positive nested-null
  на cpu и сохранить отдельный negative required-null test ×2 profiles:
  до intent/Helm dispatch, журнал и effect counter неизменны, следующий допустимый
  ConfigMap effect разрешён. Schema/requirements ради positive test не ослаблять.
  SDK reference chart доказывает coalescing, а не успешную мутацию real chart.
- [x] **Числовая нормализация только входных Helm values.** В существующем compiler
  `_operation_helm_values`/его YAML helper зафиксировать доказанное pinned Helm
  преобразование конечного integral float8.0→int8 до private JSON pinning.
  Real chart принимает8.0 и рендерит целый timeout;8.5 отвергается integer schema.
  Добавить positive integral value и сохраняемый дробный value в privateProbe;
  fractional timeout8.5, NaN/Infinity и недоказанные precision boundaries
  отклонять до intent. Проверки на границах представления выполнить через actual
  SDK; успешный Go parse сам по себе не доказывает отсутствие потери точности. Не преобразовывать
  boolean, native generation/revision/UID/RV или произвольные before/post frames.
  `_operation_helm_same` остаётся strict; повторить boolean→number refusal tests.
  Stored user values, --all, ACK.config и manifest должны совпасть с независимым SDK.
  Реализовано в `_operation_helm_values`: finite numbers в включительном диапазоне
  ±9007199254740991; integral floats →int, native runtime generation добавляется
  после нормализации. Actual SDK показал потерю точности integer2**53+1 и int64
  границ. Поддерживаемый диапазон относится к operator values transport.
  Mixed boolean/number, fractional values, safe integer/float и negative zero
  проверены owning; exact native/frame/revision/UID/RV comparisons сохранены.
- [x] **Закрыть remaining values boundaries.** После ремонта owning tests проверить
  layered files precedence, nested merge/list replacement, null/missing и unknown
  null, порядок mixed arrays, defaults и duplicate YAML keys, включая YAML merge
  aliases. Duplicate/non-string keys отказывают до intent; следующая допустимая
  операция сохраняется. Каждый positive проходит actual acquire/chart/confirmation
  и next effect, каждый negative проверяет нужную фазу и отсутствие replay/unlock.
- [x] **Закрыть исходную матрицу и дополнительные type faults.** Actual acquire/run/chart и только external API
  substitution: ACK revision/namespace/status/config/manifest, malformed/oversized,
  потерянный Helm ACK, отдельно journal ACK, post revision/values/types, native
  spec/UID, runtime Secret, source values и expiry. После отказа тот же и новый
  Manager не подтверждают effect через GET, не replay/next/unlock; журнал и
  счётчики остаются в предусмотренном unknown состоянии. Положительные8 cases
  повторить на этих же bytes. Crash/live remote actor — отдельный ещё открытый
  custody gate, эти cases его не закрывают. Матрица расширена до21×2=42 faults;
  ещё2 before-intent controls. Полный owning179 и typed/chart-caller/consumer316
  PASS на прежнем map f313, без подмены owner/material validators. Последующий
  SDK fixture/test diff требует повторного current gate; этот PASS не переносится.
- [ ] **Фактический affected/consumer union.** Включить прямые chart callers из
  `backend/tests/test_kubernetes_application_runtime_generation.py`, existing owner/
  journal/lifecycle/release и standalone consumer. Пересчитать collection и уникальные
  cases, не объявлять новый total сложением старого529 с новыми tests.
  Windows/Linux: одинаковые source/chart/tool hashes, original180с, independent60с;
  skips перечислить с применимостью. Старые529 PASS и8/6 component PASS не являются
  current compatibility gate. Не повторять дорогой набор при ошибке collection.
  Actual AST direct callers пересчитаны; полный union11 modules/686 собран3,38/30с.
  Для type diff непосредственно затронуты owning179 +runtime-generation99 +consumer38
  =316 unique: Windows165,45/180, Linux102,22/180,0 SKIP, independent0,86/60, same579
  files/map `f31374de005dad3273df4771baf9aafa145a5487bbdbf6bceca0e87826962e7e`.
  Full686 не закрыт: Linux R1 unit120с без итоговых counts, whole157,55/180;
  retirement и independent0,94/60 подтверждены. Windows316 R1 timeout180 сохранён,
  после exact PID absence получены316 PASS; исходный whole gate FAILED. Гипотеза
  concurrent Linux/Docker load проверена одним isolated R2 с прежним180с и durations:
  R2 PASS165,45/180. Это корреляция с условиями запуска, не доказательство причины
  прежнего таймаута. Old failed records не переписываются.
  Свежий collection на map9a48:11 modules/698 unique, owning191, collection1,54с,
  command3,85/30с; это collection, не functional PASS. Parser R1 ошибся в prefix
  tests/; исправление прочитало тот же capture без нового прогона, setup refusal сохранён.
  После каждого owning/source-chart diff вновь compile/collect, без сложения старых
  totals. Затем один current affected/consumer набор Windows/Linux; full final closure
  OPEN. Windows proof выполнять без concurrent Linux/Docker proof; прежние original
 180 whole/120 Linux unit/60 independent, source/image/capacity guards сохраняются.
###### Current values/source guard proof — 09.10.2026

20 causal FAIL/36 controls67,97/180 →56 GREEN58,24/180; numbers6 faults,
precision-before-intent4 и unsupported source10. Correct optional/required null,
lists/merge/duplicates/non-string keys и прежние strict-type controls сохранены.
SDK fixture оптимизирована без product comparator: две независимые views в одном
real Helm;10-case equivalence3,11с. Windows R2 whole235 timeout180/late PASS190,65с
сохранён. R3 whole235 PASS171,70/180 на map caa78; последующий test-only POSIX fix
меняет map. Linux R3 setup16 FAIL/219 PASS87,17/180, independent0,84/60 сохранён;
chmod применяется только к owned tmp clone, mounted public source остаётся read-only.

Исторический R4 map579
`0538eba4d22a28bda26f4290e282e6b05309c8ad8a473662c031247aa8537815`:
actual collection11 modules/742 unique3,31/30с. Linux owning235 PASS89,06/180,
callers/consumer137 PASS41,48/180;0 SKIP, independent0,83/0,89/60 подтвердили
3 containers/2 volumes absence для каждого proof и source565/native18/tools guards.
Windows137 PASS47,12/180;235 late functional PASS185,34с после original180 timeout.
PID12452/children отсутствуют, source held; whole gate FAILED. R3 PASS не переносится
на R4 seal. Дополнительный неизменный expensive rerun требует измеренной гипотезы;
меньший набор не закрывает whole timing. Final742 execution/native/full582 OPEN.
Следующий functional proof — реальный Helm ACK/schema/API defaults; source не
frozen/adopted, runtime565/main/установки прежние. Full goal scope сохраняется.

###### Native Helm582: границы ближайшего proof — 09.10.2026

Обновлено10.10.2026: current source579/map840;806 PASS/platform,0 SKIP,
группы299/137/370. Native A R7 PASS138,48/180, independent1,55/60.
R5 quarantine и original R6 FAILED сохранены. Private582 не принят в runtime565/main.

| Проверка | Действия и допустимый результат | Что остаётся OPEN |
|---|---|---|
| A — метаданные, PG TLS disabled | DONE R7: extracted kit/common acquire, install application=false → upgrade application=true, confirmed revision1/2, exact ACK, следующий ConfigMap и conditional unlock | Pods запрещены quota; нет SQL/HTTP/Ready/data restore/P1–P3 admission proof |
| B — метаданные, PG TLS enabled | OPEN: genuine validated TLS materials в отдельном scope; install/upgrade, exact ACK, подтверждённые revisions; при незавершённом PG checkpoint unlock обязан отказать без потери lock | Полный enabled unlock не проверяется при quota0/noPods. Его PASS обязателен отдельно в C; ожидаемый отказ B его не заменяет |
| C — работающая bundled цепочка | После общих admission/activation: настоящие PG/Qdrant/files, SQL/HTTP/Ready, remote actor retirement и непустой согласованный restore; enabled-профиль завершает настоящий PG checkpoint до conditional unlock | Disabled C нужен compact-выпуску независимо от B; enabled C завершает TLS gate после его prerequisites. A/B не разрешают ordinary restore/start |

`application.enabled` и PG TLS — разные оси. Bundled StatefulSets присутствуют
при application.enabled=false. Лабораторный Secret/identity не доказывают durable
key publication, protected origin, admission или пустоту существующего storage.

- [x] **Лабораторный допуск и изоляция завершённых scopes.** Existing
  `postgres_tls.recovery_node_lease`: age -5с..min(duration,30с), duration1–120с,
  Node owner UID/Ready/pressure. Четыре exact UID PVC, storageClassName="", unbound;
  no Jobs/PV binding. ResourceQuota count/pods=0 actual Pod negative control.
  Gap-detecting watches запрещают любой Pod event, quota drift/deletion/expired
  history. Для R1–R4 и preview diagnostic normal/independent retirement подтверждён;
  **R5 не включён в этот cleanup PASS**, его watch завершение неизвестно.
- [x] **Windows/Linux parity private lab guards.** После durable watch/trace repair
  exact50 cases: Windows1,61/180, Linux21,72/180 и independent1,02/60.
  Historical34 относится к прежним helper bytes. Private harness proof не выдаёт
  origin/admission/start/restore authority и не реконструирует R5 history.
- [x] **Bundled preview causal repair.** Diagnostic57,23/180 и independent1,97/60;
  preview FAILED1,72/30: List12/12, единственный mismatch — omitted empty egress.
  В `scripts/kubernetes/lifecycle.py` нормализовать только networking.k8s.io/v1
  NetworkPolicy с пустым egress и policyTypes/Egress; при observed comparison —
  только если поле отсутствует в actual. Causal8 FAIL/14 controls40,11/180 →
 22 GREEN36,84/180 в `backend/tests/test_kubernetes_operation_effect_journal.py`.
  Wildcard egress, foreign selector и отсутствующий Egress policyType отвергаются.
  Chart deny policy сохранён; это API semantics proof, не packet-level CNI test.
- [x] **Exact pinned Helm printer repair.** Actual R4 raw relation:
  get-manifest bytes = ACK.manifest.encode() + один LF; ACK stdout JSON-only.
  Causal10 FAIL/14 controls39,08/180 →24 GREEN33,77/180; historical map2d57/776/platform.
  В existing `_operation_effect_helm_outcome`/fixture только точное сравнение;
  empty/double LF/CRLF/foreign tail отказ, next/unlock заблокированы при unknown ACK.
- [ ] **Закрыть оставшийся CLI transport gate.** Actual install JSON-only установлен
  для R4/R5 pinned dispatch; R7 подтвердил install/upgrade stdout JSON-only.
  Реальный stderr transport отдельно не подтверждён.
  Transport диагностик теряет stderr, поэтому stderr_bytes=0 не доказывает отсутствие
  реального stderr. Захватывать bounded stderr в памяти на настоящем transport,
  сохранять только безопасные размеры/типы/booleans/координаты. Raw manifest/values/
  Secret/stdout/stderr не писать. Любое новое framing исключение требует causal
  regression; общий strip(), поиск первого JSON и subset equality запрещены.
- [ ] **Guarded retirement retained R5.** Сохранить original failure неизменным.
  Readonly post-observer0,78/60 подтвердил exact PID/start terminality operator и
  first Helm effect, journal phase owned/один confirmed revision1; no second intent.
  Parent watch completion=false; initial list cursors не сохранены в durable record.
  Проверить admissible непрерывную isolation history и exact Namespace/quota/PVC UID,
  no binding/Jobs/Pods/PV claimRef, baseline/source/tools. Текущие no-Pod snapshot и
  quota UID не заполняют history gap. При gap/unknown/storage binding quarantine
  остаётся; никаких writer replay/start/unlock/delete. После доказанного допуска —
  отдельный conditional UID/RV retirement и independent absence с own60с budget.
- [x] **Компонент durable watch для новых scopes.** Private checkpoint атомарно
  сохраняет initial/last accepted raw List RV, Namespace/quota identity, original
  deadline и terminal result.50 regressions/platform и actual readonly parent-exit
 7,31/60 подтвердили surviving incomplete witness; raw events/секреты не сохраняются.
  R6 watch terminal с24 polls/kind. Это не retrospective R5 history/retirement.
- [x] **Не принимать пустой printer RV за исходный cursor — реализовано и проверено A R7.** До dispatch получить
  actual raw PodList/ResourceQuotaList через API, проверить exact kinds и непустой
  bounded string RV; отсутствующий/пустой/чужой cursor отклоняет запуск до effect.
  Generic kubectl List может иметь пустой RV. Число0 событий без валидного initial
  cursor или при history gap не подтверждает непрерывную изоляцию.
- [x] **Readonly profile и repair private trace.** На одном preflight старый trace
 14,469с, source-scoped0,828с, те же1302 product events; foreign tracing устранён.
  Product before-intent/dispatch/outcome fences, case IDs и original caps сохранены.
  Private50 Windows/Linux подтверждены; это не PASS полного нативного A.
- [x] **Scoped probe-zero preview repair.** В existing lifecycle/test effect module
  только apps/v1 Deployment/StatefulSet и три container health-probes; убрать
  integer0 только при отсутствующем actual поле. Null/false/nonzero, replicas0,
  foreign paths/API/type drift не получают общего исключения.16 causal FAIL/22
  controls →38 GREEN; actual prospective preview R3 PASS1,89/30,8,45/120 whole.
  Chart values неизменны; metadata dry-run не разрешает persistence/Ready/restore.
- [x] **Current compatibility на map840.** Exact806 PASS/platform,0 SKIP,
  disjoint299/137/370; Windows160,39/53,36/106,31с и Linux122,77/52,91/65,70с
  в отдельных whole180. Linux independent1,02/1,12/0,91с. Не single union180.
  Original owning Linux R1 FAILED с потерянными counts сохранён.
- [x] **Полный metadata A R7.** Revision1/2, next и conditional unlock;
  valid raw List RV/durable watch, normal cleanup и independent absence.
  Whole138,48/180; independent1,55/60; R5 baseline/quarantine сохранён.

- [ ] **Зависимые runner шаги только после подтверждённой подготовки.** Compile,
  exact source/tool map и expected IDs должны завершиться успешно до dispatch;
  preparation exception останавливает dependent runner, даже если последующая
  shell-команда возвращает exit0. После unit timeout сохранять bounded stdout/
  summary при подтверждённой terminality; unknown/нет counts остаётся FAILED.
  Не менять active runner files и не запускать concurrent heavy tests в isolated proof.

R5 disposition: retained quarantine при неизвестной history; новый fresh scope
проверяет старые Namespace/quota/PVC identity, journal/lock bytes, terminal actors,
no Pods/Jobs/PV binding до/после. Старый scope не удаляется/не переиспользуется.
Retirement R5 отдельный OPEN. New R6 normal+independent cleanup не выдаёт такой допуск.

- [ ] **B enabled и C с раздельными verdicts.** A уже завершён. B имеет
  metadata-only scope и ожидаемый отказ преждевременного unlock. Полный enabled
  unlock завершает C после реального PG checkpoint; не требовать его до C.
  Original budgets, guards, R5 disposition и independent cleanup сохраняются.
  Live/unknown actor/watch gap оставляет FAILED/retained; поздняя очистка не
  переписывает functional FAIL. Нельзя сохранять старое требование successful
  unlock для noPods B или заявлять отказ эквивалентом полного TLS proof.

Порядок compact задаётся только общей карточкой 2б.3.3 (R1–R6), затем
домашний lifecycle и release; этот TLS-контракт не вводит вторую очередь. Opt-in ветка: B metadata → соответствующие TLS prerequisites → enabled C
с настоящим завершённым PG checkpoint и unlock → TLS release gates.
После product diff пересчитываются затронутые IDs/map, затем необходимая проверка;
неизменённые806/A автоматически не повторяются. Original FAILED сохраняются.

- [ ] **Actual native Helm и API defaults.** Extracted kit, pinned Helm4.3, реальный
  `Manager.chart` с common acquire: install/upgrade, enabled=False/True и оба профиля.
  Проверить фактическую release JSON schema/chart metadata/coalesced values;
  ownership resources без instance label — по настоящим Helm labels/annotations,
  без добавления фиктивной метки в fixture. Сравнивать весь управляемый объект;
  исключения generated metadata/Service allocation перечислить по точным paths
  и доказать actual API. Не удалять целиком annotations/spec и не принимать subset.
  Preview UID/RV/admission result не считать будущим ACK. Реальный dry-run и запись
  могут иметь разные generated values — [Kubernetes API](https://kubernetes.io/docs/reference/using-api/api-concepts/#generated-values).
  Проверить следующий разрешённый effect и conditional unlock; enabled не получает
  recovery unlock без своих admission prerequisites. Captured OS/remote terminality,
  owned cleanup и independent absence обязательны; исходные бюджеты сохраняются.
- [x] **Полный эффект chart.** Проверить exact source/templates/helpers/subcharts/
  dependencies и argv поставляемого chart. Поиск одного литерала `lookup ` не
  покрывает переносы/табуляцию и helper/dependency; добавить соответствующие refusal
  controls, hooks/CRDs/cluster-scoped effects не разрешать по чистому template output.
  В existing effect test module использовать временную копию поддерживаемого chart:
  lookup-space/tab/newline, helper lookup, dynamic tpl, subchart/dependency, CRD,
  hook —8 variants ×2 profiles. Actual16 controls исполнялись:10 causal FAIL/6 controls на старом guard;
  после token/path audit проходят на Windows и Linux. Декларации dependencies,
  charts/Chart.lock, CRDs, hooks и lookup/tpl token отвергаются до intent.
  POSIX clone setup отдельно отказал16 раз из-за inherited read-only mode;
  после owned-temp chmod source/validator assertions сохранены,235 Linux PASS.
  Каждый запрещённый variant отказывает до intent/Helm dispatch, журнал неизменен,
  следующий valid ConfigMap допустим. Проверять токены lookup/tpl независимо от
  whitespace в root/helper templates; dependencies/charts и CRDs отклонять пока
  нет отдельного контракта их effects. Проверять exact paths, не joined filenames.
  Для поддерживаемого chart связать анализ с тем же source map; arbitrary chart
  interpreter не создавать. Semantic helm outcome остаётся metadata подтверждением:
  Deployment/StatefulSet controllers и remote actors имеют отдельный retirement/
  Ready/start/admission контракт. Runtime565/main/установки до final adoption прежние.

- [ ] **Транспорт и custody настоящего child.** В существующем Popen/stream transport
  сохранить actual PID/start/scope и привязать его к одному prepared effect до
  возврата результата. Capture и публикация расходуют original cap; stdin не
  отправлять до предусмотренного binding. Проверить crash до/после spawn, потерю
  ACK child binding, OSError/Timeout/interruption, live child после exit родителя.
  Local transport terminal не равен remote writer terminal; неизвестная identity
  остаётся prepared и не даёт replay. Actual OS child обязателен в positive tests;
  подмена допустима на внешней API/transport границе, не в owner/admission logic.
- [ ] **Положительное подтверждение по таблице.** В existing common helpers
  `_operation_effect_bind_actor(context: dict, actor: dict) -> None` и
  `_operation_effect_confirm(context: dict, outcome: dict) -> None` уже реализованы
  для ConfigMap; own-CAS подтверждается штатным typed native validator. Остальные
  классы таблицы ещё OPEN: реализовать их штатные validators и positive/refusal tests.
  Input является
  закрытым результатом штатного validator, не caller assertion. Final version4
  schema фиксирует exact target и local/remote identities, ограниченные поля каждого
  outcome kind; текущий bounded resource target/decoder ещё не
  закрывает эти классы. Проверить actual acquire → confirmed effect → следующий
  разрешённый effect → conditional unlock. Повторить для genuine disabled и enabled
  paths; не создавать фиктивный TLS/native9 admission для disabled.
- [ ] **Разделить факт результата и receipt публикации.** Потеря ответа исходного
  effect оставляет unknown. Потеря ACK публикации уже валидированного outcome лишает
  текущий процесс receipt, даже если state committed; GET не доказывает доставку
  ACK и не восстанавливает старую authority. Две истории с одинаковым live state,
  но разной доставкой последнего ответа обязаны сохранять этот отказ. Отдельный
  explicit repair/transfer может выдать только НОВЫЙ receipt после подтверждённой
  остановки всех прежних actors, проверки допустимого retained semantic outcome,
  исходного cap/current bindings и нового conditional native ACK + journal ACK.
  Он не делает unknown effect confirmed и не повторяет его. Tests:
  `lost_outcome_publication_ack_never_reconstructs_old_receipt`,
  `unknown_effect_cannot_be_repaired_by_matching_get_or_manual_outcome`.
- [ ] **Размер и совместимость растущего журнала.** До freeze измерить полную реальную
  цепочку, включая repeated phases/own-CAS и native managedFields: record≤32768 bytes,
  journal ACK≤65536 bytes; текущий effect list≤128 — кандидатный предел, не доказанный
  запас. Проверить exact boundary/overflow до intent и oversized ACK после commit.
  Не обрезать историю, не терять actors, не менять предел ради PASS; при несовместимости
  нужен обоснованный bounded контракт и повтор affected/consumer. Unfinished1/2/3
  не повышаются автоматически до resumable4; legacy completed/new operation сохраняют
  отдельные owner/fresh/adoption gates.
- [ ] **Итоговый gate582.** На одних final bytes: owning плюс actual affected/consumer
  Windows/Linux; полный positive chain с captured actors, extracted native CLI и два
  одновременных successors до conditional unlock. Independent observer подтверждает
  terminal actors и absence owned resources; original budgets/source/tool guards
  сохраняются. До этого582 не принимается в runtime565/main. Затем P2-B → P3-A →
  activation → непустой restore PG/Qdrant/files → native/home и release checks.

- [ ] **Одна передача владельца и явный resume interface.** Files: existing lifecycle,
  recovery CLI и `test_kubernetes_operation_owner.py`. Interface:
  `Manager.resume_operation(operation) -> None` устанавливает новый local receipt
  только после подтверждённого протокола; при отказе сохраняет lock/journal и не
  dispatch следующего effect. Prior OS terminal + known effect outcome/remote actor
  absence + исходный срок/current Namespace/lock/state UID/RV → transfer intent CAS →
  native lock transfer ACK → отдельный journal outcome ACK/repeated frames → receipt.
  RED: два actual successors одновременно — только один owner/один next effect;
  потерянный ACK каждого перехода и crash между двумя CAS — оба observe-only до
  проверяемого repair. Tests: `two_successors_cannot_both_dispatch`,
  `lost_transfer_lock_or_journal_ack_blocks_same_and_new_successor`.
  Сохранить отказные контракты `recover_lock`/`release_lock`; P3-O не выдаёт P3-A admission.
- [ ] **Завершённый deliverable, затем следующая область.** После причинных RED/GREEN
  заморозить final source; выбрать affected/consumer по actual call map, owning
  считать подмножеством. Windows/Linux на тех же bytes, actual extracted CLI/native
  two-process resume до conditional unlock, independent actor/resource absence.
  Успешная OS component проверка отдельно от полного protocol proof; source579 proof
  не переносить автоматически. Ранее passed неизменные группы повторять только при
  связанном diff/новом риске. Не расширять scope новым controller/store/issuer и не
  повторять full home restore до готовности origin/admission/activation зависимостей.

- [x] Affected564 Linux и consumer38 Windows/Linux на одном frozen575-r2:602 distinct
  PASS/platform без skips, whole180/child120, source/tool guards и independent owned
  cleanup подтверждены. Не повторять неизменный набор без причины; новый связанный
  diff требует своей affected/consumer проверки.
- [ ] RED на реальных `recover_lock`/`release_lock`/`restore` в новом Manager и штатном
  extracted CLI: `new_process_cannot_unlock_from_operation_id_only`,
  `lost_unlock_reply_cannot_authorize_next_acquire_or_start`. Unknown delete с уже
  отсутствующим lock не равен completed operation. Долговечный terminal outcome и
  actual bindings проверяются до следующей initial/fresh/adoption операции; потерянный
  journal не восстанавливается из отсутствия lock. Отдельно проверить ещё присутствующий
  lock и замену Namespace/lock UID. Read-only observe остаётся доступным при отказе.
- [ ] **Положительный resume, отдельно от отказных tests.** Existing
  Existing durable CLI получает отдельный явный путь продолжения той же операции;
  `Manager.recover_lock(operation)` сохраняет семантику условного освобождения lock.
  Путь продолжения вызывает новый `Manager.resume_operation(operation)` только после
  выполнения custody/outcome/transfer gates ниже; обычный unlock не выдаёт receipt. Новый процесс подтверждает прежнего owner/actors и текущие
  Namespace/lock/journal UID/RV, затем получает собственное подтверждение передачи
  владения через условную native запись существующего recovery пути. Одного GET,
  совпадения operation ID или перечитывания старого ACK недостаточно. Передача не
  повторяет неизвестный effect и не разрешает key/auth/DB admission автоматически.
  Tests: `new_process_resumes_confirmed_phase_with_new_owner_ack`,
  `live_or_unknown_previous_owner_blocks_handoff`,
  `unknown_handoff_ack_cannot_issue_next_effect`. Первый test вызывает actual Manager
  в отдельном OS process и проходит положительную цепочку до conditional unlock;
  после transport proof повторить штатный extracted CLI с настоящим Kubernetes API.
  Прежний OS-процесс оператора — отдельный actor: сохранить идентичность запуска
  и handle/terminal acknowledgement существующего supervisor до handoff. PID без
  идентичности запуска, его повторное использование или отсутствие Jobs не
  доказывают остановку оператора. Unknown/недоступный прежний host — observe-only.
  Test `live_previous_operator_without_jobs_blocks_handoff` вызывает два actual
  OS-процесса: пока первый жив, второй не получает effect; после confirmed exit
  новый native handoff ACK разрешает только следующий подтверждённый этап.
  Для unknown/partial effect отдельный observe/repair сохраняет блокировку; журнал
  различает результат этапа и право нового владельца на следующий этап.
- [ ] **Срок и retention общего state.** Persist original parent expiry/remaining
  policy до dispatch; новый процесс не получает новые90/600/900с.
  - [x] Зафиксировать original lifetime до первого preflight; tests
    `test_preflight_elapsed_cannot_reissue_default_parent_at_acquire` и
    `test_shorter_parent_preserves_original_issue_time_after_preflight` прошли
    causal RED2 → owning153 GREEN на frozen577. `issued_at` сохраняется от создания Manager;
    shorter caller parent только сокращает ещё не опубликованный expiry/cap.
    Повтор acquire/preflight, поздняя установка deadline и ожидание native ответа
    не продлевают срок. Для опубликованного journal lifetime неизменен.
  - [x] Closed version2 `{issued_at, expires_at}`: canonical UTC, положительный
    duration≤900с; на каждом effect использовать минимум original UTC remaining,
    локального monotonic cap и исходного дочернего лимита. Reject malformed,
    UTC до issued_at и исчерпанный срок до dispatch без записи/мутации.
    `test_native_effect_timeout_uses_original_remaining_parent` проверяет настоящий
    переданный subprocess timeout, а не только сравнение record.
  - [ ] Совместимость version1: unfinished journal только observe/explicit repair,
    без придуманного expiry и автоматического takeover. Completed version1 может
    начать новую operation только после полного actual owner/fresh/adoption допуска;
    старая operation не получает resume permission. Missing state не равен fresh.
  - [x] Actual new OS process читает прежний expiry через extracted CLI на577;
    expired parent блокирует effect/unlock, readonly common status доступен,
    authority flags False. PostgreSQL phase/migration отдельно проверены actual
    CLI test, common вложен под `common`; credentials отсутствуют.
  - [ ] Actual entrypoint combinations: PostgreSQL checkpoint + common после expiry,
    missing journal, API Forbidden/timeout. Ошибка не означает absence и не выдаёт
    новый budget. Native common-only proof не заменяет эти комбинации.
  - [ ] Переход на другой host после restart требует проверяемого контракта времени
    оператора; monotonic значения не переносить как timestamp между узлами/перезагрузкой.
    Пока валидность исходного UTC remaining не установлена, handoff остаётся observe-only.
    Тест clock drift вызывает actual entrypoint, не подменяет owner/admission.
    Это проектный guard; свойства monotonic описаны в
    [документации Python](https://docs.python.org/3/library/time.html#time.monotonic).
  - [ ] Expired operation сохраняет lock/journal и исходные custody bindings;
    заранее описать в существующем observe/repair runbook отказ старта, проверку
    прежних процессов и разрешённую условную retirement. Новый scope для диагностики
    не переиздаёт budget старого effect. Timeout controller/Job не является доказательством
    остановки всех Pod; проверять actual actors до unlock/start, см.
    [контракт Job](https://kubernetes.io/docs/concepts/workloads/controllers/job/#job-termination-and-cleanup).
  - [ ] Полный constructor caller closure577: selected730/14 modules Windows/Linux
    и real-tools bootstrap/source guards/independent cleanup прошли; дополнительно
    Linux62 modules/2075 cases завершены:2072 PASS/3 Windows-only SKIP. Windows
    g4-R2 PASS155,17/180; original FAILED сохранён. На final578 g5:
    Windows345 PASS/3 SKIP71,89/180; Linux348 PASS46,62/180, independent0,83/60.
    Применимые Windows symlink случаи OPEN. Affected716+consumer38 и actual native13
    проверены на одном final578; remaining constructor caller modules сверить с
    actual call map изменённого run/CAS пути. Каждый перенесённый результат требует
    обоснованного совпадения проверяемого пути либо нового прогона на final seal;
    прежний577 union не объявлять полной578 приёмкой. Positive handoff — отдельный gate.
    900с — текущий parent policy; legitimate операция большего размера требует
    отдельного измеренного и заранее принятого контракта, без изменения предела
    для закрытия уже неуспешного прогона.

  Запись `completed`
  означает завершение только своей операции, не ключевой admission. Пропавший state
  на существующих данных требует P2-A adoption/repair, даже если lock отсутствует.
  Operation state независим от DB/PVC backup: импорт старого архива не заменяет
  текущий target journal. Не включать ключи, credentials или их производные в record;
  terminal history/GC не удалять автоматически как side effect обычного unlock.
  Tests: `restore_archive_cannot_replace_target_operation_state`,
  `missing_state_on_existing_data_cannot_start_fresh`,
  `expired_parent_after_process_restart_blocks_dispatch`. Scope сохраняется из
  compact spec: одна инсталляция на namespace; второй release/Namespace UID или
  чужой state не дают takeover/overwrite. Отдельный negative test с двумя releases.
- [ ] `confirmed_own_cas_advances_owner_receipt`, `unknown_or_foreign_cas_cannot_refresh_owner`:
  после собственного journal/lock CAS новый UID/RV/body принимается только из exact
  native ACK и повторных API frames той же операции. Passive GET не обновляет authority;
  stale CAS, чужая запись и потерянный ACK не разрешают следующий effect. Положительная
  цепочка actual acquire → checkpoint/intent CAS → next effect не должна ломаться из-за
  ожидаемой смены RV; проверить disabled и genuine enabled без фиктивного native9.
  - [x] До product diff578 составлен AST writer inventory существующего lock: actual
    `claim_postgres_recovery`, `replace_postgres_recovery_lock`,
    `replace_postgres_recovery_session_decision`, `replace_postgres_recovery_sql_record`,
    `replace_postgres_recovery_schema_record`,
    `replace_postgres_recovery_session_invalidation_record`,
    `replace_postgres_recovery_session_absence_record` и inline CAS. Отдельный
    checkpoint ConfigMap (`begin_postgres_checkpoint`/`advance_postgres_checkpoint`)
    сам по себе не меняет lock receipt. Artifact578 фиксирует AST method/call sites
    и pre-diff source hash. Полная карта command/bytes, permit, previous UID/RV,
    native ACK и next effect для каждого writer выполняется в сквозном шаге ниже;
    перечень методов не означает приёмку этих цепочек.
  - [x] Компонентный causal RED24 (20 FAIL/4 controls) →24 GREEN на578 через actual
    acquire/public kube replace, минимальный refresh в existing Manager; owning177
    и final affected716 повторно проверены. `confirmed_own_cas_advances_owner_receipt`
    имеет disabled и validated enabled material profile; это ещё не вся typed цепочка.
    Сохранять Namespace UID, operation, original lifetime и exact full body;
    после ACK проверить два одинаковых live frames до обновления общего state.
    Common journal требует отдельного native ACK и повторных frames до следующего
    effect. ACK только lock или passive reread не выдают authority.
  - [x] Компонентные unknown/foreign/expiry отказные проверки578: lock и common
    journal не атомарны. Tests:
    `confirmed_lock_cas_with_unknown_journal_ack_blocks_next_effect`,
    `foreign_write_between_lock_ack_and_state_refresh_blocks_effect`,
    `expiry_between_lock_cas_and_state_ack_cannot_renew_owner`. Unknown/invalid ACK
    второго CAS сохраняет lock и native records в их фактическом состоянии для
    observe/repair: запись state могла уже committed, автоматического rollback нет.
    Исходные receipts/evidence сохраняются; catch, новый Manager или совпадающий
    GET не восстанавливают authority и не повторяют CAS.
    Positive: actual writer → next scoped effect → conditional unlock. Подмена
    owner/admission/refresh методов тестовой фикстурой запрещена.
  - [x] Первый typed writer579: `test_typed_recovery_lock_writer_returns_current_native_ack`
    и `test_typed_recovery_lock_returned_binding_allows_next_exact_cas` прошли causal
    RED2 →GREEN вместе с7 controls; affected487/platform PASS. Возвращается проверенный
    native ACK. Это не приёмка session/SQL/schema/invalidation/absence или full claim.
  - [ ] Связать genuine claim/resume нового процесса с P3-O verified handoff:
    existing occupied lock не допускает новый acquire; `claim_postgres_recovery`
    сам не выдаёт common receipt. Подтверждённое завершение прежнего OS actor,
    original lifetime, native owner transfer ACK и common admission предшествуют
    следующему effect. Не обходить зависимость Mock owner/admission или fake native9.
  - [ ] **Сквозная цепочка typed writers после common acquire.** В existing owning
    claim/session/SQL/schema/checkpoint tests связать каждый writer из inventory
    с actual Manager.acquire → writer native ACK → current journal/lock binding →
    следующий разрешённый этап → conditional unlock. Enabled использует actual
    parse/validate сертификатов и штатные generation/claim permits; disabled — свой
    общий recovery путь. Валидированный материал сам по себе не выдаёт native/DB
    admission. Не подменять owner/admission и не переносить fake TLS/native9 в disabled.
    Добавить `typed_writer_chain_preserves_current_lock_rv`,
    `checkpoint_configmap_cas_does_not_refresh_lock_receipt` и refusal при drift
    между typed ACK и next effect. Проверить actual caller, который использует
    возвращённый desired с прежним RV: следующий writer обязан получить текущий
    подтверждённый binding, а не повторять CAS со stale resourceVersion.
  - [ ] **Размер растущего lock и ответов.** На этой же реальной цепочке измерить
    YAML request, native JSON ACK с managedFields и вложенный common state для
    session decision/SQL/schema/invalidation/absence records. Текущий common CAS
    принимает request≤32768 bytes и ACK≤16384 bytes; некоторые typed writers ранее
    запрашивали stdout≤2 MiB. Проверить рабочую верхнюю границу и отдельные negative
    `oversize_lock_is_refused_before_dispatch`,
    `oversize_native_ack_retains_lock_and_blocks_replay`. Превышение после commit
    сохраняет uncertain outcome; GET не разрешает replay. Не увеличивать лимит
    только для зелёного прогона: при несовместимости сначала обосновать измеренный
    единый bounded контракт request/ACK/state и заново проверить owning consumers.
  - [ ] **Приёмка final source.** После typed diff создать новый frozen seal; actual
    collection/call map определяет затронутый union без повторного счёта owning.
    Windows/Linux component/consumer, native extracted CLI и применимый home proof
    выполняются на тех же bytes. Native symlink cases проверить на Windows runner
    с доступным созданием symlink, без автоматического изменения прав текущей машины.

- [ ] `unknown_native_effect_poisons_same_and_new_operator`,
  `parent_deadline_survives_operator_restart`: stop/Helm/migration/create/import/start/
  unlock получают distinct intent/outcome и общий remaining budget до subprocess dispatch.
  Timeout, nonzero/неразбираемый ACK или interruption после dispatch допускают только
  наблюдение, без второго effect после catch в том же Manager или нового процесса.
  Проверка owner перед командой не отменяет уже отправленный effect и не доказывает
  остановку удалённого процесса; сохранить lock и проверить owned actors фактически.
- [ ] Native/new-process: terminal Job с ещё живым Pod, Pod без ожидаемого label,
  недоступный прежний node и API timeout не разрешают start/unlock. Проверять captured
  UID/ownerReferences и исходный Node binding, не только selector/terminal phase;
  isolation/retirement идут по существующему runbook без force-delete. Tests:
  `terminal_job_with_live_owned_pod_blocks_start`, `missing_label_does_not_hide_owned_writer`.

P3-O не даёт key/auth/outbox/DB admission. Его unknown/fresh правила связываются с P2-A;
первый startup и штатный reuse дополнительно требуют P3-A и completed activation.
Тесты вызывают actual Manager entrypoints; подмена допустима только на границе native
API/subprocess, не для acquire/owner/admission/start/release методов.

##### Продолжение очереди: origin, admission и сквозная приёмка

- [ ] **P2-B — origin/promotion и действительная operator identity.** Files:
  существующие `lifecycle.py`, `postgres_tls.py`, owning publication/durable CLI tests
  и operator archive. До native effects связать уже существующий защищённый источник
  expected pins/решения с exact release/source set, recovery/rotation, namespace,
  old/new Secret UID/RV и исходным expiry. Указать производителя и способ доставки
  отдельно от private policy: файл, подписанный/хешированный самим проверяемым
  процессом, не является независимым подтверждением. Public `admission_sha256`
  обозначает финальный public admission record, не hash ключа или caller assertion;
  record schema/version и источник expected digest фиксируются до первого эффекта.
  Текущий decode/carry не создаёт этот record и не подтверждает его происхождение.
  Новый issuer/controller не создавать. Если существующий источник не установлен,
  native promotion/start
  остаются OPEN; chart и refusal tests можно завершать независимо.
  RED→GREEN: `self_sealed_material_cannot_promote_key`,
  `promotion_binds_actual_new_uid`, `promotion_from_other_namespace_is_refused`.
  Native preflight той же identity проверяет CRD discovery, producer list/get,
  Secret get/create и lock CAS; Forbidden/unsupported version/CSI не означают absence.
  Запрет повторного create после unknown ACK и резервирование имени остаются обязательны.

- [ ] **P3-A — один admission перед каждым эффектом.** Files: существующие
  `lifecycle.py`, `postgres_tls.py`, `test_kubernetes_lifecycle.py`,
  `test_kubernetes_recovery_key_durable_cli.py`. Disabled restore получает собственные
  genuine operation/DB-generation bindings; enabled сохраняет действительные TLS
  prerequisites. Проверять admission до `migrate()`, `chart(True)`/`start()` и
  recovery unlock; обычный `recover_lock()` не является обходом. Не переносить
  искусственный native9 из fixture в disabled-путь. Зафиксировать в existing journal
  отдельные import intent/outcome для PostgreSQL, files и Qdrant: текущий pg_restore
  с `--exit-on-error` не доказывает отсутствие частичного импорта. Unknown/partial
  import или COMMIT не повторять; DB generation и прежний admission недействительны,
  до отдельного repair приложение не запускается. Проверить
  `partial_import_cannot_start_or_reimport`,
  `database_reimport_invalidates_prior_start_admission`,
  `restore_without_key_barrier_cannot_start_or_unlock` для обоих профилей.
  До startup hooks явно проверить all-provider auth purge, held historical outbox
  и политику восстановленных queued/running Jobs по их типам: ни seed, ни worker не
  запускает восстановленную работу до допуска. Business/audit/totals сравнивать с
  исходным backup с точно разрешёнными auth/outbox изменениями; новый baseline не
  подменяет исходный. Обычный restart/upgrade сохраняет ключ, sessions/pending flows
  и pending outbox; явный DR имеет собственный путь ротации/purge/hold.
  Включить существующий chart guard в общий оставшийся parent budget: fresh90с
  инспекции и Helm timeout600с не переиздают общий срок при resume. Записать admission
  до первой активации metadata, отдельно от outcome/Ready; hash не зависит от будущего
  activation ACK и не образует циклическую связь с создаваемой annotation.

- [ ] **P3-B — process interruption и сквозная приёмка.** На одном final frozen
  snapshot выполнить штатный extracted CLI в новом процессе: positive chain и
  interruptions CAS/private commit/Secret ACK/import/SQL COMMIT/start/unlock.
  Повторный процесс только наблюдает неопределённый effect, не отправляет его снова.
  TTL≤900с и parent/controller budgets не переиздаются между CLI этапами; expiry
  перед первым recovery start блокирует старт. `ordinary_reuse_of_activated_generation`
  проверяет штатный restart после истечения старого intent TTL без нового ключа,
  purge или продления сроков. Отказ после отправленного start не доказывает stopped:
  проверить реальный Deployment/Pod, сохранить lock и не запускать второй effect.
  Terminal/unlock требует свежих DB/Secret bindings, Ready выбранной generation,
  model-free HTTP и отсутствия прежних maintenance actors по owned UID; API timeout
  не считается absence. Native, затем home: непустые PG/Qdrant/files, проверки
  сохранности данных/аудита/held outbox и старой cookie, исходные budgets, штатная
  cleanup и independent absence. Retained R1 закрыть отдельно по подтверждённой
  ownership; credentials/конфигурацию конкретной среды хранить вне общего репозитория.

**Приёмка тестовых сроков:** original whole1926-case и Windows SQL/schema472
остаются FAILED/OPEN. Успех disjoint groups даёт функциональное покрытие и не
закрывает эти gates. До final release указать для каждого gate источник критерия,
применимость к выпускаемому snapshot и связанное evidence: обязательный timing gate
закрывается только исходным критерием. Новая организация regression допускается
только как заранее записанный отдельный контракт, сохраняя старый verdict, а не
как заднее переименование FAILED в PASS. Не повторять неизменную дорогую матрицу
без гипотезы или изменения поведения. Whole-branch review и required CI относятся
к final source/chart/operator/images; компонентный565/572 PASS их не заменяет.

#### Пробелы P2/P3 после проверки кандидата571 — 09.10.2026

**Состояние на момент review (история):** кандидат571 расширяет существующий Manager/CLI и
strict decoders; accepted565 и frozen570r2/569 не изменены. Сохранены отдельные
39 PASS35,69с и30 PASS26,87с, полученные до последнего исправления fixture.
После замены общего List на настоящий VaultStaticSecretList контрольный case
завершился1 FAIL1,51с. Единого GREEN69 на текущем source нет; P2 не закрыт.
Legacy inspector теперь отказывает при publication, но положительный переход
на новый Secret ещё не реализован. Новый CLI unit test с MockManager проверяет
маршрутизацию, а не настоящий controller/API или standalone consumer.

**Files / interfaces:** продолжать в `scripts/kubernetes/lifecycle.py`,
`postgres_tls.py` и owning `test_kubernetes_recovery_key_secret_publication.py`;
для P3 использовать существующие recovery/lifecycle tests. Кандидат содержит
`Manager.publish_postgres_recovery_key_secret(...) -> dict` и
`decode_recovery_key_publication(..., now=...) -> dict`; private trust decoder
не является независимым issuer. CLI `postgres-recovery-publish-key-secret`
принимает `--key-material-trust-file`, `--expected-key-material-trust-sha256`
и `--new-runtime-secret-name` только с остальными protected recovery inputs.
Public verdict не выдаёт publisher/start/writer/unlock permission.

- [ ] **P2 — действительный VSO API и доступы.** Сначала закрыть сохранённый RED
  `test_fresh_vso_producer_inventory_blocks_owner_conflict[none]` на typed list.
  Принимать generic `(List, v1)` и typed list только для реально запрошенного
  kind/поддерживаемой apiVersion; wrong kind/version, неполная pagination и
  oversized response отказывают. API содержит VaultStaticSecretList и CSISecrets:
  [официальный VSO API reference](https://developer.hashicorp.com/vault/docs/deploy/kubernetes/vso/api-reference).
  Зафиксировать capability table для обнаруженных CRD, включая явный отказ для
  неподдерживаемого CSI/producer schema; отсутствие CRD допустимо только при
  успешной свежей discovery. Forbidden/timeout не считать отсутствием VSO.
  Native preflight выполняется той же operator identity с минимальными discovery,
  list/get producer, get/create Secret и existing lock CAS правами; cluster-admin
  не является условием. Добавить `vso_typed_list_matches_requested_resource`,
  `vso_forbidden_is_not_absence`, `unsupported_vso_kind_refuses_before_create`.
  Collection resourceVersion не использовать как RV каждого объекта: собственный
  lock CAS не должен давать ложный producer drift; полный UID/RV/spec/status всех
  producer objects повторно сравнивается. Зафиксировать резервирование нового
  имени и одного владельца в release contract: два чтения не исключают будущую
  запись внешнего контроллера. Не исправлять конфликт изменением чужого VSO.
- [ ] **P2 — происхождение private material и продвижение.** Указать существующий
  защищённый источник material/source pins и независимого решения, его доставку
  оператору и отдельную проверку полномочий; test-generated seal/хеш собственного
  файла не закрывает origin. Проверить `self_sealed_material_cannot_promote_key`,
  `unconfirmed_secret_cannot_promote_rotation` и `promotion_binds_actual_new_uid`.
  Новый issuer/controller не создавать. До такого подтверждения наблюдение даже
  confirmed Secret сохраняет publisher/start/writer/unlock=False. Private key,
  его производные, trust bytes и private path не публикуются в журналах/ошибках.
- [ ] **P2 — успешный overlay, а не только запрет legacy.** На confirmed publication
  и проверенных private inputs SQL/schema/purge/absence Jobs и application generation
  получают exact новый Secret name/UID/RV; старые native Job, PG template, PVC и
  trust сохраняются. Проверить `confirmed_new_secret_reaches_maintenance_jobs`
  и `confirmed_new_secret_reaches_application_generation` вместе с существующими
  negative drift tests. Все data-поля кроме APP_SECRET_KEY равны исходному Secret.
  Применимый VSO не получает новое destination/rollout target; разрешённые refs
  не выводятся из одного изменения общего secrets.existingSecret.
- [ ] **P2/P3 — rollback после ротации.** Добавить
  `helm_reapply_old_values_cannot_restore_previous_key` и
  `application_image_rollback_preserves_active_key_generation` в actual lifecycle
  tests. После DR старый manifest/values/backup не разрешает вернуть старый ключ;
  rollback приложения допускается только с совместимой DB и текущим новым Secret.
  Secret retirement выполняется отдельно после terminal/owned identity checks;
  старый Secret не удаляется ради получения нового admission.
- [ ] **P1–P3 — срок и потеря operator storage.** Исходный TTL≤900с не начинается
  заново на каждом CLI этапе; controller90с ограничивается остатком parent budget.
  После долгого SQL/schema/OIDC этапа повторно проверить expiry до start/unlock.
  Tests `publication_resume_keeps_original_expiry` и
  `expired_promotion_cannot_start_after_maintenance` не допускают автоматического
  обновления issued_at или RNG. До первой мутации проверить доступность private
  persistence; его потеря после possible RNG требует отдельного repair evidence,
  а восстановление копии store не выдаёт first-dispatch permission.
- [ ] **P1–P3 — новый процесс и полный consumer.** На одном frozen snapshot сначала
  общий owning/затронутый Windows/Linux набор и standalone extracted consumer,
  затем настоящий CLI в новом процессе с положительным переходом и потерей ответа
  до/после CAS, Secret create, private commit, SQL COMMIT, start и unlock. Unit mock
  и сохранённая metadata не заменяют эти assertions. В обоих PG TLS профилях
  проверить реальный restore/resume/start и отсутствие startup hooks/writer до
  допуска. Disabled не получает искусственный native9 marker. Native/home proof
  включает штатную очистку и independent absence в исходном общем budget;
  применимые skips, непустой restore и final review/release gates остаются OPEN.

Это исторические критерии review571. VSO defect и затронутое функциональное
покрытие закрыты в572 в объёме раздела исполнения; отдельные39+30 PASS сами по
себе не были общим69 PASS. Текущую работу задаёт очередь P2-A–P3-B выше; оставшиеся
native/origin/application/common-start gates не закрываются этим уточнением.

#### Интеграция со штатным восстановлением
- [ ] Связать proof со штатным recovery/start: protected origin/current trust,
  текущие DB/key bindings, запрет writer до завершения, неопределённый COMMIT,
  restart/resume и условное снятие lock. Результат отдельного helper не является
  разрешением на старт или доказательством полного T3.

Общие auth/audit/session recovery критерии обязательны и для disabled-выпуска;
специфичные PG TLS trust/PKI переходы принимаются только для enabled-профиля.
Сначала короткие regression tests изменённых помощников на Windows/Linux, затем
один полный сценарий; повтор дорогого запуска — после установленной причины и
изменения либо новой проверяемой гипотезы. Исходные FAIL сохраняются. Эти шаги не
заменяют непустой restore PG/Qdrant/files и отдельную итоговую release matrix.

**Историческая сводка T1–T3:** T1 реализована и проверена 05.10.2026: Linux120/Windows59 PASS,
без skips; actual schema3 toolbox offline smoke PASS. T2 завершена: chart/Jobs и actual
initdb/restart/projected permissions PASS; final operator349/Windows31 без skips.
T3 частично реализована: static/SCRAM/native gates и budgets проверены;
static/native proof398 завершён; checkpoint foundation проверен 06.10.2026:
final Linux445, Windows34 (13 deselected), actual journal API/CLI/restart PASS.
Сохранённое поколение проверяется readonly; current old TLS static gate реализован
и проверен. Current-state readonly gate добавлен: final Windows149/Linux634 PASS
без skips; actual1.37 API defaults/list command PASS со synthetic historical bindings.
Current runtime/HTTP trust readonly barrier проверен: Linux693/Windows208 PASS
без skips на exact344 source. Recovery claim/CAS и cooperative dispatch fence
проверены: Linux755/Windows270, exact345, actual1.37 ConfigMap CAS PASS.
Recovery-ID resume и durable stop intent проверены: Linux797/Windows312,
exact346 и actual1.37 schema2 marker CAS/fresh resume PASS.
Captured graceful stop проверен: Linux850/Windows365, exact347 и actual1.37/PG17.11
terminal regular/init statuses, Node/Lease и conditional UID/RV stop PASS.
Stopped old-template restore проверен: Linux878/Windows393, exact348 и actual1.37
UID/RV template replace при PG0/same StatefulSet/PVC/PV PASS, без нового Pod.
Bounded old-PG start проверен: Linux918/Windows433, exact349, actual1.37/PG17.11
новый Pod на same PVC/PV и сохранённая synthetic SQL строка PASS. Native old
TLS/SCRAM и read-only SQL подтверждены отдельным native lab proof (06.10.2026).
Production native preparation/dispatch/terminal verdict/owned cleanup проверены:
final Windows597/Linux1082, source354; actual marker9 UID/RV delete +fresh observer PASS.
Application SQL compatibility/integrity/session-key и terminal/unlock остаются OPEN;
full dedicated storage recovery не завершён.
Полный storage recovery T3 и T4–T6 OPEN. PG TLS ещё
нельзя включать. [Evidence и ограничения](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md).

## Global Constraints

- Compact, bundled, один backend worker и одна application replica; replicas — позднее.
- TLS выключен по умолчанию. Для первоначального disabled release TLS-задачи не
  являются prerequisite, если выбранная среда не требует PG TLS. Его audit,
  backup/restore, dependency, resource и CI gates остаются обязательными.
- Установка с TLS не выпускается по одному render/file-only proof: обязательны
  T1–T6, native matrix и полный restore с актуальными trusted materials.
- UID/GID backend1000, PG999; modes0440 и read-only mounts проверяются фактически.
- Не менять local/Compose contracts. Не применять новые values к рабочему кластеру
  до изолированной приёмки. Generic документы не содержат реквизитов инфраструктуры.
- Default audit work4s/helper1s/cleanup0.5s/HTTP<5s остаются прежними; новые
  preflight/migration budgets не выдаются за гарантии shared-engine startup.
- Commit/push, upgrade работающей установки и публикация — отдельные действия;
  проверки используют свежие owned namespaces/PVC и синтетическую PKI.

## Review Focus

| Риск | Обязательный результат | Задача |
|---|---|---|
| Первый initdb ещё не имеет runtime Secret/БД | статический preflight без SQL; повтор после VSO; native gate до migration | T2/T3 |
| Изменение HBA/Secret под работающим старым Pod | content-hash ConfigMap, immutable UID checks, old backup до изменения storage | T2/T3 |
| Backup2 без PG identity | unknown, явный legacy adoption, отсутствие auto-plaintext inference | T4 |
| Старый CRL/CA истёк к disaster recovery | byte integrity отдельно от current trust; явный связанный transition | T4/T5 |
| Старые connection pools/ошибка reload | новые Pod UID и соединения, actual pg_stat_ssl/сертификат | T3/T5 |
| Mutable Secret активного поколения | только immutable True и exact live identity/body; прежний legacy install сохранён | P2-A |
| Pending/failed Helm вместо обычного deployed | first effect запрещён; отдельное recovery и согласованные revision/status | P2-A |
| Потеря обеих durable metadata | отсутствие не равно fresh install; пустой новый target либо explicit repair | P2-A/P3-A |
| Сбой между записью ConfigMap и Helm history | lock retained; observation only без первого start или replay | P2-A/P3-B |
| Старый intent TTL при ordinary restart | активное поколение сохраняется; первая recovery активация требует свежего допуска | P2-A/P3-B |
| Новый operator / unknown unlock / собственный CAS | отсутствующий lock не равен terminal; receipt только по подтверждённому ACK, общий deadline и actual owned actors | P3-O/P3-A/P3-B |
| Успешный CLI, но нет semantic outcome | разные validators для resource/Helm/Job/exec/import; generic exit0 не выдаёт следующий effect | Every-effect582 |
| Потерянный ACK публикации outcome | GET не воссоздаёт старый receipt; только отдельный подтверждённый repair/transfer, без повторения unknown effect | Every-effect582 |
| Рост журнала и exec-probe classification | измеренные byte/count limits и exact read-only contract до dispatch; overflow/неизвестный RPC сохраняют отказ | Every-effect582 |
| Installed-only или новый desired trust при старом installed | выбрать установленный профиль; ошибочный installed не обходится fallback; actual running/started callers и live SQL/HTTP отдельно | Every-effect582 selected-probe matrix |
| Boolean/number и coalescing Helm values | type-safe private before/ACK/stored comparisons; `true` не принимается как `1`; native defaults проверены отдельно | Helm582 |
| Collection failure и пропущенный chart caller | compile/actual collection до runner; application-runtime-generation входит в current affected union;0 собранных cases не RED | Helm582 |
| Parent timeout при live child / потеря watch cursor | retain scope; durable readonly watch evidence; late terminality не заполняет history gap и не разрешает cleanup/replay | Native Helm582 |
| Exact get-manifest printer и расход parent budget | ACK+один LF; malformed bytes refusal; per-command causal profile без расширения cap/удаления fences | Native Helm582 |
| API опускает integer0 health-probe delay | Только известные workload/probe пути; false/null/nonzero и чужие поля сохраняют отказ | Native Helm582 probe-zero |
| Empty raw List RV / потерянные test counts | Отказ до dispatch при невалидном cursor; после timeout exit0 без exact counts не PASS | Native Helm582 / current compatibility |
| Preparation failure перед dependent runner | Compile/map/IDs и structured success до запуска; ошибку не перекрывает поздний shell exit0 | Current compatibility / native A |

## T1. Profile validation и согласованный operator kit

**Files:** создать `scripts/kubernetes/postgres_tls.py`,
`backend/tests/test_kubernetes_postgres_tls.py`; изменить
`scripts/kubernetes/requirements.txt`, `scripts/kubernetes/operator_tools.py`,
`scripts/kubernetes/release.py`, `deploy/ci/operator-tools/{consume.py,Dockerfile,README.md}`,
`scripts/kubernetes/tests/toolbox_smoke.py`, `backend/tests/test_kubernetes_release.py`.

**Interfaces:** `parse_profile(pod: dict, storage: dict) -> dict | None` возвращает
проверенные пути/refs/policy; `validate_materials(profile: dict, server: dict,
client: dict, now: datetime) -> dict` возвращает public identity без secret bytes.
`None` означает явно disabled template, а не неизвестный исторический профиль.

- [x] Зафиксировать cryptography version + wheel hashes и платформы Python3.11+
  Windows/Linux. Проверить advisory/license/сборку toolbox; запретить установку
  зависимостей при запуске Pod. До этого dependency gate OPEN.
- [x] RED: parser flags/unknown fields, strict base64, limits N/N+1 до decoding,
  key/cert mismatch, encrypted private key, CA constraints/EKU/SAN, CRL подпись и
  issuer/time, mutable/foreign/recreated Secret UID, canary redaction. Пределы
  server cert256KiB/key64KiB, CA256KiB/CRL256KiB, ≤16 certificate blocks.
- [x] Минимальная реализация parser/validator; не считать статическую PEM проверку
  полной native chain validation. Хранить raw private bytes только в памяти;
  исключить их из exceptions/returned identity/evidence.
- [x] Новый release/overlay/bootstrap/toolbox metadata используют schema3 и
  exact archive allowlist с новым модулем; old kit2/archive fixtures сохранены.
  Проверить new3/new3 PASS, old2/new3 FAIL до mutations, old2/old2 PASS. Kit3
  не принимает schema2 archive под видом совместимого; backup2 разбирается T4.
- [x] GREEN: `python -m pytest backend/tests/test_kubernetes_postgres_tls.py backend/tests/test_kubernetes_release.py backend/tests/test_kubernetes_toolbox.py -q`.
  Версии и результаты standalone bootstrap вне checkout включить в evidence.

## T2. Server/client chart и Jobs

**Files:** `deploy/helm/ai-knowledge/{values.yaml,values.schema.json,Chart.yaml}`,
`templates/{storage.yaml,application.yaml,_helpers.tpl}`; создать `templates/postgres-tls.yaml`;
`scripts/kubernetes/postgres_tls.py`, `scripts/kubernetes/lifecycle.py`;
создать `backend/tests/test_kubernetes_postgres_tls_chart.py`.

**Interfaces:** `attach_client_profile(operation_spec: dict, installed_template: dict) -> None`
переносит только PG client env/mount/volume, без server key; проверяет их единство.

- [x] RED: disabled не меняет storage command/HBA/volumes; все сочетания enabled/
  refs/crlEnabled, name collision, malformed env/valueFrom/duplicates отклоняются.
  Backend/Jobs получают только client Secret; frontend/server key boundary соблюдена.
- [x] TLS server args, immutable HBA с hash в имени и сохранением старой версии,
  explicit local/TCP rules из spec; `helm.sh/resource-policy: keep` требует
  документированной последующей cleanup проверки ссылок, а не бесконечного накопления.
- [x] Клиентские PGSSLMODE/ROOTCERT/CRL/GSSENCMODE/REQUIREAUTH/SSLCERTMODE из spec,
  runtime conflict checks только выбранного TLS профиля. URL query не разрешать
  произвольно. Проверить native defaults и implicit HOME files на shipping image.
- [x] Jobs наследуют installed профиль для backup и desired для migration/restore;
  legacy template без PG projection продолжает работать. Проверить initdb с
  local `okf` и fresh/nonempty PVC; не считать pg_isready TLS проверкой.
- [x] GREEN: новые chart tests + `test_kubernetes_chart.py`,
  `test_kubernetes_temporary_storage.py`, `test_kubernetes_lifecycle.py`; Helm lint.

**Checkpoint T2, 05.10.2026:** complete. Actual shipping chart на отдельном lab
namespace: fresh initdb → commit → graceful Pod replacement → новый Pod UID и
тот же PVC → чтение сохранённой строки. PG17.11/Psycopg3.3.6/libpq180006:
TCP verify-full/SCRAM/TLSv1.3, presented leaf fingerprint; plaintext, wrong CA,
hostname/password отвергнуты до и после restart. PG root999/client root1000,
0440/read-only подтверждены фактически. Final operator349/Windows31 PASS,
без skips; exact owned namespaces/PV удалены. [Отчёт](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md).
Минимальный runner из T5 создан раньше для этого gate; полный T5 остаётся OPEN.
T3 native ordering/preflight/recovery и T4–T6 не закрыты; TLS release не разрешён.

## T3. Install/provision/upgrade и управляемое recovery

**Files:** `scripts/kubernetes/lifecycle.py`, `scripts/kubernetes/postgres_tls.py`;
создать `backend/tests/test_kubernetes_postgres_tls_lifecycle.py`,
`backend/tests/test_kubernetes_postgres_tls_checkpoint.py`.

**Interfaces:** `preflight_postgres_tls(desired, installed, require_runtime_secret)`
не выполняет SQL в fresh namespace; post-storage probe использует Job с desired
profile, выдаёт только non-secret verdict и подтверждает TCP/SCRAM/ssl.

- [x] RED: отсутствие БД не ломает static preflight; VSO runtime Secret pending
  не запускает SQL; TLS Secrets отсутствуют → отказ до storage mutations. После
  VSO повторить secret validation. Existing SCRAM verifier boolean проверяется
  до stop, initial verifier — после PG bootstrap до migration.
- [x] Зафиксировать budgets: kubectl requests с request-timeout30s, client wait
  каждого bounded check ≤60s; post-storage Job activeDeadlineSeconds60 и controller
  wait≤90s. Общие storage300s/start600s/stop180s не увеличивать молча. Превышение
  не освобождает lock и не запускает следующий writer.
- [x] Durable checkpoint foundation: bounded public schema2 в protected ConfigMap,
  UID/resourceVersion CAS и lock/namespace binding; сохранение перед stop/storage/
  migration/start, повтор UID/hash/validity/values/chart checks. Отдельный readonly
  `operation-status`; incomplete TLS journal не снимается обычным recover-lock.
- [x] Отдельная offline-проверка сохранённого schema3 release/chart/operator kit:
  protected expected release SHA/revision, toolbox/image digests, bounded one-read
  inputs, exact safe archive members/JSON. Код старого kit не исполняется;
  пакет не распаковывается. Это только integrity gate, не разрешение recovery.
- [x] Readonly binding по operation ID: verified schema3 artifact render совпадает
  со всеми ресурсами saved Helm manifest, его byte hash и template/image hashes
  installed journal. Known controller-template API defaults подтверждены на
  Kubernetes1.37; неизвестные defaults/admission changes не удаляются и отказывают.
  После possible migration/terminal phase/fresh install нет разрешения recovery.
  Повтор namespace/lock/journal/values/files binding после Helm template; writes нет.
- [ ] Для управляемого storage recovery передавать protected installed release/kit
  и exact chart artifact digest; не подменять их текущим chart tree или Helm
  manifest hash. Unpinned image reference остаётся digest=null и не разрешает
  возврат старого writer. Определить проверенную нормализацию API defaults при
  сверке old rendered template с сохранённым installed template.
- [ ] Перед изменением storage сохранить operation checkpoint из spec; повторные
  UID/hash checks перед backup, mutation, migration, start. TLS false→true требует
  backup старым профилем; true→false обычным deploy отклоняется.
- [ ] RED по каждой границе: pipeline interruption, новый PG не Ready, key mismatch,
  Pod недоступен, stale UID, expired old trust, migration началась/не начиналась.
  Прежний профиль можно вернуть только отдельным recovery по operation ID; после
  migration применяется SQL compatibility policy/restore в новые PVC.
- [ ] GREEN: lifecycle tests проверяют порядок вызовов и отсутствие следующей
  мутации после отказа. Не использовать `helm rollback --force`, force-delete Pod,
  сброс lock по TTL или автоматический TLS downgrade.

**Checkpoint T3, 05.10.2026:** partial. Static preflight связывает desired и
installed profiles, namespace/Secret/HBA UID и public hashes. Fresh/VSO pending
не выполняет SQL; повторная проверка после arrival и перед дальнейшими фазами
отклоняет замену/expiry/conflicting native inputs. Existing SCRAM проверяется
boolean через owned Ready PG Pod до stop; post-storage Job подтверждает TCP,
verify-full, SCRAM и presented leaf до migration. Completed native Job сохраняется
как evidence. Sanitized failures не освобождают lock и не запускают next writer.

Final Linux398 PASS/0 skips, 78.64s, включая 49 новых lifecycle cases. Actual
Manager static/SCRAM/native gate seed+restart PASS на owned PG-only стенде;
это не полный Manager.deploy, VSO или interruption/recovery proof. API default
HBA volume0644 принимается строго; другие modes/unknown fields отвергаются.
Поздний ответ succeeded не продлевает controller deadline90s для чтения logs.
Все пять owned namespaces/PV очищены; initial FAIL сохранены.

До T4 backup3 installed-TLS deploy/backup блокируются **до lock/stop**; format2
restore в TLS target также отклоняется до import. Fresh native gate не разрешает
production TLS release или upgrade без checkpoint/recovery. Эти временные guards
снимаются только вместе с проверенным backup3, не отдельным обходом. Следующая
работа: durable operation checkpoint и recovery по operation ID; затем T4.
[Подробная приёмка](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md).

**Checkpoint foundation T3, 06.10.2026:** реализован protected namespace ConfigMap
`okf-pg-operation-<operation-id>`, schema2 (не schema operator kit/backup). Содержит
public refs/UID/hashes, chart-tree и ordered-values hashes, installed Helm manifest
hash, image refs и digest при pinning, template hashes и observed PG/PVC/Pod IDs.
Raw templates/env/keys/passwords в journal отсутствуют. Фазы монотонны;
`migration_started` пишется до writer и означает `may_have_started`, даже при
потере ответа. Lost CAS reply запрещает повтор из устаревшей памяти; новый процесс
может прочитать статус, но это не разрешение возобновить mutation.

Actual PG-only journal proof: первый PG уже создан runner; journal сохраняется
перед graceful Pod replacement. Новая Manager-инстанция и отдельный CLI читают
фазу storage_changing; unlock отказывает. После нового Pod UID/того же PVC проходят
реальные CAS updates и conditional UID/RV lock deletion; journal остаётся до owned
cleanup. Это не полный Manager.provision/deploy или recovery старого storage.
Final445 Linux/34 Windows PASS, 47 новых cases, 3 owned namespaces/PV очищены.

Next: dedicated storage recovery по operation ID с old kit/artifact/image identity,
current trust, подтверждением выхода нового Pod и SQL compatibility decision.
Первые two T3 gates и checkpoint foundation закрыты; исходный checkpoint/recovery
контракт целиком остаётся OPEN. T4 guards installed-TLS backup/upgrade не сняты.

**Checkpoint package integrity T3, 06.10.2026:** добавлен
`release.py verify-recovery-package`. Expected release SHA256/revision поступают
из protected promotion независимо от проверяемого пакета; хэши внутри release
сами по себе не дают доверия. Проверяется только schema3. Schema2 и unknown
versions отказывают; поддержка recovery старого schema2 остаётся отдельной задачей.
Результат — public identity без raw files/keys/paths. Нет extraction, исполнения
старого kit, SQL, cluster mutation или снятия lock. Пакет ещё нужно связать с
installed journal/template, нормализовать API defaults, проверить current trust,
writer absence и выход нового PG Pod перед dedicated storage recovery.

Final485 Linux PASS/0 skips, 32.14s; 39 новых package cases и отдельный CRL-expiry
regression. Windows34 PASS/5 deselected/0 skips; пять symlink cases выполнены на
Linux. Real Helm package chart0.2.0 + current kit offline PASS; все340 snapshot
files совпали, включая274 frozen backend files. Linux unit-контейнер запускался
на домашней VM с readonly source, network none и временным DATA_DIR без kubeconfig.
Это не доказательство установленного поколения или native recovery. Initial
failures сохранены, product expiry guards не ослаблены. Полный T3 и T4–T6 OPEN.

**Checkpoint saved-generation binding T3, 06.10.2026:** реализован readonly
`lifecycle.py postgres-recovery-check`. Verified package → old chart render →
exact saved Helm manifest content/byte hash → installed API template/image hashes
в journal. Это проверка **сохранённого** поколения, не current workload/writer
или certificate validity. Known defaults проверены server-side dry-run на1.37.0;
полный нормализованный template должен точно совпасть с сохранённым hash.
Неизвестные admission fields не игнорируются. Saved manifest/values должны быть
сохранены защищённо вне public journal; raw templates не копируются в journal.

Migration may_have_started/completed, terminal/provision/fresh journal, missing
UID/pinned image/manifest hash, изменённые files/namespace/lock/journal отказывают.
CLI не делает SQL, storage mutations или unlock; verdict всегда содержит
`storage_recovery_authorized: false`. Schema2 artifact recovery не поддержан.
Следующая часть T3: current old trust/native validity, отсутствие current writer,
PG/PVC/Pod UID binding, recovery checkpoint и graceful возврат storage по ID.

Final520 Linux PASS/0 skips33.39s и35 Windows PASS/0 skips0.62s. Native defaults
и actual Helm package/TLS-HBA binding PASS с **синтетическим** checkpoint на основе
реальных API-admitted template hashes; persistent objects0. Это не proof старого
installed release, native certificate recovery или full pipeline interruption.
Exact341 snapshot/274 frozen backend hashes, Ruff/YAML/whitespace PASS; CI module
добавлен, hosted CI не запускался. Полный T3 и T4–T6 OPEN, TLS release не разрешён.

**Current old trust prerequisite T3, 06.10.2026:** opt-in
`postgres-recovery-check --check-old-tls-materials` сначала проходит полный saved
package/generation gate, затем читает текущие immutable Secrets старого профиля.
UID/CA/CRL/leaf/chain hashes должны совпасть с journal, сроки проверяются по текущему
UTC. Известный отозванный сертификат в старой server chain запрещает возврат.
Delta, indirect/scoped CRL требуют отдельной поддержки и здесь отказывают.
HBA content/hash/namespace/release проверяются, UID фиксируется на время inspection;
Secrets/HBA читаются повторно. HBA UID исторически не записан в journal2: это
проверка неизменности текущего объекта, не доказательство его исторического UID.
После чтений снова проверяются protected files/ordered values и namespace/lock/journal.
Профиль без управляемого TLS отказывает: plaintext recovery не подразумевается.

Verdict разделяет `old_tls_materials_validated: true` и
`old_tls_native_verified: false`; `storage_recovery_authorized: false` сохраняется.
Это статическая проверка, не complete path builder/native libpq proof и не атомарный
снимок Kubernetes. 34 новых case: Windows69 PASS/0 skips1.19s вместе с прежними
saved-generation cases; Ruff/YAML/whitespace PASS, frozen274 unchanged. CI list
дополнен, hosted CI не выполнен. После явного разрешения на передачу исходников
проверен exact342-files snapshot в frozen Linux container:554 PASS/0 skips33.35s.
Контейнер UID1000, network none, source readonly, temp DATA_DIR, без kubeconfig.
Remote image digest совпал с ранее frozen image;274 backend hashes неизменны.
Прошлый Linux520 относится к прежнему поколению исходников.

Остаются обязательными до recovery mutation: namespace-wide отсутствие writer и
способных его запустить controllers/Jobs; актуальные PG/PVC/Pod UID и template
binding; durable recovery claim/phase journal с блокировкой исходного pipeline;
graceful выход новых PG Pods, возврат прежнего storage, native проверка старой
leaf/verify-full/SCRAM перед разрешением writer. Migration may_have_started не
сбрасывать. TLS backup3 guard сохраняется. Полный T3 и T4–T6 OPEN.

**Current-state readonly prerequisite T3, 06.10.2026:** реализован
`postgres-recovery-check --check-current-recovery-state`. Флаг автоматически
включает старую TLS-проверку после полного saved-generation gate. Дважды читается
весь namespace: Deployment/ReplicaSet/StatefulSet/DaemonSet/Job/CronJob/Pod/PVC,
без label/field selector. Между проходами повторяется old TLS/HBA; HBA UID хранится
между полными проходами. Raw inventory сравнивается только в памяти; публичный
ответ не содержит Job/Pod templates, inline env/command или их hashes.
После inspection повторяются protected files/values и namespace/lock/journal.

PG StatefulSet UID совпадает с installed и observed journal. Текущий PG template
может отличаться от old только canonical TLS projection с old/desired journaled
refs; image, PGDATA, env, security, mounts и PVC bindings остаются прежними.
Только phase storage_changing допускает ещё не записанные new PG Pod UID/template;
в остальных фазах нужны observed identities. Новый unready PG допускается для
inspection; новая неисправная TLS material не должна запрещать возврат допустимой
старой. Это не разрешение пропустить native gate после возврата.

Все claims из saved application/PG/Qdrant совпадают по именам с observed journal;
каждый текущий PVC имеет тот же UID, Bound/volumeName и не удаляется. PG/Qdrant
replicas только0/1, без volumeClaimTemplates/custom ordinals. Qdrant template
точно совпадает с saved/API-normalized template; его текущий UID фиксируется
между проходами, исторический Qdrant UID journal2 не содержит. Storage Pods имеют
точного StatefulSet owner/name, совпадающие containers/volumes/security projection,
без ephemeral containers. Готовность PG не заменяет TLS/native проверку.

Application и model-stub отсутствуют или полностью остановлены (replicas/status0,
observedGeneration актуален). Application UID/template совпадает с installed;
model-stub template совпадает с saved. Остаточные ReplicaSets только0 и с known
Deployment owner UID. Все Jobs terminal без active; каждый оставшийся Job Pod
terminal с подтверждённым выходом каждого regular/init container. Неизвестные
Pods/controllers, DaemonSet или CronJob в namespace запрещают этот compact gate.
Для home stub mode перед recovery inspection нужно остановить также model-stub.
Это явная цена консервативной изоляции; ordinary deploy/local/Compose не меняются.

Inventory ограничен256 objects/8MiB; command reader держит только N+1 bytes,
подавляет stderr и останавливает/reaps oversized process. GET timeout60s/request30s,
bounded process/reader cleanup до1+1s. Inventory не пишется на диск. Reader error,
timeout, переполнение, duplicate/NaN/deep JSON или изменение между чтениями отказывают.
Verdict: current_application_writers_absent/current_storage_identity_verified=true,
old_tls_native_verified=false, storage_recovery_authorized=false. Это API snapshot
известной compact топологии, не atomic fence/physical Pod exit proof и не защита от
будущего вмешательства внешнего оператора/CRD controller.

Final634 Linux PASS/0 skips35.83s (15 operator modules), Windows149 PASS/0 skips2.60s
(current-state74 + generation48 + old-trust27); новые80 cases. Actual1.37.0 defaults
и bounded namespace-wide GET/List PASS, persistent resources0; historical checkpoint,
PVC UID/status и simulated rotation синтетические. Это не acceptance фактического
старого installed release/physical exit/native TLS. Exact343 source/274 frozen
backend hashes, Ruff/YAML/whitespace PASS; hosted CI не выполнен.

Текущие runtime Secret/ConfigMap body validity/UID/private freeze и selected HTTP
trust теперь проверяются отдельным readonly barrier (ниже). Нативный old SCRAM,
историческое происхождение ключа сессии и effects recovery ещё не разрешены.

Следующая часть T3: durable recovery claim/phases по operation ID и блокировка
исходного pipeline перед effects; подтвердить остановку original operator/CI и
отсутствие pending Helm/in-flight mutations (snapshot сам этого не доказывает).
Повторять все gates непосредственно перед effects, не сбрасывать may_have_started.
После graceful выхода captured PG Pod — возврат старого storage на тех же PVC,
новый PG Pod UID, native old-leaf/verify-full/SCRAM; только затем рассматривать
terminal journal/unlock и запуск writer отдельным проверенным pipeline. Отдельная
политика first TLS activation/plaintext downgrade и backup3 остаются обязательными.
Полный T3 и T4–T6 OPEN; installed TLS deploy/backup guard сохраняется.

## T3. Current runtime и selected HTTP trust — readonly barrier

- [x] `postgres-recovery-check --check-current-runtime` автоматически включает
  protected package/generation, old TLS и current-state checks; после runtime
  снова проверяет current-state, runtime и files/values/namespace/lock/journal.
- [x] Old backend envFrom exact ConfigMap → Secret без prefix/optional/лишних
  источников; PG POSTGRES_PASSWORD ссылается на тот же Secret/key. Inline
  credentials, frontend envFrom и PG envFrom отказывают в этом compact профиле.
- [x] Current runtime ConfigMap data exact protected saved manifest; duplicate
  config/Secret keys, controlled PG/TLS env и inline HTTP TLS disable отказывают.
  Runtime Secret Opaque, namespace/name/nondeleting/UID, bounded string data,
  valid base64/UTF8; bundled URL без query/fragment и непустой matching password,
  session key не короче32. Это проверка формы, а не фактической SQL-аутентификации.
- [x] API GET каждого объекта: stdout2MiB/N+1, timeout60s/request30s, strict JSON
  без duplicate/NaN. Два чтения и повторные полные passes фиксируют UID/data/
  type/immutable только в памяти. ResourceVersion bookkeeping может изменяться;
  UID/data не могут. Private body и производные hashes не попадают в journal,
  public verdict, argv, stdout или файлы. Secret обновление VSO во время проверки
  означает отказ и новую отдельную инспекцию; не автоматический retry effects.
- [x] При выбранном HTTP bundle требуется **независимо защищённый** ранее
  сохранённый public `--expected-http-ca-sha256`. Это не hash текущего GET и не
  доверие только имени. ConfigMap immutable, единственный PEM key, hash exact,
  каждый сертификат CA/current validity; UID/body freeze. Отсутствующий HTTP
  bundle отмечается явно; ненужный hash без bundle/inspection отказывает.
- [x] New59 runtime tests; итоговый operator Linux693/Windows208 без skips;
  exact344 snapshot/frozen application274, Ruff/CI YAML/whitespace PASS.
  CI test list дополнен; hosted CI и native runtime API/SQL/HTTPS не выполнялись.

Public verdict содержит только flags: current_runtime_validated,
selected_http_trust_validated (false если bundle отсутствует),
old_runtime_native_verified=false, storage_recovery_authorized=false.
Не заявляется историческое совпадение Secret с состоянием до аварии: checkpoint2
не хранит private body или исторический UID runtime Secret. Перед запуском old
writer нужен независимый protected Secret/VSO version provenance для session key,
либо отдельно согласованная ротация с проверкой последствий для сессий. Нельзя
объявить новый допустимый APP_SECRET_KEY прежним ключом только по длине32.
Фактические credentials/роль проверяет native old SCRAM; статический CA barrier
не доказывает HTTPS certificate path или доступность model/OIDC endpoints.

Исторический следующий шаг T3 на этом checkpoint: durable recovery claim/CAS и остановка исходного pipeline до
любых effects, immediate repeat всех gates; captured PG Pod graceful exit,
тот же StatefulSet/PVC, новый Pod UID и native old TLS/SCRAM до writer/unlock.
Protected HTTP hash берётся из доверенного release inventory, созданного до
инцидента; отсутствие этого anchor не обходится hash из текущего кластера.
Полный T3 и T4–T6 OPEN, backup3 guard и TLS disabled default сохраняются.

## T3. Durable recovery ownership и cooperative dispatch fence

- [x] `postgres-recovery-claim`: отдельная явная операция, требует operation ID,
  все protected saved inputs и protected `--pipeline-stop-record`.
  Automatically full generation/old TLS/current-state/runtime gate; pre-migration,
  installed old TLS обязателен. Plaintext/first activation не получают adoption.
- [x] Stop record bounded4KiB/regular/no symlink/strict JSON/version1 и exact keys:
  binding (context/namespace/namespace UID/release/operation/lock UID),
  checkpoint UID/SHA, original_pipeline_stopped=true. Это **подтверждение оператора**
  о фактически остановленном исходном процессе/CI, не результат автоматической
  проверки удалённого runner. Ложный boolean не является fencing in-flight Helm.
- [x] Helm status bounded2MiB/requested context+namespace/60s; matching name,
  namespace, positive integer revision, status только deployed/failed. Pending,
  uninstalling/unknown/malformed отказывают. Status/revision повторяются перед
  и после CAS. Stop record/inputs/ordered values/journal/ns/lock повторяются;
  изменение или неоднозначный ответ сохраняют lock, не разрешают следующий effect.
- [x] Один CAS `replace` существующего maintenance ConfigMap по UID/RV сохраняет
  public `data.postgres_recovery`: schema1, binding, checkpoint UID/SHA,
  recovery ID, phase=claimed, public stop-record/release/values hashes и Helm
  status/revision. Original operation ID/checkpoint2/migration не изменяются.
  No TTL/reset/retry/delete/auto-adoption; после lost response только инспекция.
- [x] Central pre-dispatch gate в текущем operator kit запрещает original TLS
  pipeline дальнейшие kubectl/Helm effects при claim/ownership loss. Auth can-i/
  whoami и rollout status readonly; auth reconcile/exec/scale/apply/delete/Helm
  upgrade/rollback effects запрещены. Claim owner также не получает effects.
- [x] `operation-status` возвращает recovery ID/phase, если есть bound claim;
  новый reader проверяет current checkpoint/namespace/lock UID и public record.
  Ordinary recover-lock/release_lock отказывают при claim, включая malformed.
- [x] New62 tests: CAS race двух owners, lost reply/wrong UID, protected inputs
  race, stop record, migration/pending Helm, malformed records, fresh reader,
  command fence/claim-owner no-effects/unlock. Linux755/Windows270 без skips;
  exact345/frozen application274; Ruff/CI YAML/whitespace PASS.
- [x] Actual1.37 namespace+две ConfigMap: native UID/RV CAS и stale-RV refusal,
  fresh reader, original Helm command отказала **до dispatch**, checkpoint data
  unchanged; namespace cleanup по captured UID/RV и absence check PASS.
  Static/runtime/Helm release gates — синтетические doubles, не acceptance
  реального historical generation, stop confirmation или TLS/SQL recovery.

Claim не останавливает уже запущенный subprocess/Helm, older kit, внешний actor
или CRD-controller. Обязательны фактическая остановка original pipeline,
отсутствие in-flight mutations и независимый protected stop record; в этом
этапе автоматический CI cancellation adapter не реализован. API snapshot,
допустимый Helm status и ручной record сами не образуют atomic cluster fence.
Нельзя удалять/перезаписывать recovery marker или переносить lock на новый UID.
Claim phase только claimed, storage_recovery_authorized=false. Повторный claim
не является resume: owner inspection не разрешает checkpoint завершить/снять lock.

Следующая часть T3 OPEN: явное возобновление по recovery ID с immutable input
binding, отдельные durable recovery phases/CAS до effects; immediate repeat
current writer/storage/trust/runtime gates. Graceful stop captured PG Pod,
подтверждённый выход, тот же StatefulSet/PVC и новый Pod UID; native old TLS/
leaf/SCRAM до terminal/unlock/writer. Session-key provenance/согласованная
ротация и explicit plaintext activation policy также остаются обязательными.
T4 backup3 и T5–T6 OPEN; installedTLS deploy/backup guard не снят.

## T3. Recovery-ID resume и stop_prepared intent

- [x] `postgres-recovery-resume` readonly: обязательны existing recovery ID,
  original operation ID, protected saved package/manifest/values/stop record.
  Record bound к current namespace/lock/checkpoint UID/SHA; release/values/stop
  SHA и Helm revision/status должны совпасть с claim. Полные old generation,
  TLS, writer/storage и runtime gates повторяются, затем owner/inputs/Helm ещё раз.
- [x] `postgres-recovery-prepare-stop` повторяет resume дважды и записывает
  **только** public intent через lock UID/RV CAS. PostgreSQL/Pod/PVC не изменяются.
  Lost reply/conflict/changed reply or input → retained lock, no automatic retry.
- [x] Recovery marker schema1/claimed сохраняется для чтения; переход в
  marker schema2/stop_prepared добавляет captured observed PG UID/template SHA,
  Pod names/UID/owner/ready и PVC names/UID, а также protected HTTP CA hash/null.
  Это отдельная schema от checkpoint2/kit3; original checkpoint/migration неизменны.
- [x] Resume stop_prepared требует тот же captured storage binding и HTTP hash.
  Readiness может меняться: это не identity и не native TLS/auth proof.
  Pod UID, PG template/UID, PVC UID изменяться не могут. Повтор prepare отказ;
  новый reader может только инспектировать/перепроверить существующий intent.
- [x] Dispatch permit scoped к одному exact kubectl replace request и YAML bytes,
  всегда очищается finally. Он не разрешает scale/exec/apply/unlock; такие
  команды запрещены даже внутри permit и после успешной/неоднозначной записи.
- [x] New42 tests; Windows312/0skip8.73s (6 modules), Linux797/0skip49.69s
  (18 modules), exact346/frozen application274; Ruff/CI YAML/whitespace PASS.
  Actual1.37 namespace+две ConfigMap: schema2 CAS и fresh prepared resume,
  duplicate prepare/stale-RV/original dispatch отказ, checkpoint unchanged;
  owned UID/RV namespace cleanup/absence PASS. Static/Helm/storage/runtime gates
  в native metadata-only fixture синтетические; PG shutdown/SQL не выполнялись.

## T3. Captured graceful stop / observer (06.10.2026)

- [x] `postgres-recovery-stop` принимает те же protected inputs, original operation,
  existing recovery ID и stop attestation. До effects повторяются полные gates,
  PG template/UID, единственный captured scheduled Pod и fresh Ready Node/Lease.
  Unscheduled/foreign/deleting Pod, чужой finalizer, changed UID/template,
  sidecar init и grace>150s отказывают до intent. Сам attestation не отключает CI.
- [x] Public recovery marker3 `stop_requested` сохраняется UID/RV CAS **до**
  effects. Поля stop: Node name/UID, Lease UID, owned finalizer, grace, имена
  regular/init containers и proof=null. Marker1/2 readable; checkpoint2/migration
  не меняются. Это отдельный протокол от kit3/backup3.
- [x] Captured Pod удерживается собственным finalizer. Узкие условия проверяются
  на свежем объекте; Kubernetes получает полный сохранённый объект через stdin
  `kubectl replace -f -` с exact UID/RV. После защиты Pod только captured PG
  StatefulSet условно меняется replicas1→0, template/PVC не меняются. Unknown
  поля сохраняются. Exact args/body permit очищается finally; no general mutation
  permit, no force delete, no SQL/import/app start/unlock.
- [x] Confirm exit: captured UID/owner/node, same Ready Node UID/fresh bound Lease,
  terminal Pod phase и terminated status каждого regular/init container.
  Missing/running/unknown status, SIGKILL/137 и unexplained Pod absence отказывают.
  Regular ready=false; successful init ready=true допускается только при terminated.
  Public bounded exit_code/signal proof записывается `stop_completed` **до** снятия
  собственного finalizer; после этого проверяются Pod absence/PG0 и полные gates.
- [x] Timeout/lost response не повторяют stop dispatch. Existing requested/completed
  marker вызывает только observer; принятый scale/proof/cleanup может быть дочитан.
  Если intent сохранён, но finalizer/scale не отправлен, observer отказывает и
  сохраняет lock: отдельный explicit repair protocol ещё нужен. Никакой TTL/adoption.
- [x] New53 tests; Windows365/0skip15.45s (7 modules), Linux850/0skip71.45s
  (19 modules); exact347/frozen application274 unchanged. Ruff/CI YAML/whitespace
  PASS. Actual Kubernetes1.37/PostgreSQL17.11: real graceful exit0 regular/init,
  init ready=true, Node/Lease, intent-before-stop, proof-before-cleanup, fresh
  observer/no redispatch, unchanged journal и UID/RV namespace cleanup PASS.
  Native lab использует emptyDir; saved generation/TLS/runtime/Helm gates — doubles.

**Осталось до полного T3:** explicit repair для interruption до stop dispatch;
связанный native proof полных static/runtime gates на real PVC; CSI/partition/
node-reboot fencing критерии целевой среды. Ready/Lease и terminal status —
проверка доступного узла, не атомарный fence от внешнего оператора/старого kit.
RBAC: namespace-scoped get/update Pod/StatefulSet/lock/checkpoint, get Node и
get Lease в kube-node-lease; проверить минимальные права на целевой платформе,
не выдавать автоматически широкую ClusterRole.

Следующий этап — durable old-template restore на том же StatefulSet/PVC с новым
Pod UID. До изменения template повторить ownership/inputs/trust/runtime/no-writer
и stop proof; возможная миграция запрещает recovery. Далее native old-leaf/
verify-full/SCRAM/SQL integrity, только затем отдельный terminal/unlock/app-writer
protocol. Session-key provenance/согласованная ротация, plaintext activation policy
и T4–T6 OPEN. InstalledTLS backup3 guard сохранён; stop_verified не является
storage_recovery_authorized=true. Release/working home application не обновлялись.

## T3. Old-template restore при PostgreSQL0 (06.10.2026)

- [x] `postgres-recovery-restore-template` принимает existing operation/recovery ID
  и прежние protected package/chart/manifest/values/stop/HTTP trust inputs.
  До intent — full generation/trust/runtime/no-writer gates, same PG UID/template,
  PG0/no Pod, durable terminal proof и same healthy Node/fresh bound Lease.
  Possible migration, expiry/changed runtime/HTTP anchor/UID/input отказывают.
- [x] Public marker4 `template_restore_requested` записывает только
  `restore.old_template_sha256` и сохраняет stop proof. Это отдельный протокол
  от kit/backup/checkpoint. Original checkpoint/migration не изменяются.
  Старый template читается из bounded protected saved manifest, нормализуется
  известными API defaults и обязан совпасть с installed template SHA.
- [x] Перед единственной записью повторяются ownership/files/gates и captured
  template/UID/RV. Narrow ephemeral template permit допускает только installed
  template hash и PG replicas0. `kubectl replace -f -` сохраняет все остальные
  поля StatefulSet/PVC, body только stdin; оба permits очищаются finally.
  Template меняется при нулевых репликах: Pod/SQL/writer не запускаются.
- [x] Marker4 observer не повторяет restore mutation. Принятый template/lost reply
  дочитывается; pending non-applied intent сохраняет lock и требует отдельного
  explicit repair. `template_restored` пишется после exact target verification;
  PG0/no Pod, Node/Lease и все protected gates повторяются после completion.
  Readonly inventory overlay допускает only captured/old hash во requested и
  только old hash во restored; claims/UID unchanged, любые PG Pods отказывают.
- [x] New28 tests: Windows393/0skip20.93s (8 modules), Linux878/0skip91.05s
  (20 modules); exact348/frozen application274. Ruff/CI YAML/whitespace PASS.
  Actual1.37/PG17.11 on disposable PVC: graceful stop, restored template PG0,
  same StatefulSet/PVC/PV UID, no new Pod, fresh observer/no redispatch, original
  checkpoint unchanged, owned namespace/PVC/PV cleanup PASS. Full saved package/
  TLS/runtime/Helm gates native fixture — doubles; old native TLS/SQL не проверены.

Old-template PG start — реализованная часть и открытая platform приёмка:
- [x] До replicas0→1 повторяются stop/old template/PVC/PV UID, current trust/runtime,
  protected owner/inputs и namespace-wide no-writer/controller checks. Обязателен
  независимо защищённый storage-fencing-record с exact checkpoint/recovery/stop
  proof и PVC/PV binding. Он является утверждением оператора, не CSI fence.
- [ ] Проверить фактическую CSI/Node fencing policy целевой среды и её отказную
  матрицу. RWO, Node Ready/Lease или API absence не подтверждают исключительный
  доступ к storage; неизвестное состояние ограждения запрещает выдачу записи.
- [x] Marker5 start_requested предшествует единственному UID/RV CAS replicas0→1.
  Lost reply observer не повторяет dispatch; pending/non-applied intent требует
  explicit repair. Новый PG Pod: новый UID при том же owner/PVC, storage300s.
  Первый observed UID записывается в pod_observed до Ready; назначенный Node/Lease
  связывается сразу до Ready, последующая подмена/исчезновение блокирует переход.
- [x] Независимый native proof возвращает old TLS Secret на том же PG/PVC,
  подтверждает old leaf/verify-full/SCRAM/read-only SQL и сохранность synthetic
  seed; wrong CA/password/expected leaf отказывают. Это отдельный lab proof.
- [ ] Production recovery-native Job intent/UID/template/observer/completion
  protocol и application SQL compatibility/integrity до разрешения writer.
  storage_ready по-прежнему подтверждает только Ready нового Pod.
- [ ] Terminal/unlock/app writer после SQL compatibility и session-key provenance
  либо согласованной ротации. Checkpoint migration не сбрасывать. Explicit repair
  для ранее записанного, но не отправленного stop/template/start intent ещё OPEN.

Full T3/T4–T6 и installedTLS backup3 guard сохранены. Working home application,
release/commit/push не менялись; template_restored=true не означает writer permit.


### T3. Bounded old-PG start — 06.10.2026

- [x] `postgres-recovery-start` требует `--storage-fencing-record`; readonly resume
  marker5 требует тот же файл и SHA. JSON≤16KiB/strict fields/duplicates/NaN,
  version1, issued/expires с timezone с TTL≤15min; before-dispatch решение актуально.
  Поздний observer проверяет неизменное решение на recorded requested_at;
  просроченное решение не даёт нового dispatch. Marker5 не меняет checkpoint2.
- [x] PG1/new Pod/owner/old exact template, same Bound PVC/PV claimRef и fresh
  healthy Node/Lease повторяются; deadline300s, отдельные reads≤30s/2MiB.
  storage_ready/fresh reader не разрешают приложение, import или unlock.
- [x] New42 cases; Windows433/0skip30.79s (9 modules), Linux918/0skip149.16s
  (21 modules); exact349/frozen274 unchanged. Native1.37/PG17.11 на disposable
  PVC: graceful stop→old template→one start CAS→new Pod UID→SQL seed42 survives;
  namespace/PVC/PV cleanup и fresh observer/checkpoint unchanged PASS.
  Full native package/TLS/runtime/Helm gates doubles, fencing decision — lab
  attestation, old template отличается annotation. Native old TLS/SCRAM не доказаны.
- [ ] Операторский runbook: отдельно подтвердить остановку исходного pipeline и
  незавершённых команд/внешних акторов, физическую остановку прежнего процесса,
  исключительный доступ к data volume по принятой platform/CSI policy. Только
  после этого независимо защитить решение, ограничить TTL и доступ к файлу.
  Runtime может читать PVC/PV, Node и Lease; cluster-scoped PV get RBAC проверить
  в целевой среде без выдачи неограниченного ClusterRole. Boolean не ограждает узел.
  Схема решения: version/binding/checkpoint_uid/checkpoint_sha256/recovery_id,
  old_template_sha256/stop_proof_sha256, issued_at/expires_at,
  exclusive_storage_access_confirmed/external_actors_fenced=true,
  claims=[{name,uid,pv_name,pv_uid}]; stop_proof_sha256 — SHA256 canonical JSON
  stop.proof (sort_keys, separators comma/colon), никаких credentials/private bodies.

Следующий этап: native old TLS/SCRAM и SQL compatibility/provenance; затем terminal
protocol с app/session-key/unlock. Full T3/T4–T6 и installedTLS backup3 guard OPEN.


### T3. Native old TLS restoration proof — 06.10.2026

- [x] Fixed `postgres_recovery_probe_command` и bounded strict public verdict:
  libpq≥18; explicit verify-full/CA/CRL/SCRAM/gss-disable/clientcert-disable,
  TCP+pg_stat_ssl/password/SCRAM verifier; statement timeout3s и transaction
  read-only; SELECT1 и old server leaf SHA256. No SQL writes/application imports.
  Positive flags строго bool true; body≤1KiB, duplicate/NaN/private/extra/unknown
  fields отказывают. Credentials/errors/SQL values не возвращаются.
- [x] New26 tests: Windows459/0skip29.80s (10 modules), Linux944/0skip154.82s
  (22 modules); source350/frozen backend274. Actual1.37/PG17.11: initial new TLS
  Secret→graceful stop→old TLS template→new Pod на same PVC/PV→old leaf native
  read-only connection PASS. Wrong CA/password/expected changed leaf отказаны;
  protected CP/marker5 unchanged, namespace/PVC/PV cleanup PASS.
- [x] Подготовительный durable native intent (marker6/native_prepared) реализован
  и отдельно проверен ниже; create/exec/native completion не разрешены.
- [ ] Включить probe в dispatch/observe/cleanup recovery protocol. Ordinary
  dispatch fence не изменены: эта реализация сама не разрешает create/exec Job,
  native completion, writer или unlock. Нужен новый explicit intent до Job create,
  UID/owner/pinned image/command/template binding, bounded observer/lost-reply и
  scoped completed Job cleanup, full inventory/trust/runtime repeats; unknown
  active Job/Pod не освобождать из общего no-writer gate. Completed logs одного
  Job не доказывают состояние storage после его подмены/нового рестарта.
- [ ] Отдельно проверить application schema revision/SQL compatibility/integrity,
  session-key provenance/ротацию, platform fencing и terminal/app/unlock protocol.
  SELECT1/read-only transaction и synthetic seed не заменяют application proof.

Native lab использует настоящие Secret CA/CRL/key и read-only projections, runtime
credentials synthetic только memory/API stdin. Saved package/current generation/
runtime/Helm gates recovery Manager всё ещё doubles, platform fencing — lab
attestation. Эти ограничения сохраняются; full T3/T4–T6/backup3 guard OPEN.


### T3. Durable native Job preparation — 06.10.2026

- [x] `postgres-recovery-prepare-native` с protected saved release/kit/manifest,
  operation/recovery-ID/stop-record/values/chart и storage-fencing-record. Marker6
  native_prepared расширяет marker5 полем native={job_name,job_template_sha256,
  server_leaf_sha256}. Strict parser сохраняет old stop/Pod/node/trust bindings,
  не меняет checkpoint2/migration. Нет Job UID или фиктивного native verdict.
- [x] Proposal построен из protected saved old application/backend: exact pinned
  image из installed snapshot, whole old client/server TLS profile/HBA и public
  old leaf; fixed read-only SQL command, backoff0/deadline60/no token/restartNever.
  Data PVC/mount readOnly; frontend/server-key projection не попадают в Job.
  Normalized template SHA binds command/security/env/projections/resources.
- [x] Full protected gates+owner/files/old template/new PG UID/node/readiness/PVC/
  trust/runtime повторяются; reserved Job name должен отсутствовать. Two passes
  до единственного ConfigMap UID/RV CAS, затем повтор. Lost reply не повторяет
  CAS/create; prepared observer/fresh resume только сверяет exact proposal.
  Namespace no-writer gate продолжает отклонять active/unknown Jobs/Pods.
- [x] New31 tests, Windows490/0skip41.34s (11 modules), Linux975/0skip197.78s
  (23 modules); source351/frozen274. Native1.37/PG17.11 old TLS restore/native
  proof повторён; actual marker6 CAS/prepared fresh observer/no additional Job,
  unchanged checkpoint и namespace/PVC/PV cleanup PASS. Native full package/
  generation/runtime/Helm gates — doubles, platform fencing — lab attestation.
- [x] Следующий explicit transition marker7: durable create intent→one scoped
  Job create→captured Job UID/owner/image/command/template и narrow readonly
  current-state overlay; доказательство и ограничения приведены ниже.
- [x] Bounded terminal observer и durable native verdict marker8 реализованы
  и проверены ниже. Marker7 сам не разрешает positive native completion.
- [ ] Completed scoped Job cleanup остаётся следующим transition. Markers6–8
  не разрешают app writer или unlock. Unknown writers не исключать.

Actual restored native TLS connection и metadata intent не закрывают application
SQL compatibility/integrity, session-key provenance, platform failure matrix,
explicit pre-dispatch repair, terminal/unlock и full T3/T4–T6/backup3 guard OPEN.

### T3. Durable native Job dispatch — 06.10.2026

- [x] CLI `postgres-recovery-dispatch-native` требует protected recovery/fencing
  inputs. Marker7 native_create_requested сохраняется UID/RV CAS до единственного
  scoped create; native_job_observed сохраняет first actually observed Job UID.
  Marker6 readable, checkpoint2/migration неизменны. Existing7 никогда не
  повторяет create, даже если Job отсутствует; pending intent оставляет lock.
- [x] Exact saved proposal привязан к lock ConfigMap UID и checkpoint SHA.
  Private body только JSON stdin; permit exact argv+bytes очищается finally.
  Create выводит только public object name; actual Job читается bounded GET.
  PodReplacementPolicy=Failed, parallelism/completions1, deadline60/backoff0,
  restartNever/no token; unknown Job spec/admission/defaults отклоняются.
- [x] Native reader inventory overlay локальная: exact Job UID/owner/checkpoint/
  normalized template SHA, pinned image/command/security/env/projections и
  только один matching Job Pod. API-generated labels/defaults принимаются
  строго; extra command/container/ephemeral/token/writable/foreign owner/UID
  отказывают. Остальной namespace проходит исходный no-writer gate.
- [x] При lost create response first candidate UID читается реально и проверяется
  с exact intent на повторных full gates до UID CAS. Это readonly transient
  overlay, не label/name exemption или writer authorization. После capture
  disappearance/replacement отказывают. Создание Pod/status transition между
  inventory passes может безопасно отказать; следующий observer не redispatch.
- [x] Исправлен найденный пробел marker6 fencing: immutable body SHA повторяется
  для markers5/6/7. Другой валидный PV/fencing record не заменяет исходный.
  Validity decision остаётся связанной с start.requested_at.
- [x] New42 tests: Windows532/0skip57.82s (12 modules), Linux1017/0skip289.82
  (24 modules); source352/frozen274. Actual1.37/PG17.11: real one create+lost
  reply→fresh UID capture→observer/resume без второго create; actual Job/Pod
  admission, fixed read-only native TLS/SCRAM/SQL PASS. Same PVC/PV/SQL42,
  original checkpoint unchanged, owned namespace/PVC/PV cleanup PASS.
- [ ] Следующие gates: durable native terminal Pod UID/verdict до owned cleanup;
  schema compatibility/integrity/session-key provenance; platform fencing/fault
  matrix и app/terminal/unlock. Job creation или old TLS SELECT1 не закрывают их.

Native full package/generation/runtime/Helm prerequisites остаются doubles.
Application data в lab — emptyDir, PG — real disposable PVC; readonly application
PVC boundary отдельно проверена unit inventory tests. Physical fencing — lab
attestation. Full T3/T4–T6 и installedTLS backup3 guard OPEN.

### T3. Durable native terminal verdict — 06.10.2026

- [x] CLI `postgres-recovery-complete-native`, strict marker8
  native_pod_observed→native_verified. First actually observed reader Pod UID
  записывается до terminal wait; scheduled Node/Lease identity фиксируется
  отдельно, сразу после scheduling и до positive verdict. Captured replacement/
  disappearance отказывают, original checkpoint2/migration неизменны.
- [x] Only exact old-client Job UID/template/command/image и matching Pod:
  Job Complete/succeeded1/no active/failed, Pod Succeeded/one operation container,
  terminal exit0/signal0/reason Completed, no init/ephemeral. NodeReady/fresh bound
  Lease, same new PG UID/old template/PVC/trust/runtime/gates повторяются.
- [x] Bounded public logs≤1024 и flags TCP/TLS/SCRAM/SQL-readonly/roundtrip/old leaf
  сохраняются native_verified CAS только после terminal proof и full pre/post
  gates. Unknown/private/oversize/false/changed leaf/late logs отказывают.
  Controller90s/request30s/client≤60s не увеличены. Lost CAS reply — observer
  exact captured state, без replay logs/SQL или Job create.
- [x] First not-yet-observed Pod можно ждать в пределах90s с повторными gates.
  При unscheduled Pod UID уже durable; Node identity заполняется до verification.
  Если captured Pod исчез, API absence не считается exit/native proof.
- [x] New34 tests: Windows566/0skip87.62s (13 modules), Linux1051/0skip382.68
  (25 modules); source353/frozen274. Actual1.37/PG17.11 real marker8 Pod/node/
  terminal/verdict CAS +lost response→fresh completed observer/resume PASS.
  Exact actual Job/Pod/native old TLS/readonly connection, same PVC/PV/seed42,
  checkpoint unchanged и owned namespace/PVC/PV cleanup PASS.
- [ ] Next — durable owned Job cleanup intent/UID-RV foreground deletion/both Job
  and captured Pod absence; затем application schema/SQL integrity/session-key,
  platform failure matrix/pre-dispatch repair и app/terminal/unlock protocol.

Job сохранён после marker8: native_verified не выполняет cleanup или app start.
Native full package/generation/runtime/Helm gates — doubles; application data
emptyDir/PG actual PVC, fencing lab-only. Full T3/T4–T6/backup3 guard OPEN.

### T3. Durable owned native Job cleanup — 06.10.2026

- [x] `postgres-recovery-cleanup-native`: strict public marker9 наследует verified
  marker8; cleanup хранит только Job/Pod resourceVersion и aware requested_at.
  native_cleanup_requested CAS до единственного conditional raw Job DELETE с
  exact UID/resourceVersion и Foreground propagation. Scope permit очищается
  finally; stdout DELETE discarded. Captured Job/Pod/Node identity и terminal
  proof повторяются, чужие/заменённые объекты не удаляются.
- [x] Existing marker9 observer-only: lost intent/delete/cleaned CAS не повторяет
  DELETE, create, SQL, app start или unlock. Native cleaned требует отсутствия
  BOTH exact Job и captured Pod, full protected prerequisites/no-writer repeat,
  durable cleaned CAS и post-repeat. Lock/original checkpoint сохранены.
- [x] Нормальный foreground GC наблюдается readonly: aware deletionTimestamp и
  единственный foregroundDeletion Job finalizer допускаются только для сравнения
  ранее verified terminal objects. Finalizers/Pod не patch/delete/force-delete.
  Замена UID/name/node/template, неизвестные finalizers/status или reappearance
  после cleaned отказывают. Job absence не является container exit proof.
- [x] New31 tests; final Windows597/14modules/0skip/113.3s,
  Linux1082/26modules/0skip/460.73s, source354/frozen backend274.
  Actual1.37/PG17.11 marker9 intent/UID-RV Foreground DELETE/lost reply→fresh
  observer/both absence/lock and original checkpoint retained PASS; no retry.
- [ ] Pre-dispatch repair остаётся отдельным protocol: lost intent до отправки
  DELETE оставляет pending lock. Не вводить автоматический повтор удаления.
- [ ] Application schema/SQL integrity/session-key policy и provenance/rotation,
  platform fault matrix и terminal/app-writer/unlock; full T3/T4–T6/backup3 guard.
  Решение 06.10.2026: разрешена явная ротация APP_SECRET_KEY с обязательным новым
  входом всех пользователей; durable decision/key verification/login proof OPEN.

Source bundle SHA256: `42d471adcfcb261b2f6205b034d04e36f4b26424ad00be251e4c22202f8102ed`. Full saved package/runtime/Helm gates native
fixture doubles, app data emptyDir/PG actual disposable PVC, physical fence lab-only.
Working application/local/Compose unchanged; no commit/push/release upgrade.

### T3. Protected explicit session rotation prerequisite — 06.10.2026

- [x] Решение пользователя: явная APP_SECRET_KEY rotation при recovery разрешена;
  все пользователи должны войти заново. Старый key нельзя молча восстановить.
- [x] `postgres-recovery-check-session-rotation` readonly после marker9 native_cleaned:
  independent protected public record и expected SHA256 обязательны, вместе с
  original saved generation/stop/fencing/recovery bindings. Record1<=16KiB strict
  JSON связывает operation/checkpoint/recovery/rotation ID, current runtime Secret
  name/UID/resourceVersion, aware issued/expires TTL<=900s, три strict true flags:
  rotation completed, all sessions reauthentication, previous key reuse prevented.
- [x] Actual Secret reference берётся из protected old backend template. Bound
  GET<=2MiB/request30s, Opaque namespace/name/UID/RV, strong key/matching bundled
  credentials/no inline TLS controls; private body freeze только в памяти.
  Full protected gates, record/anchor/current Secret repeats; no mutations.
- [x] New93 cases (40 record/mechanism+16 controller/CLI+36 decision+1 expiry): full Windows690/18modules/
  0skip/184.0s, Linux1175/30modules/0skip/718.37s; source358/frozen274.
  Synthetic cookie старого key rejects, fresh synthetic sign-in works; actual
  OIDC profile binding changes. Old key reuse would revive old cookie, hence
  independently accepted provider prevention is mandatory.
- [x] Actual home: synthetic issuer заменил ONLY disposable immutable runtime
  Secret новым UID/new key; DB password/URL unchanged. Product command repeated
  readonly check and refused wrong anchor, lock/checkpoint/Job count unchanged.
- [x] Durable session decision/CAS/lost-reply observer проверены отдельно ниже.
- [ ] Accepted provider rotation/rollback prevention and full native application login/OIDC
  proof, application SQL/schema/integrity, terminal/app-writer/unlock remain OPEN.

Protected issuer confirms actual fresh key generation/delivery and rollback
prevention; booleans/current GET are not automatic historical key-difference
proof. Product command does not rotate provider/key, invalidate DB rows, write
metadata, run SQL, start app or unlock. Public session_rotation_attestation_validated
true/session_rotation_native_verified false/storage_recovery_authorized false.
Native package/runtime/Helm gates doubles, app data emptyDir/PG actual disposable
PVC, fencing/issuer synthetic lab-only; full T3–T6/backup3 guard retained.

Source bundle SHA256: `198bcee8fe6a5c7c26d8691eb5ca2332d1bc1ec803713673ff8b095be07e14a7`; no working/local/Compose/release upgrade,
no commit/push. Hosted CI/full coverage/production SSO/platform acceptance OPEN.


### T3 durable explicit session rotation decision — 06.10.2026

- [x] Отдельный public lock field `postgres_session_rotation` schema1<=4KiB:
  policy rotate_reauthenticate, canonical native9 SHA/public issuer SHA,
  operation/checkpoint/recovery/rotation and runtime Secret name-UID-RV,
  aware decision_at/reauthentication_required. No key/private derivative.
- [x] `postgres-recovery-record-session-rotation`: two full protected passes,
  current Secret private body compared only in memory, one exact lock UID/RV CAS
  <=30s/controller90s, strict bounded response and post-CAS full repeat.
  Existing decision never overwritten; lost response fresh observer no second CAS.
  Native marker9/checkpoint2/kit3 unchanged; no Secret/SQL/Job/start/unlock effects.
- [x] Late-expiry RED1 retained, fixed final boundary validation. Recorded decision
  checks issuer validity at decision_at; current Secret identity/full gates repeat.
  Historical observer renews same bound Lease; production freshness guard unchanged.
- [x] Home actual decision CAS/lost reply/fresh observer/no repeated write/native9
  retained; Windows690/18modules/184.0s and Linux1175/30modules/718.37s,
  0skip/source358/frozen274. New93 cases; original intermediate failures retained.
- [ ] Next: application SQL/schema compatibility, actual session/login/OIDC and
  data integrity; terminal/app writer/unlock and full T3–T6/backup3 guard OPEN.

Original Linux aggregate observer timed out600s; no retained ID/full result,
so exit0 Docker events were not accepted. Final run captures named owned container
ID, exact image/mount/labels/command, logs/state/elapsed, observes<=900s and normally
removes only proven exited owned container. No force; product60/90/30 or
performance thresholds unchanged. Final count/native/source gates are mandatory.


### T3. Readonly old-image SQL revision foundation — 06.10.2026

- [x] Fixed probe в protected old pinned backend image читает его Alembic graph и
  public.alembic_version через TCP verify-full/SCRAM/readOnly. Statement3000ms,
  connect3s, bounded<=9 version rows; только один image head и один equal DB head.
  Missing/empty/multi/unknown/mismatched revision отказывает. No env.py/migration/
  stamp/create_all/app startup, no private errors in output.
- [x] Public verdict<=2048 exact fields/strict booleans/unique JSON/public leaf:
  schema_revision_equal=true, schema_structure_verified=false. Equal revision
  alone is not structural/data integrity or writer authorization.
- [x] New focused50 Windows 0.15s/Linux 0.08s,0skip;
  source360/frozen274 before-after. Earlier aggregate690/1175 remains historical
  source358 acceptance; a new combined full suite is not claimed.
- [x] Actual1.37/PG17.11 old-image graph + missing/equal/changed/empty/multi cases
  use owned Jobs/Pods and frozen image/source, exit0/1 expected. DB version table
  is synthetic lab seed; this does not validate the real application schema.
  SQL42 continuity, unchanged native9/session decision/CP, normal owned cleanup
  and independent namespace/PV absence checked.
- [ ] Dedicated durable SQL reader intent/UID/Pod/terminal/cleanup protocol, scoped
  no-writer overlay, kit/consumer/CI integration remain next. Actual schema
  structure/FS/Qdrant/outbox integrity, native login/OIDC, compatible-newer-schema
  policy after possible migration, terminal/app/unlock/full T3–T6 OPEN.

SQL foundation module is independent and has no CLI create/exec/app/unlock permit.
Strict one-head rule follows current project database_revision_command contract;
unknown/multiple revisions are not inferred compatible from graph ancestry.
Source bundle SHA256: 3ea8c5fb14713dc31f8f6a794f06ba0d7a86bc75c0cec1660a19616c09b60f48. Native saved generation/runtime/Helm gates
fixture doubles, app data emptyDir/PG actual PVC; fencing/issuer synthetic lab-only.


### T3. Separate public SQL reader preparation — 06.10.2026

- [x] Public `postgres_application_sql` record1<=8KiB binds native9 SHA and
  session decision1 SHA, operation/checkpoint/recovery/runtime Secret name-UID-RV,
  unique SQL Job name/fixed readonly old-image proposal SHA/public server leaf.
  Strict reader states prepared/create_requested/job_observed/pod_observed/verified/
  cleanup_requested/cleaned; decoder alone does not implement these transitions.
- [x] Intent time cannot precede session decision, cleanup cannot precede intent;
  existing5s clock skew retained. Pod/node/Lease/terminal/verdict/cleanup fields
  obey exact phase rules. Revision verdict never claims structural compatibility.
- [x] `postgres-recovery-prepare-application-sql`: two full protected session/
  generation/runtime/trust/newPG/fencing/file/no-writer passes, absent reserved
  Job, ONE exact UID/RV public-field CAS, bounded stdin/stdout2MiB/request30s/
  controller90s; strict response and full post-repeat. Existing prepared/lost
  reply observer writes nothing; later phases are refused by prepare.
- [x] Fixed private proposal uses protected old pinned backend, client CA/CRL,
  readOnly application data PVC/mount, no service-account token/server key/frontend.
  Unique name/lock owner/checkpoint/native/session annotations, deadline60/backoff0.
  Public prepared does not create Job, execute SQL, start app or unlock.
- [x] SQL helpers relocated into already allowlisted postgres_tls.py before CLI
  integration; exact schema3 archive member set/format unchanged. Three SQL test
  modules in CI. New71 record/controller cases plus prior50 SQL probe cases.
  Current aggregate Windows811/21modules/195.06s and
  Linux1296/33modules/797.58s,0skip/source361/frozen274.
- [x] Actual own namespace: prepared CAS/lost reply/fresh observer/no second CAS,
  no reserved SQL Job, original native9/session decision/checkpoint retained.
  Fixed readonly SQL native six cases remain checked. Normal own cleanup and
  independent namespace/PV absence; frozen OCI identity/content graph verified.
- [ ] Next producer stages: SQL create intent/one dispatch/actual UID and Pod-Node
  capture/strict exact-reader inventory overlay/terminal logs/scoped cleanup.
  Schema structure/integrity/native login/OIDC/terminal/app/unlock/full T3–T6 OPEN.

Public application_sql_job_prepared=true/job_created=false/revision_verified=false/
schema_structure_verified=false/storage_recovery_authorized=false. Parser readable
states are not permission to create or resume future phases. Original native9/
checkpoint2/session decision1 are unchanged. Source bundle: a3e040940a37f2b04e599e25c39f2569ea3c4c2ef0344b336f68e19d12aeb9f2.
Native generation/runtime/Helm gates doubles, app data emptyDir/PG actual disposable
PVC, issuer/fencing lab-only. Earlier standalone SQL50 source360 is historical;
current helpers are shipped through the existing schema3 module.

## T4. Backup3, legacy2 и восстановление с заменой trust

**Files:** `scripts/kubernetes/lifecycle.py`, `scripts/kubernetes/postgres_tls.py`;
создать `backend/tests/test_kubernetes_postgres_tls_backup.py`;
обновить `backend/tests/test_kubernetes_runtime_trust.py` и release fixtures.

**Interfaces:** `validate_restore_profile(saved, target, transition, manifest_sha256,
context, namespace, now, *, namespace_uid) -> dict` возвращает проверенное решение; отсутствие
transition не разрешает изменение hashes/policy. Legacy identity обозначается
`unknown` отдельно от `None`/disabled.

- [ ] RED: backup3 missing PG identity, CA/CRL extra/missing/hash tampering,
  symlink/path traversal, raw CRLF bytes, частичная запись manifest, canary key
  в archive. `complete=true` записывается последним после выхода writer Job.
- [ ] Legacy2 unknown отказывает до lock/import. Только явный
  `--legacy-postgres-profile=disabled`, проверенный оператором исходный профиль,
  disabled target и отсутствие PG artifacts позволяют adoption. Старые format1
  продолжают использовать исторический kit; не добавлять неявную миграцию.
- [ ] RED: source backup с expired CA/CRL остаётся byte-valid; обычный restore
  с недействительным target отказывает. Предлагаемый `--postgres-tls-transition`
  принимает protected JSON из spec, проверяет exact manifest/source/target hashes,
  context/namespace/namespace UID/expiry/reason, canonical profile hashing из spec,
  current native trust, запрет downgrade/CRL removal. Reject duplicate JSON keys,
  bool version/NaN/unknown keys; legacy unknown не получает искусственного hash.
  Wrong binding/expired decision/изменённый Secret отклоняются до import.
- [ ] Сохранить решение/hash в restore evidence без key/password. Никаких загрузок
  CA по URL или копирования старого private key из backup. Доступность issuer и
  replacement leaf — часть DR prerequisites.
- [ ] GREEN: backup/TLS/runtime-trust/lifecycle tests; old kit отвергает backup3,
  new kit принимает валидный legacy2 только с явным adoption. Socket dump/restore
  и TCP metadata/integrity Jobs проверяются раздельно.

## T5. Настоящий TLS и projected Secret

**Files:** создать `scripts/kubernetes/tests/postgres_tls_acceptance.py`,
`backend/tests/test_kubernetes_postgres_tls_runner.py`,
`backend/tests/postgres_tls_probe.py`; расширить профильные backend tests при
необходимости, не менять frozen native assertions ради PASS.

- [ ] RED runner guards: explicit context/new namespace, owner+UID каждой удаляемой
  сущности, refusal foreign PVC/Secret/Pod, обязательные source/image digests,
  missing tools/fixtures → FAIL required run, cleanup failure → не PASS.
- [ ] Synthetic PKI, no real credentials: root/intermediate/leaf, revoked leaf,
  expired/not-yet-valid certificates/CRL, wrong CA/SAN/password, missing CRL,
  root0440/group999 key и backend0440/group1000, real read-only projection.
- [ ] Первоначальный initdb, restart, leaf rotation, CA overlap→switch→remove old
  trust; новое соединение принимает новый leaf и отвергает старый после удаления
  trust/revocation. GSS/plaintext не заменяют TLS. Проверить pg_stat_ssl,
  presented certificate fingerprint, SCRAM и commit; pool reuse не считается proof.
- [ ] Audit denied response→DB→stdout, UUID correlation/no replay, wrong trust
  bounded failure и recovery; failure budget измеряется без повышения thresholds.
- [ ] Evidence фиксирует frozen images/source hashes, platform/libpq/OpenSSL,
  assertions по каждому case и clean exit owned ресурсов. Private keys/contents
  документов/credentials в evidence отсутствуют. File-only result не заменяет T5.

## T6. Полный lifecycle, доставка и эксплуатация

**Files:** `.github/workflows/kubernetes.yml`, `deploy/ci/gitlab-kubernetes.yml`,
`deploy/ci/gitlab-kubernetes-consumer.yml`, `scripts/kubernetes/tests/configuration_acceptance.py`,
`docs/{KUBERNETES_DEPLOYMENT.md,KUBERNETES_TESTING.md,AUDIT_DELIVERY.md}`;
создать `docs/superpowers/reports/2026-10-05-kubernetes-postgres-tls-acceptance.md`.

- [ ] Одноразовый release: install→CRUD/BM25/SSE→restart→reapply+backup3→restore
  непустых данных в новые PVC; immutable audit rows/held historical outbox,
  integrity/totals, новый login, network isolation. Отдельно legacy2 adoption и
  disaster restore после истечения старого CRL с transition и новым leaf.
- [ ] Неудачная ротация, разрыв controller session и recovery из T3 проверяются
  на настоящих Pods. Состояние после отказа: app остановлен, lock сохранён,
  данные не импортируются повторно и два writer не возникают.
- [ ] До home proof измерить CPU/RAM/disk, `/tmp`+`/work`+logs, orphan/quarantine
  накопление и eviction; начальные 1Gi/256Mi не принимаются как corpus sizing.
  Стенды запускать последовательно; чужие сервисы не останавливать без нужды.
- [ ] Выполнить общие cold-start/storage-exhaustion критерии задач 1/6/8 [rollout-плана](2026-10-03-kubernetes-rollout.md): ENOSPC не публикует completed backup, partial restore не запускает writer, обычный restart сохраняет ключ/pending, DR выполняет ротацию/purge/hold до старта. Сценарии используют отдельные ограниченные тома/узлы; действующий домашний стенд и его диск не выводятся из строя. TLS профиль дополнительно повторяет current trust/CRL проверку после возврата узла; старый byte-valid сертификат не считается актуальным trust.
- [ ] Freeze exact candidate; выполнить относящиеся required audit matrices,
  backend coverage≥85%, frontend/project/Compose regression, dependency audit,
  package/standalone-consumer и hosted CI. Старые v7/v16 результаты не переносятся.
- [ ] Runbook: PKI owner, expiry/CRL alerts, issuer access, RPO/RTO и backup retention,
  protected values/keys provisioning, recovery kit2/kit3, Secret/HBA cleanup.
  Нативные platform/CSI/NetworkPolicy проверки остаются отдельной приёмкой.

## Критерий завершения и приоритет

T1–T6 отмечаются выполненными только с командами, exit codes, case counts,
image/source identity и сохранёнными исходными FAIL в отчёте. Implementation,
local proof, home proof и hosted/platform proof имеют отдельные статусы.
Review документации не закрывает ни один из этих пунктов. Новые блокировки
сначала локализуются; замена проверки фиктивным PASS или изменение threshold
не допускаются. Реплики frontend → API/worker split → backend replicas остаются
последующими этапами общего rollout; TLS не включает их автоматически.


## T3 checkpoint: SQL reader dispatch (06.10.2026)

- [x] Separate SQL create intent before one exact create, then actual Job UID CAS.
- [x] Observer-only recovery after lost intent/create/UID reply; no second create.
- [x] Independently derive protected old-image proposal and journal/native/session
  anchors before allowing the exact read-only Job/Pod in local inventory.
- [x] Check full namespace UID/name uniqueness before filtering; preserve unrelated
  resources and reject extra reader, changed owner/template/token/writable mounts.
- [x] CLI `postgres-recovery-dispatch-application-sql` requires saved generation,
  pipeline-stop/fencing and protected session record with independent anchor.
- [ ] Durable first SQL Pod/Node/Lease capture, terminal verdict and scoped cleanup.
- [ ] Structural schema compatibility, whole data/FS/Qdrant/outbox integrity and
  actual post-rotation login before terminal/app writer/unlock.

New60 cases; aggregate Windows871/268.3s and Linux1356/
349.67s, zero skips, source364/frozen274. Actual product SQL Job
create lost-response/fresh observer/no second create/Job UID and exact Pod/native
read-only terminal revision proof PASS. That native terminal observation is not
persisted as a product SQL verdict: public record remains job_observed/verdict=null.
Whole generation/runtime/Helm native gates remain fixture doubles; version table,
issuer/fencing are synthetic and appdata emptyDir. T3–T6 and backup3 guard remain OPEN.


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


## T3 checkpoint: durable SQL completion (06.10.2026)

- [x] Persist the first actual SQL Pod UID/name and scheduled Node/Lease identity.
- [x] Permit only the same unscheduled Pod to acquire its scheduled binding.
- [x] Repeat protected prerequisites, exact Job/Pod/Node and successful terminal
  proof before and after bounded2048-byte readonly revision logs.
- [x] One constrained verified CAS; a verified observer repeats current gates and
  terminal identity, without another SQL Job, log read or journal write.
- [x] Compare all non-SQL lockdata and stable metadata across transitions; ignore
  only SQL field/resourceVersion/managedFields. SQL Job/Pod RVs typed and bounded.
- [x] CLI requires the same old package, manifest, pipeline-stop/fencing, protected
  session record and independent anchor as SQL dispatch.
- [ ] Next: scoped SQL cleanup, both Job/Pod absence and one UID/RV Foreground DELETE.
- [ ] Structural schema and whole data/FS/Qdrant/outbox integrity, actual session
  login/provider rollback, terminal/app writer/unlock; full T3-T6 remain OPEN.

New28 cases; source365/frozen274. Windows899/312.16s/25 modules,
Linux1384/410.78s/37 modules, zero skips. Actual product SQL Pod/Node
binding, successful readonly revision and durable verified CAS/lost reply/fresh
observer without logs or write PASS; original native9/session1/CP unchanged. The
version table is synthetic; full native generation/runtime/Helm gates are doubles,
issuer/fencing lab-only, appdata emptyDir. No structural/writer authorization.


## T3 checkpoint: удаление SQL reader (06.10.2026)

- [x] После verified сохранить отдельный cleanup intent с фактическими Job/Pod RV.
- [x] Перед удалением повторить защищённые gates и точную идентичность объектов.
- [x] Один Foreground DELETE с preconditions UID/RV; сохранённый intent допускает
  только наблюдение, без повторного DELETE после прерывания или потери ответа.
- [x] cleaned CAS только после отсутствия и Job, и Pod; повторная проверка после CAS.
- [x] GC допускает только известные поля удаления; чужие объекты, замена Job/Pod,
  неизвестные finalizers и повторное появление после cleaned запрещены.
- [ ] Структурная совместимость SQL, целостность данных/FS/Qdrant/outbox, фактический
  login после ротации и защита от возврата старого ключа, terminal/writer/unlock.

Новые40 тестов; source366/frozen274. Windows939/26 модулей/
340.29s; Linux1424/38 модулей/549.73s, без skips.
В настоящем Kubernetes прошли условный DELETE, потеря ответа и новый observer
без повторного удаления. Native9/session1/checkpoint/SQL42 сохранены. Полные
native generation/runtime/Helm gates остаются doubles, версия БД синтетическая,
issuer/fencing лабораторные, appdata emptyDir. T3–T6 и backup3 guard остаются OPEN.


## T3 checkpoint: проверка структуры SQL (06.10.2026)

- [x] Строгий публичный эталон public-schema-1: digest образов backend/PostgreSQL,
  версия PostgreSQL, единственная Alembic head, hash каталога и ограниченные counts.
  Ожидаемый SHA эталона поступает независимо из защищённого выпуска.
- [x] Фиксированный read-only REPEATABLE READ probe с verify-full/SCRAM и точным
  leaf SHA; сравнение relations/columns/defaults/constraints/indexes/sequences/
  triggers/routines/RLS policies/types/extensions/rewrite rules. Установлены N+1
  пределы, 30s бюджет, statement timeout3s, lock timeout1s, общий предел1MiB.
- [x] Только hash/counts/version выходят из collector; ошибка даёт {"ok":false}.
  Структурное совпадение не разрешает writer или unlock.
- [ ] Создание эталона в отдельной пустой БД миграциями закреплённого старого образа;
  независимый protected promotion SHA, воспроизводимость и проверка происхождения.
- [x] Dedicated структурный Job/controller: durable intent, UID/Pod/Node/Lease,
  строгий verdict, lost-response observer, owned cleanup; исходный SQL record1
  остаётся revision-only. Не получать эталон из восстанавливаемой БД.
- [ ] Роли/grants и конфигурация БД, реальные данные/FS/Qdrant/outbox, login после
  ротации и защита от старого ключа, terminal/writer/unlock, полный T3–T6.

Source367/frozen274; новые67 cases, Windows1006/27 модулей/362.76s,
Linux1491/39 модулей/574.0s, без skips. В отдельной PostgreSQL17.11
прошли14 проверок каталогов и6 запусков фиксированного TLS probe. Создана настоящая
схема миграциями в тестовой БД; повреждения столбцов, ограничений, trigger, RLS,
sequence, policy и rewrite rule отвергаются. Существующий native recovery/cleanup
повторно проверен, его ограничения сохранены. На этом историческом checkpoint product structural Job ещё не был создан;
текущий статус приведён ниже.


## T3 checkpoint: durable структурный reader и ограниченное ожидание (07.10.2026)

- [x] Fixed offline producer создаёт reference1 только миграциями pinned старого
  backend в пустой scratch-БД. Две отдельные PostgreSQL дали byte-identical
  reference; повтор на непустой БД отвергнут, каталог после отказа неизменен.
- [x] Отдельный postgres_application_schema record1 после native9/session
  decision1/CLEANED revision SQL1: SHA журналов, recovery/checkpoint, current
  Secret UID/RV и independently protected reference SHA/backend/PG/head.
  Старый revision-only SQL record и остальные поля lock не меняются.
- [x] prepare/dispatch/complete/cleanup-application-schema: durable intent до
  единственного create, Job/Pod/Node/Lease binding, readonly verdict, Foreground
  UID/RV DELETE, отсутствие Job и Pod до CLEANED. Lost replies наблюдаются без
  повторного create/delete. Namespace inventory проверяется до фильтрации.
- [x] Actual Kubernetes structural Job на реальной схеме миграций старого образа:
  disabled FK trigger отвергнут, exact restoration принята; CAS/lost replies/
  fresh observers/owned cleanup. Storage writer и unlock остаются запрещены.
- [x] Source377/frozen274, новые252 cases. Windows1258/36 модулей/
  344.45s; Linux1743/48 модулей/714.22s,
  без skips. Независимые review structural/cache/wait без блокеров.
- [x] Original native600 gate пройден новым кандидатом без увеличения бюджета.
  Ранние source375/376 observer600 FAIL и source363 observer900 FAIL сохранены.
- [ ] Общий owned offline build runner и independently protected promotion:
  фиксированный producer helper не подтверждает изоляцию внешнего pipeline.
  Printed self-hash не становится trusted expected SHA.
- [ ] Roles/grants/global DB settings, реальные данные/FS/Qdrant/outbox,
  native login/OIDC после явной ротации APP_SECRET_KEY и antirollback,
  terminal/app-writer/unlock, pre-dispatch repair и полный T3–T6/backup3 guard.

Bundle SHA256: 53842ff31251a1dfc21063d0fa2843ce878d83497e2545c3fc8da8044f492ac7. Общие generation/runtime/Helm native
checks ещё doubles; issuer/fencing лабораторные, appdata emptyDir. Проверка
каталога public не означает полную приёмку восстановления. Working deployment,
локальный запуск и Compose не обновлялись; hosted CI ещё не подтверждён.

## T3: общий offline build и независимое подтверждение эталона

- [x] Offline CLI в прежнем модуле operator kit; schema3/allowlist не изменены.
- [x] Проверка actual root/selected-platform image identity, собственные internal
  networks, tmpfs, nonroot/read-only, без host ports/binds и implicit pull.
- [x] Два fresh PG, побайтно одинаковый эталон, повтор отвергнут до миграций,
  каталог неизменен; bounded execution и штатная cleanup с независимым absence.
- [x] Atomic write-once artifacts, strict внешний SHA/ref/image/head verifier;
  Windows/Linux contracts и реальное native доказательство.
- [x] Прежний kit consumer/bootstrap, module loading и воспроизводимый archive
  проверены. Новый CLI работает из извлечённого комплекта без source checkout.
- [x] Обезличенная [операторская инструкция](../../KUBERNETES_SCHEMA_REFERENCE.md),
  включая отказ/retained IDs и узкое ручное pre-dispatch cleanup решение.
- [ ] Protected pipeline promotion: независимые expected SHA/immutable storage,
  builder/toolbox/image/head identity и права promotion. Self-hash не даёт trust.
- [ ] Реальная проверка roles/grants/settings, данных/FS/Qdrant/outbox,
  all-provider invalidation и native login/OIDC/antirollback после разрешённой
  ротации ключа; terminal/writer/unlock, pre-dispatch operator repair, T4–T6.

Source392r5 adopted07.10.2026: focused509 PASS/0skip на Windows4.57s и Linux10.64s;
collection1664/2149. Actual native23.16s и independent absence PASS. Kit Windows/
Linux64 PASS+1skip (нет Helm/kubectl в unit окружении); этот standalone bootstrap
сценарий отдельно выполнен настоящим pinned toolbox PASS. Synthetic revision
не опубликован. Original r2/r3/r4 FAIL и ошибочный kit observer count сохранены.
Рабочее приложение/local/Compose не обновлялись, writer=false, fullT3=false;
полный прежний runtime matrix source377 используется отдельно, hostedCI OPEN.



Проверка package layout, 07.10.2026. После переноса source377 найден collection ERROR: три теста импортировали соседний helper без относительного пути. Исправлены только эти три импорта; существующий tracked пустой tests/__init__.py включён в manifest source378. Runtime-файлы побайтно совпадают с принятой версией377. Windows: collection1258 PASS, smoke58 PASS/3.00s, изменённые модули37 PASS/1.39s; Linux: collection1743 PASS и focused89 PASS/5.33s. Независимое ревью:37 PASS и collection1258 PASS, блокирующих замечаний нет. Завершённый собственный Linux-контейнер удалён штатно, независимое отсутствие ресурсов проверено.

Первоначальный collection ERROR сохранён; ошибочное сообщение wrapper о58 PASS до исправления явно отозвано (фактически0). Полный runtime/native matrix для test-only изменения не повторялся: используется ранее принятый source377. Hosted CI, generic owned builder/protected promotion и полная приёмка T3 остаются OPEN. Разрешение на запуск приложения и снятие запрета записи не выдано этой проверкой.


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


- [x] Закрытый bounded record и явный CLI с одной lock CAS; прежний readonly путь сохранён.
- [x] Deadline regression RED→GREEN, Windows/Linux73, fault/CLI29.
- [x] Native lost reply/idempotent/RV race и независимая штатная очистка.
- [ ] Protected publisher/promotion, provider login, antirollback, repair и activation/unlock.


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


- [x] T4 closed canonical profile и проверка protected transition metadata, включая namespace UID.
- [x] RED76→GREEN76, combined208 Windows/Linux, independent144.
- [ ] T4 archive byte protocol/backup3/legacy adoption/controller/native restore — следующие этапы.


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


Дополнительный обязательный consumer gate: storage validator сейчас отслеживает
PVC и application-теги, но сам не классифицирует Job с runtime Secret БД без PVC.
Это воспроизведено на metadata fixture; permission-флаги helper остаются false.
Перед общим enablement нужны полные namespace inventories Pod/Job/CronJob,
Deployment/StatefulSet/DaemonSet/ReplicaSet и ссылки из обычных, init и ephemeral
containers, envFrom/keyRef и projected volumes. Классификатор не должен сохранять
значения env, команды или приватные template bytes. Пропуск разрешён только для
точной выбранной application/storage generation и native-проверенного readonly
operation cohort. Неизвестные credential consumers блокируют дальнейший эффект.
Сам перечень ссылок не доказывает отсутствие произвольных внешних SQL writers.

Дополнительный обязательный gate для resume: до stop сохранить отдельную
неизменяемую запись исходной конфигурации под точными UID parent/namespace/lock
и operation. Checkpoint3 сам не сохраняет исходные runtime ConfigMap/Secret RV,
Qdrant generation, PV specification и Service targets между процессами. Новое
чтение после restart должно сравниваться с сохранённой исходной записью,
а не становиться новым основанием для запуска. Хранить только закрытые
публичные UID/RV, привязки, разрешённые template/spec SHA и public TLS identity;
значения Secret и хеши приватного содержимого не сохранять.

Допустимые изменения replicas/RV/Pod приложения и информационного storage
status описать отдельно по полям. Они не разрешают drift runtime/TLS UID/RV,
шаблонов, PV bindings/specification или service targets. Неизвестный ответ
создания записи восстанавливать пассивной проверкой UID/body без замены записи
или повторного stop/start. Проверки fresh Manager с изменёнными runtime/TLS RV,
Qdrant/PV/service обязательны до native effects и общего enablement.


### Уточнение статуса нативного backup/restart — 08.10.2026

Принятая реализация producer остаётся прежней (набор 475). Последующие изменения родительского контроллера проходят отдельную проверку и ещё не включены в рабочий runtime. Полная приёмка T3/T4 остаётся открытой.

- Проверены отдельно: штатная остановка приложения, полный producer и публикация архива, запись исходного срока запуска до перехода в `start_intent`; самостоятельный SQL-reader с PostgreSQL `verify-full`/SCRAM, результатом readonly SQL и сохранением результата через UID/RV CAS.
- Последний неизменяемый кандидат 534: Windows 3099 passed / 19 skipped, Linux 3152 passed / 5 skipped; оба в исходных 180 секундах. Нативная проверка SQL-reader: 870,01 из исходных 900 секунд. Стенд использовал служебную остановку для подготовки этой отдельной проверки; это не сквозная приёмка родительского контроллера.
- Реализуются отдельно: завершение reader с условным удалением Job, подтверждение отсутствия Job/Pod, неизменяемое намерение запуска и единственный UID/RV patch приложения. Локальные проверки этих границ не заменяют нативную приёмку.
- Следующие обязательные проверки: нативное завершение reader; запуск и готовность выбранных Deployment/ReplicaSet/Pod и обоих контейнеров; HTTP без обращения к моделям; readonly TLS/SCRAM из работающего backend; `completed` CAS и условное снятие блокировки; статус/восстановление после неоднозначного ответа; метаданные Helm, защищённое происхождение запуска и стенд дома.

Сохраняются исходные абсолютные сроки: reader Job — 60 секунд от создания, удаление — 180 секунд от записанного намерения, запуск — 600 секунд от первоначального effect. Отдельная компонентная проверка не продлевает эти сроки и не считается успешной сквозной проверкой. Предыдущие неуспешные прогоны и открытый полный backend gate сохраняются.

### Сохранение журналов и ограничения времени backup — 08.10.2026

Принятый runtime остаётся на наборе 475. Отдельный кандидат 545 прошёл полный Windows-набор (3400 passed / 19 skipped, wrapper 125,11 с) и Linux-набор (3453 passed / 5 skipped, wrapper 106,66 с) в прежних 180 секундах. Это проверка кода, а не нативная приёмка запуска приложения.

- [ ] Нативно подтвердить удаление повторного reader по UID/RV, отсутствие его Job и Pod, затем запуск приложения, оба Ready-контейнера, HTTP без обращения к моделям и readonly SQL через выбранный backend.
- [ ] Сохранить `completed` до отдельного намерения снять lock; удалять lock один раз по UID/RV с `propagationPolicy: Orphan`, сохраняя журналы. Обычный `release_lock` должен отказывать для standalone backup3.
- [ ] После снятия lock отдельно подтвердить сохранность исходного source-журнала, terminal producer, родительского журнала и защищённого архива. Исторический status не подтверждает текущий trust, готовность приложения или происхождение архива.
- [ ] Повторить нативный этап после проверки гипотезы объединённых чтений. В первом компонентном запуске 545 передача состояния функционально завершилась за 920,38 с, но исходный предел 900 с был превышен: результат FAILED, этап удаления reader не запускался. Выполнена условная штатная очистка Namespace/PV/wrapper/тома; исходный отказ сохраняется.

Для кандидата 547 проверяется объединение точно выбранных ресурсов в ограниченный `get`: все три независимых снимка, UID/RV, типы, namespace, размеры, сроки и проверки источников сохраняются. Локальные связанные проверки: 157 passed. Полные платформенные проверки 547 прошли: Windows 3465 passed / 19 skipped, wrapper 127,84 с; Linux 3518 passed / 5 skipped, wrapper 106,80 с; исходный предел 180 с сохранён. Повторный нативный компонент 547 прошёл: подготовка архива/SQL-reader и передача — 696,28 / 900 с; удаление reader свежим процессом — 104,49 / 180 с. Переданы только защищённый архив и публичные ссылки; права на эффекты из памяти не переносились. Job удалён один раз по UID/RV, отсутствие Job/Pod и сохранность источников/архива подтверждены, временные ресурсы штатно удалены. Общая цепочка запуска приложения, completed и Orphan unlock ещё требует нативной проверки; срок запуска 600 с и удаления 180 с не продлеваются. Полная T3/T4, защищённое подтверждение происхождения, Helm и ранее неуспешный общий backend gate остаются открытыми.


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
