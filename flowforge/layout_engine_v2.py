"""Second-generation layout engine for ComfyUI FlowForge.

The v2 engine keeps the mature placement and special-case handling from
``flowforge.layout`` but changes how layout candidates are selected and adds a
structural crossing-refinement stage for movable group internals.

Key differences from the legacy engine:

- candidate selection uses actual input/output port geometry instead of node
  centres;
- cyclic subgraphs are condensed into strongly connected components before
  assigning layers;
- long edges are expanded with virtual dummy vertices while ordering layers so
  they influence every layer they pass through;
- repeated directional median sweeps plus local transposition replace the
  single forward/backward barycenter pass as the final crossing refinement;
- every refined candidate is compared with its unrefined counterpart and is
  only kept when the v2 quality score improves.

Dummy vertices exist only in the in-memory ordering graph. They are never
written into the ComfyUI workflow.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from statistics import median
from typing import Hashable

from .layout import (
    LayoutReport,
    LayoutScore,
    LayoutSettings,
    _apply_layout_pass,
    _assign_groups,
    _assign_virtual_hub_groups_to_endpoints,
    _build_layout_candidates,
    _compress_debug_sidecar_layers,
    _group_bottom_padding,
    _group_flow_adjacency,
    _group_has_layout_content,
    _group_layout_size,
    _group_top_padding,
    _internal_layer_positions,
    _is_decorative_node,
    _is_pinned_node,
    _is_virtual_hub_node,
    _move_group_subtree,
    _nested_hierarchy_group_ids,
    _node_input_port_y,
    _node_output_port_y,
    _node_visual_height,
    _node_visual_width,
    _position_control_nodes_near_targets,
    _position_decorative_nodes_left,
    _position_text_previews_near_sources,
    _position_ungrouped_nodes,
    _position_virtual_set_get_nodes,
    _resolve_group_geometry_overlaps,
    _resolve_layout_candidate_count,
    _score_layout_candidate,
    _separate_unpinned_nodes_from_pinned_geometry,
    _shrink_nodes_to_minimum_size,
    _top_level_groups,
    _update_bounding_boxes,
)
from .logger import setup_logger
from .model import Group, Link, Workflow

logger = setup_logger(__name__)

V2_SWEEP_ROUNDS = 4
V2_TRANSPOSE_PASSES = 4
V2_CROSSING_WEIGHT = 2_000.0
V2_RIGHT_TO_LEFT_WEIGHT = 3_000.0
V2_OVERLAP_WEIGHT = 100_000.0
V2_WIDTH_WEIGHT = 2.5
V2_HEIGHT_WEIGHT = 1.0
V2_LINK_WEIGHT = 0.02
V2_ASPECT_WEIGHT = 2.5
V2_TARGET_ASPECT_RATIO = 1.35
V2_MAX_WIDTH_GROWTH_RATIO = 1.35
V2_SIGNIFICANT_CROSSING_GAIN_RATIO = 0.05
V2_SIGNIFICANT_CROSSING_GAIN_MIN = 8
V2_SIGNIFICANT_RTL_GAIN_RATIO = 0.10
V2_SIGNIFICANT_RTL_GAIN_MIN = 3

Vertex = Hashable


@dataclass(frozen=True)
class EngineV2Score:
    """Port-aware quality metrics used to select v2 layout candidates."""

    total: float
    crossings: int
    right_to_left_links: int
    movable_overlaps: int
    link_length: float
    width: float
    height: float


def apply(workflow: Workflow, settings: LayoutSettings | None = None) -> Workflow:
    """Apply the v2 layout engine using the normal adaptive candidate count."""
    return apply_best_layout(workflow, settings)


def apply_best_layout(
    workflow: Workflow,
    settings: LayoutSettings | None = None,
    candidate_count: int | None = None,
) -> Workflow:
    """Evaluate legacy placement candidates plus the v2 structural refiner."""
    settings = settings or LayoutSettings()
    resolved_candidate_count = (
        _resolve_layout_candidate_count(workflow)
        if candidate_count is None
        else max(1, int(candidate_count))
    )
    variants = _build_layout_candidates(workflow, settings, resolved_candidate_count)

    # Keep the authored geometry in the candidate pool as a safety baseline,
    # but also build a structural candidate that actually rearranges top-level
    # groups.  It uses SCC-aware flow layers while preserving nested group
    # subtrees as authored units.
    authored = deepcopy(workflow)
    best_workflow: Workflow | None = authored
    best_score: EngineV2Score | None = _score_engine_v2(authored)
    best_index = 1

    balanced = _balanced_group_flow_candidate(workflow, settings)
    total_candidates = len(variants) + 1 + (1 if balanced is not None else 0)
    logger.info(
        "v2 candidate 1/%s source=authored score=%.2f crossings=%s rtl=%s overlaps=%s "
        "link=%.0f size=%.0fx%.0f aspect_cost=%.0f",
        total_candidates,
        best_score.total,
        best_score.crossings,
        best_score.right_to_left_links,
        best_score.movable_overlaps,
        best_score.link_length,
        best_score.width,
        best_score.height,
        _aspect_cost(best_score.width, best_score.height),
    )

    variant_start = 2
    if balanced is not None:
        balanced_score = _score_engine_v2(balanced)
        logger.info(
            "v2 candidate 2/%s source=balanced-compact score=%.2f crossings=%s rtl=%s "
            "overlaps=%s link=%.0f size=%.0fx%.0f aspect_cost=%.0f",
            total_candidates,
            balanced_score.total,
            balanced_score.crossings,
            balanced_score.right_to_left_links,
            balanced_score.movable_overlaps,
            balanced_score.link_length,
            balanced_score.width,
            balanced_score.height,
            _aspect_cost(balanced_score.width, balanced_score.height),
        )
        if _candidate_is_better(balanced_score, best_score):
            best_workflow = balanced
            best_score = balanced_score
            best_index = 2
        variant_start = 3

    for index, variant in enumerate(variants, start=variant_start):
        baseline = deepcopy(workflow)
        _apply_layout_pass(baseline, variant, log=False)
        baseline_score = _score_engine_v2(baseline)

        refined = deepcopy(baseline)
        changed = _refine_movable_group_internals(refined, variant)
        candidate_source = "baseline"
        if changed:
            _finalize_refinement(refined, variant)
            refined_score = _score_engine_v2(refined)
            if _candidate_is_better(refined_score, baseline_score):
                candidate = refined
                candidate_score = refined_score
                candidate_source = "refined"
            else:
                candidate = baseline
                candidate_score = baseline_score
        else:
            candidate = baseline
            candidate_score = baseline_score

        logger.info(
            "v2 candidate %s/%s source=%s score=%.2f crossings=%s rtl=%s overlaps=%s "
            "link=%.0f size=%.0fx%.0f aspect_cost=%.0f",
            index,
            total_candidates,
            candidate_source,
            candidate_score.total,
            candidate_score.crossings,
            candidate_score.right_to_left_links,
            candidate_score.movable_overlaps,
            candidate_score.link_length,
            candidate_score.width,
            candidate_score.height,
            _aspect_cost(candidate_score.width, candidate_score.height),
        )

        if best_score is None or _candidate_is_better(candidate_score, best_score):
            best_workflow = candidate
            best_score = candidate_score
            best_index = index

    if best_workflow is None or best_score is None:
        raise RuntimeError("Layout engine v2 did not produce a candidate")

    legacy_score: LayoutScore = _score_layout_candidate(best_workflow)
    best_workflow.layout_report = LayoutReport(
        candidate_count=total_candidates,
        selected_candidate=best_index,
        score=legacy_score,
    )
    logger.info(
        "Layout engine v2 selected candidate %s/%s: score=%.2f crossings=%s rtl=%s "
        "overlaps=%s link=%.0f size=%.0fx%.0f aspect_cost=%.0f",
        best_index,
        total_candidates,
        best_score.total,
        best_score.crossings,
        best_score.right_to_left_links,
        best_score.movable_overlaps,
        best_score.link_length,
        best_score.width,
        best_score.height,
        _aspect_cost(best_score.width, best_score.height),
    )
    return best_workflow



def _balanced_group_flow_candidate(
    workflow: Workflow,
    settings: LayoutSettings,
) -> Workflow | None:
    """Compact authored geometry horizontally without destroying its structure.

    Real ComfyUI workflows can contain feedback edges between functional areas.
    Treating every strongly-connected group component as one graph layer stacks
    otherwise well-arranged groups vertically and creates a tower.  This
    candidate instead keeps the author's vertical bands and left-to-right item
    order, then removes avoidable horizontal whitespace.

    Top-level groups move as complete subtrees, so nested groups keep their
    exact relative geometry. Ungrouped bridge/dataflow nodes participate in
    the same packing pass instead of becoming obstacles afterwards.
    """
    candidate = deepcopy(workflow)
    _shrink_nodes_to_minimum_size(candidate)
    _assign_groups(candidate)

    top_groups = [
        group
        for group in _top_level_groups(candidate)
        if _group_has_layout_content(candidate, group)
    ]
    if not top_groups:
        return None

    if any(group.pinned for group in top_groups) or any(
        _is_pinned_node(candidate, node) for node in candidate.nodes.values()
    ):
        return None

    decorative_right_edge = _position_decorative_nodes_left(candidate, settings)
    start_x = max(50.0, decorative_right_edge + settings.group_h_gap)

    # Items are packed in authored X order.  Only items whose vertical spans
    # overlap need horizontal separation; items in different authored rows may
    # share the same X band.
    items: list[tuple[str, int, float, float, float, float]] = []
    for group in top_groups:
        if (
            len(group.bounding) >= 4
            and group.bounding[2] > 0
            and group.bounding[3] > 0
        ):
            x, y, width, height = group.bounding
        else:
            width, height = _group_layout_size(candidate, group, settings)
            member_nodes = [
                node
                for item in candidate.groups
                if item.id == group.id
                for node in item.nodes
            ]
            if not member_nodes:
                continue
            x = min(node.x for node in member_nodes)
            y = min(node.y for node in member_nodes)
        items.append(("group", group.id, x, y, width, height))

    for node in candidate.ungrouped_nodes:
        if _is_decorative_node(node) or _is_virtual_hub_node(node):
            continue
        items.append(
            (
                "node",
                node.id,
                node.x,
                node.y,
                _node_visual_width(node),
                _node_visual_height(node),
            )
        )

    if len(items) < 2:
        return None

    items.sort(key=lambda item: (item[2], item[3], item[0], item[1]))
    groups_by_id = {group.id: group for group in candidate.groups}
    placed: list[tuple[str, int, float, float, float, float]] = []

    for kind, item_id, _old_x, y, width, height in items:
        target_x = start_x
        for other_kind, _other_id, other_x, other_y, other_width, other_height in placed:
            vertical_gap = (
                settings.group_v_gap
                if "group" in {kind, other_kind}
                else settings.node_v_gap
            )
            vertically_overlaps = (
                y < other_y + other_height + vertical_gap
                and other_y < y + height + vertical_gap
            )
            if not vertically_overlaps:
                continue

            horizontal_gap = (
                settings.group_h_gap
                if "group" in {kind, other_kind}
                else settings.node_h_gap
            )
            target_x = max(target_x, other_x + other_width + horizontal_gap)

        if kind == "group":
            group = groups_by_id[item_id]
            _move_group_subtree(
                candidate,
                group,
                target_x - group.bounding[0],
                0.0,
            )
        else:
            candidate.nodes[item_id].x = target_x

        placed.append((kind, item_id, target_x, y, width, height))

    _finalize_refinement(candidate, settings)
    return candidate


def _candidate_is_better(candidate: EngineV2Score, incumbent: EngineV2Score) -> bool:
    """Prefer quality gains without allowing small gains to explode workflow width."""
    if candidate.movable_overlaps != incumbent.movable_overlaps:
        return candidate.movable_overlaps < incumbent.movable_overlaps

    crossing_gain = incumbent.crossings - candidate.crossings
    rtl_gain = incumbent.right_to_left_links - candidate.right_to_left_links
    significant_crossing_gain = (
        candidate.crossings == 0 < incumbent.crossings
        or crossing_gain
        >= max(
            V2_SIGNIFICANT_CROSSING_GAIN_MIN,
            math.ceil(incumbent.crossings * V2_SIGNIFICANT_CROSSING_GAIN_RATIO),
        )
    )
    significant_rtl_gain = (
        candidate.right_to_left_links == 0 < incumbent.right_to_left_links
        or rtl_gain
        >= max(
            V2_SIGNIFICANT_RTL_GAIN_MIN,
            math.ceil(incumbent.right_to_left_links * V2_SIGNIFICANT_RTL_GAIN_RATIO),
        )
    )

    if (
        incumbent.width > 0
        and candidate.width > incumbent.width * V2_MAX_WIDTH_GROWTH_RATIO
        and not significant_crossing_gain
        and not significant_rtl_gain
    ):
        return False

    return candidate.total < incumbent.total

def _refine_movable_group_internals(workflow: Workflow, settings: LayoutSettings) -> bool:
    """Re-layout fully movable groups with SCC-aware, dummy-edge ordering."""
    changed = False
    nested_group_ids = _nested_hierarchy_group_ids(workflow)
    for group in workflow.groups:
        if group.id in nested_group_ids or not _group_can_be_refined(workflow, group):
            continue
        if _refine_group(workflow, group, settings):
            changed = True
    return changed


def _group_can_be_refined(workflow: Workflow, group: Group) -> bool:
    if group.pinned or len(group.nodes) < 2:
        return False
    return not any(
        _is_pinned_node(workflow, node)
        or _is_decorative_node(node)
        or _is_virtual_hub_node(node)
        for node in group.nodes
    )


def _refine_group(workflow: Workflow, group: Group, settings: LayoutSettings) -> bool:
    node_ids = {node.id for node in group.nodes}
    adj: dict[int, list[int]] = {node_id: [] for node_id in node_ids}
    rev_adj: dict[int, list[int]] = {node_id: [] for node_id in node_ids}

    internal_links: list[Link] = []
    for link in workflow.links.values():
        if link.source in node_ids and link.target in node_ids:
            adj[link.source].append(link.target)
            rev_adj[link.target].append(link.source)
            internal_links.append(link)

    if not internal_links:
        return False

    layers = _assign_scc_longest_path_layers(node_ids, adj)
    _compress_debug_sidecar_layers(layers, adj, rev_adj, workflow)
    if len(set(layers.values())) <= 1:
        return False

    layer_order = _minimize_crossings_with_dummies(workflow, layers, internal_links)
    if not layer_order:
        return False

    base_x = group.bounding[0] + settings.group_padding
    base_y = group.bounding[1] + _group_top_padding(settings)
    max_layer = max(layer_order)
    physical_layers = {
        layer: [
            node_id
            for node_id in layer_order.get(layer, [])
            if isinstance(node_id, int)
        ]
        for layer in range(max_layer + 1)
    }
    layer_positions = _internal_layer_positions(
        workflow,
        physical_layers,
        max_layer,
        base_x,
        base_y,
        settings,
    )

    placed_nodes = []
    for layer in range(max_layer + 1):
        node_order = physical_layers[layer]
        if not node_order:
            continue

        layer_x, current_y = layer_positions[layer]
        for node_id in node_order:
            node = workflow.nodes[node_id]
            node.x = layer_x
            node.y = current_y
            placed_nodes.append(node)
            current_y += _node_visual_height(node) + settings.node_v_gap

    if not placed_nodes:
        return False

    content_right = max(node.x + _node_visual_width(node) for node in placed_nodes)
    content_bottom = max(node.y + _node_visual_height(node) for node in placed_nodes)
    group.bounding = [
        group.bounding[0],
        group.bounding[1],
        max(0.0, content_right - base_x) + 2.0 * settings.group_padding,
        max(0.0, content_bottom - base_y)
        + _group_top_padding(settings)
        + _group_bottom_padding(settings),
    ]
    return True


def _assign_scc_longest_path_layers(
    node_ids: set[int],
    adj: dict[int, list[int]],
) -> dict[int, int]:
    """Assign longest-path layers on a DAG of strongly connected components."""
    components = _tarjan_components(node_ids, adj)
    component_by_node: dict[int, int] = {}
    for component_index, component in enumerate(components):
        for node_id in component:
            component_by_node[node_id] = component_index

    component_adj: dict[int, set[int]] = {index: set() for index in range(len(components))}
    indegree = {index: 0 for index in range(len(components))}
    for source_id in node_ids:
        source_component = component_by_node[source_id]
        for target_id in adj.get(source_id, []):
            target_component = component_by_node[target_id]
            if source_component == target_component:
                continue
            if target_component not in component_adj[source_component]:
                component_adj[source_component].add(target_component)
                indegree[target_component] += 1

    component_layer = {index: 0 for index, degree in indegree.items() if degree == 0}
    queue = sorted(component_layer)
    mutable_indegree = dict(indegree)
    while queue:
        component_id = queue.pop(0)
        for target in sorted(component_adj[component_id]):
            component_layer[target] = max(
                component_layer.get(target, 0),
                component_layer[component_id] + 1,
            )
            mutable_indegree[target] -= 1
            if mutable_indegree[target] == 0:
                queue.append(target)
                queue.sort()

    return {
        node_id: component_layer.get(component_by_node[node_id], 0)
        for node_id in node_ids
    }


def _tarjan_components(
    node_ids: set[int],
    adj: dict[int, list[int]],
) -> list[list[int]]:
    """Return deterministic strongly connected components using Tarjan's algorithm."""
    index = 0
    indices: dict[int, int] = {}
    lowlink: dict[int, int] = {}
    stack: list[int] = []
    on_stack: set[int] = set()
    components: list[list[int]] = []

    def strongconnect(node_id: int) -> None:
        nonlocal index
        indices[node_id] = index
        lowlink[node_id] = index
        index += 1
        stack.append(node_id)
        on_stack.add(node_id)

        for target_id in sorted(adj.get(node_id, [])):
            if target_id not in indices:
                strongconnect(target_id)
                lowlink[node_id] = min(lowlink[node_id], lowlink[target_id])
            elif target_id in on_stack:
                lowlink[node_id] = min(lowlink[node_id], indices[target_id])

        if lowlink[node_id] != indices[node_id]:
            return

        component: list[int] = []
        while stack:
            current = stack.pop()
            on_stack.remove(current)
            component.append(current)
            if current == node_id:
                break
        components.append(sorted(component))

    for node_id in sorted(node_ids):
        if node_id not in indices:
            strongconnect(node_id)

    return components


