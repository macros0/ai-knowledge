"""Explicit rpl04 provider imports into fresh held storage, never schema adoption or activation."""
from __future__ import annotations

import json
import re
import sys
from hashlib import sha256
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from replica_backup import digest, validate_config
from replica_restore import assert_new_target


def validate_imported_predecessor(snapshot,source_config,target_config,plan):
    from app.deployment.replica_backup import (
        BackupPlan,
        PreMaintenanceSourceState,
        canonical,
    )
    validate_config(source_config)
    assert_new_target(target_config,plan)
    if (not isinstance(plan,BackupPlan) or not isinstance(plan.source,PreMaintenanceSourceState)
            or plan.schema_revision!='rpl04_runtime_identity'
            or source_config['namespace']!=plan.source.namespace
            or source_config['installation_id']!=plan.source.installation_id
            or snapshot['database']!=target_config['database']
            or snapshot['schema_revisions']!=['rpl04_runtime_identity']
            or snapshot['maintenance_table'] is not False or snapshot['work_maintenance_epoch'] is not False
            or len(snapshot['identities'])!=1 or len(snapshot['controls'])!=1
            or type(snapshot['fence_audit_events']) is not int or snapshot['fence_audit_events']!=1
            or any(type(snapshot[name]) is not int or snapshot[name]!=0 for name in
                ('active_work','active_chats','owned_model_slots','deleting_artifacts'))
            or snapshot['counts']!=plan.counts.model_dump()):
        raise ValueError('Exact imported predecessor schema, counts and audited S4 fence required')
    identity,control=snapshot['identities'][0],snapshot['controls'][0]
    expected={'backend':'s3','store_id':source_config['store_id'],'bucket':source_config['bucket'],
        'prefix':source_config['prefix'],
        'endpoint':f"https://{source_config['storage_release']}-artifacts.{source_config['namespace']}.svc:9000"}
    descriptor=control.get('active_descriptor')
    transport={'region':'us-east-1','addressing_style':'path','ca_bundle':'/etc/okf/trust/ca-bundle.crt','allow_http':False}
    if (identity.get('id')!=1 or identity.get('installation_id')!=plan.source.installation_id
            or identity.get('schema_revision')!=plan.schema_revision
            or not re.fullmatch('[0-9a-f]{64}',identity.get('fingerprint',''))
            or sha256(canonical(identity['fingerprint'])).hexdigest()!=plan.model_vector_sha256
            or not re.fullmatch('sha256:[0-9a-f]{64}',identity.get('storage_receipt_digest',''))
            or control.get('id')!=1 or control.get('installation_id')!=plan.source.installation_id
            or control.get('active_store_id')!=target_config['store_id']
            or control.get('maintenance_token')!=plan.backup_id or control.get('migration_id') is not None
            or control.get('readonly_roots')!=[] or not isinstance(descriptor,dict)
            or set(descriptor)-set(expected)-set(transport)
            or any(descriptor.get(k)!=v for k,v in expected.items())
            or any(k in descriptor and descriptor[k]!=v for k,v in transport.items())
            or type(descriptor.get('allow_http',False)) is not bool):
        raise ValueError('Imported source identity, storage descriptor or S4 token differs')
    entries={e.id:e for e in plan.inventory if e.component=='artifacts'}
    artifacts=snapshot['artifacts']
    if len(artifacts)!=len(entries) or {a['object_id'] for a in artifacts}!=set(entries):
        raise ValueError('Imported accepted artifact inventory differs')
    for artifact in artifacts:
        entry=entries[artifact['object_id']]
        if (artifact['store_id']!=target_config['store_id'] or artifact['key']!='objects/'+entry.id
                or type(artifact['size']) is not int
                or (artifact['size'],artifact['sha256'])!=(entry.expected_size,entry.expected_sha256)):
            raise ValueError('Imported artifact scope, size or checksum differs')
    return {'counts':dict(snapshot['counts']),'artifacts':[dict(a) for a in artifacts]}


