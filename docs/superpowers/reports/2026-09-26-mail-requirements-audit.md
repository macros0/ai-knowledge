# Сверка требований импорта писем с доказательствами

Текущий итог v22: закрыт явный пробел задачи4 — ancestor named MAPI property map; protected body не проходит в LLM/канонические чанки. Windows parser399/1skip, Linux400, backend scope65/5skip и два новых frozen cases. Полный v21 Windows2118/21skip/89.51% и Linux2120/19skip/89.07%; полный Linux v22 exit0,2122pass/19skip/89.06%, пять настоящих ENOSPC cases passed. Код после freeze не менялся, все58 строк матрицы подтверждены в записанном объёме. Согласованный план реализации и приёмки выполнен. [Финальный отчёт v22](2026-09-26-mail-final-acceptance-v22.md). Исторические ожидания и версии ниже сохранены; ориентироваться на строки матрицы и этот итог. Production rollout, рабочий schema drift, mass regenerate и будущий risk/admin gate не выполнялись.

Последнее дополнение: GPT-4.1 для генерации и строгая проверка авторства разрешены и реализованы. Финальные положительные/отрицательные контроли: 40 Windows/40 Linux и четыре реальные синтетические загрузки с GPT-4.1; [проверка авторства](2026-09-26-mail-authorship-verification.md). Полный предыдущий frozen backend2115/19skip/coverage89.04% прошёл; последние изменения авторства проверены отдельно и не выдаются за полный повтор. Свежий v21 Q01: все девять gates и SQL/Qdrant audit пройдены; Q02: 252 сценария без потерь/перестановок и неизменный snapshot — [нагрузка и replay](2026-09-26-mail-v21-load-replay.md). Общая приёмка сохраняет отдельно отмеченные открытые критерии. Предыдущие версии и ожидания решения ниже — история, не текущий статус.

Снимок: 26.09.2026, parser `mail-sources-v20`. Это сверка существующих
результатов с планом, а не новый запуск всех проверок. Последующие исправления
и прогоны должны дополнять этот документ с явной версией.

«Подтверждено» относится к указанному сценарию и среде. «Частично» означает,
что есть полезное доказательство, но не закрыт весь критерий строки плана.
«Открыто» означает отсутствие достаточного доказательства, а не автоматически баг.
Результаты и ограничения прогонов: [отчёт приёмки](2026-09-25-mail-ingestion-acceptance.md).

Дополнение v21: итоговое независимое ревью нашло семь дефектов даже при зелёных
полных v20 suites. Один проход исправлений и новые RED/GREEN результаты —
[отчёт исправлений](2026-09-26-mail-final-review-fixes.md). Mixed MIME, decoding,
native EX sender, warnings/count budget, private attempt cleanup и plain Markdown
image изменены; исторические строки ниже не заменяют новую проверку этих путей.
Windows parser384/1skip, Linux parser385, frontend190 и targeted backend83
уникальных pass; full Linux v21/coverage ещё выполняется. Общая приёмка открыта.

## Форматы и вложения

