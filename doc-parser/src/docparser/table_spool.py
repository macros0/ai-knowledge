"""Private parser-worker spool; publishes a manifest only after every table ends."""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import re

from docparser.xlsx_structure import row_bytes


class FileTableSink:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.entries = {}

    def begin_table(self, descriptor):
        if not re.fullmatch('[0-9a-f]{32}', descriptor.table_id) or len(self.entries) >= 1000:
            raise ValueError('Table identity/count safety limit exceeded')
        self.entries[descriptor.table_id] = {'descriptor': asdict(descriptor), 'quality': {'complete': False},
                                          'rows_file': descriptor.table_id + '.jsonl'}
        (self.folder / (descriptor.table_id + '.jsonl')).write_bytes(b'')

    def append_rows(self, table_id, rows):
        if table_id not in self.entries or len(rows) > 256:
            raise ValueError('Invalid table batch')
        with (self.folder / self.entries[table_id]['rows_file']).open('ab') as stream:
            for row in rows:
                stream.write(row_bytes(row) + b'\n')

    def finish_table(self, table_id, summary):
        entry = self.entries[table_id]
        digest = sha256()
        with (self.folder / entry['rows_file']).open('rb') as stream:
            for raw in stream:
                digest.update(raw)
        if digest.hexdigest() != summary.digest or not summary.complete:
            raise ValueError('Incomplete table spool')
        entry['sha256'] = digest.hexdigest()
        entry['quality'] = asdict(summary)

    def close(self):
        if not all(e['quality'].get('complete') for e in self.entries.values()):
            raise ValueError('Unfinished table spool')
        manifest = {'schema_version': 1, 'tables': list(self.entries.values())}
        (self.folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf8')
