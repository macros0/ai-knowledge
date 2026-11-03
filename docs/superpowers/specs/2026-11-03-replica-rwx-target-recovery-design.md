# Selected NFS RWX target recovery

The approved O2 work includes independent recovery into fresh storage. The RWX
source→real S3 path is complete. The remaining target-RWX path must prove physical
relocation even when source and target settings both name `/artifacts`. Execute
inline under the user's standing autonomous instruction; no new platform scope.

## Decision and boundary

Support only the authenticated `authorized-nfs-csi-4.13.6-training-v1` source
recipe, external artifacts provider, and its observed NFSv4.1/TCP/sec=sys mount
layout. Keep the existing S3 path and all ownership/schema/auth/epoch checks.
Other RWX drivers, IPv6, aliases and unproven filesystem layouts fail closed.
This is a training recovery recipe, not general provider or HA certification.

Alternatives: comparing local paths cannot prove relocation; permitting arbitrary
RWX backends would discard the current guard. A fresh mount with a manifest-bound
source identity provides the required narrow evidence without changing BlobRefs.

The independent PC source-binding JSON is accepted only when its canonical digest
equals the artifact scope in the already-authenticated BackupPlan, with exact
recipe/provider/source namespace and strict coordinate types. The archive master
key stays on the PC. The CLI consumes the coordinator's verified plan and binding;
it does not independently authenticate the archive.

## Physical proof

The real read-only spike used a fresh retained claim/PV, namespace
`replica-test-rwxt20261103`, and a restricted non-root Pod without Secrets or a
service-account token. Mountinfo reports root `/`, exact `/artifacts` mountpoint,
`nfs4`, server `172.18.0.1`, CSI export path, `vers=4.1,proto=tcp,sec=sys`.
Client statvfs filesystem ID is zero and cannot be used as a source filesystem ID.
The fresh root inode is8194; authenticated server-native source inode is8193.

Observe the target inside the operator process, never through a caller-supplied
success boolean. Require a canonical existing absolute root without symlinks, one
exact NFS mountpoint, root `/`, the same canonical source server, the selected
mount options and a new namespace/CSI claim UUID export path. Reject source paths,
ancestor/descendant paths, source inode reuse and unsupported or ambiguous mounts.
Hold a no-follow directory descriptor while checking metadata and mount device;
refuse replacement during observation. Do not use inode/device values from local
disk as NFS evidence. Observation itself writes nothing.

Before the artifact probe can write, validate relocation. Reobserve after the
probe and inside the owned SQL adoption. Add the stable target binding digest
(server/export/root inode/owner/mode plus authenticated source scope) to the
restore storage receipt, so same-operation resume refuses a different target.
Ephemeral client device/mount IDs are observation checks, not persistent identity.
No raw backup/source binding contains credentials.

RWX adoption requires the restored control descriptor to match the supported RWX
source shape and logical store ID. Equal local root descriptors are allowed only
after physical proof. New installation/namespace, held source epoch, schema and
fingerprint anchor, provider-owner exclusion, transactional auth invalidation,
held target epoch and explicit later release remain mandatory. Source auth and
identity must remain unchanged on every refusal or rolled-back failure.

## Validation and operations

Tests cover valid same-path relocation/resume, tampered/missing binding, wrong
namespace/recipe, same/parent/child export, source inode alias, local/stacked/bind
mounts, malformed options, symlinks, changed target on resume, provider ownership,
and unchanged S3 behavior. CLI proof must precede any probe write.

Then run native current-image proof and independent restore into fresh PG/Qdrant
plus the new RWX claim; old business tasks must not reexecute before release.
Check cold hashes and application2/2/2 functionality, close under maintenance and
retain PVCs. The same NFS server serves both claims in this recipe: source app/PG/Q
must be off and the old claim unmounted, but do not claim that NFS itself is off.
The earlier S3-target drill supplies actual whole-source-server-off evidence.
Large buffered NFS writes remain failed/unqualified. Production schedule staysOFF.
