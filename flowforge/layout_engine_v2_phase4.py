"""Ungrouped-flow compaction for FlowForge layout engine v2.

Phase 4 keeps the complete Phase 3 result as a baseline and tries to compact
eligible linked ungrouped components into a shared horizontal band. Direct
group incidence is eligible, but groups remain fixed geometry and are not part
of the active compaction graph.

Diagnostics also build a read-only group-bridged connectivity graph that treats
groups as connector supernodes. This measures whether fixed groups are what
join otherwise fragmented ungrouped flow before a more invasive global graph is
considered.

The Phase 4 gate is intentionally strict: no additional crossings, RTL links,
or movable overlaps are allowed, workflow width must fall by at least 8%, and
workflow area may not increase.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from .layout import (
    LayoutSettings,
    _group_by_node_id,
    _has_nested_groups,
    _has_positive_bounding,
    _is_control_source_node,
    _is_decorative_node,
    _is_pinned_node,
    _is_terminal_text_preview_node,
    _is_virtual_hub_node,
    _node_visual_height,
    _node_visual_width,
    _wrap_layer_columns,
)
from .layout_engine_v2 import (
    EngineV2Score,
    _assign_scc_longest_path_layers,
    _finalize_refinement,
    _score_engine_v2,
)
from .layout_engine_v2_phase3 import apply_best_layout as _apply_phase3_best_layout
from .logger import setup_logger
from .model import Node, Workflow

logger = setup_logger(__name__)

UNGROUPED_COMPACT_MIN_COMPONENT_NODES = 3
UNGROUPED_COMPACT_TARGET_WORKFLOW_RATIO = 0.62
UNGROUPED_COMPACT_MIN_POTENTIAL_REDUCTION_RATIO = 0.10
UNGROUPED_COMPACT_MIN_ACCEPTED_WIDTH_REDUCTION_RATIO = 0.08
UNGROUPED_COMPACT_MAX_AREA_RATIO = 1.0


@dataclass(frozen=True)
class Phase4Diagnostics:
    """Explain whether conservative ungrouped compaction can affect a workflow."""

    total_ungrouped: int
    eligible_nodes: int
    excluded_pinned: int
    excluded_decorative: int
    excluded_virtual_hub: int
    excluded_control: int
    excluded_text_preview: int
    direct_group_nodes: int
    linked_components: int
    group_bridged_components: int
    largest_group_bridged_ungrouped_nodes: int
    largest_group_bridged_groups: int
    largest_group_bridged_span: float
    candidate_components: int
    compactable_components: int
    largest_component_nodes: int
    largest_component_span: float
    max_potential_reduction: float
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
    """Run Phase 3, then try conservative pure-ungrouped compaction."""
    settings = settings or LayoutSettings()
    baseline = _apply_phase3_best_layout(workflow, settings, candidate_count)
    if _has_nested_groups(baseline):
        logger.info("Skipping Phase 4 compaction for nested group hierarchy")
        return baseline
    baseline_score = _score_engine_v2(baseline)

    compact = deepcopy(baseline)
    if not _compact_pure_ungrouped_components(compact, settings, baseline_score.width):
        return baseline

    _finalize_refinement(compact, settings)
    compact_score = _score_engine_v2(compact)
    if _ungrouped_candidate_is_better(compact_score, baseline_score):
        logger.info(
            "Phase 4 ungrouped compaction accepted: "
            "size %.0fx%.0f -> %.0fx%.0f, crossings=%s, rtl=%s",
            baseline_score.width,
            baseline_score.height,
            compact_score.width,
            compact_score.height,
            compact_score.crossings,
            compact_score.right_to_left_links,
        )
        return compact

    logger.info(
        "Phase 4 ungrouped compaction rejected: "
        "size %.0fx%.0f -> %.0fx%.0f, crossings %s -> %s, rtl %s -> %s",
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


def diagnose_phase4(
    workflow: Workflow,
    settings: LayoutSettings | None = None,
) -> Phase4Diagnostics:
    """Return a read-only explanation of the Phase 4 decision for one layout."""
    settings = settings or LayoutSettings()
    baseline_score = _score_engine_v2(workflow)
    counts = _phase4_exclusion_counts(workflow)
    eligible = _eligible_ungrouped_nodes(workflow)
    eligible_by_id = {node.id: node for node in eligible}
    adjacency, undirected = _eligible_graph(workflow, eligible_by_id)

    linked_components = [
        component
        for component in _connected_components(undirected)
        if any(adjacency[node_id] for node_id in component)
    ]
    group_bridged_rows = _group_bridged_component_rows(workflow, eligible_by_id)
    largest_group_bridged = max(
        group_bridged_rows,
        key=lambda row: row[2],
        default=(set(), set(), 0.0),
    )
    candidate_components = [
        component
        for component in linked_components
        if len(component) >= UNGROUPED_COMPACT_MIN_COMPONENT_NODES
    ]

    component_rows: list[tuple[set[int], float, float]] = []
    for component in candidate_components:
        nodes = [eligible_by_id[node_id] for node_id in component]
        span = _component_span(nodes)
        potential = _component_potential_reduction(
            workflow,
            nodes,
            {node_id: adjacency[node_id] for node_id in component},
            baseline_score.width,
        )
        component_rows.append((component, span, potential))

    compactable = [
        row
        for row in component_rows
        if row[2] >= UNGROUPED_COMPACT_MIN_POTENTIAL_REDUCTION_RATIO
    ]
    largest = max(component_rows, key=lambda row: row[1], default=(set(), 0.0, 0.0))
    max_potential = max((row[2] for row in component_rows), default=0.0)

    compact = deepcopy(workflow)
    moved = _compact_pure_ungrouped_components(
        compact,
        settings,
        baseline_score.width,
    )
    if not moved:
        if len(eligible) < UNGROUPED_COMPACT_MIN_COMPONENT_NODES:
            reason = "too_few_eligible_nodes"
        elif not candidate_components:
            reason = "no_linked_component_with_min_nodes"
        elif not compactable:
            reason = "no_component_meets_potential_reduction_threshold"
        else:
            reason = "no_component_moved"
        return Phase4Diagnostics(
            total_ungrouped=len(workflow.ungrouped_nodes),
            eligible_nodes=len(eligible),
            linked_components=len(linked_components),
            group_bridged_components=len(group_bridged_rows),
            largest_group_bridged_ungrouped_nodes=len(largest_group_bridged[0]),
            largest_group_bridged_groups=len(largest_group_bridged[1]),
            largest_group_bridged_span=largest_group_bridged[2],
            candidate_components=len(candidate_components),
            compactable_components=len(compactable),
            largest_component_nodes=len(largest[0]),
            largest_component_span=largest[1],
            max_potential_reduction=max_potential,
            attempted=False,
            accepted=False,
            rejection_reason=reason,
            baseline_width=baseline_score.width,
            baseline_height=baseline_score.height,
            proposed_width=None,
            proposed_height=None,
            baseline_crossings=baseline_score.crossings,
            proposed_crossings=None,
            baseline_rtl=baseline_score.right_to_left_links,
            proposed_rtl=None,
            **counts,
        )

    _finalize_refinement(compact, settings)
    compact_score = _score_engine_v2(compact)
    accepted = _ungrouped_candidate_is_better(compact_score, baseline_score)
    return Phase4Diagnostics(
        total_ungrouped=len(workflow.ungrouped_nodes),
        eligible_nodes=len(eligible),
        linked_components=len(linked_components),
        group_bridged_components=len(group_bridged_rows),
        largest_group_bridged_ungrouped_nodes=len(largest_group_bridged[0]),
        largest_group_bridged_groups=len(largest_group_bridged[1]),
        largest_group_bridged_span=largest_group_bridged[2],
        candidate_components=len(candidate_components),
        compactable_components=len(compactable),
        largest_component_nodes=len(largest[0]),
        largest_component_span=largest[1],
        max_potential_reduction=max_potential,
        attempted=True,
        accepted=accepted,
        rejection_reason=(
            "accepted"
            if accepted
            else _ungrouped_rejection_reason(compact_score, baseline_score)
        ),
        baseline_width=baseline_score.width,
        baseline_height=baseline_score.height,
        proposed_width=compact_score.width,
        proposed_height=compact_score.height,
        baseline_crossings=baseline_score.crossings,
        proposed_crossings=compact_score.crossings,
        baseline_rtl=baseline_score.right_to_left_links,
        proposed_rtl=compact_score.right_to_left_links,
        **counts,
    )


def _phase4_exclusion_counts(workflow: Workflow) -> dict[str, int]:
    group_by_node_id = _group_by_node_id(workflow)
    counts = {
        "excluded_pinned": 0,
        "excluded_decorative": 0,
        "excluded_virtual_hub": 0,
        "excluded_control": 0,
        "excluded_text_preview": 0,
        "direct_group_nodes": 0,
    }
    for node in workflow.ungrouped_nodes:
        if node.id not in workflow.nodes:
            continue
        if _is_pinned_node(workflow, node):
            counts["excluded_pinned"] += 1
        elif _is_decorative_node(node):
            counts["excluded_decorative"] += 1
        elif _is_virtual_hub_node(node):
            counts["excluded_virtual_hub"] += 1
        elif _is_control_source_node(workflow, node):
            counts["excluded_control"] += 1
        elif _is_terminal_text_preview_node(node):
            counts["excluded_text_preview"] += 1
        elif _has_direct_group_incident_link(workflow, node, group_by_node_id):
            counts["direct_group_nodes"] += 1
    return counts


def _eligible_graph(
    workflow: Workflow,
    eligible_by_id: dict[int, Node],
) -> tuple[dict[int, list[int]], dict[int, set[int]]]:
    adjacency: dict[int, list[int]] = {node_id: [] for node_id in eligible_by_id}
    undirected: dict[int, set[int]] = {node_id: set() for node_id in eligible_by_id}
    for link in workflow.links.values():
        if link.source not in eligible_by_id or link.target not in eligible_by_id:
            continue
        adjacency[link.source].append(link.target)
        undirected[link.source].add(link.target)
        undirected[link.target].add(link.source)
    return adjacency, undirected


def _group_bridged_component_rows(
    workflow: Workflow,
    eligible_by_id: dict[int, Node],
) -> list[tuple[set[int], set[int], float]]:
    """Describe eligible-node connectivity when groups act as fixed connectors."""
    group_by_node_id = _group_by_node_id(workflow)
    graph: dict[tuple[str, int], set[tuple[str, int]]] = {
        ("node", node_id): set() for node_id in eligible_by_id
    }

    def endpoint_vertex(node_id: int) -> tuple[str, int] | None:
        if node_id in eligible_by_id:
            return ("node", node_id)
        group = group_by_node_id.get(node_id)
        if group is not None:
            return ("group", group.id)
        return None

    for link in workflow.links.values():
        source = endpoint_vertex(link.source)
        target = endpoint_vertex(link.target)
        if source is None or target is None or source == target:
            continue
        graph.setdefault(source, set()).add(target)
        graph.setdefault(target, set()).add(source)

    rows: list[tuple[set[int], set[int], float]] = []
    for component in _mixed_connected_components(graph):
        node_ids = {vertex_id for kind, vertex_id in component if kind == "node"}
        group_ids = {vertex_id for kind, vertex_id in component if kind == "group"}
        if not node_ids or not group_ids:
            continue
        rows.append(
            (
                node_ids,
                group_ids,
                _mixed_component_span(workflow, eligible_by_id, node_ids, group_ids),
            )
        )
    return rows


def _mixed_connected_components(
    undirected: dict[tuple[str, int], set[tuple[str, int]]],
) -> list[set[tuple[str, int]]]:
    components: list[set[tuple[str, int]]] = []
    unseen = set(undirected)
    while unseen:
        start = min(unseen)
        stack = [start]
        component: set[tuple[str, int]] = set()
        while stack:
            vertex = stack.pop()
            if vertex in component:
                continue
            component.add(vertex)
            unseen.discard(vertex)
            stack.extend(sorted(undirected[vertex] - component, reverse=True))
        components.append(component)
    return components


def _mixed_component_span(
    workflow: Workflow,
    eligible_by_id: dict[int, Node],
    node_ids: set[int],
    group_ids: set[int],
) -> float:
    bounds: list[tuple[float, float]] = []
    for node_id in node_ids:
        node = eligible_by_id[node_id]
        bounds.append((node.x, node.x + _node_visual_width(node)))

    groups_by_id = {group.id: group for group in workflow.groups}
    for group_id in group_ids:
        group = groups_by_id.get(group_id)
        if group is None or not _has_positive_bounding(group):
            continue
        bounds.append((group.bounding[0], group.bounding[0] + group.bounding[2]))

    if not bounds:
        return 0.0
    return max(right for _left, right in bounds) - min(left for left, _right in bounds)


def _component_potential_reduction(
    workflow: Workflow,
    nodes: list[Node],
    adjacency: dict[int, list[int]],
    workflow_width: float,
) -> float:
    current_span = _component_span(nodes)
    if current_span <= 0:
        return 0.0

    layers = _assign_scc_longest_path_layers({node.id for node in nodes}, adjacency)
    if len(set(layers.values())) <= 1:
        return 0.0

    layer_widths: dict[int, float] = {}
    for node in nodes:
        layer = layers[node.id]
        layer_widths[layer] = max(
            layer_widths.get(layer, 0.0),
            _node_visual_width(node),
        )

    max_layer_width = max(layer_widths.values(), default=0.0)
    target_cap = max(
        max_layer_width,
        min(
            current_span,
            max(
                _group_span(workflow),
                workflow_width * UNGROUPED_COMPACT_TARGET_WORKFLOW_RATIO,
            ),
        ),
    )
    return max(0.0, 1.0 - target_cap / current_span)


def _ungrouped_rejection_reason(
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
    if width_reduction < UNGROUPED_COMPACT_MIN_ACCEPTED_WIDTH_REDUCTION_RATIO:
        return "insufficient_final_width_reduction"

    baseline_area = baseline.width * baseline.height
    candidate_area = candidate.width * candidate.height
    if (
        baseline_area > 0
        and candidate_area > baseline_area * UNGROUPED_COMPACT_MAX_AREA_RATIO
    ):
        return "area_regression"
    return "accepted"


def _compact_pure_ungrouped_components(
    workflow: Workflow,
    settings: LayoutSettings,
    workflow_width: float,
) -> bool:
    """Compact eligible linked ungrouped components around fixed group geometry."""
    eligible = _eligible_ungrouped_nodes(workflow)
    if len(eligible) < UNGROUPED_COMPACT_MIN_COMPONENT_NODES:
        return False

    eligible_by_id = {node.id: node for node in eligible}
    adjacency, undirected = _eligible_graph(workflow, eligible_by_id)

    components = _connected_components(undirected)
    components = [
        component
        for component in components
        if len(component) >= UNGROUPED_COMPACT_MIN_COMPONENT_NODES
        and any(adjacency[node_id] for node_id in component)
    ]
    if not components:
        return False

    components.sort(
        key=lambda component: _component_span(
            [eligible_by_id[node_id] for node_id in component]
        ),
        reverse=True,
    )

    moved = False
    for component in components:
        nodes = [eligible_by_id[node_id] for node_id in component]
        if _compact_component(
            workflow,
            nodes,
            {node_id: adjacency[node_id] for node_id in component},
            settings,
            workflow_width,
        ):
            moved = True
    return moved


def _eligible_ungrouped_nodes(workflow: Workflow) -> list[Node]:
    """Return movable ungrouped nodes safe for candidate band compaction."""
    result: list[Node] = []
    for node in workflow.ungrouped_nodes:
        if node.id not in workflow.nodes:
            continue
        if (
            _is_pinned_node(workflow, node)
            or _is_decorative_node(node)
            or _is_virtual_hub_node(node)
            or _is_control_source_node(workflow, node)
            or _is_terminal_text_preview_node(node)
        ):
            continue
        # Direct group incidence is allowed in Phase 4. Those nodes are part of
        # the mixed group/ungrouped flow that causes the remaining width
        # regressions. Groups themselves remain fixed obstacles, and the global
        # acceptance gate rejects any crossing/RTL regression.
        result.append(node)
    return result


def _has_direct_group_incident_link(
    workflow: Workflow,
    node: Node,
    group_by_node_id,
) -> bool:
    for link_id in (*node.input_links, *node.output_links):
        link = workflow.links.get(link_id)
        if link is None:
            continue
        other_id = link.source if link.target == node.id else link.target
        if other_id in group_by_node_id:
            return True
    return False


def _connected_components(undirected: dict[int, set[int]]) -> list[set[int]]:
    components: list[set[int]] = []
    unseen = set(undirected)
    while unseen:
        start = min(unseen)
        stack = [start]
        component: set[int] = set()
        while stack:
            node_id = stack.pop()
            if node_id in component:
                continue
            component.add(node_id)
            unseen.discard(node_id)
            stack.extend(sorted(undirected[node_id] - component, reverse=True))
        components.append(component)
    return components


def _compact_component(
    workflow: Workflow,
    nodes: list[Node],
    adjacency: dict[int, list[int]],
    settings: LayoutSettings,
    workflow_width: float,
) -> bool:
    node_ids = {node.id for node in nodes}
    current_span = _component_span(nodes)
    if current_span <= 0:
        return False

    layers = _assign_scc_longest_path_layers(node_ids, adjacency)
    if len(set(layers.values())) <= 1:
        return False

    layer_to_nodes: dict[int, list[Node]] = {}
    for node in nodes:
        layer_to_nodes.setdefault(layers[node.id], []).append(node)

    layer_sizes: dict[int, tuple[float, float]] = {}
    for layer, layer_nodes in layer_to_nodes.items():
        width = max(_node_visual_width(node) for node in layer_nodes)
        height = sum(_node_visual_height(node) for node in layer_nodes)
        height += settings.node_v_gap * max(0, len(layer_nodes) - 1)
        layer_sizes[layer] = (width, height)

    max_layer_width = max(width for width, _height in layer_sizes.values())
    target_cap = max(
        max_layer_width,
        min(
            current_span,
            max(
                _group_span(workflow),
                workflow_width * UNGROUPED_COMPACT_TARGET_WORKFLOW_RATIO,
            ),
        ),
    )
    potential_reduction = _component_potential_reduction(
        workflow,
        nodes,
        adjacency,
        workflow_width,
    )
    if potential_reduction < UNGROUPED_COMPACT_MIN_POTENTIAL_REDUCTION_RATIO:
        return False

    graph_left = _graph_left(workflow)
    component_top = min(node.y for node in nodes)
    positions = _wrap_layer_columns(
        layer_sizes,
        target_cap,
        graph_left,
        component_top,
        settings.node_h_gap,
        settings.node_v_gap,
    )

    proposed: dict[int, tuple[float, float]] = {}
    for layer in sorted(layer_to_nodes):
        x, y = positions[layer]
        for node in sorted(
            layer_to_nodes[layer],
            key=lambda item: (item.y, item.x, item.id),
        ):
            proposed[node.id] = (x, y)
            y += _node_visual_height(node) + settings.node_v_gap

    obstacles = _component_obstacles(workflow, node_ids)
    vertical_shift = _resolve_component_vertical_shift(
        workflow,
        nodes,
        proposed,
        obstacles,
        settings,
    )

    changed = False
    for node in nodes:
        x, y = proposed[node.id]
        y += vertical_shift
        if abs(node.x - x) > 1e-9 or abs(node.y - y) > 1e-9:
            changed = True
        node.x = x
        node.y = y
    return changed


def _component_span(nodes: list[Node]) -> float:
    if not nodes:
        return 0.0
    left = min(node.x for node in nodes)
    right = max(node.x + _node_visual_width(node) for node in nodes)
    return max(0.0, right - left)


def _group_span(workflow: Workflow) -> float:
    groups = [
        group
        for group in workflow.groups
        if group.nodes and _has_positive_bounding(group)
    ]
    if not groups:
        return 0.0
    left = min(group.bounding[0] for group in groups)
    right = max(group.bounding[0] + group.bounding[2] for group in groups)
    return max(0.0, right - left)


def _graph_left(workflow: Workflow) -> float:
    candidates = [
        group.bounding[0]
        for group in workflow.groups
        if group.nodes and _has_positive_bounding(group)
    ]
    candidates.extend(
        node.x
        for node in workflow.nodes.values()
        if not _is_decorative_node(node)
    )
    return min(candidates, default=50.0)


def _component_obstacles(
    workflow: Workflow,
    component_node_ids: set[int],
) -> list[tuple[float, float, float, float]]:
    rects: list[tuple[float, float, float, float]] = []
    rects.extend(
        (
            group.bounding[0],
            group.bounding[1],
            group.bounding[0] + group.bounding[2],
            group.bounding[1] + group.bounding[3],
        )
        for group in workflow.groups
        if group.nodes and _has_positive_bounding(group)
    )
    rects.extend(
        (
            node.x,
            node.y,
            node.x + _node_visual_width(node),
            node.y + _node_visual_height(node),
        )
        for node in workflow.nodes.values()
        if node.id not in component_node_ids and not _is_decorative_node(node)
    )
    return rects


def _resolve_component_vertical_shift(
    workflow: Workflow,
    nodes: list[Node],
    proposed: dict[int, tuple[float, float]],
    obstacles: list[tuple[float, float, float, float]],
    settings: LayoutSettings,
) -> float:
    del workflow
    shift = 0.0
    gap = max(settings.node_v_gap, 12.0)

    for _ in range(len(obstacles) + len(nodes) + 1):
        required = shift
        collision_found = False
        for node in nodes:
            x, y = proposed[node.id]
            rect = (
                x,
                y + shift,
                x + _node_visual_width(node),
                y + shift + _node_visual_height(node),
            )
            for obstacle in obstacles:
                if not _rects_overlap(rect, obstacle):
                    continue
                collision_found = True
                required = max(required, obstacle[3] + gap - y)
        if not collision_found or required <= shift:
            return shift
        shift = required

    return shift


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


def _ungrouped_candidate_is_better(
    candidate: EngineV2Score,
    baseline: EngineV2Score,
) -> bool:
    """Require graph-quality preservation plus a real compactness gain."""
    if candidate.movable_overlaps > baseline.movable_overlaps:
        return False
    if candidate.crossings > baseline.crossings:
        return False
    if candidate.right_to_left_links > baseline.right_to_left_links:
        return False

    if baseline.width <= 0:
        return False
    width_reduction = 1.0 - candidate.width / baseline.width
    if width_reduction < UNGROUPED_COMPACT_MIN_ACCEPTED_WIDTH_REDUCTION_RATIO:
        return False

    baseline_area = baseline.width * baseline.height
    candidate_area = candidate.width * candidate.height
    return (
        baseline_area <= 0
        or candidate_area <= baseline_area * UNGROUPED_COMPACT_MAX_AREA_RATIO
    )
