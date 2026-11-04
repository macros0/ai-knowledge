# VM103 cross-node rehearsal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline. Use the existing replicas ledger, fresh operation journals and no new reviewer/subagent.

**Goal:** Проверить общие файлы и перенос принятой worker-задачи между двумя логическими Kubernetes-узлами на выбранной пользователем VM103.

**Architecture:** Отдельный kind-кластер `okf-crossnode`: schedulable control-plane и worker. PG/Q остаются на control-plane, приложение распределяется между узлами, файлы — на новом собственном CSI PVC Synology. Существующие `okf-policy`/`okf-kube` и прежние PVC сохраняются. Это проверка логического переноса, без квалификации физических доменов отказа.

**Tech Stack:** Existing kind0.33.0/Kubernetes1.36.4, Cilium1.20.2, CSI NFS4.13.6, Synology DS211/NFSv4.0, current96931d1 backend.

**Spec:** [Согласованный storage design](../specs/2026-10-31-kubernetes-replicas-storage-design.md), [O1/O3](2026-10-31-kubernetes-replicas-operations.md).

## Global constraints and platform gate

- Пользователь выбрал VM103 в `call_e84edb240ebb4300b46ca1ac14468b6f`. Это выбор топологии; отдельный platform-допуск для изменения storage node обязателен по O1.
- Review package: `tests/tmp/replica-compact-live-lab/two-node-vm103-review/platform-proposal.json`, operation `70f13c26a1ea4b308d638ad0c264a5c6`, SHA256 `c1de06d2075da141f42037c8cae269e630a9fed2219013e740ef784803aa2cdf`.
- Пакет подготовлен, не применён. В нём точные команды, хэши13 файлов,43 объекта Cilium/CSI/CRD/namespace/StorageClass и восемь image digests. Из них10 — явно зафиксированные Cilium CRD; автоматическое создание CRD отключено. Hubble выключен, Secret в manifests нет. Никакие сгенерированные при первом рендере временные TLS-материалы не передавались и не применялись.
- Proxmox `10.10.1.50`, VM103 UUID `25f9c3e7-255d-495c-a685-be2de0abb224`, `scsi0=local-lvm:vm-103-disk-1`. Планируемое расширение100→130GiB, затем только конец `/dev/sda1` и ext4; начало/UUID и остальные разделы неизменны. Уменьшение диска не поддерживается, обратное сжатие запрещено. Reboot, другие VM/CT и NAS configuration в пакет не входят.
- Текущие13,26GB free лишь на2,97GB выше10% floor. Новый бюджет12GiB поверх неизменного10% floor проверяется после расширения. RAM VM остаётся10GiB; перед созданием нужно минимум4GiB MemAvailable, при работе минимум1GiB. Для своих старых учебных ролей/провайдеров допустимы новые временные capacity loans с обязательным полным возвратом. Не снижать floor, не prune и не удалять старый scratch.
- API `127.0.0.1:26443`, отдельный owner-only kubeconfig в operation directory; все команды обязаны указывать его явно. Pod subnet10.248/16 и service subnet10.250/16 не пересекаются с host routes и CIDRs обоих старых кластеров: policy10.246/16+10.98/16, kube10.244/16+10.96/16. Остановленный старый kube-контейнер проверен чтением файлов без запуска; API-порт свободен. Перепроверить перед созданием.
- Не повторять неизвестные resize/growpart/create/apply/stop/import. Сначала durable intent, затем чтение фактического размера/UID/CID/spec. При частичном создании сохранять узлы и evidence. Не удалять старый кластер для экономии ресурсов.
- Production scheduleOFF; real model/IdP/Gateway, physical host/power loss и resilient certification остаются отдельными gates.

## Review focus

Переезд считается только при разных реальных Node UID/native CID и доказанном
завершении принятой задачи с одной публикацией. Local PV PG/Q не должен переехать
на пустой диск. Остановка нового worker не разрешает остановку control-plane,
VM-хоста или прежнего узла. Зависший NFS mount не даёт права force-delete.

## Task 1: concrete package and admission

- [x] Read-only Proxmox identity/config digest, thin pool37.75%/metadata1.46%, VM103 running; guest GPT/ext4/UUID/start sectors, existing node UID and routes captured. Exact-IP saved SSH key matches; outdated alias rejected without changing SSH trust.
- [x] Render pinned two-node config, Hubble-disabled Cilium and existing CSI chart with new placement, explicit10 CRD and nondefault Retain NFSv4.0 StorageClass. No shared Secrets or corpus transferred.
- [x] Strict client schema dry-run43 objects exit0 against existing API discovery, unique object inventory/image SHA/subnet checks passed. This is not a new-cluster server validation or live CNI result.
- [ ] Obtain explicit platform authorization for the exact reviewed disk/node/CNI/CSI package before any infrastructure mutation.

