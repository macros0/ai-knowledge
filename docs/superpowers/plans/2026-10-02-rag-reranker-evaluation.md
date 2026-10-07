# RAG Reranker Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Subagent execution is an alternative only if chosen by the user.

**Goal:** проверить выигрыш повторного ранжирования на русском корпусе при сохранении источников и p95 дополнительной задержки не более 3 секунд; получить решение о целесообразности пилота.

**Architecture:** одинаковые итоговые блоки существующего поиска сохраняются в локальный snapshot и воспроизводятся через отдельный модельный worker. Модель переставляет ограниченный префикс списка; API и production retrieval в этом плане не меняются. Baseline и after используют одинаковый исходный пул и фиксированную разметку.

**Tech Stack:** Python, существующие SQLAlchemy/Qdrant/probe, отдельное окружение PyTorch/Transformers для BGE и Qwen3 reranker, JSONL IPC, pytest. Версии и model revisions фиксируются после проверки окружения, до benchmark.

**Spec:** [2026-10-02-rag-reranker-evaluation-design.md](../specs/2026-10-02-rag-reranker-evaluation-design.md).

**Статус 02.10.2026:** эталон 48/1342 проверен и заморожен; development выбрал BGE N80, единственный holdout прошёл ranking gates (+0,0539 nDCG@10, CI [+0,0024; +0,1090], без новых critical top-20 потерь). Парные ответы проверяются. Одиночный scoring и scoring при активной генерации прошли 100 пар; два запроса остаются на 50% coverage из-за `busy`. Пилот не разрешён. Подробности: [отчёт](../reports/2026-10-02-rag-reranker-evaluation.md).

## Global Constraints

- Глоссарий, область поиска, embedding, graph expansion, RRF, фильтры, глубина и генератор фиксированы для парного сравнения.
- Rerank после текущих фильтров и финального среза, до выбора контекста и назначения номеров sources; никаких удалений или порогов score.
- Множество, текст и канонические ссылки источников неизменны; равные оценки сохраняют исходный порядок.
- Два кандидата: BGE v2-m3 и Qwen3 0.6B; N=40/80; 48 вопросов, development/holdout с разделением семейств.
- p95 дополнительной задержки ≤3.0 с; успешный scoring ≥95% в каждом поддерживаемом профиле; fallback отдельно.
- При непрохождении гейтов — оставить выключенным. Новая матрица только с явной гипотезой, без переноса порогов.
- Рабочие API, `.env`, фоновые сервисы и общие backend requirements не менять в ходе offline эксперимента. Развёртывание на другом хосте — отдельное решение.
- Реальные тексты только в игнорируемых артефактах; отчёты без секретов и копий документов.
- Коммит/пуш не входят в текущий запрос на план. Если коммит будет отдельно запрошен: `node scripts/check-project.mjs`, адресный stage, никаких `git add .`.

## Review Focus

- Длинный источник с ответом в конце: окна покрывают хвост, канонический текст не обрезается — задача 3.
- Письма с отрицанием/противоположными решениями: релевантны обе стороны, sender не становится автором — задачи 1 и 5.
- Частичный ответ модели, NaN, таймаут и отмена: никаких частичных перестановок и продолжающегося скрытого worker — задачи 2 и 4.
- Выбранная область и старое поколение документа: канонический snapshot не расширяет область и не смешивает поколения — задачи 1 и 2.
- Отчёт выглядит быстрым из-за fallback или подбора по holdout: успешные/неуспешные запросы разделены, конфигурация заморожена — задачи 4 и 5.

## Структура файлов

Все перечисленные ниже новые файлы создаются только при исполнении плана.

| Файл | Ответственность |
|---|---|
| `backend/test_scripts/reranker_eval/cases.py` | схема разметки, split, проверка полного пула |
| `backend/test_scripts/reranker_eval/snapshot.py` | экспорт контрольных стадий, canonical fingerprints |
| `backend/test_scripts/reranker_eval/ranking.py` | проверка оценок и чистая перестановка |
| `backend/test_scripts/reranker_eval/inputs.py` | окна текста для scoring |
| `backend/test_scripts/reranker_eval/worker.py` | загрузка модели и JSONL протокол |
| `backend/test_scripts/reranker_eval/runner.py` | deadline, admission, завершение процесса |
| `backend/test_scripts/reranker_eval/metrics.py` | nDCG, recall, coverage, latency, решение |
| `backend/test_scripts/reranker_eval/__init__.py` | пакет инструментов эксперимента |
| `backend/test_scripts/benchmark_reranker.py` | CLI capture/replay/report/answers |
| `backend/test_scripts/reranker_eval/requirements.in` | отдельные зависимости worker |
| `backend/test_scripts/reranker_eval/requirements.lock.txt` | проверенные точные версии для выбранной платформы |
| синтетические cases в `backend/tests/test_reranker_eval_*.py` | небольшой синтетический корпус для CI |
| `backend/tests/test_reranker_eval_*.py` | тесты задач без скачивания моделей |
| `tests/artifacts/reranker/` | игнорируемые реальные cases/snapshots/manifest/results |
| `docs/superpowers/reports/2026-10-02-rag-reranker-evaluation.md` | итоговый проверяемый отчёт |

