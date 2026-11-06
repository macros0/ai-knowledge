"""Manifest-bound source coordinates and actual NFS target relocation."""
from hashlib import sha256
import importlib
import json
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.deployment.bootstrap import canonical
from app.deployment.replica_backup import BackupPlan
from tests.test_replica_backup import fixture_plan


RECIPE = "authorized-nfs-csi-4.13.6-training-v1"
SOURCE_CLAIM = "aefc7699-35fc-4484-8186-b4b5caac4ee3"
TARGET_CLAIM = "5d4fec89-4edd-4e1a-8e33-40d96a609065"


def fixture_binding():
    plan, _ = fixture_plan()
    subdir = f"okf-{plan.source.namespace}-rwx-artifacts-pvc-{SOURCE_CLAIM}"
    source = dict(version=1, recipe=RECIPE, namespace=plan.source.namespace,
        namespace_uid="e723d1a6-f1e1-44cd-8c16-adf9ca9ba92e",
        node_uid="9020a1d9-843e-4947-8808-53477289b863", claim_uid=SOURCE_CLAIM,
        pv_uid="a18fa734-057d-4430-a940-cbe9b8770119", driver="okf-training-nfs.csi.k8s.io",
        volume_handle=f"172.18.0.1##{subdir}#pvc-{SOURCE_CLAIM}#retain", server="172.18.0.1",
        share="/", subdir=subdir, nfs_source="172.18.0.1:/" + subdir,
        server_filesystem_id=7489247591754693893, server_directory_inode=8193,
        server_directory_device=1792, server_directory_uid=1000, server_directory_gid=1000,
        server_directory_mode=0o2770)
    value = plan.model_dump(mode="json")
    value["storage"][2].update(provider="external", recipe=RECIPE, scope_sha256=sha256(canonical(source)).hexdigest())
    return BackupPlan.model_validate(value), source


def observation(*, export=None, inode=8194):
    export = export or f"/okf-replica-test-target01-rwxt-artifacts-pvc-{TARGET_CLAIM}"
    return dict(root="/artifacts", mountinfo=f"3862 3854 0:591 / /artifacts rw,relatime - nfs4 172.18.0.1:{export} rw,vers=4.1,hard,proto=tcp,sec=sys,addr=172.18.0.1\n",
        major=0, minor=591, inode=inode, uid=1000, gid=1000, mode=0o40770)


def module():
    try:
        return importlib.import_module("app.deployment.rwx_restore")
    except ModuleNotFoundError:
        pytest.fail("Manifest-bound physical RWX restore proof is missing")


def verify(monkeypatch, *, source=None, plan=None, observed=None):
    default_plan, default_source = fixture_binding()
    source = default_source if source is None else source
    plan = default_plan if plan is None else plan
    tool = module()
    monkeypatch.setattr(tool, "_observe_nfs_root", lambda root: observed or observation())
    settings = SimpleNamespace(artifact_backend="rwx", artifact_provider="external",
        artifact_root="/artifacts", deployment_namespace="replica-test-target01")
    return tool.verify_rwx_relocation(settings, plan, source)


def test_fresh_nfs_export_same_local_path_has_stable_physical_proof(monkeypatch):
    proof = verify(monkeypatch)
    assert proof["source_scope_sha256"] == fixture_binding()[0].storage[2].scope_sha256
    assert proof["namespace"] == "replica-test-target01" and proof["inode"] == 8194
    assert proof["nfs_source"].endswith(TARGET_CLAIM)
    other = observation(); other.update(major=1, minor=77)
    other["mountinfo"] = other["mountinfo"].replace("3862 3854 0:591", "200 100 1:77")
    assert verify(monkeypatch, observed=other) == proof  # Client mount/device IDs are ephemeral.


