# Windows → Fluent Bit → OpenSearch на Proxmox

Дата: 20.10.2026. Результат: **PASS на домашнем стенде**, без Kubernetes.

## Что подключено

```text
Windows backend :18000, durable Logstash JSON stdout
  → Windows Fluent Bit 5.1.0 (tail + SQLite offsets + filesystem buffer)
  → Windows 127.0.0.1:19200
  → SSH tunnel ubuntu@10.10.1.52
  → VM103 127.0.0.1:19200
  → Docker OpenSearch 3.9.0 :9200
  → index audit-windows-20261020
```

VM103 `training-ubuntu`: 8 vCPU, 10 GiB RAM, диск 100 GiB; текущий IP 10.10.1.52.
Proxmox: 10.10.1.50. SSH identity проверена существующим known_hosts alias 10.10.1.41.
OpenSearch доступен только на loopback VM и через SSH; проверенный прямой TCP-доступ
с Windows к 10.10.1.52:19200 отклонён. Security plugin отключён только у этого
изолированного тестового приёмника. Это не конфигурация production OpenSearch заказчика.

Контейнер `okf-windows-audit-opensearch`, restart `unless-stopped`, memory 2 GiB,
heap 512 MiB, single-node. Данные в именованном томе
`okf-windows-audit-data-20261020`, сеть `okf-windows-audit-transport-20261020`.
Образ закреплён digest:
`opensearchproject/opensearch@sha256:adfa61f85025d06b4aeb562e7e74fde7e31c437039c93c3862c17e9acebd6c7c`.

