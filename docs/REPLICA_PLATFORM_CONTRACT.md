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
| Разрешённый disposable namespace/context | Не предоставлен |
| PVC/CSI, диски и домены отказа | Не проверены |
| Разрешения на namespace/cluster components | Не проверены |
| PG/Qdrant/RWX/S3 реализации и digests | Не выбраны до проверки платформы |
| Независимое backup destination | Не предоставлено |

Новые runtime настройки пока отклоняются кодом сборки. Pure validator описывает
будущие сочетания и не является разрешением запуска. Helm распознаёт новые
значения mode; frontend-split поддерживает независимые frontend при одном
старом combined backend. Distributed backend mode по-прежнему отклоняется.
