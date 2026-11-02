# Перенос существующего корпуса в BlobStore

Операция `migrate_artifact_storage.py` переносит принятые бинарники, поколения,
staging, кэши источников, оригиналы незавершённых обзоров вложений и готовые
архивы в отдельное local/RWX/S3-хранилище. Исходные файлы не удаляются.
Переключение ссылок и выбранного хранилища выполняется одной SQL-транзакцией
только после полной проверки. Идентификаторы документов, опубликованных
поколений, концептов и точек Qdrant сохраняются; LLM, embedding и reindex не вызываются.

Это offline preview для `ENVIRONMENT=development`, `APP_ROLE=combined`.
Production mutation и distributed runtime пока запрещены. S3 проверен через
protocol double, RWX — через файловый адаптер; это не приёмка реального CSI/S3
заказчика. Полный backup/restore drill и runtime R1–R5/O1–O3 остаются отдельными
этапами [плана](superpowers/plans/2026-10-31-kubernetes-replicas.md).

## Подготовка

1. Применить Alembic до текущего head (`rpl05_maintenance_epoch`) обычной maintenance-процедурой.
   `create_all` не добавляет колонки в существующие таблицы. Миграционный CLI
   сам схему не обновляет и рабочие процессы не останавливает.
2. Остановить **все** backend/worker/GC, скрипты и внешние writers; дождаться
   завершения readers. Заблокировать запуск старых контейнеров и доступ сторонних
   writers к исходным файлам. После начала `copy` SQL maintenance fence отклоняет
   обычные сессии приложения, но не заменяет остановку процессов/права ОС.
3. Выделить отдельный пустой target с новым `store_id` и эксклюзивным root/prefix.
   Источник и target не могут пересекаться. Использовать прежние provider
   credentials источника только для чтения; защищать архив источника правами ОС/IAM.
4. Зафиксировать образ/версию CLI, сохранить исходный runtime env и private transfer-каталог на устойчивом диске
   **вне** `DATA_DIR`, diagnostics, source/target roots. Содержимое плана, payloads,
   manifests и backup evidence приватно: там есть имена/метаданные корпуса.
   Предоставить доступ только оператору, исключить каталог из git, логов и GC.

Запускать из `backend/` с выбранным Python окружением. Во всех командах передавать
тот же явный исходный env; пример для Linux-контейнера:

```sh
python scripts/migrate_artifact_storage.py \
  --runtime-env-file /run/private/source.env --show-installation
```

Команда возвращает только `installation_id`. Source/target manifests — JSON v1,
без credentials; оба содержат этот ID. Например, source local:

```json
{"version":1,"installation_id":"<32 hex>","store":{"backend":"local","store_id":"source","root":"/legacy/artifacts"}}
```

Target RWX (volume уже смонтирован, адаптер не создаёт инфраструктуру):

```json
{"version":1,"installation_id":"<тот же ID>","store":{"backend":"rwx","store_id":"shared-v1","root":"/shared/artifacts"}}
```

Target S3:

```json
{"version":1,"installation_id":"<тот же ID>","store":{"backend":"s3","store_id":"s3-v1","endpoint":"https://s3.example.org","bucket":"knowledge","prefix":"installation-v1/","region":"us-east-1","addressing_style":"path","allow_http":false}}
```

Допустим также `ca_bundle` — путь к смонтированному CA. Source descriptor должен
соответствовать исходным settings. Для S3 используйте те же endpoint/prefix/region/
style/TLS-параметры. Credentials наследуются из runtime settings либо передаются
через `--private-config /run/private/providers.json`, JSON map по `store_id`:

```json
{"s3-v1":{"artifact_s3_access_key":"<secret>","artifact_s3_secret_key":"<secret>"}}
```

Допустим `artifact_s3_session_token`. Credentials не включаются в публичные
descriptors, plan, receipts или stdout; не передавать их аргументами командной строки.

## Plan и независимый backup

```sh
python scripts/migrate_artifact_storage.py \
  --runtime-env-file /run/private/source.env \
  --source-manifest /transfer/source.json --target-manifest /transfer/target.json \
  --plan /transfer/run-1/plan.json
```

Фаза по умолчанию — `plan`: читает source/SQL и записывает приватный transfer
inventory/payloads; не меняет SQL/provider. В stdout — counts, migration ID,
`source_revision` и `plan_hash`. План фиксирует SQL revision, полный SHA256/size
исходных файлов/объектов и inventory владельцев. Некорректные protocol pointers,
подменённые bindings, checksum/identity и отсутствующие принятые файлы вызывают отказ.
Старый незавершённый staging нужно исправить/штатно удалить до нового плана,
а не обходить проверку или заново генерировать принятые данные.

При остановленных writers сделать отдельный согласованный backup SQL и файлов/
provider, сохранив identity/GC marker. Backup должен лежать вне runtime/target
roots. Составить evidence JSON:

