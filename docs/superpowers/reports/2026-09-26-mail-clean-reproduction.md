# Проверка зафиксированного среза без локальной конфигурации

26.09.2026. Оба полных backend прогона и независимое ревью завершены.
Этот отчёт относится к frozen v20 и не означает завершения общей приёмки:
ревью выявило семь дефектов, исправляемых в v21 отдельным проходом.

## Срез и окружение

Из shared main HEAD `51c453f6d7d3adc0e759aea9fd3dd3e48c47b485` экспортированы
750 tracked/nonignored untracked файлов с текущими изменениями. SHA256 каждого
исходного и скопированного файла сравнен. В срезе отсутствуют `.git`, `.env`,
`.venv`, `node_modules`. Это экспорт незакоммиченной рабочей копии, не проверка
клонирования опубликованного коммита. Первый экспорт отказал до копирования
из-за сравнения разных Windows разделителей; исправленный экспорт отдельный.

Локальные артефакты: `tests/tmp/mail-clean-v20-final-r2/reports/`, исходники
рядом в `source/`. Manifest750, tracked HEAD patch/stat сохранены; untracked
исходники есть в source и manifest, обычный git diff их не включает.
Patch SHA256 `918fe00cb3551b9b80d47d16c79272a62ba9f85e34a4b0a62991f4af8c325e16`.

Windows backend использует установленный venv: импорты app/docparser из нового
source подтверждены через __file__. Это не новая установка Python dependencies.
Linux использует подготовленный `okf-mail-verify-tests:20260926-fonts`, Python3.12,
pytest9.1.1, pytest-cov и DejaVu; сеть выключена, UID1000, source read-only.
Полный backend получает отдельный tmpfs для default runtime data и pytest.

Frontend построен штатным Dockerfile target `build` из snapshot/frontend:
Node20-alpine, фактические `npm ci` и `npm run build`, без host node_modules/env.
Образ `okf-mail-frontend-clean-v20:20260926`; сборка exit0. Это сборка, не deployment.

## Завершённые проверки

- Linux полный backend:2083passed/24skipped/322warnings,633.57s,exit0.
  Фактическое покрытие88.95%, CI gate85% пройден. Лог
  `backend-linux-full-r2.log`, JUnit `backend-linux-full-r2.xml`,
  coverage `coverage-linux-r2.xml`. Пропуски не считаются пройденными тестами;
  этот результат относится к frozen v20, а не будущим исправлениям ревью.
- Windows полный backend:2086passed/21skipped/29warnings,1507.72s,exit0;
  `backend-windows-full.log` и `backend-windows-full.xml`. Также frozen v20.
- Linux полный parser:370passed,6.16s,exit0.
- Windows полный parser:369passed/1POSIX skip,10.72s,exit0.
- Windows Node suite без node_modules в срезе:188passed,546ms,exit0.
- Frontend Linux Docker build:exit0, лог `frontend-linux-build.log`.
- Linux Node20 полный frontend suite:188passed,628ms,exit0;
  `frontend-linux-tests-r2.log`. Первоначальный frontend-only image прогон
  имел186pass/2fail: tests116/117 не находили соседние backend UI manifests.
  Повтор добавил read-only snapshot/backend mount, как в полной структуре repo;
  продуктовые файлы и тесты не менялись, исходный отказ сохранён.
- Linux полный ESLint:exit0,0errors/36warnings. Предупреждения существующих
  React hooks/anonymous exports сохранены в `frontend-linux-eslint.log`.
- Runtime `pip check`: `No broken requirements found`,exit0.
- Разрешённый `pip-audit --local`:124 dependencies,0 известных уязвимых,
  exit0. Локальный okf-doc-parser0.1.0 пропущен как отсутствующий на PyPI;
  его опубликованные зависимости присутствуют в аудите. Это не анализ
  собственного исходного кода. JSON и stderr сохранены.

## В процессе / исходные отказы

Оба полных прогона завершены, результаты приведены выше.
Первые Linux37 collection errors относятся к запрету записи default data_dir
в read-only source mount, до выполнения тестов. Исходный лог/XML сохранён;
повтор использует отдельный64MiB tmpfs `/workspace/data`, не меняет app code.

Независимый единственный final reviewer получил frozen source, HEAD patch,
plan/spec/ledger и ограничения A02/S07. Итог: нужны исправления одного P1
и шести P2. Семь воспроизведений проверены родителем; исправления и новые
результаты описаны в `2026-09-26-mail-final-review-fixes.md`.
Linux frontend tests/полный ESLint после build завершены отдельно выше.
Зелёные тесты v20 не закрывают найденные пробелы и не переносятся на v21.

При экспорте operator `MAIL_IMPORT.md` был обновлён: regenerate сохраняет старое
поколение до переключения, ENOSPC paused/storage_full, parser v20. После ревью
product code рабочей копии изменён до v21; immutable snapshot остаётся v20.
Рабочие модели/mail flag/data/services неизменны;
commit/push/deploy не выполнялись. A02 и S07 требуют решения пользователя,
M04 provenance и оставшиеся критерии не закрываются чистотой pytest.
