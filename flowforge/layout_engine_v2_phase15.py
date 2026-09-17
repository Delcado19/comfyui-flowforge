"""Boundary-aware refinement and crossing-first selection for layout engine v2.

This phase builds on :mod:`flowforge.layout_engine_v2` without changing its
SCC/dummy-vertex core. It adds fixed boundary anchors for links that enter or
leave a movable group and uses a crossing-first comparison policy when choosing
between layout candidates.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Hashable

from .layout import (
    LayoutReport,
    LayoutScore,
    LayoutSettings,
    _apply_layout_pass,
    _build_layout_candidates,
    _compress_debug_sidecar_layers,
    _group_bottom_padding,
    _group_top_padding,
    _node_input_port_y,
    _node_output_port_y,
    _node_visual_height,
    _node_visual_width,
    _resolve_layout_candidate_count,
    _score_layout_candidate,
)
from .layout_engine_v2 import (
    EngineV2Score,
    V2_SWEEP_ROUNDS,
    V2_TRANSPOSE_PASSES,
    _assign_scc_longest_path_layers,
    _boundary_crossings,
    _finalize_refinement,
    _group_can_be_refined,
    _median_reorder_layer,
    _port_segments,
    _score_engine_v2,
    _segments_intersect,
    _vertex_bypassed,
    _vertex_key,
)
from .logger import setup_logger
from .model import Group, Link, Workflow

logger = setup_logger(__name__)

Vertex = Hashable


def apply_best_layout(
    workflow: Workflow,
    settings: LayoutSettings | None = None,
    candidate_count: int | None = None,
) -> Workflow:
    """Select a layout with boundary-aware refinement and crossing-first gates."""
    settings = settings or LayoutSettings()
    resolved_candidate_count = (
        _resolve_layout_candidate_count(workflow)
        if candidate_count is None
        else max(1, int(candidate_count))
    )
    variants = _build_layout_candidates(workflow, settings, resolved_candidate_count)

    best_workflow: Workflow | None = None
    best_score: EngineV2Score | None = None
    best_index = 0

    for index, variant in enumerate(variants, start=1):
        baseline = deepcopy(workflow)
        _apply_layout_pass(baseline, variant, log=False)
        baseline_score = _score_engine_v2(baseline)

        refined = deepcopy(baseline)
        changed = _refine_movable_group_internals_boundary_aware(refined, variant)
        if changed:
            _finalize_refinement(refined, variant)
            refined_score = _score_engine_v2(refined)
            if _score_order_key(refined_score) < _score_order_key(baseline_score):
                candidate = refined
                candidate_score = refined_score
            else:
                candidate = baseline
                candidate_score = baseline_score
        else:
            candidate = baseline
            candidate_score = baseline_score

        logger.debug(
            "v2.1 candidate %s/%s crossings=%s rtl=%s overlaps=%s link=%.2f size=%.0fx%.0f",
            index,
            len(variants),
            candidate_score.crossings,
            candidate_score.right_to_left_links,
            candidate_score.movable_overlaps,
            candidate_score.link_length,
            candidate_score.width,
            candidate_score.height,
        )

        if best_score is None or _score_order_key(candidate_score) < _score_order_key(best_score):
            best_workflow = candidate
            best_score = candidate_score
            best_index = index

    if best_workflow is None or best_score is None:
        raise RuntimeError("Layout engine v2.1 did not produce a candidate")

    legacy_score: LayoutScore = _score_layout_candidate(best_workflow)
    best_workflow.layout_report = LayoutReport(
        candidate_count=len(variants),
        selected_candidate=best_index,
        score=legacy_score,
    )
    logger.info(
        "Layout engine v2.1 selected candidate %s/%s: crossings=%s rtl=%s overlaps=%s",
        best_index,
        len(variants),
        best_score.crossings,
        best_score.right_to_left_links,
        best_score.movable_overlaps,
    )
    return best_workflow


def _score_order_key(score: EngineV2Score) -> tuple[int, int, int, float, float, float, float]:
    """Return the lexicographic candidate order used by the v2.1 safety gate.

    Geometry safety is evaluated first, then physical crossings, then
    right-to-left links. Cable length and compactness only break ties after the
    structural metrics are equal.
    """
    area = score.width * score.height
    return (
        score.movable_overlaps,
        score.crossings,
        score.right_to_left_links,
        score.link_length,
        area,
        score.width + score.height,
        score.total,
    )


def _refine_movable_group_internals_boundary_aware(
    workflow: Workflow,
    settings: LayoutSettings,
) -> bool:
    """Refine movable groups while treating external endpoints as fixed anchors."""
    changed = False
    for group in workflow.groups:
        if not _group_can_be_refined(workflow, group):
            continue
        if _refine_group_boundary_aware(workflow, group, settings):
            changed = True
    return changed


def _refine_group_boundary_aware(
    workflow: Workflow,
    group: Group,
    settings: LayoutSettings,
) -> bool:
    """Refine one group without allowing its incident crossing count to regress."""
    node_ids = {node.id for node in group.nodes}
    adj: dict[int, list[int]] = {node_id: [] for node_id in node_ids}
    rev_adj: dict[int, list[int]] = {node_id: [] for node_id in node_ids}
    internal_links: list[Link] = []
    boundary_links: list[Link] = []

    for link in workflow.links.values():
        source_inside = link.source in node_ids
        target_inside = link.target in node_ids
        if source_inside and target_inside:
            adj[link.source].append(link.target)
            rev_adj[link.target].append(link.source)
            internal_links.append(link)
        elif source_inside != target_inside:
            boundary_links.append(link)

    if not internal_links:
        return False

    layers = _assign_scc_longest_path_layers(node_ids, adj)
    _compress_debug_sidecar_layers(layers, adj, rev_adj, workflow)
    if len(set(layers.values())) <= 1:
        return False

    layer_order = _minimize_crossings_with_boundaries(
        workflow,
        layers,
        internal_links,
        boundary_links,
    )
    if not layer_order:
        return False

    before_crossings = _group_incident_crossing_count(workflow, node_ids)
    original_positions = {
        node_id: (workflow.nodes[node_id].x, workflow.nodes[node_id].y)
        for node_id in node_ids
    }
    original_bounding = list(group.bounding)

    _place_group_order(workflow, group, settings, layer_order)
    after_crossings = _group_incident_crossing_count(workflow, node_ids)
    if after_crossings > before_crossings:
        for node_id, (x, y) in original_positions.items():
            workflow.nodes[node_id].x = x
            workflow.nodes[node_id].y = y
        group.bounding = original_bounding
        logger.debug(
            "Rejected boundary-aware refinement for group %s: incident crossings %s -> %s",
            group.id,
            before_crossings,
            after_crossings,
        )
        return False

    return any(
        (workflow.nodes[node_id].x, workflow.nodes[node_id].y) != original_positions[node_id]
        for node_id in node_ids
    )


def _minimize_crossings_with_boundaries(
    workflow: Workflow,
    layers: dict[int, int],
    internal_links: list[Link],
    boundary_links: list[Link],
) -> dict[int, list[Vertex]]:
    """Order group nodes with fixed external endpoint anchors on both boundaries."""
    max_layer = max(layers.values(), default=0)
    left_boundary_layer = -1
    right_boundary_layer = max_layer + 1
    layer_to_vertices: dict[int, list[Vertex]] = {
        layer: [] for layer in range(left_boundary_layer, right_boundary_layer + 1)
    }
    stable_y: dict[Vertex, float] = {}

    for node_id, layer in layers.items():
        layer_to_vertices[layer].append(node_id)
        stable_y[node_id] = workflow.nodes[node_id].y

    expanded_edges: list[tuple[Vertex, Vertex]] = []
    for link in sorted(internal_links, key=lambda item: item.id):
        source_layer = layers[link.source]
        target_layer = layers[link.target]
        if target_layer <= source_layer:
            continue
        _append_internal_edge_chain(
            workflow,
            layer_to_vertices,
            stable_y,
            expanded_edges,
            link,
            source_layer,
            target_layer,
        )

    for link in sorted(boundary_links, key=lambda item: item.id):
        source_inside = link.source in layers
        target_inside = link.target in layers
        if not source_inside and target_inside:
            _append_incoming_boundary_chain(
                workflow,
                layer_to_vertices,
                stable_y,
                expanded_edges,
                link,
                layers[link.target],
            )
        elif source_inside and not target_inside:
            _append_outgoing_boundary_chain(
                workflow,
                layer_to_vertices,
                stable_y,
                expanded_edges,
                link,
                layers[link.source],
                max_layer,
            )

    for layer, vertices in layer_to_vertices.items():
        if layer in {left_boundary_layer, right_boundary_layer}:
            vertices.sort(key=lambda vertex: (stable_y.get(vertex, 0.0), _vertex_key(vertex)))
        else:
            vertices.sort(
                key=lambda vertex: (
                    _vertex_bypassed(workflow, vertex),
                    stable_y.get(vertex, 0.0),
                    _vertex_key(vertex),
                )
            )

    predecessors: dict[Vertex, list[Vertex]] = {}
    successors: dict[Vertex, list[Vertex]] = {}
    for source, target in expanded_edges:
        successors.setdefault(source, []).append(target)
        predecessors.setdefault(target, []).append(source)

    for _ in range(V2_SWEEP_ROUNDS):
        for layer in range(0, max_layer + 1):
            _median_reorder_layer(
                workflow,
                layer_to_vertices,
                layer,
                predecessors,
                reference_layer=layer - 1,
                stable_y=stable_y,
            )
        for layer in range(max_layer, -1, -1):
            _median_reorder_layer(
                workflow,
                layer_to_vertices,
                layer,
                successors,
                reference_layer=layer + 1,
                stable_y=stable_y,
            )
        _transpose_layers_with_boundaries(
            layer_to_vertices,
            expanded_edges,
            max_layer,
        )

    return {layer: layer_to_vertices[layer] for layer in range(0, max_layer + 1)}


def _append_internal_edge_chain(
    workflow: Workflow,
    layer_to_vertices: dict[int, list[Vertex]],
    stable_y: dict[Vertex, float],
    expanded_edges: list[tuple[Vertex, Vertex]],
    link: Link,
    source_layer: int,
    target_layer: int,
) -> None:
    previous: Vertex = link.source
    source_y = _node_output_port_y(workflow.nodes[link.source], link.source_port)
    target_y = _node_input_port_y(workflow.nodes[link.target], link.target_port)
    span = target_layer - source_layer
    for layer in range(source_layer + 1, target_layer):
        dummy: Vertex = ("dummy", link.id, layer)
        layer_to_vertices[layer].append(dummy)
        fraction = (layer - source_layer) / span
        stable_y[dummy] = source_y + (target_y - source_y) * fraction
        expanded_edges.append((previous, dummy))
        previous = dummy
    expanded_edges.append((previous, link.target))


def _append_incoming_boundary_chain(
    workflow: Workflow,
    layer_to_vertices: dict[int, list[Vertex]],
    stable_y: dict[Vertex, float],
    expanded_edges: list[tuple[Vertex, Vertex]],
    link: Link,
    target_layer: int,
) -> None:
    source = workflow.nodes.get(link.source)
    target = workflow.nodes.get(link.target)
    if source is None or target is None:
        return

    anchor: Vertex = ("boundary-in", link.id)
    layer_to_vertices[-1].append(anchor)
    source_y = _node_output_port_y(source, link.source_port)
    target_y = _node_input_port_y(target, link.target_port)
    stable_y[anchor] = source_y
    previous: Vertex = anchor
    span = target_layer + 1

    for layer in range(0, target_layer):
        dummy: Vertex = ("boundary-in-dummy", link.id, layer)
        layer_to_vertices[layer].append(dummy)
        fraction = (layer + 1) / span
        stable_y[dummy] = source_y + (target_y - source_y) * fraction
        expanded_edges.append((previous, dummy))
        previous = dummy
    expanded_edges.append((previous, link.target))


def _append_outgoing_boundary_chain(
    workflow: Workflow,
    layer_to_vertices: dict[int, list[Vertex]],
    stable_y: dict[Vertex, float],
    expanded_edges: list[tuple[Vertex, Vertex]],
    link: Link,
    source_layer: int,
    max_layer: int,
) -> None:
    source = workflow.nodes.get(link.source)
    target = workflow.nodes.get(link.target)
    if source is None or target is None:
        return

    anchor: Vertex = ("boundary-out", link.id)
    right_boundary_layer = max_layer + 1
    layer_to_vertices[right_boundary_layer].append(anchor)
    source_y = _node_output_port_y(source, link.source_port)
    target_y = _node_input_port_y(target, link.target_port)
    stable_y[anchor] = target_y
    previous: Vertex = link.source
    span = right_boundary_layer - source_layer

    for layer in range(source_layer + 1, right_boundary_layer):
        dummy: Vertex = ("boundary-out-dummy", link.id, layer)
        layer_to_vertices[layer].append(dummy)
        fraction = (layer - source_layer) / span
        stable_y[dummy] = source_y + (target_y - source_y) * fraction
        expanded_edges.append((previous, dummy))
        previous = dummy
    expanded_edges.append((previous, anchor))


def _transpose_layers_with_boundaries(
    layer_to_vertices: dict[int, list[Vertex]],
    expanded_edges: list[tuple[Vertex, Vertex]],
    max_layer: int,
) -> None:
    """Transpose interior vertices while counting crossings at fixed boundaries."""
    for _ in range(V2_TRANSPOSE_PASSES):
        improved = False
        for layer in range(0, max_layer + 1):
            vertices = layer_to_vertices[layer]
            if len(vertices) <= 1:
                continue
            index = 0
            while index < len(vertices) - 1:
                before = _local_crossings_with_boundaries(
                    layer_to_vertices,
                    expanded_edges,
                    layer,
                    max_layer,
                )
                vertices[index], vertices[index + 1] = vertices[index + 1], vertices[index]
                after = _local_crossings_with_boundaries(
                    layer_to_vertices,
                    expanded_edges,
                    layer,
                    max_layer,
                )
                if after < before:
                    improved = True
                else:
                    vertices[index], vertices[index + 1] = vertices[index + 1], vertices[index]
                index += 1
        if not improved:
            break


def _local_crossings_with_boundaries(
    layer_to_vertices: dict[int, list[Vertex]],
    edges: list[tuple[Vertex, Vertex]],
    layer: int,
    max_layer: int,
) -> int:
    crossings = _boundary_crossings(layer_to_vertices, edges, layer - 1, layer)
    crossings += _boundary_crossings(layer_to_vertices, edges, layer, layer + 1)
    return crossings


def _place_group_order(
    workflow: Workflow,
    group: Group,
    settings: LayoutSettings,
    layer_order: dict[int, list[Vertex]],
) -> None:
    """Apply an ordered group-internal layering to physical node coordinates."""
    base_x = group.bounding[0] + settings.group_padding
    base_y = group.bounding[1] + _group_top_padding(settings)
    current_x = base_x
    max_bottom = base_y

    for layer in sorted(layer_order):
        node_order = [node_id for node_id in layer_order[layer] if isinstance(node_id, int)]
        if not node_order:
            continue

        layer_width = max(_node_visual_width(workflow.nodes[node_id]) for node_id in node_order)
        current_y = base_y
        for node_id in node_order:
            node = workflow.nodes[node_id]
            node.x = current_x
            node.y = current_y
            current_y += _node_visual_height(node) + settings.node_v_gap
        max_bottom = max(max_bottom, current_y - settings.node_v_gap)
        current_x += layer_width + settings.node_h_gap

    content_right = current_x - settings.node_h_gap
    group.bounding = [
        group.bounding[0],
        group.bounding[1],
        max(0.0, content_right - base_x) + 2.0 * settings.group_padding,
        max(0.0, max_bottom - base_y)
        + _group_top_padding(settings)
        + _group_bottom_padding(settings),
    ]


def _group_incident_crossing_count(workflow: Workflow, node_ids: set[int]) -> int:
    """Count physical crossings where at least one segment touches the group."""
    segments = _port_segments(workflow)
    crossings = 0
    for index, first in enumerate(segments):
        for second in segments[index + 1 :]:
            if not (
                first[0] in node_ids
                or first[1] in node_ids
                or second[0] in node_ids
                or second[1] in node_ids
            ):
                continue
            if first[0] in {second[0], second[1]} or first[1] in {second[0], second[1]}:
                continue
            if _segments_intersect(first[2], first[3], second[2], second[3]):
                crossings += 1
    return crossings