| ID | Статус | Доказательство / оставшийся вопрос |
|---|---|---|
| M01 | Подтверждено | test_mail_parser: UTF-8, CP1251, KOI8, folded headers, RFC2231; полные parser suites Windows/Linux v14 |
| M02 | Подтверждено для fixtures | alternative без дублирования body, nested EML/rfc822, multipart walk; полный произвольный MIME не обещается |
| M03 | Подтверждено для fixtures v18 | HTML tables/safe links + 26 CID cases EML/MSG: PNG/JPEG/GIF/WebP, без filename, parent/child/sibling isolation, duplicates, SVG/spoof/bomb/limits. Browser 362ec942c83740f2: red root дважды, blue MSG, только local img; missing CID warning. Pipeline image-only не вызывает LLM. Полный browser request audit остаётся A01 |
| M04 | Подтверждено в проверенном объёме | Public CFB Unicode/ANSI и synthetic Cyrillic ранее проверены. Владелец подтвердил direct Outlook Save As MSG; текущий v21 локальный supervised repeat exit0: русский текст, три источника/два вложения, нет replacement/warnings. Частный файл остаётся вне Git/CI/LLM; версия Outlook неизвестна. См. mail-outlook-export-verification |
| M05 | Подтверждено как unsupported | Настоящие compressed/uncompressed RTF-only streams, тест запрещает их чтение; nested reason виден RU/EN; конвертация RTF не заявляется |
| M06 | Подтверждено для synthetic v15 | Пропуск Package/Ole10Native воспроизведён (8 RED), исправлен: 8 valid + 3 malformed CFB tests, payload bytes точны. Реальные разновидности Office-экспорта сверх этих layouts не обещаются |
| M07 | Подтверждено для fixtures | DOCX table/nested table/header/story tests, v13 OLE node admission; RU/EN browser evidence v11 |
| M08 | Подтверждено для fixtures v22 | Native embedded MSG substorage сохраняет собственные значения; общая named-property карта корня используется дочерним MSG, child-local карта не подменяет её. Full parser Windows399+1skip/Linux400; API/browser container download |
| M09 | Подтверждено для fixtures v15 | XLSX .eml/.msg/.xlsx/.BIN/без расширения: 5 RED → 5 GREEN, directory skipped, bytes/source IDs проверены; PDF EML и container limit/CRC/EF tests проходят |
| M10 | Подтверждено свежим v21 end-to-end | mail-chain-v21-final/report.json: 6/6, DOCX→MSG→XLSX, real GPT-4.1, BM25/fake8D, SQL source ownership, original downloads, exact canonical spans, cited answer и OKF export. Не проверка dense quality |
| M11 | Подтверждено для synthetic v19 | test_mail_depth_chain: реальный CFB/MIME/XLSX, EML→MSG→XLSX допускается, MSG→EML→MSG→XLSX останавливается на depth3 до чтения bytes. Исходный MSG сохранён, skipped_depth/warning проверены. RED1 → GREEN2, Windows/Linux suites |
| M12 | Подтверждено для linked OLE | DOCX location tests, включая external resolver и node cap; не выдумываются bytes linked target. Скриншот/иконка не является mail fixture |
| M13 | Подтверждено для сигнатур v16 | 13 RED → GREEN: CFB DOC/XLS, ZIP, сигнатурные PST/OST/TNEF, переименование, bytes/sibling/no-dir/limits. Pipeline сохраняет warning/status/problem/manifest; browser ZIP reason RU/EN. Исторические варианты и ZIP с префиксом не проверены |
| M14 | Подтверждено для fixtures | .bin RFC822, spoofed .msg opaque, не-MAPI CFB root отклонён; type определяется bytes |
| M15 | Подтверждено диагностическое поведение v22 | Native CFB SMIME/IRM/calendar, EML cases и named PidNameContentClass без transport headers. Root IRM preflight422; nested IRM body не индексируется, предупреждение на child. Windows399+1skip/Linux400 parser, linked backend65+5skip и2 native-map pipeline cases, authenticated browser. signature_verified=false; расшифровка/криптографическая проверка не заявляются. См. mail-final-acceptance-v22 |

## Смысл и метаданные

