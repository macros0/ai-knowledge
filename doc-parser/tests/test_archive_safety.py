import io
import stat
import zipfile
from pathlib import Path

import pytest
from docparser.archive_reader import ArchiveReadError, open_archive
from docparser.attachment_policy import TraversalLimits


@pytest.mark.parametrize(
    "name", ["../escape.xlsx", "C:/escape.xlsx", "//server/share.xlsx", "safe/file:stream"]
)
def test_unsafe_paths_rejected_before_extract(tmp_path, name):
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, b"unsafe")
    with pytest.raises(ArchiveReadError):
        with open_archive(path, format="zip", limits=TraversalLimits(), helper_path=None) as reader:
            list(reader.entries())


def test_links_rejected(tmp_path):
    path = tmp_path / "link.zip"
    entry = zipfile.ZipInfo("linked.xlsx")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(entry, "/etc/passwd")
    with pytest.raises(ArchiveReadError):
        with open_archive(path, format="zip", limits=TraversalLimits(), helper_path=None) as reader:
            list(reader.entries())


def test_declared_payload_limit_checked_before_sink(tmp_path):
    path = tmp_path / "large.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("data.txt", b"a" * 100)
    with pytest.raises(ArchiveReadError):
        with open_archive(
            path, format="zip", limits=TraversalLimits(max_payload_bytes=50), helper_path=None
        ) as reader:
            reader.extract_selected(
                frozenset(e.entry_key for e in reader.entries()), lambda _: io.BytesIO()
            )


@pytest.mark.parametrize("name", ["rar3-symlink-unix.rar", "rar5-hlink.rar"])
def test_rar_links_are_not_followed(name):
    path = Path(__file__).parent / "fixtures/archives" / name
    with pytest.raises(ArchiveReadError):
        with open_archive(path, format="rar", limits=TraversalLimits(), helper_path=None) as reader:
            list(reader.entries())
