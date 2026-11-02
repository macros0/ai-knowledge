"""Read-only integrity/provenance checks for explicitly trusted harness summaries.

Ed25519 authenticates the configured issuer; its honesty and native observations
remain operator trust. No signer, probe, deployment authority or network IO here.
"""
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Annotated, Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.diagnostics.paths import assert_no_links
from test_scripts.replica_acceptance_report import binding_digest, build_certification_report, required_checks

DOMAIN = b'okf-replica-evidence-v1\0'
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Name = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[a-z][a-z0-9_.-]*$')]
Status = Literal['passed', 'failed', 'pending', 'skipped']
Kind = Literal['live', 'ci', 'review']


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, hide_input_in_errors=True)


class _Version(_Strict):
    schema_version: Literal[1]

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError('Integer version required')
        return value


class Scope(_Strict):
    run_id: Annotated[str, Field(pattern=r'^[a-z0-9]{8,32}$')]
    namespace_uid: Annotated[str, Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]
    installation_id: Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
    acceptance_id: Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]


class _Reference(_Strict):
    path: Annotated[str, Field(min_length=6, max_length=255)]
    sha256: Digest

    @field_validator('path')
    @classmethod
    def relative_json(cls, value):
        if (not re.fullmatch(r'[A-Za-z0-9_./-]+\.json', value)
                or any(part in {'', '.', '..'} for part in value.split('/'))
                or PurePosixPath(value).is_absolute()):
            raise ValueError('Relative unaliased JSON path required')
        return value


class _Issuer(_Strict):
    id: Name
    enabled: bool
    public_key: Digest
    producer_sha256: Digest
    checks: list[Name] = Field(min_length=1, max_length=128)

    @model_validator(mode='after')
    def unique_checks(self):
        if len(self.checks) != len(set(self.checks)):
            raise ValueError('Unique authorized check names required')
        return self


class _Trust(_Version):
    issuers: list[_Issuer] = Field(min_length=1, max_length=32)

    @model_validator(mode='after')
    def unique_issuers(self):
        if len({i.id for i in self.issuers}) != len(self.issuers):
            raise ValueError('Unique issuer required')
        return self


class _Bundle(_Version):
    issuer: Name
    producer_sha256: Digest
    binding_sha256: Digest
    scope: Scope
    receipts: list[_Reference] = Field(max_length=128)
    signature: Annotated[str, Field(pattern=r'^[0-9a-f]{128}$')]


class _Receipt(_Version):
    issuer: Name
    producer_sha256: Digest
    name: Name
    kind: Kind
    binding_sha256: Digest
    scope: Scope
    artifact: _Reference
    signature: Annotated[str, Field(pattern=r'^[0-9a-f]{128}$')]


class _Outcome(_Strict):
    id: Name
    status: Status


class _Artifact(_Version):
    name: Name
    kind: Kind
    binding_sha256: Digest
    scope: Scope
    producer_sha256: Digest
    outcomes: list[_Outcome] = Field(min_length=1, max_length=512)
    native_reference_sha256: list[Digest] = Field(max_length=256)
    restore_verified: bool | None

    @model_validator(mode='after')
    def coherent_outcomes(self):
        if (len({o.id for o in self.outcomes}) != len(self.outcomes)
                or len(set(self.native_reference_sha256)) != len(self.native_reference_sha256)
                or self.kind == 'live' and not self.native_reference_sha256):
            raise ValueError('Unique bounded native outcomes required')
        return self


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def read_public_json(path, *, max_bytes=65536):
    """Bounded stable regular input; never follow a link or block on a FIFO."""
    value = Path(path).absolute()
    assert_no_links(value, Path(value.anchor))
    if value != value.resolve(strict=True):
        raise ValueError('Canonical unaliased input path required')
    original = value.lstat()
    if not stat.S_ISREG(original.st_mode) or original.st_nlink != 1 or original.st_size > max_bytes:
        raise ValueError('Bounded single-link regular input required')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(value, flags), 'rb') as stream:
        before = os.fstat(stream.fileno())
        raw = stream.read(max_bytes+1)
        after = os.fstat(stream.fileno())
    def identity(metadata):
        # Windows3.12 stat/fstat expose different legacy ctime meanings.
        # Compare file identity, size, mtime and links; authenticate read bytes.
        return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_nlink
    if (not stat.S_ISREG(before.st_mode) or len(raw)>max_bytes
            or len({identity(item) for item in (original,before,after,value.lstat())}) != 1):
        raise ValueError('Unstable input refused')
    assert_no_links(value, Path(value.anchor))
    return json.loads(raw, object_pairs_hook=_pairs), sha256(raw).hexdigest()