| ID | Статус | Доказательство / оставшийся вопрос |
|---|---|---|
| S01 | Подтверждено для fixtures v19 | test_mail_content_presence: EML/MSG с From и длинной темой, тело пустое; LLM не вызывается, no_text_layer, canonical subject сохранён. Fresh + resume после publication failure; live subject-only-v19.eml 93e54907342e4295 |
| S02 | Подтверждено для fixtures v19 | 12 tests EML/MSG × empty/scan/XLSX × fresh/resume; scan no_text_layer без LLM, XLSX концепт только root/0. Browser scan eb1e2e6755c046b8 (0 concepts), table 095786dd64cf489e (1 concept, 18 days); original PDF/XLSX bytes совпадают, PDF скачан через UI |
| S03 | Подтверждено для synthetic case v19 | real LLM/BM25 probe: «Согласовано» + quoted proposal даёт 18 дней до/после. mail-semantics-v19 и after2/report.json; не общая гарантия |
| S04 | Подтверждено с GPT-4.1 на synthetic и browser | Два повтора, API replay и свежий browser не выдумывают отмену, явная отмена распознаётся в обоих положительных контролях. Mini ошибается в обоих повторах. См. mail-chat-model-comparison report |
| S05 | Подтверждено для fixtures v19 | 11 metadata cases Windows/Linux: missing/malformed date, timezone/FILETIME precedence, unknown sender; native CFB EX recipient сохраняет raw EX address без выдуманного SMTP. Browser Date unknown RU/EN ранее проверен; реальный сервер Exchange для разрешения адресов не используется |
| S06 | Подтверждено строгой проверкой v21 | Последний real GPT-4.1 upload/API сохраняет 22 дня/электронный акт и unknown у неподписанных уточнений; две явные подписи Ольги показаны как обозначения реплик. Канонические SQL источники и 40 Windows/40 Linux linked tests; browser unsigned case ранее проверен. Не универсальная атрибуция всех парафраз |
| S07 | Подтверждено строгой проверкой v21 | Неподписанная XLSX: неизвестный автор, 14 дней, sender Petr не публикуется как автор; explicit «Автор таблицы: Ольга Смирнова.» прочитан из DOCX root/0. Real upload/GPT-4.1/API positive+negative, HTTP/history regression и свежий authenticated browser. Прежний model-only false attribution сохранён в историческом отчёте |

## Лимиты и сбои

| ID | Статус | Доказательство / оставшийся вопрос |
|---|---|---|
| L01 | Подтверждено для границ fixtures v20 | EML/MSG/DOCX/XLSX/PDF: child depth1/2 допускается, depth3 не декодируется; Office/PDF сохраняют ограниченный оригинал с warning. Матрица использует входной depth; физические цепочки отдельно v19. Узлы2/3/4 и Unicode9/10/11 проверены на уменьшенных лимитах, не нагрузка максимального корпуса |
| L02 | Подтверждено для границ fixtures v20 | PDF actual9/10/11 × declared0/1/10/11/12 при cap10: ложный меньший Size не обходит лимит; больший отклоняется консервативно. Общий actual budget9/10/11 для EML/DOCX/XLSX/PDF; MSG metadata accounting отдельно test_msg_limits. Эти случаи не исчерпывают произвольные повреждённые контейнеры |
| L03 | Подтверждено для веток | MIME/MSG node cap, v13 DOCX admission, v14 XLSX/PDF до чтения и один remainder. Свежая нагрузка Q01 — v20 |
| L04 | Подтверждено для adversarial матрицы v20 | 7 real spawn cases каждой ОС: base64 valid/malformed51MiB, HTML>2M chars, RTF declared1GiB, ZIP ratio>1000 root/child,1500 MIME. Во время worker API200, следующий parse успешен, live child/partial error artifacts отсутствуют. RSS/time/CPU/retained bytes измерены; не peak disk и не exhaustive arbitrary malformed corpus |
| L05 | Подтверждено для fixtures | MIME/MSG/raw nested shared budget и container tests v14; budget не создаётся заново для child |
| L06 | Подтверждено для предусмотренных случаев | Parser path/collision/symlink/reparse tests плюс отдельные текущие Windows/Linux probes 14 имён: traversal/absolute/UNC/drive-relative/ADS/CON/NUL/NUL-byte/bidi/long Unicode/repeated names, 14 отдельных ASCII targets без escape/overwrite. Два настоящих Windows NTFS junction на destination/ancestor дают skipped_storage и пустой target. Это не exhaustive filesystem audit |
| L07 | Подтверждено для fixtures | MSG child properties, embedded pipeline, v14 реальный XLSX CRC/PDF EF; sibling сохраняется; RTF warning показан в браузере |
| L08 | Подтверждено для harness | Supervisor spawn/time/memory/output/cleanup, upload admission без ghost document; OS isolation failure отказывает закрыто |
| L09 | Подтверждено v12 | Два real spawned workers, третий Busy, slots освобождены; Linux 20 passed/8 Windows-only skips; нагрузка без orphan |
| L10 | Подтверждено предусмотренное поведение отказов | Full Linux v21: пять реальных ENOSPC cases на отдельном8MiBtmpfs, upload507/no ghost, EML/MSG spawn cleanup/retry, regenerate сохраняет старое поколение/resume. Injected Windows/Linux FS, PostgreSQL53100/SQLite13, SQL publication rollback и Qdrant отказ не дают ложный done. Native full Windows disk и физическое заполнение DB/Qdrant отдельно не проверены; это граница покрытия, а не требование исходного плана |

