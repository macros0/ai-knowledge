"""Narrow Linux/NFS relocation proof for the selected authenticated lab recipe."""
from hashlib import sha256
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

RECIPE = "authorized-nfs-csi-4.13.6-training-v1"
MAX_BINDING = 64 * 1024
MAX_MOUNTS = 4 * 1024 * 1024
Positive = Annotated[int, Field(strict=True, gt=0, le=2**64 - 1)]
Owner = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
UUID_TEXT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
DNS = r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?"


class SourceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    version: Literal[1]
    recipe: Literal["authorized-nfs-csi-4.13.6-training-v1"]
    namespace: str
    namespace_uid: str
    node_uid: str
    claim_uid: str
    pv_uid: str
    driver: Literal["okf-training-nfs.csi.k8s.io"]
    volume_handle: str
    server: str
    share: Literal["/"]
    subdir: str
    nfs_source: str
    server_filesystem_id: Positive
    server_directory_inode: Positive
    server_directory_device: Positive
    server_directory_uid: Owner
    server_directory_gid: Owner
    server_directory_mode: Annotated[int, Field(strict=True, ge=0, le=0o7777)]


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def read_source_binding(path):
    """Bound a projected regular file; never follow a FIFO or accept duplicate keys."""
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("Duplicate source binding field")
            result[key] = value
        return result

    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BINDING + 1)
        if not raw or len(raw) > MAX_BINDING:
            raise ValueError()
        value = json.loads(raw, object_pairs_hook=pairs)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError, TypeError, UnicodeError):
        raise ValueError("Bounded regular RWX source binding required") from None
    finally:
        if fd is not None:
            os.close(fd)


def _source(plan, value):
    try:
        if not isinstance(value, dict):
            raise ValueError()
        scope = next(v for v in plan.storage if v.component == "artifacts")
        if (scope.provider != "external" or scope.recipe != RECIPE
                or sha256(_canonical(value)).hexdigest() != scope.scope_sha256):
            raise ValueError()
        source = SourceBinding.model_validate(value)
        if type(value.get("version")) is not int or source.namespace != plan.source.namespace:
            raise ValueError()
        for field in ("namespace_uid", "node_uid", "claim_uid", "pv_uid"):
            uid = getattr(source, field)
            if not re.fullmatch(UUID_TEXT, uid) or UUID(uid).int == 0:
                raise ValueError()
        if str(IPv4Address(source.server)) != source.server:
            raise ValueError()
        if (not re.fullmatch(r"okf-" + re.escape(source.namespace) + "-" + DNS + r"-pvc-" + source.claim_uid, source.subdir)
                or source.nfs_source != source.server + ":/" + source.subdir
                or source.volume_handle != f"{source.server}##{source.subdir}#pvc-{source.claim_uid}#retain"
                or (source.server_directory_uid, source.server_directory_gid) != (1000, 1000)
                or source.server_directory_mode not in {0o770, 0o2770}):
            raise ValueError()
        return source, scope.scope_sha256
    except (OSError, ValueError, TypeError, StopIteration):
        raise ValueError("Authenticated selected NFS source binding required") from None


def _observe_nfs_root(value):
    """Pin a real existing directory while reading kernel mount identity."""
    if sys.platform != "linux" or value is None:
        raise ValueError("Linux NFS mount observation required")
    root = Path(value)
    try:
        if (not root.is_absolute() or root.resolve(strict=True) != root
                or re.search(r"[\s\\]", str(root))):
            raise ValueError()
    except (OSError, ValueError):
        raise ValueError("Stable canonical NFS root required") from None
    fd = None
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        first = os.fstat(fd)
        with Path("/proc/self/mountinfo").open("rb") as stream:
            raw = stream.read(MAX_MOUNTS + 1)
        if not raw or len(raw) > MAX_MOUNTS:
            raise ValueError()
        current, final = root.stat(follow_symlinks=False), os.fstat(fd)
        def fields(s):
            return s.st_dev, s.st_ino, s.st_uid, s.st_gid, s.st_mode
        if fields(first) != fields(current) or fields(first) != fields(final) or not stat.S_ISDIR(first.st_mode):
            raise ValueError()
        return dict(root=str(root), mountinfo=raw.decode("utf-8"), major=os.major(first.st_dev),
            minor=os.minor(first.st_dev), inode=first.st_ino, uid=first.st_uid, gid=first.st_gid, mode=first.st_mode)
    except (OSError, ValueError, UnicodeError):
        raise ValueError("Stable canonical NFS root required") from None
    finally:
        if fd is not None:
            os.close(fd)


def _mount(observed, *, version="4.1"):
    matches = []
    for line in observed["mountinfo"].splitlines():
        pieces = line.split(" - ")
        if len(pieces) != 2:
            raise ValueError("Ambiguous kernel mount record")
        before, after = pieces[0].split(), pieces[1].split()
        if len(before) < 6 or len(after) != 3:
            raise ValueError("Malformed kernel mount record")
        if before[4] == observed["root"]:
            matches.append((before, after))
    if len(matches) != 1:
        raise ValueError("One exact NFS root mount required")
    before, after = matches[0]
    if (before[2] != f"{observed['major']}:{observed['minor']}" or before[3] != "/"
            or after[0] != "nfs4"):
        raise ValueError("Actual NFS root mount required")
    options = {}
    for option in after[2].split(","):
        key, _, value = option.partition("=")
        if key in options:
            raise ValueError("Ambiguous NFS mount options")
        options[key] = value
    if any(options.get(k) != v for k, v in {"vers": version, "proto": "tcp", "sec": "sys"}.items()):
        raise ValueError("Selected NFS transport required")
    return after[1], options


def verify_rwx_relocation(settings, plan, source_binding):
    """Return stable physical identity, never trust supplied target observations."""
    from .nas_restore import RECIPE as NAS_RECIPE, verify_nas_relocation
    if any(v.component == "artifacts" and v.recipe == NAS_RECIPE for v in plan.storage):
        return verify_nas_relocation(settings, plan, source_binding)
    source, scope = _source(plan, source_binding)
    namespace = settings.deployment_namespace
    if (settings.artifact_backend != "rwx" or settings.artifact_provider != "external"
            or not re.fullmatch(r"replica-test-" + DNS, namespace)
            or namespace == source.namespace or settings.artifact_root is None):
        raise ValueError("New selected RWX recovery target required")
    observed = _observe_nfs_root(settings.artifact_root)
    mounted_source, options = _mount(observed)
    prefix = source.server + ":"
    if not mounted_source.startswith(prefix) or options.get("addr") != source.server:
        raise ValueError("Same authenticated NFS server required")
    export = mounted_source[len(prefix):]
    old = "/" + source.subdir
    if export == old or export.startswith(old + "/") or old.startswith(export.rstrip("/") + "/"):
        raise ValueError("Source and target exports must be disjoint")
    if not re.fullmatch(r"/okf-" + re.escape(namespace) + "-" + DNS + r"-pvc-" + UUID_TEXT, export):
        raise ValueError("New namespace CSI claim export required")
    mode = stat.S_IMODE(observed["mode"])
    if (not stat.S_ISDIR(observed["mode"]) or type(observed["inode"]) is not int or observed["inode"] <= 0
            or observed["inode"] == source.server_directory_inode
            or (observed["uid"], observed["gid"]) != (source.server_directory_uid, source.server_directory_gid)
            or mode not in {0o770, 0o2770}):
        raise ValueError("Different owned NFS directory required")
    return dict(source_scope_sha256=scope, namespace=namespace, nfs_source=mounted_source,
        inode=observed["inode"], uid=observed["uid"], gid=observed["gid"], mode=mode)