def _referenced(root, reference):
    value, digest = read_public_json(root/reference.path)
    if digest != reference.sha256:
        raise ValueError('Evidence digest mismatch')
    return value


def _authenticate(value, issuers, *, permission):
    issuer = issuers.get(value.issuer)
    if (issuer is None or not issuer.enabled or issuer.producer_sha256 != value.producer_sha256
            or permission not in issuer.checks):
        raise ValueError('Unauthorized evidence issuer')
    unsigned = value.model_dump(mode='json', exclude={'signature'})
    domain = DOMAIN+b'closure\0' if isinstance(value,_Bundle) else DOMAIN
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(issuer.public_key)).verify(
        bytes.fromhex(value.signature), domain+_canonical(unsigned))


def _verify(binding, scope, *, bundle_path, trust_path, evidence_root):
    required = required_checks(binding)
    fingerprint = binding_digest(binding)
    scope = Scope.model_validate(scope)
    root = Path(evidence_root).absolute()
    assert_no_links(root, Path(root.anchor))
    if (not root.is_dir() or root != root.resolve(strict=True)
            or Path(trust_path).absolute().is_relative_to(root)):
        raise ValueError('Trust policy must be separate from evidence root')
    trust_value, trust_sha = read_public_json(trust_path)
    trust = _Trust.model_validate(trust_value)
    bundle_value, bundle_sha = read_public_json(bundle_path)
    bundle = _Bundle.model_validate(bundle_value)
    if bundle.scope != scope or bundle.binding_sha256 != fingerprint:
        raise ValueError('Bundle differs from selected acceptance')
    issuers = {issuer.id: issuer for issuer in trust.issuers}
    # The terminal signed closure authenticates the complete receipt set, so a
    # bundle assembler cannot omit/replace a failed result with an earlier pass.
    _authenticate(bundle,issuers,permission='qualification.bundle')
    names, paths, metadata = set(), set(), []
    for reference in bundle.receipts:
        if reference.path in paths:
            raise ValueError('Duplicate receipt path')
        paths.add(reference.path)
        receipt = _Receipt.model_validate(_referenced(root, reference))
        if (required.get(receipt.name) != receipt.kind
                or receipt.scope != scope or receipt.binding_sha256 != fingerprint or receipt.name in names):
            raise ValueError('Unauthorized receipt')
        _authenticate(receipt,issuers,permission=receipt.name)
        names.add(receipt.name)
        artifact = _Artifact.model_validate(_referenced(root, receipt.artifact))
        if (artifact.scope != scope or artifact.binding_sha256 != fingerprint or artifact.name != receipt.name
                or artifact.kind != receipt.kind or artifact.producer_sha256 != receipt.producer_sha256):
            raise ValueError('Artifact differs from signed receipt')
        statuses = {outcome.status for outcome in artifact.outcomes}
        status = next((s for s in ('failed','pending','skipped') if s in statuses), 'passed')
        metadata.append(dict(name=receipt.name, kind=receipt.kind, status=status,binding_sha256=fingerprint,
            artifact_sha256=receipt.artifact.sha256, restore_verified=artifact.restore_verified))
    report = build_certification_report(binding, metadata)
    return {**report, 'provenance':dict(authority='explicitly-configured-Ed25519-harness',
        bundle_sha256=bundle_sha,trust_sha256=trust_sha,receipt_count=len(metadata),
        scope_sha256=sha256(_canonical(scope.model_dump(mode='json'))).hexdigest(),
        native_observations_independently_replayed=False)}


def verify_evidence(binding, scope, *, bundle_path, trust_path, evidence_root):
    """Authenticate configured harness summaries and pass verified metadata to policy."""
    try:
        return _verify(binding,scope,bundle_path=bundle_path,trust_path=trust_path,evidence_root=evidence_root)
    except Exception:  # noqa: BLE001 -- malformed private inputs and signature/path errors stay redacted.
        raise ValueError('Replica qualification evidence is invalid or untrusted') from None
