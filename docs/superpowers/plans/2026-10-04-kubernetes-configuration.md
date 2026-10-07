# Kubernetes Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Исполнение самим агентом в существующей Kubernetes-ветке; делегирование только по отдельному указанию пользователя.

**Goal:** одна поставка поддерживает typed probe/traffic/runtime CA настройки,
операционные Jobs и восстановление на двух независимых обезличенных профилях.

**Architecture:** расширяется существующий compact chart и оператор. Backend
использует прежний Settings loader. Trust передаётся вместе с env/volume в Jobs;
schema 2 выпускается вместе с trusted bootstrap/toolbox, а старый комплект
сохраняется для восстановления release 1.

**Tech Stack:** существующие Helm, Python 3.11+/PyYAML 6.0.3, Python ssl,
FastAPI/Next, PostgreSQL 17/Qdrant, kind/Gateway API/kgateway и тестовый Keycloak.
Новые runtime зависимости не требуются.

**Spec:** [Исправленный контракт конфигурации](../specs/2026-10-04-kubernetes-configuration-design.md).

**Статус исполнения 04.10.2026:** задачи 0–6 реализованы в `codex/kubernetes-rollout`.
Synthetic runtime scenarios завершены с явным продолжением после сохранённых
failed gates: minimal, actual legacy restore → upgrade, A/B HTTPS/SSO/изоляция,
100 MiB boundary, finite/zero/idle, CA rotation/recovery и fresh-claim restore.
Финальная регрессия: 316 PASS/0 skips; stability: 10×43 и дополнительно 10×12
fixture cases. [Фактический отчёт](../reports/2026-10-04-kubernetes-configuration-acceptance.md)
разделяет подтверждённое, исправленные failures и открытые platform/audit gates.
Один непрерывный полный запуск окончательного runner отдельно не повторялся.
Commit, публикация и интеграция в main не выполнялись.

## Global Constraints

- Reuse `codex/kubernetes-rollout`; чужие незакоммиченные изменения сохраняются.
  До edits проверить соответствие spec актуальному снимку и записать BASE в ledger.
- Compact, Recreate, application replicas=1, workers=1, bundled storage;
  фиксированные health paths/ports и existing PROD guards сохраняются.
- Local launchers и Compose bundled/external сохраняются. Отдельные OIDC/audit,
  metrics/external/replication проекты не присоединяются к этому пакету.
- Application-ready wait 600s, application-stop wait 180s. Проверки spec:
  `max(S + R) + 60 ≤ 600`, grace текущего/желаемого workload `+30 ≤ 180`.
- Defaults probes — шесть строк таблицы spec; explicit Kubernetes defaults
  сохраняют поведение. Новые поддеревья строгие, существующая schema не закрывается
  глобально без инвентаризации.
- Gateway defaults 300s/300s; `0s` только explicit opt-in + finite idle policy.
  TrafficPolicy выключен по умолчанию; выключенный adapter не требует kgateway API.
- Runtime trust: immutable existing ConfigMap, key `ca-bundle.crt`, volume
  `runtime-trust`, read-only path `/etc/okf/trust/ca-bundle.crt`; backend
  SSL_CERT_FILE/REQUESTS_CA_BUNDLE и frontend NODE_EXTRA_CA_CERTS.
- CA mount/env следуют выбранному old/new Pod template. Secret содержимое,
  private keys, real addresses/IDs не попадают в tracked files/CI output.
- Release schema 2 и matching tools `[2]`; trusted bootstrap поступает из toolbox.
  Старый комплект 1 сохраняется для restore → upgrade, без неявной совместимости.
- Новый backup имеет собственный `manifest.format=2`, включая trust off.
  Его нельзя выпускать отдельно от matching tools: старый reader обязан отказать,
  а не молча восстановить данные без trust. Release `format` остаётся прежним.
- Сохранение результатов задачи — focused diff + ledger. Commit/merge/push,
  публикация артефактов и окруженческая доставка выполняются отдельным действием.
  Checklist не делает эти действия автоматически разрешёнными.

## Review Focus

1. Старый Pod использует CA A, новый — B: backup должен использовать A, migration B
   (задачи 3/4); чтение только нового bundle не защищает backup.
