"""Explicit fixture probe. Default is a typed stub, never a quality acceptance."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--profile", choices=["standard", "local_qwen"])
    parser.add_argument("--sample-size", type=int, default=5, choices=range(1, 21))
    args = parser.parse_args(argv)
    if args.live and not args.profile:
        parser.error("--live requires an explicit --profile")
    try:
        cases = json.loads(args.fixture.read_text(encoding="utf-8-sig"))
        if not isinstance(cases, list) or not cases:
            raise ValueError()
        for case in cases:
            if (
                not isinstance(case, dict)
                or not isinstance(case.get("query"), str)
                or not isinstance(case.get("blocks"), list)
                or case.get("expected_decision") not in {"allow", "reject", "uncertain"}
            ):
                raise ValueError()
            if any(
                not isinstance(block, dict) or not isinstance(block.get("content"), str)
                for block in case["blocks"]
            ):
                raise ValueError()
        families = {}
        for case in cases:
            family, split = case.get("family"), case.get("split")
            if family and split:
                if family in families and families[family] != split:
                    raise ValueError()
                families[family] = split
    except (OSError, ValueError, TypeError):
        parser.error(
            "Invalid fixture: expected a nonempty array of labelled cases with canonical blocks"
        )

    from app.config import Settings
    from app.services.source_assessment.config import (
        resolve_assessment_config,
        resolve_assessment_connection,
    )
    from app.services.source_assessment.factory import get_assessor
    from app.services.source_assessment.service import assess_sources
    from app.services.source_assessment.types import ItemDecision, ProviderAssessment

    settings = Settings(
        source_assessment_sample_size=args.sample_size,
        **({"source_assessment_llm_profile": args.profile} if args.live else {}),
    )
    config = resolve_assessment_config(settings)
    report = {
        "live": args.live,
        "acceptance": "not_measured",
        "sample_size": args.sample_size,
        "completion_count": 0,
        "false_reject": 0,
        "false_allow": 0,
        "uncertain": 0,
        "unavailable": 0,
        "cases": [],
    }
    for number, case in enumerate(cases, 1):

        class Stub:
            def assess(self, request, **kwargs):
                values = case.get("provider_items")
                if not isinstance(values, list):
                    raise ValueError("Missing typed stub response")
                return ProviderAssessment(
                    tuple(ItemDecision(**item) for item in values), completion_count=0
                )

        adapter = (
            get_assessor(config, resolve_assessment_connection(settings)) if args.live else Stub()
        )
        outcome = assess_sources(
            case["query"],
            case.get("locale", "ru"),
            case["blocks"],
            enabled=True,
            config=config,
            assessor=adapter,
            deadline=time.monotonic() + config.timeout_seconds,
            cancel=None,
        )
        false_reject = outcome.decision == "reject" and (
            case.get("must_not_reject") or case["expected_decision"] != "reject"
        )
        false_allow = outcome.decision == "allow" and case["expected_decision"] == "reject"
        report["false_reject"] += int(bool(false_reject))
        report["false_allow"] += int(false_allow)
        report["uncertain"] += int(outcome.decision == "uncertain")
        report["unavailable"] += int(outcome.status == "unavailable")
        if outcome.completion_count is not None:
            report["completion_count"] += outcome.completion_count
        report["cases"].append(
            {
                "case": number,
                **outcome.public().model_dump(mode="json"),
                "matches_expected": outcome.decision == case["expected_decision"],
                "false_reject": bool(false_reject),
                "false_allow": false_allow,
                "input_tokens": outcome.input_tokens,
                "output_tokens": outcome.output_tokens,
                "completion_count": outcome.completion_count,
                "queue_ms": outcome.queue_ms,
            }
        )
    # No text, source identifiers, connection or queries in the aggregate report.
    print(json.dumps(report, ensure_ascii=False))
    return 1 if report["false_reject"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
