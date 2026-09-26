from app.services.staging import StagingStore


def test_staging_manifest_persists_parser_version(tmp_path):
    staging = StagingStore("stageversion00001", staging_root=tmp_path / "staging")

    staging.create(1, parser_version="mail-sources-v1")

    assert staging.load()["parser_version"] == "mail-sources-v1"


def test_staging_manifest_persists_source_file_hash(tmp_path):
    staging = StagingStore("stagehash000000001", staging_root=tmp_path / "staging")

    staging.create(1, parser_version="mail-sources-v1", source_file_hash="a" * 64)

    assert staging.load()["source_file_hash"] == "a" * 64