2. Grace старого Pod больше бюджета, новый корректен: preflight отказывает до lock
   (задача 1), исключая остановку с заведомо недостаточным ожиданием.
3. API group существует, TrafficPolicy kind/RBAC/admission отсутствует: безопасный
   отказ до stop; group discovery не считается достаточным (задача 2).
4. Schema 2 archive корректен, но bootstrap/toolbox старый: отказ до распаковки/
   мутаций; восстановление старого backup всё ещё доступно старым комплектом (5/6).
5. Профили только рендерятся или rebuilt CA image меняется между A/B: это не
   приёмка одинаковых артефактов; нужны runtime и recorded image identities (6).
6. Старый reader принимает дополнительные backup files: одного поля trust
   недостаточно; format mismatch и восстановление после failed rotation
   проверяются отдельно (4/6).

## Карта файлов

| Файлы | Ответственность |
|---|---|
| `deploy/helm/ai-knowledge/{values.yaml,values.schema.json,templates/application.yaml,templates/platform.yaml,templates/_helpers.tpl}` | Timing/traffic/trust values, render и защиты |
| `scripts/kubernetes/lifecycle.py` | Budget/admission/trust preflight, operation Pod contract, backup/restore |
| `scripts/kubernetes/{release.py,operator_tools.py}` и `deploy/ci/operator-tools/{consume.py,Dockerfile}` | Schema 2 producer и trusted consumer/toolbox |
| `scripts/kubernetes/{home_access.py,home_probe.py}` | Reuse домашнего HTTPS/SSO smoke без per-profile rebuild |
| Новый `scripts/kubernetes/tests/configuration_acceptance.py` | Управление сценариями через существующий lifecycle, без второй реализации операций |
| `backend/tests/test_kubernetes_{chart,lifecycle,release,toolbox,home_access}.py` | Расширение текущих contract tests |
| Новые `backend/tests/test_kubernetes_runtime_trust.py`, `test_kubernetes_configuration_acceptance.py` | TLS adapters и безопасность runner |
| Новые `backend/tests/fixtures/kubernetes/{consume_schema1.py,lifecycle_schema1.py,public-ca.pem}` | Исторические bootstrap/backup reader для unit matrix и публичный PEM fixture; не включаются в release archive |
| `.github/workflows/kubernetes.yml`, `scripts/kubernetes/tests/{toolbox_smoke.py,acceptance.sh}` | CI regression и проверка фактического toolbox |
| `SECURITY.md`, `docs/KUBERNETES_{DEPLOYMENT,TESTING,HOME_ACCESS}.md` | Trust/rotation/limits, команды и доказательства приёмки |

## Проверочные команды

Unit commands ниже выполняются из `backend/` подходящим Python проекта. Перед
chart tests задать `HELM_BIN` существующим проверенным Helm 4.3.0; skip по отсутствию
Helm не принимается за успех. На Windows использовать `.venv/Scripts/python.exe`;
в Linux CI — `python`. Basetemp/cache только в предназначенной test directory.
Shell acceptance выполняется на Linux стенде с explicit context/namespace.
Версии домашних компонентов сверяются до запуска с текущим home report;
kgateway 2.4.5 CRD проверяется отдельно, latest docs не заменяют patch schema.

### Task 0: Сохранить исходный schema-1 комплект и зафиксировать baseline

**Files:** Create historical `backend/tests/fixtures/kubernetes/{consume_schema1.py,lifecycle_schema1.py}`;
private evidence вне tracked files; текущий ledger `.superpowers/sdd/2026-10-03-kubernetes-rollout/progress.md`.

