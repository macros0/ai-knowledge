"""Real selected S3/Qdrant services, only on explicit owned loopback test targets."""
import json
import os
from pathlib import Path

import pytest


class PrivateTarget(dict):
    def __repr__(self):
        return "PrivateTarget(<redacted>)"


@pytest.fixture
def target():
    path = os.environ.get("OKF_REPLICA_STORAGE_FILE")
    if not path:
        if os.environ.get("OKF_REPLICA_STORAGE_REQUIRED") == "1":
            pytest.fail("Required isolated managed storage target missing")
        pytest.skip("Explicit isolated managed storage file required")
    value = PrivateTarget(json.loads(Path(path).read_text(encoding="utf-8-sig")))
    if (value.get("namespace") != "replica-test-s3rpl20261031" or value.get("run_id") != "s3rpl20261031"
            or value.get("endpoint") not in {"http://127.0.0.1:25435", "https://127.0.0.1:25435"}
            or value.get("qdrant_url") not in {"http://127.0.0.1:25433", "https://127.0.0.1:25433"}
            or value.get("bucket") != "replica-test-s3rpl20261031"):
        pytest.fail("Only the explicitly owned loopback storage targets are permitted")
    return value


def settings(target, tmp_path):
    from app.config import Settings
    value = Settings(_env_file=None, data_dir=tmp_path)
    for name, configured in {"artifact_backend": "s3", "artifact_store_id": "managed-lab-s3",
            "artifact_s3_endpoint": target["endpoint"], "artifact_s3_bucket": target["bucket"],
            "artifact_s3_prefix": "replica_test/s3rpl20261031/", "artifact_s3_allow_http": True,
            "artifact_s3_access_key": target["access"], "artifact_s3_secret_key": target["secret"]}.items():
        setattr(value, name, configured)
    value.okf_deployment_schema, value.app_role = "replicas-v1", "api"
    value.deployment_namespace = target["namespace"]
    value.qdrant_url, value.qdrant_api_key = target["qdrant_url"], target["qdrant_key"]
    value.qdrant_prefer_grpc, value.qdrant_grpc_port = False, 25434
    value.qdrant_collection = "replica_test_s3rpl20261031_contract"
    if target["endpoint"].startswith("https:"):
        value.artifact_s3_ca_bundle = Path(target["ca_file"])
    return value


def test_selected_rustfs_passes_actual_artifact_bootstrap_contract(target, tmp_path):
    from app.services.artifacts import get_blob_store
    from app.services.artifacts.control import migration_access
    from app.deployment.bootstrap import probe_artifacts
    value = settings(target, tmp_path)
    with migration_access():
        store = get_blob_store(value)
        try:
            store.client.create_bucket(Bucket=store.bucket)
        except store.client.exceptions.BucketAlreadyOwnedByYou:
            pass
    receipt = probe_artifacts(value, installation_id=target["installation_id"], selection_digest="sha256:" + "3" * 64)
    assert receipt["checks"] == ["conditional_put", "cold_read", "range_read", "delete"]
    assert receipt["qualification"] == "basic-bootstrap-only"


def test_selected_services_reject_wrong_credentials(target, tmp_path):
    from botocore.exceptions import ClientError
    from app.services.artifacts import get_blob_store
    from app.services.artifacts.control import migration_access
    import httpx
    value = settings(target, tmp_path)
    value.artifact_s3_secret_key = "WRONG_TEST_SECRET"
    with migration_access():
        store = get_blob_store(value)
        with pytest.raises(ClientError) as error:
            store.client.head_bucket(Bucket=store.bucket)
        assert error.value.response["ResponseMetadata"]["HTTPStatusCode"] in {401, 403}
    with httpx.Client(trust_env=False, timeout=10, verify=target.get("ca_file", True)) as client:
        denied = client.get(target["qdrant_url"] + "/collections")
        allowed = client.get(target["qdrant_url"] + "/collections", headers={"api-key": target["qdrant_key"]})
    assert denied.status_code in {401, 403} and allowed.status_code == 200