@pytest.mark.parametrize("change", ["tamper", "missing", "extra", "namespace", "recipe", "provider", "bool_inode", "wrong_handle"])
def test_bad_source_binding_refuses_before_any_target_observation(monkeypatch, change):
    plan, source = fixture_binding()
    if change == "tamper":
        source["server_directory_inode"] += 1
    elif change == "missing":
        source.pop("claim_uid")
    elif change == "extra":
        source["approved"] = True
    else:
        if change == "namespace":
            source["namespace"] = "replica-test-other01"
        elif change == "recipe":
            source["recipe"] = "unqualified-nfs"
        elif change == "bool_inode":
            source["server_directory_inode"] = True
        elif change == "wrong_handle":
            source["volume_handle"] += "-different"
        value = plan.model_dump(mode="json")
        value["storage"][2]["scope_sha256"] = sha256(canonical(source)).hexdigest()
        if change == "provider":
            value["storage"][2]["provider"] = "managed"
        plan = BackupPlan.model_validate(value)
    tool = module()
    monkeypatch.setattr(tool, "_observe_nfs_root", lambda root: pytest.fail("Untrusted source must fail before observation/probe"))
    with pytest.raises(ValueError):
        tool.verify_rwx_relocation(SimpleNamespace(artifact_backend="rwx", artifact_provider="external",
            artifact_root="/artifacts", deployment_namespace="replica-test-target01"), plan, source)


@pytest.mark.parametrize("change", ["source", "source_child", "source_parent", "inode_alias", "server_alias", "wrong_namespace",
    "local", "bind_root", "stacked", "wrong_device", "no_vers", "old_vers", "udp", "wrong_security", "writable_other", "not_directory"])
def test_unsafe_actual_mount_cannot_authorize_target(monkeypatch, change):
    _, source = fixture_binding(); observed = observation()
    if change in {"source", "source_child", "source_parent"}:
        export = "/" + source["subdir"] + ("/child" if change == "source_child" else "")
        if change == "source_parent":
            export = "/"
        observed = observation(export=export)
    elif change == "inode_alias":
        observed["inode"] = source["server_directory_inode"]
    elif change == "wrong_namespace":
        observed["mountinfo"] = observed["mountinfo"].replace("replica-test-target01", "replica-test-other01")
    elif change == "server_alias":
        observed["mountinfo"] = observed["mountinfo"].replace("172.18.0.1", "nfs-alias.local")
    elif change == "local":
        observed["mountinfo"] = observed["mountinfo"].replace("nfs4", "ext4")
    elif change == "bind_root":
        observed["mountinfo"] = observed["mountinfo"].replace("0:591 / ", "0:591 /some-subpath ")
    elif change == "stacked":
        observed["mountinfo"] *= 2
    elif change == "wrong_device":
        observed["minor"] += 1
    elif change in {"no_vers", "old_vers", "udp", "wrong_security"}:
        old, new = {"no_vers": ("vers=4.1,", ""), "old_vers": ("vers=4.1", "vers=3"),
            "udp": ("proto=tcp", "proto=udp"), "wrong_security": ("sec=sys", "sec=none")}[change]
        observed["mountinfo"] = observed["mountinfo"].replace(old, new)
    elif change == "writable_other":
        observed["mode"] = 0o40777
    elif change == "not_directory":
        observed["mode"] = 0o100770
    with pytest.raises(ValueError):
        verify(monkeypatch, observed=observed)


def test_changed_target_inode_changes_persisted_proof(monkeypatch):
    before = verify(monkeypatch)
    after = verify(monkeypatch, observed=observation(inode=8195))
    assert sha256(canonical(before)).hexdigest() != sha256(canonical(after)).hexdigest()


def test_real_local_directory_never_counts_as_nfs(tmp_path):
    plan, source = fixture_binding()
    with pytest.raises(ValueError):
        module().verify_rwx_relocation(SimpleNamespace(artifact_backend="rwx", artifact_provider="external",
            artifact_root=tmp_path, deployment_namespace="replica-test-target01"), plan, source)


def test_binding_file_is_bounded_regular_json_and_rejects_duplicate_keys(tmp_path):
    tool = module()
    path = tmp_path / "binding.json"
    path.write_text('{"version":1,"version":1}')
    with pytest.raises(ValueError):
        tool.read_source_binding(path)
    path.write_bytes(b" " * (64 * 1024 + 1))
    with pytest.raises(ValueError):
        tool.read_source_binding(path)
    with pytest.raises(ValueError):
        tool.read_source_binding(tmp_path)
    _, source = fixture_binding()
    path.write_text(json.dumps(source))
    assert tool.read_source_binding(path) == source