**Interfaces:** сохранённый `kit-v1` содержит exact source snapshot, chart,
allowlisted tools/archive, trusted bootstrap/toolbox identity и image identities.
Fixtures для unit matrix не заменяют фактический kit-v1 для runtime restore.
Для обоих kits фиксируется одинаковый layout: `incoming/release.json`,
`incoming/chart.tgz`, `incoming/operator-tools.tar.gz`, `source/` (точный lab
snapshot) и `evidence.json` с hashes, revision и actual image identities.
Trusted toolbox identity и ожидаемая revision поступают из отдельного защищённого
input, а не выбираются из скачанного kit. Runtime values/Secrets/keys хранятся
отдельно. Извлечённые tools создаются bootstrap в новом каталоге; `source/`
используется только для подготовки lab/исторических тестов, не вместо consumer.
Лабораторная image-ID проверка повторяет existing toolbox smoke; её результаты
отделяются от OCI digest delivery, проверки consumer не обходятся.
Source snapshot содержит только необходимые исходники по явному allowlist:
без `.env`, `.git`, данных, credentials и локальных diagnostics. Проверить наличие
всех старых images и CLI dependencies до смены протокола, а не во время аварии.

- [x] Сверить worktree/status/spec, сохранить исходные consume.py/lifecycle.py fixtures и SHA256
  до изменения protocol. Сохранить schema-1 источники/артефакты для восстановления
  в private lab directory; runtime Secret/CA keys туда поступают отдельным
  защищённым способом. Не подменять отсутствующий старый артефакт новым кодом.
- [x] Запустить существующие chart/lifecycle/release/toolbox tests одной командой:
  `python -m pytest tests/test_kubernetes_chart.py tests/test_kubernetes_lifecycle.py tests/test_kubernetes_release.py tests/test_kubernetes_toolbox.py -q`.
  Ожидание: PASS, 0 skips. При исходном failure сохранить evidence и устранить
  причину в пределах задачи до изменения контрактов.
- [x] Записать hashes BASE и legacy kit в ledger. Локальные Docker image IDs и
  dirty-snapshot hashes подписываются как лабораторные доказательства, а не как
  опубликованная immutable release. Новый toolbox не перезаписывает старый tag.

### Task 1: Probe schema/render и budgets оператора

**Files:** Modify chart values/schema/application/_helpers; lifecycle.py;
Test existing `backend/tests/test_kubernetes_chart.py`, `test_kubernetes_lifecycle.py`.

**Interfaces:** `validate_operator_budgets(desired: dict, current: dict | None) -> None`
в lifecycle.py, raises OperationError. Consumes Deployment docs; missing поля
старого Pod нормализуются по Kubernetes defaults. Новый helper `okf.probeTiming`
рендерит только четыре timing поля из schema, endpoint/action остаётся в template.

- [x] Добавить failing render tests `test_probe_overrides_preserve_actions`,
  `test_probe_defaults_preserve_behavior`, `test_probe_unknown_keys_and_types_refused`:
  изменение period не меняет action; path/port/exec/enabled/successThreshold,
  unknown subtree keys, bool/string/fraction/negative timing отклоняются.
  Для YAML null проверить семантику Helm merge: удалённое required timing поле
  или subtree отказывает; отсутствие не заменяется случайным zero.
- [x] Добавить budget tests: defaults проходят; startup.delay=600 отказывает;
  current.grace=151 отказывает при desired.grace=60; desired.grace=150 проходит,
  151 отказывает. Через manager.deploy с вызовом реального preflight
  проверять отсутствие acquire/stop/chart при invalid budgets.

  ```python
  # Fixture сохраняет реальный preflight; подменены только внешние reads
  # и последующие операции. Desired валиден, current Deployment имеет grace=151.
  with pytest.raises(module.OperationError):
      manager.deploy(backup_root)
  manager.acquire.assert_not_called()
  manager.stop.assert_not_called()
  manager.chart.assert_not_called()
  ```

- [x] Run `python -m pytest tests/test_kubernetes_chart.py tests/test_kubernetes_lifecycle.py -q`;
  ожидаемый RED — отсутствующие overrides/guards, не ошибка окружения.
- [x] Добавить defaults/spec schema и render helper. Включить budget guard в
  preflight с actual current Deployment; сохранить waits 600/180. Run та же команда:
  GREEN, 0 skips; старые replicas/workers/PROD/storage tests проходят.
- [x] Сохранить focused diff/ledger с выполненными assertions и невыбранными
  timing overrides; непроверенный CSI startup не объявлять подтверждённым.

### Task 2: Timeouts, optional TrafficPolicy и preflight

**Files:** Modify chart values/schema/platform/_helpers, lifecycle.py, SECURITY.md;
Test existing chart/lifecycle tests.

