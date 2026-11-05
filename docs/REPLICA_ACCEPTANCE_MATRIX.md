# Матрица приёмки реплик и хранилищ

Состояние доказательств на05.11.2026. План реализации:
[Kubernetes replicas and storage](superpowers/plans/2026-10-31-kubernetes-replicas.md).
Зелёный отдельный тест или старый отказной прогон не заменяет приёмку всей
текущей версии и выбранного профиля. Ни один профиль пока не объявлен resilient.

| Область | Подтверждённый результат | Незакрытая проверка |
|---|---|---|
| Общий frontend functional runner | [Новый 12-stage native прогон](superpowers/reports/2026-11-05-frontend-native-acceptance.json) на e42007a: durable intents, один upload/export/UID DELETE, два NDJSON потока, прерывание без model replay, replacement/session/SHA/search/ZIP/logout, холодная проверка и закрытие пяти процессов. Fixture off/3 PVC retained; CI и обе live capacity loans возвращены с 2/2/2/runtime/PVC/9 таблицами. Linux 6859 cases = 6638 passed + 221 прежний optional skip, 0 errors/failures, 87,70% coverage; Windows 170 passed + 1 Linux-only skip | Simulation не закрывает CSRF/SSO; отдельный реальный прогон приведён ниже. Доверенный release bundle, другие recipes и физические домены остаются отдельными |
| Frontend-split и потеря frontend при стриме | [Выбранная закрытая репетиция](superpowers/reports/2026-11-04-replica-frontend-split.json):2 frontend/1 combined,6 CNI отказов PG/Q/model, общая сессия/upload/SHA/search/ZIP/NDJSON. UID-delete при model-active1 оборвал stream без result; survivor/replacement/backend сохранили данные, model вызван один раз. Logout401 на3 маршрутах. Новый стенд выключен/3PVC сохранены, обе прежние2/2/2/runtime/PVC/9 tables возвращены | Simulation исключает production CSRF; её ограничения сохраняются. Реальный owned Gateway/IdP проверен отдельным новым прогоном ниже. Первые ошибки стенда/dry-run сохранены отдельно |
| Реальный owned Gateway/Keycloak/CSRF | [Все12 native этапов](superpowers/reports/2026-11-05-frontend-oidc-native-acceptance.json) на fdfae89: secure SSO, missing/wrong CSRF403 с сохранением сессии, два frontend slot/Service, upload/search/download/ZIP/NDJSON, active UID loss без model replay, replacement/logout/cold SHA. Первый кадр0,074/0,078с при потоках12,108/12,113с.3 handles closed/8 controllers0/3PVC retained; обе исходные2/2/2/runtime/PVC/9 таблиц возвращены. Linux7003=6782pass+221unchanged optional skip/0fail/87,70% | Customer Gateway/IdP и другие storage bindings; доверенный подписанный release bundle, distributed/resilient/physical qualification |
| Текущая managed-S3 PG/Qdrant/model matrix | [Нативная объединённая матрица](superpowers/reports/2026-11-05-replica-provider-matrix.json): реальный stale SQL-token отклонён с rollback; Qdrant denial на4 Pod с12 положительными контролями и та же работа восстановлена при fence2. Неопределённый model reservation сохранён после TTL и сверён двумя API; отдельное физическое восстановление выполнило один аудит точного slot, та же работа завершена при fence4. Все4 документа/SHA/search/старый ZIP/сессия сохранены; свежая проверка epoch6/2/2/2/noCNP/idle. Исторический parent остаётся10/14. [Закрытие](superpowers/reports/2026-11-05-replica-provider-matrix-closure.json): CI6b41f85 —7317 cases/7096 pass/221 прежний optional skip/0fail/87,70%; fixture off/3PVC, оба займа возвращены с2/2/2/runtime/PVC/9 таблицами. Шесть истёкших TLS-листьев обновлены с прежними CA и ключами | Другие bindings, длительные отказы, физические домены и полная qualification |
| All-external S3 runtime и независимый restore | [Закрытая репетиция](superpowers/reports/2026-11-04-replica-all-external-runtime-recovery.json): три внешних для app namespace TLS/scoped provider,2/2/2, ноль storage/PVC/root credentials в app. Независимая format3 копия и source-off restore:81 таблица/28 sequences/2 full vectors/6 blobs; held adoption,172 work rows неизменны, старые/new HTTP и4 процесса12 SHA. Source/target off held1/held3,6 PVC сохранены, обе прежние2/2/2/runtime/PVC/9 tables восстановлены | Отдельный учебный provider namespace на той же VM; конкретные customer providers, другие варианты, реальные model/IdP и полная qualification остаются открытыми |
| Основание P0/P1 | Контракты профилей, инвентарь writers, явная namespace/installation identity и изоляция тестовых входов реализованы | Входы и полномочия инфраструктуры заказчика |
| Frontend F1/F2 | Разделение frontend/backend, общая сессия и доступ к API проверены на двух frontend; прямой доступ frontend к storage запрещён CNI | Customer Gateway/SSO и реальные IdP endpoints |
| Файлы S1–S4 | Local/RWX/S3 adapters, перенос потребителей, GC и возобновляемая offline-миграция с проверкой целостности реализованы; [runbook](ARTIFACT_STORAGE_MIGRATION.md) | Квалификация конкретного RWX/CSI/provider; миграция рабочего корпуса выполняется только после проверенного backup |
| Runtime R1–R4 | PostgreSQL ownership/fencing, роли API/worker и координация реализованы; cancel/delete, worker-loss и partition прошли в общем текущем запуске | Дополнительные внешние providers и полная release matrix |
| Runtime R5 | Общий текущий22-stage run прошёл без failures/required skips;80 прежних документов сохранены, финальные2 frontend +2 API +2 workers проверены | Другие providers, реальные модели/IdP и физические домены отказа требуют собственных gates |
| Storage O1 | Managed basic PG/Qdrant/S3, TLS, scoped credentials, смена CA и CNI; NFS/CSI, RWX application/ENOSPC/restart и fresh RWX-target restore проверены. Отдельный mixed DS211 NFSv4.0/CSI4.13.6 + managed PG/Q прошёл2/2/2, пользовательскую EDQUOT507 и повтор после освобождения места | Исходная большая запись в одном Linux-ядре остаётся failed; длительный outage/cross-node, остальные external provider contracts и независимые failure domains открыты |
| Recovery O2 | Независимые current-copy, rpl04→rpl05 replacement, compact held-handoff и selected RWX-target recovery прошли. Корпус внешнего NAS отдельно скопирован на ПК и восстановлен в новый S3 target; прежние данные/новая публикация/2/2/2 проверены при остановленном источнике и запрещённом из target доступе к NAS. Проверены cold-check, журнал попыток, учебное восстановление ключа и локальные alerts | NAS-target relocation вне строгого NFSv4.1 рецепта, остальные provider recipes; production key custody/monitoring не квалифицированы |
| Release O3 | Обязательные project checks и профильные Windows/Linux cohorts;557 parser passes и standalone frontend build; полный Linux backend CI fdfae89:6782 passed/221 прежний optional skip/87,70% coverage, включая real-OIDC native frontend runner. Финальное ревью и пять исправлений проверены; policy validator, подписанные файлы и read-only runner реализованы. [Семь профилей](../deploy/kubernetes/replicas/support-matrix.json) перечислены с открытыми gates, certified-профилей нет | Повтор CI при последующих изменениях кода, реальные доверенные evidence producers, общая fault/profile matrix и завершение runbooks |

