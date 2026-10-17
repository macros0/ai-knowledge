# Compact Kubernetes: кандидат для main, 10.10.2026

Base: `main c2ba685`. Branch: `codex/kubernetes-integration`.
Интеграционный кандидат подготовлен. Перед merge обязательны обычные CI checks
на итоговом коммите; дополнительной разработки для compact scope не осталось.
Старый Kubernetes worktree с экспериментами и shared dirty main сохранены.
Обязательный результат: домашний стенд и комплект переноса; PROD отдельно.

## Что входит

Chart compact/bundled с одним application Pod и worker, Recreate и отдельными PVC;
компактный оператор deploy/preflight/backup с отказом автоматического restore;
ограниченный синтетический HTTPS/Keycloak home helper; model stub; profile guard,
запуск Alembic и model/vector marker; независимая liveness; ручной DR и явное
удаление auth sessions. Docker-сборки сохраняют текущие main diagnostics/parser/
frontend tooling; runtime public CA и build secrets доступны отдельно.

Текущая авторизация main сохранена. Helper принимает только confidential/basic,
без PKCE, auto UserInfo. Дополнительные профили отклоняются до внешних команд.
Не добавляются experimental OIDC flow store/audit outbox/recovery controller,
HA/PROD и historical crash matrices. Frozen kit остаётся отдельной принятой поставкой.

## Проверки

| Проверка | Результат |
|---|---|
| `node scripts/check-project.mjs` после review fix | PASS: npm audit 0, ESLint, 492 frontend tests + 1 skip, Ruff |
| Полный backend pytest + coverage >=85% (коллекция до review fix) | 3163 PASS / 36 SKIP / 1 FAIL; coverage 88.67%, порог 85% достигнут; 3404.45 с |
| Итоговый изменённый backend-модуль с общим conftest | PASS: 31, без skips, 30.41 с |
| Полный doc-parser | PASS: 406, 1 skip |
| Frontend production build | PASS |
| Compact focused Windows | PASS: 64, 11 Helm skips (Helm не установлен) |
| Docker builds backend/frontend/stub | PASS, из source candidate |
| Helm lint с явными home values и chart contracts на Linux | PASS: lint + 11 tests, без skips |
| Новый kind install и managed update | PASS; source app 2/2 Ready |
| HTTPS/OIDC функциональный smoke | PASS: trust/negative trust, redirect, four roles/unmapped denial, Secure/HttpOnly/CSRF, 2 MiB DOCX/generation/sources/BM25/chat/logout |
| Final compact contracts Linux (после fix) | PASS: 75, без skips, в собранном backend image + read-only source/Helm |
| Новый stopped-source nonempty backup | PASS: format 2, strict verify-content, SHA; operation `2d5ffdeed9d0`; totals 1 document / 1 chunk / 1 concept / 2 points; source остаётся остановленным |
| Дополнительный restart после возврата прежних сервисов | Не выполнен: requests 9346 MiB/94%, свободно около 530 MiB, новый app+IdP требуют 1152 MiB. Первые install/update/HTTPS smoke пройдены |
| GitHub workflows | Добавлены; не запускались (push не выполнялся) |

Независимый review: одно Important/P2 замечание о неподдерживаемых OIDC profiles.
Воспроизведено 4 RED failures; исправлено отказом до создания ресурсов и проверено
focused GREEN, 31 PASS с общим conftest, 75 Linux PASS и обязательным check-project. Других Critical/Important замечаний
reviewer в default home пути не обнаружил. Повторные матрицы не требуются.

Исходный FAIL — `test_public_realm_requires_s256_and_userinfo_groups_only`:
pytest собрал положительную проверку public/S256/UserInfo-only до review fix,
а затем исполнил её с helper, уже запрещающим этот неподдерживаемый профиль.
Traceback подтвердил именно новый `Current main supports only ...` guard.
Этот тест заменён четырьмя проверками отказа и CLI early-refusal.