def read_imported_predecessor(connection,source_config,target_config,plan):
    """Read actual old SQL shape before provider writes; no rpl05 model queries."""
    from replica_upgrade import corpus
    from sqlalchemy import inspect, text
    if connection.dialect.name!='postgresql':
        raise ValueError('Selected isolated PostgreSQL required for predecessor import')
    inspector=inspect(connection)
    if not inspector.has_table('work_items'):
        raise ValueError('Imported predecessor tables are missing')
    snapshot={'database':connection.scalar(text('SELECT current_database()')),
        'schema_revisions':connection.execute(text('SELECT version_num FROM alembic_version')).scalars().all(),
        'maintenance_table':inspector.has_table('replica_maintenance_state'),
        'work_maintenance_epoch':any(c['name']=='maintenance_epoch' for c in inspector.get_columns('work_items')),
        'identities':[dict(r) for r in connection.execute(text('SELECT * FROM replica_runtime_identity LIMIT 2')).mappings()],
        'controls':[dict(r) for r in connection.execute(text('SELECT * FROM artifact_storage_control LIMIT 2')).mappings()]}
    audit=connection.execute(text("SELECT new_value FROM audit_log WHERE action_type='replica_maintenance_enter' "
        "AND target_type='predecessor_artifact_fence' AND target_id=:operation LIMIT 3"),{'operation':plan.backup_id}).scalars().all()
    snapshot['fence_audit_events']=len(audit) if all(v=={'epoch':0} for v in audit) else -1
    predicates={'active_work':"work_items WHERE status='running' OR owner_id IS NOT NULL OR lease_until IS NOT NULL",
        'active_chats':"chat_runs WHERE status='running'",
        'owned_model_slots':"replica_capacity_slots WHERE resource='llm' AND (owner_id IS NOT NULL OR release_required)",
        'deleting_artifacts':"artifact_objects WHERE state='deleting'"}
    snapshot.update({key:connection.scalar(text('SELECT count(*) FROM '+statement)) for key,statement in predicates.items()})
    snapshot['counts']={key:connection.scalar(text('SELECT count(*) FROM '+table)) for key,table in {
        'documents':'documents','concepts':'okf_concepts','chunks':'document_chunks',
        'work_items':'work_items','audit_events':'audit_log'}.items()}
    snapshot['artifacts']=[dict(r) for r in connection.execute(text("SELECT * FROM artifact_objects WHERE state='ready' ORDER BY object_id LIMIT 100001")).mappings()]
    if len(snapshot['artifacts'])>99998:
        raise ValueError('Imported artifact row bound exceeded')
    snapshot['counts']['blobs']=len(snapshot['artifacts'])
    result=validate_imported_predecessor(snapshot,source_config,target_config,plan)
    tables=corpus(connection)
    result.update(sql_scope_sha256=digest([snapshot['identities'],snapshot['controls']]),
        held_work_state_sha256=digest({name:tables[name] for name in ('work_items','work_progress')}),
        materialized_tables={k:v for k,v in tables.items() if k not in {'work_items','work_progress'}})
    return result


