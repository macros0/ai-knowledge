# Production shell backup/restore: изолированная приёмка

26.09.2026. Выполнены неизменённые `scripts/production/backup-bundled.sh`
и `restore-bundled.sh`, с настоящим Bash/Docker Compose в WSL Ubuntu.
Это проверка процедуры на synthetic данных, не развёртывание production.

## Результат

1. Windows backup предыдущей проверки (`repair-windows/backup-hex`) восстановлен
   штатным restore в новый проект `okf-mail-script-drill-source-v20` и новый
   Linux filesystem каталог `/tmp/okf-mail-script-drill-source-v20-data`.
   SHA256 всех девяти артефактов проверены, выполнены pg_restore, Qdrant snapshot
   recovery, Alembic и `check_integrity.py --strict --expected-manifest`:
   один документ, проблем0. Запущены backend/frontend services.
2. Реальный HTTP к запущенному backend, SQL и Qdrant сверены с сохранённым
   Windows baseline: пять файлов byte-identical; три sources/chunks/locations;
   шесть прежних point IDs; active generation и канонический текст неизменны.
   Скачивание корня/вложенного EML/DOCX и source-location проверены по HTTP200,
   hashes и `nosniff`, а не только прямым вызовом route/TestClient.
3. Штатный backup остановил backend/frontend, создал dump/list, snapshot,
   totals, data.tar, image inventory, manifest/checksums и возобновил сервисы.
   Их готовность проверена `compose up -d --wait` перед следующим шагом.
4. Этот backup восстановлен штатным restore в другой новый проект
   `okf-mail-script-drill-target-v20` и каталог
   `/tmp/okf-mail-script-drill-target-v20-data`. Strict integrity снова прошёл;
   real HTTP/SQL/Qdrant проверка совпала с первой и Windows baseline полностью.

Все шаги завершились exit0; итоговый `tests/tmp/mail-script-drill-v20/report.json`
имеет phase=passed. Source/target `live-verification.json` идентичны.
Production shell scripts не менялись; их SHA256 сохранены в отчёте.

## Негативные проверки

Отдельная копия backup с изменённым postgres.dump отклонена по SHA256.
Целевой каталог/тома для corrupt-project не созданы. Оригинальные backup
и проекты source/target не изменялись этой проверкой.

При занятом настоящем `flock` backup отказал с сообщением о другой операции.
Source backend/frontend не остановлены и не перезапущены: контейнеры и
`StartedAt/running` до/после совпали. Скрипт создаёт пустой архивный каталог
до проверки lock; он не считается готовым backup. Проверка зафиксирована
в `negative.json`, exit0. Replace/destructive mode не использовался.

## Стенд и ограничения

Сеть обоих проектов internal, host ports отсутствуют. App/parser/scripts/
prompts/Alembic текущего checkout смонтированы read-only. PostgreSQL17 и
Qdrant1.19 настоящие, backend — настоящий uvicorn/create_app и HTTP routes.
Frontend — **inert HTTP fixture**, проверяет stop/start orchestration, но
не доказывает развёртывание Next.js. Auth disabled/development; исходная
synthetic генерация использовала fake LLM/8D embedding. Это один документ
с вложенным EML/DOCX, не полный корпус всех форматов и повреждённых backup.

Первый запуск от обычного WSL пользователя остановился на checksum чтении:
каталог snapshot исходного synthetic backup имеет mode0700/owner root.
Повтор выполнен WSL root, без изменения исходных прав. Это исправление
доступа стенда к закрытому backup, не production code fix; первый лог сохранён.

После проверки остановлены только два новых проекта; containers/volumes/
data/archives сохранены. Рабочие PostgreSQL/Qdrant/Keycloak и пользовательские
данные не перезапускались. Коммита, push и deployment не было. Backend suite
продолжался независимо с неизменённым code manifest.

## Артефакты и команды

`tests/tmp/mail-script-drill-v20/`: `compose.yaml`, `drill.env`, `verify_live.py`,
`run-restore-source.sh`, `run-backup-restore.sh`, `run-negative.sh`,
`restore-source-root.log`, `backup-restore.log`, `backup-console.log`,
`source-reports/live-verification.json`, `target-reports/live-verification.json`,
`negative.json`, `corrupt-restore.log`, `overlap-backup.log`, `report.json`,
`services.log`, `stop-drill.log`; первоначальный `restore-source.log` также сохранён.
Backup path записан в `backup-path.txt`, содержимое — в `backups/`.

```powershell
wsl -d Ubuntu -u root -- bash /mnt/c/Users/alexey/ai-workspace/ai-knowledge/tests/tmp/mail-script-drill-v20/run-restore-source.sh
wsl -d Ubuntu -u root -- bash /mnt/c/Users/alexey/ai-workspace/ai-knowledge/tests/tmp/mail-script-drill-v20/run-backup-restore.sh
wsl -d Ubuntu -u root -- bash /mnt/c/Users/alexey/ai-workspace/ai-knowledge/tests/tmp/mail-script-drill-v20/run-negative.sh
```

Повторение требует новых имён projects/directories: runner отказывается
перезаписывать существующие данные/тома. Эти ignored файлы и старый baseline
не являются неявными CI dependencies; для автоматизации нужен самостоятельный
builder с новым изолированным Windows baseline. Текущий отчёт доказывает
выполненный drill, а не воспроизводимость на машине без сохранённых artifacts.
