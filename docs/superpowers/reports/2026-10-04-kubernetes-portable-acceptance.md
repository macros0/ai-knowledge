# Локальная проверка Kubernetes поставки

Дата проверки: 04.10.2026. Текущий код ветки `codex/kubernetes-rollout`
прошёл сборку и полный минимальный Kubernetes-сценарий: установка, работа
с синтетическими документами, перезапуск, повторное применение поставки
с backup, восстановление в новый namespace и сохранение PVC при uninstall.
Продуктовый код и chart в рамках этого прогона не изменялись.

Это проверка общего решения на локальном Docker. Совместимость с конкретной
платформой остаётся неподтверждённой до ответов на
[открытые вопросы](../../KUBERNETES_PLATFORM_QUESTIONS.md) и окруженческой
приёмки. Различия с примерными chart сами по себе не означают несовместимость.

## Проверяемая поставка

Ветка содержит незакоммиченные изменения поверх `f551f5f`.
`OKF_BUILD_REVISION` образов backend/frontend указывает на этот базовый commit;
для идентификации фактически проверенного кода дополнительно сохранены SHA256
снимков. Этот прогон не является проверкой только чистого commit `f551f5f`.

Docker build contexts собраны из файлов приложения без локальных env-файлов,
credentials, backups, `node_modules` и домашних конфигураций. Снимок содержит
текущие изменения model budget и модели-заглушки.

| Объект | SHA256 |
|---|---|
| Снимок build contexts, 580 файлов | `df0a4b2815b23de3e0de25471279a6c0528d1f653ec0fc3c276db8d0757f542a` |
| Chart и operator scripts, 20 файлов | `d448a8a6fcac68b1713bb2d53a91f274ea544b7b245a702cdebf830f2839e5c2` |
| Локальный backend image ID | `61ab71268f3a68fca17a47e028db6e01289a053d0fb01cd4aa2210fe816e6cd8` |
| Локальный frontend image ID | `18d73a1cef07e69d3459742500f8706bf56418e17573de56c364cd056d8d54e8` |
| Локальный model stub image ID | `3699be70a5b0d823cab19a456425267c8469ce01011a3e43139ac27f3d1a68a3` |

Image IDs относятся к локальному Docker store; публикация и проверка
registry release digests в этом прогоне не выполнялись.

## Стенд и выполнение

Docker Engine 29.4.2, 16 CPU, около 15,5 ГиБ доступной Docker памяти;
kind 0.33.0, один node Kubernetes 1.37.0, kubectl 1.37.0, Helm 4.3.0.
Бинарники kind и kubectl сверены с официальными контрольными суммами.
Кластер использовал отдельный kubeconfig. Домашняя VM и другие среды
не изменялись.

Профиль `DEPLOYMENT_TIER=ci`, `MODEL_MODE=stub`, auth disabled создан
существующим `lab.py` только для изолированной синтетической проверки.
PostgreSQL 17 и Qdrant 1.19.0 запущены по digest из chart; четыре тома
в каждом namespace — новые RWO PVC по 2 ГиБ с local-path storage.

На Windows этапы `scripts/kubernetes/tests/acceptance.sh` воспроизведены
через временный PowerShell runner с теми же Python/Helm/kubectl командами.
Все 12 этапов завершились с exit code 0; отдельно проверено сохранение четырёх
PVC после удаления восстановленного release. Удалён только временный
kind-кластер после сохранения результатов. Синтетические backups находятся
вне его томов и сохранены локально; они не добавлены в Git.

## Результаты

| Проверка | Результат и границы |
|---|---|
| Сборка | Backend, standalone Next.js frontend и stub собраны текущими Dockerfile |
| Установка | Storage, миграции, compact Pod и readiness прошли |
| Корпус | Оригинал и вложение доступны; BM25 находит fixture |
| DOCX | Upload → stub generation → index → source inventory прошли; `problem` отсутствует |
| Frontend | `/health` rewrite и NDJSON chat proxy с upstream stub SSE прошли |
| Перезапуск | Новый Pod достиг readiness с теми же PVC |
| Повторное применение | Lifecycle остановил приложение, создал проверенный backup, выполнил миграцию и запустил приложение |
| Restore | Новый namespace и PVC; импорт PostgreSQL/файлов/Qdrant; совпадение totals и строгая проверка содержимого |
| После restore | Повторные BM25, DOCX upload/generation/index/sources и чат через frontend прошли |
| Retention | Helm uninstall оставил все четыре внешних PVC до удаления временного кластера |

