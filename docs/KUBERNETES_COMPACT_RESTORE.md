# Ручной stopped restore домашнего compact

Версия для текущих исходников main; выполняйте из корня checkout. Архивный frozen kit имеет собственный runbook.

Это последовательность для одного оператора на healthy single-node kind. При ошибке прекратить работу, сохранить lock и артефакты, разобрать состояние writers; автоматического resume нет. Следующую попытку импорта выполнять в ещё одном пустом target. Исходная копия/PVC не удаляются.

Для source backup, который оставляет приложение остановленным (из корня checkout):

```bash
export DR_CONTEXT=kind-home DR_SOURCE_NS=okf-home-app
export DR_SOURCE_PRIVATE="$HOME/okf-home-private/access"
export DR_BACKUP_ROOT="$HOME/okf-home-backups"
export DR_IMAGE_VALUES="$PWD/deploy/kubernetes/profiles/compact.values.example.yaml"
# Выбранный при deploy overlay с точными source images; при иных tags/digests указать тот файл.
python3 - <<'PY'
import os,sys
from pathlib import Path
sys.path.insert(0,'scripts/kubernetes')
import lifecycle as lc
p=Path(os.environ['DR_SOURCE_PRIVATE'])
m=lc.Manager(os.environ['DR_CONTEXT'],os.environ['DR_SOURCE_NS'],'ai-knowledge',
    [p/'base.values.yaml',p/'sso.values.yaml',Path(os.environ['DR_IMAGE_VALUES'])],expected_tier='home')
m.preflight();m.acquire();old=m.stop()
assert old is not None and not m.application_pods()
m.backup_stopped(Path(os.environ['DR_BACKUP_ROOT']),old)
m.release_lock()
print('Backup complete; source remains stopped')
PY
```

В закрытом каталоге выбрать завершённый `<operation>/manifest.json`, проверить `lc.verify_backup()`, перенести архив вне исходной VM и сверить его SHA-256. В следующем примере `DR_BACKUP` — уже распакованная проверенная копия. `DR_SOURCE_PRIVATE` — закрытый каталог исходного тестового CA: он нужен только для выпуска новых leaf-сертификатов этого лабораторного профиля. Он не входит в поставку и не передаётся приложению; другого trust/PKI-профиля эта процедура не обещает. Срок CA должен ещё действовать.

1. По команде `home_access.py` из установки подготовить **ещё два новых namespace** и новый приватный каталог, но **не вызывать deploy; backend image tag выбирается из текущей source-копии, third values overlay должен сохранять точные images из backup/release.json**. Это создаёт четыре новых PVC, отдельный IdP и новый случайный `APP_SECRET_KEY`. Использовать те же images. Задать переменные:

```bash
export DR_TARGET_NS=okf-home-restored DR_TARGET_ACCESS=okf-home-restored-access
export DR_PRIVATE="$HOME/okf-home-private/restored"
export DR_BACKUP="$HOME/okf-home-backups/<operation>"
# DR_CONTEXT, DR_SOURCE_NS, DR_SOURCE_PRIVATE заданы выше.
```

2. Сохранить exact публичный trust backup; выпустить отдельные target leaf сертификаты с исходным тестовым CA, исправить только target Secrets и публичную ConfigMap. Из корня checkout:

