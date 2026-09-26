# Артефакты приёмки импорта писем

Последняя проверка: [v22](../../../docs/superpowers/reports/2026-09-26-mail-final-acceptance-v22.md). Обязательный native named-map пробел исправлен; результаты прежних версий ниже — история.

- Реальный Outlook Save As MSG: provenance подтверждён владельцем;
  `tests/tmp/mail-outlook-v21-r2/report.json` — текущий supervised parse exit0,
  три источника/два вложения, русский текст без warnings/replacement. Исходник
  и SHA остаются локально. Первый wrapper path failure документирован отдельно;
  старый metadata report не используется как immutable доказательство.

- Финальные контроли авторства: `tests/tmp/mail-authorship-positive-v21/` —
  четыре real GPT-4.1 upload/generation/chat сценария, неизвестный автор XLSX,
  явный автор таблицы DOCX и подписи реплик; 40 Windows/40 Linux scoped tests.
  Два окончательных файла и SHA сохранены отдельно. Полный frozen backend
  `mail-authorship-v21-final` ранее завершён: 2115 passed/19 skipped,
  coverage89.04%; он предшествует последним scoped изменениям.
- Свежие v21 Q01/Q02: `tests/tmp/mail-load-v21/` (100 файлов, 9/9 gates,
  exact SQL/Qdrant audit) и `tests/tmp/mail-q02-v21/` (252 replay cases,
  нет потерь/перестановок, SQL/Qdrant integrity). Детали в
  `docs/superpowers/reports/2026-09-26-mail-v21-load-replay.md`.

- Final review/v21: `tests/tmp/mail-review-v21-final/` —757file SHA snapshot,
  Linux parser385, full backend session98724 завершён с18 stale escaping failures;
  повтор2115/19skip выше. Старый frozen источник и failure evidence сохранены.
  `tests/tmp/mail-clean-v20-final-r2/reports/review-*` —parent RED/GREEN fixes,
  Windows parser384+1skip/frontend190/targeted backend83 unique pass.
  Actual MarkdownViewer5 static renders; browser CID lightbox screenshot in
  v21 reports. `2026-09-26-mail-final-review-fixes.md` records7 findings/scope.

- Clean frozen v20 slice: `tests/tmp/mail-clean-v20-final-r2/reports/` —750file
  manifest/HEAD patch, parser Windows369+1skip/Linux370, frontend clean188,
  production Dockerfile build Node20/npmci, approved runtime audit124/0known.
  Full backend Windows2086/Linux2083 complete, CIcoverage88.95%; final reviewer
  found7 defects. These frozen v20 results are not final v21 evidence.
  Details in `2026-09-26-mail-clean-reproduction.md`.

- v20 U01 images: `tests/tmp/mail-ui-v20/image-keyboard-red.json`,
  `image-lightbox-{focus,return}-red.json`, `keyboard-hydration-verification.json`,
  `image-keyboard-{green,ru-green}.png`, `image-keyboard-{tests,eslint,build}-final.log`:
  actual Tab/Enter/Space/trap/restore RED→GREEN, RU/EN reload/log checks,
  frontend188passed/build/ESLint. Scope limits in UI report.

- v20 L10: `tests/tmp/mail-disk-full-v20/` — real tmpfs ENOSPC RED2fail→GREEN3,
  nested RED2fail→final5passed; `parser-windows-final`369+1skip,
  `parser-linux-final`370, backend Windows98+5skip/Linux95+8skip.
  `platform-coverage.json`:103 unique passed,0 uncovered platform skips.
  Логи/JUnit и initial collection error сохранены; отчёт и ограничения:
  `docs/superpowers/reports/2026-09-26-mail-disk-full-verification.md`.
  Полный backend2084 ниже относится к срезу до этого исправления.

В Git хранится только этот указатель. Пользовательские письма, локальные базы,
ключи и runtime dumps не являются тестовыми fixtures и сюда не добавляются.
Синтетические/лицензированные parser fixtures и их provenance находятся в
`doc-parser/tests/fixtures/`; общий отчёт —
`docs/superpowers/reports/2026-09-25-mail-ingestion-acceptance.md`.

Локальные результаты текущей приёмки (игнорируются Git):

- v20 U01 locale: `tests/tmp/mail-ui-v20/locale-{highlight-green,draft-green,verification}.json`,
  `locale-source-green.png`, `locale-build.log` — browser stale RSC RED→GREEN,
  source/tab/draft/focus retained, keyboard navigation/download exact bytes;
  fresh188 frontend tests/ESLint/build passed. Full accessibility audit pending.
  Дополнение Modal: `modal-focus-red.json`, `modal-{exact,similar,replacement}-focus-final.json`,
  `modal-similar-focus-final.png`, `modal-final-{tests,build}.log` в том же каталоге:
  initial focus/trap/restore RED→GREEN, async preview/autoFocus/replacement checked;
  отмена без новой загрузки или выполнения bulk-операции.

