# Матрица приёмки реплик и хранилищ

Состояние доказательств на03.11.2026. План реализации:
[Kubernetes replicas and storage](superpowers/plans/2026-10-31-kubernetes-replicas.md).
Зелёный отдельный тест или старый отказной прогон не заменяет приёмку всей
текущей версии и выбранного профиля. Ни один профиль пока не объявлен resilient.

| Область | Подтверждённый результат | Незакрытая проверка |
|---|---|---|
| Основание P0/P1 | Контракты профилей, инвентарь writers, явная namespace/installation identity и изоляция тестовых входов реализованы | Входы и полномочия инфраструктуры заказчика |
| Frontend F1/F2 | Разделение frontend/backend, общая сессия и доступ к API проверены на двух frontend; прямой доступ frontend к storage запрещён CNI | Customer Gateway/SSO и реальные IdP endpoints |
| Файлы S1–S4 | Local/RWX/S3 adapters, перенос потребителей, GC и возобновляемая offline-миграция с проверкой целостности реализованы; [runbook](ARTIFACT_STORAGE_MIGRATION.md) | Квалификация конкретного RWX/CSI/provider; миграция рабочего корпуса выполняется только после проверенного backup |
| Runtime R1–R4 | PostgreSQL ownership/fencing, роли API/worker и координация реализованы; cancel/delete, worker-loss и partition прошли в общем текущем запуске | Дополнительные внешние providers и полная release matrix |
| Runtime R5 | Общий текущий22-stage run прошёл без failures/required skips;80 прежних документов сохранены, финальные2 frontend +2 API +2 workers проверены | Другие providers, реальные модели/IdP и физические домены отказа требуют собственных gates |
| Storage O1 | Managed basic PG/Qdrant/S3, TLS, scoped credentials, смена CA, CNI и отдельный S3 ENOSPC contract проверены | RWX, external/mixed end-to-end; заполнение storage через приложение; независимые failure domains |
| Recovery O2 | Независимая20-document копия восстановлена при выключенном источнике; отдельно старая rpl04 копия прошла фактический rpl05 replacement recovery с новой identity, отзывом сессий и функциональной проверкой2/2/2. Проверены cold-check, журнал попыток, учебное восстановление ключа и доставка локальных alerts | Оставшиеся adoption/update и provider recipes; production key custody/monitoring не квалифицированы |
| Release O3 | Обязательные project checks и профильные Windows/Linux cohorts;557 parser passes и standalone frontend build; полный Linux backend CI кода5f5c43d:6390 passed/220 optional skips/87,62% coverage; policy validator, проверка подписанных файлов и read-only режим runner реализованы. [Семь профилей](../deploy/kubernetes/replicas/support-matrix.json) перечислены с открытыми gates, certified-профилей нет | Повтор CI при последующих изменениях кода, реальные доверенные evidence producers, общая fault/profile matrix, завершение runbooks и свежее ревью всей ветки |

## Доказательства учебного профиля

| Испытание | Свидетельство и граница результата |
|---|---|
| Legacy diagnostic journal | [S4 journal/input conversion](superpowers/reports/2026-11-03-replica-legacy-diagnostics.json): перенос истории с исходным сроком хранения, замороженный запрос, cold/source-off worker, откат и повторный перенос проверены на синтетических данных и native PostgreSQL. Фактическая source-off Kubernetes/provider/backup приёмка этой процедуры ещё открыта |
| Очереди compact: atomic handoff | [SQL/CLI handoff](superpowers/reports/2026-11-03-replica-compact-handoff.json):176 Windows и24 native PostgreSQL passes; сохранение исходных заданий/одобрений, новых queued WorkItems и frozen inputs, общий откат и повтор после сбоя. Последующее покрытие legacy diagnostic journal приведено отдельно выше; фактическая source-off Kubernetes приёмка ещё открыта |
| Compact adoption SQL/CLI | [SQL/CLI evidence](superpowers/reports/2026-11-03-replica-compact-adoption.json):153 Windows passes/2 platform skips и18 native PostgreSQL passes; сохранение непустого корпуса/NULL generations, held epoch1 и возобновляемая публикация identity проверены на отдельной PostgreSQL17.11. Перенос старых очередей и фактическая source-off Kubernetes приёмка остаются открытыми |
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
| Полный CI текущего backend и runtime-образ | [Linux CI/image](superpowers/reports/2026-11-03-replica-full-linux-backend-ci.json):5f5c43d,6390 passes/220 explicit skips/0 failures/errors,6610 exact case IDs,87,62% coverage при85%; новый образ с572 проверенными файлами.203 PostgreSQL opt-in,12 platform и5 real ENOSPC opt-in требуют отдельных сред; образ ещё не развёрнут для compact-приёмки |
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
отчётом; provider/profile приёмка и финальное ревью остаются открытыми.
