# Входной контракт для проверки реплик

Статус 31.10.2026: подготовлен контракт; окружение заказчика не проверено.
Схема: `deploy/kubernetes/replicas/platform.schema.json`. Заполненный manifest
с адресами и Secret references хранится вне Git. Продукты и версии PG management,
RWX и S3 выбираются после проверки прав/дисков; отсутствие фактов не заменяется defaults.

Обязательные свидетельства: реальные узлы/домены отказа, StorageClass/access modes,
повторное подключение PVC, CPU/RAM/disk quotas, разрешения на операторы,
доступность закреплённых images и CA, независимое backup destination и владелец,
лимиты моделей. Для каждого self-managed продукта нужны версия, digest, лицензия,
install scope, требования дисков, failure model, backup/restore recipe и ответственный.

Отдельный manifest запуска acceptance содержит schema_version=1, run_id
(8–32 строчных латинских букв/цифр), profile=basic|resilient, явный context,
namespace=`replica-test-<run_id>`, database_url с БД `replica_test_<run_id>`,
qdrant_collection=`replica_test_<run_id>`, bucket_prefix=`replica_test/<run_id>/`.
Credentials этого manifest не выводятся в report. Наличие имени namespace не
доказывает изоляцию: перед будущими mutations проверяется живая метка
`okf-purpose=replica-test`, актуальные endpoint identities и права.

Без `--live` runner проверяет только синтаксическую изоляцию входов. Для
`--suite runtime --live` он сверяет живые namespace labels, установленные
идентичности SQL/storage/model, готовность шести реплик и состав Service,
проверяет общую сессию, CNI-изоляцию frontend от managed storage и два синтетических
DOCX через upload/worker/search/download с SHA-256. Тестовые документы сохраняются
до явной очистки выделенного namespace. Дополнительный `--idle-faults` выполняет
scale API/worker 2→1→2 и обычную замену простаивающих Pod без force delete, затем
повторяет документный smoke. Активные/неопределённые model reservations запрещают
этот idle probe; он не подтверждает drain или остановку внешнего запроса.
Невыполненные активные fault/recovery сценарии сохраняют pending, passed=false и
код 1. Отчёт содержит digest выполняемого runner и фактически запущенных образов.
Подробный запуск и ограничения: [deployment](KUBERNETES_DEPLOYMENT.md#replica-runtime-smoke).

## Необходимые входы окружения

| Вход | Состояние |
|---|---|
| Разрешённый disposable namespace/context | Учебный kind-okf-policy: replica-test-s3rpl20261031; namespace проверен по managed-by/run-id |
| PVC/CSI, диски и домены отказа | Учебный стенд: один node, local-path/RWO; RWX/CSI failover и независимые домены не подтверждены |
| Разрешения на namespace/cluster components | Не проверены |
| PG/Qdrant/RWX/S3 реализации и digests | Basic recipes: PG17, Qdrant1.19.0, RustFS1.0.4 закреплены; RWX/platform и resilient не выбраны/не квалифицированы |
| Независимое backup destination | Не предоставлено |

R5 foundation (01.11.2026): Helm формирует frontend/API/worker, но runtime требует
namespace-bound keyed identity с совпадающей установленной записью SQL и schema
rpl04_runtime_identity. Автоматической инициализации этой записи при старте нет.
O1 storage bootstrap, O2 maintenance и реальные multi-Pod испытания ещё обязательны;
пример basic держит application.enabled=false. Resilient остаётся запрещён.
Наличие шаблонов не подтверждает доступность платформенных хранилищ заказчика.

O1 basic evidence (01.11.2026): selected Qdrant/RustFS run in restricted non-root
Pods with separate local-path PVCs on the owned lab. S3 publication/cold/range/
delete and credential refusals, Qdrant schema/populated refusal, full PostgreSQL
schema migration and competing initialization/retry have passed. Cilium is present;
live policy/provider/TLS/restore and customer qualification remain separate gates.
See `docs/REPLICA_STORAGE_OPERATIONS.md` for exact pins and remaining requirements.