## Дубли, позиции и права

| ID | Статус | Доказательство / оставшийся вопрос |
|---|---|---|
| D01 | Подтверждено | Upload/dedup tests и HTTP acceptance: exact duplicate contract сохранён |
| D02 | Подтверждено для fixtures | Реальный MSG/equivalent EML fingerprint test + HTTP similar consent; не hard duplicate по одному Message-ID |
| D03 | Подтверждено | Message-ID/body/attachment multiplicity/order tests |
| D04 | Подтверждено для synthetic pipeline | test_mail_duplicate_admission: тот же MSG standalone и в настоящем DOCX OLE, upload consent + публикация, два doc_id/root filenames, концепты root и root/0, вложенные bytes равны оригиналу. LLM/Qdrant заменены test doubles; parse/SQL/FS/publication реальные |
| D05 | Подтверждено для ASGI/SQLite harness | 8 EML/MSG × exact/semantic × concurrent/trash cases: barrier обоих parses, задержка публикации подписи, ровно один admission и 409 duplicate/similar; trash twin не блокирует. Cross-process PostgreSQL lock этим набором отдельно не проверен |
| P01 | Подтверждено | pipeline source ownership, substorage и API chunk-source mapping |
| P02 | Подтверждено | Повторная/неоднозначная quote отвергается; lexical inferred, не exact |
| P03 | Подтверждено для fixtures | Unicode/emoji/hash/whitespace/CRLF source-location tests, browser highlights v11 |
| P04 | Подтверждено | Legacy/recovered/stale/malformed/missing-source fallbacks, три пользовательских false-positive cases |
| P05 | Подтверждено свежим v22 | probe_mail_reparse.py:15 неизменных inputs, по два разных каталога Windows/Linux, одинаковые source tree/metadata/warnings/text/links/emitted hashes. reparse-windows.json/reparse-linux.json в mail-final-acceptance-v22-r2/reports, cross_platform_equal=true, differing_files=[] |
| A01 | Подтверждено на synthetic browser cases | v20 hostile HTML: fulltext, оба чанка и6 концептов без активного содержимого, local PNG работают; canary positive control/0 pixels. v21 plain EML+MSG: fulltext,2 chunks,2 concepts,0 img/embedded active elements и0 root/child server pixel requests. v21 control origin отдельно не перепроверялся; не exhaustive external-network audit |
| A02 | Подтверждено на проверенном наборе с разрешённой GPT-4.1 | Runtime generation/chat GPT-4.1 применены по разрешению пользователя. Последний v21 real generation/chat body/delimiters/generation/nested 4/4: факт23days сохранён, canary не исполнен, source только user-role, system без canary. Немногочисленный synthetic набор не гарантирует стойкости на всех инструкциях; Nemo failures и будущий risk/admin backlog документированы |
| A03 | Подтверждено для API cases | Anonymous/role/trashed/root download authz tests и source API tests |
| A04 | Подтверждено для расширенной матрицы v20 | 12 Windows +12 Linux: EML/MSG/native Bcc, normal/TNEF/fresh/resume, final HTTP chat200/503, actual provider inputs/history/logs/stdout/stderr, malformed CFB root/child и worker deadline. Private headers отсутствуют в derived data; raw originals сохраняют headers. Не exhaustive arbitrary library/provider exception corpus |

## Жизненный цикл и эксплуатация