Windows Fluent Bit 5.1.0: официальный ZIP и SHA256 совпали:
`de614af5914aafc3e05ac361340207e130bfe2971d7970a99e8c2451edd672af`.
Источник: [официальный архив](https://packages.fluentbit.io/windows/fluent-bit-5.1.0-win64.zip),
[официальный checksum](https://packages.fluentbit.io/windows/fluent-bit-5.1.0-win64.zip.sha256).

## Проверки фактической доставки

- 18 событий из доступных stdout-файлов → 18 документов в OpenSearch.
- Для каждого события: все 15 канонических полей сверены с неизменяемым payload
  основной БД; все 24 поля `_source` совпали с исходным Logstash JSON.
- `@timestamp` сохранён из времени события; `_id` равен `event_id`.
- Строгий mapping: `dynamic: strict`, 24 поля, один shard, ноль replica.
- Реальные HTTP 401 на `/api/audit` создали штатные `auth_access_denied` и прошли весь путь.
  Контент документов и бизнес-данные не менялись. В этой Windows-проверке проверены
  события denied/WARN; INFO/ERROR отдельно здесь не создавались.
- При остановленном тестовом OpenSearch новое событие получило локальный
  `stdout_written`; Fluent Bit записал chunk 4096 байт в filesystem buffer и повторял отправку.
- В первоначальной конфигурации с HTTP keepalive после остановки приёмника
  наблюдались повторяющиеся `http_do=-1`, несмотря на уже доступный API через SSH.
  После отключения `net.keepalive` и перезапуска сборщика сохранённый chunk доставлен.
- Финальная конфигурация с `net.keepalive Off` прошла **повторный отказ**:
  OpenSearch остановлен → новое событие записано → OpenSearch запущен → доставка
  восстановилась автоматически, **без перезапуска Fluent Bit или SSH**.
- Повторное чтение 16 событий отдельным collector с новым tail DB оставило
  ровно 16 документов; у всех `_version=2`. Повторный collector остановлен.
- Сценарий запуска сборщика/туннеля выполнен повторно: offsets сохранились,
  новое событие доставлено, итог 18/18.
- Финальная БД: audit_log 460 строк, outbox `stdout_written=18`, pending/held отсутствуют.
  442 исторические записи без outbox этим сборщиком не экспортировались.

`stdout_written` означает локальную запись stdout, а не подтверждение OpenSearch.
Здесь удалённый приём подтверждён отдельной проверкой API OpenSearch.

## Файлы и управление

Защищённый рабочий каталог (ACL: текущий пользователь и SYSTEM):

`C:\Users\alexey\ai-workspace\ai-knowledge\tests\tmp\windows-opensearch-20261020`

- `fluent-bit.conf`, `parsers.conf` — действующая конфигурация.
- `state/tail.db` — позиции чтения, `state/buffer/` — дисковый буфер.
- `Start-Collector.ps1` — запуск/перезапуск только этого SSH-туннеля и сборщика.
- `Stop-Collector.ps1` — остановка только их проверенных PID/деревьев процессов.
- `verify_delivery.py`, `final-delivery.json`, `automatic-recovery.json`,
  `replay-dedup.json` — сверка с БД и фактические результаты.
- `collector-outage.log`, `collector-keepalive-failure.log`,
  `collector-automatic-recovery.log` — сохранённые журналы проверок.
- `index-definition.json`, `verified-collector.json` — mapping и версия/проверка архива.

Из корня репозитория, под обычным пользователем alexey:

```powershell
& ./tests/tmp/windows-opensearch-20261020/Start-Collector.ps1
& ./tests/tmp/windows-opensearch-20261020/Stop-Collector.ps1
Invoke-RestMethod http://127.0.0.1:19200/audit-windows-20261020/_count
```

Скрипты не останавливают backend/PostgreSQL и не удаляют OpenSearch/том данных.
OpenSearch при необходимости можно запустить по SSH на VM103:
`sudo docker start okf-windows-audit-opensearch`.
После перезапуска Windows запустить `Start-Collector.ps1` вручную; Windows service
и автозапуск не устанавливались. Для работы туннеля VM103 должна быть включена.
Если IP VM изменится, адрес нужно обновить в скрипте запуска после проверки guest agent.

Сборщик читает только JSON stdout backend, исключая stderr:

1. Текущий стандартный запуск: `C:/Users/alexey/AppData/Local/Temp/opencode/python-out.log`.
2. Предыдущий запуск включения аудита:
   `C:/Users/alexey/AppData/Local/Temp/opencode/audit-enabled-20261020/python-out.log`.

Tail читает с начала и хранит offsets; grep принимает только
`logger_name=ai_knowledge.audit`. `Logstash_Format Off`, `Id_Key event_id`,
`Write_Operation index`, неограниченные retries, общий дисковый лимит output 256 MiB.
Буфер конечный: длительный простой требует контроля свободного места/очереди.
Журналы backend находятся в TEMP, а runtime сборщика — в tests/tmp:
это домашний проверочный запуск, эти каталоги нельзя очищать при работающей доставке.
Ротация/перезапись журнала backend и автозапуск всей цепочки после перезагрузки
в этой приёмке не проверялись. Для постоянной эксплуатации нужен отдельный
постоянный каталог журналов/агента и управляемый запуск.

## Состояние инфраструктуры и ограничения

Оставлены работающими: VM103, OpenSearch, Windows Fluent Bit, SSH-туннель.
CT102 остановлен по ранее данному разрешению для освобождения RAM.
Старые `okf-policy-control-plane` и `okf-kube-control-plane` остановлены;
их конфигурация и тома сохранены. Kubernetes-разработка не продолжалась.

Миграции основной БД не запускались. Проверка добавила пять штатных записей
от реальных неавторизованных HTTP-запросов; прямых изменений бизнес-данных не было.
Код приложения, .env и старые незакоммиченные Kubernetes-файлы не менялись.
Коммит/пуш не выполнялись.

Подготовительные проблемы сохранены отдельно от успешного результата:
официальный ZIP Fluent Bit 4.2.3 имел несовпадение SHA256 с опубликованным checksum
и не исполнялся; запрошенный Windows ZIP 5.1.4 отсутствовал (404).
Использован существующий официальный 5.1.0 с совпавшим checksum.
Изначальная dedicated Docker internal network не публиковала loopback-порт
в этой версии Docker; заменена выделенной bridge-сетью, данные named volume сохранены.

Приёмка подтверждает домашний путь Windows → Proxmox OpenSearch.
Конфигурация/учётные данные/сертификаты/индексирование OpenSearch заказчика
и сквозная приёмка в его инфраструктуре остаются отдельной задачей.
