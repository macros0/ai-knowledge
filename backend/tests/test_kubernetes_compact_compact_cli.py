from pathlib import Path
import importlib.util,sys
import pytest
ROOT=Path(__file__).resolve().parent

def test_restore_cli_refuses_before_manager_or_cluster_commands(monkeypatch,capsys):
    path=ROOT.parents[1]/'scripts/kubernetes/lifecycle.py'
    monkeypatch.syspath_prepend(str(path.parent))
    spec=importlib.util.spec_from_file_location('compact_snapshot_lifecycle',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    class Forbidden:
        def __init__(self,*args,**kwargs):
            raise AssertionError('unsupported restore reached Manager')
    monkeypatch.setattr(module,'Manager',Forbidden)
    monkeypatch.setattr(sys,'argv',[str(path),'restore','--context','kind-home','--namespace','okf-fresh','--values','safe.values.yaml','--backup','complete-backup'])
    with pytest.raises(SystemExit) as result:module.main()
    assert result.value.code==2
    assert 'Automatic restore is unsupported in the compact home kit' in capsys.readouterr().err


def test_restore_library_is_also_disabled_before_backup_read(monkeypatch):
    path=ROOT.parents[1]/'scripts/kubernetes/lifecycle.py'
    spec=importlib.util.spec_from_file_location('compact_snapshot_restore_library',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def forbidden(*args,**kwargs):
        raise AssertionError('unsupported restore read backup')
    monkeypatch.setattr(module,'verify_backup',forbidden)
    manager=module.Manager.__new__(module.Manager)
    with pytest.raises(module.OperationError,match='Automatic restore is unsupported'):
        manager.restore(Path('complete-backup'))
