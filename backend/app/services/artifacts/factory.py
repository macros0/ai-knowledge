"""Explicit provider selection; no credential discovery or default AWS endpoint."""
from pathlib import Path

from .local import LocalBlobStore
from .rwx import RwxBlobStore


def get_blob_store(settings):
    from .control import enforce_runtime
    enforce_runtime(settings)
    backend = settings.artifact_backend
    root = getattr(settings, "artifact_root", None)
    store_id = settings.artifact_store_id
    if backend == "local":
        return LocalBlobStore(Path(root) if root is not None else Path(settings.data_dir) / "artifacts", store_id=store_id)
    if backend == "rwx":
        if root is None:
            raise ValueError("Explicit RWX root required")
        return RwxBlobStore(Path(root), store_id=store_id)
    if backend != "s3":
        raise ValueError("Unknown artifact backend")

    from .s3 import S3BlobStore, validate_endpoint
    endpoint = getattr(settings, "artifact_s3_endpoint", None)
    bucket = getattr(settings, "artifact_s3_bucket", None)
    prefix = getattr(settings, "artifact_s3_prefix", "")
    style = getattr(settings, "artifact_s3_addressing_style", "path")
    allow_http = getattr(settings, "artifact_s3_allow_http", False)
    validate_endpoint(endpoint, bucket, prefix, style, allow_http)
    access = getattr(settings, "artifact_s3_access_key", None)
    secret = getattr(settings, "artifact_s3_secret_key", None)
    if not access or not secret:
        raise ValueError("Explicit artifact S3 credentials required")
    ca = getattr(settings, "artifact_s3_ca_bundle", None)
    if ca is not None and not Path(ca).is_file():
        raise ValueError("Artifact S3 CA bundle unavailable")
    region = getattr(settings, "artifact_s3_region", "us-east-1")
    if not region:
        raise ValueError("Explicit artifact S3 region required")
    import boto3
    from botocore.config import Config
    session = boto3.session.Session(aws_access_key_id=access, aws_secret_access_key=secret,
        aws_session_token=getattr(settings, "artifact_s3_session_token", None), region_name=region)
    client = session.client("s3", endpoint_url=endpoint, verify=str(ca) if ca else True,
        config=Config(s3={"addressing_style": style}, connect_timeout=5, read_timeout=30,
                      retries={"mode": "standard", "max_attempts": 2}))
    scratch = getattr(settings, "artifact_scratch_root", None) or Path(settings.data_dir) / "artifact-scratch"
    return S3BlobStore(client=client, endpoint=endpoint, bucket=bucket, prefix=prefix,
        store_id=store_id, scratch_root=Path(scratch), addressing_style=style, allow_http=allow_http,
        max_bytes=getattr(settings, "artifact_max_bytes", 2 * 1024 * 1024 * 1024))
