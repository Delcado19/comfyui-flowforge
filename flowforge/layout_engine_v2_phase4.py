"""Ungrouped-flow compaction for FlowForge layout engine v2.

Phase 4 keeps the complete Phase 3 result as a baseline and tries to compact
pure linked ungrouped components into a shared horizontal band. Components that
are directly incident to groups or belong to established local-placement
special cases remain untouched.

The first Phase 4 gate is intentionally strict: no additional crossings, RTL
links, or movable overlaps are allowed.
"""

from __future__ import annotations

from copy import deepcopy

from .layout import (
    LayoutSettings,
    _group_by_node_id,
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


def apply_best_layout(
    workflow: Workflow,
    settings: LayoutSettings | None = None,
    candidate_count: int | None = None,
) -> Workflow:
    """Run Phase 3, then try conservative pure-ungrouped compaction."""
    settings = settings or LayoutSettings()
    baseline = _apply_phase3_best_layout(workflow, settings, candidate_count)
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


def _compact_pure_ungrouped_components(
    workflow: Workflow,
    settings: LayoutSettings,
    workflow_width: float,
) -> bool:
    """Compact linked ungrouped components that do not touch group geometry."""
    eligible = _eligible_ungrouped_nodes(workflow)
    if len(eligible) < UNGROUPED_COMPACT_MIN_COMPONENT_NODES:
        return False

    eligible_by_id = {node.id: node for node in eligible}
    adjacency: dict[int, list[int]] = {node_id: [] for node_id in eligible_by_id}
    undirected: dict[int, set[int]] = {node_id: set() for node_id in eligible_by_id}

    for link in workflow.links.values():
        if link.source not in eligible_by_id or link.target not in eligible_by_id:
            continue
        adjacency[link.source].append(link.target)
        undirected[link.source].add(link.target)
        undirected[link.target].add(link.source)

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
    """Return movable ungrouped nodes safe for independent band compaction."""
    group_by_node_id = _group_by_node_id(workflow)
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
        if _has_direct_group_incident_link(workflow, node, group_by_node_id):
            continue
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

    group_span = _group_span(workflow)
    max_layer_width = max(width for width, _height in layer_sizes.values())
    target_cap = max(
        max_layer_width,
        min(
            current_span,
            max(
                group_span,
                workflow_width * UNGROUPED_COMPACT_TARGET_WORKFLOW_RATIO,
            ),
        ),
    )
    potential_reduction = 1.0 - target_cap / current_span
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

    if (
        candidate.crossings < baseline.crossings
        or candidate.right_to_left_links < baseline.right_to_left_links
    ):
        return True

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
