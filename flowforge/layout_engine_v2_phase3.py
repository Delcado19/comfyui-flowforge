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

    compact = deepcopy(baseline)
    compact_settings = LayoutSettings(
        node_x_distance=settings.node_x_distance,
        node_y_distance=settings.node_y_distance,
        wrap_columns=True,
    )
    _compact_global_flow(compact, compact_settings)
    _refine_group_level_order(compact, compact_settings)
    _finalize_refinement(compact, compact_settings)
    compact_score = _score_engine_v2(compact)

    if _compact_candidate_is_better(compact_score, baseline_score):
        logger.info(
            "Compact global-flow candidate accepted: size %.0fx%.0f -> %.0fx%.0f, "
            "crossings %s -> %s, rtl %s -> %s",
            baseline_score.width,
            baseline_score.height,
            compact_score.width,
            compact_score.height,
            baseline_score.crossings,
            compact_score.crossings,
            baseline_score.right_to_left_links,
            compact_score.right_to_left_links,
        )
        return compact

    logger.info(
        "Compact global-flow candidate rejected: size %.0fx%.0f -> %.0fx%.0f, "
        "crossings %s -> %s, rtl %s -> %s",
        baseline_score.width,
        baseline_score.height,
        compact_score.width,
        compact_score.height,
        baseline_score.crossings,
        compact_score.crossings,
        baseline_score.right_to_left_links,
        compact_score.right_to_left_links,
    )
    return baseline


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
