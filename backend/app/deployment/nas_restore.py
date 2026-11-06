"""Literal DS211 relocation from an authenticated read-only client binding."""
from hashlib import sha256
import os
import re
import stat
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .rwx_restore import DNS, Owner, Positive, UUID_TEXT, _canonical

RECIPE = 'synology-ds211-dsm6.2.4-25556-u8-nfs4.0-csi4.13.6-training-v1'


class NasSourceBinding(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    version: Literal[1]
    provenance_kind: Literal['selected-nas-readonly-client-source-v1']
    recipe: Literal['synology-ds211-dsm6.2.4-25556-u8-nfs4.0-csi4.13.6-training-v1']
    namespace: str
    namespace_uid: str
    node_uid: str
    claim_uid: str
    pv_uid: str
    driver: Literal['okf-training-nfs.csi.k8s.io']
    volume_handle: str
    server: Literal['10.10.1.5']
    share: Literal['/volume1/Vitaliy']
    subdir: str
    reader_pod_uid: str
    reader_image_ref: str
    wire_uid: Literal[1031]
    wire_gid: Literal[100]
    reader_source_sha256: str
    server_filesystem_identity_claimed: Literal[False]
    nfs_source: str
    nfs_version: Literal['4.0']
    mount_root: Literal['/']
    readonly: Literal[True]
    client_directory_inode: Positive
    client_directory_device: Positive
    client_directory_uid: Owner
    client_directory_gid: Owner
    client_directory_mode: Annotated[int,Field(strict=True,ge=0,le=0o7777)]
    client_objects_inode: Positive


def _source(plan,value):
    try:
        scope=next(v for v in plan.storage if v.component=='artifacts')
        if (not isinstance(value,dict) or scope.provider!='external' or scope.recipe!=RECIPE
                or sha256(_canonical(value)).hexdigest()!=scope.scope_sha256
                or type(value.get('version')) is not int or type(value.get('readonly')) is not bool
                or type(value.get('server_filesystem_identity_claimed')) is not bool):
            raise ValueError()
        source=NasSourceBinding.model_validate(value)
        if source.namespace!=plan.source.namespace or not re.fullmatch('replica-test-'+DNS,source.namespace):
            raise ValueError()
        for name in ('namespace_uid','node_uid','claim_uid','pv_uid','reader_pod_uid'):
            uid=getattr(source,name)
            if not re.fullmatch(UUID_TEXT,uid) or UUID(uid).int==0:
                raise ValueError()
        if (not re.fullmatch('okf-syn-'+re.escape(source.namespace)+'-artifacts-pvc-'+source.claim_uid,source.subdir)
                or source.nfs_source!=source.server+':'+source.share+'/'+source.subdir
                or source.volume_handle!=f'{source.server}#{source.share.lstrip("/")}#{source.subdir}#pvc-{source.claim_uid}#retain'
                or not re.fullmatch('[a-z0-9./:_-]+@sha256:[0-9a-f]{64}',source.reader_image_ref)
                or not re.fullmatch('[0-9a-f]{64}',source.reader_source_sha256)
                or source.client_directory_inode==source.client_objects_inode
                or (source.client_directory_uid,source.client_directory_gid,source.client_directory_mode)!=(0,100,0o2770)):
            raise ValueError()
        return source,scope.scope_sha256
    except (ValueError,TypeError,StopIteration):
        raise ValueError('Authenticated selected NAS client binding required') from None


def _wire_identity():
    try:
        return os.getuid(),os.getgid()
    except AttributeError:
        raise ValueError('Native Linux wire identity required') from None


def verify_nas_relocation(settings,plan,source_binding):
    """Observe the target locally; never accept caller-supplied mount facts."""
    from .rwx_restore import _mount, _observe_nfs_root
    source,scope=_source(plan,source_binding)
    namespace=settings.deployment_namespace
    if (settings.artifact_backend!='rwx' or settings.artifact_provider!='external'
            or not re.fullmatch('replica-test-'+DNS,namespace) or namespace==source.namespace
            or settings.artifact_root is None or _wire_identity()!=(source.wire_uid,source.wire_gid)):
        raise ValueError('New isolated NAS target and selected wire identity required')
    observed=_observe_nfs_root(settings.artifact_root)
    mounted,options=_mount(observed,version='4.0')
    prefix=source.server+':';old=source.share+'/'+source.subdir
    if not mounted.startswith(prefix) or options.get('addr')!=source.server or 'hard' not in options or 'soft' in options:
        raise ValueError('Selected hard NFS4.0 server required')
    export=mounted[len(prefix):]
    if (export==old or export.startswith(old+'/') or old.startswith(export.rstrip('/')+'/')
            or not re.fullmatch(re.escape(source.share)+'/okf-syn-'+re.escape(namespace)+'-artifacts-pvc-'+UUID_TEXT,export)):
        raise ValueError('Disjoint new namespace CSI claim export required')
    mode=stat.S_IMODE(observed['mode'])
    if (not stat.S_ISDIR(observed['mode']) or type(observed['inode']) is not int or observed['inode']<=0
            or observed['inode']==source.client_directory_inode
            or (observed['uid'],observed['gid'])!=(source.client_directory_uid,source.client_directory_gid)
            or mode not in {0o770,0o2770}):
        raise ValueError('Different restrictive client-observed NAS directory required')
    return dict(source_scope_sha256=scope,namespace=namespace,nfs_source=mounted,nfs_version='4.0',
        client_directory_inode=observed['inode'],client_directory_uid=observed['uid'],client_directory_gid=observed['gid'],
        client_directory_mode=mode,wire_uid=source.wire_uid,wire_gid=source.wire_gid,server_filesystem_identity_claimed=False)