```json
{
  "version":1,
  "installation_id":"<ID>",
  "source_revision":"<из plan>",
  "files":[
    {"role":"database","path":"/backup/sql.dump","sha256":"<SHA256>","size":123},
    {"role":"artifacts","path":"/backup/corpus.tar","sha256":"<SHA256>","size":456}
  ]
}
```

Evidence — подтверждение оператора о независимом backup выбранной revision.
CLI проверяет наличие, размер и полные хэши backup-файлов, обе роли и привязку к
плану. Он **не проверяет**, что dump/archive семантически полны и восстанавливаемы.
Для этого нужен отдельный restore drill. Не подставлять произвольные файлы вместо
backup; evidence не является сертификатом восстановления.

## Copy → verify → cutover

Повторять исходные аргументы, добавляя фазу и подтверждения:

```sh
python scripts/migrate_artifact_storage.py \
  --runtime-env-file /run/private/source.env \
  --source-manifest /transfer/source.json --target-manifest /transfer/target.json \
  --plan /transfer/run-1/plan.json \
  --phase copy --apply --writers-stopped --backup-manifest /backup/evidence.json
```

Затем та же команда с `--phase verify`, после успешной проверки — `--phase cutover`.
Любая mutation требует `--apply`, stopped-writer confirmation и тот же backup.
`verify` тоже mutation: сохраняет проверенный receipt. `cutover` повторно проверяет
все source/target bytes, SQL revision, receipts/pins и GC ownership marker.
Неполная copy, потеря marker, изменение SQL/исходного файла или target digest
не позволяют переключиться. SHA256 считается по полным байтам, не по S3 ETag.

Copy использует стабильные object IDs и immutable PUT. После каждого объекта
metadata, migration pin и receipt фиксируются вместе. Сбой PUT/SQL может оставить
невыбранный объект в target; повторная команда с **теми же** plan/payloads/env
проверяет/переиспользует его, не дублируя bindings. Не удалять transfer-каталог и
не создавать новый migration ID для обхода активного maintenance fence.
После сбоя операции приложение остаётся остановленным: исправить причину и
повторить соответствующую фазу. Не очищать control/receipt вручную.

Cutover атомарно заменяет bindings/heads/checkpoints и durable protocol markers,
снимает maintenance fence и сохраняет target descriptor в БД. Retry после потери
ACK возвращает уже совершённое переключение; старый receipt не откатит более
новую миграцию. Legacy документы без active generation сохраняют эту identity:
для них создаётся отдельный immutable snapshot, а не новое опубликованное поколение.
Принятые queued exports получают durable source pins; завершённые/failed jobs
сохраняют исторические параметры, не превращаясь в живые source refs.

## Запуск на новом хранилище и восстановление

Перед запуском установить target provider settings **и новые пустые пути**
`DATA_DIR`, diagnostics и явного scratch. Старые source roots навсегда отмечаются
read-only в control history. Старый runtime/provider descriptor и попытка писать
кэши внутрь retired roots отклоняются при startup/provider construction/SQL write.
Перенос между ОС с другими абсолютными путями требует отдельной проверки mappings;
CLI не угадывает соответствие mount paths.

Проверить download исходного документа/вложений, поиск существующих концептов,
OKF-export, resume принятого staging и очередь exports на холодном worker.
Включение preview-флага не отменяет production/distributed gates. Source архив
сохраняется для recovery; автоматической очистки source в этой операции нет.

После новых записей rollback — согласованное восстановление backup SQL + source
provider/marker + прежней runtime configuration в отдельный recovery target.
Обратное переключение env или schema downgrade не восстанавливает согласованное
состояние. Alembic downgrade отказывается при receipts/selection/portable pointers.
`reindex.py` проверяет runtime selection **до** обращения к Qdrant и запускается
только отдельной осознанной recovery-операцией.

## Kubernetes maintenance

`scripts/kubernetes/lifecycle.py migrate-artifacts` принимает
`--source-manifest`, `--target-manifest`, `--migration-plan`, `--migration-phase`,
`--backup-manifest`, необязательный `--private-config` и `--apply` вместе с обычными
параметрами manager. Все migration paths — абсолютные **container paths**.
Volume mounts для source, target, private env/credentials, backup и устойчивого
transfer-каталога нужно заранее предоставить operation Pod через deployment
configuration. Команда не загружает локальные файлы и не создаёт bucket/RWX volume.

Manager захватывает maintenance lock, останавливает backend/frontend и запускает
явный CLI Job. Успех освобождает operation lock, но не поднимает старую runtime
configuration. Ошибка/timeout оставляет lock и workloads остановленными для
проверки Job. После cutover применить согласованную target configuration и
выполнить обычную процедуру старта/приёмки. Production CLI gate остаётся действующим.