**Interfaces:** Helm helper `okf.durationMilliseconds(value: string)` возвращает
строку целого количества ms или fail; `0s` — отдельное значение с отдельной
семантикой. `Manager.preflight_platform(document: dict) -> None` проверяет
discovery kind, RBAC и TrafficPolicy server dry-run без печати содержимого.

- [x] RED tests: duration 300s/5m равны; backend 301s при request 300s отказывает;
  negative/fraction/compound/invalid unit отказывают. Gateway API schema bounds
  проверяются до умножения: положительный single-unit компонент 1–5 цифр,
  длина ≤32; единственный zero token — `0s`.
- [x] RED tests для zero pair: `0s` без allowUnboundedTimeouts отказывает;
  opt-in без Gateway/TrafficPolicy отказывает; 0/300s, 300s/0, 0/0 с prerequisites
  проходят без сравнения zero как меньшей длительности.
- [x] RED platform tests: adapter off не вызывает kgateway discovery/dry-run;
  group без resource kind, RBAC deny, rejected dry-run дают OperationError до lock.
  Для RBAC проверять exit code и ответ, разрешения create/patch/get по сценарию
  создания/обновления, а не только существование API group.
  Отдельные first-install/update tests: dry-run создания отсутствующего объекта
  и dry-run patch существующего принадлежащего release объекта. Не менять
  Helm ownership через SSA/force-conflicts; чужой объект с тем же именем — отказ.
  Не выводить stdout/stderr admission, который может содержать конфигурацию.
- [x] Run chart/lifecycle tests (команда Task 1), подтвердить meaningful RED.
  Добавить typed trafficPolicy defaults и allowUnboundedTimeouts=false;
  render route-only frontend-stream policy с timeouts.streamIdle.
  Подключить discovery по APIResourceList, rights и server dry-run в preflight.
- [x] Run та же команда: GREEN. Документировать, что dry-run не подтверждает
  attachment/ResolvedRefs или controller behavior; runtime conditions проверяются
  в Task 6. SECURITY описывает route-wide zero/idle policy и отсутствие IP изменений.

### Task 3: Runtime trust application/Jobs и проверка TLS

**Files:** Modify chart values/schema/application/_helpers, lifecycle.py, SECURITY.md;
Create public PEM fixture и `backend/tests/test_kubernetes_runtime_trust.py`;
Test existing chart/lifecycle tests.

**Interfaces в lifecycle.py:**
`runtime_trust_reference(pod: dict) -> str | None` — fixed contract env/mount/volume;
`validate_trust_configmap(document: dict) -> dict` — identity
`{configMap, key, sha256}` с checksum UTF-8 bytes `data['ca-bundle.crt']`;
`operation_pod_spec(template: dict, backend: dict) -> dict` — data/work и optional
allowlisted runtime-trust. OperationError не содержит PEM/Secret или endpoint.

- [x] RED tests: trust off сохраняет прежний env/mount; trust on монтируется
  read-only в оба контейнера, env совпадает с fixed path; conflicting controlled
  TLS keys в ConfigMap/Secret отклоняются. Missing ConfigMap/key, mutable map,
  invalid PEM/private key дают отказ; checksum соответствует точным bytes.
- [x] RED operation tests: old template CA A + desired B даёт Job с A для backup;
  desired template даёт B для migration/restore; env без mount, mount без volume,
  иной path/key/readOnly или несовпадение reference отказывают. Diagnostics/чужие
  volumes не копируются в operation Pod.

  ```python
  pod = module.operation_pod_spec(old_template, old_backend)
  assert next(v for v in pod['volumes'] if v['name'] == 'runtime-trust')['configMap']['name'] == 'ca-a'
  assert not any(v['name'] == 'diagnostics' for v in pod['volumes'])
  ```

- [x] Run `python -m pytest tests/test_kubernetes_chart.py tests/test_kubernetes_lifecycle.py tests/test_kubernetes_runtime_trust.py -q`;
  подтвердить RED. Реализовать schema/mount/env, reference validator и ssl PEM parse;
  preflight проверяет desired и existing trust. Manager.job использует pure
  operation_pod_spec, не второй YAML lifecycle. Runtime dependencies не добавлять.
