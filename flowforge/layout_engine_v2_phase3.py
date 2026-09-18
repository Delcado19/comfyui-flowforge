"""Compact global-flow refinement for FlowForge layout engine v2.

Phase 3 keeps the Phase 2 structural result as its baseline and creates one
additional global compactness candidate. The candidate reuses the existing
boustrophedon wrapping logic for top-level group layers and linked ungrouped
flow, then re-applies weighted group ordering.

This pass is deliberately conservative: small workflows are ignored and a
compact candidate may only trade a bounded amount of crossing/RTL quality for a
substantial width reduction.
"""

from __future__ import annotations

from copy import deepcopy
import math

from .layout import (
    LayoutSettings,
    _is_decorative_node,
    _is_pinned_node,
    _node_visual_height,
    _node_visual_width,
    _position_groups_globally,
    _position_ungrouped_nodes,
)
from .layout_engine_v2 import EngineV2Score, _finalize_refinement, _score_engine_v2
from .layout_engine_v2_phase2 import (
    _quality_key,
    _refine_group_level_order,
    apply_best_layout as _apply_phase2_best_layout,
)
from .logger import setup_logger
from .model import Node, Workflow

logger = setup_logger(__name__)

COMPACT_GLOBAL_MIN_WIDTH = 7_000.0
COMPACT_GLOBAL_MIN_WIDTH_REDUCTION_RATIO = 0.18
COMPACT_GLOBAL_MAX_CROSSING_REGRESSION_RATIO = 0.015
COMPACT_GLOBAL_MAX_CROSSING_REGRESSION_MIN = 4
COMPACT_GLOBAL_MAX_RTL_REGRESSION_RATIO = 0.08
COMPACT_GLOBAL_MAX_RTL_REGRESSION_MIN = 3
COMPACT_GLOBAL_MAX_PERIMETER_GROWTH_RATIO = 1.15


def apply_best_layout(
    workflow: Workflow,
    settings: LayoutSettings | None = None,
    candidate_count: int | None = None,
) -> Workflow:
    """Run Phase 2, then try one width-aware compact global-flow candidate."""
    settings = settings or LayoutSettings()
    baseline = _apply_phase2_best_layout(workflow, settings, candidate_count)
    baseline_score = _score_engine_v2(baseline)

    if baseline_score.width < COMPACT_GLOBAL_MIN_WIDTH:
        return baseline

    compact_settings = LayoutSettings(
        node_x_distance=settings.node_x_distance,
        node_y_distance=settings.node_y_distance,
        wrap_columns=True,
    )

    structural_workflow = baseline
    structural_score = baseline_score
    structural_name = "baseline"

    global_compact = deepcopy(baseline)
    _compact_global_flow(global_compact, compact_settings)
    _refine_group_level_order(global_compact, compact_settings)
    _finalize_refinement(global_compact, compact_settings)
    global_score = _score_engine_v2(global_compact)
    if _compact_candidate_is_better(global_score, baseline_score):
        structural_workflow = global_compact
        structural_score = global_score
        structural_name = "global"

    decorative_compact = deepcopy(structural_workflow)
    if _compact_decorative_nodes_above(decorative_compact, compact_settings):
        decorative_score = _score_engine_v2(decorative_compact)
        if _decorative_candidate_is_better(decorative_score, structural_score):
            logger.info(
                "Compact %s+decorative candidate accepted: "
                "size %.0fx%.0f -> %.0fx%.0f, crossings=%s, rtl=%s",
                structural_name,
                structural_score.width,
                structural_score.height,
                decorative_score.width,
                decorative_score.height,
                decorative_score.crossings,
                decorative_score.right_to_left_links,
            )
            return decorative_compact

    if structural_workflow is not baseline:
        logger.info(
            "Compact global candidate accepted without decorative compaction: "
            "size %.0fx%.0f -> %.0fx%.0f, crossings %s -> %s, rtl %s -> %s",
            baseline_score.width,
            baseline_score.height,
            structural_score.width,
            structural_score.height,
            baseline_score.crossings,
            structural_score.crossings,
            baseline_score.right_to_left_links,
            structural_score.right_to_left_links,
        )
        return structural_workflow

    logger.info(
        "Compact candidates rejected: baseline size %.0fx%.0f, crossings=%s, rtl=%s",
        baseline_score.width,
        baseline_score.height,
        baseline_score.crossings,
        baseline_score.right_to_left_links,
    )
    return baseline


