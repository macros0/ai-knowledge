# Исправления по итоговому ревью импорта писем

Последующее дополнение: пользователь разрешил GPT-4.1 generation и strict authorship, решения реализованы и проверены на synthetic API/browser. Full v21 session98724 завершился18staleescaping assertions; исправленные expectations проверены targeted. Новый759-file full backend52398 выполняется. Текущий статус: [отчёт авторства](2026-09-26-mail-authorship-verification.md). Остальной текст ниже фиксирует предыдущий проход.

26.09.2026. Один независимый reviewer проверил frozen v20:750 файлов с
совпавшими SHA, tracked diff и новые untracked файлы. Вердикт: нужны исправления.
Один проход исправлений выполняется в текущей рабочей копии; повторное ревью
не запрашивалось. Это не завершение всей приёмки и не разрешение deployment.

## Подтверждённые дефекты и изменения

1. P1: plain EML/MSG переносили буквальный Markdown image в canonical text;
   ReactMarkdown создавал внешний img. Plain mail теперь экранируется;
   MarkdownViewer разрешает только отдельный attachment текущего документа.
   Внешние/data/absolute/traversal URL не создают img. CID проекция сохраняется.
2. P2: multipart/mixed терял вторую независимую body-часть. Независимые части
   объединяются по MIME-порядку; alternative выбирает одно представление.
   Различие упорядоченных слов/чисел даёт mail_alternative_mismatch; это
   диагностическое сравнение, не доказательство семантического равенства.
3. P2: повреждённые EML/MSG байты заменялись на U+FFFD без предупреждения.
   Строгое декодирование обнаруживает восстановление: mail_decode_recovered.
4. P2: заранее созданный pipeline attempt сохранялся после parser failure
   или отказа checkpoint. TemporaryDirectory принадлежит одной попытке до
   перемещения в durable attachments; все отказные выходы очищают только его.
   Supervisor по-прежнему не удаляет произвольный каталог, созданный вызывающим.
5. P2: шесть центральных budget rejection веток не записывали warnings.
   Теперь count/size причины сохраняются на зарегистрированном source;
   SQL document.problem отражает attachment_partial_result.
6. P2: после MSG node cap создавался source для каждого остатка. Список handles
   ограничен допустимым числом плюс один sentinel; обход завершается одним
   attachment_count_marker, включая расход общего бюджета потомками.
7. P2: native MSG без transport headers терял EX-отправителя. Читаются native
   sender свойства; EX-адрес остаётся буквальным. Representing sender хранится
   отдельно, не подменяет sender и не доказывает авторство вложения (S07).

Для body corruption/mismatch введён отдельный problem mail_text_partial_result
с RU/EN объяснением; прежний text_partial_result по-прежнему означает text cap.
Source warning labels также переведены; новые ключи включены в backend manifests.

## Проверки текущего прохода

- RED: remote plain EML/MSG, потеря mixed body, MSG count cap; исправлен
  ошибочный вызов parse_document_result(budget=...) в новом тесте до зачёта RED.
- RED:6 отсутствующих budget warnings; encoding EML/MSG и native EX sender;
  alternative mismatch, pipeline parse/checkpoint cleanup, diagnostic labels.
- GREEN:15 новых parser cases; затем полный Windows parser384passed/1POSIXskip,
  8.78s. Первые3 регрессии нового прохода (malformed recipient=None и старое
  ожидание неэкранированных brackets) исправлены; исходный лог сохранён.
- GREEN: backend83 уникальных cases:82passed/5skipped в связанном suite,
  плюс отдельный новый real-spawn failure case. Полный повтор объединённого
  набора на Linux и окончательный свежий срез ещё не выполнены.
- Реальный spawned worker дважды сохраняет partial.bin и возвращает ошибку:
 3 cleanup tests passed,19.14s; предыдущая публикация побайтно сохранна.
- SQL publication diagnostics: кодировка/alternative/late central size rejection
  сохраняют warnings/problem; fake LLM, memory Qdrant, isolated relational DB.
- Frontend190passed; scoped ESLint0errors/3existingwarnings; Next build exit0.
- Пять actual MarkdownViewer static renders: remote/relative/traversal/escaped
  text не создают img; local CID projection создаёт только same-document URL.
  Локальный SWC первый запуск требовал loadBindings; исправлен стенд. Сетевых
  запросов нет; это не браузерная проверка lightbox/сети после v21.
- Browser session истекла. Переход по штатному login redirect заблокирован
  автоматической проверкой разрешений для localhost:18081; обход не выполнялся.
  Пользователь явно разрешил этот Keycloak origin; обычный redirect восстановил
  demo.admin без ввода credentials. На прежнем synthetic HTML документе оба
  same-document CID изображения загружены (naturalWidth>0). EML Enter открывает
  dialog; Tab удерживается на Close; Escape возвращает фокус на EML button.
  Screenshot: tests/tmp/mail-review-v21-final/reports/cid-lightbox-v21.png.
  Это positive CID/browser regression, не новый plain tracking network canary.
- Scoped Ruff E4/E7/E9/F/I для изменённых parser/diagnostics/newtests clean;
  pipeline E4/E7/E9/F clean. Его существующий несортированный import block
  не переписан в этом проходе.

Логи RED/GREEN текущего прохода: tests/tmp/mail-clean-v20-final-r2/reports/;
исходный immutable source рядом остаётся v20 и не изменён.

Новый SHA-verified срез:757 файлов в tests/tmp/mail-review-v21-final/source,
без .env/.venv/node_modules/.git. Linux полный parser385passed,6.07s,exit0.
Полный Linux backend с CI85% coverage и FULL_STORAGE_TEST_ROOT на отдельном
8MiB tmpfs выполняется: session98724; source read-only, networknone, UID1000.
До terminal/JUnit/coverage его результат не считается зелёным. Первоначальный
доступ к Docker был недоступен в sandbox; штатная escalation разрешена.

## Версия и открытые критерии

PARSER_VERSION=mail-sources-v21: canonical text, warnings/metadata и дерево
изменились. Старый checkpoint требует целевой перегенерации; его нельзя
смешивать с новым layout. Автоматическая или массовая перегенерация не запускалась.
Полные v20 Windows2086/Linux2083 и CI coverage88.95% не являются полными
результатами v21. Требуется свежая независимая от .env проверка v21.

A02 генерация Nemo и S07 attribution ждут решения пользователя. Модель
генерации, mail flag, рабочие документы/БД/сервисы не менялись; commit/push/deploy
не выполнялись. Future risk/admin gate остаётся backlog. M04 provenance,
screen reader/first-visit и другие частичные критерии сохраняют свои ограничения.
