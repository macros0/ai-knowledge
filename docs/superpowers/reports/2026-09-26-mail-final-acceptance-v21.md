# Итоговая проверка среза v21

Проверка выполняется на текущем конечном коде после исправлений единственного final review и строгой проверки авторства. Никаких commit/push/production rollout или массового regenerate не выполнялось. Локальный mail flag включён по указанию пользователя.

## Неизменяемые исходники и команды

`tests/tmp/mail-final-acceptance-v21-r2/source/`: 762 файла из tracked/untracked non-ignored рабочей копии. Без `.env`, `.git`, локальных зависимостей и частных пользовательских писем. `reports/source-manifest.json`, HEAD/status/patch фиксируют происхождение и SHA каждого файла. Срез не является клоном опубликованного коммита; коммит не запрошен. Linux source подключён read-only; Windows использует установленный venv с PYTHONPATH на этот срез, не новую установку зависимостей. Полные suites используют один и тот же app/parser/frontend source.

Backend: `python -m pytest -q --cov=app --cov-report=term-missing --cov-report=xml:... --cov-fail-under=85 --basetemp=<unique> --junitxml=...`; cwd frozen backend. Linux: Python3.12, установленный `okf-mail-verify-tests:20260926-fonts`, UID1000, `--network none --read-only`, отдельные tmpfs `/tmp`, `/workspace/data` и `/fullstorage`8MiB. Только Linux устанавливает `FULL_STORAGE_TEST_ROOT=/fullstorage`; host disk не заполняется. Windows: Python3.12 из backend venv, без opt-in заполнения host disk. Отчёты `backend-{linux,windows}.{log,xml}`, `coverage-{linux,windows}.xml`.

Parser: полный `python -m pytest -q --no-cov`, cwd frozen doc-parser, unique basetemp/JUnit. Frontend: штатный Dockerfile target build, Node20-alpine, `npm ci`, `npm run build`; затем в собранном образе `node --test` и полный ESLint с frozen backend UI manifests. Изменений в работающем Next `.next` для сборки не выполнялось.

## Результаты

- Linux полный backend: **2120 passed,19 skipped,331 warnings,652.52s,exit0**, coverage **89.07%**, CI85 пройден. Все пять real full-storage cases прошли без skip/failure. Пропуски не считаются успешными тестами.
- Windows полный backend: **выполняется, session52732**. Нельзя считать общую итоговую проверку завершённой до terminal/JUnit/coverage.
- Windows полный parser: **384 passed,1 POSIX skip,11.48s,exit0**; Linux: **385 passed,6.29s,exit0**.
- Полный Ruff backend: exit0. Frontend Node suite: **190 passed** Windows и Linux; Linux полный ESLint:0errors/36 прежних warnings, scoped:0errors/2 прежних anonymous-export warnings. Docker frontend build exit0.
- Fresh real GPT-4.1 `mail-chain-v21-final/report.json`: DOCX→MSG→XLSX, upload/publication/search/chat/checked spans/download/OKF export, **6/6 checks**, exit0. Ключевые факты из XLSX — код3509/12дней, цитируется именно root/0/0. Реальные отдельные PostgreSQL/Qdrant, BM25/fake8D; это не dense quality benchmark.
- Q01/Q02 повторены после parser v21: все9 load gates/exact index audit и252 old-corpus scenarios без потерь/перестановок. [Отдельный отчёт нагрузки и replay](2026-09-26-mail-v21-load-replay.md).
- Авторство: последние40 Windows/40 Linux связанные tests, четыре настоящих synthetic uploads/GPT-4.1 positive+negative и authenticated browser. [Отчёт авторства](2026-09-26-mail-authorship-verification.md).
- Реальный MSG direct Outlook Save As: provenance подтверждён владельцем, supervised parse v21 без warning/replacement, два вложения. Только локально, без LLM/Git/CI. [Отчёт экспорта](2026-09-26-mail-outlook-export-verification.md).

## Дополнительные текущие доказательства

L06: `path-probe.json` Windows и `linux-path-probe/reports/path-probe.json` Linux:14 опасных имён дают14 разных ASCII targets, байты не перезаписаны, escape нет. Windows дополнительно два настоящих NTFS junction (destination/ancestor): skipped_storage, target остаётся пустым. Временные junction/targets находятся только в ignored каталоге проверки; продуктовый код не менялся.

U01: после browser RED одинаковых доступных имён в `DocumentSources` добавлены RU/EN имена файла/источника к download и честное «контейнер для». Клавиатурный Enter скачал контейнер nested MSG, SHA совпал с закреплённым оригиналом. В EN серверный текст после router.refresh согласуется с клиентским. Hydration errors не наблюдались; две dev Fast Refresh full-reload warnings после редактирования сохранены и не названы отсутствующими warnings. `source-download-en.png` и браузерные состояния находятся в reports.

Прежняя матрица добавляла требования NVDA/JAWS, every Modal consumer, exhaustive filesystem audit и физически полный диск каждой OS/DB сверх исходного плана. Они остаются честными ограничениями покрытия; исходные RU/EN keyboard/hydration, unsafe-path tests и infrastructure status gates обязательны. M15 требует честной диагностики и отсутствия заявления о проверенной подписи, не реализации криптографии/расшифровки. Это ruling о границах первоначального плана, не снятие лимитов или security/lifecycle gates.

Оба full suites завершены: Windows2118passed/21skip/89.51%/1619.97s, Linux2120passed/19skip/89.07%/652.52s. Последующая сверка выявила настоящий пробел named MAPI properties, исправленный в v22; текущий итог — [отчёт v22](2026-09-26-mail-final-acceptance-v22.md). Срез v21 остаётся историческим, не выдаётся за проверку нового патча.
