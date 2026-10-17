"""Read one ZIP, 7z and RAR file without configuring any application database."""

import argparse
import hashlib
import io
import json
from pathlib import Path

from docparser.archive_reader import open_archive
from docparser.attachment_policy import TraversalLimits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("--helper", type=Path)
    args = parser.parse_args()
    output = []
    for path in args.archives:
        with open_archive(
            path, format=path.suffix.lower()[1:], limits=TraversalLimits(), helper_path=args.helper
        ) as reader:
            entries = [e for e in reader.entries() if not e.is_dir]
            hashes = {}
            for entry in entries:
                sink = io.BytesIO()
                reader.extract_selected(frozenset({entry.entry_key}), lambda _: sink)
                hashes[entry.display_path] = hashlib.sha256(sink.getvalue()).hexdigest()
            output.append(
                {"format": path.suffix[1:], "entries": len(entries), "content_hashes": hashes}
            )
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
