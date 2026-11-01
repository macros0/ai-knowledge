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

`backend/test_scripts/run_replica_acceptance.py --suite contracts --manifest PATH`
сейчас проверяет только синтаксическую изоляцию входов. Live probes ещё не
реализованы: обязательные проверки возвращают pending, passed=false и код 1.
Ни запись на диски, ни установка/изменение компонентов этим инструментом сейчас
не выполняются. Это намеренно незавершённый gate P0, а не успешная приёмка стенда.

## Необходимые входы окружения

| Вход | Состояние |
|---|---|
| Разрешённый disposable namespace/context | Учебный kind-okf-policy: replica-test-s3rpl20261031; namespace проверен по managed-by/run-id |
| PVC/CSI, диски и домены отказа | Учебный стенд: один node, local-path/RWO; RWX/CSI failover и независимые домены не подтверждены |
| Разрешения на namespace/cluster components | Не проверены |
| PG/Qdrant/RWX/S3 реализации и digests | Не выбраны до проверки платформы |
| Независимое backup destination | Не предоставлено |

R5 foundation (01.11.2026): Helm формирует frontend/API/worker, но runtime требует
namespace-bound keyed identity с совпадающей установленной записью SQL и schema
rpl04_runtime_identity. Автоматической инициализации этой записи при старте нет.
O1 storage bootstrap, O2 maintenance и реальные multi-Pod испытания ещё обязательны;
пример basic держит application.enabled=false. Resilient остаётся запрещён.
Наличие шаблонов не подтверждает доступность платформенных хранилищ заказчика.