Имеющиеся `probe_sources.py`, `glossary-probe-cases.json`, `benchmark_infotype_reference.py` и fixtures — исходный материал. Не копировать поисковый pipeline как независимую реализацию: переиспользовать API-функции и проверять parity. Не проводить попутную общую реорганизацию retrieval.

## Задача 1. Зафиксировать корпус, эталон и baseline

**Files:** создать `cases.py`, `snapshot.py`, `benchmark_reranker.py`, синтетический fixture и `backend/tests/test_reranker_eval_snapshot.py`.

**Interfaces:**
- `load_cases(path: Path) -> list[dict]`: проверяет `id`, `family_id`, `split`, `query`, `request`, `judgments`, `mandatory_sources`, `required_facts`, `forbidden_claims`, `answerable`.
- `capture_case(case: dict, *, user: User, settings: Settings) -> dict`: содержит `raw`, `merged`, `final`, `manifest`; использует реальную область/locale/mail/glossary семантику.
- Snapshot source key — hash сериализованной canonical identity с source/generation/type/slug/chunk, offsets/digest и kind. При отсутствии доказуемой identity кейс не допускается в сравнение.

- [x] Написать тесты `test_capture_matches_api_final_order`, `test_scope_mail_locale_are_preserved`, `test_generation_change_invalidates_snapshot`, `test_family_cannot_cross_splits`, `test_unjudged_pool_is_rejected`. Сравнивать полный порядок и canonical identity, а не только число источников.
- [x] Из `backend/` запустить `.\.venv\Scripts\python.exe -m pytest tests/test_reranker_eval_snapshot.py -q`; подтвердить падение новых проверок до реализации.
- [x] Реализовать capture через существующие retrieval/hydration/filter helpers. Если нужны raw stages — добавить наблюдение только в harness; parity с API обязателен. Capture read-only, без создания сообщений в рабочей истории. Перед и после capture сверять поколения/состояние корпуса; при изменении повторить capture.
- [x] Подготовить 48 вопросов и разметить исходники по spec. Не принимать текущий порядок поиска за relevance labels. Freeze cases, split, judgments и hashes до сравнения моделей.

  Независимая blind проверка и primary audit всех 1342 кандидатов, positive canonical/raw offsets, 63 mandatory; 48 cases/split/labels frozen до quality replay. Пробел исходной выдачи и потеря факта на scoring входе отмечены отдельно.
- [x] Повторить тесты и `tests/test_probe_sources.py`; проверить, что `git check-ignore tests/artifacts/reranker/snapshot.json` подтверждает игнорирование локальных текстов.

**Готово, когда:** API и replay дают одинаковые контрольные блоки, все оцениваемые кандидаты размечены, baseline воспроизводим. Отсутствующие до rerank источники перечислены отдельно.

## Задача 2. Реализовать перестановку с сохранением источников

**Files:** `ranking.py`, `backend/tests/test_reranker_eval_ranking.py`.

**Interfaces:** `reorder_prefix(blocks: list[dict], scores: list[float], *, top_n: int) -> list[dict]`. Количество scores равно `min(top_n, len(blocks))`; числа конечные; иначе `ValueError`. Возвращает новый список, исходные блоки не мутирует. Сортировка по `(-score, original_position)`, затем нетронутый хвост.

- [x] Написать проверки: для scores `[0.1, 0.9, 0.9]` порядок `[1, 2, 0]`; при N=2 у `[A,B,C,D]` хвост `[C,D]` неизменен; empty/one-element; одинаковые заголовки с разными source IDs не схлопываются.
- [x] Добавить `test_reorder_preserves_canonical_content` и rejection для missing/extra/NaN/Inf scores; сериализованные канонические поля до/после совпадают.
- [x] Запустить `.\.venv\Scripts\python.exe -m pytest tests/test_reranker_eval_ranking.py -q`, подтвердить красный тест, реализовать функцию и повторить до PASS.

**Готово, когда:** операции над рейтингом не могут потерять или переписать доказательство; численные оценки не попадают в текст ответа.

## Задача 3. Подготовка длинных входов и две модели

**Files:** `inputs.py`, `worker.py`, отдельные requirements; `backend/tests/test_reranker_eval_inputs.py`, `backend/tests/test_reranker_eval_worker.py`.

