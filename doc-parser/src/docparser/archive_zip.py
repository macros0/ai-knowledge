import stat
import zipfile

from docparser.archive_reader import ArchiveEntry, ArchiveReader, ArchiveReadError


class ZipArchiveReader(ArchiveReader):
    def __init__(self, path, limits):
        super().__init__(path, limits)
        try:
            self.archive = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise ArchiveReadError("Broken ZIP") from exc

    def _read_entries(self):
        self.members = self.archive.infolist()
        return [
            ArchiveEntry(
                str(i),
                info.filename,
                info.file_size,
                info.compress_size,
                info.is_dir(),
                stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR),
                bool(info.flag_bits & 1),
            )
            for i, info in enumerate(self.members)
        ]

    def _extract(self, selected, sink_factory, report):
        try:
            for entry in selected:
                with self.archive.open(self.members[int(entry.entry_key)]) as stream:
                    self.copy(entry, stream, sink_factory(entry), report)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise ArchiveReadError("ZIP extraction failed") from exc

    def close(self):
        self.archive.close()
