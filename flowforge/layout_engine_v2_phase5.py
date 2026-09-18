"""Mixed group/ungrouped global-flow refinement for layout engine v2.

Phase 5 is an isolated candidate on top of the complete Phase 4 result. It
builds one directed dependency graph whose real vertices are whole groups and
eligible ungrouped nodes. Group internals remain unchanged: a group is moved as
one rectangle together with all of its member nodes.

Pinned geometry remains a hard constraint. Workflows with pinned groups or
pinned ungrouped nodes are skipped entirely by this first Phase 5 experiment.
Authored positive-size groups with no assigned member nodes are also skipped:
they are real canvas geometry but are not represented by the mixed dependency
graph, so allowing the normal overlap finalizer to relocate them can fragment
the visible workflow. Established local-placement special cases remain outside
the mixed graph and are re-applied by the normal refinement finalizer.

Physical placement keeps dependency layers monotonic from left to right. This
is intentionally separate from graph ordering: row wrapping is not used because
reversing alternate rows creates right-to-left links and long cross-row wires.
Phase 5 evaluates both weighted graph order and baseline-stable order, each with
conservative vertical-gap profiles derived from the existing layout settings.

The acceptance gate is deliberately strict: no additional movable overlaps,
crossings, or right-to-left links in either the port-aware engine metric or the
node-center corpus metric; at least 8% workflow-width reduction; and no workflow
area growth.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import TypeAlias

from .layout import (
    LayoutSettings,
    _group_by_node_id,
    _group_flow_adjacency,
    _group_has_fixed_geometry,
    _has_positive_bounding,
    _is_pinned_node,
    _move_group_geometry,
    _node_visual_height,
    _node_visual_width,
)
from .layout_engine_v2 import (
    EngineV2Score,
    _assign_scc_longest_path_layers,
    _finalize_refinement,
    _score_engine_v2,
    _segment_crossing_count,
)
from .layout_engine_v2_phase4 import (
    _eligible_ungrouped_nodes,
    apply_best_layout as _apply_phase4_best_layout,
)
from .logger import setup_logger
from .model import Group, Node, Workflow

logger = setup_logger(__name__)

MIXED_GLOBAL_MIN_WIDTH = 5_000.0
MIXED_GLOBAL_MIN_GROUPS = 2
MIXED_GLOBAL_MIN_UNGROUPED = 3
MIXED_GLOBAL_MIN_ACCEPTED_WIDTH_REDUCTION_RATIO = 0.08
MIXED_GLOBAL_MAX_AREA_RATIO = 1.0
MIXED_GLOBAL_MIN_VERTICAL_GAP = 12.0
MIXED_GLOBAL_COMPACT_GAP_RATIO = 0.5
MIXED_SWEEP_ROUNDS = 6
MIXED_TRANSPOSE_PASSES = 6

MixedSpec: TypeAlias = tuple[str, int]
MixedDummy: TypeAlias = tuple[str, int, int, int]
MixedOrderVertex: TypeAlias = int | MixedDummy
WeightedMixedEdge: TypeAlias = tuple[MixedOrderVertex, MixedOrderVertex, int]


@dataclass(frozen=True)
class Phase5Diagnostics:
    """Explain whether mixed global-flow placement can affect one workflow."""

    group_vertices: int
    ungrouped_vertices: int
    graph_edges: int
    layers: int
    candidate_count: int
    proposal_variant: str | None
    attempted: bool
    accepted: bool
    rejection_reason: str
    baseline_width: float
    baseline_height: float
    proposed_width: float | None
    proposed_height: float | None
    baseline_crossings: int
    proposed_crossings: int | None
    baseline_rtl: int
    proposed_rtl: int | None
    baseline_center_crossings: int
    proposed_center_crossings: int | None
    baseline_center_rtl: int
    proposed_center_rtl: int | None


@dataclass(frozen=True)
class _CenterFlowMetrics:
    """Corpus-compatible node-center crossing and RTL metrics."""

    crossings: int
    right_to_left_links: int


@dataclass
class _Phase5Candidate:
    """One scored physical realization of the shared mixed dependency graph."""

    name: str
    workflow: Workflow
    score: EngineV2Score
    center_metrics: _CenterFlowMetrics


def apply_best_layout(
    workflow: Workflow,
    settings: LayoutSettings | None = None,
    candidate_count: int | None = None,
) -> Workflow:
    """Run Phase 4, then evaluate conservative mixed-flow placement variants."""
    settings = settings or LayoutSettings()
    baseline = _apply_phase4_best_layout(workflow, settings, candidate_count)
    baseline_score = _score_engine_v2(baseline)
    baseline_center = _center_flow_metrics(baseline)
    if baseline_score.width < MIXED_GLOBAL_MIN_WIDTH:
        return baseline

    candidates = _phase5_candidate_variants(baseline, settings, baseline_score)
    selected = _select_accepted_phase5_candidate(
        candidates,
        baseline_score,
        baseline_center,
    )
    if selected is not None:
        logger.info(
            "Phase 5 mixed global-flow accepted (%s): "
            "size %.0fx%.0f -> %.0fx%.0f, port crossings %s -> %s, "
            "center crossings %s -> %s, port rtl %s -> %s, center rtl %s -> %s",
            selected.name,
            baseline_score.width,
            baseline_score.height,
            selected.score.width,
            selected.score.height,
            baseline_score.crossings,
            selected.score.crossings,
            baseline_center.crossings,
            selected.center_metrics.crossings,
            baseline_score.right_to_left_links,
            selected.score.right_to_left_links,
            baseline_center.right_to_left_links,
            selected.center_metrics.right_to_left_links,
        )
        return selected.workflow

    rejected = _best_diagnostic_candidate(
        candidates,
        baseline_score,
        baseline_center,
    )
    if rejected is not None:
        logger.info(
            "Phase 5 mixed global-flow rejected (%s): "
            "size %.0fx%.0f -> %.0fx%.0f, port crossings %s -> %s, "
            "center crossings %s -> %s, port rtl %s -> %s, center rtl %s -> %s; "
            "reason=%s",
            rejected.name,
            baseline_score.width,
            baseline_score.height,
            rejected.score.width,
            rejected.score.height,
            baseline_score.crossings,
            rejected.score.crossings,
            baseline_center.crossings,
            rejected.center_metrics.crossings,
            baseline_score.right_to_left_links,
            rejected.score.right_to_left_links,
            baseline_center.right_to_left_links,
            rejected.center_metrics.right_to_left_links,
            _mixed_rejection_reason(
                rejected.score,
                baseline_score,
                rejected.center_metrics,
                baseline_center,
            ),
        )
    return baseline


def diagnose_phase5(
    workflow: Workflow,
    settings: LayoutSettings | None = None,
) -> Phase5Diagnostics:
    """Return a read-only explanation of the Phase 5 decision."""
    settings = settings or LayoutSettings()
    baseline_score = _score_engine_v2(workflow)
    baseline_center = _center_flow_metrics(workflow)
    specs, spec_by_vertex, adjacency, edge_weights = _build_mixed_graph(workflow)
    group_count = sum(1 for kind, _value in specs if kind == "group")
    ungrouped_count = sum(1 for kind, _value in specs if kind == "node")
    layers_map = (
        _assign_scc_longest_path_layers(set(spec_by_vertex), adjacency)
        if spec_by_vertex
        else {}
    )
    layer_count = len(set(layers_map.values()))

    precheck_reason = _mixed_precheck_reason(
        workflow,
        baseline_score,
        group_count,
        ungrouped_count,
        edge_weights,
        layer_count,
    )
    if precheck_reason is not None:
        return Phase5Diagnostics(
            group_vertices=group_count,
            ungrouped_vertices=ungrouped_count,
            graph_edges=len(edge_weights),
            layers=layer_count,
            candidate_count=0,
            proposal_variant=None,
            attempted=False,
            accepted=False,
            rejection_reason=precheck_reason,
            baseline_width=baseline_score.width,
            baseline_height=baseline_score.height,
            proposed_width=None,
            proposed_height=None,
            baseline_crossings=baseline_score.crossings,
            proposed_crossings=None,
            baseline_rtl=baseline_score.right_to_left_links,
            proposed_rtl=None,
            baseline_center_crossings=baseline_center.crossings,
            proposed_center_crossings=None,
            baseline_center_rtl=baseline_center.right_to_left_links,
            proposed_center_rtl=None,
        )

    candidates = _phase5_candidate_variants(workflow, settings, baseline_score)
    selected = _select_accepted_phase5_candidate(
        candidates,
        baseline_score,
        baseline_center,
    )
    proposal = selected or _best_diagnostic_candidate(
        candidates,
        baseline_score,
        baseline_center,
    )
    if proposal is None:
        return Phase5Diagnostics(
            group_vertices=group_count,
            ungrouped_vertices=ungrouped_count,
            graph_edges=len(edge_weights),
            layers=layer_count,
            candidate_count=0,
            proposal_variant=None,
            attempted=False,
            accepted=False,
            rejection_reason="no_geometry_change",
            baseline_width=baseline_score.width,
            baseline_height=baseline_score.height,
            proposed_width=None,
            proposed_height=None,
            baseline_crossings=baseline_score.crossings,
            proposed_crossings=None,
            baseline_rtl=baseline_score.right_to_left_links,
            proposed_rtl=None,
            baseline_center_crossings=baseline_center.crossings,
            proposed_center_crossings=None,
            baseline_center_rtl=baseline_center.right_to_left_links,
            proposed_center_rtl=None,
        )

    accepted = selected is not None
    return Phase5Diagnostics(
        group_vertices=group_count,
        ungrouped_vertices=ungrouped_count,
        graph_edges=len(edge_weights),
        layers=layer_count,
        candidate_count=len(candidates),
        proposal_variant=proposal.name,
        attempted=True,
        accepted=accepted,
        rejection_reason=(
            "accepted"
            if accepted
            else _mixed_rejection_reason(
                proposal.score,
                baseline_score,
                proposal.center_metrics,
                baseline_center,
            )
        ),
        baseline_width=baseline_score.width,
        baseline_height=baseline_score.height,
        proposed_width=proposal.score.width,
        proposed_height=proposal.score.height,
        baseline_crossings=baseline_score.crossings,
        proposed_crossings=proposal.score.crossings,
        baseline_rtl=baseline_score.right_to_left_links,
        proposed_rtl=proposal.score.right_to_left_links,
        baseline_center_crossings=baseline_center.crossings,
        proposed_center_crossings=proposal.center_metrics.crossings,
        baseline_center_rtl=baseline_center.right_to_left_links,
        proposed_center_rtl=proposal.center_metrics.right_to_left_links,
    )


def _phase5_candidate_variants(
    baseline: Workflow,
    settings: LayoutSettings,
    baseline_score: EngineV2Score,
) -> list[_Phase5Candidate]:
    """Build scored physical variants without changing the mixed dependency graph."""
    candidates: list[_Phase5Candidate] = []
    for order_mode in ("weighted", "stable"):
        for horizontal_mode in ("layer", "compact", "anchored"):
            for vertical_gap in _phase5_vertical_gaps(settings):
                candidate = deepcopy(baseline)
                if not _place_mixed_global_flow(
                    candidate,
                    settings,
                    baseline_score.width,
                    order_mode=order_mode,
                    horizontal_mode=horizontal_mode,
                    vertical_gap=vertical_gap,
                ):
                    continue
                _finalize_refinement(candidate, settings)
                if horizontal_mode == "layer":
                    variant_name = f"{order_mode}-gap-{vertical_gap:g}"
                elif horizontal_mode == "compact":
                    variant_name = f"{order_mode}-compactx-gap-{vertical_gap:g}"
                else:
                    variant_name = f"{order_mode}-anchoredx-gap-{vertical_gap:g}"
                candidates.append(
                    _Phase5Candidate(
                        name=variant_name,
                        workflow=candidate,
                        score=_score_engine_v2(candidate),
                        center_metrics=_center_flow_metrics(candidate),
                    )
                )
    return candidates


def _phase5_baseline_y_candidate_variants(
    baseline: Workflow,
    settings: LayoutSettings,
    baseline_score: EngineV2Score,
) -> list[_Phase5Candidate]:
    """Build diagnostic Anchored-X candidates that preserve Phase 4 Y positions.

    These variants are intentionally excluded from normal Phase 5 selection
    until visual review confirms that baseline-Y anchoring improves cohesion.
    """
    candidates: list[_Phase5Candidate] = []
    for order_mode in ("weighted", "stable"):
        candidate = deepcopy(baseline)
        if not _place_mixed_global_flow(
            candidate,
            settings,
            baseline_score.width,
            order_mode=order_mode,
            horizontal_mode="anchored",
            vertical_mode="baseline",
        ):
            continue
        _finalize_refinement(candidate, settings)
        candidates.append(
            _Phase5Candidate(
                name=f"{order_mode}-anchoredxy",
                workflow=candidate,
                score=_score_engine_v2(candidate),
                center_metrics=_center_flow_metrics(candidate),
            )
        )
    return candidates


def _phase5_layer_anchor_candidate_variants(
    baseline: Workflow,
    settings: LayoutSettings,
    baseline_score: EngineV2Score,
) -> list[_Phase5Candidate]:
    """Build diagnostic Anchored-X candidates with layer-level Y anchoring."""
    candidates: list[_Phase5Candidate] = []
    for order_mode in ("weighted", "stable"):
        for vertical_gap in _phase5_vertical_gaps(settings):
            candidate = deepcopy(baseline)
            if not _place_mixed_global_flow(
                candidate,
                settings,
                baseline_score.width,
                order_mode=order_mode,
                horizontal_mode="anchored",
                vertical_gap=vertical_gap,
                vertical_mode="layer_anchor",
            ):
                continue
            _finalize_refinement(candidate, settings)
            candidates.append(
                _Phase5Candidate(
                    name=f"{order_mode}-anchoredx-yband-gap-{vertical_gap:g}",
                    workflow=candidate,
                    score=_score_engine_v2(candidate),
                    center_metrics=_center_flow_metrics(candidate),
                )
            )
    return candidates


def _phase5_compressed_yband_candidate_variants(
    baseline: Workflow,
    settings: LayoutSettings,
    baseline_score: EngineV2Score,
) -> list[_Phase5Candidate]:
    """Build diagnostic partial Y-band anchors at compact and minimum spacing."""
    candidates: list[_Phase5Candidate] = []
    compact_gap = _phase5_vertical_gaps(settings)[-1]
    vertical_gaps = [compact_gap]
    if not math.isclose(compact_gap, MIXED_GLOBAL_MIN_VERTICAL_GAP):
        vertical_gaps.append(MIXED_GLOBAL_MIN_VERTICAL_GAP)

    for order_mode in ("weighted", "stable"):
        for vertical_gap in vertical_gaps:
            anchor_strengths = [0.25, 0.5, 0.75]
            if math.isclose(vertical_gap, MIXED_GLOBAL_MIN_VERTICAL_GAP):
                anchor_strengths = [0.10, 0.15, 0.20, *anchor_strengths]
            for anchor_strength in anchor_strengths:
                candidate = deepcopy(baseline)
                if not _place_mixed_global_flow(
                    candidate,
                    settings,
                    baseline_score.width,
                    order_mode=order_mode,
                    horizontal_mode="anchored",
                    vertical_gap=vertical_gap,
                    vertical_mode="layer_anchor",
                    vertical_anchor_strength=anchor_strength,
                ):
                    continue
                _finalize_refinement(candidate, settings)
                percent = int(round(anchor_strength * 100))
                candidates.append(
                    _Phase5Candidate(
                        name=(
                            f"{order_mode}-anchoredx-yband{percent}"
                            f"-gap-{vertical_gap:g}"
                        ),
                        workflow=candidate,
                        score=_score_engine_v2(candidate),
                        center_metrics=_center_flow_metrics(candidate),
                    )
                )
    return candidates


def _phase5_vertical_gaps(settings: LayoutSettings) -> list[float]:
    """Return conservative spacing variants derived from existing layout settings."""
    standard = max(settings.group_v_gap, settings.node_v_gap)
    node_gap = settings.node_v_gap
    compact = max(
        MIXED_GLOBAL_MIN_VERTICAL_GAP,
        min(standard, node_gap) * MIXED_GLOBAL_COMPACT_GAP_RATIO,
    )
    result: list[float] = []
    for value in (standard, node_gap, compact):
        if not any(math.isclose(value, existing) for existing in result):
            result.append(value)
    return result


def _select_accepted_phase5_candidate(
    candidates: list[_Phase5Candidate],
    baseline: EngineV2Score,
    baseline_center: _CenterFlowMetrics,
) -> _Phase5Candidate | None:
    accepted = [
        candidate
        for candidate in candidates
        if _mixed_candidate_is_better(
            candidate.score,
            baseline,
            candidate.center_metrics,
            baseline_center,
        )
    ]
    if not accepted:
        return None
    return min(accepted, key=_phase5_candidate_quality_key)


def _phase5_candidate_quality_key(
    candidate: _Phase5Candidate,
) -> tuple[int, int, int, int, int, float, float, float, str]:
    score = candidate.score
    center = candidate.center_metrics
    return (
        score.movable_overlaps,
        score.crossings,
        center.crossings,
        score.right_to_left_links,
        center.right_to_left_links,
        score.width * score.height,
        score.width,
        score.height,
        candidate.name,
    )


def _best_diagnostic_candidate(
    candidates: list[_Phase5Candidate],
    baseline: EngineV2Score,
    baseline_center: _CenterFlowMetrics,
) -> _Phase5Candidate | None:
    if not candidates:
        return None

    reason_rank = {
        "accepted": 0,
        "area_regression": 1,
        "insufficient_final_width_reduction": 2,
        "center_rtl_regression": 3,
        "rtl_regression": 4,
        "center_crossing_regression": 5,
        "crossing_regression": 6,
        "movable_overlap_regression": 7,
        "invalid_baseline_width": 8,
    }

    def key(
        candidate: _Phase5Candidate,
    ) -> tuple[int, int, int, int, float, float, str]:
        reason = _mixed_rejection_reason(
            candidate.score,
            baseline,
            candidate.center_metrics,
            baseline_center,
        )
        return (
            reason_rank.get(reason, 99),
            max(0, candidate.score.crossings - baseline.crossings),
            max(
                0,
                candidate.center_metrics.crossings
                - baseline_center.crossings,
            ),
            max(
                0,
                candidate.score.right_to_left_links
                - baseline.right_to_left_links,
            )
            + max(
                0,
                candidate.center_metrics.right_to_left_links
                - baseline_center.right_to_left_links,
            ),
            candidate.score.width * candidate.score.height,
            candidate.score.width,
            candidate.name,
        )

    return min(candidates, key=key)


def _center_flow_metrics(workflow: Workflow) -> _CenterFlowMetrics:
    """Measure the same center-to-center geometry used by corpus reporting."""
    centers = {
        node.id: (
            node.x + (float(node.size[0]) if len(node.size) >= 1 else 0.0) / 2.0,
            node.y + (float(node.size[1]) if len(node.size) >= 2 else 0.0) / 2.0,
        )
        for node in workflow.nodes.values()
    }
    segments: list[
        tuple[int, int, tuple[float, float], tuple[float, float]]
    ] = []
    right_to_left = 0
    for link in workflow.links.values():
        start = centers.get(link.source)
        end = centers.get(link.target)
        if start is None or end is None:
            continue
        segments.append((link.source, link.target, start, end))
        if end[0] < start[0]:
            right_to_left += 1
    return _CenterFlowMetrics(
        crossings=_segment_crossing_count(segments),
        right_to_left_links=right_to_left,
    )


def _mixed_precheck_reason(
    workflow: Workflow,
    baseline_score: EngineV2Score,
    group_count: int,
    ungrouped_count: int,
    edge_weights: dict[tuple[int, int], int],
    layer_count: int,
) -> str | None:
    if baseline_score.width < MIXED_GLOBAL_MIN_WIDTH:
        return "workflow_below_min_width"
    if _has_phase5_pin_constraint(workflow):
        return "pinned_geometry_constraint"
    if _has_phase5_unmodeled_group_geometry(workflow):
        return "unmodeled_empty_group_geometry"
    if group_count < MIXED_GLOBAL_MIN_GROUPS:
        return "too_few_groups"
    if ungrouped_count < MIXED_GLOBAL_MIN_UNGROUPED:
        return "too_few_eligible_ungrouped_nodes"
    if not edge_weights:
        return "no_mixed_graph_edges"
    if layer_count <= 1:
        return "single_mixed_layer"
    return None


def _has_phase5_pin_constraint(workflow: Workflow) -> bool:
    if any(
        group.nodes and _group_has_fixed_geometry(group)
        for group in workflow.groups
    ):
        return True
    return any(
        _is_pinned_node(workflow, node)
        for node in workflow.ungrouped_nodes
        if node.id in workflow.nodes
    )


def _has_phase5_unmodeled_group_geometry(workflow: Workflow) -> bool:
    """Return True for authored group rectangles that the mixed graph cannot model."""
    return any(
        not group.nodes and _has_positive_bounding(group)
        for group in workflow.groups
    )


def _build_mixed_graph(
    workflow: Workflow,
) -> tuple[
    list[MixedSpec],
    dict[int, MixedSpec],
    dict[int, list[int]],
    dict[tuple[int, int], int],
]:
    """Build a directed graph of group supernodes and eligible ungrouped nodes."""
    groups = [
        group
        for group in workflow.groups
        if group.nodes and _has_positive_bounding(group)
    ]
    eligible_nodes = _eligible_ungrouped_nodes(workflow)

    specs: list[MixedSpec] = [
        ("group", group.id) for group in sorted(groups, key=lambda item: item.id)
    ]
    specs.extend(
        ("node", node.id) for node in sorted(eligible_nodes, key=lambda item: item.id)
    )
    vertex_by_spec = {spec: index for index, spec in enumerate(specs)}
    spec_by_vertex = {index: spec for spec, index in vertex_by_spec.items()}
    adjacency_sets: dict[int, set[int]] = {
        vertex: set() for vertex in spec_by_vertex
    }
    edge_weights: dict[tuple[int, int], int] = {}

    group_by_node_id = _group_by_node_id(workflow)
    eligible_ids = {node.id for node in eligible_nodes}

    def endpoint_spec(node_id: int) -> MixedSpec | None:
        group = group_by_node_id.get(node_id)
        if group is not None and ("group", group.id) in vertex_by_spec:
            return ("group", group.id)
        if node_id in eligible_ids:
            return ("node", node_id)
        return None

    for link in sorted(workflow.links.values(), key=lambda item: item.id):
        source_spec = endpoint_spec(link.source)
        target_spec = endpoint_spec(link.target)
        if source_spec is None or target_spec is None or source_spec == target_spec:
            continue
        source = vertex_by_spec[source_spec]
        target = vertex_by_spec[target_spec]
        adjacency_sets[source].add(target)
        pair = (source, target)
        edge_weights[pair] = edge_weights.get(pair, 0) + 1

    group_adjacency, _reverse = _group_flow_adjacency(workflow)
    for source_group_id, targets in group_adjacency.items():
        source_spec = ("group", source_group_id)
        if source_spec not in vertex_by_spec:
            continue
        source = vertex_by_spec[source_spec]
        for target_group_id in targets:
            target_spec = ("group", target_group_id)
            if target_spec not in vertex_by_spec or target_spec == source_spec:
                continue
            target = vertex_by_spec[target_spec]
            adjacency_sets[source].add(target)
            edge_weights.setdefault((source, target), 1)

    adjacency = {
        vertex: sorted(targets)
        for vertex, targets in adjacency_sets.items()
    }
    return specs, spec_by_vertex, adjacency, edge_weights


def _place_mixed_global_flow(
    workflow: Workflow,
    settings: LayoutSettings,
    workflow_width: float,
    *,
    order_mode: str = "weighted",
    horizontal_mode: str = "layer",
    vertical_gap: float | None = None,
    vertical_mode: str = "stacked",
    vertical_anchor_strength: float = 1.0,
) -> bool:
    """Place mixed-flow layers with conservative left-to-right geometry."""
    del workflow_width
    specs, spec_by_vertex, adjacency, edge_weights = _build_mixed_graph(workflow)
    group_count = sum(1 for kind, _value in specs if kind == "group")
    ungrouped_count = sum(1 for kind, _value in specs if kind == "node")
    if (
        _has_phase5_pin_constraint(workflow)
        or _has_phase5_unmodeled_group_geometry(workflow)
        or group_count < MIXED_GLOBAL_MIN_GROUPS
        or ungrouped_count < MIXED_GLOBAL_MIN_UNGROUPED
        or not edge_weights
    ):
        return False

    layers = _assign_scc_longest_path_layers(set(spec_by_vertex), adjacency)
    if len(set(layers.values())) <= 1:
        return False

    order: dict[int, list[MixedOrderVertex]]
    if order_mode == "weighted":
        order = _weighted_mixed_order(
            workflow,
            spec_by_vertex,
            layers,
            edge_weights,
        )
    elif order_mode == "stable":
        stable_order = _stable_mixed_order(workflow, spec_by_vertex, layers)
        order = {layer: [] for layer in stable_order}
        for layer, vertices in stable_order.items():
            order[layer].extend(vertices)
    else:
        raise ValueError(f"Unknown Phase 5 order mode: {order_mode}")
    layer_to_real = {
        layer: [
            vertex
            for vertex in vertices
            if isinstance(vertex, int)
        ]
        for layer, vertices in order.items()
    }
    layer_to_real = {
        layer: vertices
        for layer, vertices in layer_to_real.items()
        if vertices
    }
    if not layer_to_real:
        return False

    groups_by_id = {group.id: group for group in workflow.groups}
    nodes_by_id = {node.id: node for node in workflow.nodes.values()}
    layer_sizes: dict[int, tuple[float, float]] = {}
    placement_vertical_gap = (
        max(settings.group_v_gap, settings.node_v_gap)
        if vertical_gap is None
        else max(MIXED_GLOBAL_MIN_VERTICAL_GAP, vertical_gap)
    )
    if not 0.0 <= vertical_anchor_strength <= 1.0:
        raise ValueError(
            "Phase 5 vertical anchor strength must be between 0 and 1"
        )
    horizontal_gap = max(settings.group_h_gap, settings.node_h_gap)

    for layer, vertices in layer_to_real.items():
        widths: list[float] = []
        heights: list[float] = []
        for vertex in vertices:
            width, height = _mixed_vertex_size(
                spec_by_vertex[vertex],
                groups_by_id,
                nodes_by_id,
            )
            widths.append(width)
            heights.append(height)
        layer_sizes[layer] = (
            max(widths),
            sum(heights) + placement_vertical_gap * max(0, len(heights) - 1),
        )

    real_specs = [
        spec_by_vertex[vertex]
        for vertices in layer_to_real.values()
        for vertex in vertices
    ]
    base_x = min(
        (
            _mixed_vertex_xy(spec, groups_by_id, nodes_by_id)[0]
            for spec in real_specs
        ),
        default=50.0,
    )
    base_y = min(
        (
            _mixed_vertex_xy(spec, groups_by_id, nodes_by_id)[1]
            for spec in real_specs
        ),
        default=50.0,
    )
    vertex_y: dict[int, float] = {}
    if vertical_mode == "stacked":
        for layer in sorted(layer_to_real):
            current_y = base_y
            for vertex in layer_to_real[layer]:
                vertex_y[vertex] = current_y
                _width, height = _mixed_vertex_size(
                    spec_by_vertex[vertex],
                    groups_by_id,
                    nodes_by_id,
                )
                current_y += height + placement_vertical_gap
    elif vertical_mode == "baseline":
        for vertices in layer_to_real.values():
            for vertex in vertices:
                vertex_y[vertex] = _mixed_vertex_xy(
                    spec_by_vertex[vertex],
                    groups_by_id,
                    nodes_by_id,
                )[1]
    elif vertical_mode == "layer_anchor":
        for layer in sorted(layer_to_real):
            current_y = base_y
            stacked_y: dict[int, float] = {}
            offsets: list[float] = []
            for vertex in layer_to_real[layer]:
                stacked_y[vertex] = current_y
                original_y = _mixed_vertex_xy(
                    spec_by_vertex[vertex],
                    groups_by_id,
                    nodes_by_id,
                )[1]
                offsets.append(original_y - current_y)
                _width, height = _mixed_vertex_size(
                    spec_by_vertex[vertex],
                    groups_by_id,
                    nodes_by_id,
                )
                current_y += height + placement_vertical_gap

            ordered_offsets = sorted(offsets)
            middle = len(ordered_offsets) // 2
            if len(ordered_offsets) % 2:
                layer_shift = ordered_offsets[middle]
            else:
                layer_shift = (
                    ordered_offsets[middle - 1] + ordered_offsets[middle]
                ) / 2.0

            applied_shift = layer_shift * vertical_anchor_strength
            for vertex, stacked in stacked_y.items():
                vertex_y[vertex] = stacked + applied_shift
    else:
        raise ValueError(f"Unknown Phase 5 vertical mode: {vertical_mode}")

    if horizontal_mode == "layer":
        layer_positions = _monotonic_layer_positions(
            layer_sizes,
            base_x,
            base_y,
            horizontal_gap,
        )
        vertex_x = {
            vertex: layer_positions[layer][0]
            for layer, vertices in layer_to_real.items()
            for vertex in vertices
        }
    elif horizontal_mode == "compact":
        vertex_x = _compact_mixed_vertex_x_positions(
            layer_to_real,
            layers,
            edge_weights,
            spec_by_vertex,
            groups_by_id,
            nodes_by_id,
            vertex_y,
            base_x,
            horizontal_gap,
        )
    elif horizontal_mode == "anchored":
        vertex_x = _anchored_compact_mixed_vertex_x_positions(
            layer_to_real,
            layers,
            edge_weights,
            spec_by_vertex,
            groups_by_id,
            nodes_by_id,
            vertex_y,
            base_x,
            horizontal_gap,
        )
    else:
        raise ValueError(f"Unknown Phase 5 horizontal mode: {horizontal_mode}")

    changed = False
    for layer in sorted(layer_to_real):
        for vertex in layer_to_real[layer]:
            spec = spec_by_vertex[vertex]
            x = vertex_x[vertex]
            y = vertex_y[vertex]
            old_x, old_y = _mixed_vertex_xy(spec, groups_by_id, nodes_by_id)
            if abs(old_x - x) > 1e-9 or abs(old_y - y) > 1e-9:
                changed = True
            _move_mixed_vertex(
                spec,
                groups_by_id,
                nodes_by_id,
                x,
                y,
            )
    return changed


def _monotonic_layer_positions(
    layer_sizes: dict[int, tuple[float, float]],
    base_x: float,
    base_y: float,
    horizontal_gap: float,
) -> dict[int, tuple[float, float]]:
    """Keep dependency layers monotonic in X while sharing one vertical band."""
    positions: dict[int, tuple[float, float]] = {}
    current_x = base_x
    for layer in sorted(layer_sizes):
        positions[layer] = (current_x, base_y)
        current_x += layer_sizes[layer][0] + horizontal_gap
    return positions


def _compact_mixed_vertex_x_positions(
    layer_to_real: dict[int, list[int]],
    layers: dict[int, int],
    edge_weights: dict[tuple[int, int], int],
    spec_by_vertex: dict[int, MixedSpec],
    groups_by_id: dict[int, Group],
    nodes_by_id: dict[int, Node],
    vertex_y: dict[int, float],
    base_x: float,
    horizontal_gap: float,
) -> dict[int, float]:
    """Compact independent flow lanes without weakening edge direction.

    The legacy Phase 5 realization gives every real vertex in one dependency
    layer the same X coordinate and advances the next layer by the widest
    rectangle in the layer. That is simple and safe, but one wide group can
    needlessly push unrelated narrow chains to the right.

    This variant keeps the same layer assignment and vertical order. Each vertex
    starts at the leftmost X allowed by its already placed predecessors, then
    moves right only as needed to clear already placed geometry whose vertical
    span overlaps its own. Direct forward dependencies therefore remain
    left-to-right while unrelated vertical lanes may use different X positions.
    """
    predecessors: dict[int, list[int]] = {}
    for source, target in edge_weights:
        if layers.get(source, 0) >= layers.get(target, 0):
            continue
        predecessors.setdefault(target, []).append(source)

    positions: dict[int, float] = {}
    placed_rects: list[tuple[float, float, float, float]] = []

    for layer in sorted(layer_to_real):
        for vertex in layer_to_real[layer]:
            spec = spec_by_vertex[vertex]
            width, height = _mixed_vertex_size(
                spec,
                groups_by_id,
                nodes_by_id,
            )
            y = vertex_y[vertex]
            x = base_x

            for source in predecessors.get(vertex, []):
                source_x = positions.get(source)
                if source_x is None:
                    continue
                source_width, _source_height = _mixed_vertex_size(
                    spec_by_vertex[source],
                    groups_by_id,
                    nodes_by_id,
                )
                x = max(x, source_x + source_width + horizontal_gap)

            while True:
                next_x = x
                for left, top, right, bottom in placed_rects:
                    if y >= bottom or top >= y + height:
                        continue
                    if x >= right + horizontal_gap:
                        continue
                    if left >= x + width + horizontal_gap:
                        continue
                    next_x = max(next_x, right + horizontal_gap)
                if next_x <= x + 1e-9:
                    break
                x = next_x

            positions[vertex] = x
            placed_rects.append((x, y, x + width, y + height))

    return positions


def _anchored_compact_mixed_vertex_x_positions(
    layer_to_real: dict[int, list[int]],
    layers: dict[int, int],
    edge_weights: dict[tuple[int, int], int],
    spec_by_vertex: dict[int, MixedSpec],
    groups_by_id: dict[int, Group],
    nodes_by_id: dict[int, Node],
    vertex_y: dict[int, float],
    base_x: float,
    horizontal_gap: float,
) -> dict[int, float]:
    """Use compact width while retaining as much baseline X placement as possible.

    The normal compact realization is an earliest-feasible schedule: every real
    vertex is pushed as far left as dependency and overlap constraints allow.
    That can over-compact weakly constrained branches into distant islands.

    Anchored compaction starts from the exact compact schedule, keeps its right
    boundary, direct forward-edge constraints, and horizontal ordering for
    vertically overlapping rectangles, then shifts vertices right within their
    available slack toward their Phase 4 X positions. It can therefore preserve
    authored/top-level locality without giving back the compact width envelope.
    """
    earliest = _compact_mixed_vertex_x_positions(
        layer_to_real,
        layers,
        edge_weights,
        spec_by_vertex,
        groups_by_id,
        nodes_by_id,
        vertex_y,
        base_x,
        horizontal_gap,
    )
    if not earliest:
        return {}

    widths: dict[int, float] = {}
    heights: dict[int, float] = {}
    baseline_x: dict[int, float] = {}
    for vertex in earliest:
        spec = spec_by_vertex[vertex]
        width, height = _mixed_vertex_size(spec, groups_by_id, nodes_by_id)
        widths[vertex] = width
        heights[vertex] = height
        baseline_x[vertex] = _mixed_vertex_xy(
            spec,
            groups_by_id,
            nodes_by_id,
        )[0]

    right_boundary = max(
        earliest[vertex] + widths[vertex]
        for vertex in earliest
    )

    successors: dict[int, list[int]] = {}
    for source, target in edge_weights:
        if (
            source in earliest
            and target in earliest
            and layers.get(source, 0) < layers.get(target, 0)
        ):
            successors.setdefault(source, []).append(target)

    right_neighbours: dict[int, list[int]] = {}
    vertices = list(earliest)
    for index, first in enumerate(vertices):
        first_top = vertex_y[first]
        first_bottom = first_top + heights[first]
        for second in vertices[index + 1 :]:
            second_top = vertex_y[second]
            second_bottom = second_top + heights[second]
            if first_top >= second_bottom or second_top >= first_bottom:
                continue

            first_right = earliest[first] + widths[first]
            second_right = earliest[second] + widths[second]
            if first_right + horizontal_gap <= earliest[second] + 1e-9:
                right_neighbours.setdefault(first, []).append(second)
            elif second_right + horizontal_gap <= earliest[first] + 1e-9:
                right_neighbours.setdefault(second, []).append(first)

    positions = dict(earliest)
    for vertex in sorted(
        positions,
        key=lambda value: (earliest[value], layers.get(value, 0), value),
        reverse=True,
    ):
        width = widths[vertex]
        upper_bound = right_boundary - width

        for target in successors.get(vertex, []):
            upper_bound = min(
                upper_bound,
                positions[target] - width - horizontal_gap,
            )
        for neighbour in right_neighbours.get(vertex, []):
            upper_bound = min(
                upper_bound,
                positions[neighbour] - width - horizontal_gap,
            )

        desired = baseline_x[vertex]
        positions[vertex] = max(
            earliest[vertex],
            min(desired, upper_bound),
        )

    return positions


def _mixed_vertex_size(
    spec: MixedSpec,
    groups_by_id: dict[int, Group],
    nodes_by_id: dict[int, Node],
) -> tuple[float, float]:
    kind, value = spec
    if kind == "group":
        group = groups_by_id[value]
        return group.bounding[2], group.bounding[3]
    node = nodes_by_id[value]
    return _node_visual_width(node), _node_visual_height(node)


def _mixed_vertex_xy(
    spec: MixedSpec,
    groups_by_id: dict[int, Group],
    nodes_by_id: dict[int, Node],
) -> tuple[float, float]:
    kind, value = spec
    if kind == "group":
        group = groups_by_id[value]
        return group.bounding[0], group.bounding[1]
    node = nodes_by_id[value]
    return node.x, node.y


def _mixed_vertex_center_y(
    spec: MixedSpec,
    groups_by_id: dict[int, Group],
    nodes_by_id: dict[int, Node],
) -> float:
    x, y = _mixed_vertex_xy(spec, groups_by_id, nodes_by_id)
    del x
    _width, height = _mixed_vertex_size(spec, groups_by_id, nodes_by_id)
    return y + height / 2.0


def _move_mixed_vertex(
    spec: MixedSpec,
    groups_by_id: dict[int, Group],
    nodes_by_id: dict[int, Node],
    x: float,
    y: float,
) -> None:
    kind, value = spec
    if kind == "group":
        group = groups_by_id[value]
        _move_group_geometry(
            group,
            x - group.bounding[0],
            y - group.bounding[1],
        )
        return
    node = nodes_by_id[value]
    node.x = x
    node.y = y


def _stable_mixed_order(
    workflow: Workflow,
    spec_by_vertex: dict[int, MixedSpec],
    layers: dict[int, int],
) -> dict[int, list[int]]:
    """Preserve baseline vertical order inside each mixed dependency layer."""
    groups_by_id = {group.id: group for group in workflow.groups}
    nodes_by_id = {node.id: node for node in workflow.nodes.values()}
    layer_to_vertices: dict[int, list[int]] = {}
    for vertex, layer in layers.items():
        layer_to_vertices.setdefault(layer, []).append(vertex)

    for vertices in layer_to_vertices.values():
        vertices.sort(
            key=lambda vertex: (
                _mixed_vertex_center_y(
                    spec_by_vertex[vertex],
                    groups_by_id,
                    nodes_by_id,
                ),
                _mixed_order_key(vertex),
            )
        )
    return layer_to_vertices


def _weighted_mixed_order(
    workflow: Workflow,
    spec_by_vertex: dict[int, MixedSpec],
    layers: dict[int, int],
    edge_weights: dict[tuple[int, int], int],
) -> dict[int, list[MixedOrderVertex]]:
    """Order the shared mixed graph with weighted median sweeps and transpose."""
    max_layer = max(layers.values(), default=0)
    groups_by_id = {group.id: group for group in workflow.groups}
    nodes_by_id = {node.id: node for node in workflow.nodes.values()}
    layer_to_vertices: dict[int, list[MixedOrderVertex]] = {
        layer: [] for layer in range(max_layer + 1)
    }
    stable_y: dict[MixedOrderVertex, float] = {}

    for vertex, layer in layers.items():
        layer_to_vertices.setdefault(layer, []).append(vertex)
        stable_y[vertex] = _mixed_vertex_center_y(
            spec_by_vertex[vertex],
            groups_by_id,
            nodes_by_id,
        )

    expanded_edges: list[WeightedMixedEdge] = []
    for (source, target), weight in sorted(edge_weights.items()):
        source_layer = layers.get(source, 0)
        target_layer = layers.get(target, 0)
        if target_layer <= source_layer:
            continue

        previous: MixedOrderVertex = source
        source_y = stable_y.get(source, 0.0)
        target_y = stable_y.get(target, 0.0)
        span = target_layer - source_layer
        for layer in range(source_layer + 1, target_layer):
            dummy: MixedDummy = ("mixed-dummy", source, target, layer)
            layer_to_vertices.setdefault(layer, []).append(dummy)
            fraction = (layer - source_layer) / span
            stable_y[dummy] = source_y + (target_y - source_y) * fraction
            expanded_edges.append((previous, dummy, weight))
            previous = dummy
        expanded_edges.append((previous, target, weight))

    for vertices in layer_to_vertices.values():
        vertices.sort(
            key=lambda vertex: (
                stable_y.get(vertex, 0.0),
                _mixed_order_key(vertex),
            )
        )

    predecessors: dict[MixedOrderVertex, list[tuple[MixedOrderVertex, int]]] = {}
    successors: dict[MixedOrderVertex, list[tuple[MixedOrderVertex, int]]] = {}
    for expanded_source, expanded_target, weight in expanded_edges:
        successors.setdefault(expanded_source, []).append((expanded_target, weight))
        predecessors.setdefault(expanded_target, []).append((expanded_source, weight))

    for _ in range(MIXED_SWEEP_ROUNDS):
        for layer in range(1, max_layer + 1):
            _weighted_mixed_median_reorder(
                layer_to_vertices,
                layer,
                predecessors,
                layer - 1,
                stable_y,
            )
        for layer in range(max_layer - 1, -1, -1):
            _weighted_mixed_median_reorder(
                layer_to_vertices,
                layer,
                successors,
                layer + 1,
                stable_y,
            )
        _weighted_mixed_transpose(layer_to_vertices, expanded_edges, max_layer)

    return layer_to_vertices


def _weighted_mixed_median_reorder(
    layer_to_vertices: dict[int, list[MixedOrderVertex]],
    layer: int,
    neighbours: dict[MixedOrderVertex, list[tuple[MixedOrderVertex, int]]],
    reference_layer: int,
    stable_y: dict[MixedOrderVertex, float],
) -> None:
    vertices = layer_to_vertices.get(layer, [])
    reference = layer_to_vertices.get(reference_layer, [])
    if len(vertices) <= 1 or not reference:
        return
    positions = {vertex: index for index, vertex in enumerate(reference)}

    def sort_key(vertex: MixedOrderVertex) -> tuple[float, float, str]:
        weighted_positions = [
            (positions[other], weight)
            for other, weight in neighbours.get(vertex, [])
            if other in positions
        ]
        median_position = (
            _weighted_median(weighted_positions)
            if weighted_positions
            else math.inf
        )
        return (
            median_position,
            stable_y.get(vertex, 0.0),
            _mixed_order_key(vertex),
        )

    layer_to_vertices[layer] = sorted(vertices, key=sort_key)


def _weighted_median(values: list[tuple[int, int]]) -> float:
    ordered = sorted(values)
    total_weight = sum(max(0, weight) for _position, weight in ordered)
    if total_weight <= 0:
        return math.inf

    threshold = total_weight / 2.0
    cumulative = 0
    for position, weight in ordered:
        cumulative += max(0, weight)
        if cumulative >= threshold:
            return float(position)
    return float(ordered[-1][0])


def _weighted_mixed_transpose(
    layer_to_vertices: dict[int, list[MixedOrderVertex]],
    edges: list[WeightedMixedEdge],
    max_layer: int,
) -> None:
    for _ in range(MIXED_TRANSPOSE_PASSES):
        improved = False
        for layer in range(max_layer + 1):
            vertices = layer_to_vertices.get(layer, [])
            if len(vertices) <= 1:
                continue
            index = 0
            while index < len(vertices) - 1:
                before = _weighted_mixed_local_crossings(
                    layer_to_vertices,
                    edges,
                    layer,
                    max_layer,
                )
                vertices[index], vertices[index + 1] = (
                    vertices[index + 1],
                    vertices[index],
                )
                after = _weighted_mixed_local_crossings(
                    layer_to_vertices,
                    edges,
                    layer,
                    max_layer,
                )
                if after < before:
                    improved = True
                else:
                    vertices[index], vertices[index + 1] = (
                        vertices[index + 1],
                        vertices[index],
                    )
                index += 1
        if not improved:
            break


def _weighted_mixed_local_crossings(
    layer_to_vertices: dict[int, list[MixedOrderVertex]],
    edges: list[WeightedMixedEdge],
    layer: int,
    max_layer: int,
) -> int:
    crossings = 0
    if layer > 0:
        crossings += _weighted_mixed_boundary_crossings(
            layer_to_vertices,
            edges,
            layer - 1,
            layer,
        )
    if layer < max_layer:
        crossings += _weighted_mixed_boundary_crossings(
            layer_to_vertices,
            edges,
            layer,
            layer + 1,
        )
    return crossings


def _weighted_mixed_boundary_crossings(
    layer_to_vertices: dict[int, list[MixedOrderVertex]],
    edges: list[WeightedMixedEdge],
    left_layer: int,
    right_layer: int,
) -> int:
    left = layer_to_vertices.get(left_layer, [])
    right = layer_to_vertices.get(right_layer, [])
    if not left or not right:
        return 0

    left_pos = {vertex: index for index, vertex in enumerate(left)}
    right_pos = {vertex: index for index, vertex in enumerate(right)}
    boundary_edges = [
        (source, target, weight)
        for source, target, weight in edges
        if source in left_pos and target in right_pos
    ]

    crossings = 0
    for index, first in enumerate(boundary_edges):
        for second in boundary_edges[index + 1 :]:
            if first[0] == second[0] or first[1] == second[1]:
                continue
            if (left_pos[first[0]] - left_pos[second[0]]) * (
                right_pos[first[1]] - right_pos[second[1]]
            ) < 0:
                crossings += first[2] * second[2]
    return crossings


def _mixed_order_key(vertex: MixedOrderVertex) -> str:
    return str(vertex)


def _mixed_candidate_is_better(
    candidate: EngineV2Score,
    baseline: EngineV2Score,
    candidate_center: _CenterFlowMetrics | None = None,
    baseline_center: _CenterFlowMetrics | None = None,
) -> bool:
    """Require strict graph-quality preservation plus real width/area gain."""
    return (
        _mixed_rejection_reason(
            candidate,
            baseline,
            candidate_center,
            baseline_center,
        )
        == "accepted"
    )


def _mixed_rejection_reason(
    candidate: EngineV2Score,
    baseline: EngineV2Score,
    candidate_center: _CenterFlowMetrics | None = None,
    baseline_center: _CenterFlowMetrics | None = None,
) -> str:
    if candidate.movable_overlaps > baseline.movable_overlaps:
        return "movable_overlap_regression"
    if candidate.crossings > baseline.crossings:
        return "crossing_regression"
    if (
        candidate_center is not None
        and baseline_center is not None
        and candidate_center.crossings > baseline_center.crossings
    ):
        return "center_crossing_regression"
    if candidate.right_to_left_links > baseline.right_to_left_links:
        return "rtl_regression"
    if (
        candidate_center is not None
        and baseline_center is not None
        and candidate_center.right_to_left_links
        > baseline_center.right_to_left_links
    ):
        return "center_rtl_regression"
    if baseline.width <= 0:
        return "invalid_baseline_width"

    width_reduction = 1.0 - candidate.width / baseline.width
    if width_reduction < MIXED_GLOBAL_MIN_ACCEPTED_WIDTH_REDUCTION_RATIO:
        return "insufficient_final_width_reduction"

    baseline_area = baseline.width * baseline.height
    candidate_area = candidate.width * candidate.height
    if (
        baseline_area > 0
        and candidate_area > baseline_area * MIXED_GLOBAL_MAX_AREA_RATIO
    ):
        return "area_regression"
    return "accepted"