**Interfaces:**
- `make_windows(query: str, block: dict, *, tokenizer, forms: tuple[str, ...]) -> list[str]`: правила окон из spec; canonical block не меняет.
- Worker request: `{request_id, query, blocks: [{index, title, text, forms}], top_n}`; модель выбирается при старте, не содержимым документа.
- Worker response: `{request_id, status, scores: [{index, score}], window_counts, truncated_blocks, elapsed_ms}`; индекс каждого блока ровно один, агрегирование max по окнам.
- Worker после загрузки сообщает `ready` с model/tokenizer revisions и лимитами. Ошибка — ограниченный код, без отражения входного текста в stderr/stdout.

- [x] Написать тесты окон: короткий блок целиком; факт в последней строке; повтор шапки таблицы; полная пара ≤2048 токенов; максимум 4 окна; `mail_fragment` предпочтительнее digest; слишком длинный вопрос даёт контролируемый отказ.
- [ ] Написать stub-тесты BGE logits и Qwen yes/no adapter, сопоставления индексов и prompt-injection текста как данных. Тесты не импортируют тяжёлые зависимости при обычном запуске backend.
- [x] Запустить `.\.venv\Scripts\python.exe -m pytest tests/test_reranker_eval_inputs.py tests/test_reranker_eval_worker.py -q`; подтвердить FAIL и реализовать контракты.
- [ ] Инвентаризировать CPU/RAM/GPU и ресурсы работающего генератора без смены конфигурации. Зафиксировать host, driver, accelerator support и лимит памяти в локальном manifest. Если доступного ресурса нет — зафиксировать ограничение, не освобождать его остановкой чужих сервисов.
- [x] Создать изолированное экспериментальное Python-окружение; зафиксировать совместимые версии PyTorch/Transformers и immutable model revisions. Не включать `trust_remote_code` без отдельной проверки необходимости.
- [x] Запустить маленький model smoke на синтетических русском тексте/кодах, затем повторить тесты. Записать cold load, device, precision и RAM/VRAM. Это проверка работоспособности, не доказательство улучшения поиска.

  Оба CUDA smoke и cold load записаны; GPU tensor peaks измерены, RSS остаётся не измерен (полный memory gate не закрыт).

**Готово, когда:** обе модели оценивают идентичные canonical blocks через зафиксированные политики; различия токенизации/числа окон видны в отчёте.

## Задача 4. Воспроизводимый replay, deadline и нагрузка

**Files:** `runner.py`, `metrics.py`, дополнить CLI; `backend/tests/test_reranker_eval_runner.py`, `backend/tests/test_reranker_eval_metrics.py`.

**Interfaces:**
- `run_rerank(query: str, blocks: list[dict], *, top_n: int, deadline_seconds: float = 3.0) -> dict`: `{blocks, status, reason, elapsed_ms, model_revision, scored_count}`.
- Статусы: `applied`, `busy`, `timeout`, `unavailable`, `invalid_response`, `unsupported_input`; все кроме applied возвращают исходный порядок. Отмена распространяется исключением, не маскируется статусом.
- `evaluate_run(baseline: dict, after: dict, cases: list[dict]) -> dict`: метрики/гейты spec; manifest mismatch блокирует сравнение.
- CLI из `backend/`: `.\.venv\Scripts\python.exe test_scripts/benchmark_reranker.py capture|replay|report|answers --help`. Capture/replay/report не вызывают answer LLM. Для model worker передавать отдельный `--worker-python`.

- [x] Написать реальные subprocess tests с fake worker: зависание, частичный JSON, неверный request_id, missing scores, смерть worker, переполнение ответа, отмена. Подтвердить возврат исходного порядка и отсутствие оставшегося процесса после timeout/cancel.
- [x] Добавить admission test: один активный job, следующий получает busy без очереди; слот не освобождается до остановки прежнего inference. При холодном worker — unavailable до ready, cold readiness измеряется отдельно.
- [x] Добавить тесты метрик на вручную вычисленных nDCG/recall, unknown judgments, no-answer, группе critical, corpus mismatch и разделении applied/fallback. Bootstrap seed=42, 2000 парных выборок по вопросам; CI не подменяет пороги.
- [x] Запустить `.\.venv\Scripts\python.exe -m pytest tests/test_reranker_eval_runner.py tests/test_reranker_eval_metrics.py -q`; после подтверждения FAIL реализовать runner/metrics и повторить до PASS.
- [x] Выполнить development матрицу 2×2. Зафиксировать одну конфигурацию до holdout: сначала проходят гейты корректности/ресурсов, затем максимум nDCG@10; при разнице <0.01 предпочесть меньшую p95 задержку.

  Performance 2×2 выполнена; Qwen N80 исключена до quality по p95 4,224 с. Три допустимые конфигурации сравнены на frozen development с deadline3. Заморожена BGE N80: delta0,1522 против0,1410/0,1256; разница с N40 >0,01. Это выбор кандидата качества, parallel resource gate остаётся failed.
