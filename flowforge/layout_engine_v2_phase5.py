"""Mixed group/ungrouped global-flow refinement for layout engine v2.

Phase 5 is an isolated candidate on top of the complete Phase 4 result. It
builds one directed dependency graph whose real vertices are whole groups and
eligible ungrouped nodes. Group internals remain unchanged: a group is moved as
one rectangle together with all of its member nodes.

Pinned geometry remains a hard constraint. Workflows with pinned groups or
pinned ungrouped nodes are skipped entirely by this first Phase 5 experiment.
Established local-placement special cases remain outside the mixed graph and
are re-applied by the normal refinement finalizer.

The acceptance gate is deliberately strict: no additional movable overlaps,
crossings, or right-to-left links; at least 8% workflow-width reduction; and no
workflow-area growth.
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
    _toplevel_wrap_row_width_cap,
    _wrap_layer_columns,
)
from .layout_engine_v2 import (
    EngineV2Score,
    _assign_scc_longest_path_layers,
    _finalize_refinement,
    _score_engine_v2,
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
MIXED_GLOBAL_TARGET_WIDTH_RATIO = 0.70
MIXED_GLOBAL_MIN_ACCEPTED_WIDTH_REDUCTION_RATIO = 0.08
MIXED_GLOBAL_MAX_AREA_RATIO = 1.0
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


def apply_best_layout(
    workflow: Workflow,
    settings: LayoutSettings | None = None,
    candidate_count: int | None = None,
) -> Workflow:
    """Run Phase 4, then try one shared group/ungrouped global-flow candidate."""
    settings = settings or LayoutSettings()
    baseline = _apply_phase4_best_layout(workflow, settings, candidate_count)
    baseline_score = _score_engine_v2(baseline)
    if baseline_score.width < MIXED_GLOBAL_MIN_WIDTH:
        return baseline

    candidate = deepcopy(baseline)
    if not _place_mixed_global_flow(candidate, settings, baseline_score.width):
        return baseline

    _finalize_refinement(candidate, settings)
    candidate_score = _score_engine_v2(candidate)
    if _mixed_candidate_is_better(candidate_score, baseline_score):
        logger.info(
            "Phase 5 mixed global-flow accepted: "
            "size %.0fx%.0f -> %.0fx%.0f, crossings %s -> %s, rtl %s -> %s",
            baseline_score.width,
            baseline_score.height,
            candidate_score.width,
            candidate_score.height,
            baseline_score.crossings,
            candidate_score.crossings,
            baseline_score.right_to_left_links,
            candidate_score.right_to_left_links,
        )
        return candidate

    logger.info(
        "Phase 5 mixed global-flow rejected: "
        "size %.0fx%.0f -> %.0fx%.0f, crossings %s -> %s, rtl %s -> %s; reason=%s",
        baseline_score.width,
        baseline_score.height,
        candidate_score.width,
        candidate_score.height,
        baseline_score.crossings,
        candidate_score.crossings,
        baseline_score.right_to_left_links,
        candidate_score.right_to_left_links,
        _mixed_rejection_reason(candidate_score, baseline_score),
    )
    return baseline


def diagnose_phase5(
    workflow: Workflow,
    settings: LayoutSettings | None = None,
) -> Phase5Diagnostics:
    """Return a read-only explanation of the Phase 5 decision."""
    settings = settings or LayoutSettings()
    baseline_score = _score_engine_v2(workflow)
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
        )

    candidate = deepcopy(workflow)
    moved = _place_mixed_global_flow(candidate, settings, baseline_score.width)
    if not moved:
        return Phase5Diagnostics(
            group_vertices=group_count,
            ungrouped_vertices=ungrouped_count,
            graph_edges=len(edge_weights),
            layers=layer_count,
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
        )

    _finalize_refinement(candidate, settings)
    candidate_score = _score_engine_v2(candidate)
    accepted = _mixed_candidate_is_better(candidate_score, baseline_score)
    return Phase5Diagnostics(
        group_vertices=group_count,
        ungrouped_vertices=ungrouped_count,
        graph_edges=len(edge_weights),
        layers=layer_count,
        attempted=True,
        accepted=accepted,
        rejection_reason=(
            "accepted"
            if accepted
            else _mixed_rejection_reason(candidate_score, baseline_score)
        ),
        baseline_width=baseline_score.width,
        baseline_height=baseline_score.height,
        proposed_width=candidate_score.width,
        proposed_height=candidate_score.height,
        baseline_crossings=baseline_score.crossings,
        proposed_crossings=candidate_score.crossings,
        baseline_rtl=baseline_score.right_to_left_links,
        proposed_rtl=candidate_score.right_to_left_links,
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
) -> bool:
    """Place groups and eligible ungrouped nodes in one shared layered flow."""
    specs, spec_by_vertex, adjacency, edge_weights = _build_mixed_graph(workflow)
    group_count = sum(1 for kind, _value in specs if kind == "group")
    ungrouped_count = sum(1 for kind, _value in specs if kind == "node")
    if (
        _has_phase5_pin_constraint(workflow)
        or group_count < MIXED_GLOBAL_MIN_GROUPS
        or ungrouped_count < MIXED_GLOBAL_MIN_UNGROUPED
        or not edge_weights
    ):
        return False

    layers = _assign_scc_longest_path_layers(set(spec_by_vertex), adjacency)
    if len(set(layers.values())) <= 1:
        return False

    order = _weighted_mixed_order(
        workflow,
        spec_by_vertex,
        layers,
        edge_weights,
    )
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
    vertical_gap = max(settings.group_v_gap, settings.node_v_gap)
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
            sum(heights) + vertical_gap * max(0, len(heights) - 1),
        )

    max_layer_width = max(width for width, _height in layer_sizes.values())
    automatic_cap = _toplevel_wrap_row_width_cap(layer_sizes)
    target_cap = max(
        max_layer_width,
        min(automatic_cap, workflow_width * MIXED_GLOBAL_TARGET_WIDTH_RATIO),
    )

    real_specs = [spec_by_vertex[vertex] for vertices in layer_to_real.values() for vertex in vertices]
    base_x = min(
        (_mixed_vertex_xy(spec, groups_by_id, nodes_by_id)[0] for spec in real_specs),
        default=50.0,
    )
    base_y = min(
        (_mixed_vertex_xy(spec, groups_by_id, nodes_by_id)[1] for spec in real_specs),
        default=50.0,
    )
    layer_positions = _wrap_layer_columns(
        layer_sizes,
        target_cap,
        base_x,
        base_y,
        horizontal_gap,
        vertical_gap,
    )

    changed = False
    for layer in sorted(layer_to_real):
        x, current_y = layer_positions[layer]
        for vertex in layer_to_real[layer]:
            spec = spec_by_vertex[vertex]
            old_x, old_y = _mixed_vertex_xy(spec, groups_by_id, nodes_by_id)
            if abs(old_x - x) > 1e-9 or abs(old_y - current_y) > 1e-9:
                changed = True
            _move_mixed_vertex(
                spec,
                groups_by_id,
                nodes_by_id,
                x,
                current_y,
            )
            _width, height = _mixed_vertex_size(
                spec,
                groups_by_id,
                nodes_by_id,
            )
            current_y += height + vertical_gap
    return changed


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
    for source, target, weight in expanded_edges:
        successors.setdefault(source, []).append((target, weight))
        predecessors.setdefault(target, []).append((source, weight))

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
) -> bool:
    """Require strict graph-quality preservation plus real width/area gain."""
    return _mixed_rejection_reason(candidate, baseline) == "accepted"


def _mixed_rejection_reason(
    candidate: EngineV2Score,
    baseline: EngineV2Score,
) -> str:
    if candidate.movable_overlaps > baseline.movable_overlaps:
        return "movable_overlap_regression"
    if candidate.crossings > baseline.crossings:
        return "crossing_regression"
    if candidate.right_to_left_links > baseline.right_to_left_links:
        return "rtl_regression"
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