def test_vector_bootstrap_verifies_schema_and_refuses_populated_collection(target, tmp_path):
    from uuid import uuid4
    from app.deployment.bootstrap import initialize_empty_vector_store
    from app.services.vector_store import VectorStore
    from qdrant_client import models as qm
    value = settings(target, tmp_path)
    initialize_empty_vector_store(value)
    store = VectorStore(settings=value)
    point_id = str(uuid4())
    try:
        store.client.upsert(store.collection, points=[qm.PointStruct(id=point_id,
            vector=[1.0] + [0.0] * (value.embedding_dimensions - 1), payload={"bootstrap_test": True})], wait=True)
        with pytest.raises(ValueError, match="nonempty"):
            initialize_empty_vector_store(value)
    finally:
        store.client.delete(store.collection, points_selector=qm.PointIdsList(points=[point_id]), wait=True)


def require_tls(target):
    if not target["endpoint"].startswith("https:") or not Path(target.get("ca_file", "")).is_file():
        if os.environ.get("OKF_REPLICA_TLS_REQUIRED") == "1":
            pytest.fail("Required selected TLS target missing")
        pytest.skip("Selected TLS target required")


def test_selected_tls_refuses_wrong_hostname(target):
    import socket
    import ssl
    require_tls(target)
    context = ssl.create_default_context(cafile=target["ca_file"])
    for port in (25433, 25435):
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            with pytest.raises(ssl.SSLCertVerificationError) as error:
                context.wrap_socket(raw, server_hostname="wrong-replica.invalid")
        assert error.value.verify_code == 62


def test_rotated_tls_refuses_previous_ca(target):
    import socket
    import ssl
    require_tls(target)
    previous = target.get("previous_ca_file")
    if not previous:
        if os.environ.get("OKF_REPLICA_CA_ROTATION_REQUIRED") == "1":
            pytest.fail("Required previous CA receipt missing")
        pytest.skip("Explicit completed CA rotation required")
    context = ssl.create_default_context(cafile=previous)
    for port in (25433, 25435):
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            with pytest.raises(ssl.SSLCertVerificationError) as error:
                context.wrap_socket(raw, server_hostname="127.0.0.1")
        assert error.value.verify_code in {19, 20, 21}


def test_published_object_survives_storage_restart_with_verified_tls(target, tmp_path):
    import hashlib
    from dataclasses import fields
    from app.services.artifacts import get_blob_store
    from app.services.artifacts.contract import BlobRef
    from app.services.artifacts.control import migration_access
    require_tls(target)
    path = Path(__file__).resolve().parents[3] / "tests/tmp/replica-restart-ref.json"
    if not path.is_file():
        pytest.fail("Required pre-restart artifact receipt missing")
    value = json.loads(path.read_text())
    assert set(value) == {field.name for field in fields(BlobRef)}
    ref = BlobRef(**value)
    with migration_access():
        store = get_blob_store(settings(target, tmp_path))
        with store.open(ref) as stream:
            payload = stream.read()
    assert len(payload) == ref.size and hashlib.sha256(payload).hexdigest() == ref.sha256


