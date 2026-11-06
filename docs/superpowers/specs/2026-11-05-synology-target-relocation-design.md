# Selected Synology target relocation

D4 of the existing replicas/storage plan restores the already authenticated
synthetic NAS copy into a new namespace and a disjoint CSI directory on the
same selected DS211. No new NAS setting, export, user, quota or platform driver.
Production remains off and no customer/provider certificate is issued.

## Selected approach and authority

Keep `authorized-nfs-csi-4.13.6-training-v1` strict at NFS4.1/UID1000. Add an
explicit branch for the existing authenticated source recipe
`synology-ds211-dsm6.2.4-25556-u8-nfs4.0-csi4.13.6-training-v1`. Reuse the
format3 verifier, import, held identity adoption, credential separation and
explicit release. Generic NFS support or a flag skipping source proof is outside
this task. This implementation choice follows the user's standing autonomous
instruction and the already selected NAS-target4.0 requirement.

The NAS backup scope authenticates the exact adjacent source-binding object.
Its provenance is `selected-nas-readonly-client-source-v1`; no server filesystem
identity was observed. Validate its complete bounded schema, namespace and
nonzero UUIDs, selected server10.10.1.5/share`/volume1/Vitaliy`, driver
`okf-training-nfs.csi.k8s.io`, exact CSI handle/subdirectory/export, immutable
reader image/code hashes, readonly4.0/root mount and wireUID1031/G100. Preserve
the explicit `server_filesystem_identity_claimed=false`. The recorded source
directory is client-observed UID0/G100/mode2770; do not fabricate UID1031 ownership.

The target observer reads its own Linux mountinfo and pinned directory stat.
Require one whole-root NFS4 mount, TCP/sec=sys/vers4.0/hard/address10.10.1.5,
the same selected share and a new namespace/claim-derived CSI directory disjoint
from the source. Require a different observed inode, recorded directory owner/
group and restrictive group-writable mode; the process uses wireUID1031/G100.
Client device/mount IDs are ephemeral. Proof names remain client observations,
with no server filesystem identity claim. Reobserve the proof around the artifact
probe and SQL adoption through the existing official identity command.

## Rehearsal and completion

Use the retained two-document/81-table/28-sequence/four-point/eleven-blob PC
archive and its authenticated binding. Keep the original source application,
providers and source directory unmounted/off. New target uses retained existing
CSI4.13.6/StorageClass, fresh PVCs and owned managed PostgreSQL/Qdrant. Never
clear existing NAS folders; write only the new claim directory. Prove source
isolation before import. Verify authenticated import and all catalog/vector/blob
hashes, held jobs and no legacy task replay. Adopt new identity/scoped accounts,
revoke old sessions, explicitly release, test2/2/2 old/new HTTP plus cold SHA,
then fence/stop/retain the target, close owned handles and return capacity.

Source changes require corresponding tests and a verified target image. All
historical receipts remain immutable. Existing failed trials and provider gates
are preserved. This closes the selected target recipe, without promising power
loss durability or physical resilience of this NAS.