- [x] В TLS tests поднимать одноразовый local HTTPS fixture с ключом только в
  tmp directory. Реальный KeycloakOidcProvider discovery и существующий LLM
  adapter обращаются через TLS; правильный bundle проходит, wrong CA даёт
  certificate-verification error. Клиенты создаются после изменения env, чтобы
  cached SSL contexts не подменяли проверку. Не mock HTTP transport в этой проверке.
- [x] Run та же команда: GREEN. Node HTTPS probe и запуск actual operation image
  с CA/env выполняются в Task 6; один Python ssl parse не закрывает TLS acceptance.

### Task 4: Trust-aware backup/restore и CA rotation

**Files:** Modify lifecycle.py и docs/KUBERNETES_DEPLOYMENT.md;
Test existing lifecycle tests, runtime trust tests.

**Interfaces:** `validate_restore_trust(saved: dict | None, target: dict | None) -> None`
сравнивает key/checksum; target ConfigMap name может отличаться в новом namespace.
Новый backup `manifest.json` имеет `format=2` независимо от release config schema.
Новые `release.json` backup identities обязательно содержат `trust` (dict или null).
При trust on `trust-bundle.pem` обязателен, присутствует в manifest files, его
hash равен `release.json.trust.sha256`; при trust off поле равно null и PEM
отсутствует. Проверяются согласованно manifest, identity и artifact, включая
несовпадение двух hashes даже при корректных checksums остальных файлов.

- [x] RED tests: backup с old CA A сохраняет A hash/reference/public artifact,
  а не desired B; manifest verifies bundle checksum. Trust off сохраняет null.
  Legacy format=1 отказывает комплектом 2 с указанием использовать исходный
  комплект 1. Исторический `verify_backup` из lifecycle_schema1.py отказывает
  format=2, включая trust off; fixture побайтово соответствует исходнику kit-v1
  и не исправляется ради теста. Unit tests не требуют приватного kit directory.
  Новый format=2 с missing trust, missing/extra PEM или несовпадением identity
  hash отказывает. Формат release manifest не меняется из-за версии backup.
- [x] RED restore tests: эквивалентный bundle под другим target name проходит;
  missing/mismatch/corrupted bundle отказывает до acquire/import/chart.
  Source namespace, new claims/empty target, matching images/storage/model guards
  остаются действующими. Bundle не создаётся автоматически при restore:
  оператор заранее подготавливает immutable target ConfigMap из сохранённого PEM.
- [x] Run `python -m pytest tests/test_kubernetes_lifecycle.py tests/test_kubernetes_runtime_trust.py -q`,
  подтвердить RED, затем реализовать запись old trust identity/artifact и проверку
  restore. Проверять hash и пригодность сохранённого PEM, не только имя.
- [x] Run та же команда: GREEN. Документировать rotation через existing deploy
  с backup/migration, overlap bundle (старые + новые корни), recovery lock и
  retention bundles по сроку backup. Возврат old reference допустим только
  при доступных старых endpoints и работоспособном текущем backup path.
  При failed migration/start текущая конфигурация может уже быть новой:
  не обещать повторный deploy как безусловное восстановление. Сначала сохранить
  evidence; если lock остался, проверить отсутствие writer/живых Jobs перед
  штатным recover-lock (при необходимости сначала штатная остановка с ожиданием).
  при невозможности безопасного redeploy восстановить проверенный pre-change
  backup в новый namespace/PVC matching комплектом и прежними trust/config.
  Переключение доступа выполняется отдельно после проверки восстановленной копии.
  CA-only revert не представлять SQL rollback; DB password rotation отдельно.
  Зафиксировать failure tests до/после chart apply: нет automatic rollback/unlock,
  повторного start/миграции после failure или force-delete; backup/bundle сохраняются.
  При провале внешнего HTTPS smoke после успешного deploy lock уже может быть
  освобождён: отсутствие lock не считать успешной TLS-приёмкой. Restore в новый
  namespace не требует снимать lock исходного повреждённого окружения.
  Runtime Secret/ConfigMap старого workload не изменять между preflight и backup:
  versioned refs либо окно без параллельной внешней ротации. Старые refs сохранять
  до завершения операции; Secret data в recovery manifest не копировать.