def _minimize_crossings_with_dummies(
    workflow: Workflow,
    layers: dict[int, int],
    links: list[Link],
) -> dict[int, list[Vertex]]:
    """Order real and virtual vertices with repeated median sweeps and transpose."""
    max_layer = max(layers.values(), default=0)
    layer_to_vertices: dict[int, list[Vertex]] = {layer: [] for layer in range(max_layer + 1)}
    stable_y: dict[Vertex, float] = {}

    for node_id, layer in layers.items():
        layer_to_vertices.setdefault(layer, []).append(node_id)
        stable_y[node_id] = workflow.nodes[node_id].y

    expanded_edges: list[tuple[Vertex, Vertex]] = []
    for link in sorted(links, key=lambda item: item.id):
        source_layer = layers[link.source]
        target_layer = layers[link.target]
        if target_layer <= source_layer:
            continue

        previous: Vertex = link.source
        source_y = workflow.nodes[link.source].y
        target_y = workflow.nodes[link.target].y
        span = target_layer - source_layer
        for layer in range(source_layer + 1, target_layer):
            dummy: Vertex = ("dummy", link.id, layer)
            layer_to_vertices.setdefault(layer, []).append(dummy)
            fraction = (layer - source_layer) / span
            stable_y[dummy] = source_y + (target_y - source_y) * fraction
            expanded_edges.append((previous, dummy))
            previous = dummy
        expanded_edges.append((previous, link.target))

    for layer, vertices in layer_to_vertices.items():
        vertices.sort(key=lambda vertex: (_vertex_bypassed(workflow, vertex), stable_y.get(vertex, 0.0), _vertex_key(vertex)))

    predecessors: dict[Vertex, list[Vertex]] = {}
    successors: dict[Vertex, list[Vertex]] = {}
    for source, target in expanded_edges:
        successors.setdefault(source, []).append(target)
        predecessors.setdefault(target, []).append(source)

    for _ in range(V2_SWEEP_ROUNDS):
        for layer in range(1, max_layer + 1):
            _median_reorder_layer(
                workflow,
                layer_to_vertices,
                layer,
                predecessors,
                reference_layer=layer - 1,
                stable_y=stable_y,
            )
        for layer in range(max_layer - 1, -1, -1):
            _median_reorder_layer(
                workflow,
                layer_to_vertices,
                layer,
                successors,
                reference_layer=layer + 1,
                stable_y=stable_y,
            )
        _transpose_layers(layer_to_vertices, expanded_edges, max_layer)

    return layer_to_vertices


