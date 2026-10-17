"""Read-only supervised attachment assessment; no database, pipeline or LLM."""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def inspect_file(path, *, helper=None):
    from tempfile import TemporaryDirectory
    from app.services.parser_supervisor import inspect_attachments_supervised
    from docparser.attachment_policy import AssessmentLimits, TraversalLimits
    from docparser.attachment_assessment import recommend_scope

    limits = AssessmentLimits()
    with TemporaryDirectory(prefix="scope-inspection-") as folder:
        inventory = inspect_attachments_supervised(
            path,
            path.name,
            attachments_dir=folder,
            limits=limits,
            traversal=TraversalLimits(),
            archive_helper_path=helper,
        )
    return {
        "inventory": inventory.to_dict(),
        "recommendation": asdict(recommend_scope(inventory, limits)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--helper")
    args = parser.parse_args()
    source = args.file.resolve(strict=True)
    if args.output.resolve() == source:
        parser.error("Report must not overwrite the source")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = inspect_file(source, helper=args.helper)
    if hashlib.sha256(source.read_bytes()).hexdigest() != before:
        raise RuntimeError("Source changed during inspection")
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