def predecessor_offline_observation(items,nodes,containers,value,binding):
    """An unreachable/replaced node or surviving orphan is never source-off proof."""
    namespaces=[o for o in items if o.get('kind')=='Namespace']
    if (len(namespaces)!=1 or namespaces[0]['metadata'].get('name')!=value['namespace']
            or namespaces[0]['metadata'].get('uid')!=value['namespace_uid']
            or namespaces[0]['metadata'].get('deletionTimestamp') or not binding.get('nodes')):
        raise ValueError('Original source namespace and node bindings required')
    current={n['metadata']['name']:n for n in nodes}
    for name,uid in binding['nodes'].items():
        node=current.get(name,{})
        if (node.get('metadata',{}).get('uid')!=uid or node.get('metadata',{}).get('deletionTimestamp')
                or not any(c.get('type')=='Ready' and c.get('status')=='True'
                    for c in node.get('status',{}).get('conditions',[]))):
            raise ValueError('Original source node is unreachable or replaced')
    expected={('Deployment',r) for r in ('frontend','api','worker','model-stub')}
    expected|={('StatefulSet',value['storage_release']+'-'+r) for r in ('postgres','qdrant','artifacts')}
    controllers=[]
    for obj in items:
        kind,meta,status=obj.get('kind'),obj.get('metadata',{}),obj.get('status',{})
        if kind in {'Deployment','StatefulSet'}:
            release=value['application_release'] if kind=='Deployment' else value['storage_release']
            if ((kind,meta.get('name')) not in expected or not meta.get('uid')
                    or meta.get('deletionTimestamp') or meta.get('labels',{}).get('app.kubernetes.io/instance')!=release
                    or obj['spec'].get('replicas')!=0 or status.get('observedGeneration')!=meta.get('generation')
                    or any(status.get(k,0)!=0 for k in ('replicas','readyReplicas','availableReplicas','updatedReplicas','currentReplicas'))):
                raise ValueError('A source controller is available or unknown')
            controllers.append({'kind':kind,'name':meta['name'],'uid':meta['uid'],
                'generation':meta['generation'],'spec_sha256':digest(obj['spec'])})
        elif kind=='Pod':
            if (status.get('phase') not in {'Succeeded','Failed'}
                    or any('terminated' not in s.get('state',{}) for s in status.get('containerStatuses',[]))):
                raise ValueError('A source Pod is still available')
        elif kind=='Job' and (status.get('active',0) or not any(c.get('type') in {'Complete','Failed'}
                and c.get('status')=='True' for c in status.get('conditions',[]))):
            raise ValueError('A source Job is not terminal')
        elif kind=='CronJob' and (obj.get('spec',{}).get('suspend') is not True or status.get('active')):
            raise ValueError('A source schedule is active')
        elif kind in {'HorizontalPodAutoscaler','VerticalPodAutoscaler','DaemonSet','ReplicationController'}:
            raise ValueError('An unsupported source writer controller remains')
        elif kind=='ReplicaSet' and (obj['spec'].get('replicas',0) or status.get('replicas',0)):
            raise ValueError('A source replica controller remains active')
        elif kind=='EndpointSlice' and any(e.get('addresses') for e in obj.get('endpoints') or []):
            raise ValueError('A source endpoint is still available')
    if len(controllers)!=7 or {(o['kind'],o['name']) for o in controllers}!=expected:
        raise ValueError('Exact stopped source controller set required')
    retired=set(binding.get('writer_pod_uids',[]))|{binding.get('provider_pod_uid')}
    for container in containers:
        labels=container.get('labels',{})
        if container.get('state')=='CONTAINER_RUNNING' and (labels.get('io.kubernetes.pod.namespace')==value['namespace']
                or labels.get('io.kubernetes.pod.uid') in retired):
            raise ValueError('A source process still exists in native CRI')
    return digest({'namespace_uid':value['namespace_uid'],'nodes':binding['nodes'],
        'controllers':sorted(controllers,key=lambda o:(o['kind'],o['name'])),'running_processes':0})


