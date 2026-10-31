# Начало реализации реплик — P0/P1

Ветка: `codex/kubernetes-replicas`, 31.10.2026. План:
[Kubernetes replicas](../plans/2026-10-31-kubernetes-replicas.md).

## Реализовано

- Pure ReplicaProfile: смешанные providers, PostgreSQL/shared storage для distributed,
  проверка counts/types, certification syntax. Это проверка контракта, не сертификат HA.
- Settings и migration preflight отклоняют неподдержанную schema/роль; replicas-v1
  пока закрыт кодом сборки. Старый storage validator сохранён.
- Helm schema описывает будущие параметры; capability guard запрещает noncompact,
  compact отклоняет игнорируемые новые параметры и runtime.config не обходит guard.
- Acceptance entry point проверяет run-scoped targets, выдаёт санитизированный отчёт.
  Live checks pending, exit 1; сетевых mutations пока нет.
- Инвентарь состояния и schema входов инфраструктуры.

## Проверка и ограничения

Тесты писались до реализации; первичные import failures и затем конкретные
Settings/preflight/schema failures наблюдались до GREEN. Независимая code review
выявила игнорирование новых настроек в compact; добавлены regression tests и schema guard.
Повреждённые manifest types дополнительно проверены RED→GREEN.

Финальный профильный pytest: **43 passed, 11 skipped** (Helm отсутствует).
Frontend suite: **721 passed, 1 skipped, 0 failed**. Локальный ESLint и Ruff
изменённых Python-файлов прошли; `git diff --check` не обнаружил whitespace errors.
Это не полный backend suite и не успешный общий check-project.

Системный Python не имеет SQLAlchemy; используется `backend/.venv/Scripts/python.exe`.
Для отдельных pytest процессов нужны разные basetemp: общий Windows SQLite fixture
может удерживать файлы до завершения процесса.

P0 live probes, реальные namespace labels/CSI/reattach/backup failure domains и
provider selection не проверены. Контекст/manifest тестовой платформы не предоставлен.
Helm отсутствует: render tests пропускаются, это открытый gate P1.

Общий check-project остановлен на npm audit: в sandbox нет сети; автоматическая
проверка разрешений отклонила повтор с сетью, поскольку metadata зависимостей
отправляются внешнему npm registry без отдельного разрешения пользователя.
Коммит до обязательной общей проверки не делается. Запрет не обходится.

## Решения при исполнении

- Ruling: используем текущий checkout с новой веткой вместо нового worktree —
  планы пока untracked и есть чужие изменения; это сохраняет их на месте и не
  требует переноса. Риск: работать с diff внимательно, не включать чужие файлы в commit.
- Ruling: P0 tool реализован как fail-closed foundation, а не фиктивный live PASS —
  нет платформенного manifest/подтверждённых прав. Риск: окруженческая приёмка остаётся открытой.
- Ruling: vendors/версии не выбираются без дисков/прав; интерфейсы приложения
  продолжают разрабатываться независимо от инфраструктурного gate.
