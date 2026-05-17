"""Tests for read-only layout quality reporting."""

from __future__ import annotations

import json
from dataclasses import replace

from flowforge.layout_quality import (
    build_quality_summary,
    build_workflow_quality_report,
    summarize_quality_text,
)


WORKFLOW = {
    "nodes": [
        {"id": 1, "type": "SourceA", "pos": [0, 200], "size": [100, 60], "outputs": [{"links": [1]}]},
        {"id": 2, "type": "SourceB", "pos": [0, 0], "size": [100, 60], "outputs": [{"links": [2]}]},
        {"id": 3, "type": "TargetA", "pos": [400, 0], "size": [100, 60], "inputs": [{"link": 1}]},
        {"id": 4, "type": "TargetB", "pos": [400, 200], "size": [100, 60], "inputs": [{"link": 2}]},
    ],
    "links": [
        [1, 1, 0, 3, 0, "IMAGE"],
        [2, 2, 0, 4, 0, "IMAGE"],
    ],
    "groups": [{"id": 10, "title": "Main", "bounding": [-20, -20, 560, 340]}],
    "last_node_id": 4,
    "last_link_id": 2,
    "version": 0.4,
}


def test_build_workflow_quality_report_counts_geometry_and_layout_metrics():
    report = build_workflow_quality_report(WORKFLOW, "crossing.json")

    assert report.path == "crossing.json"
    assert report.node_count == 4
    assert report.link_count == 2
    assert report.laid_out_node_count == 4
    assert report.laid_out_link_count == 2
    assert report.group_count == 1
    assert report.original.width == 560
    assert report.original.link_crossings == 1
    assert report.moved_nodes > 0
    assert sum(report.laid_out_crossing_categories.values()) == report.laid_out.link_crossings
    assert sum(report.laid_out_right_to_left_categories.values()) == report.laid_out.right_to_left_links
    assert report.layout_candidate_count is not None
    assert report.layout_score is not None


def test_build_workflow_quality_report_can_optimize_before_layout():
    workflow = {
        "nodes": [
            {"id": 1, "type": "UNETLoader", "pos": [0, 0], "size": [200, 60], "outputs": [{"links": [10, 11, 12]}]},
            {"id": 2, "type": "KSampler", "pos": [900, 0], "size": [200, 100], "inputs": [{"link": 10}]},
            {"id": 3, "type": "KSampler", "pos": [1100, 300], "size": [200, 100], "inputs": [{"link": 11}]},
            {"id": 4, "type": "KSampler", "pos": [1300, 600], "size": [200, 100], "inputs": [{"link": 12}]},
        ],
        "links": [
            [10, 1, 0, 2, 0, "MODEL"],
            [11, 1, 0, 3, 0, "MODEL"],
            [12, 1, 0, 4, 0, "MODEL"],
        ],
        "groups": [],
        "last_node_id": 4,
        "last_link_id": 12,
    }

    report = build_workflow_quality_report(workflow, "fanout.json", optimize_first=True)

    assert report.node_count == 4
    assert report.laid_out_node_count > report.node_count
    assert report.laid_out_link_count > report.link_count
    assert report.moved_nodes >= 4
    assert report.layout_candidate_count is not None


def test_build_quality_summary_skips_non_workflow_files(tmp_path):
    (tmp_path / "workflow.json").write_text(json.dumps(WORKFLOW), encoding="utf-8")
    (tmp_path / "metadata.json").write_text(json.dumps({"not": "workflow"}), encoding="utf-8")
    (tmp_path / "generated_layouted.json").write_text(json.dumps(WORKFLOW), encoding="utf-8")

    summary = build_quality_summary(tmp_path)

    assert summary.ok
    assert summary.discovered_files == 2
    assert summary.checked_workflows == 1
    assert summary.skipped_files == 1
    assert summary.reports[0].path == "workflow.json"


def test_summarize_quality_text_includes_aggregate_metrics(tmp_path):
    (tmp_path / "workflow.json").write_text(json.dumps(WORKFLOW), encoding="utf-8")
    summary = build_quality_summary(tmp_path)

    text = summarize_quality_text(summary)

    assert "Checked UI workflows: 1" in text
    assert "Optimization: disabled" in text
    assert "Straight-line crossings:" in text
    assert "Largest laid-out workflows" in text


def test_summarize_quality_text_mentions_optimizer_mode(tmp_path):
    (tmp_path / "workflow.json").write_text(json.dumps(WORKFLOW), encoding="utf-8")
    summary = build_quality_summary(tmp_path, optimize_first=True)

    text = summarize_quality_text(summary)

    assert "Optimization: enabled" in text


def test_summarize_quality_text_includes_category_breakdowns(tmp_path):
    (tmp_path / "workflow.json").write_text(json.dumps(WORKFLOW), encoding="utf-8")
    summary = build_quality_summary(tmp_path)
    summary.reports[0] = replace(
        summary.reports[0],
        laid_out_crossing_categories={"within_group x group->group": 2},
        laid_out_right_to_left_categories={"within_group": 1},
    )

    text = summarize_quality_text(summary)

    assert "Top laid-out crossing categories:" in text
    assert "- within_group x group->group: 2" in text
    assert "Top laid-out right-to-left categories:" in text
    assert "- within_group: 1" in text
