"""Archive decoding does not activate applications or write to source storage."""
from io import BytesIO

import pytest

from app.deployment.replica_backup import BackupError, BackupReader
from tests.test_replica_backup import KEY, create_backup, fixture_plan


def test_verified_reader_copies_original_bytes_and_rechecks_archive(tmp_path):
    plan, payloads = fixture_plan()
    root = tmp_path / "backup"
    create_backup(root, plan=plan, payloads=payloads)
    reader = BackupReader(root, KEY)
    for entry_id, data in payloads.items():
        target = BytesIO()
        receipt = reader.copy(entry_id, target)
        assert target.getvalue() == data
        assert receipt.size == len(data)
    assert reader.verify().status == "complete"


def test_corrupt_archive_refused_before_any_destination_write(tmp_path):
    root = tmp_path / "backup"
    create_backup(root)
    path = next((root / "objects").glob("*.bin"))
    data = path.read_bytes()
    path.write_bytes(data[:-1])
    class Forbidden:
        def write(self, _data):
            pytest.fail("No output before initial whole-archive authentication")
    with pytest.raises(BackupError):
        reader = BackupReader(root, KEY)
        reader.copy(path.stem, Forbidden())
