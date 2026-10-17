from dataclasses import replace

import pytest
from docparser.attachment_assessment import recommend_scope
from docparser.attachment_policy import (
    AssessmentLimits,
    AttachmentInventory,
    AttachmentNode,
    make_source_key,
)


def node(
    index, *, parent_key="root", rows=0, cells=0, assessment="small", kind="xlsx", complete=True
):
    return AttachmentNode(
        source_key=f"key-{index}",
        parent_key=parent_key,
        locator=f"part-{index}",
        display_name="same.xlsx",
        kind=kind,
        metrics={"rows": rows, "cells": cells, "text_bytes": 0, "file_bytes": 0},
        assessment=assessment,
        children_complete=complete,
    )


def inventory(*nodes):
    return AttachmentInventory(source_file_hash="a" * 64, nodes=list(nodes))


@pytest.mark.parametrize(
    "rows,cells,selected", [(499, 19999, True), (500, 0, False), (0, 20000, False)]
)
def test_individual_boundaries(rows, cells, selected):
    result = recommend_scope(inventory(node(1, rows=rows, cells=cells)), AssessmentLimits())
    assert result.decisions[0].selected is selected
    assert result.requires_review is (not selected)


def test_aggregate_is_deterministic_and_stops_strictly_below_threshold():
    entries = [node(i, rows=250) for i in range(5)]
    first = recommend_scope(inventory(*entries), AssessmentLimits())
    again = recommend_scope(inventory(*reversed(entries)), AssessmentLimits())
    excluded = {d.source_key for d in first.decisions if not d.selected}
    assert excluded == {"key-0", "key-1"}
    assert excluded == {d.source_key for d in again.decisions if not d.selected}
    assert sum(250 for d in first.decisions if d.selected) == 750
    assert first.requires_review


def test_unknown_and_unsupported_are_distinct():
    unknown = node(1, assessment="unknown", complete=False)
    unsupported = replace(node(2), supported=False, assessment="unsupported")
    result = recommend_scope(inventory(unknown, unsupported), AssessmentLimits())
    decisions = {d.source_key: d for d in result.decisions}
    assert decisions["key-1"].reason == "unknown"
    assert decisions["key-2"].reason == "unsupported"
    assert not any(d.selected for d in result.decisions)
    assert result.requires_review


def test_container_does_not_double_count_child_workload():
    parent = node(0, kind="archive")
    parent.metrics["file_bytes"] = 30 * 1024 * 1024
    result = recommend_scope(
        inventory(parent, node(1, parent_key=parent.source_key, rows=10)), AssessmentLimits()
    )
    assert all(d.selected for d in result.decisions)
    assert not result.requires_review


def test_heavy_child_does_not_turn_off_mail_body_or_sibling():
    parent = node(0, kind="mail")
    result = recommend_scope(
        inventory(
            parent, node(1, parent_key="key-0", rows=500), node(2, parent_key="key-0", rows=3)
        ),
        AssessmentLimits(),
    )
    assert [d.selected for d in result.decisions] == [True, False, True]


def test_source_key_is_bound_to_source_and_physical_occurrence():
    a = make_source_key("a" * 64, ("word/document.xml:rId1:0",))
    assert a == make_source_key("a" * 64, ("word/document.xml:rId1:0",))
    assert a != make_source_key("b" * 64, ("word/document.xml:rId1:0",))
    assert a != make_source_key("a" * 64, ("word/document.xml:rId1:1",))
