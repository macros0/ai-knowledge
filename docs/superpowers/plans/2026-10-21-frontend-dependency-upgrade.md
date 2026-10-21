# React Runtime Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Выполнять автономно в текущем чате, с одним итоговым независимым review.

**Goal:** Внедрить React/React DOM 19.3.0, сохранить ESLint 9.39.5 и закрыть PR #8–#10 после проверки и merge.
**Architecture:** Один новый PR из отдельного worktree от актуального origin/main. Manifest и lock обновляются совместно; функциональные изменения допускаются только для подтверждённой регрессии.
**Tech Stack:** Next 16.3.8, React 19.3.0, ESLint 9.39.5, npm, Node, GitHub Actions.
**Spec:** docs/superpowers/specs/2026-10-21-frontend-dependency-upgrade-design.md

## Global Constraints

- React и React DOM: ^19.3.0 в manifest, 19.3.0 в lock.
- ESLint 9.39.5, Next/eslint-config-next 16.3.8 и lint-правила сохраняются.
- Без force/legacy-peer-deps, ослабления checks или пропуска тестов.
- Посторонние изменения сохраняются. Production deploy, миграции БД/корпуса вне объёма.
- #8 закрывается как отложенный; #9/#10 — как заменённые, только после merge замены.

## Review Focus

- Рассогласованная пара React/React DOM: npm ls после чистой установки, этап 2.
- Несвязанные изменения ESLint/lock: сравнение всех package entries и diff, этап 2.
- SSR/hydration регрессия при зелёных unit-тестах: production build и browser smoke, этап 3.
- Отличие Windows Node от Node 20 CI/Docker: версии фиксируются, Linux checks обязательны, этапы 1/3.
- Потеря чужих файлов при синхронизации main: резервная копия и сравнение хэшей, этап 4.

### Task 1: Изоляция и baseline

Files: читать AGENTS.md, frontend/AGENTS.md, manifests, CI, scripts/check-project.mjs; обновить эти ТЗ/план.

- [ ] Получить актуальный origin/main, записать SHA, git status, Node/npm и статусы старых PR.
- [ ] Создать/использовать отдельный managed worktree; ветка codex/react-runtime-upgrade. Перенести только согласованные документы.
- [ ] Найти рабочий npm-cli.js; вызывать через node, не сломанные npm/npx shims. Проверить выбранные npm flags без вывода секретов.
- [ ] Выполнить npm ci и baseline lint, frontend tests, production build; сохранить логи и baseline warnings.
- [ ] Зафиксировать решения и результаты в локальном ledger. Новые тесты не нужны для одного изменения зависимостей; при поведенческом исправлении сначала воспроизводящий тест.

### Task 2: Совместное обновление React

Files: frontend/package.json, frontend/package-lock.json, .github/dependabot.yml.

- [ ] Установить react@19.3.0 и react-dom@19.3.0 одной командой npm с сохранением ^ диапазонов. Проверить точные версии lock.
- [ ] Добавить Dependabot group react-runtime, patterns react/react-dom, в npm /frontend. Сохранить остальные настройки.
- [ ] Сравнить lock с baseline: разрешены только необходимые изменения react/react-dom/scheduler; ESLint и плагины неизменны.
- [ ] Выполнить чистый npm ci, npm ls и аудит. Ожидается успешное выполнение и отсутствие invalid/missing dependencies и high/critical audit findings.

### Task 3: Приёмка, review и PR

Files: условно адресные frontend/src и frontend/test при доказанной регрессии; отчёт в PR.

- [ ] Выполнить node scripts/check-project.mjs из корня с рабочим NPM_CLI_PATH; из frontend выполнить node node_modules/next/dist/bin/next build. Ожидается exit 0; сравнить warnings/skips с baseline.
- [ ] Поднять изолированный экземпляр собранного frontend и проверить сценарии браузера из ТЗ. Записать фактический revision, проверенные сценарии и ограничения окружения; не подменять проверку production-страницы старым dev server.
- [ ] Провести независимое review итогового diff; устранить блокирующие замечания, проверить исправления.
- [ ] Закоммитить только файлы задачи. Создать и прикрепить новый PR: React-пара, неизменный ESLint, группа Dependabot и проверенные результаты. Указать замещение #9/#10 и отложенный #8.
- [ ] Дождаться всех обязательных checks актуального head, CI и Compact Kubernetes. При изменении базы актуализировать ветку и проверки. Не засчитывать старый CI #10.

### Task 4: Merge, закрытие PR и синхронизация

- [ ] Слить проверенный head с expected SHA и соблюдением защиты main.
- [ ] Подтвердить remote merge SHA, версии React/React DOM 19.3.0 и ESLint 9.39.5, пройти обязательные post-merge workflows.
- [ ] Проверить #8–#10. Оставшиеся открытые закрыть: #8 — миграция отложена до подтверждённой совместимости; #9/#10 — внедрены заменой. Добавить поясняющие комментарии со ссылкой на merged PR, без ignore-команд.
- [ ] Сохранить и проверить хэши посторонних изменений, безопасно обновить локальный main fast-forward, восстановить чужие файлы. Проверить main...origin/main = 0 0; при расходящейся истории остановить только синхронизацию.
- [ ] Сохранить доказательства проверок, архивировать managed worktree после завершения; выдать SHA, PR, статусы проверок и ограничения. Не объявлять неисполненные проверки успешными.

## Откат

Revert PR коммита слияния с повторением обязательных checks. Production deploy в этой задаче отсутствует.