### Task 5: Schema 2 producer, trusted bootstrap и toolbox

**Files:** Modify release.py/operator_tools.py/consume.py/toolbox Dockerfile,
scripts/kubernetes/tests/toolbox_smoke.py, existing release/toolbox tests,
deploy/ci/operator-tools/README.md. Использовать Task 0 schema-1 fixture.

**Interfaces:** имена/параметры existing package_tools/tools_identity/consume
сохраняются. Manifest `config_schema=2`, archive metadata и tools identity `[2]`;
trusted bootstrap принимает только 2. Release/chart/archive/toolbox SHA/revision
guards не ослабляются. Fixture bootstrap 1 используется только в controlled tests.

- [x] RED matrix tests old/new consumer × release 1/2: 1/1 и 2/2 проходят,
  1/2 и 2/1 отказывают до destination creation. Archive metadata/version mismatch,
  swapped chart/toolbox и tampered source member также отказывают.
  Unit-generated schema-1 archive проверяет protocol, не считается actual kit-v1 restore.
  Прямой `release.py overlay` также проверяет config_schema=2 до создания output;
  проверка только через bootstrap оставила бы обход на уровне отдельного CLI.
- [x] Run `python -m pytest tests/test_kubernetes_release.py tests/test_kubernetes_toolbox.py -q`,
  подтвердить RED. Перевести producer/archive/bootstrap на 2 и matching toolbox;
  обновить callers/fixtures без замены negative assertions на success.
- [x] Run та же команда: GREEN. Из repository root собрать toolbox под новым lab
  tag, сохраняя старый. `PYTHON_IMAGE_PIN`, `HELM_SHA` и `KUBECTL_SHA` берутся из
  проверенных inputs сохранённого toolbox build; base обязательно по digest:
  `docker build --platform linux/amd64 -f deploy/ci/operator-tools/Dockerfile --build-arg PYTHON_IMAGE="$PYTHON_IMAGE_PIN" --build-arg HELM_SHA="$HELM_SHA" --build-arg KUBECTL_SHA="$KUBECTL_SHA" -t okf-toolbox:configuration-v2 .`.
  Затем из repository root:
  `python scripts/kubernetes/tests/toolbox_smoke.py --image okf-toolbox:configuration-v2`.
  Expected PASS: фактический non-root bootstrap 2, offline source-free consumption,
  mismatch rejection и корректные file permissions. Registry publication отсутствует.
- [x] Записать actual toolbox ID и protocol hashes; consumer CI template продолжает
  получать protected pinned identity. Один новый archive не заменяет trusted bootstrap.

### Task 6: Минимальный профиль, A/B и межверсионное восстановление

**Files:** Modify home_access.py/home_probe.py, acceptance.sh,
.github/workflows/kubernetes.yml; Create configuration_acceptance.py и
`backend/tests/test_kubernetes_configuration_acceptance.py`;
Modify existing home_access tests, docs/KUBERNETES_TESTING.md/KUBERNETES_HOME_ACCESS.md;
Create report `docs/superpowers/reports/2026-10-04-kubernetes-configuration-acceptance.md`
только после фактического прогона.

**Interfaces:** `home_access.application_values(namespace: str, backend_image_tag: str, *, trust_configmap: str | None = None) -> dict`
сохраняет legacy CA-build mode;
при trust_configmap задаёт runtime reference и сохраняет supplied image identity.
CLI home_access получает optional `--runtime-trust-configmap` и
`--backend-image-tag`; runtime mode требует supplied tag вместо вычисления CA tag.
Проверенные digest overlays комплектов имеют приоритет над lab tags. Helper создаёт
полный immutable public bundle из image standard roots + lab CA без пересборки.
Новый runner CLI: `--context`, `--artifact-root`, `--kit-v1`, `--kit-v2`,
`--keycloak-image`. Только explicit kind context и созданные okf-* namespaces;
private root вне репозитория. Операции запускаются verified tools соответствующего
kit через existing lifecycle subprocess, чтобы не смешать Python modules 1/2.
Runner получает отдельный `--trusted-inputs` (защищённый JSON с ожидаемыми
revision/toolbox identities для обоих kits); layout соответствует Task 0.
Prefix `kind-`/`okf-` не доказывает владение ресурсом: новые namespaces должны
отсутствовать до запуска, получить run-id label; cleanup сверяет UID и run-id
с приватным inventory. Существующие namespaces/cluster runner не удаляет.

