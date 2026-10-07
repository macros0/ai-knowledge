# Конфигурационная приёмка Kubernetes — 04.10.2026

## Результат и границы

Задачи 0–6 [плана конфигурации](../plans/2026-10-04-kubernetes-configuration.md)
реализованы в `codex/kubernetes-rollout`. Synthetic сценарии завершены, итоговая
регрессия — **316 PASS, 0 skips**, 266.85 s. Имеется одно существующее предупреждение
Starlette/httpx. Это локальная приёмка незакоммиченного снимка поверх
`f551f5f1457de1a0c073c3df09f9929375c14802`, а не промышленный release.
Коммит, merge, публикация и целевые DEV/TEST/PROD операции не выполнялись.

Прогон выполнялся последовательно на отдельном kind в Docker Desktop/Linux:
16 CPU, около 15.5 GiB RAM. Домашняя Ubuntu VM проверена: 4 vCPU, фактически
6 GiB RAM, около 50 GiB disk; существующие стеки сохранены. Из-за её текущей
нагрузки выбран предусмотренный планом отдельный стенд. Проверены autostart VM,
Docker и restart policy действующего kind; перезагрузка и отключение сервисов
не выполнялись. Отключение электричества как причина предыдущего crash не доказано.

## Версии и неизменность артефактов

Helm 4.3.0 (`bec5b06`), Kubernetes/kubectl 1.37.0, kind 0.33.0,
Gateway API 1.6.2, kgateway 2.4.5, PostgreSQL 17, Qdrant 1.19.0.
Keycloak 26.8.0 закреплён образом
`quay.io/keycloak/keycloak@sha256:b0f60d489d51c5d113390bdf5461d4c06e6051be026c05549f2e1e10ec352bcc`.
SHA256 установленной OpenAPI schema TrafficPolicy:
`38cedaff807c079057b85cd5cd767cfaf5a9b6cb1d4052c872f5180617f6eba9`.

| Образ | Проверенный локальный Docker configuration ID, SHA256 |
|---|---|
| Backend | `61ab71268f3a68fca17a47e028db6e01289a053d0fb01cd4aa2210fe816e6cd8` |
| Frontend | `18d73a1cef07e69d3459742500f8706bf56418e17573de56c364cd056d8d54e8` |
| Model stub | `3699be70a5b0d823cab19a456425267c8469ce01011a3e43139ac27f3d1a68a3` |
| Toolbox schema 1 | `c44f2e2d18aee7efe86aadf120d4d86c2628d494277f1eb30c238e1ac253b561` |
| Toolbox schema 2 | `e4daaa3a3110e22c02d41f8c6d5ce7574aac6881336b7b58f4441c49d1b6741b` |

580 application source hashes совпали с ранее собранным снимком; образы
приложения переиспользованы. Pod image identities совпали в minimal, A, B,
legacy upgrade и restore. CA для A/B различаются, приложение не пересобиралось.
Локальные configuration IDs и синтетическая metadata не доказывают опубликованные
OCI manifest digests; delivery guard не ослаблен.

Schema-1 kit сохранён целиком. Chart SHA256:
`3d5c02bcec0ae1b69ac044001401f25825bc2be57feac06b0aeecadf2b2f0c24`,
tools SHA256: `139ed6f5056bc7c89a1d0eb10eb31edd3e155793c36d66da2d9e4c08f7b729d9`.
Schema-2 runtime kit: chart SHA256
`215bfc37bef9daac7387ca59e69ba31657592996f1048a2a86a80226c8c71643`,
tools SHA256: `04b12b860377516ea08d0143860ae59087560544534d64d92166c578a682e47a`.
Первоначальный kit-v2 также сохранён. Изменённый runtime kit содержит исправление
фактического kubectl patch; его release SHA проверен отдельно.

## Фактические сценарии

