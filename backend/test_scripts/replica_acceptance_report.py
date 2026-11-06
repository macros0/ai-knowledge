"""Profile-bound qualification policy for trusted, already verified harness evidence.

This module performs no IO, authenticates no provider receipts and authorizes no
deployment. A hash-shaped input is not proof that an acceptance run occurred.
Raw receipt ingestion/authority and runner integration remain separate gates.
"""
from hashlib import sha256
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
ImageDigest = Annotated[str, Field(pattern=r'^sha256:[0-9a-f]{64}$')]
Label = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.+:-]+$')]


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class _Roles(_Strict):
    frontend: int = Field(ge=1, le=10000)
    api: int = Field(ge=1, le=10000)
    worker: int = Field(ge=1, le=10000)


class _Topology(_Strict):
    orchestrator: Literal['kind', 'kubernetes']
    schedulable_nodes: int = Field(ge=1, le=10000)
    physical_failure_domains: int = Field(ge=1, le=10000)
    storage_class: Label
    volume_access_modes: list[Literal['ReadWriteOnce', 'ReadWriteOncePod', 'ReadWriteMany', 'ReadOnlyMany']] = Field(min_length=1, max_length=4)
    roles: _Roles

    @model_validator(mode='after')
    def coherent_domains(self):
        if (self.physical_failure_domains > self.schedulable_nodes
                or len(set(self.volume_access_modes)) != len(self.volume_access_modes)):
            raise ValueError('Invalid topology')
        return self


class _Component(_Strict):
    provider: Literal['managed', 'external']
    recipe: Label
    version: Label
    image: Annotated[str, Field(max_length=512, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}$')] | None
    license: Label

    @model_validator(mode='after')
    def pinned_managed_image(self):
        if self.provider == 'managed' and self.image is None or self.image is not None and '://' in self.image:
            raise ValueError('Pinned managed image required')
        return self


class _Components(_Strict):
    postgres: _Component
    qdrant: _Component
    artifacts: _Component


class _Images(_Strict):
    frontend: ImageDigest
    api: ImageDigest
    worker: ImageDigest


class _Runtime(_Strict):
    revision: Annotated[str, Field(pattern=r'^(?:[0-9a-f]{40}|[0-9a-f]{64})$')]
    images: _Images
    identity_fingerprint: Digest
    schema_revision: Label
    authentication: Label
    model_provider: Label
    embeddings: Label


class _Binding(_Strict):
    deployment_schema: Literal['replicas-v1']
    profile: Literal['basic', 'resilient']
    artifact_backend: Literal['s3', 'rwx']
    topology: _Topology
    components: _Components
    runtime: _Runtime


class _Evidence(_Strict):
    name: Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[a-z][a-z0-9_.]+$')]
    status: Literal['passed', 'failed', 'pending', 'skipped']
    kind: Literal['unit', 'integration', 'live', 'ci', 'review']
    binding_sha256: Digest
    artifact_sha256: Digest | None = None
    restore_verified: bool | None = None


class _Certificate(_Strict):
    schema_version: Literal[1]
    qualification_class: Literal['basic-certified', 'resilient-certified'] = Field(alias='class')
    binding_sha256: Digest
    policy_sha256: Digest
    evidence_sha256: Digest

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError('Integer version required')
        return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _binding(value):
    try:
        # Revalidate even model instances/nested mutable containers at each call.
        return _Binding.model_validate(value.model_dump(mode='json') if isinstance(value, _Binding) else value)
    except (ValidationError, TypeError, ValueError):
        raise ValueError('Invalid bounded public qualification binding') from None


def binding_digest(value):
    return sha256(_canonical(_binding(value).model_dump(mode='json'))).hexdigest()


def required_checks(value):
    binding = _binding(value)
    checks = {name: 'live' for name in (
        'contracts.platform', 'frontend.session_sse_upload_logout',
        'storage.postgres.recipe', 'storage.qdrant.recipe', 'storage.artifacts.recipe',
        'storage.contract', 'storage.publication_full', 'runtime.coherence',
        'runtime.active_worker_loss', 'runtime.postgres_partition', 'runtime.stale_worker_return',
        'runtime.cancel_delete', 'runtime.model_capacity', 'operations.independent_restore',
        'operations.schema_update', 'operations.backup_failure_age_alerts', 'operations.key_recovery')}
    checks.update({name: 'ci' for name in ('release.project_check', 'release.backend_ci',
        'release.parser_ci', 'release.frontend_build')})
    checks['release.branch_review'] = 'review'
    for name in ('postgres', 'qdrant', 'artifacts'):
        if getattr(binding.components, name).provider == 'external':
            checks['storage.' + name + '.external_ownership'] = 'live'
    if binding.profile == 'resilient':
        checks.update({name: 'live' for name in ('resilience.node_loss', 'resilience.storage_loss',
            'resilience.independent_domain_restore')})
    return checks


def _policy_digest(value):
    return sha256(_canonical({'schema_version': 1, 'required': required_checks(value),
        'restore_must_be_verified': True, 'distributed_minimum_replicas_per_role': 2,
        'resilient_minimum_physical_domains': 2, 'kind_physical_resilience': False})).hexdigest()


def build_certification_report(value, checks):
    """Validate trusted check metadata; never promote the repository inventory."""
    binding = _binding(value)
    required = required_checks(binding)
    if type(checks) is not list or len(checks) > 128:
        raise ValueError('Bounded qualification evidence list required')
    try:
        evidence = [_Evidence.model_validate(check) for check in checks]
    except (ValidationError, TypeError, ValueError):
        raise ValueError('Invalid bounded qualification evidence') from None
    by_name = {item.name: item for item in evidence}
    if len(by_name) != len(evidence) or set(by_name) - set(required):
        raise ValueError('Unknown or duplicate qualification check')
    fingerprint = binding_digest(binding)
    missing = sorted(set(required) - set(by_name))
    failed = sorted(name for name, item in by_name.items() if (
        item.status != 'passed' or item.kind != required[name]
        or item.binding_sha256 != fingerprint or item.artifact_sha256 is None
        or name == 'operations.independent_restore' and item.restore_verified is not True))
    if any(getattr(binding.topology.roles, role) < 2 for role in ('frontend', 'api', 'worker')):
        failed.append('runtime.replica_topology')
    if binding.profile == 'resilient' and (binding.topology.orchestrator == 'kind'
            or binding.topology.physical_failure_domains < 2):
        failed.append('resilience.independent_domains')
    complete = not missing and not failed
    certificate = None
    status = 'tested' if evidence else 'planned'
    if complete:
        status = binding.profile + '-certified'
        certificate = {'schema_version': 1, 'class': status, 'binding_sha256': fingerprint,
            'policy_sha256': _policy_digest(binding),
            'evidence_sha256': sha256(_canonical([item.model_dump(mode='json') for item in
                sorted(evidence, key=lambda item: item.name)])).hexdigest()}
    return {'schema_version': 1, 'profile': binding.profile, 'status': status,
        'binding_sha256': fingerprint, 'qualification_complete': complete,
        'missing_required': missing, 'failed_required': sorted(failed), 'certification': certificate}


def certificate_applies(certificate, value):
    """Only a stale-binding/policy check, not authentication or deployment approval."""
    try:
        supplied = _Certificate.model_validate(certificate)
        binding = _binding(value)
    except (ValidationError, ValueError, TypeError):
        return False
    return (supplied.qualification_class == binding.profile + '-certified'
        and supplied.binding_sha256 == binding_digest(binding)
        and supplied.policy_sha256 == _policy_digest(binding))