- [x] RED runner/home tests: runtime CA mode не меняет image identity;
  contexts/paths вне lab отказывают до kube writes; отсутствующий kit/bundle,
  image drift и несовпадение chart/tools identities отказывают. Runner не печатает
  credentials/config bodies и не очищает чужие ресурсы при ошибке.
- [x] Run `python -m pytest tests/test_kubernetes_home_access.py tests/test_kubernetes_configuration_acceptance.py -q`,
  подтвердить RED. Реализовать orchestration с reuse home_access/home_probe/lab/
  smoke/lifecycle; базовый acceptance.sh и существующий BuildKit CA test сохраняются.
  У home probe убрать предположение о per-profile CA baked image, сохраняя
  negative TLS client с независимым standard trust. Сейчас probe использует
  собственный `/probe/ca.crt`: его успех сам по себе не доказывает runtime trust
  приложения. Проверять приложение через настоящий SSO flow, Node отдельным
  HTTPS вызовом из frontend, а Job — с env/mount выбранного template и настоящим
  HTTPS запросом. Не копировать полный backend Secret в probe. Run та же команда: GREEN.
- [x] Сверить ресурсы домашней training-ubuntu перед изменениями. Первый стенд
  4 vCPU/8 GiB; A/B/restore запускать по очереди, одновременно только один
  active application stack плюс необходимая lab infrastructure. Backup/bundles
  сохранить до cleanup; дефицит RAM не лечить остановкой чужих VM/сервисов.
  Docker/kind стенд, использованный portable acceptance, допустим при недостатке
  home VM, с явным указанием фактического места прогона в report.
- [x] Подготовить verified kgateway 2.4.5/Gateway API и isolated Keycloak по home
  runbook; записать exact CRD schema/hash. Построить application images один раз.
  Не bake разные CA в A/B images. Перед/после каждого профиля сравнить identities.
- [x] Из repository root выполнить:
  `python scripts/kubernetes/tests/configuration_acceptance.py --context "$LAB_CONTEXT" --artifact-root "$PRIVATE_ARTIFACT_ROOT" --kit-v1 "$KIT_V1" --kit-v2 "$KIT_V2" --trusted-inputs "$TRUSTED_INPUTS" --keycloak-image "$KEYCLOAK_IMAGE"`.
  Значения — подготовленные local lab inputs, не текущий context по умолчанию.
  Runner выполняет последовательные сценарии ниже; каждый failure сохраняется.

| Сценарий | Проверяемый результат |
|---|---|
| Минимальный schema-2 профиль, Gateway/Vault/trust off | Actual install/ready/upload/поиск и baseline lifecycle; отсутствие новых env/mount/API dependency |
| A: HTTPS, default probes/resources, policy off | Confidential SSO/CSRF/roles, multipart, NDJSON heartbeat/upstream SSE/cancel; actual backend trust и отдельный Node HTTPS probe |
| B: HTTPS, другие namespace/callback/Secret/PVC/CA, timings/resources, policy on | Те же artifacts; actual attachment/ResolvedRefs/controller behavior, данные A не видны B |
| Zero timeout policy | Controlled finite request limit короче fixture stream; 0s + idle позволяет active stream закончиться; stalled upstream/client stream прерывается idle; cancellation остаётся рабочим |
| CA rotation B → B2 → прежний bundle | Deploy с непустым backup old trust, new Job trust, HTTPS после rotation/revert; old immutable bundle доступен |
| Ошибка CA после применения нового workload | Синтетический корректный PEM, не доверяющий endpoint: HTTPS gate падает даже при зелёной readiness; backup сохранён, проверенный recovery по Task 4 возвращает HTTPS и данные |
| Backup/restore B | Не менее двух docs, concept/chunk/vector counts, hashes/content integrity; restore в новый namespace/PVC с эквивалентным CA, повторные поиск/HTTPS |
| Legacy kit-v1 restore → kit-v2 upgrade | Реальный непустой backup 1 восстанавливается verified комплектом 1, затем upgrade 2; hashes/counts/PVC retention сохраняются |
| Граница upload | Файл MAX_UPLOAD_MB MiB плюс фактический multipart; допустимый проходит, сверхлимит отклоняется предсказуемо без обрыва/тихой потери |

