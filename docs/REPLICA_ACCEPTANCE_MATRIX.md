# Матрица приёмки реплик и хранилищ

Состояние доказательств на02.11.2026. План реализации:
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
| Recovery O2 | Независимая зашифрованная копия на ПК и более раннее восстановление3 документов при недоступном источнике проверены; журнал попыток и cold-check реализованы | Restore последней20-document копии; schema-changing update/adoption, key recovery, доставка alerts и остальные provider recipes |
| Release O3 | Обязательные проверки проекта и профильные Windows/Linux cohorts проходят для соответствующих зафиксированных изменений | Полный CI итоговой версии, общая fault/profile matrix, завершение runbooks и свежее ревью всей ветки |

## Доказательства учебного профиля

| Испытание | Свидетельство и граница результата |
|---|---|
| Общий runtime | [Common runtime](superpowers/reports/2026-11-02-replica-common-runtime.json): все22 этапа одного текущего запуска, maintenance15→16→17,80 исходных документов сохранены; только учебный managed-S3/stub/fake profile |
| Source-off backup/restore | [Managed-S3 recovery](superpowers/reports/2026-11-01-replica-managed-s3-recovery.json): прежний3-document архив восстановлен в новый target; это не restore последней копии |
| Последняя независимая копия | [Before-scoped-faults backup](superpowers/reports/2026-11-02-replica-before-scoped-faults-backup.json):20 документов/100 blobs; проверка архива не равна восстановлению |
| Scoped permissions | [Credentials](superpowers/reports/2026-11-01-replica-scoped-credentials.json): фактические SQL/S3 положительные и отрицательные проверки |
| Смена CA | [Coordinated CA rotation](superpowers/reports/2026-11-01-replica-coordinated-ca-rotation.json): согласованное обновление работающей учебной установки |
| Обновление image | [Scoped image upgrade](superpowers/reports/2026-11-02-replica-scoped-image-upgrade.json): прежние документы и сессия сохранены; схема не менялась |
| Заполнение S3 | [Bounded S3 ENOSPC](superpowers/reports/2026-11-02-replica-bounded-s3-enospc.json): реальный ENOSPC в отдельном128Mi tmpfs, сохранность контрольного объекта и восстановление записи; весь путь приложения/PVC этим не проверен |
| Полный metadata snapshot | [Bounded corpus snapshot](superpowers/reports/2026-11-02-replica-bounded-corpus-snapshot.json): все10766 work rows учтены, тот же held operation безопасно завершён; общий родительский прогон остался неуспешным |
| Лимит LLM | [Capacity observation](superpowers/reports/2026-11-02-replica-capacity-observation.json): оба API отказали при занятом слоте, нормальный RPC handoff допустим, пик1 и целостность документов подтверждены; отдельная диагностика не закрывает общий run |
| Небольшая нагрузка | [Scoped load diagnostic](superpowers/reports/2026-11-02-replica-scoped-load-diagnostic.json): одна небольшая synthetic trial с1/2 workers; не оценка production throughput или линейного ускорения |
| CI и свободный диск | [Lab CI capacity recovery](superpowers/reports/2026-11-02-replica-lab-ci-capacity-recovery.json): неуспешный ENOSPC CI сохранён; raw temporary artifacts проверены и скопированы на ПК до удаления только собственных временных деревьев |

## Условия окончательной приёмки

Общий runner обязан получить все обязательные результаты текущего профиля с
нулём failures и required skips. Изменение recipe, inputs или profile identity
требует нового соответствующего прогона. Версии, image digests, реальные узлы,
PVC/CSI, домены отказа и backup destination входят в доказательства профиля.

Одноузловой kind на одной VM не доказывает переживание потери физического узла
или хранилища. Stub-модель и fake embeddings не доказывают совместимость или
производительность реальных моделей. Production backup schedule остаётся OFF
по решению пользователя; проверка доставки alerts и восстановления ключа
потребует соответствующих реальных тестовых входов.