После начала полного прогона из backend test files изменился только этот модуль
(проверено SHA-256). Неизменённая часть: 3137 PASS / 36 SKIP; итоговый модуль:
31 PASS. Все 3204 актуальных collected cases покрыты этими двумя частями;
11 Helm skips дополнительно выполнены на Linux. Исходный full FAIL сохранён
в evidence, свежий единый full run после fix и hosted CI не выполнялись.
Порог покрытия и остальные тесты не менялись.

## Происхождение образов

Source snapshot SHA-256:
`e88556453f32d364a61a0b5a70b97ab03e96f4989c9d0a50ae04feb80cba27fe`.
После fix проверены 623 Docker source inputs: различий нет. Build revision label
`integration-20261010` — идентификатор сборки, не Git SHA и не exact-HEAD assertion.
Тестовые уникальные tags `compact-main1010` не заменяли tags работающих старых стендов.

| Образ | Локальный image ID (не registry digest) |
|---|---|
| Backend | `sha256:33d176df3c01d9d0364557d60a6754a25b6f52959bd383dc0fa83ecfdd2c82b7` |
| Frontend | `sha256:1f2d5a55f35ffd4d7c2e16548db3b213df14cab304f2781e8dc2acc856a7d0f3` |
| Stub | `sha256:7ec61e16f4ebcae8be49a30b366ad5ba50ee3f8b5faee9e2874c4c8e2fa72e75` |

## Границы и состояние

На VM scheduler отказал новому Pod из-за Insufficient memory. Для проверки временно
остановлены только наши прежний исходный test IdP и restored test application;
их PVC сохранены. Оба сервиса возвращены и Ready. Новый application и его IdP
остановлены после проверки; storage/PVC сохранены. На повторный запуск одновременно
со всеми прежними стендами сейчас недостаточно памяти по requests; defaults не
ослаблены. User-authorized передача final helper/tests и Linux contracts выполнены;
backup выполнен на остановленной source-копии без изменения старых стендов.
Новый install/update/HTTPS smoke использовал default профиль с моделями stub.
Live model quality, PROD, HA, активная старая cookie после DR и GitHub CI не приняты
этим прогоном. Ручной restore принят ранее на frozen kit, не повторён на этих images.


## 12.10.2026 — Первый TEST pilot, bundled

Подтверждено пользователем: PostgreSQL и Qdrant входят в нашу поставку; external
storage этому заказчику не требуется. В clean Kubernetes worktree объединён
committed main `e2f6d94` (R1 full-text fix). Единственный append conflict SECURITY.md
разрешён сохранением обоих разделов. Shared dirty main не менялся.

Добавлены [TEST example](../../../deploy/kubernetes/profiles/test-bundled.values.example.yaml)
и [один путь пилота](../../KUBERNETES_TEST_PILOT.md). Пример содержит placeholders,
не разрешает target deploy; models live, production auth, bundled storage,
отдельные PG/Qdrant StatefulSets, один application/worker, четыре existing PVC.

Проверки конечного локального кода до commit:
- Helm 4.3.0 Windows archive checksum PASS; TEST lint/render PASS.
- R1 owning + compact/chart/model/profile contracts с общим conftest:
  **129 PASS / 0 SKIP**, 153.41 с. Тесты/код не менялись во время прогона.
- Полный doc-parser: **398 PASS / 1 SKIP**, 12.84 с.
- check-project: npm audit/ESLint/Ruff PASS, frontend **492 PASS / 1 SKIP**.
- Независимый read-only review follow-up delta: **0 Critical / 0 Important**;
  R1 paths совпадают с `e2f6d94`, команды/secret/CA/auth/egress/restore согласованы.

Свежий единый full backend/coverage, Docker builds и kind acceptance для нового
HEAD должны выполнить hosted CI; старые images/build proofs относятся к 10.10.
Actual customer admission/PVC/route/IdP/models/CA/egress и restore provisioning
здесь не проверены. Target runtime Secrets, release digests и HTTPS/SSO inputs
должны быть предоставлены оператором окруженческой копии. Публикация кандидатной
ветки/PR требует отдельного разрешения на конкретный outgoing range; main merge
и target rollout выполняются после своих gates.
