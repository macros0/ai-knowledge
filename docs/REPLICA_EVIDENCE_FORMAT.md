# Доверенные свидетельства приёмки реплик

Проверка файлов реализована в `backend/test_scripts/replica_evidence.py`.
Она подтверждает целостность и подпись явно доверенного harness. Ответственность
за достоверность native observations и полноту отдельных сценариев остаётся
у этого harness и оператора. Подписанный вымышленный результат всё ещё является
вымышленным: verifier не воспроизводит удалённые испытания.

Никакой реальный издатель пока не настроен. Тесты создают временные Ed25519
ключи; старые отчёты не подписываются задним числом. Все сертификаты публичной
матрицы остаются `null`. Этот режим не выполняет probes, deploy или активацию.

## Независимые входы оператора

Выберите вне каталога evidence три входа: protected manifest установки, ожидаемый
`binding.json` и `trust.json`. Binding использует текущую строгую схему
[policy validator](KUBERNETES_REPLICAS_ACCEPTANCE.md). Manifest сохраняет поля
обычного isolated runner и дополнительно содержит `namespace_uid`,
`installation_id`, `runtime_fingerprint` и уникальный32-hex `acceptance_id` этой
приёмочной сессии. Namespace обязан соответствовать `replica-test-<run_id>`.
Не переиспользуйте acceptance ID для следующего прогона/изменения результатов.

Trust JSON имеет `schema_version:1` и список `issuers` (не более32). Каждый issuer:

- `id`: ограниченное публичное имя;
- `enabled`: явное boolean разрешение;
- `public_key`:32-byte Ed25519 public key в64 lowercase hex символах;
- `producer_sha256`: точный SHA реализации доверенного producer;
- `checks`: список разрешённых полных имён policy checks. Отдельное полномочие
  `qualification.bundle` разрешает подписывать итоговый состав пакета.

Trust выбирает оператор, а не файл bundle. Отзыв `enabled`, изменение ключа,
producer SHA или check permissions требует новой проверки файлов. Подпись
не является полномочием на развёртывание. Ротацию issuer/private-key custody и
реальную выдачу receipts нужно подключить к доверенному harness отдельно;
универсальной команды «подписать любой passed JSON» нет.

## Файлы evidence

Все JSON inputs ограничены64KiB (manifest CLI —1MiB). Bundle содержит не более128
receipt references. Reference имеет только `path` и `sha256`: относительный
неалиасный JSON-путь и SHA фактических байтов. Ссылки, junctions, hardlinks, FIFO,
абсолютные пути, `..`, URL, повторные JSON keys и нестабильные файлы отвергаются.
Verifier читает файлы без сетевых запросов и ничего не записывает.

Bundle содержит `schema_version:1`, `issuer`, `producer_sha256`,
`binding_sha256`, `scope`, `receipts` и `signature`. Scope строго равен выбранным
`run_id`, `namespace_uid`, `installation_id`, `acceptance_id`. Подпись итогового
набора не позволяет сборщику незаметно убрать/подменить failed receipt или
смешать результаты разных сессий. Доверенный issuer должен закрывать сессию
одним неизменяемым terminal bundle; verifier не ведёт удалённый журнал отзыва.

Каждый receipt содержит те же version/issuer/producer/binding/scope, полные
`name` и `kind` policy check, один `artifact` reference и `signature`.
Полномочие issuer проверяется для каждого check отдельно. Artifact содержит
version/name/kind/binding/scope/producer, непустой список уникальных
`outcomes:{id,status}`, список `native_reference_sha256` и `restore_verified`.
Для live evidence нужен хотя бы один native reference hash; это подписанное
свидетельство harness, не повторная проверка содержимого удалённого native receipt.
Restore требует явного `restore_verified:true`. Статус policy выводится из
реальных прочитанных outcomes: failed/pending/skipped блокируют сертификат.
Число outcomes ограничено512, native references —256; неизвестные поля запрещены.

Подпись — Ed25519 в128 lowercase hex символах. Канонизация unsigned объекта:
UTF-8 `json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)`.
Receipt подписывает `b'okf-replica-evidence-v1\0' + canonical(unsigned)`;
terminal bundle использует отдельный домен
`b'okf-replica-evidence-v1\0closure\0' + canonical(unsigned)`.

## Read-only режим общего runner

```powershell
backend/.venv/Scripts/python.exe backend/test_scripts/run_replica_acceptance.py `
  --suite all --manifest <protected-manifest.json> `
  --qualification-binding <expected-binding.json> `
  --qualification-trust <operator-trust.json> `
  --qualification-root <evidence-directory> `
  --qualification-bundle <terminal-bundle.json>
```

Нужны все четыре qualification flags и suite `all`; live/SSH/fault/owned/apply/
resume flags в этом режиме запрещены. Ожидаемые profile/runtime fingerprint
сверяются с manifest, затем проверяются подписи, SHA, scopes и полный набор policy.
Exit0 означает успешную offline-проверку свидетельств выбранного доверенного
harness. `deployment_authorized` всегда `false`. Missing/failed/skipped result,
неверная подпись или неопределённый вход дают exit1 без исходных private значений.
Без этих flags существующий live runner сохраняет прежние pending gates.
Не используйте один `certificate_applies` как замену повторной проверке bundle,
trust и всех файлов: это только проверка binding/policy формы сертификата.