```bash
python3 - <<'PY'
import os,sys,json,subprocess
from pathlib import Path
import yaml
sys.path.insert(0,'scripts/kubernetes')
import lifecycle as lc
from home_access import issue_host_certificate
source=Path(os.environ['DR_BACKUP']);lc.verify_backup(source)
p=Path(os.environ['DR_PRIVATE']);ca=Path(os.environ['DR_SOURCE_PRIVATE'])
ctx=os.environ['DR_CONTEXT'];app=os.environ['DR_TARGET_NS'];access=os.environ['DR_TARGET_ACCESS']
assert app!=os.environ['DR_SOURCE_NS'] and app!=access
bundle=(source/'trust-bundle.pem').read_bytes()
cm={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'compact-restored-roots'},
    'immutable':True,'data':{'ca-bundle.crt':bundle.decode()}}
subprocess.run(['kubectl','--context',ctx,'-n',app,'create','-f','-'],input=yaml.safe_dump(cm).encode(),check=True)
for role,ns in [('identity',access),('knowledge',app)]:
    cert=issue_host_certificate(p,role,access,ca/'ca.crt',ca/'ca.key',p/'tls.key')
    doc={'apiVersion':'v1','kind':'Secret','metadata':{'name':'lab-tls'},'type':'kubernetes.io/tls',
        'stringData':{'tls.crt':cert.read_text(),'tls.key':(p/'tls.key').read_text()}}
    subprocess.run(['kubectl','--context',ctx,'-n',ns,'apply','-f','-'],input=yaml.safe_dump(doc).encode(),check=True,stdout=subprocess.DEVNULL)
probe=json.loads(subprocess.run(['kubectl','--context',ctx,'-n',app,'get','secret','synthetic-probe','-o','json'],check=True,capture_output=True).stdout)
probe['stringData']={'ca.crt':(ca/'ca.crt').read_text()}
subprocess.run(['kubectl','--context',ctx,'-n',app,'replace','-f','-'],input=json.dumps(probe).encode(),check=True,stdout=subprocess.DEVNULL)
value=yaml.safe_load((p/'sso.values.yaml').read_text());value['trust']={'existingConfigMap':'compact-restored-roots'}
(p/'sso.values.yaml').write_text(yaml.safe_dump(value))
PY
```

3. Проверить пустой target и identity, импортировать данные, очистить auth и только затем запустить. Никакой migration/deploy не выполняется до импорта. Обычные guards остаются активными:

