# Приёмка объединённой версии диагностики — 01.10.2026

Spec: согласованные рекомендации и «делай» в текущем чате; исходные критерии
в docs/superpowers/plans/2026-09-28-admin-diagnostics-levels-performance.md.
База интеграции fef1b5c; текущий main 38f7ed6 включить и зафиксировать.

## Global constraints

Работа только в существующей codex/diagnostics-integration. Production,
рабочий main checkout и frozen v16/v11 стенд сохраняются. Push/main merge
не входят. Авторизация передачи безопасного source/test archive в
root@10.10.1.50 → CT102 и отдельный /opt/okf-diag-integration-20261001 сохраняется.
GC/RAG/пороги/privacy не менять. Прежний FAILED сохраняется как исторический
результат; новая release acceptance имеет собственные status/evidence.
Реальная performance ошибка не исправляется повторным полным прогоном.

## Task 1: Зафиксировать объединённый кандидат

Включить main38f7ed6, сохранить оба UI/API/diagnostic контракта. Исправить
пояснение отчёта: прежний p95 c1, memory опыт c8; overhead observer не измерен.
Конфликты разрешать по контрактам; для runtime исправления RED/GREEN.
Expected: merge commit, нет конфликтов; candidate source revision фиксирован.

## Task 2: Функциональные проверки и review

Полный backend pytest один раз после merge; полный Node, Ruff, ESLint,
production frontend build. Исправленные ошибки проверить отдельно,
не объявлять полный suite green без полного успешного запуска.
Fresh whole-branch review по executing-plans, focused на новых merge changes,
контрактах chat/error/routes и document labels. Все important findings закрыть.
Expected: явные числовые результаты, нет неразобранных regressions.

## Task 3: Штатная release сборка на отдельном CT102 проекте

Передать allowlisted source/test files, без .env/документов/БД; проверить SHA.
Построить images с фиксированным source и lockfile. Backend штатный uvicorn,
frontend штатный diagnostics-runner.mjs, без profiler/import/GC flags.
Baseline enabled. Сохранить runtime private env локально на CT102,
в доказательства только allowlisted public metadata. Ready и HTTP lifecycle.
Expected: pinned image IDs, штатные команды, ready, согласованный synthetic corpus.

## Task 4: Приёмка 1 документа

Использовать существующие probe/analyzer с исходным порядком повторов,
concurrency1/8, off/standard/detailed; ordinary и active ZIP (с off+ZIP).
>=30s warmup, >=60s measured, >=1000 requests каждую series. Исходные p95
+10% ordinary/+20% ZIP, backend RSS delta<=128MiB, frontend<=32MiB,
ответы/counters/losses/duration/phase overlap как в исходном analyzer.
Штатный долгоживущий Node между series; никаких forced GC или restarts,
чтобы менять outcome. Внешние numeric PVE/process observers допустимы.
При failure остановить дальнейшую матрицу, сохранить evidence и разобрать
конкретный отказ; не начинать tuning или полный повтор ради passed.
Expected: воспроизводимый pass/fail двух блоков с exact SHA.

## Task 5: Остальная приёмка и завершение

Только если оба 1doc блока проходят: 100doc normal, active ZIP, stopped ZIP
по исходным критериям. После любого failure остальные зависимые blocks
не запускать. Закончить HTTP lifecycle/CRC/SHA/RBAC/delete, capture stop,
ready/queues/workers; остановить только собственный project. Сверить frozen/
production images/started_at. Сохранить safe JSON и полный отчёт, commit локально.
Expected: новая acceptance passed только после всех required gates,
либо failed с точным blocker и границей завершённой работы.

## Review Focus

Новые UI document-label paths после merge; allowlisted diagnostic codes,
route templates, source selection/streaming preserved; false acceptance
из-за неполной матрицы, пропавшего child RSS, необнаруженного ZIP overlap,
observer overhead, старого main/image/env, ложного zero loss.