## Доказательства учебного профиля

[Заполнение S3 на уровне приложения](superpowers/reports/2026-11-04-replica-s3-application-full.json)
проверено на65d6ee2: отдельный128Mi tmpfs дал ENOSPC, новая загрузка получила503,
прежние оригинал/поиск/ZIP сохранились через2frontend/2API. Независимая очистка
своего файла сработала через75,08с; та же регенерация автоматически завершилась
на попытке3/fence3 до140,52с без редактирования очереди/аренд/slots. Два ранних
поколения abandoned, одно новое active. Повтор известного503 с тем же ключом
дал ровно одну новую загрузку. Четыре процесса проверили16cold SHA,2документа,
5 точных business works,2upload receipts/audit и scratch0; новый ZIP содержит
оба исходных SHA. Полный Linux CI6489passed/221 прежний optional skip,0failures,
87,70% покрытия. Испытание выявило и закрыло ошибку классификации BlobUnavailable.
Новый стенд выключен/held3/2PVC сохранены,16 синтетических blob скопированы и
перепроверены на отдельном ПК; обе временные ёмкости возвращены. Копия файлов
не заменяет format3 backup. Этот результат не квалифицирует PG/Q exhaustion,
production quotas, физическую HA или полный mixed-профиль.

| Испытание | Свидетельство и граница результата |
|---|---|
| Независимое восстановление compact | [Фактическая репетиция](superpowers/reports/2026-11-03-replica-compact-independent-restore.json): источник полностью выключен; независимая копия,80 таблиц/27 последовательностей,2 вектора и16 файлов проверены до официального rpl04→rpl05. S4 перенёс10 объектов после реального exit23 с возобновлением без перезаписи. Затем прошли held handoff/adoption/retry и2 frontend/2 API/2 worker без рестартов: три исходных задания выполнены по одному разу, accepted checkpoint SHA сохранён, approval не выполнено, прежние search/download и новая публикация проверены. Frozen bundle содержит только исходное событие до cutoff; повторно проверены13 событий/expiry и10 объектов. Новый target остановлен с сохранением PVC под held epoch3; прежняя учебная топология2/2/2 восстановлена и сверена по runtime/PVC и девяти каноническим таблицам |
| Legacy diagnostic journal | [S4 journal/input conversion](superpowers/reports/2026-11-03-replica-legacy-diagnostics.json): перенос истории с исходным сроком хранения, замороженный запрос, cold/source-off worker, откат и повторный перенос проверены на синтетических данных и native PostgreSQL. Фактическая source-off Kubernetes/provider/backup приёмка этого managed-S3 рецепта приведена в независимой compact-репетиции выше; другие рецепты остаются отдельными gates |
| Очереди compact: atomic handoff | [SQL/CLI handoff](superpowers/reports/2026-11-03-replica-compact-handoff.json):176 Windows и24 native PostgreSQL passes; сохранение исходных заданий/одобрений, новых queued WorkItems и frozen inputs, общий откат и повтор после сбоя. Последующее покрытие legacy diagnostic journal приведено отдельно выше; фактическая source-off Kubernetes приёмка этих трёх рецептов приведена в независимой compact-репетиции выше; остальные рецепты требуют отдельных прогонов |
| Compact adoption SQL/CLI | [SQL/CLI evidence](superpowers/reports/2026-11-03-replica-compact-adoption.json):153 Windows passes/2 platform skips и18 native PostgreSQL passes; сохранение непустого корпуса/NULL generations, held epoch1 и возобновляемая публикация identity проверены на отдельной PostgreSQL17.11. Фактическая source-off приёмка managed-S3 рецепта с пропущенной/NULL generation и тремя queued intent завершена отдельно выше; это не квалификация остальных providers |
| Общий runtime | [Common runtime](superpowers/reports/2026-11-02-replica-common-runtime.json): все22 этапа одного текущего запуска, maintenance15→16→17,80 исходных документов сохранены; только учебный managed-S3/stub/fake profile |
| Source-off backup/restore | [Managed-S3 recovery](superpowers/reports/2026-11-01-replica-managed-s3-recovery.json): прежний3-document архив восстановлен в новый target; это не restore последней копии |
| Последняя независимая копия | [Before-scoped-faults backup](superpowers/reports/2026-11-02-replica-before-scoped-faults-backup.json):20 документов/100 blobs; [current-copy recovery](superpowers/reports/2026-11-02-replica-current-copy-recovery.json): фактический source-off restore в новый namespace, scoped permissions, отзыв25 сессий и7 runtime smoke checks. Полный22-stage R5 остаётся результатом исходной установки |
| Scoped permissions | [Credentials](superpowers/reports/2026-11-01-replica-scoped-credentials.json): фактические SQL/S3 положительные и отрицательные проверки |
| Смена CA | [Coordinated CA rotation](superpowers/reports/2026-11-01-replica-coordinated-ca-rotation.json): согласованное обновление работающей учебной установки |
| Обновление image | [Scoped image upgrade](superpowers/reports/2026-11-02-replica-scoped-image-upgrade.json): прежние документы и сессия сохранены; схема не менялась |
| Заполнение S3 | [Bounded S3 ENOSPC](superpowers/reports/2026-11-02-replica-bounded-s3-enospc.json): реальный ENOSPC в отдельном128Mi tmpfs, сохранность контрольного объекта и восстановление записи; весь путь приложения/PVC этим не проверен |
| Полный metadata snapshot | [Bounded corpus snapshot](superpowers/reports/2026-11-02-replica-bounded-corpus-snapshot.json): все10766 work rows учтены, тот же held operation безопасно завершён; общий родительский прогон остался неуспешным |
| Лимит LLM | [Capacity observation](superpowers/reports/2026-11-02-replica-capacity-observation.json): оба API отказали при занятом слоте, нормальный RPC handoff допустим, пик1 и целостность документов подтверждены; отдельная диагностика не закрывает общий run |
| Небольшая нагрузка | [Scoped load diagnostic](superpowers/reports/2026-11-02-replica-scoped-load-diagnostic.json): одна небольшая synthetic trial с1/2 workers; не оценка production throughput или линейного ускорения |
| CI и свободный диск | [Lab CI capacity recovery](superpowers/reports/2026-11-02-replica-lab-ci-capacity-recovery.json): неуспешный ENOSPC CI сохранён; raw temporary artifacts проверены и скопированы на ПК до удаления только собственных временных деревьев |
| Полный baseline CI | [Backend CI](superpowers/reports/2026-11-02-replica-baseline-backend-ci.json): снимок e3c7025,5910 passed/183 skipped/0 failures, точные case IDs обоих shard; не полный CI последующих изменений |
| Финальное ревью и полный CI backend | [Единый проход исправлений](superpowers/reports/2026-11-03-replica-final-review-fixes.json): все пять Important-замечаний получили RED/GREEN. Полный ccfcf9e:6652 точных case IDs,6431 passes/221 explicit optional skips/0 failures/errors/87,67% coverage;2429 Git-файлов неизменны. Native PostgreSQL:87 passes/8 explicit provider skips. Прежние неуспешные/отменённые прогоны сохранены. Новый runtime сверил573 Git-файла,578 app files и117 прежних пакетов и импортирован в kind; это не квалификация приложения/провайдера |
| Реальный RWX/NFS bootstrap | [NFS/CSI evidence](superpowers/reports/2026-11-03-replica-nfs-rwx-bootstrap.json): отдельно разрешённый private NFSv4, новая ограниченная1GiB ext4 ФС, pinned CSI4.13.6/Kubernetes1.36.4, nondefault Retain SC. Два Pod проверили идемпотентность, отказ перезаписи, чтение/range и конкурентное создание с одним победителем. Это один kind-узел/одна VM; приложение, server loss/full-storage, restore и cross-node ещё не квалифицированы |
| RWX приложение и ограниченные отказы | [Текущий ccfcf9e на RWX](superpowers/reports/2026-11-03-replica-rwx-application.json):2/2/2, общая сессия, idempotent upload/search/download/export; реальный507 без частичной записи, exact-inode cleanup и повторная публикация; NFS restart сохранил сессию и данные;11 SHA проверены на четырёх процессах. Первый большой буферизованный NFS write завис и не засчитан, физическое восстановление сохранено. Новый target выключен под held epoch1 с тремя PVC; прежние2/2/2 восстановлены. Независимый RWX restore и large-write/outage/cross-node gates остаются открытыми |
| Независимая RWX-копия | [Холодная проверка копии](superpowers/reports/2026-11-03-replica-rwx-independent-backup.json):13 зашифрованных объектов на отдельном ПК, native PG17 с81 таблицей/28 последовательностями, четыре полные точки Qdrant и11 blob. Все объекты расшифрованы и аутентифицированы, adjacent physical binding привязан HMAC manifest. Source app/storage выключены под epoch1; прежние2/2/2 восстановлены. Это backup; RWX restore/rebind с доказательством отдельного mount ещё не выполнен |
| RWX-source → реальная S3-цель | [Завершённый source-off restore](superpowers/reports/2026-11-03-replica-rwx-source-s3-restore.json): независимая PC-копия, все81 таблица/28 sequences, четыре полные точки/11 blobs совпали до adoption. Источник NFS/PG/Qdrant/app физически выключен; unchanged CLI held2/release, старый cookie401, прежний корпус/export и новые upload/export прошли на2/2/2. Четыре native процесса сверили17 объектов/scratch0/старые задачи без повторного выполнения. Target остановлен/held3/три PVC сохранены, NFS и прежние2/2/2/девять таблиц восстановлены. Этот отчёт не проверяет RWX-цель; отдельная проверка приведена ниже |
| Независимый restore в свежую RWX-цель | [Завершённая cf74f8a репетиция](superpowers/reports/2026-11-03-replica-rwx-target-restore.json): полный Linux CI6466 passes/221 прежний optional skip/0fail, точный новый образ; manifest-bound source и фактический новый NFS export/inode. Все81 таблица/28 sequences/четыре точки/11 blobs совпали до adoption; held2, отзыв сессии, явный release. Реальный exit23 до output после уже успешной adoption и повторный CLI сохранили identity и109 записей каталога.2/2/2 HTTP и четыре native процесса17 SHA/scratch0/задачи однократно прошли. Target физически остановлен/held3/три PVC сохранены, прежние2/2/2/девять таблиц восстановлены. Source claim unmounted, общий NFS-сервер работал; это не server-off/large-write/cross-node qualification |
| Удалённый Synology NFSv4.0 | [Компонентная проверка](superpowers/reports/2026-11-03-replica-synology-nfs4-component.json):256 МиБ через текущий RwxBlobStore, другой процесс проверил SHA/range/conflict; реальные непривилегированные контейнеры и NFS mount. Квота1 ГиБ не сработала:1025 МиБ приняты без storage-full, тест остановлен на лимите. Только свои файлы удалены, mounts/контейнеры закрыты, прежние2/2/2 Ready. Это не Kubernetes/CSI/app/restore/outage приёмка; v4.1 отклонён, quota gate открыт |
| Synology: квота правильного пользователя и CSI | [Последующий прогон](superpowers/reports/2026-11-03-replica-synology-userquota-csi.json): Vitaly UID1031/GID100; настоящий StorageFullError, контрольный SHA и повтор failed object ID после очистки прошли. Существующий CSI4.13.6, новый Retain PVC и два отдельных Pod прошли256 МиБ write/cold SHA/range/conflict, затем удалили свои объекты и завершились. PVC/PV/namespace/nondefault class сохранены пустыми; живых CRI/NFS mount нет. Это закрывает компонентные quota/CSI gates; app/backup/restore/outage/cross-node и durability остаются открытыми |
| Принятие schema-fence | [Schema adoption](superpowers/reports/2026-11-02-replica-schema-adoption.json): реальная SQL-миграция непустой PostgreSQL rpl04→rpl05 и принятие maintenance с аудитом; полный Kubernetes update/adoption существующего корпуса остаётся отдельной проверкой |
| Операторский CLI обновления | [Schema upgrade CLI](superpowers/reports/2026-11-02-replica-schema-upgrade-cli.json): отдельный SQL credential при scoped Settings и повтор после SQL commit/ошибки identity-файла; проверка SQL на изолированном PostgreSQL не заменяет Kubernetes rollout |
| Протокол копии rpl04 | [Predecessor protocol](superpowers/reports/2026-11-02-replica-predecessor-protocol.json): отдельный epoch0/S4-fence, неизменность старого формата, атомарный переход к rpl05 и SQL-восстановление; provider-copy/source-off recovery ещё не проверены |
| Настоящий старый источник | [Predecessor live source](superpowers/reports/2026-11-02-replica-predecessor-live-source.json):498 файлов исторического образа сверены с Git, выполнена настоящая rpl04 миграция, отдельные PVC/scoped SQL/S3,2/2/2 без рестартов, shared session и2 DOCX upload→publish→search→download SHA. Это подготовка источника, не завершённое обновление схемы или восстановление |
| Независимая копия старого источника | [Predecessor backup](superpowers/reports/2026-11-02-replica-predecessor-backup.json): настоящий PostgreSQL snapshot/native dump, Qdrant snapshot и10 blobs скопированы на отдельный ПК, все12 зашифрованных объектов проверены. Источник затем полностью отключён с сохранением PVC |
| Проверка экспортёра и source-off | [Export validation](superpowers/reports/2026-11-02-replica-predecessor-export-validation.json):109 Windows passes и157 Linux passes без skips,8 native PG cases; архив повторно аутентифицирован после физического отключения источника. Это проверка копии, не её восстановления |
| Импорт старого корпуса | [Predecessor provider import](superpowers/reports/2026-11-02-replica-predecessor-provider-import.json): отдельный этап source-off импорта в новые PostgreSQL/Qdrant/S3,2 документа/369 заданий/4 точки/10 SHA, приложения0 и прежний S4-token |
| Восстановление с обновлением схемы | [Predecessor schema recovery](superpowers/reports/2026-11-02-replica-predecessor-schema-recovery.json): фактический rpl04→rpl05 в новом target, новая identity/scoped SQL/S3, отзыв2 сессий, held epoch1→2 и явный release.9 таблиц/369 заданий/10 SHA/4 vectors проверены до старта;2/2/2 Ready без рестартов,2 прежних search/download SHA и2 новых документа,7 smoke checks. Не подтверждает in-place update старого namespace, compact/NULL-generation, остальные providers или полную R5 приёмку нового образа |
| Parser/frontend итоговых источников | [Artifact CI](superpowers/reports/2026-11-02-replica-current-artifact-ci.json):557 parser tests/0 skips, новая frontend production build/19 routes, правильный health rewrite/revision stamp, network-none/read-only/non-root. Оба дерева97+366 файлов совпадают с записанной текущей revision; это не полный backend CI |
| Учебное восстановление ключа | [Key recovery](superpowers/reports/2026-11-02-replica-backup-key-recovery.json): отдельный защищённый каталог на ПК, исходный ключ сохранён; новый процесс с отказом чтения исходного key directory расшифровал все12 объектов настоящей копии и отклонил неверный ключ. Это не OS sandbox, отдельный физический escrow или повтор полного provider restore |

