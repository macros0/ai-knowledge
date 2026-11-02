"""Native old-schema SQL validation before any new provider is activated."""
import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db.models import ArtifactStorageControl, AuditLog
from app.deployment.replica_backup import BackupPlan
from tests.replica_postgres.test_predecessor_backup import source_config, source_descriptor
from tests.replica_postgres.test_schema_adoption import (
    bootstrap_settings as bootstrap_settings,
)
from tests.replica_postgres.test_schema_adoption import (
    predecessor as predecessor,
)
from tests.test_replica_backup_coordinator import config
from tests.test_replica_predecessor_restore import recovery_plan


def importer():
    path=Path(__file__).resolve().parents[3]/'scripts/kubernetes/replica_predecessor_restore.py'
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('native_predecessor_import',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def imported_sql(predecessor):
    engine,_config,_settings,old=predecessor
    source=config()
    source['installation_id']=old['installation_id']
    target=source_config(engine)
    target['installation_id']='8'*32
    operation='b'*32
    with engine.begin() as connection:
        descriptor=source_descriptor(source)
        descriptor.update(region='us-east-1',addressing_style='path',ca_bundle='/etc/okf/trust/ca-bundle.crt',allow_http=False)
        connection.execute(ArtifactStorageControl.__table__.update().values(active_store_id=source['store_id'],
            active_descriptor=descriptor,maintenance_token=operation))
        connection.execute(AuditLog.__table__.insert().values(event_id=str(uuid4()),action_type='replica_maintenance_enter',
            target_type='predecessor_artifact_fence',target_id=operation,new_value={'epoch':0}))
        plan=BackupPlan.model_validate(recovery_plan(connection,old['runtime_fingerprint'],
            installation_id=old['installation_id'],namespace=source['namespace'],backup_id=operation))
    return engine,source,target,plan


def test_pg_import_reads_actual_old_columns_and_keeps_source_identity(imported_sql):
    engine,source,target,plan=imported_sql
    with engine.connect() as connection:
        before=connection.scalar(text('SELECT fingerprint FROM replica_runtime_identity'))
        result=importer().read_imported_predecessor(connection,source,target,plan)
        assert result['counts']==plan.counts.model_dump()
        assert len(result['materialized_tables'])==9
        assert result['held_work_state_sha256']
        assert connection.scalar(text('SELECT fingerprint FROM replica_runtime_identity'))==before
        assert connection.scalar(text('SELECT maintenance_token FROM artifact_storage_control'))==plan.backup_id
        assert connection.scalar(text('SELECT version_num FROM alembic_version'))=='rpl04_runtime_identity'


@pytest.mark.parametrize('fault',['token','epoch_column'])
def test_pg_import_rejects_a_changed_old_fence_or_partially_migrated_shape(imported_sql,fault):
    engine,source,target,plan=imported_sql
    with engine.begin() as connection:
        if fault=='token':
            connection.execute(text('UPDATE artifact_storage_control SET maintenance_token=NULL'))
        else:
            connection.execute(text('ALTER TABLE work_items ADD COLUMN maintenance_epoch bigint NOT NULL DEFAULT 0'))
    with engine.connect() as connection,pytest.raises(ValueError):
        importer().read_imported_predecessor(connection,source,target,plan)
