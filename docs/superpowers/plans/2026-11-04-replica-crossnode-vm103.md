# VM103 cross-node rehearsal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline. Use the existing replicas ledger, fresh operation journals and no new reviewer/subagent.

**Goal:** Проверить общие файлы и перенос принятой worker-задачи между двумя логическими Kubernetes-узлами на выбранной пользователем VM103.

**Architecture:** Отдельный kind-кластер `okf-crossnode`: schedulable control-plane и worker. PG/Q остаются на control-plane, приложение распределяется между узлами, файлы — на новом собственном CSI PVC Synology. Существующие `okf-policy`/`okf-kube` и прежние PVC сохраняются. Это проверка логического переноса, без квалификации физических доменов отказа.

**Tech Stack:** Existing kind0.33.0/Kubernetes1.36.4, Cilium1.20.2, CSI NFS4.13.6, Synology DS211/NFSv4.0, current96931d1 backend.

**Spec:** [Согласованный storage design](../specs/2026-10-31-kubernetes-replicas-storage-design.md), [O1/O3](2026-10-31-kubernetes-replicas-operations.md).

## Global constraints and platform gate

- Пользователь выбрал VM103 в `call_e84edb240ebb4300b46ca1ac14468b6f`. Это выбор топологии; отдельный platform-допуск для изменения storage node обязателен по O1.
- Review package: `tests/tmp/replica-compact-live-lab/two-node-vm103-review/platform-proposal.json`, operation `70f13c26a1ea4b308d638ad0c264a5c6`, SHA256 `c1de06d2075da141f42037c8cae269e630a9fed2219013e740ef784803aa2cdf`.
- Пакет отдельно разрешён пользователем и применён один раз. В нём точные команды, хэши13 файлов,43 объекта Cilium/CSI/CRD/namespace/StorageClass и восемь image digests. Из них10 — явно зафиксированные Cilium CRD; автоматическое создание CRD отключено. Hubble выключен, Secret в manifests нет. Никакие сгенерированные при первом рендере временные TLS-материалы не передавались и не применялись.
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
- [x] Obtained explicit platform authorization `call_dd93b9225d464094a786a12d8deb4473` for the exact reviewed disk/node/CNI/CSI package before any infrastructure mutation.

## Task 2: disk growth and isolated cluster

**Files:** reviewed `commands.txt`, `kind.yaml`, `cilium*.yaml`, `csi*.yaml`, `namespace-and-storageclass.yaml`; new private operation journal with per-action intent/result.

- [x] Rechecked exact PVE config digest/volume/UUID and existing source backup/closure. Saved primary34/secondary33 GPT sectors independently on this PC with readback/SHA verification.
- [x] Executed absolute `qm disk resize 103 scsi0 130G --digest <reviewed digest>` once; observed139586437120 guest bytes before partition change. No skiplock.
- [x] Dry-run and actual growpart changed only partition1 end/size. resize2fs ran once; original ext4/partition UUIDs and boot/EFI partitions preserved. An overly high statvfs125GiB heuristic refused after the command; read-only complete block-count verification reconciled the result without repeating resize.16 guard cases passed, with negative tests observed RED first. No shrink/reboot.
- [x] Fresh primary role/four provider loan journals preserve PG109/full Q/PVC/runtime/nine-table baselines. Initial3.33GB RAM pre-create refusal preserved4GiB floor; a new idle-role loan from the previously owned22-document r5o2u stand raised available RAM to5.83GB. All six loans were subsequently returned after final closure with runtime/PVC/data checks.
- [x] Confirmed old CIDRs, absent port/cluster/directory and pinned node image; created once with `--retain` and separate0600 kubeconfig. Two actual Node UID/container/image bindings recorded.
- [x] Applied43 reviewed objects once. Both nodes/17 platform Pods Ready0restarts, CSI registered on both; cross-node PodIP and DNS/Service HTTP passed. A denied client timed out and the allowed client passed again afterward. Three probe Pods Succeeded/CRI0; no app credentials were present during this gate.

## Task 3: cross-node filesystem and application baseline

**Interfaces:** consumes admitted platform inventory and unchanged96931d1 runtime; produces exact per-node claim/native runtime/HTTP/SQL baseline.