Отдельные результаты04.11: [mixed NAS runtime](superpowers/reports/2026-11-04-replica-synology-mixed-runtime.json),
[независимая копия NAS](superpowers/reports/2026-11-04-replica-synology-independent-backup.json),
[восстановление NAS→S3](superpowers/reports/2026-11-04-replica-synology-source-s3-restore.json).
NAS оставался включён; недоступность исходника доказана остановкой его приложения,
PG/Q, отсутствием NFS mount и фактическим TCP-отказом из четырёх target-процессов.

[Отдельный NAS component outage](superpowers/reports/2026-11-04-replica-synology-bounded-outage.json)
проверил150с блокировки node→NAS2049 с естественным истечением kernel timed set,
native DROP и ожидающими read/write на136,6с. Тот же процесс завершил чтение182,9с
и запись183,1с с двумя совпавшими1MiB SHA. Первые ошибки harness сохранены;
подтверждённый прогон полностью закрыт, прежние2/2/2/runtime/PVC/девять таблиц
возвращены. Это компонентное доказательство; следующий application-прогон описан ниже.

[Полный application/worker outage](superpowers/reports/2026-11-04-replica-synology-application-outage.json)
проверил тот же150с node→NAS2049 block после подтверждения принятой задачи,
владельца attempt1 и активного model stub. Публикация ожидала восстановления,
старое поколение и концепты сохранились; liveness200, readiness timeout и ноль
рестартов. Завершение наблюдалось к194,2с: прежняя задача/attempt1, тот же candidate
стал active, problem=null. Все четыре HTTP-маршрута проверили сессию, оба SHA/поиск,
старый ZIP, idempotent retry и новый экспорт; четыре native-процесса —12 cold SHA,
scratch0 и две upload audit. Новый стенд выключен/held1/три PVC сохранены, nft и
forwards закрыты; прежние2/2/2/runtime/PVC/девять таблиц и все capacity loans
восстановлены. Это закрывает выбранный150с application case; межузловые,
физические и остальные profile gates остаются открыты, certificationnull.