| Сценарий | Измеренный результат |
|---|---|
| Matching bootstrap 1/2 | Оба комплекта приняты своим offline/nonroot toolbox; cross-version и identity mismatch отвергнуты тестами до output/mutation |
| Minimal: Gateway/Vault/runtime trust off | Install, ready, upload, generation, BM25/stream, restart/reapply, непустой backup и restore, четыре PVC после uninstall |
| Actual legacy kit | Format-1 backup: 2 docs / 2 chunks / 2 concepts / 4 vectors; restore исходными tools 1, затем upgrade tools/chart 2 и smoke |
| A | Production protections, confidential OIDC, роли/CSRF/logout, HTTPS, default probes/resources, policy off; собственные Secrets/PVC/CA/callback |
| B | Те же application images; другие namespace/callback/CA, ресурсы и probe timings; маркер A отсутствует до upload B; policy on |
| Upload B | Файл 100 MiB плюс multipart принят; 100 MiB + 1 byte предсказуемо отклонён, частичного документа не появилось |
| Attachment B | TrafficPolicy Accepted/Attached; HTTPRoute Accepted/ResolvedRefs/Programmed с актуальной generation |
| Controlled finite | Ограничение 2 s: поток прерван за 3.04 s, получены 2 frames |
| Controlled zero | Request/backend `0s`, idle 10 s: активный поток завершён за 13.04 s, все 14 frames |
| Controlled idle | Stalled поток закрыт за 11.04 s, только первый frame |
| Product delayed flow | Stub delay 25 s, NDJSON heartbeat и cancel/history проверены через Gateway при `0s`/idle 10 s; heartbeat приложения не отключался |
| Actual TLS clients | Настоящий confidential SSO/backend OIDC, Node HTTPS из frontend; operation-image OIDC и LLMClient/LiteLLM HTTPS SSE с env/mount выбранного template |
| Trust rotation/revert | Новый immutable overlap bundle, deploy с backup старого trust, HTTPS/Job smoke; возврат прежней ссылки без пересборки |
| Wrong CA/recovery | Корректный PEM с посторонним issuer: readiness зелёная, Node HTTPS и OIDC/model Jobs отказали; у Jobs подтверждена certificate verification failure. Возврат прежнего bundle восстановил HTTPS |
| Fresh restore B | Backup **до** ротации: 3 docs / 3 chunks / 3 concepts / 6 vectors; SHA/content integrity и totals проверены lifecycle. Новые namespace/PVC, эквивалентный bundle под новым именем; Node/OIDC Job, upload/BM25/stream и login/logout smoke повторены |
| Retention/cleanup | По четыре PVC сохранялись после каждого uninstall; source и backup удерживались до проверки копии. Затем удалены только namespaces с совпадающими UID/run-id; приватные backup/bundles/logs сохранены вне kind |

Ротация проверяет overlap/revert доверенных корней и независимые CA профилей A/B.
Замена issuer сертификата внешнего IdP и реальное certificate renewal этой
проверкой не подтверждаются. Node negative gate фиксирует отказ HTTPS; конкретная
certificate error дополнительно проверена настоящими backend adapters в Jobs.

## Ошибки и продолжение

Приёмка завершена серией прогонов с явным продолжением из сохранённого состояния.
Исходные failed results/logs не переписаны. Один непрерывный полный запуск
окончательного runner после всех исправлений отдельно не повторялся.

- Реальный kubectl трактовал `--patch-file=-` как имя файла. Исправлено на
  `--patch` с JSON в argument array; RED/GREEN lifecycle regression сохранён.
- Helm 4 SSA конфликтовал с Update field ownership тестовой HTTPRoute.
  После проверки отсутствия writers выполнен явный recover-lock известной
  операции. Изолированный Helm drill подтвердил same-manager server-side apply;
  lab fixture исправлена без force и ослабления ownership guards.
- Дополнительная model-through-Gateway fixture дала 503/timeout при рабочем
  прямом stub и зелёных route conditions. Эта ошибка сохранена и не названа TLS
  успехом. TLS adapter проверен через отдельный HTTPS endpoint того же stub image
  в namespace; product NetworkPolicy не расширялась.
- Lab-helper ожидал удаление всех Pods, включая отдельные TLS fixtures.
  Ожидание сужено до chart roles, четыре PVC сохранены; завершён fresh restore.
- Windows CRLF и отсутствие fixture data cwd мешали Linux shell contracts.
  Нормализована только изолированная копия; scripts исходного Compose не менялись.

## Автоматические проверки и открытые gates

Финальная команда регрессии — полный список 15 suites из Task 6: 316 PASS,
0 skips. CI focused list расширен с сохранением прежних checks; полный CI здесь
не запускался. Неизменённые Linux Compose backup и diagnostics contracts прошли.

Stability: 10 последовательных прогонов по 43 теста — исходные 11 runner guards,
29 runtime-trust cases (включая настоящие OIDC/LLM TLS fixtures) и 3 home helper
cases. После последних исправлений ещё 10 последовательных прогонов по 12
новых cases: 7 endpoint guards, scoped park и 4 ранних CLI validations.
Это fixture stability, а не десять полных A/B rollout.

Выполнен scoped self-review chart/lifecycle/trust/schema/bootstrap/runner/CI/docs.
Независимый fresh-context review не выполнялся: согласован inline режим без
делегирования. `git diff --check` прошёл; существующие CRLF предупреждения отмечены.

Остаются открыты: целевые admission/CSI/quota/CRD/RBAC/LB limits, enforcing CNI
matrix этого отдельного стенда, actual IdP/certificate renewal, live модели/dense
retrieval и качество ответов, registry publication/CI Lint/promotion, полный CI,
production RPO/RTO и независимый review. Исторические 5 high dependency findings
не закрыты этим пакетом. External storage, новые OIDC/audit механизмы, frontend/API/
worker реплики и HA остаются отдельными этапами roadmap.

Приватные журналы, Secrets, endpoint identities, CA keys и данные не входят в
отчёт. Доказательства находятся в защищённом lab volume и игнорируемом ledger;
публикация backup или сырых логов не выполнялась.
После приёмки удалены только созданные для неё kind cluster и lab-оператор;
приватный том оставлен. Format-1 и format-2 backups повторно проверены в
offline/read-only контейнере после cleanup.
