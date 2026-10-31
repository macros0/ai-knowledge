import importlib.util
import json
from pathlib import Path

import pytest

from tests.test_artifact_storage_migration import corpus as corpus, migration, backup


def test_cli_plan_default_and_mutation_requires_apply(corpus):
    from scripts.migrate_artifact_storage import main
    task = migration(corpus)
    root = corpus[1]
    source, target = root / "source.json", root / "target.json"
    source.write_text(json.dumps(task.source))
    target.write_text(json.dumps(task.target))
    args = ["--source-manifest", str(source), "--target-manifest", str(target), "--plan", str(task.path)]
    assert main(args, settings=corpus[0], stores=task.stores) == 0
    assert task.path.exists()
    assert main([*args, "--phase", "copy"], settings=corpus[0], stores=task.stores) == 2
    assert not list((task.stores["target"].root / "objects").iterdir())
    proof = backup(task, root)
    assert main([*args, "--phase", "copy", "--apply", "--writers-stopped", "--backup-manifest", str(proof)],
                settings=corpus[0], stores=task.stores) == 0


def lifecycle(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/lifecycle.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("artifact_migration_lifecycle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kubernetes_migration_is_explicit_and_never_restarts_old_application(monkeypatch):
    module = lifecycle(monkeypatch)
    manager = module.Manager.__new__(module.Manager)
    calls = []
    manager.preflight = lambda: calls.append("preflight")
    manager.acquire = lambda: calls.append("lock")
    manager.stop = lambda: calls.append("stop")
    manager.job = lambda command: calls.append(command) or "migration-job"
    manager.get = lambda *_a: {"status": {"succeeded": 1}}
    manager.finish_job = lambda name: calls.append(("finish", name))
    manager.release_lock = lambda: calls.append("unlock")
    manager.start = lambda: pytest.fail("migration must not restart old storage configuration")
    manager.migrate_artifacts("/private/source.json", "/private/target.json", "/transfer/plan.json", "copy", "/backup/evidence.json")
    assert calls[:3] == ["preflight", "lock", "stop"]
    command = calls[3]
    assert command[:2] == ["python", "scripts/migrate_artifact_storage.py"]
    assert "--writers-stopped" in command and "--apply" in command
    assert calls[-1] == "unlock"


def test_reindex_refuses_migration_before_touching_qdrant(corpus, monkeypatch):
    from scripts import reindex
    import sys
    task = migration(corpus)
    task.copy(apply=True, writers_stopped=True, backup=backup(task, corpus[1]))
    monkeypatch.setattr(sys, "argv", ["reindex.py"])
    monkeypatch.setattr(reindex, "get_settings", lambda: corpus[0])
    monkeypatch.setattr(reindex, "VectorStore", lambda: pytest.fail("Qdrant touched during storage migration"))
    with pytest.raises(ValueError, match="maintenance"):
        reindex.main()
