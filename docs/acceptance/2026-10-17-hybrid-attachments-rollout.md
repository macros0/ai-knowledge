# Включение гибридной обработки в рабочем main — 17.10.2026

Изменения перенесены в незакоммиченный `main` поверх `ec26700257f10eaba006c443e3ae523d0a22614f`. Коммитов и push нет, Git index пуст. Объединены пять пересечений с текущим main (CSS/переводы/manifest), карточки таблиц из ec26700 сохранены. Посторонние Kubernetes-документы не входили в перенос.

## Схема и резервная копия

До переноса фактическая PostgreSQL схема полностью совпала с моделями revision062: Alembic compare_metadata вернул0 differences,44 модельные таблицы. Маркер был f0a1b2c3d4e5. Backend/frontend остановлены; PostgreSQL, Qdrant и Ollama оставались запущены.

Созданы согласованные PostgreSQL custom dump, uploads, Qdrant snapshot, исходная runtime-конфигурация и копии изменяемых файлов main. Backup размещён в `data/backups/hybrid-attachments-20261017T142056Z`. Полный SQL dump восстановлен в отдельную `okf_scope_verify_20261017_142056`, счётчики совпали. На ней сначала подтверждены только ожидаемые scope additions, осознанно согласован marker062, затем выполнен upgrade063 и получен нулевой schema drift.

На рабочей БД повторены те же проверки и действия: marker согласован с уже существовавшей схемой062, затем штатная migration063. Старые migrations повторно не исполнялись и не изменялись. После upgrade compare_metadata вернул0 differences. Счётчики сохранены:44 documents,44 generations,3192 chunks,13371 concepts,121 sources,24 tables,1785 table rows.

Целостность1740 исходных файлов uploads подтверждена SHA-256 сравнением с backup. Qdrant snapshot прочитан как корректный архив с20 entries; его hash и размер записаны в private backup manifest. PostgreSQL restore проверен; Qdrant snapshot на отдельном сервере в этом переносе не восстанавливался.

## Runtime и проверки

- В `.env` включён `ATTACHMENT_REVIEW_ENABLED=true` и настроен абсолютный `ARCHIVE_7ZIP_PATH`; остальные ключи сохранены.
- Установлены pinned py7zr1.1.4 и rarfile4.5; имеющийся Windows7-Zip25.01 не обновлялся.
- Реальные ZIP/7z/RAR3-solid/RAR5-solid/RAR5-crc codecs smoke завершились exit0. Хеши содержимого RAR совпали с квалифицированными fixtures.
- Backend18000, frontend16300 перезапущены через штатный hidden helper; health/ready и frontend вернули200. Keycloak OIDC сохранён, запрос к protected API без сессии вернул401.
- Интегрированные backend targeted suites:82 passed,0 failed (scope API/admission/publication/cancel/export, SQL/schema, E2E search, dictionaries).
- `node scripts/check-project.mjs`: passed, включая npm audit, frontend508 passed/1 старый skip и Ruff. Первый sandbox npm-audit network failure сохранён; повтор выполнен с обычным доступом к registry, без обхода audit.
- Production webpack build: exit0. `git diff --check`: exit0.

## Фактический документ

Документ `211c5906782441cb` открыт через рабочую SSO-сессию Admin. В UI создан durable review:3 XLSX,2 включены и1 тяжёлый выключен. Тяжёлый: нижняя оценка122 строки/9444 ячейки; два лёгких:9 строк/423 и387 ячеек. Исходник исключённого Excel скачан через тот же protected endpoint, размер1223764bytes.

Review сохранён в state draft, revision1. Действующее поколение `54f31288b0704a479f11b30e33d11952` осталось активным; candidate отсутствует, документ done,2909 опубликованных чанков. LLM-перегенерация этим переносом не запускалась. После проверки галочек пользователь может нажать «Перегенерировать с выбранным составом»; действующие данные переключатся после успешной публикации нового поколения.

Скриншот рабочего диалога сохранён в игнорируемых локальных артефактах `tests/tmp/hybrid-attachments-rollout-20261017/live-document-scope.png`. Содержимое заказчика и его имена в этот отчёт не скопированы.

## Восстановление

Для отключения новых review выключить flag в этой совместимой версии и перезапустить backend; сохранённые policies продолжают действовать. Полный возврат к старому коду требует согласованного восстановления БД/FS/Qdrant. Не выполнять downgrade с сохранёнными reviews/policies. Private backup содержит runtime-секреты и данные: он остаётся локальным, не добавляется в Git.

## Актуализация18.10: incremental scope и производительность

Исторический rollout выше относится к063. По отдельной команде пользователя применена064 с предварительным проверенным backup `data/backups/scope064-20261017T213404Z`. Текущий backend включает fast exclusion/reuse/delta и edit lease; после performance fixes перезапущен только backend штатным hidden helper. Health/ready ok, frontend200. Дополнительной миграции для оптимизаций не потребовалось.

Рабочий документ уже опубликован с одним исключённым тяжёлым Excel: active142chunks/1716concepts/1858points, archive2767chunks, stored2909; candidate отсутствует. Текущий performance проход не переключал пользовательский состав. Исходники, retained text/provenance/IDs и числовые векторы подтверждены SHA; поиск/экспорт показывают только active, исключённый чанк404. Все47 документов done.

A50 PASS: изолированная копия, прогрев +10exclude/10reuse, p95 admission→publish18.43с/29.08с, preview4.15с/0.68с, peak953.20MiB, providers0. Cold reuse36.57с отдельно; пороги не менялись. [Отчёт и ограничения](2026-10-17-hybrid-attachments.md#закрытие-a50--18102026), [машиночитаемые измерения](2026-10-18-scope-performance-metrics.json). Тестовые базы/коллекции удалены. Изменения функции остаются незакоммиченными; Git mutation/commit/push этим проходом не выполнялись. В общем main отдельно появился commitbeeecf7 про page selection/clear; hashes Python runtime квалификации не изменились.