```bash
python3 - <<'PY'
import os,sys,json
from pathlib import Path
sys.path.insert(0,'scripts/kubernetes')
import lifecycle as lc
ctx=os.environ['DR_CONTEXT'];target=os.environ['DR_TARGET_NS'];source_ns=os.environ['DR_SOURCE_NS']
p=Path(os.environ['DR_PRIVATE']);backup=Path(os.environ['DR_BACKUP'])
manifest=lc.verify_backup(backup);identity=json.loads((backup/'release.json').read_text())
assert manifest['namespace']!=target and target!=source_ns
m=lc.Manager(ctx,target,'ai-knowledge',[p/'base.values.yaml',p/'sso.values.yaml',Path(os.environ['DR_IMAGE_VALUES'])],expected_tier='home')
m.preflight()
nodes=json.loads(m.kube('get','nodes','-o','json').stdout)['items']
assert len(nodes)==1 and any(c['type']=='Ready' and c['status']=='True' for c in nodes[0]['status']['conditions'])
node_uid=nodes[0]['metadata']['uid']
assert not m.get('deployment','application') and not m.get('configmap',m.lock)
for kind in ['jobs','pods','statefulsets']:
    assert not json.loads(m.kube('get',kind,'-o','json').stdout)['items']
claims=json.loads(m.kube('get','pvc','-o','json').stdout)['items']
assert {c['metadata']['name'] for c in claims}=={'okf-data','okf-diagnostics','okf-postgres','okf-qdrant'}
source_claims=json.loads(m.run(['kubectl','--context',ctx,'-n',source_ns,'get','pvc','-o','json']).stdout)['items']
assert not ({c['metadata']['uid'] for c in claims}&{c['metadata']['uid'] for c in source_claims})
source_pvs={c['spec'].get('volumeName') for c in source_claims}-{None}
assert all(c['spec'].get('volumeName') not in source_pvs for c in claims)
old=json.loads(m.run(['kubectl','--context',ctx,'-n',source_ns,'get','secret','runtime-secret','-o','json']).stdout)
assert old['data']['APP_SECRET_KEY']!=m.get('secret','runtime-secret')['data']['APP_SECRET_KEY']
template=m.document('Deployment','application')['spec']['template']['spec']
assert identity['images']==[c['image'] for c in template['containers']]
assert identity['model_mode']==m.document('ConfigMap','runtime')['data']['MODEL_MODE']
assert identity['storage_images']==[m.document('StatefulSet',name)['spec']['template']['spec']['containers'][0]['image'] for name in ['postgres','qdrant']]
lc.validate_restore_trust(identity['trust'],m.preflight_trust(template))
m.acquire();m.chart(False);m.storage_ready();job,pod=m.toolbox()
m.exec(pod,['python','-c',"from pathlib import Path; assert not any(p.name!='lost+found' for p in Path('/data').iterdir()); from sqlalchemy import text; from app.db.session import get_engine; e=get_engine(); c=e.connect(); assert c.execute(text(\"select count(*) from information_schema.tables where table_schema='public'\")).scalar()==0; from qdrant_client import QdrantClient; from app.config import get_settings; s=get_settings(); assert not QdrantClient(url=s.qdrant_url).get_collections().collections"])
pg=json.loads(m.kube('get','pods','-l','okf-role=postgres','-o','json').stdout)['items'][0]['metadata']['name']
with (backup/'postgres.dump').open('rb') as data:
    m.exec(pg,['pg_restore','-U','okf','-d','okf_knowledge','--no-owner','--no-acl','--exit-on-error','--single-transaction'],source=data)
m.exec(pod,lc.audit_outbox_hold_command())
extract="import tarfile,sys; t=tarfile.open(fileobj=sys.stdin.buffer,mode='r|'); t.extractall(sys.argv[1],filter='data')"
for name,destination in [('data.tar','/data'),('qdrant.tar','/work/qdrant')]:
    with (backup/name).open('rb') as data:m.exec(pod,['python','-c',extract,destination],source=data)
m.exec(pod,['python','-c',"from pathlib import Path; import sys; sys.path.insert(0,'scripts'); from restore_qdrant_snapshot import restore_snapshot; f=list(Path('/work/qdrant').glob('*.snapshot')); assert len(f)==1; restore_snapshot(f[0])"])
m.exec(pod,['python','scripts/backup_totals.py','--output','/work/totals.json'])
assert json.loads(m.exec(pod,['cat','/work/totals.json']).stdout)==json.loads((backup/'totals.json').read_text())
m.exec(pod,['python','scripts/check_integrity.py','--strict','--verify-content'])
sql=['psql','-X','-U','okf','-d','okf_knowledge','-v','ON_ERROR_STOP=1','-At']
m.exec(pod,['python','scripts/invalidate_all_auth_sessions.py','--confirm-stopped-and-key-rotated'])
assert m.exec(pg,sql+['-c','SELECT count(*) FROM auth_sessions;']).stdout.decode().splitlines()==['0']
assert not m.application_pods()
node=json.loads(m.kube('get','nodes','-o','json').stdout)['items'][0]
assert node['metadata']['uid']==node_uid and any(c['type']=='Ready' and c['status']=='True' for c in node['status']['conditions'])
m.finish_job(job);m.migrate();m.start();m.release_lock()
print('Restore verified; target started with fresh key and cleared sessions')
PY
python3 scripts/kubernetes/home_probe.py --context "$DR_CONTEXT" --namespace "$DR_TARGET_NS" --marker CompactRestoredFixture
```

4. Новый browser login, открыть восстановленный документ и проверить его источник/поиск. До production переносить только подготовленный комплект и отдельно согласованные endpoints/secret policy. Для другого IdP/CA, живых моделей или недоступного исходного namespace нужны отдельные явно проверенные процедуры.

Приёмка frozen kit 10.10 выполнила операции stopped restore, новые claims,
fresh key, очистку обеих таблиц сессий и новый вход. Эта source-редакция заменяет
SQL cleanup на явный скрипт текущего main с необязательной таблицей flows;
целиком на новом target она ещё не запускалась. Скрипт проверен отдельными тестами.
Состояние интеграции указано в [отчёте](superpowers/reports/2026-10-10-kubernetes-main-integration.md).
Ошибки импорта/неизвестное состояние требуют ручного разбора и ещё одного пустого
target; одноразовый recovery нельзя превращать в автоматический resume.
