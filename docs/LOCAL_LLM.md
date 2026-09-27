# Локальная Qwen через llama.cpp

Профиль включается настройкой `LLM_PROFILE=local_qwen`. Он задаёт отдельные
JSON-схемы для OKF, таблиц, определения разработки и переводов, отключает thinking
и включает очередь с одним одновременно выполняемым запросом. Чат получает
приоритет между вызовами; текущая генерация не прерывается.
Очередь общая для одного процесса: запускайте backend с одним uvicorn worker.
Серверный `-np 1` дополнительно ограничивает одновременную обработку.

Для другого провайдера используйте `LLM_PROFILE=standard` и его URL/модель/ключ.
Обычный профиль сохраняет прежние параметры LiteLLM. Облачного fallback нет.
Потоковый чат доступен независимо от профиля; старый `POST /api/chat` сохранён.

## Настройки этой установки

```dotenv
LLM_PROFILE=local_qwen
LLM_MODEL=openai//models/Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf
LLM_CHAT_MODEL=
TRANSLATION_MODEL=
LLM_BASE_URL=http://10.10.1.24:8080/v1
LLM_API_KEY=local
LLM_TEMPERATURE=0.2
LLM_LOCAL_ENABLE_THINKING=false
LLM_LOCAL_TOP_P=0.8
LLM_LOCAL_TOP_K=20
LLM_LOCAL_MIN_P=0
LLM_LOCAL_REPEAT_PENALTY=1
LLM_LOCAL_PRESENCE_PENALTY=0
LLM_LOCAL_CACHE_PROMPT=true
LLM_MAX_CONCURRENCY=1
LLM_INTERACTIVE_CONCURRENCY=1
LLM_MAX_TOKENS=4096
LLM_MAX_TOKENS_CAP=8192
LLM_TRUNCATION_RETRY_ATTEMPTS=1
LLM_TRUNCATION_MAX_TOKENS_MULTIPLIER=2
LLM_CLASSIFICATION_MAX_TOKENS=512
LLM_TRANSLATION_MAX_TOKENS=2048
LLM_CHAT_MAX_TOKENS=1536
LLM_RETRY_ATTEMPTS=2
LLM_INTERACTIVE_RETRY_ATTEMPTS=1
LLM_CHUNK_RETRY_ATTEMPTS=1
LLM_FIRST_TOKEN_TIMEOUT_SECONDS=120
LLM_STREAM_IDLE_TIMEOUT_SECONDS=30
LLM_INTERACTIVE_STREAM_IDLE_TIMEOUT_SECONDS=30
LLM_TIMEOUT_SECONDS=130
LLM_MAX_TOTAL_TIMEOUT_SECONDS=300
LLM_CHAT_TOTAL_TIMEOUT_SECONDS=180
LLM_CHUNK_BUDGET_SECONDS=600
LLM_QUEUE_TIMEOUT_SECONDS=180
CHAT_MAX_CONTEXT_CHARS=16000
CHAT_FOCUS_NAMED_OBJECTS=true
CHAT_TOP_K_DEFAULT=5
CHAT_TOP_K_PRESETS=[3,5,10]
SEARCH_PER_BRANCH_TOP_K=40
PIPELINE_MAX_WORKERS=1
```

Значения меняются через env с перезапуском backend. Sampling `LLM_LOCAL_*`
применяется только в локальном профиле. Лимит источников в символах учитывает
сериализованные метаданные и ссылки; это не число токенов. Ширина поиска остаётся
40 кандидатов на ветку, сокращается только контекст модели.
Канонические чанки и source spans не пересоздаются. `OKF_MAX_CHUNK_CHARS`
оставлен прежним: изменение границ требует отдельной оценки полноты.
Эмбеддинги bge-m3 и индексы не меняются; реиндекс не нужен.

`CHAT_FOCUS_NAMED_OBJECTS` (по умолчанию false) распространяет существующий
фильтр точного имени на короткие обзорные вопросы вроде «Для чего используется
ZPRP_DISABILITY_CHLD?». Берутся блоки с именем в заголовке и собственное содержание
концепта вместо соседних разделов исходного чанка. Детальные вопросы и сравнения
не сужаются этим дополнительным правилом. Если в заголовках имя не найдено,
сохраняется обычная выдача.
`LLM_LOCAL_CHAT_INSTRUCTIONS` задаёт инструкцию краткого ответа в локальном
профиле; пустое значение отключает её. По умолчанию — до 200 слов для обзора,
без перечисления соседних тем; явно запрошенные полные списки и код сохраняются.

Для первого токена действует отдельный prefill-таймаут; далее — idle-таймаут.
Общий бюджет включает очередь, а бюджет чанка — классификации, повторы, split
и salvage. Превышение output limit не считается успешным ответом. Схема JSON
проверяет форму ответа, но не доказывает фактическую точность и полноту.

NDJSON `POST /api/chat/stream` передаёт предварительный текст, затем окончательный
результат со ссылками. Ошибка заменяет предварительный ответ. Отключение клиента
отменяет ожидание и закрывает LLM-stream; слот освобождается после завершения
worker. Таймаут UI 210 секунд включает чтение тела ответа.

Приложение до импорта LiteLLM выставляет `LITELLM_LOCAL_MODEL_COST_MAP=True`,
если переменная ещё не задана: используется встроенный справочник без запроса
цен на GitHub. Это не изоляция всей сети — другие интеграции работают как прежде.

## Сервер и откат

CT101, `/etc/systemd/system/llama-server.service`: `-c 32768 -np 1`,
`--cache-ram 4096`. Сохранены Q6-веса, Vulkan, flash attention, Q8 KV, MTP,
`-b 2048 --ubatch-size 1024 --cache-reuse 256`.
Флаг cache-reuse сохранён из прежней службы, но сервер 5a6a0dd отключает его
для этого контекста как неподдерживаемый (проверено по журналу).
Обычный prompt cache и его лимит cache-ram работают отдельно.
По умолчанию `--chat-template-kwargs '{"enable_thinking":false}'`; запрос может
переопределить через `LLM_LOCAL_ENABLE_THINKING`.
Меньшее окно экономит память. Основные причины задержки — фактическая длина
prompt, thinking, число выходных токенов и конкуренция; 32K само по себе
не гарантирует ускорения короткого запроса.

Приёмка из backend:
`python scripts/probe_local_qwen.py --output <report.json>` с локальным env.
Только синтетические данные, без сохранения документов/индексов.
`--quick` выполняет два одинаковых RAG-запроса для сравнения; учитывайте прогрев
prompt cache. Для корпуса отдельно оценивайте полноту, релевантность и цитаты.

Исходная служба сохранена в
`/etc/systemd/system/llama-server.service.pre-qwen20260927`.
Для отката восстановите её, выполните `systemctl daemon-reload` и
`systemctl restart llama-server`. Исходная локальная .env сохранена в
игнорируемом `tests/tmp/qwen-rollback/.env.before`; восстановите и перезапустите
backend. Резервный env содержит секреты, не публикуйте его.
После изменений проверяйте `/health`, `/v1/models` и `/slots`.