def _compact_decorative_nodes_above(
    workflow: Workflow,
    settings: LayoutSettings,
) -> bool:
    """Pack decorative annotations into rows above the non-decorative graph."""
    decorative = [
        node
        for node in workflow.nodes.values()
        if _is_decorative_node(node) and not _is_pinned_node(workflow, node)
    ]
    graph_nodes = [
        node
        for node in workflow.nodes.values()
        if not _is_decorative_node(node)
    ]
    if not decorative or not graph_nodes:
        return False

    graph_left = min(node.x for node in graph_nodes)
    graph_right = max(
        node.x + _node_visual_width(node)
        for node in graph_nodes
    )
    graph_top = min(node.y for node in graph_nodes)
    h_gap = max(settings.node_h_gap, 24.0)
    v_gap = max(settings.node_v_gap, settings.group_v_gap)
    widest_decorative = max(_node_visual_width(node) for node in decorative)
    row_width_cap = max(graph_right - graph_left, widest_decorative)

    rows: list[list[Node]] = [[]]
    row_widths = [0.0]
    row_heights = [0.0]
    for node in sorted(decorative, key=lambda item: (item.y, item.x, item.id)):
        node_width = _node_visual_width(node)
        addition = node_width if not rows[-1] else node_width + h_gap
        if rows[-1] and row_widths[-1] + addition > row_width_cap:
            rows.append([])
            row_widths.append(0.0)
            row_heights.append(0.0)
            addition = node_width
        rows[-1].append(node)
        row_widths[-1] += addition
        row_heights[-1] = max(row_heights[-1], _node_visual_height(node))

    total_height = sum(row_heights)
    total_height += v_gap * max(0, len(rows) - 1)
    current_y = graph_top - v_gap - total_height
    changed = False

    for row, row_height in zip(rows, row_heights):
        current_x = graph_left
        for node in row:
            if abs(node.x - current_x) > 1e-9 or abs(node.y - current_y) > 1e-9:
                changed = True
            node.x = current_x
            node.y = current_y
            current_x += _node_visual_width(node) + h_gap
        current_y += row_height + v_gap

    return changed


def _compact_global_flow(workflow: Workflow, settings: LayoutSettings) -> None:
    """Re-run global positioning with wrapping forced on for movable flow."""
    decorative_right = max(
        (
            node.x + _node_visual_width(node)
            for node in workflow.nodes.values()
            if _is_decorative_node(node)
        ),
        default=20.0,
    )
    start_x = max(50.0, decorative_right + settings.group_h_gap)

    _position_groups_globally(workflow, settings, start_x=start_x)
    _position_ungrouped_nodes(
        workflow,
        settings,
        start_x_floor=decorative_right + settings.group_h_gap,
    )


def _decorative_candidate_is_better(
    candidate: EngineV2Score,
    structural: EngineV2Score,
) -> bool:
    """Accept decoration-only compaction without changing graph quality."""
    if candidate.movable_overlaps != structural.movable_overlaps:
        return False
    if candidate.crossings != structural.crossings:
        return False
    if candidate.right_to_left_links != structural.right_to_left_links:
        return False
    if not math.isclose(candidate.link_length, structural.link_length, rel_tol=0.0, abs_tol=1e-6):
        return False
    if candidate.width >= structural.width:
        return False

    structural_area = structural.width * structural.height
    candidate_area = candidate.width * candidate.height
    return structural_area <= 0 or candidate_area < structural_area


def _compact_candidate_is_better(
    candidate: EngineV2Score,
    baseline: EngineV2Score,
) -> bool:
    """Allow substantial compaction with only tightly bounded quality regressions."""
    if candidate.movable_overlaps > baseline.movable_overlaps:
        return False

    if _quality_key(candidate) < _quality_key(baseline):
        return True

    if baseline.width <= 0:
        return False

    width_reduction = 1.0 - candidate.width / baseline.width
    if width_reduction < COMPACT_GLOBAL_MIN_WIDTH_REDUCTION_RATIO:
        return False

    crossing_tolerance = max(
        COMPACT_GLOBAL_MAX_CROSSING_REGRESSION_MIN,
        math.ceil(
            baseline.crossings * COMPACT_GLOBAL_MAX_CROSSING_REGRESSION_RATIO
        ),
    )
    rtl_tolerance = max(
        COMPACT_GLOBAL_MAX_RTL_REGRESSION_MIN,
        math.ceil(
            baseline.right_to_left_links * COMPACT_GLOBAL_MAX_RTL_REGRESSION_RATIO
        ),
    )
    if candidate.crossings > baseline.crossings + crossing_tolerance:
        return False
    if (
        candidate.right_to_left_links
        > baseline.right_to_left_links + rtl_tolerance
    ):
        return False

    baseline_perimeter = baseline.width + baseline.height
    candidate_perimeter = candidate.width + candidate.height
    return (
        baseline_perimeter <= 0
        or candidate_perimeter
        <= baseline_perimeter * COMPACT_GLOBAL_MAX_PERIMETER_GROWTH_RATIO
    )