- [x] Zero/idle tests используют controlled fixture, не ожидание настоящего LLM
  300 секунд. Не отключать heartbeat рабочего приложения ради idle теста:
  отдельный lab upstream fixture проверяет stalled stream, рабочий NDJSON — active/cancel.
  Не уменьшать upload limit для успешного gate. При выявленном frontend ограничении
  сохранить failed evidence и выделить исправление явно; эта задача не закрывается.
  TLS/SSO/model smoke — самостоятельный gate после deploy/restore: нынешняя
  readiness не проверяет модели и не подтверждает доверие IdP. A/B isolation
  проверять по уникальному документу-маркеру A до загрузки данных B; отсутствие
  результата при пустом запросе или одинаковые counts этого не доказывают.
- [x] Run regression из backend:
  `python -m pytest tests/test_kubernetes_chart.py tests/test_kubernetes_lifecycle.py tests/test_kubernetes_release.py tests/test_kubernetes_toolbox.py tests/test_kubernetes_home_access.py tests/test_kubernetes_runtime_trust.py tests/test_kubernetes_configuration_acceptance.py tests/test_kubernetes_chat_modes.py tests/test_chat_token_budget.py tests/test_kubernetes_network_probe.py tests/test_kubernetes_model_profile.py tests/test_model_stub_contract.py tests/test_deployment_profile.py tests/test_health_readiness.py tests/test_auth_sso.py -q`.
  Ожидание PASS, 0 skips. Из repository root на Linux выполнить
  `bash scripts/production/tests/test_backup_scripts.sh` и
  `bash scripts/production/tests/test_collect_diagnostics.sh` (existing contracts).
  Проверки local/Compose config не заменяют live production rollout.
  Список CI расширять относительно текущего workflow, сохраняя network/model/
  token-budget/stub checks. Gate стабильности из roadmap сохраняется: новые
  автоматические сценарии — 10 последовательных прогонов на изолированных
  fixtures, без десятикратной сборки images/полного A/B rollout. В report указать
  точный набор повторявшихся сценариев; runtime drill и его неповторённые части
  отражать отдельно, исключения — явно с владельцем и причиной.
- [x] Обновить CI focused test list, docs и report с actual IDs/versions, timing,
  counts и failed/unverified gates. Исторический dependency FAIL (5 high) сохранён;
  отдельная tinyglobby приёмка 05.10.2026 закрыла локальный dependency gate,
  см. [Dependency handoff](2026-10-04-audit-outbox.md#dependency-handoff-05102026).
  Это не подтверждение hosted release CI. Текущий audit file-profile gate остаётся
  RED (13 PASS / 3 FAIL), следующий этап — Task 3a.5 audit-плана. Cleanup только
  созданных lab resources после проверки backup; PVC retention измерить до удаления cluster.

## Завершение и handoff

Self-review плана: 14 критериев spec распределены по задачам 0–6; шесть Review Focus
имеют owning tests. Имена trust/env/volume/schema не расходятся между задачами.
Budget reserve — policy, latest docs — reference, фактическая patch CRD/TLS и
maximum upload — обязательные runtime gates. Тесты producer/consumer и
переносимых CLI не считаются успешной окруженческой delivery или live-model quality.

После просмотра плана сохраняется последовательное исполнение самим агентом
в существующей ветке. При реализации задачи идут без промежуточного «продолжать?»;
права на commit/публикацию/интеграцию рассматриваются отдельно. Review фактического
пакета, зелёные обязательные checks и доступные recovery артефакты предшествуют
утверждению результата. Предварительная оценка 4–7 ч.д. уточняется по фактической
декомпозиции, runtime failures не скрываются изменением критериев.