def _median_reorder_layer(
    workflow: Workflow,
    layer_to_vertices: dict[int, list[Vertex]],
    layer: int,
    neighbours: dict[Vertex, list[Vertex]],
    *,
    reference_layer: int,
    stable_y: dict[Vertex, float],
) -> None:
    vertices = layer_to_vertices.get(layer, [])
    reference = layer_to_vertices.get(reference_layer, [])
    if len(vertices) <= 1 or not reference:
        return

    positions = {vertex: index for index, vertex in enumerate(reference)}

    def sort_key(vertex: Vertex) -> tuple[float, float, float, str]:
        adjacent_positions = [
            positions[other]
            for other in neighbours.get(vertex, [])
            if other in positions
        ]
        barycenter = median(adjacent_positions) if adjacent_positions else math.inf
        return (
            float(_vertex_bypassed(workflow, vertex)),
            float(barycenter),
            stable_y.get(vertex, 0.0),
            _vertex_key(vertex),
        )

    layer_to_vertices[layer] = sorted(vertices, key=sort_key)


def _transpose_layers(
    layer_to_vertices: dict[int, list[Vertex]],
    expanded_edges: list[tuple[Vertex, Vertex]],
    max_layer: int,
) -> None:
    for _ in range(V2_TRANSPOSE_PASSES):
        improved = False
        for layer in range(max_layer + 1):
            vertices = layer_to_vertices.get(layer, [])
            if len(vertices) <= 1:
                continue
            index = 0
            while index < len(vertices) - 1:
                before = _local_boundary_crossings(layer_to_vertices, expanded_edges, layer, max_layer)
                vertices[index], vertices[index + 1] = vertices[index + 1], vertices[index]
                after = _local_boundary_crossings(layer_to_vertices, expanded_edges, layer, max_layer)
                if after < before:
                    improved = True
                    index += 1
                else:
                    vertices[index], vertices[index + 1] = vertices[index + 1], vertices[index]
                index += 1
        if not improved:
            break


