"""Group-level crossing refinement for FlowForge layout engine v2.

Phase 2 keeps the proven node-level v2 engine as its baseline, then treats
movable groups as vertices in a second Sugiyama-style ordering pass. The pass
uses the existing inter-group topology, SCC-aware layer assignment, weighted
physical group edges, dummy vertices for long edges, repeated weighted-median
sweeps, and local transposition.

Only vertical order inside an existing group-flow layer is changed. Group
columns, internal node geometry, pinned groups, and graph topology are left
untouched. A refined result is accepted only when its global port-aware quality
key improves over the baseline.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import TypeAlias

from .layout import LayoutSettings, _group_flow_adjacency, _is_pinned_node
from .layout_engine_v2 import (
    EngineV2Score,
    _assign_scc_longest_path_layers,
    _finalize_refinement,
    _score_engine_v2,
    apply_best_layout as _apply_core_best_layout,
)
from .logger import setup_logger
from .model import Group, Workflow

logger = setup_logger(__name__)

GROUP_SWEEP_ROUNDS = 6
GROUP_TRANSPOSE_PASSES = 6

GroupDummy: TypeAlias = tuple[str, int, int, int]
GroupVertex: TypeAlias = int | GroupDummy
WeightedGroupEdge: TypeAlias = tuple[GroupVertex, GroupVertex, int]


def apply_best_layout(
    workflow: Workflow,
    settings: LayoutSettings | None = None,
    candidate_count: int | None = None,
) -> Workflow:
    """Run the core v2 engine, then try weighted group-level refinement."""
    settings = settings or LayoutSettings()
    baseline = _apply_core_best_layout(workflow, settings, candidate_count)
    baseline_score = _score_engine_v2(baseline)

    refined = deepcopy(baseline)
    if not _refine_group_level_order(refined, settings):
        return baseline

    _finalize_refinement(refined, settings)
    refined_score = _score_engine_v2(refined)
    if _quality_key(refined_score) < _quality_key(baseline_score):
        logger.info(
            "Group-level v2 refinement accepted: crossings %s -> %s, rtl %s -> %s",
            baseline_score.crossings,
            refined_score.crossings,
            baseline_score.right_to_left_links,
            refined_score.right_to_left_links,
        )
        return refined

    logger.info(
        "Group-level v2 refinement rejected: crossings %s -> %s, rtl %s -> %s",
        baseline_score.crossings,
        refined_score.crossings,
        baseline_score.right_to_left_links,
        refined_score.right_to_left_links,
    )
    return baseline


def _quality_key(score: EngineV2Score) -> tuple[int, int, int, float, float, float, float]:
    """Compare layout candidates lexicographically by visible graph quality."""
    return (
        score.movable_overlaps,
        score.crossings,
        score.right_to_left_links,
        score.link_length,
        score.width + score.height,
        score.width,
        score.height,
    )


def _refine_group_level_order(workflow: Workflow, settings: LayoutSettings) -> bool:
    """Reorder movable groups vertically inside their existing flow layers."""
    groups = [group for group in workflow.groups if group.nodes]
    if len(groups) < 2:
        return False

    group_ids = {group.id for group in groups}
    adjacency_sets, _reverse = _group_flow_adjacency(workflow)
    adjacency = {
        group_id: sorted(target for target in adjacency_sets.get(group_id, set()) if target in group_ids)
        for group_id in group_ids
    }
    if not any(adjacency.values()):
        return False

    layers = _assign_scc_longest_path_layers(group_ids, adjacency)
    edge_weights = _group_edge_weights(workflow, group_ids, adjacency)
    if not edge_weights:
        return False

    movable_group_ids = {
        group.id
        for group in groups
        if not _group_is_fixed(workflow, group)
        and len(group.bounding) >= 4
        and group.bounding[2] > 0
        and group.bounding[3] > 0
    }
    if len(movable_group_ids) < 2:
        return False

    layer_order = _weighted_group_order(
        workflow,
        layers,
        edge_weights,
        movable_group_ids,
    )
    return _apply_group_layer_order(
        workflow,
        layers,
        layer_order,
        movable_group_ids,
        settings,
    )


def _group_is_fixed(workflow: Workflow, group: Group) -> bool:
    return group.pinned or any(_is_pinned_node(workflow, node) for node in group.nodes)


def _group_edge_weights(
    workflow: Workflow,
    group_ids: set[int],
    adjacency: dict[int, list[int]],
) -> dict[tuple[int, int], int]:
    """Weight direct group dependencies by their physical wire multiplicity."""
    group_by_node_id = {
        node.id: group
        for group in workflow.groups
        for node in group.nodes
        if group.id in group_ids
    }
    weights: dict[tuple[int, int], int] = {}

    for link in workflow.links.values():
        source_group = group_by_node_id.get(link.source)
        target_group = group_by_node_id.get(link.target)
        if source_group is None or target_group is None or source_group is target_group:
            continue
        pair = (source_group.id, target_group.id)
        if target_group.id not in adjacency.get(source_group.id, []):
            continue
        weights[pair] = weights.get(pair, 0) + 1

    # Bridge-only dependencies do not have one physical link whose endpoints
    # both belong to groups. Keep them in the ordering graph with unit weight.
    for source_id, targets in adjacency.items():
        for target_id in targets:
            weights.setdefault((source_id, target_id), 1)

    return weights


def _weighted_group_order(
    workflow: Workflow,
    layers: dict[int, int],
    edge_weights: dict[tuple[int, int], int],
    movable_group_ids: set[int],
) -> dict[int, list[GroupVertex]]:
    """Order group vertices with weighted median sweeps and transposition."""
    max_layer = max(layers.values(), default=0)
    group_by_id = {group.id: group for group in workflow.groups}
    layer_to_vertices: dict[int, list[GroupVertex]] = {
        layer: [] for layer in range(max_layer + 1)
    }
    stable_y: dict[GroupVertex, float] = {}

    for group_id, layer in layers.items():
        group = group_by_id.get(group_id)
        if group is None:
            continue
        layer_to_vertices.setdefault(layer, []).append(group_id)
        stable_y[group_id] = _group_center_y(group)

    expanded_edges: list[WeightedGroupEdge] = []
    for (source_id, target_id), weight in sorted(edge_weights.items()):
        source_layer = layers.get(source_id, 0)
        target_layer = layers.get(target_id, 0)
        if target_layer <= source_layer:
            continue

        previous: GroupVertex = source_id
        source_y = stable_y.get(source_id, 0.0)
        target_y = stable_y.get(target_id, 0.0)
        span = target_layer - source_layer
        for layer in range(source_layer + 1, target_layer):
            dummy: GroupDummy = ("group-dummy", source_id, target_id, layer)
            layer_to_vertices.setdefault(layer, []).append(dummy)
            fraction = (layer - source_layer) / span
            stable_y[dummy] = source_y + (target_y - source_y) * fraction
            expanded_edges.append((previous, dummy, weight))
            previous = dummy
        expanded_edges.append((previous, target_id, weight))

    for vertices in layer_to_vertices.values():
        vertices.sort(key=lambda vertex: (stable_y.get(vertex, 0.0), _vertex_key(vertex)))

    predecessors: dict[GroupVertex, list[tuple[GroupVertex, int]]] = {}
    successors: dict[GroupVertex, list[tuple[GroupVertex, int]]] = {}
    for source, target, weight in expanded_edges:
        successors.setdefault(source, []).append((target, weight))
        predecessors.setdefault(target, []).append((source, weight))

    for _ in range(GROUP_SWEEP_ROUNDS):
        for layer in range(1, max_layer + 1):
            _weighted_median_reorder_layer(
                layer_to_vertices,
                layer,
                predecessors,
                reference_layer=layer - 1,
                stable_y=stable_y,
                movable_group_ids=movable_group_ids,
            )
        for layer in range(max_layer - 1, -1, -1):
            _weighted_median_reorder_layer(
                layer_to_vertices,
                layer,
                successors,
                reference_layer=layer + 1,
                stable_y=stable_y,
                movable_group_ids=movable_group_ids,
            )
        _weighted_transpose_layers(
            layer_to_vertices,
            expanded_edges,
            max_layer,
            movable_group_ids,
        )

    return layer_to_vertices


def _weighted_median_reorder_layer(
    layer_to_vertices: dict[int, list[GroupVertex]],
    layer: int,
    neighbours: dict[GroupVertex, list[tuple[GroupVertex, int]]],
    *,
    reference_layer: int,
    stable_y: dict[GroupVertex, float],
    movable_group_ids: set[int],
) -> None:
    vertices = layer_to_vertices.get(layer, [])
    reference = layer_to_vertices.get(reference_layer, [])
    if len(vertices) <= 1 or not reference:
        return

    # A layer containing fixed real groups is left in its physical order. This
    # prevents an abstract order that cannot be committed from distorting the
    # neighbouring layers during later sweeps.
    if any(isinstance(vertex, int) and vertex not in movable_group_ids for vertex in vertices):
        return

    positions = {vertex: index for index, vertex in enumerate(reference)}

    def sort_key(vertex: GroupVertex) -> tuple[float, float, str]:
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
        return (median_position, stable_y.get(vertex, 0.0), _vertex_key(vertex))

    layer_to_vertices[layer] = sorted(vertices, key=sort_key)


def _weighted_median(values: list[tuple[int, int]]) -> float:
    """Return a deterministic weighted median of integer layer positions."""
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


def _weighted_transpose_layers(
    layer_to_vertices: dict[int, list[GroupVertex]],
    edges: list[WeightedGroupEdge],
    max_layer: int,
    movable_group_ids: set[int],
) -> None:
    for _ in range(GROUP_TRANSPOSE_PASSES):
        improved = False
        for layer in range(max_layer + 1):
            vertices = layer_to_vertices.get(layer, [])
            if len(vertices) <= 1:
                continue
            index = 0
            while index < len(vertices) - 1:
                left = vertices[index]
                right = vertices[index + 1]
                if not _vertices_can_swap(left, right, movable_group_ids):
                    index += 1
                    continue

                before = _weighted_local_boundary_crossings(
                    layer_to_vertices, edges, layer, max_layer
                )
                vertices[index], vertices[index + 1] = right, left
                after = _weighted_local_boundary_crossings(
                    layer_to_vertices, edges, layer, max_layer
                )
                if after < before:
                    improved = True
                else:
                    vertices[index], vertices[index + 1] = left, right
                index += 1
        if not improved:
            break


def _vertices_can_swap(
    left: GroupVertex,
    right: GroupVertex,
    movable_group_ids: set[int],
) -> bool:
    for vertex in (left, right):
        if isinstance(vertex, int) and vertex not in movable_group_ids:
            return False
    return True


def _weighted_local_boundary_crossings(
    layer_to_vertices: dict[int, list[GroupVertex]],
    edges: list[WeightedGroupEdge],
    layer: int,
    max_layer: int,
) -> int:
    crossings = 0
    if layer > 0:
        crossings += _weighted_boundary_crossings(
            layer_to_vertices, edges, layer - 1, layer
        )
    if layer < max_layer:
        crossings += _weighted_boundary_crossings(
            layer_to_vertices, edges, layer, layer + 1
        )
    return crossings


def _weighted_boundary_crossings(
    layer_to_vertices: dict[int, list[GroupVertex]],
    edges: list[WeightedGroupEdge],
    left_layer: int,
    right_layer: int,
) -> int:
    """Count wire-multiplicity-weighted crossings at one layer boundary."""
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


def _apply_group_layer_order(
    workflow: Workflow,
    layers: dict[int, int],
    layer_order: dict[int, list[GroupVertex]],
    movable_group_ids: set[int],
    settings: LayoutSettings,
) -> bool:
    """Commit vertical group order while preserving each group's x coordinate."""
    group_by_id = {group.id: group for group in workflow.groups}
    changed = False

    for layer in sorted(layer_order):
        physical_groups = [
            group
            for group in workflow.groups
            if group.id in layers and layers[group.id] == layer and group.nodes
        ]
        if len(physical_groups) <= 1:
            continue
        if any(group.id not in movable_group_ids for group in physical_groups):
            continue

        current_groups = sorted(
            physical_groups,
            key=lambda group: (group.bounding[1], group.bounding[0], group.id),
        )
        current_ids = [group.id for group in current_groups]
        desired_ids = [
            vertex
            for vertex in layer_order[layer]
            if isinstance(vertex, int) and vertex in current_ids
        ]
        if len(desired_ids) != len(current_ids) or desired_ids == current_ids:
            continue

        gap = _infer_group_vertical_gap(current_groups, settings.group_v_gap)
        current_y = min(group.bounding[1] for group in current_groups)
        for group_id in desired_ids:
            group = group_by_id[group_id]
            delta_y = current_y - group.bounding[1]
            if abs(delta_y) > 1e-9:
                for node in group.nodes:
                    node.y += delta_y
                group.bounding[1] = current_y
            current_y += group.bounding[3] + gap
        changed = True

    return changed


def _infer_group_vertical_gap(groups: list[Group], fallback: float) -> float:
    if len(groups) <= 1:
        return fallback
    gaps = [
        next_group.bounding[1] - (group.bounding[1] + group.bounding[3])
        for group, next_group in zip(groups, groups[1:])
    ]
    non_negative = sorted(gap for gap in gaps if gap >= 0)
    if not non_negative:
        return fallback
    return non_negative[len(non_negative) // 2]


def _group_center_y(group: Group) -> float:
    if len(group.bounding) >= 4 and group.bounding[3] > 0:
        return group.bounding[1] + group.bounding[3] / 2.0
    if not group.nodes:
        return 0.0
    top = min(node.y for node in group.nodes)
    bottom = max(node.y + max(1.0, node.size[1] if len(node.size) > 1 else 1.0) for node in group.nodes)
    return top + (bottom - top) / 2.0


def _vertex_key(vertex: GroupVertex) -> str:
    return str(vertex)
