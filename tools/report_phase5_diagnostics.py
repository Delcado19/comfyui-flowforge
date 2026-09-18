"""Targeted diagnostics for Phase 5 mixed global-flow refinement."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from flowforge.layout import LayoutSettings, _node_visual_height, _node_visual_width
from flowforge.layout_engine_v2 import _port_segments, _score_engine_v2
from flowforge.layout_engine_v2_phase4 import apply_best_layout as apply_phase4_layout
from flowforge.layout_engine_v2_phase5 import (
    _best_diagnostic_candidate,
    _build_mixed_graph,
    _center_flow_metrics,
    _mixed_rejection_reason,
    _mixed_vertex_size,
    _mixed_vertex_xy,
    _phase5_candidate_variants,
    _select_accepted_phase5_candidate,
    diagnose_phase5,
)
from flowforge.model import Workflow
from flowforge.optimizer import optimize as optimize_workflow
from flowforge.parser import parse_comfyui_workflow
from flowforge.workflow_validation import discover_workflow_files


def _configure_utf8_output() -> None:
    """Keep redirected Windows output UTF-8 safe for workflow names and labels."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    _configure_utf8_output()
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
        "--attempted-only",
        action="store_true",
        help=(
            "Print only workflows for which Phase 5 built physical candidates "
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
    parser.add_argument(
        "--variants",
        action="store_true",
        help=(
            "Print every Phase 5 physical candidate with gate result and "
            "mixed-graph edge-length diagnostics."
        ),
    )
    args = parser.parse_args()

    if args.accepted_only and args.attempted_only:
        parser.error("--accepted-only and --attempted-only are mutually exclusive")

    if args.accepted_only or args.attempted_only:
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
    attempted_reasons: dict[str, int] = {}
    skipped_reasons: dict[str, int] = {}

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
                attempted_reasons[diag.rejection_reason] = (
                    attempted_reasons.get(diag.rejection_reason, 0) + 1
                )
        else:
            skipped_count += 1
            skipped_reasons[diag.rejection_reason] = (
                skipped_reasons.get(diag.rejection_reason, 0) + 1
            )

        if args.accepted_only and not diag.accepted:
            continue
        if args.attempted_only and not diag.attempted:
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
        if args.variants and diag.attempted:
            _print_variant_diagnostics(baseline)

    print(
        "Summary: "
        f"selected={len(selected)} "
        f"attempted={attempted_count} "
        f"accepted={accepted_count} "
        f"rejected={rejected_count} "
        f"skipped={skipped_count}"
    )
    _print_reason_summary("Attempted rejection reasons", attempted_reasons)
    _print_reason_summary("Skipped reasons", skipped_reasons)
    return 0


def _print_reason_summary(title: str, reasons: dict[str, int]) -> None:
    if not reasons:
        return
    values = " ".join(
        f"{reason}={count}"
        for reason, count in sorted(
            reasons.items(),
            key=lambda item: (-item[1], item[0]),
        )
    )
    print(f"{title}: {values}")


def _print_variant_diagnostics(baseline: Workflow) -> None:
    """Print every physical Phase 5 proposal against the same Phase 4 baseline."""
    baseline_score = _score_engine_v2(baseline)
    baseline_center = _center_flow_metrics(baseline)
    baseline_mixed = _mixed_geometry_summary(baseline)
    candidates = _phase5_candidate_variants(
        baseline,
        settings=LayoutSettings(),
        baseline_score=baseline_score,
    )
    print("  variants:")
    for candidate in sorted(candidates, key=lambda item: item.name):
        reason = _mixed_rejection_reason(
            candidate.score,
            baseline_score,
            candidate.center_metrics,
            baseline_center,
        )
        mixed = _mixed_geometry_summary(candidate.workflow)
        edge_delta = (
            (mixed["edge_length"] / baseline_mixed["edge_length"] - 1.0) * 100.0
            if baseline_mixed["edge_length"] > 0
            else 0.0
        )
        print(
            f"    - {candidate.name}: "
            f"{candidate.score.width:.0f}x{candidate.score.height:.0f}; "
            f"port-crossings={candidate.score.crossings}; "
            f"center-crossings={candidate.center_metrics.crossings}; "
            f"port-rtl={candidate.score.right_to_left_links}; "
            f"center-rtl={candidate.center_metrics.right_to_left_links}; "
            f"mixed-edge={mixed['edge_length']:.0f} ({edge_delta:+.1f}%); "
            f"max-mixed-edge={mixed['max_edge']:.0f}; "
            f"reason={reason}"
        )


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
    before_max_link = _max_port_link_length(baseline)
    after_max_link = _max_port_link_length(proposal.workflow)
    print(
        "  geometry: "
        f"node-bounds={before['width']:.0f}x{before['height']:.0f}"
        f"->{after['width']:.0f}x{after['height']:.0f}; "
        f"density={before['density']:.3f}->{after['density']:.3f}; "
        f"max-x-gap={before['max_x_gap']:.0f}->{after['max_x_gap']:.0f}; "
        f"max-y-gap={before['max_y_gap']:.0f}->{after['max_y_gap']:.0f}; "
        f"link-length={baseline_score.link_length:.0f}"
        f"->{proposal.score.link_length:.0f}; "
        f"max-link={before_max_link:.0f}->{after_max_link:.0f}"
    )

    before_mixed = _mixed_geometry_summary(baseline)
    after_mixed = _mixed_geometry_summary(proposal.workflow)
    print(
        "  mixed-geometry: "
        f"bounds={before_mixed['width']:.0f}x{before_mixed['height']:.0f}"
        f"->{after_mixed['width']:.0f}x{after_mixed['height']:.0f}; "
        f"density={before_mixed['density']:.3f}->{after_mixed['density']:.3f}; "
        f"max-x-gap={before_mixed['max_x_gap']:.0f}"
        f"->{after_mixed['max_x_gap']:.0f}; "
        f"max-y-gap={before_mixed['max_y_gap']:.0f}"
        f"->{after_mixed['max_y_gap']:.0f}; "
        f"edge-length={before_mixed['edge_length']:.0f}"
        f"->{after_mixed['edge_length']:.0f}; "
        f"max-edge={before_mixed['max_edge']:.0f}"
        f"->{after_mixed['max_edge']:.0f}"
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


def _mixed_geometry_summary(workflow: Workflow) -> dict[str, float]:
    """Measure cohesion of the real vertices represented by the mixed graph."""
    _specs, spec_by_vertex, _adjacency, edge_weights = _build_mixed_graph(workflow)
    groups_by_id = {group.id: group for group in workflow.groups}
    nodes_by_id = {node.id: node for node in workflow.nodes.values()}

    rectangles: dict[int, tuple[float, float, float, float]] = {}
    centers: dict[int, tuple[float, float]] = {}
    for vertex, spec in spec_by_vertex.items():
        x, y = _mixed_vertex_xy(spec, groups_by_id, nodes_by_id)
        width, height = _mixed_vertex_size(spec, groups_by_id, nodes_by_id)
        rectangles[vertex] = (x, y, x + width, y + height)
        centers[vertex] = (x + width / 2.0, y + height / 2.0)

    if not rectangles:
        return {
            "width": 0.0,
            "height": 0.0,
            "density": 0.0,
            "max_x_gap": 0.0,
            "max_y_gap": 0.0,
            "edge_length": 0.0,
            "max_edge": 0.0,
        }

    left = min(rect[0] for rect in rectangles.values())
    top = min(rect[1] for rect in rectangles.values())
    right = max(rect[2] for rect in rectangles.values())
    bottom = max(rect[3] for rect in rectangles.values())
    width = max(0.0, right - left)
    height = max(0.0, bottom - top)
    occupied_area = sum(
        max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])
        for rect in rectangles.values()
    )
    bounds_area = width * height

    weighted_edge_length = 0.0
    max_edge = 0.0
    for (source, target), weight in edge_weights.items():
        start = centers.get(source)
        end = centers.get(target)
        if start is None or end is None:
            continue
        distance = abs(end[0] - start[0]) + abs(end[1] - start[1])
        weighted_edge_length += distance * max(1, weight)
        max_edge = max(max_edge, distance)

    return {
        "width": width,
        "height": height,
        "density": occupied_area / bounds_area if bounds_area > 0 else 0.0,
        "max_x_gap": _largest_axis_gap(
            (rect[0], rect[2]) for rect in rectangles.values()
        ),
        "max_y_gap": _largest_axis_gap(
            (rect[1], rect[3]) for rect in rectangles.values()
        ),
        "edge_length": weighted_edge_length,
        "max_edge": max_edge,
    }


def _max_port_link_length(workflow: Workflow) -> float:
    """Return the longest Manhattan port-to-port link in the workflow."""
    return max(
        (
            abs(end[0] - start[0]) + abs(end[1] - start[1])
            for _source, _target, start, end in _port_segments(workflow)
        ),
        default=0.0,
    )


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
