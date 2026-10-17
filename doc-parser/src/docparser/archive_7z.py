from threading import Lock

import py7zr
from py7zr.io import Py7zIO, WriterFactory

from docparser.archive_reader import ArchiveEntry, ArchiveReader, ArchiveReadError


class _Writer(Py7zIO):
    def __init__(self, reader, entry, sink, report, lock):
        self.reader, self.entry, self.sink, self.report, self.lock = (
            reader,
            entry,
            sink,
            report,
            lock,
        )
        self.count = 0

    def write(self, data):
        with self.lock:
            if self.count + len(data) > min(self.entry.size, self.reader.limits.max_payload_bytes):
                raise ArchiveReadError("Actual 7z output limit exceeded", status="skipped_size")
            self.reader.charge(self.entry, len(data), self.report)
            self.count += len(data)
            return self.sink.write(data)

    def read(self, size=None):
        return self.sink.read(-1 if size is None else size)

    def seek(self, offset, whence=0):
        result = self.sink.seek(offset, whence)
        if result > self.reader.limits.max_payload_bytes:
            raise ArchiveReadError("7z seek limit exceeded")
        return result

    def flush(self):
        self.sink.flush()

    def size(self):
        return self.count


class _Factory(WriterFactory):
    def __init__(self, reader, selected, sink_factory, report):
        self.reader = reader
        self.selected = {e.display_path: e for e in selected}
        self.sink_factory, self.report = sink_factory, report
        self.lock = Lock()
        self.writers = []

    def create(self, filename):
        if filename not in self.selected:
            raise ArchiveReadError("Unselected 7z output")
        entry = self.selected[filename]
        writer = _Writer(self.reader, entry, self.sink_factory(entry), self.report, self.lock)
        self.writers.append(writer)
        return writer


class SevenZipArchiveReader(ArchiveReader):
    def _read_entries(self):
        try:
            with py7zr.SevenZipFile(self.path, "r") as archive:
                encrypted = archive.needs_password()
                return [
                    ArchiveEntry(
                        str(i),
                        info.filename,
                        info.uncompressed,
                        info.compressed,
                        info.is_directory,
                        not (info.is_directory or info.is_file),
                        encrypted,
                    )
                    for i, info in enumerate(archive.list())
                ]
        except (py7zr.Bad7zFile, py7zr.PasswordRequired) as exc:
            raise ArchiveReadError(
                "7z cannot be listed",
                status="password_required"
                if isinstance(exc, py7zr.PasswordRequired)
                else "archive_unreadable",
            ) from exc

    def _extract(self, selected, sink_factory, report):
        factory = _Factory(self, selected, sink_factory, report)
        try:
            with py7zr.SevenZipFile(self.path, "r") as archive:
                archive.extract(targets=[e.display_path for e in selected], factory=factory)
            if any(w.count != w.entry.size for w in factory.writers) or len(factory.writers) != len(
                selected
            ):
                raise ArchiveReadError("7z member size mismatch")
        except (
            py7zr.Bad7zFile,
            py7zr.PasswordRequired,
            py7zr.UnsupportedCompressionMethodError,
        ) as exc:
            raise ArchiveReadError("7z extraction failed") from exc