- v20 R04 full shell: `tests/tmp/mail-script-drill-v20/report.json`, source/target
  `live-verification.json`, backup/restore/negative logs — unchanged production
  scripts, Windows baseline→Linux restore→backup→restore, real HTTP/SQL/Qdrant,
  corrupted checksum/held flock rejected. Freshprojects stopped/data retained.
  Frontend inert/development auth; scope/reproduction limits in
  `docs/superpowers/reports/2026-09-26-mail-production-backup-drill.md`.

- v20 A02: `tests/tmp/mail-injection-v20{,-after,-gpt41}/report.json` и
  `transport-prompts.json` — Nemo failed, isolated GPT‑4.1 full4/4;
  `mail-injection-generation-model/report.json` — GPT8/8 exact prompt replay;
  `mail-injection-reminder/report.json` — failed Nemo diagnostic1/4;
  `mail-generation-controls-v20/report.json` — ordinary controls, limitations
  and raw wrapper caveat documented in `2026-09-26-mail-generation-injection.md`.
- v20 A04 extended: `tests/tmp/mail-privacy-chat-v20-final.{log,xml}` —12 Windows;
  `mail-privacy-boundary-linux-v20-final.{log,xml}` —12 privacy+8 boundary Linux.
  `mail-injection-v20-after/boundary-focused.{log,xml}` —90 Windows.
  `mail-final-v20-next/` — full backend2084/16skip/29warnings/exit0,1472.62s;
  code manifest327 unchanged verified, XML/log/verification.json retained.
  `pg-focused.{xml,log}` —15passed (10 original skips+5SQLite controls),
  `platform-complement.{xml,log}` —6Linuxpassed; exact IDs cover all16skips.
  Technical suite does not close failed real-model A02.

- v20 L04: `tests/tmp/mail-adversarial-v20/{windows-http,linux-http}.{log,xml}`
  и per-case JSON в `windows-http/`, `linux-http/` — 7 hostile inputs каждой ОС,
  actual worker/resource samples/API heartbeat/recovery. Production без изменений.
- v20 Q01: `tests/tmp/mail-load-v20/{load-report,index-audit}.json` — все9 gates,
  100 inputs;101 docs/131 sources/131 chunks/121 concepts/252 points, exact IDs.
- v20 A01: `tests/tmp/mail-ui-v20/hostile-{fulltext,scoped-views,storage}-audit.json`,
  `hostile-fulltext.png`, `proxy-canary-requests.jsonl` — EML→MSG, все6 концептов,
  оба чанка/fulltext; positive control1, content requests0 через same-origin proxy.
- v20 privacy: `tests/tmp/mail-v20-final/privacy-boundary-{final,linux-final}.xml`
  — 8 cases на каждой ОС, actual prompts/native MSG Bcc/derived data/parent logs.
  `backend-full.{log,xml}` в том же каталоге — полный v20:2057/16skip, exit0,
  начатый до новых privacy tests; они проверены отдельно на обеих ОС.
- v20 Q02: `tests/tmp/mail-q02-v20/{current-search,comparison,integrity}.json`
  — 252 cases, frozen SQL/Qdrant hashes unchanged. Частный snapshot остаётся
  только в ignored каталоге; внешний LLM не вызывается.
- v20: `tests/tmp/mail-rag-v19-final/parser-v20-{windows,linux}-final.xml` —
  Windows363/1skip, Linux364; `backend-v20-focused.xml` — 59/10skip.
  `backend-full.{log,xml}` — предыдущий полный прогон2056/1failure/16skip,
  PID fixture race исправлена и проверена тремя повторами. Свежий зелёный v20
  полный прогон находится в `mail-v20-final`, см. выше.
- v19 comparison: `tests/tmp/mail-model-comparison-v19/report.json` — 42 synthetic
  ответа и SHA контекстов; `tests/tmp/mail-semantics-v19-gpt41-api/report.json` —
  четыре API replay. Ручная оценка и ограничения:
  `docs/superpowers/reports/2026-09-26-mail-chat-model-comparison.md`.
- v19 RAG: `tests/tmp/mail-rag-v19/backend-full.{log,xml}` — 1 failed/2043 passed,
  `parser-race.xml` — отдельная проверка найденной гонки (итог см. в отчёте).
- v19 P05/S05: `tests/tmp/mail-reparse-v19/{windows.json,linux.json,metadata-windows.xml,metadata-linux.xml}`,
  `corpus/manifest.json`; 15 одинаковых inputs × 2 tempdir × 2 ОС,
  logical IDs/text/links/artifact hashes equal. Verifier: `doc-parser/scripts/probe_mail_reparse.py`.