def _local_boundary_crossings(
    layer_to_vertices: dict[int, list[Vertex]],
    edges: list[tuple[Vertex, Vertex]],
    layer: int,
    max_layer: int,
) -> int:
    crossings = 0
    if layer > 0:
        crossings += _boundary_crossings(layer_to_vertices, edges, layer - 1, layer)
    if layer < max_layer:
        crossings += _boundary_crossings(layer_to_vertices, edges, layer, layer + 1)
    return crossings


def _boundary_crossings(
    layer_to_vertices: dict[int, list[Vertex]],
    edges: list[tuple[Vertex, Vertex]],
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
        (source, target)
        for source, target in edges
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
                crossings += 1
    return crossings


def _vertex_bypassed(workflow: Workflow, vertex: Vertex) -> bool:
    return isinstance(vertex, int) and workflow.nodes[vertex].mode == 4


def _vertex_key(vertex: Vertex) -> str:
    return str(vertex)


def _finalize_refinement(workflow: Workflow, settings: LayoutSettings) -> None:
    """Re-run geometry contracts that can be affected by internal group movement."""
    _update_bounding_boxes(workflow, settings)
    _resolve_group_geometry_overlaps(workflow, settings)
    _separate_unpinned_nodes_from_pinned_geometry(workflow, settings)
    _position_control_nodes_near_targets(workflow, list(workflow.nodes.values()), settings)
    _position_text_previews_near_sources(workflow, settings)
    _position_virtual_set_get_nodes(workflow, settings)
    _assign_virtual_hub_groups_to_endpoints(workflow)
    _update_bounding_boxes(workflow, settings)
    _resolve_group_geometry_overlaps(workflow, settings)
    _separate_unpinned_nodes_from_pinned_geometry(workflow, settings)


def _aspect_cost(width: float, height: float) -> float:
    """Penalize both overly wide and overly tall workflow shapes.

    The previous ratio-only penalty was one-sided: layouts narrower than the
    target aspect ratio paid no aspect cost at all. Because width is weighted
    more heavily than height in the main score, that made very tall "tower"
    layouts artificially attractive. Scale the symmetric deviation in pixels
    so the penalty grows with the size of the canvas.
    """
    if width <= 0.0 or height <= 0.0:
        return 0.0

    target_width = height * V2_TARGET_ASPECT_RATIO
    return abs(width - target_width) * V2_ASPECT_WEIGHT


def _score_engine_v2(workflow: Workflow) -> EngineV2Score:
    segments = _port_segments(workflow)
    crossings = _segment_crossing_count(segments)
    right_to_left = sum(1 for _source, _target, start, end in segments if end[0] < start[0])
    link_length = sum(
        abs(end[0] - start[0]) + abs(end[1] - start[1])
        for _source, _target, start, end in segments
    )
    overlaps = _movable_overlap_count(workflow)
    left, top, right, bottom = _workflow_bounds(workflow)
    width = max(0.0, right - left)
    height = max(0.0, bottom - top)
    aspect_cost = _aspect_cost(width, height)

    total = (
        crossings * V2_CROSSING_WEIGHT
        + right_to_left * V2_RIGHT_TO_LEFT_WEIGHT
        + overlaps * V2_OVERLAP_WEIGHT
        + link_length * V2_LINK_WEIGHT
        + width * V2_WIDTH_WEIGHT
        + height * V2_HEIGHT_WEIGHT
        + aspect_cost
    )
    return EngineV2Score(
        total=total,
        crossings=crossings,
        right_to_left_links=right_to_left,
        movable_overlaps=overlaps,
        link_length=link_length,
        width=width,
        height=height,
    )


def _port_segments(
    workflow: Workflow,
) -> list[tuple[int, int, tuple[float, float], tuple[float, float]]]:
    segments: list[tuple[int, int, tuple[float, float], tuple[float, float]]] = []
    for link in workflow.links.values():
        source = workflow.nodes.get(link.source)
        target = workflow.nodes.get(link.target)
        if source is None or target is None:
            continue
        start = (
            source.x + _node_visual_width(source),
            _node_output_port_y(source, link.source_port),
        )
        end = (
            target.x,
            _node_input_port_y(target, link.target_port),
        )
        segments.append((source.id, target.id, start, end))
    return segments


def _segment_crossing_count(
    segments: list[tuple[int, int, tuple[float, float], tuple[float, float]]],
) -> int:
    crossings = 0
    for index, first in enumerate(segments):
        for second in segments[index + 1 :]:
            if first[0] in {second[0], second[1]} or first[1] in {second[0], second[1]}:
                continue
            if _segments_intersect(first[2], first[3], second[2], second[3]):
                crossings += 1
    return crossings


def _segments_intersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    if a1 == b1 or a1 == b2 or a2 == b1 or a2 == b2:
        return False

    def orientation(
        p: tuple[float, float],
        q: tuple[float, float],
        r: tuple[float, float],
    ) -> float:
        return (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])

    o1 = orientation(a1, a2, b1)
    o2 = orientation(a1, a2, b2)
    o3 = orientation(b1, b2, a1)
    o4 = orientation(b1, b2, a2)
    return o1 * o2 < 0 and o3 * o4 < 0