def test_cli_bad_binding_refuses_before_any_database_or_artifact_probe(tmp_path, monkeypatch, capsys):
    from app.deployment import bootstrap, storage_selection
    from app.db import session as database
    from tests.test_replica_restore_identity import load
    tool = load(); plan, source = fixture_binding()
    source["server_directory_inode"] += 1
    plan_file, binding_file, selection_file = [tmp_path / name for name in ("plan.json", "binding.json", "selection.json")]
    plan_file.write_text(plan.model_dump_json()); binding_file.write_text(json.dumps(source)); selection_file.write_text("{}")
    settings = SimpleNamespace(deployment_namespace="replica-test-target01", artifact_backend="rwx",
        artifact_provider="external", artifact_root="/artifacts")
    selection = SimpleNamespace(namespace=settings.deployment_namespace, installation_id="5" * 32,
        validate_runtime=lambda _: None, model_dump=lambda: {"version": 1})
    monkeypatch.setattr(bootstrap, "OperatorSettings", lambda **kwargs: settings)
    monkeypatch.setattr(storage_selection.StorageSelection, "model_validate_json", classmethod(lambda cls, raw: selection))
    def forbidden(*args, **kwargs):
        pytest.fail("Bad binding must refuse before database access or artifact writes")
    monkeypatch.setattr(bootstrap, "probe_artifacts", forbidden)
    monkeypatch.setattr(database, "configure_database", forbidden)
    monkeypatch.setattr(sys, "argv", ["restore_identity", "--selection", str(selection_file), "--backup-plan", str(plan_file),
        "--operation-id", "f" * 32, "--rwx-source-binding", str(binding_file)])
    assert tool.main() == 2
    assert json.loads(capsys.readouterr().err)["jobs_held"] is True


@pytest.mark.parametrize("change_at", [2, 3])
def test_cli_changed_mount_before_or_after_probe_cannot_reach_adoption(tmp_path, monkeypatch, capsys, change_at):
    from app.deployment import bootstrap, storage_selection
    from app.db import session as database
    from app.services.work import maintenance_control
    from tests.test_replica_restore_identity import load
    tool = load(); plan, source = fixture_binding()
    files = [tmp_path / name for name in ("plan.json", "binding.json", "selection.json")]
    for path, raw in zip(files, [plan.model_dump_json(), json.dumps(source), "{}"], strict=True):
        path.write_text(raw)
    settings = SimpleNamespace(deployment_namespace="replica-test-target01", artifact_backend="rwx",
        artifact_provider="external", artifact_root="/artifacts", db_url="sqlite://")
    selection = SimpleNamespace(namespace=settings.deployment_namespace, installation_id="5" * 32,
        validate_runtime=lambda _: None, model_dump=lambda: {"version": 1})
    monkeypatch.setattr(bootstrap, "OperatorSettings", lambda **kwargs: settings)
    monkeypatch.setattr(storage_selection.StorageSelection, "model_validate_json", classmethod(lambda cls, raw: selection))
    monkeypatch.setattr(database, "configure_database", lambda value: None)
    monkeypatch.setattr(maintenance_control, "MaintenanceOperator", lambda *args, **kwargs: nullcontext(None))
    observed, probes = [], []
    def changed(*args):
        observed.append(True)
        return {"inode": 8195 if len(observed) >= change_at else 8194}
    monkeypatch.setattr(module(), "verify_rwx_relocation", changed)
    monkeypatch.setattr(bootstrap, "probe_artifacts", lambda *args, **kwargs: probes.append(True))
    monkeypatch.setattr(sys, "argv", ["restore_identity", "--selection", str(files[2]), "--backup-plan", str(files[0]),
        "--operation-id", "f" * 32, "--rwx-source-binding", str(files[1])])
    assert tool.main() == 2
    assert len(observed) == change_at and len(probes) == change_at - 2
    assert json.loads(capsys.readouterr().err)["type"] == "ValueError"  # Never reach a transaction on the null operator.
