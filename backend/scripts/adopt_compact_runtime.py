"""Explicit offline compact-to-replicas SQL phase; always leave application jobs held."""
import argparse
from io import StringIO
import json
from pathlib import Path
import re
import sys

from dotenv.parser import parse_stream
from pydantic_settings import EnvSettingsSource

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.db.session import configure_database, get_engine
from app.deployment.bootstrap import OperatorSettings, _receipt, write_identity
from app.deployment.compact_adoption import CompactSource, adopt_compact_runtime, capture_compact_source
from app.deployment.operator_database import operator_database_url, read_regular_input
from app.services.work.maintenance_control import MaintenanceOperator, REQUIRED_WRITERS


def _load_settings(path):
    """Only this bounded literal file selects the target, never ambient/root dotenv."""
    raw=read_regular_input(path,max_bytes=1024*1024).decode('utf-8-sig')
    values={}
    for binding in parse_stream(StringIO(raw)):
        if binding.error:
            raise ValueError('Invalid explicit runtime file')
        if binding.key is not None:
            key=binding.key.lower()
            if key in values:
                raise ValueError('Duplicate runtime configuration key')
            values[key]=binding.value

    class LiteralFileSource(EnvSettingsSource):
        def _load_env_vars(self):
            # EnvSettingsSource still handles aliases and JSON/list coercion.
            # Parsing the selected bytes directly prevents dotenv interpolation.
            return {key:value for key,value in values.items() if value is not None}

    class ExplicitFileSettings(OperatorSettings):
        @classmethod
        def settings_customise_sources(cls,settings_cls,init_settings,env_settings,dotenv_settings,file_secret_settings):
            return init_settings,LiteralFileSource(settings_cls)

    return ExplicitFileSettings(_env_file=None)


def _pairs(items):
    result={}
    for key,value in items:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key]=value
    return result


def _json(path):
    return json.loads(read_regular_input(path,max_bytes=65536),object_pairs_hook=_pairs)


def _inputs(args,settings):
    source=CompactSource.model_validate(_json(args.source_snapshot)).model_dump()
    receipt=_json(args.storage_receipt)
    counts,writers=_json(args.counts),_json(args.writer_receipts)
    if (type(counts) is not dict or set(counts)!={'frontend','api','worker'}
            or any(type(n) is not int or not 0<=n<=64 for n in counts.values())
            or type(writers) is not dict or set(writers)!=REQUIRED_WRITERS
            or any(type(value) is not str or value not in {'stopped','drained'} for value in writers.values())
            or type(receipt) is not dict or type(receipt.get('version')) is not int
            or receipt!=_receipt(settings,source['installation_id'],receipt.get('selection_digest',''))):
        raise ValueError('Explicit bound adoption inputs required')
    return source,receipt,counts,writers


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=['inspect','adopt'],required=True)
    parser.add_argument('--runtime-env-file',type=Path,required=True)
    parser.add_argument('--source-snapshot',type=Path,required=True,help='Create-only inspect output; same immutable proof for adopt')
    parser.add_argument('--operation-id',required=True)
    parser.add_argument('--owner-id',required=True)
    parser.add_argument('--operator-db-file',type=Path)
    parser.add_argument('--storage-receipt',type=Path)
    parser.add_argument('--counts',type=Path)
    parser.add_argument('--writer-receipts',type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv)
    held=None
    try:
        if (not re.fullmatch(r'[0-9a-f]{32}',args.operation_id) or args.operation_id=='0'*32
                or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',args.owner_id)
                or args.phase=='inspect' and args.apply):
            raise ValueError('Explicit offline phase and ownership required')
        settings=_load_settings(args.runtime_env_file)
        if (settings.okf_deployment_schema!='replicas-v1' or not settings.runtime_image_id
                or settings.runtime_identity_file is None or settings.artifact_backend not in {'s3','rwx'}):
            raise ValueError('Complete target replica runtime required')
        incoming=[args.runtime_env_file,args.source_snapshot,args.operator_db_file,
            args.storage_receipt,args.counts,args.writer_receipts]
        if any(settings.runtime_identity_file.resolve()==path.resolve() for path in incoming if path is not None):
            raise ValueError('Separate identity publication path required')
        prepared=None
        if args.phase=='adopt':
            if not all((args.storage_receipt,args.counts,args.writer_receipts)):
                raise ValueError('Complete adoption proof files required')
            prepared=_inputs(args,settings)
            if not args.apply:
                print(json.dumps(dict(status='planned',mutations=0,mutation_scope='database',
                    database_observed=False,jobs_held=None,workloads_started=False)))
                return 0
        database=operator_database_url(settings,args.operator_db_file)
        configure_database(database)
        with MaintenanceOperator(get_engine(),operation_id=args.operation_id,owner_id=args.owner_id,settings=settings) as op:
            if args.phase=='inspect':
                with op.transaction() as session:
                    snapshot=capture_compact_source(session,settings)
                op.assert_owned()
                write_identity(args.source_snapshot,snapshot)
                print(json.dumps(dict(status='inspected',mutations=0,mutation_scope='database',
                    database_observed=True,snapshot_written=True,workloads_started=False)))
                return 0
            source,receipt,counts,writers=prepared
            with op.transaction() as session:
                document=adopt_compact_runtime(session,settings,source,receipt,operation_id=args.operation_id,
                    intended_counts=counts,writer_receipts=writers)
            held=True  # Commit returned; uncertain outcomes never set this bit.
            op.assert_owned()
            write_identity(settings.runtime_identity_file,document)
        print('REPLICA_COMPACT_ADOPTION_IDENTITY:'+json.dumps(dict(identity=document,operation_id=args.operation_id,
            jobs_held=True,maintenance_epoch=1,workloads_started=False),sort_keys=True),flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 -- never print private Settings, paths, receipt values or SQL errors.
        print(json.dumps(dict(status='adoption-refused',type=type(exc).__name__,jobs_held=held,
            workloads_started=False)),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