Оба backups содержали 2 документа, 2 чанка, 2 концепта и 4 точки Qdrant.
Перед очисткой кластера повторно проверены `complete` и контрольные суммы
обоих backup manifests. После восстановления приложение запустилось только
после сравнения totals и проверки целостности; maintenance lock исходного
release отсутствовал.

| Этап | Полное время команды |
|---|---|
| Установка | 62,54 с |
| Исходный functional smoke | 15,94 с |
| Ожидание readiness после restart | 12,61 с |
| Повторное применение с backup | 74,38 с |
| Отдельный backup с остановкой и запуском | 63,21 с |
| Restore | 86,24 с |
| Functional smoke восстановленной копии | 16,91 с |

Это время полного lifecycle, включая ожидание Pod/Jobs, а не измерение
продолжительности недоступности или промышленного RTO. В отдельных замерах
контейнер kind потреблял примерно 1–1,6 ГиБ памяти; пиковое потребление
не измерялось, sizing для реального корпуса из этих данных не выводится.

## Контрактные проверки и замечания

Выполнен актуальный набор из 12 файлов тестов job `compact` в
`.github/workflows/kubernetes.yml`: **174 passed, 0 skipped**, 158,75 с.
Одна существующая Starlette deprecation warning. Сюда входят chart/lifecycle,
профили, readiness, synthetic chat modes/token budget, delivery/toolbox и
home/network helper contracts. Сам удалённый GitHub workflow не запускался.

При попытке импортировать вместе локальные app и storage images kind сообщил
об отсутствующем content digest: кеш Docker содержал не все платформы storage
manifest. Отдельный импорт app images прошёл; Kubernetes получил закреплённые
storage images из registry. Chart, storage digests и проверочные условия
не менялись. Это локальное ограничение загрузки кеша, а не доказательство
пригодности offline-поставки; для неё нужен отдельный drill.

`npm audit --package-lock-only --json` завершился с exit code 1 и сообщил
5 high: `braces`, `micromatch`, `fast-glob`, `@next/eslint-plugin-next`,
`eslint-config-next`. Замечания связаны с цепочкой зависимостей линтинга.
Условия эксплуатации и production reachability не оценивались. Автоматическое
обновление, подавление и повышение порогов не выполнялись; dependency gate
остаётся открытым, успешная сборка его не закрывает.

## Что этот прогон не подтверждает

- Переход между разными версиями приложения/схемы: повторно применялась одна
  и та же сборка. Interruption, failed migration и rollback здесь не вводились.
- SSO/TLS/Gateway, реальный IdP, enforcing NetworkPolicy, CSI/admission и
  полную браузерную приёмку. Предыдущие домашние результаты описаны в
  [отдельном отчёте](2026-10-03-kubernetes-home-acceptance.md).
- Реальные LLM/embeddings, качество RAG, PROD token counter, dense retrieval
  и модельные лимиты; заглушка работает только с синтетическими fixtures.
- Приёмку всех DOCX/XLSX/PDF/MSG на рабочем корпусе, размер backup,
  согласованные RPO/RTO, потерю VM/узла, реплики и HA.
- Registry promotion, публикацию toolbox, platform CI, обязательные внешние
  gates, новые OIDC/audit контракты и полный regression старых способов запуска.

Следующие шаги общего решения: закрыть dependency gate отдельной проверяемой
правкой, утвердить применимые OIDC/audit контракты по открытым вопросам,
подготовить их подробные планы. Окруженческая приёмка ведётся отдельно;
репликация frontend/API/workers остаётся последующими этапами
[основного плана](../plans/2026-10-03-kubernetes-rollout.md).