def import_predecessor_backup(config,*,namespace_uid,source_config,source_namespace_uid,
        source_operation_journal,archive,key_file,report,operation_journal):
    """Fresh provider import only; keep the authenticated old S4 token unchanged."""
    import os
    from datetime import datetime, timezone
    from tempfile import TemporaryFile
    from uuid import UUID, uuid4

    from app.deployment.replica_backup import (
        BackupReader,
        PreMaintenanceSourceState,
        _sync_directory,
        canonical,
    )
    from app.services.work.maintenance_control import MaintenanceOperator
    from replica_backup import ROOT, Lab, connections
    from replica_predecessor_backup import (
        controller_snapshot,
        fetch_predecessor_observations,
    )
    from replica_restore import assert_jobs_held, restore_postgres, volumes
    from replica_runtime_run import read_json, write_json
    from sqlalchemy import inspect, text

    validate_config(config)
    validate_config(source_config)
    UUID(namespace_uid);UUID(source_namespace_uid)
    reader=BackupReader(archive,Path(key_file).read_bytes())
    plan=reader.manifest.plan
    if (not isinstance(plan.source,PreMaintenanceSourceState) or plan.schema_revision!='rpl04_runtime_identity'
            or source_config['namespace']!=plan.source.namespace
            or source_config['installation_id']!=plan.source.installation_id
            or source_namespace_uid!=str(plan.source.stop_proof.namespace_uid)):
        raise ValueError('Authenticated predecessor source scope required')
    assert_new_target(config,plan)
    private=Path(config['ca_file']).resolve(strict=True).parent
    operation_journal,report=Path(operation_journal),Path(report)
    if (operation_journal.resolve().parent!=private or operation_journal.exists()
            or operation_journal.resolve().is_relative_to(Path(archive).resolve())
            or report.resolve().parent!=ROOT/'docs/superpowers/reports' or report.exists()
            or Path(source_operation_journal).resolve().parent!=Path(source_config['ca_file']).resolve().parent):
        raise ValueError('New protected target operation and public report required')
    original=read_json(source_operation_journal,limit=4*1024*1024)
    if (original.get('phase')!='predecessor-archive-verified-source-held' or original.get('active_role') is not None
            or original.get('operation_id')!=plan.backup_id or original.get('namespace_uid')!=source_namespace_uid
            or original.get('plan')!=plan.model_dump(mode='json')
            or sha256(canonical(original.get('fingerprint'))).hexdigest()!=plan.model_vector_sha256
            or original.get('binding',{}).get('controller_snapshot_sha256')!=plan.source.stop_proof.controller_snapshot_sha256):
        raise ValueError('Exact completed source backup provenance required')
    lab,source=Lab(config,namespace_uid),Lab(source_config,source_namespace_uid)
    source_value={**source_config,'namespace_uid':source_namespace_uid}
    target_value={**config,'namespace_uid':namespace_uid}
    initial_source=predecessor_offline_observation(*fetch_predecessor_observations(source),source_value,original['binding'])
    source_volumes=volumes(source)
    lab.drained();assert_jobs_held(lab.inventory())
    target_volumes=volumes(lab)
    if {v[2] for v in source_volumes}&{v[2] for v in target_volumes}:
        raise ValueError('Fresh recovery volumes must not overlap the source')
    target_objects,_,_=fetch_predecessor_observations(lab)
    target_snapshot=controller_snapshot(target_objects,target_value)
    engine,s3,q=connections(config)
    operation=uuid4().hex
    try:
        with MaintenanceOperator(engine,operation_id=operation,owner_id='owned-predecessor-import') as operator:
            with engine.connect() as connection:
                if (connection.scalar(text('SELECT current_database()'))!=config['database']
                        or inspect(connection).get_table_names(schema='public')):
                    raise ValueError('Empty new PostgreSQL target required')
            response=q.get('/collections');response.raise_for_status()
            if response.json()['result']['collections'] or s3.list_buckets()['Buckets']:
                raise ValueError('Empty new Qdrant and S3 instances required')
            state={'version':1,'phase':'predecessor-provider-import-intent','operation_id':operation,
                'backup_id':plan.backup_id,'namespace':config['namespace'],'namespace_uid':namespace_uid,
                'source_namespace_uid':source_namespace_uid,'source_off_observation_sha256':initial_source,
                'source_volumes':source_volumes,'target_volumes':target_volumes,
                'target_controller_snapshot_sha256':target_snapshot,'completed_artifacts':[],
                'schema_revision':plan.schema_revision,'source_epoch':0,'identity_rebound':False,
                'activated':False,'production_schedule':'disabled'}
            with operation_journal.open('x',encoding='utf-8') as output:
                json.dump(state,output);output.flush();os.fsync(output.fileno())
            _sync_directory(operation_journal.parent)

            def guard():
                operator.assert_owned()
                if predecessor_offline_observation(*fetch_predecessor_observations(source),source_value,original['binding'])!=initial_source:
                    raise ValueError('Original source-off observation changed')
                if volumes(source)!=source_volumes or volumes(lab)!=target_volumes:
                    raise ValueError('Source or recovery PVC binding changed')
                lab.drained();assert_jobs_held(lab.inventory())
                if controller_snapshot(fetch_predecessor_observations(lab)[0],target_value)!=target_snapshot:
                    raise ValueError('Stopped recovery controllers or storage Pods changed')

            guard()
            pg_entry=next(e for e in plan.inventory if e.component=='postgres')
            restore_postgres(reader,pg_entry.id,lab,private/(operation+'-predecessor-pg-restore.log'))
            state['phase']='predecessor-postgres-imported-s4-held'
            write_json(operation_journal,state)
            print(json.dumps({'phase':state['phase'],'operation_id':operation,'application_replicas':0}),flush=True)
            with engine.connect() as connection:
                before=read_imported_predecessor(connection,source_config,config,plan)

                def sql_held():
                    current=read_imported_predecessor(connection,source_config,config,plan)
                    if current!=before:
                        raise ValueError('Imported predecessor data or held work changed')

                guard();sql_held()
                q_entry=next(e for e in plan.inventory if e.component=='qdrant')
                with TemporaryFile(dir=private) as stream:
                    reader.copy(q_entry.id,stream);stream.seek(0)
                    response=q.post('/collections/'+config['collection']+'/snapshots/upload?wait=true&priority=snapshot',
                        files={'snapshot':('predecessor.snapshot',stream,'application/octet-stream')})
                    response.raise_for_status()
                state['phase']='predecessor-qdrant-imported-s4-held'
                write_json(operation_journal,state)
                guard();sql_held()
                s3.create_bucket(Bucket=config['bucket'])
                for artifact in before['artifacts']:
                    guard();sql_held()
                    key=config['prefix']+artifact['key']
                    with TemporaryFile(dir=private) as stream:
                        reader.copy(artifact['object_id'],stream);stream.seek(0)
                        s3.put_object(Bucket=config['bucket'],Key=key,Body=stream,ContentLength=artifact['size'],
                            ContentType=artifact['media_type'],Metadata={'sha256':artifact['sha256']},IfNoneMatch='*')
                    body=s3.get_object(Bucket=config['bucket'],Key=key)['Body']
                    digest_hash=sha256();size=0
                    try:
                        for block in iter(lambda body=body:body.read(4*1024*1024),b''):
                            size+=len(block);digest_hash.update(block)
                    finally:
                        body.close()
                    if (size,digest_hash.hexdigest())!=(artifact['size'],artifact['sha256']):
                        raise ValueError('Restored predecessor blob checksum differs')
                    guard();sql_held()
                    state['completed_artifacts'].append(artifact['object_id'])
                    state['phase']='predecessor-artifacts-importing-s4-held'
                    write_json(operation_journal,state)
                guard();sql_held();reader.verify()
                response=q.post('/collections/'+config['collection']+'/points/count',json={'exact':True})
                response.raise_for_status()
                points=response.json()['result']['count']
                if points!=plan.counts.concepts+plan.counts.chunks:
                    raise ValueError('Restored predecessor point count differs')
                result={'status':'predecessor-providers-restored-s4-held','backup_id':plan.backup_id,
                    'operation_id':operation,'target_namespace':config['namespace'],'namespace_uid':namespace_uid,
                    'target_volumes':target_volumes,'source_volumes':source_volumes,'counts':before['counts'],
                    'qdrant_points':points,'artifact_hashes_verified':len(before['artifacts']),
                    'held_work_state_sha256':before['held_work_state_sha256'],
                    'materialized_tables':before['materialized_tables'],'sql_scope_sha256':before['sql_scope_sha256'],
                    'schema_revision':plan.schema_revision,'source_epoch':0,'source_unavailable':True,
                    'source_running_cri_containers':0,'application_replicas':0,'restored_executed_jobs':0,
                    'identity_rebound':False,'schema_migrated':False,'activated':False,
                    'import_finished_at':datetime.now(timezone.utc).isoformat(),'production_schedule':'disabled'}
                state.update(phase='predecessor-providers-verified-s4-held',receipt=result)
                write_json(operation_journal,state)
        with report.open('x',encoding='utf-8') as output:json.dump(result,output,indent=2);output.write('\n')
        print(json.dumps(result),flush=True)
        return result
    finally:
        q.close();s3.close();engine.dispose()


def main(argv=None):
    import argparse
    import traceback

    from replica_runtime_run import read_json
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('config','source-config','source-operation-journal','archive','key-file','report','operation-journal'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--namespace-uid',required=True)
    parser.add_argument('--source-namespace-uid',required=True)
    args=parser.parse_args(argv)
    try:
        import_predecessor_backup(read_json(args.config,limit=16384),namespace_uid=args.namespace_uid,
            source_config=read_json(args.source_config,limit=16384),source_namespace_uid=args.source_namespace_uid,
            source_operation_journal=args.source_operation_journal,archive=args.archive,key_file=args.key_file,
            report=args.report,operation_journal=args.operation_journal)
        return 0
    except Exception as exc:  # noqa: BLE001 -- provider errors can contain credentials; output fixed metadata only.
        with (args.config.parent/'predecessor-import-error.log').open('w',encoding='utf-8') as output:
            traceback.print_exc(file=output)
        print(json.dumps({'predecessor_import_refused':type(exc).__name__,'source_release_attempted':False,
            'target_activation_attempted':False,'frames':[{'function':f.name,'line':f.lineno}
                for f in traceback.extract_tb(exc.__traceback__) if Path(f.filename).resolve()==Path(__file__).resolve()]}),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
