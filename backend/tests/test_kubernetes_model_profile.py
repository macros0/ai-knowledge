"""Synthetic vector indexes cannot silently become a production corpus."""
import importlib.util
from types import SimpleNamespace
from pathlib import Path

import pytest


@pytest.fixture
def check_profile():
    path = Path(__file__).parents[1] / "scripts/kubernetes_migrate.py"
    assert path.exists(), "Kubernetes migration profile guard is missing"
    spec = importlib.util.spec_from_file_location("kubernetes_migrate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check_profile


def test_fake_index_cannot_be_used_with_live_models(check_profile, tmp_path):
    marker = tmp_path / "profile.json"
    marker.write_text('{"mode":"stub","dimensions":1024}')
    with pytest.raises(RuntimeError, match="profile"):
        check_profile(marker, {"mode": "live", "dimensions": 1024}, nonempty=True)


def test_embedding_dimension_change_requires_new_index(check_profile, tmp_path):
    marker = tmp_path / "profile.json"
    marker.write_text('{"mode":"live","dimensions":1024}')
    with pytest.raises(RuntimeError, match="profile"):
        check_profile(marker, {"mode": "live", "dimensions": 768}, nonempty=True)


def test_unmarked_nonempty_corpus_requires_explicit_migration(check_profile, tmp_path):
    with pytest.raises(RuntimeError, match="unmarked"):
        check_profile(tmp_path / "profile.json", {"mode": "live", "dimensions": 1024}, nonempty=True)


def test_fresh_volume_and_matching_profile_are_accepted(check_profile, tmp_path):
    marker = tmp_path / "profile.json"
    check_profile(marker, {"mode": "stub", "dimensions": 1024}, nonempty=False)
    marker.write_text('{"mode":"stub","dimensions":1024}')
    check_profile(marker, {"mode": "stub", "dimensions": 1024}, nonempty=True)


def test_settings_created_empty_directories_are_not_a_corpus(tmp_path):
    path = Path(__file__).parents[1] / "scripts/kubernetes_migrate.py"
    spec = importlib.util.spec_from_file_location("kubernetes_migrate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    volume = tmp_path / "volume"
    (volume / "uploads").mkdir(parents=True)
    (volume / "staging").mkdir()
    assert not module.has_existing_files(volume)
    (volume / "uploads" / "source.txt").write_text("fixture")
    assert module.has_existing_files(volume)


@pytest.mark.parametrize("field,new_value", [("embedding_model", "model-b"), ("qdrant_collection", "other-index"),
                                            ("embedding_provider", "ollama")])
def test_same_dimensions_do_not_hide_incompatible_vector_identity(tmp_path, field, new_value):
    path = Path(__file__).parents[1] / "scripts/kubernetes_migrate.py"
    spec = importlib.util.spec_from_file_location("kubernetes_migrate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    settings = SimpleNamespace(model_mode="live", embedding_dimensions=1024, embedding_model="model-a",
                               embedding_provider="http", qdrant_collection="documents")
    original = module.profile_for(settings)
    setattr(settings, field, new_value)
    assert module.profile_for(settings) != original