## Task 2: disk growth and isolated cluster

**Files:** reviewed `commands.txt`, `kind.yaml`, `cilium*.yaml`, `csi*.yaml`, `namespace-and-storageclass.yaml`; new private operation journal with per-action intent/result.

- [ ] Recheck exact PVE config digest/volume/UUID and existing source backup/closure. Save GPT bytes independently on this PC; verify readable before growth.
- [ ] Execute absolute `qm disk resize 103 scsi0 130G --digest <reviewed digest>` once. Observe actual139586437120 guest bytes before any partition change. Config drift stops the operation; no skiplock.
- [ ] Read-only growpart dry-run must change only partition1 end/size. Execute growpart once, observe exact geometry, then resize2fs once; verify original ext4/partition UUIDs and unchanged boot/EFI partitions. Do not shrink/reboot on failure.
- [ ] Start fresh capacity-loan journals only for owned old training roles/providers. Preserve PG catalog, full Q points/config, PVC/runtime/nine canonical tables and live S3 hashes; verify memory/disk floors and all original backup sources remain fenced.
- [ ] Confirm both old API CIDRs, target port/cluster/directory absence and pinned node image. Create once with `--retain` and explicit new kubeconfig. Record actual node/container IDs and image digests.
- [ ] Apply only the reviewed43 objects in the new cluster. Wait for CRDs, both nodes, Cilium/Envoy, CSI registration and DNS. Verify cross-node Service traffic and native CNI negative controls before any app credentials are admitted. Preserve partial failed objects; no blind create replay.

## Task 3: cross-node filesystem and application baseline

**Interfaces:** consumes admitted platform inventory and unchanged96931d1 runtime; produces exact per-node claim/native runtime/HTTP/SQL baseline.

- [ ] Create a new namespace and owned Retain PVC, fresh NAS subdirectory. Separate non-root Pod processes on different Node UIDs verify256MiB cold SHA/range/conditional conflict; remove only their own objects. Shared kernel remains an explicit limit.
- [ ] Deploy fresh managed PG/Q pinned to stable control-plane and mixed external RWX app. Scoped TLS/auth bootstrap, signed identity, anti-affinity/spread and actual2/2/2 node placement must pass. No reuse of old namespace credentials/PVCs.
- [ ] Verify shared session on both frontend/API, idempotent upload, download/search/export and native four-process hashes/scratch/lease/publication state. Capture independent encrypted backup on this PC before worker loss.

## Task 4: accepted worker loss and closure

**Files:** operation-specific fault/observer helper and meaningful guard regression tests under `tests/tmp`; public report under `docs/superpowers/reports/`.

- [ ] Before implementing the fault helper, write failing guards for wrong cluster/node/CID/image/operation, stable-storage placement, absent owner/backup, unknown stop intent and foreign mounts; watch RED then implement GREEN. No root fault until the exact native ownership evidence passes.
- [ ] Accept unique synthetic document, prove SQL worker owner/attempt/candidate and model active on the new worker. Stop only that worker container after persisting intent; retain control-plane and PG/Q. Record lease expiry/new owner/attempt and fencing; publication must remain exactly once.
- [ ] Verify completion and both old/new session/SHA/search/export across surviving processes. Restart only the same owned worker, wait for Ready and confirm original claim identities, no late stale publication or duplicate job effect. Report measured intervals without assigning an RTO SLA.
- [ ] Close owned forwards/mounts; stop new app/providers under held maintenance with PVCs retained. Return all old role/provider loans and recheck runtime/PVC/nine tables/full PG/Q and cold S3 hashes. Leave no unknown intent or active capacity debt.
- [ ] Scan public outputs for protected literals/links, run mandatory project check and relevant guard tests, publish bounded evidence and update O1/O3. No full-plan completion or certification claim from this topology.

The [kind configuration reference](https://kind.sigs.k8s.io/docs/user/configuration/)
documents multi-node config/loopback API; the [Cilium kind recipe](https://docs.cilium.io/en/stable/installation/kind/)
documents disabled default CNI and Kubernetes IPAM. Versions above are explicitly
pinned to the existing tested lab, rather than selected from the moving examples.