| ID | Статус | Доказательство / оставшийся вопрос |
|---|---|---|
| R01 | Подтверждено | Parser/source hash/version mismatch, checkpoint/publication tests, HTTP restart/resume; v14 связанные 63 tests |
| R02 | Подтверждено для harness | generation deletion/publication/cleanup/search tests; старое active generation не смешивается с новым |
| R03 | Подтверждено для harness | Source cascade и generation lifecycle tests, HTTP trash/restore/export |
| R04 | Подтверждено для synthetic production shell drill | Windows backup→штатный restore в новый Linux project/data→штатный backup→ещё новый restore. Strict integrity и real HTTP/SQL/Qdrant:5 hashes/3 sources/chunks/locations/6 IDs unchanged. Corrupt checksum/lock отказ до target mutation/writer restart. Frontend inert/auth disabled, не production deployment; см. mail-production-backup-drill report |
| R05 | Подтверждено для fixtures | Original-byte tests, DB export source tree, HTTP/chain export и restore downloads |
| R06 | Подтверждено в изолированных БД | 10 real migration/repair tests SQLite+PG: empty/populated roundtrip, legacy null-source compatibility, полный schema diff целевых таблиц, PK/FK/cascade/reconnect, additive dev repair/no-stamp/no-op/conflict refusal. Ещё 26 storage/publication tests; invalid-parent KeyError исправлен. Live dev audit read-only, применение не входит в текущую приёмку; фактический drift и процедура записаны в MAIL_IMPORT.md |
| R07 | Подтверждено в tests | Disabled embedded import сохраняет parent/original, старые sources читаются; локальный flag по просьбе пользователя НЕ выключается |
| R08 | Подтверждено | Parse diagnostic code/status и sourceTree locale tests; v14 live RU/EN reason |
| U01 | Подтверждено для предусмотренных RU/EN keyboard/hydration cases | v20 Tab upload/list/concepts/sources, image Enter/Space, Tab trap, Escape/Close возвращают focus. v21 download accessible names содержат файл/source RU/EN; Enter скачал точный pinned MSG контейнер, сервер/клиент EN после refresh согласованы.190 frontend tests обеOS, ESLint0errors/build passed. Hydration errors нет;2 dev FastRefresh warnings записаны. NVDA/JAWS и every Modal consumer не проверены, не добавляются к исходному обязательному scope |
| U02 | Подтверждено | API и browser container-only download nested-rtf.msg |
| Q01 | Подтверждено свежим прогоном v21 | 100 inputs 86,590,043 bytes, 9/9 gates; ordinary parse p95 .582s, sampled RSS110.16MiB, search p95 ratio .3301; DOCX/PDF ratios1.0549/1.0097. Real ASGI/PG/Qdrant/spawn, fake LLM/8D. Отдельный exact audit101docs/131sources/131chunks/121concepts/252points. Не latency внешнего LLM |
| Q02 | Подтверждено свежим replay v21 | 252 scenarios/32 cached vectors: нет потерь/новых источников/перестановок. После replay38 SQL tables/Qdrant integrity SHA unchanged. Только локальный frozen snapshot, без запросов к исходным хранилищам/LLM; не latency benchmark |

## Очерёдность оставшейся работы

1. M06/M09 исправлены в v15: Windows parser 206 passed/1 skipped,
   Linux 207 passed, связанные backend 63 passed. Scoped review без новых
   воспроизведённых регрессий, независимый byte probe 9 сочетаний
   EML/MSG/PDF × Package/native/Packager. Браузерный v15 прогон фиксируется
   в общем отчёте отдельно от этих unit/integration результатов.
2. R06 закрыт указанными migration/repair tests и negative tree cases; исходный
   dev-кластер остаётся с обнаруженным drift до отдельного обслуживания.
3. Разделить намеренные ограничения продукта (RTF, CID, legacy containers,
   криптография) и обещанные, но ещё не доказанные сценарии; согласование нужно
   только там, где меняется утверждённая поддержка, а не для обычной проверки.
4. Дополнить смысловые RAG cases и resource boundaries из открытых строк,
   затем один итоговый regression/load прогон финальной версии.
5. Финальный scoped review, проверка чистого воспроизведения и обновление
   общего отчёта. Не объявлять production readiness по одному зелёному suite.
