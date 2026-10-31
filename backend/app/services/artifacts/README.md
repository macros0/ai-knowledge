# Artifact adapter foundation (S1)

LocalBlobStore, RwxBlobStore and S3BlobStore are an unconnected foundation; the
existing pipeline still uses its existing generation directories. The factory
selects an adapter from artifact settings. DB bindings, migration and pipeline
activation remain pending; distributed backend execution is still rejected.

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

RWX requires an existing mount root and uses the same exclusive hard-link layout.
An existing directory does not prove a shared mount: real CSI/node restart and
concurrency acceptance remains mandatory.

S3 uses explicitly supplied endpoint/bucket/prefix, credentials, region and CA;
there is no ambient AWS credential discovery. HTTPS verification is mandatory;
plain HTTP requires an explicit development opt-in. The SDK before-send hook
rejects changes of authority, including redirects. Single PUT and multipart
completion use If-None-Match: *; unsupported conditional writes fail. Conflicting
retries verify the complete existing object. Upload/download spool to bounded
local scratch (2 GiB default), and reads verify full SHA256/size before exposing
the requested range. ETag and metadata are not integrity evidence. Failed
multipart writes attempt abort; operators still need orphan-upload lifecycle
cleanup. A missing bucket is an error, not successful deletion.

The shared contract suite uses an in-memory S3 fixture plus actual botocore
request-model/hook tests. These do not certify a customer S3 service. Provider
quota/access/timeout/restart/conditional-write acceptance remains open.
