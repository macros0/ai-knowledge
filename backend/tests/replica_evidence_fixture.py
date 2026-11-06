"""Synthetic signed harness fixtures; never production keys or real acceptance."""
import copy
from hashlib import sha256
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from test_scripts.replica_acceptance_report import binding_digest, required_checks
from tests.test_replica_acceptance_report import binding

DOMAIN = b'okf-replica-evidence-v1\0'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def fixture(tmp_path):
    value = binding()
    scope = dict(run_id='abcdefgh', namespace_uid='11111111-2222-4333-8444-555555555555',
        installation_id='a'*32, acceptance_id='b'*32)
    private = Ed25519PrivateKey.generate()
    producer = 'd'*64
    root = tmp_path/'evidence'
    root.mkdir()
    trust = tmp_path/'trust.json'
    trust.write_bytes(canonical(dict(schema_version=1, issuers=[dict(id='test-harness', enabled=True,
        public_key=private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
        producer_sha256=producer, checks=list(required_checks(value))+['qualification.bundle'])])))
    receipts = []
    for index, (name, kind) in enumerate(required_checks(value).items()):
        artifact = dict(schema_version=1, name=name, kind=kind, binding_sha256=binding_digest(value),
            scope=copy.deepcopy(scope), producer_sha256=producer,
            outcomes=[dict(id='synthetic-assertion', status='passed')],
            native_reference_sha256=['c'*64] if kind=='live' else [],
            restore_verified=True if name=='operations.independent_restore' else None)
        artifact_path = root/f'artifact-{index}.json'
        artifact_path.write_bytes(canonical(artifact))
        receipt = dict(schema_version=1, issuer='test-harness', producer_sha256=producer,
            name=name, kind=kind, binding_sha256=binding_digest(value), scope=copy.deepcopy(scope),
            artifact=dict(path=artifact_path.name, sha256=sha256(artifact_path.read_bytes()).hexdigest()))
        receipt['signature'] = private.sign(DOMAIN+canonical(receipt)).hex()
        receipt_path = root/f'receipt-{index}.json'
        receipt_path.write_bytes(canonical(receipt))
        receipts.append(dict(path=receipt_path.name,sha256=sha256(receipt_path.read_bytes()).hexdigest()))
    bundle = root/'bundle.json'
    data=dict(binding=value,scope=scope,bundle_path=bundle,trust_path=trust,evidence_root=root,private=private)
    seal_bundle(data,dict(schema_version=1,issuer='test-harness',producer_sha256=producer,
        binding_sha256=binding_digest(value),scope=scope,receipts=receipts))
    return data


def seal_bundle(data, bundle):
    bundle.pop('signature',None)
    bundle['signature']=data['private'].sign(DOMAIN+b'closure\0'+canonical(bundle)).hex()
    data['bundle_path'].write_bytes(canonical(bundle))


def change_receipt(data, *, index=0, receipt_change=None, artifact_change=None):
    bundle = json.loads(data['bundle_path'].read_bytes())
    reference = bundle['receipts'][index]
    path = data['evidence_root']/reference['path']
    receipt = json.loads(path.read_bytes())
    if artifact_change:
        artifact_path = data['evidence_root']/receipt['artifact']['path']
        artifact = json.loads(artifact_path.read_bytes())
        artifact_change(artifact)
        artifact_path.write_bytes(canonical(artifact))
        receipt['artifact']['sha256'] = sha256(artifact_path.read_bytes()).hexdigest()
    if receipt_change:
        receipt_change(receipt)
    receipt.pop('signature')
    receipt['signature'] = data['private'].sign(DOMAIN+canonical(receipt)).hex()
    path.write_bytes(canonical(receipt))
    reference['sha256'] = sha256(path.read_bytes()).hexdigest()
    seal_bundle(data,bundle)


def arguments(data):
    return {key: value for key,value in data.items() if key != 'private'}