def _movable_overlap_count(workflow: Workflow) -> int:
    movable = [
        node
        for node in workflow.nodes.values()
        if not _is_pinned_node(workflow, node)
        and not _is_decorative_node(node)
        and not _is_virtual_hub_node(node)
    ]
    overlaps = 0
    for index, first in enumerate(movable):
        first_rect = (
            first.x,
            first.y,
            first.x + _node_visual_width(first),
            first.y + _node_visual_height(first),
        )
        for second in movable[index + 1 :]:
            second_rect = (
                second.x,
                second.y,
                second.x + _node_visual_width(second),
                second.y + _node_visual_height(second),
            )
            if _rects_overlap(first_rect, second_rect):
                overlaps += 1
    return overlaps


def _rects_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return not (
        first[2] <= second[0]
        or second[2] <= first[0]
        or first[3] <= second[1]
        or second[3] <= first[1]
    )


def _workflow_bounds(workflow: Workflow) -> tuple[float, float, float, float]:
    boxes: list[tuple[float, float, float, float]] = []
    for node in workflow.nodes.values():
        boxes.append(
            (
                node.x,
                node.y,
                node.x + _node_visual_width(node),
                node.y + _node_visual_height(node),
            )
        )
    for group in workflow.groups:
        if len(group.bounding) < 4 or group.bounding[2] <= 0 or group.bounding[3] <= 0:
            continue
        boxes.append(
            (
                group.bounding[0],
                group.bounding[1],
                group.bounding[0] + group.bounding[2],
                group.bounding[1] + group.bounding[3],
            )
        )
    if not boxes:
        return 0.0, 0.0, 0.0, 0.0
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )