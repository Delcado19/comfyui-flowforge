"""Targeted diagnostics for Phase 5 mixed global-flow refinement."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from flowforge.layout import LayoutSettings, _node_visual_height, _node_visual_width
from flowforge.layout_engine_v2 import _score_engine_v2
from flowforge.layout_engine_v2_phase4 import apply_best_layout as apply_phase4_layout
from flowforge.layout_engine_v2_phase5 import (
    _best_diagnostic_candidate,
    _center_flow_metrics,
    _phase5_candidate_variants,
    _select_accepted_phase5_candidate,
    diagnose_phase5,
)
from flowforge.model import Workflow
from flowforge.optimizer import optimize as optimize_workflow
from flowforge.parser import parse_comfyui_workflow
from flowforge.workflow_validation import discover_workflow_files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explain why Phase 5 accepts or rejects selected workflows."
    )
    parser.add_argument("root", type=Path, help="Workflow corpus root.")
    parser.add_argument(
        "--match",
        action="append",
        default=[],
        help="Case-insensitive substring filter; may be repeated.",
    )
    parser.add_argument(
        "--optimize",
        action="store_true",
        help="Run FlowForge Optimize before the Phase 4 baseline.",
    )
    parser.add_argument(
        "--accepted-only",
        action="store_true",
        help=(
            "Print only workflows whose Phase 5 candidate passes the full gate "
            "and suppress routine INFO logging."
        ),
    )
    parser.add_argument(
        "--geometry",
        action="store_true",
        help=(
            "Compare compact baseline/proposal geometry diagnostics, including "
            "empty groups, node gaps, density, and moved top-level items."
        ),
    )
    args = parser.parse_args()

    if args.accepted_only:
        logging.disable(logging.INFO)

    files = discover_workflow_files(args.root)
    matches = [value.casefold() for value in args.match]
    selected = [
        path
        for path in files
        if not matches
        or any(value in str(path.relative_to(args.root)).casefold() for value in matches)
    ]

    if not selected:
        print("No matching workflow files found.")
        return 1

    attempted_count = 0
    accepted_count = 0
    rejected_count = 0
    skipped_count = 0

    for path in selected:
        relative = path.relative_to(args.root)
        data = json.loads(path.read_text(encoding="utf-8"))
        workflow = parse_comfyui_workflow(data)
        if args.optimize:
            workflow = optimize_workflow(workflow)
        baseline = apply_phase4_layout(workflow)
        diag = diagnose_phase5(baseline)

        if diag.attempted:
            attempted_count += 1
            if diag.accepted:
                accepted_count += 1
            else:
                rejected_count += 1
        else:
            skipped_count += 1

        if args.accepted_only and not diag.accepted:
            continue

        print(relative)
        print(
            "  mixed-graph: "
            f"groups={diag.group_vertices} "
            f"ungrouped={diag.ungrouped_vertices} "
            f"edges={diag.graph_edges} "
            f"layers={diag.layers} "
            f"candidates={diag.candidate_count}"
        )
        if diag.attempted:
            print(
                "  proposal: "
                f"variant={diag.proposal_variant}; "
                f"{diag.baseline_width:.0f}x{diag.baseline_height:.0f} -> "
                f"{diag.proposed_width:.0f}x{diag.proposed_height:.0f}; "
                f"port-crossings={diag.baseline_crossings}"
                f"->{diag.proposed_crossings}; "
                f"center-crossings={diag.baseline_center_crossings}"
                f"->{diag.proposed_center_crossings}; "
                f"port-rtl={diag.baseline_rtl}->{diag.proposed_rtl}; "
                f"center-rtl={diag.baseline_center_rtl}"
                f"->{diag.proposed_center_rtl}; "
                f"accepted={diag.accepted}; reason={diag.rejection_reason}"
            )
        else:
            print(
                "  proposal: not attempted; "
                f"reason={diag.rejection_reason}"
            )

        if args.geometry and diag.attempted:
            _print_geometry_diagnostics(baseline)

    print(
        "Summary: "
        f"selected={len(selected)} "
        f"attempted={attempted_count} "
        f"accepted={accepted_count} "
        f"rejected={rejected_count} "
        f"skipped={skipped_count}"
    )
    return 0


def _print_geometry_diagnostics(baseline: Workflow) -> None:
    baseline_score = _score_engine_v2(baseline)
    baseline_center = _center_flow_metrics(baseline)
    candidates = _phase5_candidate_variants(
        baseline,
        settings=LayoutSettings(),
        baseline_score=baseline_score,
    )
    proposal = _select_accepted_phase5_candidate(
        candidates,
        baseline_score,
        baseline_center,
    ) or _best_diagnostic_candidate(
        candidates,
        baseline_score,
        baseline_center,
    )
    if proposal is None:
        print("  geometry: no physical proposal available")
        return

    before = _node_geometry_summary(baseline)
    after = _node_geometry_summary(proposal.workflow)
    print(
        "  geometry: "
        f"node-bounds={before['width']:.0f}x{before['height']:.0f}"
        f"->{after['width']:.0f}x{after['height']:.0f}; "
        f"density={before['density']:.3f}->{after['density']:.3f}; "
        f"max-x-gap={before['max_x_gap']:.0f}->{after['max_x_gap']:.0f}; "
        f"max-y-gap={before['max_y_gap']:.0f}->{after['max_y_gap']:.0f}"
    )

    empty_groups = [
        group
        for group in baseline.groups
        if not group.nodes and len(group.bounding) >= 4
    ]
    if empty_groups:
        names = ", ".join(
            f"{group.name or f'group-{group.id}'}#{group.id}"
            for group in empty_groups
        )
        print(f"  empty-groups: {len(empty_groups)} [{names}]")
    else:
        print("  empty-groups: 0")

    before_groups = {group.id: group for group in baseline.groups}
    moved_groups: list[tuple[float, str]] = []
    for group in proposal.workflow.groups:
        previous = before_groups.get(group.id)
        if previous is None or len(group.bounding) < 2 or len(previous.bounding) < 2:
            continue
        dx = group.bounding[0] - previous.bounding[0]
        dy = group.bounding[1] - previous.bounding[1]
        if abs(dx) < 0.5 and abs(dy) < 0.5:
            continue
        label = (
            f"{group.name or f'group-{group.id}'}#{group.id} "
            f"dx={dx:+.0f} dy={dy:+.0f}"
        )
        moved_groups.append((abs(dx) + abs(dy), label))

    if moved_groups:
        print("  moved-groups:")
        for _distance, label in sorted(moved_groups, reverse=True):
            print(f"    - {label}")
    else:
        print("  moved-groups: none")

    before_ungrouped = {node.id: node for node in baseline.ungrouped_nodes}
    moved_nodes: list[tuple[float, str]] = []
    for node in proposal.workflow.ungrouped_nodes:
        previous = before_ungrouped.get(node.id)
        if previous is None:
            continue
        dx = node.x - previous.x
        dy = node.y - previous.y
        if abs(dx) < 0.5 and abs(dy) < 0.5:
            continue
        label = f"{node.type}#{node.id} dx={dx:+.0f} dy={dy:+.0f}"
        moved_nodes.append((abs(dx) + abs(dy), label))

    if moved_nodes:
        print("  moved-ungrouped:")
        for _distance, label in sorted(moved_nodes, reverse=True)[:16]:
            print(f"    - {label}")
        if len(moved_nodes) > 16:
            print(f"    - ... {len(moved_nodes) - 16} more")
    else:
        print("  moved-ungrouped: none")


def _node_geometry_summary(workflow: Workflow) -> dict[str, float]:
    rectangles = [
        (
            node.x,
            node.y,
            node.x + _node_visual_width(node),
            node.y + _node_visual_height(node),
        )
        for node in workflow.nodes.values()
    ]
    if not rectangles:
        return {
            "width": 0.0,
            "height": 0.0,
            "density": 0.0,
            "max_x_gap": 0.0,
            "max_y_gap": 0.0,
        }

    left = min(rect[0] for rect in rectangles)
    top = min(rect[1] for rect in rectangles)
    right = max(rect[2] for rect in rectangles)
    bottom = max(rect[3] for rect in rectangles)
    width = max(0.0, right - left)
    height = max(0.0, bottom - top)
    occupied_area = sum(
        max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])
        for rect in rectangles
    )
    bounds_area = width * height
    return {
        "width": width,
        "height": height,
        "density": occupied_area / bounds_area if bounds_area > 0 else 0.0,
        "max_x_gap": _largest_axis_gap((rect[0], rect[2]) for rect in rectangles),
        "max_y_gap": _largest_axis_gap((rect[1], rect[3]) for rect in rectangles),
    }


def _largest_axis_gap(intervals) -> float:
    ordered = sorted(
        (float(start), float(end))
        for start, end in intervals
        if end > start
    )
    if len(ordered) < 2:
        return 0.0

    max_gap = 0.0
    current_end = ordered[0][1]
    for start, end in ordered[1:]:
        if start > current_end:
            max_gap = max(max_gap, start - current_end)
        current_end = max(current_end, end)
    return max_gap


if __name__ == "__main__":
    raise SystemExit(main())
