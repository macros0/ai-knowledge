import os
import shutil
import subprocess
from pathlib import Path

import rarfile

from docparser.archive_reader import ArchiveEntry, ArchiveReader, ArchiveReadError


def resolve_archive_helper(configured=None):
    candidate = configured or os.environ.get("ARCHIVE_7ZIP_PATH")
    if not candidate and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "7-Zip/7z.exe"
    if not candidate:
        candidate = shutil.which("7zz") or shutil.which("7z")
    if not candidate or not Path(candidate).is_absolute() or not Path(candidate).is_file():
        raise ArchiveReadError("RAR backend unavailable", status="archive_backend_unavailable")
    return str(Path(candidate).resolve())


class RarArchiveReader(ArchiveReader):
    def __init__(self, path, limits, helper_path):
        super().__init__(path, limits)
        self.helper_path = helper_path
        try:
            self.archive = rarfile.RarFile(path, part_only=True)
        except rarfile.Error as exc:
            raise ArchiveReadError("Broken RAR") from exc

    def _read_entries(self):
        if self.archive.needs_password() and not self.archive.infolist():
            raise ArchiveReadError("RAR headers encrypted", status="password_required")
        self.members = self.archive.infolist()
        if any(
            info.flags & (rarfile.RAR_FILE_SPLIT_BEFORE | rarfile.RAR_FILE_SPLIT_AFTER)
            for info in self.members
        ):
            raise ArchiveReadError("RAR volumes required", status="missing_volume")
        return [
            ArchiveEntry(
                str(i),
                info.filename,
                info.file_size,
                None,
                info.isdir(),
                bool(info.is_symlink() or info.file_redir),
                info.needs_password(),
            )
            for i, info in enumerate(self.members)
        ]

    def _extract(self, selected, sink_factory, report):
        helper = resolve_archive_helper(self.helper_path)
        for entry in selected:
            args = [
                helper,
                "e",
                "-so",
                "-bb0",
                "-bd",
                "-y",
                "-p-",
                "-spd",
                "--",
                str(self.path.resolve()),
                entry.display_path,
            ]
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                self.copy(entry, process.stdout, sink_factory(entry), report)
                if process.wait(timeout=5) != 0:
                    raise ArchiveReadError("RAR extraction failed")
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                process.stdout.close()

    def close(self):
        self.archive.close()
