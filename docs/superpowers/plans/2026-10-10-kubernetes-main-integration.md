# Интеграция compact Kubernetes в main

Согласовано через «продолжай» после оценки готовности merge. Base — актуальный main c2ba685; isolated branch codex/kubernetes-integration.

- [x] Перенести принятый compact chart/operator, добавить необходимые runtime health/profile/migration hooks к текущему приложению main, сохранить его auth/диагностику и Docker tooling. Образы собрать из этого дерева; старый OCI-kit остаётся отдельным принятом snapshot.
- [x] Проверить candidate: существующие contract tests, node scripts/check-project.mjs, backend pytest/coverage, doc-parser tests, frontend build, Docker builds и один новый kind smoke. Ограничения/непроверенный CI указывать точно. Crash recovery, PROD/HA и исторические матрицы не добавлять.
- [x] Независимый review итогового диапазона, исправления существенных замечаний, один focused commit. Main/shared dirty checkout и старый Kubernetes worktree сохраняются. Push/merge main отдельно после подтверждённой готовности.

Для текущего main OIDC flow state хранится в signed session; DR меняет ключ до старта и очищает серверные auth sessions, а таблицу persisted flows очищает при её наличии. Новый generic recovery controller не требуется.


Итоговые доказательства и границы: [отчёт](../reports/2026-10-10-kubernetes-main-integration.md).
Полный backend прогон собран до review fix: coverage 88.67%, 1 устаревший public-profile FAIL;
после fix весь изменённый модуль 31 PASS. Обязательный fresh hosted CI — перед merge,
а не новая матрица домашней приёмки. Push и main merge отдельны.


## Продолжение 12.10: первый TEST pilot

Подтверждено пользователем: PG/Qdrant входят в нашу поставку, bundled; внешние
хранилища этому заказчику не нужны. Scope — current-main sync, один TEST example
и короткая инструкция. Целевое развёртывание требует actual platform inputs.

- [x] Свести main e2f6d94 с Kubernetes candidate, сохранив оба security раздела.
- [x] Проверить owning R1/parser/compact contracts, TEST render и check-project;
  один review follow-up delta, focused merge commit. Hosted CI — на конечном HEAD.
- [ ] Перед публикацией показать точный outgoing range и получить разрешение
  на push/PR согласно прежнему разделению commit/push/merge.
