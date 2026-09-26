# Повтор нагрузки и поиска на v21

26.09.2026. Парсер `mail-sources-v21`. Прогоны завершены exit0; результаты не подменяют прежние v20 отчёты.

## Q01: нагрузка

`tests/tmp/mail-load-v21/load-report.json`: 100 файлов, 86 590 043 байта; фиксированный manifest SHA256 `bd2d2e70d9aaeb58fa4bdac6d30ceb34a5f28def198d29654836598ba4bb5e94`. Реальные ASGI, PostgreSQL :25432, Qdrant :26333 и spawn-парсинг; LLM и 8D embeddings детерминированные, внешний LLM-вызов запрещён. Рабочие хранилища не изменялись. Код пути нагрузки не менялся во время измерения.

Все девять gates прошли. Обычные письма: parse p95 0.582 с; наблюдённый максимум RSS worker 110.16 MiB; загрузка 302.21 с. Поиск во время загрузки: 1237 запросов, p95 0.0525 с против 0.1591 с до нагрузки, ratio 0.3301. DOCX flag-on/off p95 ratio 1.0549, PDF 1.0097. Нет HTTP/search ошибок, потерянных слотов и orphan workers. Это измерение синтетической нагрузки, не производительность внешнего LLM.

Независимый `audit_mail_load.py` подтвердил SQL/Qdrant ownership и точные ID: 101 документ (включая контроль), 131 источник, 131 чанк, 121 концепт и 252 точки. Отчёт `tests/tmp/mail-load-v21/index-audit.json`.

## Q02: старый корпус

`tests/tmp/mail-q02-v21/comparison.json`: 252 сценария, 32 заранее сохранённых query vectors. Все `full_source_losses`, `group_losses`, `new_sources`, `final_only_losses` пусты; `order_changes_only` пуст. Пустые BM25 cases перечислены отдельно как диагностика baseline, не скрыты.

Использован только изолированный frozen snapshot `mail_q02_v10`, PostgreSQL :25432/Qdrant :26333. Completion/acompletion запрещены; эмбеддинги взяты из старого cache. Приватный корпус не передавался внешнему LLM, в Git или CI. После replay `integrity.py` подтвердил неизменность 38 SQL tables и Qdrant: SQL SHA256 `8c0a0b8e180c50a650b5fb16322c964aab9f0c4e275ec50e534f8878b396728a`, points SHA256 `35e7002a00f6bb406aef27c1b0eec6e4b6200dfe3f74e72ca4851ad8f5e7588b`.

Отчёты остаются локальными ignored артефактами. Общая приёмка сохраняет отдельно документированные ограничения и открытые вопросы; эти прогоны подтверждают именно Q01/Q02.
