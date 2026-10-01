"""In-memory accounting of owned files; full scans are recovery/maintenance only."""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ReconcileResult:
    changed_files: int
    used_bytes: int
    measured_at: datetime
    concurrent_write: bool = False


class SegmentIndex:
    def __init__(self, root: Path, walk):
        self.root = root
        self._walk = walk
        self.files: dict[Path, tuple[tuple[int, int], int]] = {}
        self.used_bytes = 0
        self.generation = 0
        self.measured_at = datetime.now(timezone.utc)
        self.apply_scan(self.scan())

    def scan(self):
        result = {}
        for path in self._walk(self.root):
            stat = path.stat()
            result[path] = ((stat.st_dev, stat.st_ino), stat.st_size)
        return result

    def apply_scan(self, files):
        changed = sum(self.files.get(path) != value for path, value in files.items())
        changed += len(self.files.keys() - files.keys())
        self.files = files
        self.used_bytes = sum(size for _, size in files.values())
        self.generation += 1
        self.measured_at = datetime.now(timezone.utc)
        return ReconcileResult(changed, self.used_bytes, self.measured_at)

    def record(self, path: Path):
        previous = self.files.get(path)
        if path.exists():
            stat = path.stat()
            current = ((stat.st_dev, stat.st_ino), stat.st_size)
            self.files[path] = current
            self.used_bytes += current[1] - (previous[1] if previous else 0)
        elif previous:
            self.files.pop(path)
            self.used_bytes -= previous[1]
        self.generation += 1
        self.measured_at = datetime.now(timezone.utc)

    def remove(self, path: Path):
        previous = self.files.pop(path, None)
        if previous:
            self.used_bytes -= previous[1]
            self.generation += 1
            self.measured_at = datetime.now(timezone.utc)

    def rename(self, old: Path, new: Path):
        self.remove(old)
        self.record(new)

    def stream_paths(self, stream: str):
        parent = self.root / "events" / stream
        return sorted(path for path in self.files if path.parent == parent and path.suffix == ".jsonl")

    def size(self, path: Path):
        value = self.files.get(path)
        return value[1] if value else 0