[Межузловая репетиция VM103](superpowers/reports/2026-11-04-replica-crossnode-vm103.json)
проверила два логических kind-узла:256MiB RWX write/read/SHA с разными Node UID,
межузловую сеть/Service/DNS и frontend→storage denials. Первый отказ выявил
совместное размещение DNS и зависшую SQL-транзакцию погибшего TCP-клиента;
он сохранён как неуспешный автоподхват с последующим ручным восстановлением.
После исправления DNS spread и PostgreSQL TCP keepalive новая принятая Cygnus
автоматически завершилась на control-plane к210,36с, пока исходный worker
оставался выключен: attempt2/fence2, problem=null, без правок lease/slots или
ручного прекращения SQL backend. Узел вернулся через240с. Через четыре маршрута
прошли сессия/четыре SHA/поиск/три ZIP; четыре native-процесса проверили23 SHA,
scratch0/четыре audit/семь известных бизнес-задач. Это логическая межузловая
проверка на одной VM и одном ядре, certification=null; физическая HA и потеря
worker во время незавершённого внешнего provider call остаются отдельными gates.

## Условия окончательной приёмки

Общий runner обязан получить все обязательные результаты текущего профиля с
нулём failures и required skips. Изменение recipe, inputs или profile identity
требует нового соответствующего прогона. Версии, image digests, реальные узлы,
PVC/CSI, домены отказа и backup destination входят в доказательства профиля.

Одноузловой kind на одной VM не доказывает переживание потери физического узла
или хранилища. Stub-модель и fake embeddings не доказывают совместимость или
производительность реальных моделей. Production backup schedule остаётся OFF
по решению пользователя. Восстановление ключа и доставка alerts проверены на
согласованных локальных учебных входах; это не production escrow/monitoring.

Локальный [прогон уведомлений](superpowers/reports/2026-11-02-replica-local-backup-alerts.json)
дал четыре подтверждённые строки inbox: stale age86401 (учебные часы), ошибка
неполной зашифрованной копии, изменение ошибки последней попытки и recovery.
Повторы без изменения состояния не создавали событий.107 Windows tests passed,
один health symlink-permission skip. Последующий
[Linux cohort](superpowers/reports/2026-11-02-replica-evidence-alerts-linux.json)
прошёл295 notification/verifier/policy/runner cases без пропусков. FIFO и symlink
фактически проверены; все2395 входных файлов остались неизменными, реплики и
данные восстановлены. Полный backend CI кода5f5c43d подтверждён отдельным
отчётом. Последующие полные ccfcf9e и cf74f8a CI и единый проход финального ревью завершены; provider/profile приёмка остаётся открытой.
