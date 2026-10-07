# Общие модельные профили Kubernetes

Процедуры соответствуют ветке `codex/kubernetes-rollout`; runtime изменения
ещё не интегрированы в `main`. Копия этого документа в основном checkout
не означает наличия там новых тестов или реализации.

Этот репозиторий содержит переносимые chart, tools, профили и тесты.
Домены, credentials, registry/Vault references, runner policies и подключение
конкретного GitLab/кластера настраиваются в отдельной окруженческой копии.
Профили DEV/TEST/PROD — примеры разделения сред, а не требование создавать
три кластера для любой установки.

## Проверяемый контракт

| Профиль | Режимы чата | Источник бюджета | Ограничение |
| --- | --- | --- | --- |
| HOME/CI/DEV/TEST, stub | `documents`, `fast`, `full` | `/props`, `/apply-template`, `/tokenize` тестового сервера | Синтетический транспорт/схема, без проверки качества модели |
| Live `local_qwen` | `fast/full` требуют успешного счётчика | API template/tokenizer того же модельного сервера | Контракт должен соответствовать выбранной модели; реальная точность проверяется в окружении |
| Live `standard` или неподдерживаемый provider | `documents`; для `fast/full` явный отказ | Подходящего счётчика в текущей реализации нет | `chat_budget_unavailable`, без оценки длины по символам |
| PROD | Только live, fake embeddings запрещены | Реальный model-matched counter | Маркированный synthetic budget отклоняется |

`documents` не вызывает LLM или его tokenizer. Поиск всё ещё зависит от
хранилищ и выбранных веток: dense/hybrid требует работающего embedding provider;
проверка без моделей использует BM25 по заранее индексированным данным.
Обычный чат без `response_mode` сохраняет прежний контракт; таблица относится
к tracked режимам `documents/fast/full`.

## Stub

Chart для `models.mode=stub` закрепляет в явном env контейнера backend:

```text
MODEL_MODE=stub
LLM_PROFILE=local_qwen
LLM_BASE_URL=http://model-stub:8000/v1
LLM_MODEL=openai/test-stub
LLM_CHAT_MODEL=openai/test-stub
SEARCH_MODE_DEFAULT=bm25
EMBEDDING_PROVIDER=fake
```

Явный env имеет приоритет над runtime ConfigMap и Secret. `local_qwen` здесь —
существующий HTTP-адаптер, не название настоящей модели.
Stub `/props` сообщает тестовое окно 32768; `/apply-template` сериализует
полный system/user prompt; `/tokenize` выдаёт детерминированный тестовый счётчик.
Ответы этих endpoints имеют `test_only=true`. Их можно использовать только
при `MODEL_MODE=stub` вне PROD; live-запрос получает отказ до генерации.
Не подключайте эти endpoints к PROD и не считайте их доказательством точности
токенизации или качества RAG.

`full` с несколькими частями использует фиксированную synthetic цитату
«ТЕСТОВЫЕ данные для проверки развёртывания.». Stub возвращает проверяемый
fact только при наличии этой строки в соответствующем context block; synthesis
ссылается на эти же глобальные номера. Произвольные документы этим механизмом
не анализируются.

## Fail-closed проверки и отмена

Context window должен быть положительным integer, template — непустой строкой,
tokens — непустым списком неотрицательных integer IDs. Ошибка HTTP, неверная
структура, отсутствующий tokenizer или неподдерживаемый профиль дают
`chat_budget_unavailable`; никаких приблизительных counters не подставляется.
В бюджет включается полный prompt, выходной лимит и прежний резерв 256 tokens.

Для отрицательной проверки только тестового сервера можно задать
`MODEL_STUB_TOKENIZER_SCENARIO=missing`: `/tokenize` ответит 404. Production
application image не включает stub и эту настройку не использует.
Отмена tracked attempt сохраняет `stopped`, пустой ответ и уже найденные
источники. Поздний ответ модели не переводит attempt в `completed`.

## Локальная матрица

```bash
cd backend
python -m pytest tests/test_kubernetes_chat_modes.py tests/test_chat_token_budget.py -q
```

Это real HTTP/SSE stub + настоящий `LLMClient` и `ChatTokenBudget`, isolated
SQLite и synthetic retrieval. Идентичность пользователя задаёт test override;
проверка не является приёмкой SSO, Gateway, Qdrant или целевого кластера.
Неизвестный live профиль требует отдельной спецификации адаптера и измерений
с настоящим template/tokenizer, а не снятия guard ради зелёного запуска.
