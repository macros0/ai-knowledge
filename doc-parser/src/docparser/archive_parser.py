"""Feed selected archive members through the same document/source admission."""

import tempfile

from docparser.archive_reader import open_archive
from docparser.embedded import process_embedded


def parse_archive(path, kind, *, attachments_dir, depth, budget, context, source_id):
    blocks = []
    policy = context.processing_policy
    with open_archive(
        path,
        format=kind,
        limits=policy.traversal,
        helper_path=getattr(context, "archive_helper_path", None),
    ) as reader:
        for index, entry in enumerate(reader.entries()):
            if entry.is_dir:
                continue
            locator = "archive:" + entry.entry_key
            if not policy.is_included(context.key_for(source_id, locator)):
                # Preserve the original archive. Excluded members need not be decoded.
                blocks.extend(
                    process_embedded(
                        b"",
                        entry.display_path,
                        attachments_dir=attachments_dir,
                        index=index,
                        depth=depth + 1,
                        budget=budget,
                        context=context,
                        parent_source_id=source_id,
                        source_locator=locator,
                    )
                )
                continue
            with tempfile.TemporaryFile() as sink:
                reader.extract_selected(frozenset({entry.entry_key}), lambda _: sink)
                sink.seek(0)
                payload = sink.read(policy.traversal.max_payload_bytes + 1)
            blocks.extend(
                process_embedded(
                    payload,
                    entry.display_path,
                    attachments_dir=attachments_dir,
                    index=index,
                    depth=depth + 1,
                    budget=budget,
                    context=context,
                    parent_source_id=source_id,
                    source_locator=locator,
                )
            )
    return blocks