- [ ] Для выбранной конфигурации выполнить ≥100 парных замеров в каждом профиле: одиночный поиск, активная генерация, два поиска. Записать все отказы и memory peaks. Полную генерацию измерять отдельными baseline/after сценариями, не смешивая с replay latency.

  BGE N80: 100 пар одиночного, 100 пар параллельного и 100 пар scoring при активной генерации. Parallel coverage failed50%; остальные scoring gates passed. Full answer load/TTFT/RSS не закрыты. Профиль scoring при генерации не подменяет end-to-end профиль.

**Готово, когда:** есть честная оценка added latency, успешного покрытия и конкуренции; быстрый fallback не позволяет пройти гейт модели.

## Задача 5. Holdout, ответы и решение

**Files:** CLI `answers`, `backend/tests/test_reranker_eval_answers.py`, отчёт, статус настоящего плана и исходной backlog-записи.

**Interfaces:** `compare_answers(case: dict, baseline_blocks: list[dict], ranked_blocks: list[dict], *, generator) -> dict`. Обе ветки используют существующие format/context/batch helpers, одинаковые prompts/model/token budgets и согласованную нумерацию. Live corpus history не изменяется.

- [x] Написать stub-тесты: номера ссылок соответствуют перестановке; fast выбирает контекст в новом порядке; full сохраняет все источники; selected-source путь не rerank; документы/обычный search сохраняют baseline; противоположные письма не исчезают.
- [x] Запустить `.\.venv\Scripts\python.exe -m pytest tests/test_reranker_eval_answers.py -q`; подтвердить FAIL, реализовать harness и повторить до PASS.
- [x] На frozen holdout выполнить один scoring-прогон выбранной конфигурации. Никаких изменений политики после просмотра результата.

  BGE N80, 24/24 applied, delta0,0539 CI[0,0024;0,1090], Recall10/20 вырос, critical_loss=0; canonical set/text100% совпадают. Повторного подбора не было.
- [ ] Сравнить baseline/after ответы на answerable holdout и no-answer кейсах: каждый критический кейс и каждый no-answer — 3 парных повторения; остальные — 1 пара. Порядок веток чередовать, model/sampling одинаковы. По первоисточникам проверить факты, отрицания, авторство, полноту таблиц и все цитаты; судья LLM не единственная проверка.
- [x] Проверить актуальность поколений перед ответами; если корпус изменился — replay маркируется историческим, live выводы и пилот блокируются до нового snapshot. Сохранённый текст не считается доказательством актуального состояния.

  Fingerprint совпал с capture; проверяется повторно до и после каждой пары. Ответы сохраняются отдельно, рабочая история не изменяется.
- [x] Запустить весь новый набор через `.\.venv\Scripts\python.exe -m pytest tests -q -k reranker_eval`, затем связанные suites `test_chat_selected_sources.py`, `test_chat_search_scope.py`, `test_chat_result_depth.py`, `test_chat_answer_modes.py`, `test_glossary_context.py`, `test_retrieval_source_snapshot.py`. Перед отдельно запрошенным коммитом — общий `node scripts/check-project.mjs` из корня.
- [ ] Составить отчёт: фиксированные гейты, обе модели development, одна holdout, CI, critical failures, before/after примеры без текста корпуса, профили latency/memory/fallback, аппаратные ограничения и решение.
- [x] Обновить чекбоксы/статусы плана по фактическим результатам и дать ссылку из backlog. Красный гейт остаётся красным; не объявлять успешный offline результат готовым production релизом.

**Готово, когда:** решение имеет одно из значений «подготовить пилот», «оставить выключенным», «данных недостаточно» с конкретным основанием. Если текущий baseline уже у потолка метрики, это не основание ослаблять гейт — reranker может быть избыточен.

## Следующий этап только при положительном результате

Подготовить отдельный ограниченный план внедрения выбранной модели: процесс/размещение, `off` по умолчанию, deadline/fallback/cancel, интеграция в `chat.py` один раз перед sources, совместимость истории и выбранных источников. Решить отображение score: не показывать новый некалиброванный score как вероятность и не оставлять вводящие в заблуждение проценты старого RRF рядом с новым порядком. Изменения UI локализовать на RU/EN/DE/FR и проверить браузером.

При изменении сетевой топологии обновить `SECURITY.md` в той же поставке. До включения проверить аутентифицированный UI → API → backend, затем off/on/off с восстановлением исходного порядка. Ни внедрение, ни включение не считаются выполненными настоящим планом оценки.