def test_selected_postgres_tls_verifies_name_and_password(target):
    import shlex
    import subprocess
    require_tls(target)
    assert target.get("postgres_port") == 25437
    # port-forward both hides the real peer address behind localhost pg_hba
    # rules and may reset after rejected TLS/auth. Exercise actual pod TCP.
    script = r'''
import ipaddress,json,shlex,subprocess,sys
value=json.load(sys.stdin)
base=['kubectl','--context','kind-okf-policy','-n','replica-test-s3rpl20261031']
namespace=json.loads(subprocess.check_output(base+['get','namespace','replica-test-s3rpl20261031','-o','json']))
assert namespace['metadata']['labels'].get('okf-run-id')=='s3rpl20261031'
assert namespace['metadata']['labels'].get('app.kubernetes.io/managed-by')=='okf-replica-acceptance'
name='replica-storage-lab-postgres-0'
pod=json.loads(subprocess.check_output(base+['get','pod',name,'-o','json']))
assert pod['metadata']['labels'].get('okf-installation-id')=='3'*32
address=str(ipaddress.ip_address(pod['status']['podIP']))
assert not ipaddress.ip_address(address).is_loopback
cert=value['ca'].encode()
assert b'BEGIN CERTIFICATE' in cert and b'PRIVATE KEY' not in cert
subprocess.run(base+['exec','-i',name,'--','sh','-c','cat > /tmp/replica-acceptance-ca.pem'],input=cert,check=True)
if value.get('previous_ca'):
    subprocess.run(base+['exec','-i',name,'--','sh','-c','cat > /tmp/replica-acceptance-previous-ca.pem'],input=value['previous_ca'].encode(),check=True)
def connect(host,password,ca='/tmp/replica-acceptance-ca.pem'):
    connection='hostaddr='+address+' host='+host+' port=5432 dbname=replica_test_s3rpl20261031 user=replica sslmode=verify-full sslrootcert='+ca+' connect_timeout=5'
    command='export PGPASSWORD='+shlex.quote(password)+'\nexec psql -w -tA '+shlex.quote(connection)+' -c '+shlex.quote('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()')
    return subprocess.run(base+['exec','-i',name,'--','sh','-s'],input=command.encode(),capture_output=True,timeout=10)
positive=connect('replica-storage-lab-postgres',value['password'])
assert positive.returncode==0 and positive.stdout.strip()==b't','Verified pod TCP TLS did not succeed'
password=connect('replica-storage-lab-postgres','WRONG_TEST_PASSWORD')
assert password.returncode!=0 and b'password authentication failed' in password.stderr.lower(),'Wrong password did not produce an authentication refusal'
hostname=connect('wrong-replica.invalid',value['password'])
assert hostname.returncode!=0 and b'certificate' in hostname.stderr.lower() and b'match' in hostname.stderr.lower(),'Wrong hostname did not produce a certificate refusal'
if value.get('previous_ca'):
    previous=connect('replica-storage-lab-postgres',value['password'],'/tmp/replica-acceptance-previous-ca.pem')
    assert previous.returncode!=0 and b'certificate verify failed' in previous.stderr.lower(),'Previous CA did not produce a certificate refusal'
print(json.dumps({'tls':True,'wrong_password':'rejected_authentication','wrong_hostname':'rejected_certificate','peer_scope':'pod-tcp','pod_uid':pod['metadata']['uid']}))
'''
    known_hosts = Path(__file__).resolve().parents[3] / "tests/tmp/replica-lab-known_hosts"
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
        "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}",
        "ubuntu@10.10.1.52", "python3 -c " + shlex.quote(script)],
        input=json.dumps({"ca": Path(target["ca_file"]).read_text(), "password": target["postgres_password"],
            "previous_ca": Path(target["previous_ca_file"]).read_text() if target.get("previous_ca_file") else None}).encode(),
        capture_output=True, timeout=40)
    assert result.returncode == 0, "Owned PostgreSQL pod TCP TLS/authentication probe failed"
    report = json.loads(result.stdout)
    assert report["tls"] and report["wrong_password"] == "rejected_authentication"
    assert report["wrong_hostname"] == "rejected_certificate" and report["peer_scope"] == "pod-tcp"


def test_selected_qdrant_grpc_uses_bound_ca(target, tmp_path, monkeypatch):
    from app.services.vector_store import VectorStore, close_shared_qdrant_clients
    require_tls(target)
    if target.get("grpc_port") != 25434:
        if os.environ.get("OKF_REPLICA_GRPC_REQUIRED") == "1":
            pytest.fail("Required selected gRPC TLS target missing")
        pytest.skip("Explicit selected gRPC TLS target required")
    monkeypatch.setenv("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", target["ca_file"])
    value = settings(target, tmp_path)
    value.qdrant_prefer_grpc = True
    try:
        collection = VectorStore(settings=value).client.get_collection(value.qdrant_collection)
        assert collection.config.params.vectors.size == value.embedding_dimensions
    finally:
        close_shared_qdrant_clients()
