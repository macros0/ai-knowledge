"""Runtime credentials must not inherit operator, DDL or unrelated-object rights."""
import importlib.util
from pathlib import Path
import sys

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/replica_credentials.py"
    assert path.is_file(), "Scoped runtime credential provisioning is missing"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("replica_credentials_test", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_s3_policy_scopes_object_mutation_and_excludes_admin_bucket_management():
    tool = module()
    bucket, prefix = "replica-test-fixture01", "replica_test/fixture01/application/"
    policy = tool.s3_policy(bucket, prefix)
    actions = {a for s in policy["Statement"] for a in s["Action"]}
    assert actions == {"s3:ListBucket", "s3:GetBucketLocation", "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"}
    object_statements = [s for s in policy["Statement"] if "s3:GetObject" in s["Action"]]
    assert object_statements[0]["Resource"] == [f"arn:aws:s3:::{bucket}/{prefix}*"]
    assert all(s["Effect"] == "Allow" and "Principal" not in s for s in policy["Statement"])
    for bad in ("", "/", "../", "replica_test/other/application/", "replica_test/fixture01/application/*"):
        with pytest.raises(ValueError):
            tool.s3_policy(bucket, bad)


def test_runtime_control_tables_can_lock_the_immutable_id_but_not_change_fences():
    tool = module()
    tables = {"documents", "audit_log", "alembic_version", "artifact_storage_control", "replica_runtime_identity", "replica_maintenance_state"}
    plan = tool.grant_plan(tables)
    for name in {"artifact_storage_control", "replica_runtime_identity", "replica_maintenance_state"}:
        assert plan[name] == {"privileges": ["SELECT"], "update_columns": ["id"]}
    assert plan["audit_log"] == {"privileges": ["SELECT", "INSERT"], "update_columns": []}
    assert plan["alembic_version"] == {"privileges": ["SELECT"], "update_columns": []}
    assert plan["documents"] == {"privileges": ["SELECT", "INSERT", "UPDATE", "DELETE"], "update_columns": []}
    with pytest.raises(ValueError):
        tool.grant_plan({"documents; DROP SCHEMA public"})


def test_credential_transition_uses_only_the_versioned_runtime_secret():
    tool = module()
    deployment = {"spec": {"replicas": 0, "template": {"spec": {"containers": [{"envFrom": [
        {"secretRef": {"name": "old"}}, {"configMapRef": {"name": "runtime"}}]}]}}}}
    changed = tool.bind_runtime_secret(deployment, "old", "new")
    assert changed["spec"]["template"]["spec"]["containers"][0]["envFrom"][0] == {"secretRef": {"name": "new"}}
    assert deployment["spec"]["template"]["spec"]["containers"][0]["envFrom"][0]["secretRef"]["name"] == "old"
    with pytest.raises(ValueError):
        tool.bind_runtime_secret({**deployment, "spec": {**deployment["spec"], "replicas": 2}}, "old", "new")


def test_iam_policy_retry_ignores_set_order_but_not_added_permission():
    import copy
    tool = module()
    policy = tool.s3_policy("replica-test-fixture01", "replica_test/fixture01/application/")
    reordered = copy.deepcopy(policy)
    reordered["Statement"].reverse()
    reordered["Statement"][0]["Action"].reverse()
    assert tool.policy_digest(reordered) == tool.policy_digest(policy)
    reordered["Statement"][0]["Action"].append("s3:DeleteBucket")
    assert tool.policy_digest(reordered) != tool.policy_digest(policy)


def test_filtered_bucket_discovery_never_exposes_other_installations():
    tool = module()
    assert tool.bucket_discovery(["owned"], "owned") == {"mode": "filtered", "visible_bucket_count": 1}
    with pytest.raises(ValueError):
        tool.bucket_discovery(["owned", "operator-canary"], "owned")


def test_offline_job_keeps_operator_url_in_a_separate_readonly_mount():
    tool = module()
    from tests.test_replica_upgrade_coordinator import deployment
    api = deployment()
    api["spec"]["template"]["metadata"] = {"labels": {"okf-role": "api", "app.kubernetes.io/instance": "owned"}}
    api["spec"]["template"]["spec"]["containers"][0]["env"] = []
    job = tool.credential_job(api, name="compute", runtime="runtime", inputs="inputs", runtime_secret="new-runtime", operator_secret="private-operator", command=["python"])
    pod = job["spec"]["template"]["spec"]
    refs = [r["secretRef"]["name"] for r in pod["containers"][0]["envFrom"] if "secretRef" in r]
    assert refs == ["new-runtime"]
    assert next(v for v in pod["volumes"] if v["name"] == "operator-database")["secret"]["secretName"] == "private-operator"
    mount = next(v for v in pod["containers"][0]["volumeMounts"] if v["name"] == "operator-database")
    assert mount["readOnly"] is True and mount["mountPath"] == "/run/operator-db"
    assert api["spec"]["template"]["spec"]["containers"][0]["envFrom"][0]["secretRef"]["name"] == "private-runtime"


@pytest.mark.parametrize("name", ["DATABASE_URL", "artifact_s3_access_key", "ARTIFACT_S3_SECRET_KEY"])
def test_offline_job_refuses_credential_environment_overrides(name):
    tool = module()
    from tests.test_replica_upgrade_coordinator import deployment
    api = deployment()
    api["spec"]["template"]["metadata"] = {"labels": {"okf-role": "api"}}
    api["spec"]["template"]["spec"]["containers"][0]["env"] = [{"name": name, "value": "unexpected"}]
    with pytest.raises(ValueError):
        tool.credential_job(api, name="compute", runtime="runtime", inputs="inputs", runtime_secret="new-runtime", operator_secret="private-operator", command=["python"])


def test_identity_jobs_connect_with_operator_file_while_hashing_application_settings():
    tool = module()
    compute, release = tool.credential_sources()
    for name, source in (("compute", compute), ("release", release)):
        compile(source, name, "exec")
        assert "OperatorSettings(_env_file=None)" in source
        assert "Path('/run/operator-db/url').read_text().strip()" in source
        assert "configure_database(s.db_url)" not in source
    assert "rebind_runtime(session,s,old" in compute
    assert "corpus(session.connection())!=expected" in release


def test_versioned_secrets_preserve_signing_key_and_never_mix_operator_credentials():
    import base64
    tool = module()
    encode = lambda value: base64.b64encode(value.encode()).decode()
    old = {"APP_SECRET_KEY": encode("signing"), "DATABASE_URL": encode("old-db"), "ARTIFACT_S3_ACCESS_KEY": encode("root"),
        "ARTIFACT_S3_SECRET_KEY": encode("root-secret"), "DIAGNOSTICS_INGEST_TOKEN": encode("diag"), "DIAG_TOKEN": encode("diag"),
        "LLM_API_KEY": encode("model"), "QDRANT_API_KEY": encode("vector")}
    runtime, operator = tool.credential_secret_data(old, "new-db", "app-access", "app-secret")
    assert set(runtime) == set(old) and runtime["APP_SECRET_KEY"] == old["APP_SECRET_KEY"]
    assert {k for k in old if old[k] != runtime[k]} == {"DATABASE_URL", "ARTIFACT_S3_ACCESS_KEY", "ARTIFACT_S3_SECRET_KEY"}
    assert operator == {"url": old["DATABASE_URL"]}
    assert old["DATABASE_URL"] == encode("old-db")
    with pytest.raises(ValueError):
        tool.credential_secret_data({**old, "UNEXPECTED_ROOT_KEY": encode("root")}, "new-db", "app-access", "app-secret")


def test_runtime_proof_checks_actual_sql_user_and_all_secret_components_without_disclosing_them():
    tool = module()
    source = tool.runtime_proof_source({"role": "expected", "access_sha256": "a" * 64, "secret_sha256": "b" * 64,
        "epoch": 6, "operation": "c" * 32, "fingerprint": "d" * 64})
    compile(source, "proof", "exec")
    assert "current_user" in source and "rolsuper" in source
    assert "artifact_s3_access_key" in source and "artifact_s3_secret_key" in source
    assert "replica_runtime_upgrade" in source and "replica_maintenance_leave" in source
    assert "print(json.dumps({'ok':True" in source


def test_job_wait_resume_exempts_only_the_exact_owned_job_and_its_controller_pod():
    tool = module()
    from tests.test_replica_backup_coordinator import config, objects
    items = objects()
    job = {"kind": "Job", "metadata": {"name": "compute", "uid": "job-uid", "labels": {"okf-run-id": config()["run_id"]}}}
    pod = {"kind": "Pod", "metadata": {"name": "compute-pod", "ownerReferences": [{"kind": "Job", "uid": "job-uid", "controller": True}]},
        "status": {"phase": "Running"}, "spec": {"nodeName": "node1"}}
    assert tool.credential_wait_inventory(items + [job, pod], job) == items
    foreign = {**pod, "metadata": {**pod["metadata"], "ownerReferences": [{"kind": "Job", "uid": "different", "controller": True}]}}
    filtered = tool.credential_wait_inventory(items + [job, foreign], job)
    with pytest.raises(ValueError):
        tool.validate_drained(filtered, config(), {"node1": True})


def test_job_wait_cannot_hide_a_scheduled_or_unreachable_writer():
    tool = module()
    from tests.test_replica_backup_coordinator import config, objects
    job = {"kind": "Job", "metadata": {"name": "compute", "uid": "job-uid"}}
    scheduled = {"kind": "CronJob", "spec": {"suspend": False}}
    with pytest.raises(ValueError):
        tool.validate_drained(tool.credential_wait_inventory(objects() + [job, scheduled], job), config(), {"node1": True})
    with pytest.raises(ValueError):
        tool.validate_drained(tool.credential_wait_inventory(objects() + [job], job), config(), {"node1": False})


def test_offline_job_refuses_a_reenabled_controller_even_before_its_pod_exists():
    tool = module()
    expected = {role: {"metadata": {"uid": role}} for role in ("api", "worker", "frontend", "model-stub")}
    items = [{"kind": "Deployment", "metadata": {"name": role, "uid": role}, "spec": {"replicas": 0}} for role in expected]
    tool.assert_credential_stopped(items, expected)
    items[2]["spec"]["replicas"] = 2
    with pytest.raises(ValueError):
        tool.assert_credential_stopped(items, expected)
