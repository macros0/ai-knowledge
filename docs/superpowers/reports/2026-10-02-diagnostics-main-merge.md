# Слияние диагностики в main — 02.10.2026

Пользователь явно разрешил локальное слияние всей подготовленной версии в main.
Объединены main `cde0724a745256d32875d58af8edf798076ccd75` и
`codex/diagnostics-integration` `64677c32a658fd1e32776bfefda56aef0463fdd3`.
Ветка `admin-diagnostics` `4ec9e14` полностью входит в integration.
Историческая `legacy/v0` не относится к текущей разработке.

## Разрешение пересечений

Два конфликта: DocumentList.jsx и SelectionBar.jsx. Сохранены вкладки
Documents / Upload documents / Trash, контекстные действия, модальное
редактирование тегов, ограничения действий и защита от устаревшего ответа
при изменении выделения. SelectionBar сохраняет актуальную реализацию main;
старый обработчик массовых тегов уже перенесён в BulkTagsModal.

DocumentList сохраняет apiToast с requestId/localReportId и проверку
shouldApplySelectionResult перед показом ошибки. В BulkTagsModal и
BulkGenerationModal добавлены ErrorReference для ошибок перенесённых действий;
пользователь видит локализованную ошибку и копируемый безопасный идентификатор.
Четыре проверки рендерят реальные JSX-компоненты для серверных/локальных
идентификаторов и проверяют отсутствие приватного текста провайдера.
Локализация и CSS объединены автоматически и проверены тестами/сборкой.

## Проверки объединённой версии

- Backend pytest tests -q: 2851 passed, 24 skipped, 22 warnings; exit 0;
  2504.51 s (41:44). При завершении qdrant-client также выдал RuntimeWarning
  о закрытии grpc_channel; код завершения остаётся 0.
- Frontend node --test: 354 passed, 1 skipped; exit 0.
- Doc-parser pytest: 400 passed, 1 skipped, 1 warning; exit 0.
- Ruff всей backend-директории: passed.
- ESLint: 0 errors, 40 warnings; новый тест отдельно без предупреждений.
- Next production build --webpack: passed. Сборка выполнена на копии
  tracked frontend-файлов; исходный .next работающего dev-server не изменялся.
  Только для сборки: 2 workers и Node heap 1536 MiB; runtime не настраивался.
- Git diff --cached --check: passed; конфликтных записей индекса нет.
- 341 Git blob с доказательствами диагностики совпадает с integration.
  Для 67 файлов с -text совпадают также исходные байты рабочего дерева;
  50 SHA256 в checksums.json последнего прогона проверены.

Команды и локальные логи текущих проверок:
`tests/tmp/diagnostics-main-merge-20261002/` (ignored; raw logs не коммитятся).
Pytest использует отдельные basetemp, SQLite/auth/LLM isolation из conftest;
рабочая БД и .env не менялись.

## Граница приёмки

Предыдущий PASSED всех пяти нагрузочных блоков относится к runtime source
`57727d9` и внешнему RSS harness `fdc327b`, как указано в
[отчёте приёмки](2026-10-01-diagnostics-merged-release-acceptance.md).
Он не является новым замером frontend-версии после включения cde0724.
Исторические FAILED и исходные численные критерии сохранены.

Новый нагрузочный прогон, Docker build, production deployment, удалённый push
и проверка реального SSO/cookie-CSRF в рамках локального слияния не выполнялись.
Авторизованная браузерная приёмка контекстных действий остаётся отдельным шагом:
её предыдущий отчёт фиксирует ограничение корпоративного входа.

Управляемый worktree integration сохранён: в нём есть ignored данные и логи
диагностики, которые не входят в восстанавливаемый Git snapshot. Другие
рабочие копии не изменялись. Исходная ветка сохранена для истории проверок.

При финальной сверке обнаружена параллельная работа с
`docs/ADMIN_DIAGNOSTICS.md` и новым `docs/ADMIN_DIAGNOSTICS_GUIDE.md`.
Эти изменения оставлены вне данного merge-коммита.
