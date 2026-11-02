# Приёмка профилей Kubernetes replicas

Статус на02.11.2026: общая приёмка **не завершена**, certified-профилей нет.
[Матрица JSON](../deploy/kubernetes/replicas/support-matrix.json) хранит семь
предусмотренных вариантов: compact/local, frontend split, managed/RWX,
managed/S3, all-external, mixed и resilient. Поле `tested` означает наличие
ограниченных фактических испытаний, а не разрешение production-развёртывания.
Реализация адаптера или успешный Helm render не заменяет provider acceptance.

## Привязка доказательств

Для проверенного учебного managed/S3 варианта сохранены точные topology,
ownership/recipe/version/image каждого storage-компонента, фактические runtime
image IDs, common identity и model/auth режимы. `binding_sha256` вычислен как
SHA-256 UTF-8 JSON объекта `binding` с `sort_keys=True,separators=(',',':')`.
Изменение любого из этих входов требует новой приёмки соответствующего профиля.
Матрица пока является инвентарём доказательств: автоматический certification
validator O3 и его отрицательные проверки ещё не завершены. Во всех записях
`certification=null`, `qualification_complete=false`, release остаётся incomplete.

Учебная topology — один kind node на одной VM, local-path/RWO. Она допускает
проверку взаимодействия реплик и восстановления в новые PVC, но не подтверждает
переживание потери физического хоста/диска. Simulation auth, stub LLM и fake
embeddings не подтверждают реальные Gateway/IdP/model providers заказчика.

## Подтверждённые проверки текущих источников

- [Schema-changing recovery](superpowers/reports/2026-11-02-replica-predecessor-schema-recovery.json):
  реальная rpl04 копия при выключенном источнике, новая schema/identity/scoped
  credentials, отзыв старых сессий, явный release,2/2/2 Ready и семь smoke checks.
  Два прежних документа проходят поиск/download SHA после двух новых загрузок.
- [Parser/frontend CI](superpowers/reports/2026-11-02-replica-current-artifact-ci.json):
  557/557 doc-parser tests без пропусков и новая standalone production-сборка
  frontend с проверенными19 routes, `/health` и revision stamp. Все97 parser и366
  frontend файлов сверены; Git подтверждает неизменность обоих деревьев между
  frozen snapshot и указанной проверяемой revision. Контейнеры network-none,
  read-only, non-root; рабочие каталоги/сервисы не используются.
- [Backend baseline](superpowers/reports/2026-11-02-replica-baseline-backend-ci.json):
  6093 случая старой revision с точным совпадением collected/executed IDs обоих
  shards. Это исторический результат, полный backend CI итоговой версии открыт.

После parser/frontend CI временно остановленные приложения учебного target
возвращены к2/2/2; все9 canonical-table hashes, PVC и runtime identity сохранены.
Сырые CI-логи и build artifacts остаются на учебной VM; публичные отчёты содержат
ограниченные metadata/counts/SHA. Новая frontend-сборка этим тестом не развёртывается.

## Команды и незакрытые gates

Обязательные локальные проверки из корня: `node scripts/check-project.mjs`.
Полный backend suite: из `backend/` — `python -m pytest tests/ -q`; doc-parser —
та же команда из `doc-parser/`, с отдельным `--basetemp` для каждого параллельного
запуска. Production frontend build выполняется из изолированного `frontend/`
прямым `node node_modules/next/dist/bin/next build`. Разрешённый `npm audit`
требует доступного registry; ошибка сети не считается успешной проверкой.

`backend/test_scripts/run_replica_acceptance.py --suite all --manifest PATH`
без live inputs проверяет только синтаксическую изоляцию и возвращает failure
при pending gates. Для реальных runtime действий требуются явные `--live`,
разрешённый namespace/context и protected manifest; дополнительные fault stages
имеют собственные operation journals и apply/resume параметры. Все required
checks должны относиться к одной принятой конфигурации. Отдельные успешные
отчёты разных image/identity нельзя объединять в один зелёный полный прогон.

Открыты фактические RWX/external/mixed проверки, application publication при
заполнении storage, остальные adoption/recovery recipes, восстановление ключа,
доставка backup error/age alerts, полный итоговый backend CI, certification
validator и свежее ревью всей ветки. Для resilient дополнительно обязательны
независимые физические домены, выбранные storage recipes и реальные испытания
потери узла/хранилища. Production backup schedule остаётся выключенным по
решению пользователя; его включение не является автоматическим итогом CI.

Порядок восстановления и ограничения:
[recovery runbook](KUBERNETES_REPLICAS_RECOVERY.md),
[операции с хранилищами](REPLICA_STORAGE_OPERATIONS.md),
[сводная матрица](REPLICA_ACCEPTANCE_MATRIX.md).