- v19 M11/S01/S02: `tests/tmp/mail-v19/` — parser Windows/Linux XML,
  backend-presence/resume XML; full backend log/XML (статус смотреть в отчёте).
  `tests/tmp/mail-ui-v19/{audit.json,presence-comparison.png}` — сравнение
  пустого письма, PDF-скана и XLSX, original download bytes.
- v18 CID: `tests/tmp/mail-v18/{parser-windows,parser-linux,backend-focused}.xml`,
  `frontend-final.log`; `tests/tmp/mail-ui-v18/{audit.json,dom-audit.json,cid-images.png}`.
  Реальные MIME/synthetic CFB fixtures, parent/child same-CID isolation,
  local image rendering, byte identity, no-text LLM guard; полный request audit не заявлен.
| Путь от корня | Что проверено / назначение |
|---|---|
| `tests/tmp/mail-v17/{parser-windows,parser-linux,backend-focused}.xml` | HTML links/escaping: Windows 255/1 skip, Linux 256, backend 76/5 POSIX skips |
| `tests/tmp/mail-ui-v17/{audit.json,dom-audit.json,html-links.png}` | Реальный browser: href/query сохранены, literal entity/image/rule/list текст не меняется; CID пока не закрыт |
| `tests/tmp/mail-v16/{parser-windows,parser-linux,backend-focused}.xml` | Unsupported-container v16: Windows 225/1 skip, Linux 226, backend focused 64/5 POSIX skips |
| `tests/tmp/mail-v16/frontend-final.log` | Все 188 frontend tests после export UI manifests |
| `tests/tmp/mail-ui-v16/{audit.json,unsupported-ru.png,unsupported-en.png}` | Synthetic ZIP warning RU/EN, done+partial, 2 sources/1 chunk, UI download bytes равны оригиналу |
| `tests/tmp/mail-v15/migration-final.xml` | 10 tests: реальные SQLite/PG migrations roundtrip + dev additive repair; новые PG database names в properties |
| `tests/tmp/mail-v15/source-tree.xml` | 26 storage/publication/mail tests после direct-edge validation fix |
| `tests/tmp/mail-v15/dev-schema-audit.json` | Read-only оригинальный dev PG: 2 missing cols, 2 nullable conflicts, NULL counts 0/0, applied false |
| `tests/tmp/mail-v15/{parser-windows,backend-focused}.xml` | Parser Windows 206 passed/1 skipped; связанные backend 63 passed после suffix/OLE wrapper fixes |
| `tests/tmp/mail-ui-v15/{source-audit.json,source-tree-ru.png,wrapped-mail-highlight.png}` | Browser XLSX→MSG→XLSX + Packager EML; 4 sources/chunks/concepts, сохранённые spans/hash и видимая подсветка |
| `tests/tmp/mail-v14/{parser-windows,backend-focused}.xml` | Parser Windows 190 passed/1 skipped; связанные backend 63 passed после v14 fixes |
| `tests/tmp/mail-ui-v14/*.png` | EN problem badge, EN/RU RTF reason + Date unknown; synthetic rtf-warning.eml |
| `tests/tmp/mail-ui-v14/logs` | Изолированный backend v14 с Keycloak; исходный корпус не менялся |
| `tests/tmp/mail-v12/backend-{coverage.json,junit.xml}` | Полный backend: 2003 passed, 11 skipped, 89.29% coverage, до узкого OLE cap fix v13 |
| `tests/tmp/mail-chain-v12/report.json` | M10 DOCX→MSG→XLSX: все 6 checks, реальная LLM, BM25/fake8D, synthetic only |
| `tests/tmp/mail-ui-v13/logs` | Изолированный backend v13 с Keycloak, health=ok |
| `tests/tmp/mail-load-v12/{load-report,index-audit}.json` | Q01 текущего canonical layout v12: все 9 gates; 101 docs/131 sources/262 points с точной SQL/Qdrant ownership |
| `tests/tmp/mail-ui-v11/*.png` | Авторизованные браузерные проверки: вложенный EML, DOCX table/header RU/EN, container-only MSG, chat citation → highlight |
| `tests/tmp/mail-ui-v12/ready/logs` | Предыдущий изолированный backend v12 с Keycloak; подпапки сохраняют диагностику запусков |
| `tests/tmp/mail-q02-v10/{snapshot,comparison,verification}.json` | Q02 HEAD/current: 252 cases, точное равенство источников; 38 SQL tables и 7376 points оригинала/копии неизменны. Остальные файлы каталога содержат частный корпус и остаются только локально |
| `tests/tmp/mail-load-v10/load-report.json` | Завершённый Q01 v10: 100 inputs, все девять SLO/resource gates passed; fake LLM/embeddings, ASGI |
| `tests/tmp/mail-load-v10/index-audit.json` | Отдельная read-only сверка: 101 docs, 131 sources/chunks/concepts, 262 points; точное совпадение SQL/Qdrant identities и ownership |
| `tests/tmp/mail-chain-v10/report.json` | M10 DOCX→MSG→XLSX: real LLM, BM25 API search, cited XLSX answer, exact SQL spans, original downloads/export; synthetic only |
| `tests/tmp/mail-http/report.json` | Real LLM, HTTP admission/consent, citations, source ownership, downloads, process restart/resume, trash/restore/export |
| `tests/tmp/mail-http/{generate,continue,restart,audit}.log` | Полные логи этой проверки, включая ожидаемые отказы и исправления harness |
| `tests/tmp/mail-load/manifest.json` | Фиксированные 100 входов 82.579 MiB, 20 controls, повторы и заранее заданные SLO |
| `tests/tmp/mail-load/load-report.json` | Фактические timings/ресурсы/ошибки; только `phase=passed` и все checks=true подтверждают перечисленные gates |
| `tests/tmp/mail-baseline/parser-comparison.json` | HEAD/current ordinary DOCX/PDF, 60 наблюдений на формат/версию, ноль text/block mismatches; до metadata v6/v7 |
| `tests/tmp/repair-runtime/reports/` | Linux source/image hashes, dependency audit, PostgreSQL/Qdrant publication probe |
| `tests/tmp/repair-restore/reports/restore.json` | Проверенный Windows → Linux component backup/restore |
| `tests/tmp/repair-full-coverage.json` | Полный Windows backend coverage предыдущего source snapshot |

