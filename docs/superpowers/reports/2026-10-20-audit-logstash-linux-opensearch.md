# Linux image и OpenSearch: домашняя проверка — 20.10.2026

Результат: штатный backend image собран и запущен на Linux; цепочка PostgreSQL →
аудит приложения → Docker stdout → Fluent Bit → OpenSearch подтверждена фактическими
документами индекса. Целевой сборщик заказчика этим прогоном не принят.

## Исходники и среда

- Ветка `codex/audit-logstash`, база `origin/main` `17d94c6a0724b809309e4fc48de4b1ae1059b4c6`, изменения без commit/push.
- Proxmox `10.10.1.50`, VM103 `training-ubuntu`: 8 CPU, 10240 MiB, диск 100 GiB.
  После запуска DHCP выдал `10.10.1.52`; SSH host key проверен по прежней VM.
- Linux 6.8.0-146-generic x86_64, Docker 29.8.1, `vm.max_map_count=1048576`.
- По разрешению пользователя CT102 штатно остановлен на время прогона. Два старых
  kind-контейнера временно остановлены; их конфигурация, volumes и приложения не изменялись.
- Только новые disposable DB `audit_test_logstash`/`audit_test_linux`, отдельные
  Docker networks, без host ports. Корневой `.env`, основная БД и корпус не копировались.
  OpenSearch security отключён только внутри этой тестовой сети.
- Qdrant заменён тестовой заглушкой в запускном harness; auth, PostgreSQL, audit,
  outbox, dispatcher, logger, Docker log driver и downstream настоящий.
  LLM/embeddings, Qdrant retrieval и Kubernetes этим тестом не принимаются.

## Версии и идентичность

| Компонент | Проверенное значение |
|---|---|
| Backend image | `okf-audit-logstash:20261020`, linux/amd64 |
| Backend immutable image ID/digest | `sha256:5e50277c74d0665ed18e73facd2b7f807f3faddc9ca4fdbac11636d3a26df30c` |
| PostgreSQL | 17.11; `postgres:17` resolved digest `sha256:2d2b8998d31037bf721cfdf764d76ba74171b4fab3431b7f72c27c56ddbdf9e3` |
| OpenSearch | 3.9.0; digest `sha256:adfa61f85025d06b4aeb562e7e74fde7e31c437039c93c3862c17e9acebd6c7c` |
| Fluent Bit | 4.2.3; digest `sha256:a5761fa961cb22dd0875883a4d446b1acd99d4935d77358aa9f50ee177e44fe2` |
| Runtime | psycopg 3.3.6, Linux libpq 180006, python-json-logger 4.2.0 |
| Final source manifest | 969 files, SHA256 `cae6438fd669d86e000ef8d77f9fac4cd32d84957825a8c443c8cd6ac510f8ec` |

Штатный `backend/Dockerfile`, hashed requirements lock и entrypoint использованы
без подмены production image тестовым. Для pytest собран отдельный слой с pytest 8.4.2;
он не использован как сервер сквозного теста. Полные image inspections сохранены.

## Найденный дефект и исправление

На исходном image реальный server startup завершался с `FileProfileError`:
entrypoint менял UID на1000 через setpriv, но сохранял `HOME=/root`. Проверка
стандартных libpq SSL-файлов получала PermissionError. Windows/root pytest этого не выявлял.

Добавлен opt-in `test_audit_docker_startup.py`: реальный Docker entrypoint должен
дать непривилегированный UID, HOME из passwd и успешный PostgreSQL audit preflight.
До изменения: **1 FAIL**, `(1000, '/root')`. Минимальное исправление:
entrypoint выставляет HOME домашнего каталога `app` перед setpriv. После новой
штатной сборки тот же тест дал **1 PASS**. Guard/profile checks не ослаблялись.

## Фактические результаты

| Проверка | Результат |
|---|---|
| Штатный Linux Dockerfile, hashed lock, pip check | PASS |
| Основной audit/POSIX suite в image, сеть отключена | 114 PASS /2 SKIP, 194.82s |
| PostgreSQL + migration + POSIX process faults | 12 PASS, 46.75s |
| Docker UID/HOME/PG startup regression | RED 1 → GREEN 1 |
| Настоящий server PID1 и health | UID 1000, health 200, restart_count 0, OOM=false |
| Fresh PostgreSQL Alembic chain | `066b1c2d3e4f` |
| Fluent Bit configuration dry-run | PASS |
| Начальный startup+HTTP поток | 4 `glossary_rule_create` +3 auth =7 indexed |
| Нагрузка при остановленном OpenSearch | 1003 service events +3 auth; всего 1013 stdout_written |
| Восстановление OpenSearch | 1013/1013 indexed, все event_id совпали с БД |
| Канонические данные | Все поля каждого события равны payload из outbox |
| Строгий mapping | Все 24 поля, INFO/WARN/ERROR и числовые уровни корректны |
| Unicode/escaped newline/tab/emoji | PASS |
| Content canary в запрещённом meta | Отсутствует в индексе |
| Повтор всего Docker лога с новым tail DB | Осталось 1013 docs; sample `_version=2` |

