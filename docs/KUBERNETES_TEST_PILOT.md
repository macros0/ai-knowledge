# Первая поставка в TEST: compact/bundled

Заказчик получает приложение вместе с PostgreSQL и Qdrant. Chart создаёт один
Pod frontend/backend, один worker и два StatefulSets хранения. Используются
четыре постоянных тома через existing PVC платформы; они не удаляются при uninstall. Обновления
выполняются с остановкой приложения. External storage и репликация не входят
в выбранную первую поставку.

## Минимальный ввод окружения

Оператор окруженческой копии заполняет приватный overlay по
[примеру TEST](../deploy/kubernetes/profiles/test-bundled.values.example.yaml):

- Явные Kubernetes context, отдельный namespace, quota, четыре PVC/StorageClass,
  разрешённые UID/GID и доступы к migration/maintenance Jobs и exec.
- Registry/repository и immutable digests backend/frontend; доступы к pull остаются
  в imagePullSecrets. PG/Qdrant поставляются по refs из базового chart; при
  обязательном mirror сохранить идентичное содержимое и заменить refs.
- HTTPS origin и маршрут платформы к `frontend:3000`, namespace её gateway,
  действующий сертификат/TLS и публичный CA bundle в immutable ConfigMap
  с ключом `ca-bundle.crt` (стандартные roots плюс корпоративные CA, без private key). Base example не создаёт
  платформенный Gateway/IdP. Заполнить narrow egress к IdP/LLM/embeddings по сети
  заказчика; пустой список блокирует внешний доступ при enforcing CNI.
- Существующий confidential OIDC client с client_secret_basic, без PKCE,
  realm/client/redirects и точное соответствие реальных групп ролям.
- Реальные LLM/embeddings endpoint/model/dimensions. Указанные example.invalid,
  replace-* и zero digests — placeholders; 1024 не универсальное требование.
  Корпус создаётся под выбранный embedding contract, менять его без реиндекса нельзя.
- Существующий runtime Secret: DATABASE_URL для нашей БД `okf_knowledge` пользователя `okf`
  на `postgres:5432` (пароль совпадает с POSTGRES_PASSWORD),
  POSTGRES_PASSWORD, APP_SECRET_KEY, KEYCLOAK_CLIENT_SECRET и нужные model API keys
  (LLM_API_KEY, EMBEDDING_API_KEY). Значения не передаются в общей документации,
  Git, CLI arguments или CI logs. Поддерживаемый способ предоставления Secret
  выбирает оператор платформы.

Совместимость actual admission/CSI/CNI/маршрута/SSO и применимые требования
платформы проверяются в целевой копии. Наличие примера не подтверждает допуск;
подтверждённое несовпадение исправляется узкой адаптацией. Домашний synthetic
home_access.py и frozen OCI kit не заменяют регистрацию/сборку TEST release.

## Один путь выпуска

1. Синхронизировать Kubernetes branch с committed main, пройти обычный и compact
   CI на конечном commit. Выпустить образы из него и записать release digests,
   chart version и runtime overlay. Секреты и backup остаются отдельно.
2. Заполнить private overlay и предоставить четыре пустых target PVC, runtime
   Secret и публичный trust ConfigMap. APP_SECRET_KEY сохраняется при обновлении;
   специальные DR правила описаны в [политике сессий](KUBERNETES_SESSION_RECOVERY.md).
3. Из source checkout выполнить команды ниже на согласованном context/namespace.
   `TEST_VALUES` — заполненный overlay вне Git; `BACKUP_ROOT` — защищённое
   хранилище оператора вне application PVC, с согласованным сохранением вне узла.
   Не использовать этот example без замены placeholder values.

```bash
python3 scripts/kubernetes/lifecycle.py preflight --context "$TEST_CONTEXT"   --namespace "$TEST_NAMESPACE" --expected-tier test --values "$TEST_VALUES"
python3 scripts/kubernetes/lifecycle.py deploy --context "$TEST_CONTEXT"   --namespace "$TEST_NAMESPACE" --expected-tier test --values "$TEST_VALUES"   --backup-root "$BACKUP_ROOT"
python3 scripts/kubernetes/smoke.py --context "$TEST_CONTEXT" --namespace "$TEST_NAMESPACE"
```

4. Пройти один целевой сценарий: Ready, вход и роли, небольшой контрольный DOCX,
   завершённая обработка, поиск, чат/источник с реальными моделями, перезапуск Pod
   с сохранением данных. Проверить backup и ручной restore контрольных данных
   до пользовательского корпуса. Ошибка миграции оставляет writers остановленными;
   неизвестный результат требует ручного разбора, без автоматического SQL rollback.
   [Ручной runbook](KUBERNETES_COMPACT_RESTORE.md) описывает лабораторный stopped
   restore с новой парой namespaces и synthetic CA: для TEST использовать ту же
   остановленную import/integrity/session-cleanup последовательность с регистрацией,
   сертификатами и private overlay целевой платформы. Home bootstrap не запускать
   на заказчике. TEST restore route/IdP/CA provisioning должен подтвердить оператор.
5. Зафиксировать одну короткую запись: commit/digests/values revision, результаты,
   ограничения, backup location и ответственный за остановку/восстановление.

Это пилот с maintenance window. PROD, HA, масштабирование и универсальная
fault matrix требуют отдельного выбора; новый обязательный пункт появляется
только при реальном отказе выбранного сценария или обязательном контракте TEST.