- [x] New namespace/Retain PVC/fresh NAS subdirectory. Separate non-root processes on different Node UIDs verified256MiB put18.621s/read47.492s/SHA/range/conflict. Own object/empty directories removed; both Jobs Succeeded/CRI0/mount0 on both nodes. Shared kernel and OS cache remain explicit limits.
- [x] Fresh managed PG/Q pinned to stable control-plane; new scoped TLS/auth bootstrap passed81 tables/10 denials/correct-wrong-correct SCRAM, signed identity and strict spread. Actual frontend/API/workers2/2/2, one of each role per node, zero restarts. Six frontend→storage denials and four API positives passed with host→NAS positive control. No old namespace credentials/PVCs reused.
- [x] Shared session/idempotent upload/download/search/export passed four routes. Four native processes verified six cold hashes/scratch0/one upload audit/two business tasks once at epoch0.
- [x] Independent PC backup:8 authenticated encrypted objects/PG81 tables28 sequences/Q2 points/6 blobs; master key stayed on PC. Reader/native snapshot handles closed, source-off under held1 proved before official release. This frozen backup precedes the later three synthetic documents.

## Task 4: accepted worker loss and closure

**Files:** operation-specific fault/observer helper and meaningful guard regression tests under `tests/tmp`; public report under `docs/superpowers/reports/`.

- [x] Before implementing the fault helper, write failing guards for wrong cluster/node/CID/image/operation, stable-storage placement, absent owner/backup, unknown stop intent and foreign mounts; watch RED then implement GREEN. No root fault until the exact native ownership evidence passes.
- [x] Accept a unique synthetic document on the selected worker. Hold the own namespace's LLM capacity-resource row with a bounded native SQL gate: prove running owner/attempt/candidate, target-Pod client waiting on that gate, zero provider owners and actual model-active0. Persist intent and stop only the exact owned new worker node; retain control-plane/PG/Q. Release the gate only after the target process is gone; observe default120s lease expiry/new owner/attempt and one publication on the surviving node.
- [x] Verify completion and both old/new session/SHA/search/export across surviving processes. Restart only the same owned worker, wait for Ready and confirm original claim identities, no late stale publication or duplicate job effect. Report measured intervals without assigning an RTO SLA.
- [x] Close owned forwards/mounts; stop new app/providers under held maintenance with PVCs retained. Return all old role/provider loans and recheck runtime/PVC/nine tables/full PG/Q and cold S3 hashes. Leave no unknown intent or active capacity debt.
- [x] Scan public outputs for protected literals/links, run mandatory project check and relevant guard tests, publish bounded evidence and update O1/O3. No full-plan completion or certification claim from this topology.

The [kind configuration reference](https://kind.sigs.k8s.io/docs/user/configuration/)
documents multi-node config/loopback API; the [Cilium kind recipe](https://docs.cilium.io/en/stable/installation/kind/)
documents disabled default CNI and Kubernetes IPAM. Versions above are explicitly
pinned to the existing tested lab, rather than selected from the moving examples.

Ruling04.11: this node-loss case targets accepted document processing before
provider admission. `model_budget` deliberately marks in-flight calls
`release_required=True`; expiry alone cannot acknowledge an uncertain external
request. A native row gate isolates worker takeover without inventing a provider
completion or clearing slots. The already completed NAS outage covered a live
model call with its original worker alive. Active-provider node loss requires a
separate explicit stopped-provider reconciliation case and is not qualified here.

Observed first node-loss trial: automatic takeover failed because both DNS Pods
were on the lost worker and its dead TCP SQL backend retained transaction locks.
Exact vanished-Pod/backend reconciliation recovered the same Lyra work at
attempt2/fence2; it is explicitly assisted, not automatic PASS. Old and new
HTTP/native SHA/session/export checks passed afterward. Failed evidence remains.
Managed PostgreSQL TCP keepalive fix `ccc2678` has2 RED→9 GREEN chart tests and
mandatory project checks PASS. The owned new-cluster DNS Deployment was spread
across actual nodes while writers were stopped under held2; same PVC/identity
were preserved. The fresh Cygnus trial completed automatically on control-plane by210.36s
while the source worker remained off; attempt2/fence2, problem=null. No manual
backend termination, lease/clock/slot change or accepted request replay occurred.
The same worker returned at240s;2/2/2 actual spread, all4doc SHA/BM25/session/three
exports and4 native processes23coldSHA/scratch0/4audits/7exact business tasks
passed. Source app/providers are off held3/CRI0/mount0both/3PVCretained.
All six capacity loans returned: primary4-doc and extra22-doc stands2/2/2,
exact runtime/PVC/nine canonical tables each; full PG109-entry catalog and Q
content preserved,4 cold processes verified8 original S3 blobs. Newnodes retained
off/oldcontainers unchanged. Final [bounded report](../reports/2026-11-04-replica-crossnode-vm103.json).

Final checks: mandatory check-project PASS (audit0/frontend756+1 optional/Ruff0),
33 acceptance-policy tests PASS, disk16/worker22 guards PASS,35 embedded/native
programs plus2 background workers compile.45 public files checked against189
protected literals; no exposures/broken local links. Full backend CI was not
repeated for the chart/document-only change; backend image/source are unchanged.
