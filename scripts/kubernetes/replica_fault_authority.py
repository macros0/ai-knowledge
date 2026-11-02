"""Current owned-profile authority for fault recovery after a verified image update.

No faults are injected here. Reuse the image operation's immutable inputs and
exact diagnostic exception, with privileged SQL projected only into offline Jobs.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def load_source_operation(config, namespace_uid, path, old_fingerprint):
    from replica_image_upgrade import ImageUpgrade
    return ImageUpgrade(config, namespace_uid, path, old_fingerprint)


def _read(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Protected source journal exceeds its bound")
    return raw


class ScopedFaultAuthority:
    def __init__(self, owner, source_journal, archive, key_file, *, now=None):
        from app.deployment.replica_backup import verify_backup
        from app.services.diagnostics.paths import assert_no_links
        from replica_ca_rotation import require_recovery_point

        self.owner = owner
        self.path = Path(source_journal).absolute()
        private = Path(owner.config["ca_file"]).resolve().parent
        if self.path.parent != private or self.path == owner.path.resolve():
            raise ValueError("Separate protected verified source journal required")
        assert_no_links(self.path, private)
        raw = _read(self.path)
        source = json.loads(raw)
        identity = source.get("new_identity", {})
        if (source.get("phase") != "verified" or source.get("image_upgrade_verified") is not True
                or source.get("operation_kind") != "same-schema-image-upgrade"
                or source.get("namespace_uid") != owner.value["namespace_uid"]
                or identity.get("installation_id") != owner.config["installation_id"]
                or identity.get("runtime_fingerprint") != owner.fingerprint):
            raise ValueError("Verified current image-operation provenance required")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Aware fault admission time required")
        previous = (owner.journal or {}).get("fault_authority")
        admitted = datetime.fromisoformat(previous["validated_at"]) if previous else now
        with Path(key_file).open("rb") as stream:
            key = stream.read(33)
        if len(key) != 32 or Path(key_file).resolve().is_relative_to(Path(archive).resolve()):
            raise ValueError("Separate backup key required")
        manifest = verify_backup(archive, key)
        # Do not strand recovery because the already-admitted operation takes
        # longer than24h; a NEW injection still requires a fresh recovery point.
        require_recovery_point(manifest, owner.config, owner.fingerprint, now=admitted)
        self.record = {"source_journal_sha256": hashlib.sha256(raw).hexdigest(),
            "backup_id": manifest.plan.backup_id, "backup_authentication": manifest.authentication,
            "backup_archive": str(Path(archive).resolve()), "validated_at": admitted.isoformat()}
        if previous is not None and previous != self.record:
            raise ValueError("Recorded fault recovery provenance changed")
        self.source = load_source_operation(owner.config, owner.value["namespace_uid"], self.path, source["fingerprint"])
        if self.source.journal != source:
            raise ValueError("Source operation changed during admission")
        self.lab = self.source.lab
        self.check()

    def check(self, *, inject=False):
        if hashlib.sha256(_read(self.path)).hexdigest() != self.record["source_journal_sha256"]:
            raise ValueError("Fault source journal changed")
        self.source.check_inputs()
        if inject:
            self.source.check_roles(staged=True, allow_running=True)

    def release_job(self, api, *, name, inputs, operation, owner):
        from replica_credentials import credential_sources
        from replica_image_upgrade import image_operator_job
        self.check()
        selected = self.source.journal
        pod = api["spec"]["template"]["spec"]
        container = pod["containers"][0]
        runtime = [v["configMapRef"]["name"] for v in container.get("envFrom", []) if "configMapRef" in v]
        identity = [v["configMap"]["name"] for v in pod["volumes"] if v["name"] == "runtime-identity"]
        if (container["image"] != selected["image"] or runtime != [selected["next_runtime"]]
                or identity != [selected["next_identity"]]):
            raise ValueError("Fault release runtime/image/identity differs from the verified installation")
        job = image_operator_job(api, name=name, image=selected["image"], runtime=selected["next_runtime"], inputs=inputs,
            runtime_secret=selected["runtime_secret"], operator_secret=selected["operator_secret"],
            new_identity=selected["next_identity"], command=["python", "-u", "/run/upgrade/release.py", operation, owner])
        return credential_sources()[1], job

    def submit_job(self, job):
        self.check()
        # This existing coordinator compares the same Job's image, command,
        # environment and projected Secret/config bindings before observation.
        self.source.submit_job(job)