Из двух unit skips один Windows-only, второй PostgreSQL parameter без URL в
изолированном запуске. Этот PostgreSQL parameter отдельно выполнен с PASS.
Linux SIGKILL, partial stdout, process crash и освобождение lock реально выполнены.

События нагрузки созданы через `record_in_session` в настоящей отдельной PostgreSQL;
шесть auth-событий получены через HTTP: unauthorized request → login → logout,
до и во время недоступности OpenSearch. Число 1013 включает четыре штатных startup
события и 1003 synthetic service events. Это correctness proof, не production SLA.

## Сборщик и граница доказательства

Fluent Bit читает **настоящий json-file лог только тестового app-контейнера**,
отбирает stdout, пропускает пустые fence lines, разбирает JSON из `log`, затем
передаёт исходные поля в строгий тестовый индекс. `Id_Key event_id` и
`Write_Operation index` обеспечили проверенную дедупликацию; retry и filesystem
buffer сохранили поток при остановке OpenSearch. `Logstash_Format Off` сохраняет
уже сформированный приложением `@timestamp`, без его замены downstream.

Это проверенные настройки нашего тестового сборщика; у сборщика заказчика
их наличие не предполагается. Конфигурация и mappings сохранены в evidence archive.
Сверялись [официальный Docker recipe OpenSearch](https://docs.opensearch.org/latest/install-and-configure/install-opensearch/docker/)
и [OpenSearch output Fluent Bit](https://docs.fluentbit.io/manual/4.2/data-pipeline/outputs/opensearch).

На реальном целевом окружении остаются OPEN: конкретный parser/mapping, CRI fragment
reassembly, near-limit frame через CRI, INFO policy, TLS/credentials, index rights,
retention и согласованное окно доставки. Домашний Docker json-file не доказывает
работу CRI/NFS/CSI/SCRAM/TLS-профилей. AC-10 заказчика остаётся OPEN.
Общий многотысячный backend suite после entrypoint fix заново не запускался;
сохранены прежние результаты и точечные исправления в основном acceptance report.

## Артефакты и воспроизведение

Локальный каталог: `tests/tmp/proxmox-audit-20261020/` этого worktree.
На VM: `/home/ubuntu/audit-logstash-20261020/`. Harness остаётся одноразовым
тестовым материалом, не новым operator/framework приложения.

- `evidence.tar.gz`, SHA256 `14b26a209563a6bb2c8210880c51775d88831f12de1cc539544b3bdb09828924`.
- `evidence/artifacts/result.json`, `probe-resume.log`, `events.json`,
  `outbox-canonical.json`, `mapping.json`, `final-db-proof.log`,
  `final-opensearch-proof.json`, `all-test-containers.json`.
- `image-build*.log`, `linux-unit-tests.log`, `linux-pg-tests.log`,
  `docker-home-red.log`, `docker-home-green.log`, collector logs.
- `probe.py`/`resume-probe.py`, test-only server bootstrap и Fluent Bit configs.

Исходные failures сохранены: urllib helper в harness, реальный HOME startup,
ожидание трёх событий вместо семи и неверное имя аргумента load generator.
После проверки причины harness исправлен; продуктовый fix только HOME в entrypoint.
Полная цепочка пройдена с пересобранным image; после учёта четырёх startup events
проверка продолжена на тех же данных, final result PASS. Первые failures не удалены.

## Завершение

Тестовые контейнеры, anonymous volumes и две сети удалены после сохранения proof.
Собранные images, исходники и evidence оставлены для повторения. Старым kind
контейнерам возвращено состояние запуска перед штатным выключением VM; их данные
сохранены. VM103 возвращена в исходное stopped, CT102 — в исходное running; финальная read-only проверка сохранена в `restoration.log`.
Ни commit/push, ни изменения основной БД/корпуса не выполнялись.


### Отдельное состояние прежнего CT102 после запуска

Контейнер CT102 running, VM103 stopped. Однако прежняя служба
`okf-knowledge.service` завершила Compose startup с ошибкой: её backend healthcheck
требует `health.status == "ok"`, а `/health` вернул `degraded` из-за таймаута LLM.
Database, Qdrant, Ollama и PDF parser в этом ответе — `ok`. Backend отвечает HTTP200,
но Docker помечает его unhealthy, зависимые компоненты Compose не стартовали.
До выключения CT102 его application readiness не измерялась, поэтому исходное
наличие этой неисправности не утверждается. Новая аудит-ветка туда не поставлялась;
настройки, healthcheck, модели и код прежней установки не изменялись.

Сохранены отдельные `restoration-final.log`, `ct102-startup-diagnostic.log`,
`ct102-backend-diagnostic.log`, `ct102-health-result.log` в локальном каталоге
артефактов. Этот отдельный OPEN не меняет PASS изолированного Linux/OpenSearch
прогона; восстановленной полностью рабочей старая установка не объявляется.
