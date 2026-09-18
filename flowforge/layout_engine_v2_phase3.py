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
from .model import Workflow

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

    candidates: list[tuple[str, Workflow, EngineV2Score]] = []

    decorative_compact = deepcopy(baseline)
    if _compact_decorative_nodes_above(decorative_compact, compact_settings):
        candidates.append(
            (
                "decorative",
                decorative_compact,
                _score_engine_v2(decorative_compact),
            )
        )

    global_compact = deepcopy(baseline)
    _compact_global_flow(global_compact, compact_settings)
    _refine_group_level_order(global_compact, compact_settings)
    _finalize_refinement(global_compact, compact_settings)
    _compact_decorative_nodes_above(global_compact, compact_settings)
    candidates.append(
        ("global", global_compact, _score_engine_v2(global_compact))
    )

    best_workflow = baseline
    best_score = baseline_score
    best_name = "baseline"
    for name, candidate, candidate_score in candidates:
        if _compact_candidate_is_better(candidate_score, best_score):
            best_workflow = candidate
            best_score = candidate_score
            best_name = name

    if best_workflow is not baseline:
        logger.info(
            "Compact %s candidate accepted: size %.0fx%.0f -> %.0fx%.0f, "
            "crossings %s -> %s, rtl %s -> %s",
            best_name,
            baseline_score.width,
            baseline_score.height,
            best_score.width,
            best_score.height,
            baseline_score.crossings,
            best_score.crossings,
            baseline_score.right_to_left_links,
            best_score.right_to_left_links,
        )
        return best_workflow

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
    """Move wide decorative annotations above the graph instead of beside it."""
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
    graph_top = min(node.y for node in graph_nodes)
    gap = max(settings.node_v_gap, settings.group_v_gap)

    ordered = sorted(decorative, key=lambda node: (node.y, node.x, node.id))
    total_height = sum(_node_visual_height(node) for node in ordered)
    total_height += gap * max(0, len(ordered) - 1)

    current_y = graph_top - gap - total_height
    changed = False
    for node in ordered:
        if abs(node.x - graph_left) > 1e-9 or abs(node.y - current_y) > 1e-9:
            changed = True
        node.x = graph_left
        node.y = current_y
        current_y += _node_visual_height(node) + gap
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
