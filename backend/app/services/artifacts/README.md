# Artifact adapter foundation (S1)

LocalBlobStore is an unconnected foundation; the existing pipeline still uses
its existing generation directories. RWX/S3 adapters, DB bindings, migration and
runtime selection are separate pending tasks. Local-only does not enable
distributed execution.

Object IDs are server-generated 32-character lowercase hex strings. Put streams
to a temporary file, fsyncs it and publishes through an exclusive hard link;
existing IDs are never replaced. Filesystems without atomic hard-link support
fail explicitly. Same bytes may retry the same ID; different bytes conflict.
Do not substitute rename-over-existing as a fallback.

The root and its contents must be owned by the service. Existing shared link
guards reject symlinks/reparse points inside it; operator-owned ancestors are
canonicalized. This is not isolation against another process with the same OS
permissions that can rewrite the root during operations. Real filesystem/CSI
guarantees require platform acceptance.

Open verifies the full SHA256/size on the same handle before exposing a bounded
range; this prioritizes integrity and costs a full pass before streaming. GC
coordination and read leases belong to S3/R2, before shared deployment is enabled.
Materialization rechecks provider bytes and cleans parser scratch on exit.
The returned path is named source; parser callers must retain document format
metadata when integrating it. No original filenames enter artifact keys.

Storage I/O errors are classified without exposing paths; cleanup failure does
not replace a primary error. Cleanup can leave an orphan if OS permissions stop
unlink, to be handled by later maintenance. Caller exceptions inside the open
context are preserved, rather than misclassified as storage errors.