`backend/test_scripts/probe_mail_ingestion.py` — небольшой воспроизводимый
parser-only профиль; он не заменяет HTTP load, search regression или real LLM.
Его MIME boundaries фиксированы, повторный запуск создаёт те же bytes/hashes.

`backend/test_scripts/probe_mail_chain.py --output <новая-папка> --name mail_chain_<новый-run>`
воспроизводит M10 на выделенных acceptance PG/Qdrant портах 25432/26333.
Требует настроенную LLM; читает только pinned synthetic MSG, не uploads.
Нельзя указывать существующую БД/папку для перезаписи. Embeddings fake/8D,
поиск BM25; такой отчёт не подтверждает dense quality или browser/Keycloak.

Нагрузочная проверка теперь также воспроизводится из репозитория:

```powershell
backend\.venv\Scripts\python.exe backend/test_scripts/build_mail_load_corpus.py --output tests/tmp/mail-load-reproduced-v10
backend\.venv\Scripts\python.exe backend/test_scripts/probe_mail_load.py --input tests/tmp/mail-load-reproduced-v10 --output tests/tmp/mail-load-v10 --name mail_load_v10
```

Builder и probe не перезаписывают существующий corpus/report/database. Для
повторения используйте новые имена. Builder создаёт 100 входов и 20 controls;
manifest повторного построения совпал с исходным: SHA256
`bd2d2e70d9aaeb58fa4bdac6d30ceb34a5f28def198d29654836598ba4bb5e94`,
86 590 043 bytes входов. Этот hash подтверждает воспроизводимость корпуса,
а не прохождение нового нагрузочного прогона: для последнего нужен завершённый
`load-report.json` с `phase=passed` и всеми checks=true. Probe использует
изолированные PG/Qdrant, fake LLM/embeddings и ASGI HTTP; пороги прежние.
Случайный boundary старого варианта делал сравнение input hashes непригодным;
это закреплено тестом `backend/tests/test_mail_probe.py`.

Ограничения текущих доказательств: HTTP harness использует ASGI TestClient и
simulation auth, а не браузерный Keycloak/Next flow; load использует fake
LLM/embeddings. Сравнение mail flag off/on на DOCX/PDF controls проверяет
накладные расходы флага в текущей версии. Отдельный `mail-baseline` сравнивает
исторический parser, но не качество поиска старого корпуса. Новые бинарные
регрессии MSG v7 выполняются штатными parser/backend tests; они выявили и
исправили потерю NUL bytes в attachment и подстановку parent body в child.
Никакие временные fixtures/скрипты/базы из `tests/tmp` не копировать в CI как
неявные зависимости: сохранять исходную команду, manifest hash и ограничения
в отчёте и воспроизводить в отдельном контуре.


RAG semantics v19: `backend/test_scripts/probe_mail_semantics.py` generates
synthetic S03/S04/S06/S07 and stores the exact context/answer. `--baseline`
replays queries on that isolated corpus without regeneration; output must be
new. Artifacts: `tests/tmp/mail-semantics-v19{,-after,-after2}/report.json`.
`awaiting_semantic_review` is NOT a pass: S04/S06/S07 currently fail semantic
review despite all structural checks. See
`docs/superpowers/reports/2026-09-26-mail-rag-provenance.md`.
