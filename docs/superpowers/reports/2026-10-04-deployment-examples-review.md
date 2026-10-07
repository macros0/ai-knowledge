# Сопоставление примеров развёртывания с общим решением

Дата: 04.10.2026. Проверены предоставленные примеры deploy repository, backend
chart 1.0.8, frontend chart 1.3.12 и описания stdout logging. Архивы читались
без запуска их scripts, skills и pipeline. В этом отчёте нет реквизитов,
названий организаций, имён пользователей, реальных URL/registry/Vault paths
или копии окруженческих values.

## Статус доказательств

Пример показывает устройство другого приложения, а не обязательный контракт
платформы. Исходники шаблонов и документация прочитаны; chart render, install,
merged GitLab CI graph, реальная регистрация IdP и доставка логов по этим
архивам не выполнялись. Подтверждённый template не доказывает действующую
конфигурацию Pod. Заданные values не обязательно появляются в rendered env.

Совместимость нашего chart/compact Pod/Next proxy платформой пока не подтверждена
и не опровергнута. Таблица ниже описывает, почему чужие настройки не переносятся
автоматически; она не утверждает запрет нашей архитектуры. Обязательность
стандартных charts, direct API route и RollingUpdate уточняется через
[список открытых вопросов](../../KUBERNETES_PLATFORM_QUESTIONS.md).

## Наблюдения и решения

| Источник внутри примера | Наблюдение | Решение для общего проекта |
|---|---|---|
| Backend `templates/deployment.yaml` | RollingUpdate, maxSurge=1, replicas configurable | Не переносить в compact: backend writers ещё нельзя перекрывать; сохраняем Recreate, 1 Pod/1 process и orchestration lock |
| Backend `templates/configmap.yaml` | Фиксированный небольшой набор ключей, не весь env map | Проверять rendered Pod env для каждого нового OIDC/audit параметра; неизвестный ключ в values сам по себе не включает функцию |
| Backend `values.yaml` / `templates/service.yaml` | Внутренний порт другого runtime 3001 | Наш backend остаётся 8000 внутри контейнера; host 18000 относится только к local launcher |
| Backend `templates/deployment.yaml` | Readiness/liveness через доменный запрос `/api/catalog`; пример backport увеличивает failure budget примерно до 20 минут | Не переносить endpoint и timing workaround. Наши `/health/live` и `/health/ready` проверяют свой контракт; длительная генерация не должна подменять liveness |
| Оба `templates/deployment.yaml` | Нет pod/container securityContext и PVC contract нашего приложения | Использовать наш UID/capabilities/fsGroup/PVC contract; default примера не подтверждает Pod Security или сохранность данных |
| Frontend `templates/configmap.yaml` / deployment | Static Nginx с портом 80, `/healthz`, immutable asset cache | Наш Next standalone остаётся на 3000 с server proxy/cookies/diagnostics; чужой Nginx ConfigMap не является его конфигурацией |
| Frontend `templates/httproute.yaml` | `/api` и отдельный chat route направлены прямо в backend, `/` — frontend | Сохраняем frontend-only внешний маршрут. Прямой route обходит наш Next request context/cookie/stream contract и будущую подпись IP |
| Frontend `templates/listenerset.yaml` | ListenerSet, TLS reference и разрешённые namespaces | Возможная интеграция после actual API discovery, permission и TLS ownership; наличие шаблона не доказывает CRD в каждом кластере |
| Frontend `templates/trafficpolicy.yaml` | Buffer maxRequestSize, streamIdle, local token bucket, отдельный chat request timeout | Добавить явную проверку buffering и отличия request timeout от idle timeout, не копировать лимиты. Local Gateway rate limit не заменяет общую admission/concurrency приложения |
| Frontend environment values | В одном профиле одновременно legacy Ingress и Gateway path | Нельзя включать доверенный IP, если остался обход sanitizing Gateway. Проверить каждый публичный путь и закрыть обход до активации profile |
| Deploy `.gitlab-ci.yml` / README | Компонентные includes, refs, обновление image tags/values, последовательность сред и resource_group | Использовать общую immutable release identity и verified operator archive; реальные includes и manual/protected semantics подтвердить merged CI Lint. Не копировать default branch, PAT automation и names jobs |
| Environment values / secret update flow | Чувствительная конфигурация и настройки TLS находятся в окруженческом примере | Не переносить raw values/accounts и отключение TLS verification. Secrets и CA передаются защищённо; факт значения в values не равен активному Secret |
| Stdout integration example | JSON output приложения, downstream collector только предполагается | Утвердить наш schema/event ID и отдельно проверить collector/parser/index/RBAC/retention; прямой OpenSearch SDK не требуется |

Backend chart schema описывает liveness overrides; frontend schema в архиве
не найдена. Это свойство файлов примера, не повод убрать наши строгие schema и
negative assertions. Ресурсы, ML model defaults и настройки модулей другого
приложения не являются sizing/profile нашего runtime. Пример не содержит
доказательств userinfo-only groups или зарегистрированного public OIDC flow.

## Дополнения к плану

1. Gate каждого нового параметра: Settings → ConfigMap/Secret → rendered
   container env → поведение приложения. Без dumps секретов в job output.
2. Проверка полного HTTP пути: bypass routes, raw client headers, реальный
   client IP, buffering upload, request/idle timeout, SSE и cancellation.
3. Наличие ListenerSet/TrafficPolicy и семантика policy устанавливаются
   read-only API discovery и disposable checks в отдельной окруженческой копии.
4. Promotion не заменяется обновлением tag в values; требуются сохранение
   digests, совместимость chart/config/operator tools и обязательные gates.
5. Structured stdout означает готовность приложения отдать событие;
   downstream delivery подтверждается поиском UUID и сверкой с audit DB.

Связанные решения: [OIDC](../specs/2026-10-04-oidc-compatibility-design.md),
[аудит](../specs/2026-10-04-audit-delivery-design.md),
[общий план](../plans/2026-10-03-kubernetes-rollout.md).
