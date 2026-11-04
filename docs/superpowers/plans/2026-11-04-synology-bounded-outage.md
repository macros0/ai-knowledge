# Selected Synology bounded outage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline. Track checks and uncertain intents in the existing replicas ledger. No new reviewer or subagent.

**Goal:** Проверить ограниченную по времени недоступность выбранного внешнего NFS и сохранность двух собственных объектов после возврата связи.

**Architecture:** Новый непривилегированный probe Job использует отдельный CSI PVC. Одна новая nftables-таблица внутри network namespace kind блокирует только TCP к NAS2049; единственный timed-set element истекает через150 секунд без обновления пакетами. NAS, firewall VM-хоста и прежние приложения не изменяются.

**Tech Stack:** Existing CSI4.13.6, Synology DS211 DSM6.2.4-25556 Update8/NFSv4.0, current96931d1 image, nftables timed set.

**Spec:** [Согласованный storage design](../specs/2026-10-31-kubernetes-replicas-storage-design.md), [O1/O3 operational gates](2026-10-31-kubernetes-replicas-operations.md).

## Global Constraints

- Не изменять storage nodes без отдельного platform допуска: прежнее разрешение на NFS/CSI не включало новую инъекцию node firewall.
- Пакет подготовлен, не применён: `tests/tmp/replica-compact-live-lab/synology-outage-d9d88fc44c2b-review/platform-proposal.json`; operation `d9d88fc44c2b4b9ab1621f834176345d`.
- Node UID `9020a1d9-843e-4947-8808-53477289b863`; container ID, image, отдельный network namespace и исходный OUTPUT hash закреплены в пакете и повторно сверяются перед изменением.
- Только `10.10.1.5`, TCP2049,150 секунд. Не обновлять timeout, не делать flush/restore общего ruleset. Не трогать NAS configuration, SMB, SSH, VM-host firewall или старые PVC.
- Новые namespace/PVC/ConfigMap/NetworkPolicy/Job — ровно пять объектов. PVC128Mi — запрос, не NAS-квота; Job192Mi/non-root1031:100/noSecrets/noToken/read-onlyRoot. Два объекта по1MiB.
- Источник NAS остаётся off/held1/unmounted; S3 restore target off/held3. Прежний стенд2/2/2 не масштабировать. При нехватке1GiB MemAvailable или disk floor — отказ до создания Job, без prune.
- Никаких forced Pod deletion при NFS D-state. Не повторять неизвестные create/apply/write; сначала журналы, UID, native CID и nft handle/spec.
- Component result не означает полный application outage, физический отказ или resilient certification. Production scheduleOFF.

## Review Focus

1. Новый или чужой NAS mount на node: отказаться от инъекции, даже если probe готов.
2. Потеря SSH/controller после apply: timed set истекает в ядре; таблица остаётся инертной до безопасной уборки.
3. Повтор apply после потерянного вывода: запрещён; одноразовый `create table`, durable intent и inspect существующей таблицы.
4. Cached read или отсутствие DROP: не выдавать PASS блокировки I/O по одному таймеру/сну.
5. Чужая таблица/изменённый handle/spec или файлы в probe PVC: отказаться от cleanup, сохранить evidence.

## Task 1: reviewable package and read-only preflight

**Files:** `tests/tmp/prepare_synology_outage_review.py`, `synology_outage_probe.py`, `render_synology_outage_review.py`; four rendered files in the review directory.

- [x] Bind current node/container/network namespace/image and original OUTPUT hash; host NAS TCP positive control; no NAS mounts and no firewall mutation.
- [x] Render `fault.nft`, `cleanup.nft`, `probe.py`, `manifests.json`; record SHA256 in proposal.
- [x] Actual `nft -c -f -` exit0; table absent and original OUTPUT unchanged afterward.
- [x] Exact package-byte verification caught Windows LF→CRLF conversion in two nft files before commit; wrote the reviewed LF bytes explicitly, rechecked all SHA256 and repeated dry-run on actual file content. No rule was applied.
- [x] Actual Kubernetes client schema dry-run exit0/five objects; namespace remains absent. Initial observer expected one JSON document, but kubectl emits five; preserved failure and used read-only `-o name` validation.
- [ ] Obtain separate platform authorization for this exact package. No live resources or firewall applied at this checkpoint.

## Task 2: owned baseline and one bounded fault

**Interfaces:** Consumes package SHA256 plus explicit platform authorization; produces private local/VM operation journals with created resource UIDs, exact probe PID/CID, PVC/PV binding, nft table handle/spec and stage receipts.

- [ ] Revalidate source/target closure and old2/2/2/runtime/PVC/nine canonical tables. Create only five recorded absent objects, journaling each intent; require native current image/zero restarts and fresh NAS export subdirectory containing this PVC UID.
- [ ] Probe verifies NFSv4.0 RW/1031:100/fresh empty root, writes/cold-checks its old1MiB object and requests cache eviction. Persist baseline scratch receipt before injection.
- [ ] Read-only node mount inventory must contain no NAS source except this fresh probe claim; bind exact export and root inode. Host NAS TCP positive control must pass.
- [ ] Persist single apply intent, check absent table and original OUTPUT digest, then atomically load reviewed nft file and record exact table handle/spec. Signal `/scratch/start` only for exact probe Pod/CID; never use NFS for control markers.
- [ ] Observe positive native DROP counter and in-flight probe I/O while element is active; record actual timeout/expires and host positive control. No sleep-only outage proof or timeout refresh.
- [ ] Observe natural element expiry; same probe process must verify old SHA and new fsynced/cold SHA. Record actual blocked/read/write/recovery durations. If reads were cached, report that boundary rather than claim blocked cold reads. Late/failed I/O stays unqualified; inspect within600s without force-kill.

## Task 3: exact cleanup and final evidence

- [ ] Verify table family/name/operation comment/handle and complete owned chain/set/rule spec before deleting only this table. Recheck table absent and original OUTPUT digest unchanged; never load a saved global ruleset.
- [ ] Require both expected hashes and no foreign entries before signalling `/scratch/cleanup`. Probe deletes only its two exact BlobRefs and empty child directories; preserve other content on any mismatch.
- [ ] Job Succeeded/nativeCRI0/NASmount0/newPVC retained, source namespaces remain stopped and old2/2/2/runtime/PVC/nine canonical tables unchanged. Close all handles and capacity debts before PASS.
- [ ] Publish bounded component result, actual failed/cached/late boundaries and remaining full application/cross-node/customer gates; scan secrets/links, run mandatory project check before commit.

Timed element semantics and atomic batch behavior follow the upstream
[nftables timed-set documentation](https://wiki.nftables.org/wiki-nftables/index.php/Element_timeouts)
and [atomic rule loading](https://wiki.nftables.org/wiki-nftables/index.php/Atomic_rule_replacement).
