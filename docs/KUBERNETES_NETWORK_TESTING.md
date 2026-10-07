# Домашняя проверка NetworkPolicy

Процедуры соответствуют коду ветки `codex/kubernetes-rollout` (compact baseline
`f551f5f` и последующие home tools); код ещё не интегрирован в `main`.
Копия документа в основном checkout не означает наличия там runtime реализации.
Для исполнения используйте checkout этой ветки и сверяйте открытые gates
с обновлёнными планом и отчётом домашней приёмки.

Проверяйте политики на отдельном kind с CNI, который их исполняет. Default
kindnet не подходит. Действующий демонстрационный cluster и его CNI сохраняются.
Используется тот же chart и штатные образы приложения с новым синтетическим
ci/stub corpus и отдельными PVC.

Фиксированная матрица: kind 0.33.0, Kubernetes 1.36.4 с digest в
`deploy/kubernetes/lab/kind-policy.yaml`, Cilium 1.20.2, Helm 4.3.0. Kubernetes 1.36
входит в [проверенную матрицу Cilium](https://docs.cilium.io/en/stable/network/kubernetes/compatibility/).
См. [официальную установку в kind](https://docs.cilium.io/en/stable/installation/kind/).
Для другого host сначала проверьте отсутствие пересечения Pod/Service CIDR с LAN,
VPN и другими кластерами. На текущей VM остаётся один control-plane на cluster;
это не испытание нескольких физических узлов.

Перед началом проверьте свободные RAM и диск. Дополнительный control-plane,
Cilium и ещё одна compact-копия требуют собственного запаса. При нехватке
остановите отдельные синтетические прогоны штатным способом; не уменьшайте
лимиты основного приложения и не изменяйте рабочие model services.

```bash
set -euo pipefail
context=kind-okf-policy
artifacts="$HOME/okf-policy-artifacts"
mkdir -p "$artifacts"
kind create cluster --name okf-policy --config deploy/kubernetes/lab/kind-policy.yaml
helm upgrade -i cilium oci://quay.io/cilium/charts/cilium --version 1.20.2 \
  --kube-context "$context" -n kube-system --set ipam.mode=kubernetes \
  --set kubeProxyReplacement=false --set operator.replicas=1 \
  --set resources.requests.memory=128Mi --set resources.limits.memory=1Gi
kubectl --context "$context" -n kube-system rollout status ds/cilium --timeout=300s
kubectl --context "$context" -n kube-system rollout status deploy/cilium-operator --timeout=300s
kubectl --context "$context" wait --for=condition=Ready nodes --all --timeout=300s
```

Устанавливаются CNI, CRD и cluster RBAC только в новом kind. kube-proxy остаётся;
Socket LB replacement не включается. До CNI node и CoreDNS могут быть NotReady.
После загрузки штатных `kube-smoke` backend/frontend/stub images в этот cluster:

```bash
kind load docker-image --name okf-policy \
  okf-backend:kube-smoke okf-frontend:kube-smoke okf-model-stub:kube-smoke
python3 scripts/kubernetes/lab.py --context "$context" --namespace okf-policy-run \
  --values-output "$artifacts/source.values.yaml"
python3 - "$artifacts/source.values.yaml" <<'PY'
import sys,yaml
from pathlib import Path
path = Path(sys.argv[1])
values = yaml.safe_load(path.read_text())
values['gateway']['namespace'] = 'okf-policy-gateway'
path.write_text(yaml.safe_dump(values))
PY
python3 scripts/kubernetes/lifecycle.py deploy --context "$context" --namespace okf-policy-run \
  --expected-tier ci --values "$artifacts/source.values.yaml" --backup-root "$artifacts/backups"
kubectl --context "$context" -n okf-policy-run exec deploy/application -c backend -- \
  python scripts/seed_backup_fixture.py --confirm-ci-fixture
python3 scripts/kubernetes/smoke.py --context "$context" --namespace okf-policy-run --synthetic-fixture
python3 scripts/kubernetes/network_probe.py --context "$context" --namespace okf-policy-run
```

При ошибке `kind load docker-image` из-за отсутствующих manifests других
архитектур используйте `docker save --platform linux/amd64 … -o <archive>`
и `kind load image-archive <archive> --name okf-policy`. Не меняйте image digests
PostgreSQL/Qdrant, чтобы обойти ошибку загрузки.

При `CreateContainerError` с отсутствующим `docker.io/library/import-…@sha256:…`
сравните `crictl inspecti <image-id>` и `ctr -n k8s.io images ls` на node.
В этом прогоне CRI нормализовал временное имя с префиксом `docker.io/library/`,
а в containerd осталась запись без префикса. Похожие ошибки описаны в
[kind issue 4184](https://github.com/kubernetes-sigs/kind/issues/4184).
Только на новой выделенной kind node удалены две конкретные временные записи
PostgreSQL через `ctr images rm`; каноническое имя и digest сохранены.
После перезапуска containerd на этой node CRI показал только канонический
digest, PostgreSQL стал Ready. PVC и другие images не удалялись.
Не переносите эту операцию на действующие узлы платформы: это диагностика
конкретного импорта домашнего стенда. Maintenance lock снимался штатным
`recover-lock` лишь после проверки отсутствия application/operation writers;
затем установка выполнялась заново.

Runner требует Ready Cilium и профиль ci/stub/auth-disabled. Он создаёт два
маленьких control Pod: в разрешённом Gateway namespace и в новом постороннем
namespace. Подтверждает живые endpoints и разрешённые соединения приложения
с backend, frontend, PostgreSQL, Qdrant и stub, DNS и доступ Gateway к frontend.
Затем проверяет запрет входа постороннего Pod ко всем этим Services, запрет
прямого Gateway-доступа к backend и внешний egress приложения/stub.

Отказом политики считается только timeout при наличии положительного контроля
доступности адресата. `connection refused`, ошибка DNS и неожиданный отказ
делают тест красным. Успешный прогон удаляет только свои control Pod и созданный
посторонний namespace; ошибка сохраняет их для диагностики.

Этот тест проверяет chart NetworkPolicy на одной Cilium node. Фактический CNI
DEV/TEST/PROD, внешние IdP/model endpoints, межузловая маршрутизация, VSO и
платформенные ограничения требуют отдельной приёмки. Приложение и его PVC
сохраняются после теста; удаление cluster не является backup или rollback.

На малой VM после успешной приёмки выполнен штатный `lifecycle.py backup`
с `--expected-tier ci` и тем же values; проверенная копия находится вне kind.
Затем остановлен только контейнер `okf-policy-control-plane` через `docker stop`.
Основной cluster, данные и PVC не удалены. Для продолжения этого прогона:

```bash
docker start okf-policy-control-plane
kubectl --context kind-okf-policy wait --for=condition=Ready nodes --all --timeout=300s
kubectl --context kind-okf-policy -n kube-system rollout status ds/cilium --timeout=300s
kubectl --context kind-okf-policy -n okf-policy-run rollout status deploy/application --timeout=300s
```

Перед стартом вновь проверьте RAM; после перезапуска повторите smoke/probes.
