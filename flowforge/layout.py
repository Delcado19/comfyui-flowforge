"""
Layout algorithm for ComfyUI FlowForge.
Implements the six-phase pipeline as described in the README.
"""

from copy import deepcopy
from dataclasses import dataclass
import math

from .model import Link, Node, Group, Workflow
from .logger import setup_logger

# Set up logger for this module
logger = setup_logger(__name__)

DEFAULT_NODE_X_DISTANCE = 80.0
DEFAULT_NODE_Y_DISTANCE = 80.0
DEFAULT_LAYOUT_CANDIDATES = 5
LAYOUT_CANDIDATE_MIN = 3
LAYOUT_CANDIDATE_MID = 5
LAYOUT_CANDIDATE_MAX = 7
LAYOUT_CANDIDATE_MIN_EVALUATIONS = 3
LAYOUT_CANDIDATE_PATIENCE = 2
LAYOUT_SMALL_WORKFLOW_LIMIT = 12
LAYOUT_LARGE_WORKFLOW_LIMIT = 30
LAYOUT_WIDE_WORKFLOW_RATIO = 1.35
LAYOUT_TALL_WORKFLOW_RATIO = 0.75
LAYOUT_DISTANCE_MIN = 20.0
LAYOUT_DISTANCE_MAX = 240.0
DECORATIVE_START_X = 20.0
DECORATIVE_START_Y = 50.0
LAYOUT_SCORE_WIDTH_WEIGHT = 2.5
LAYOUT_SCORE_HEIGHT_WEIGHT = 1.0
LAYOUT_SCORE_LINK_WEIGHT = 0.02
LAYOUT_SCORE_ASPECT_WEIGHT = 120.0
LAYOUT_SCORE_ASPECT_RATIO = 1.35
LAYOUT_SCORE_GAP_WEIGHT = 0.12
LAYOUT_SCORE_GAP_THRESHOLD = 160.0
LAYOUT_CANDIDATE_PROFILES = (
    (1.0, 1.0),
    (0.8, 1.0),
    (1.0, 0.8),
    (0.85, 1.15),
    (0.7, 0.9),
)


@dataclass
class LayoutSettings:
    """Spacing controls for layout operations."""

    node_x_distance: float = DEFAULT_NODE_X_DISTANCE
    node_y_distance: float = DEFAULT_NODE_Y_DISTANCE

    def __post_init__(self) -> None:
        self.node_x_distance = _clamp_layout_distance(self.node_x_distance)
        self.node_y_distance = _clamp_layout_distance(self.node_y_distance)

    @property
    def node_h_gap(self) -> float:
        return self.node_x_distance

    @property
    def node_v_gap(self) -> float:
        return self.node_y_distance

    @property
    def group_h_gap(self) -> float:
        return self.node_x_distance * 2.5

    @property
    def group_v_gap(self) -> float:
        return self.node_y_distance * 1.25

    @property
    def group_padding(self) -> float:
        return max(self.node_x_distance, self.node_y_distance) * 0.625

    @classmethod
    def from_payload(cls, payload) -> "LayoutSettings":
        if payload is None:
            return cls()
        if isinstance(payload, (int, float)):
            value = float(payload)
            return cls(value, value)
        if isinstance(payload, dict):
            legacy_value = payload.get("min_node_distance")
            if legacy_value is None:
                legacy_value = payload.get("node_spacing")
            if legacy_value is None:
                legacy_value = payload.get("spacing")
            if legacy_value is not None:
                try:
                    value = float(legacy_value)
                    return cls(value, value)
                except (TypeError, ValueError):
                    return cls()

            x_value = payload.get("node_x_distance")
            y_value = payload.get("node_y_distance")
            try:
                return cls(
                    DEFAULT_NODE_X_DISTANCE if x_value is None else float(x_value),
                    DEFAULT_NODE_Y_DISTANCE if y_value is None else float(y_value),
                )
            except (TypeError, ValueError):
                return cls()
        return cls()


@dataclass(frozen=True)
class LayoutScore:
    total: float
    width: float
    height: float
    link_cost: float
    aspect_cost: float
    gap_cost: float


@dataclass(frozen=True)
class LayoutReport:
    candidate_count: int
    selected_candidate: int
    score: LayoutScore


def apply(workflow: Workflow, settings: LayoutSettings | None = None) -> Workflow:
    return apply_best_layout(workflow, settings)


def apply_best_layout(
    workflow: Workflow,
    settings: LayoutSettings | None = None,
    candidate_count: int | None = None,
) -> Workflow:
    """
    Try several layout variants and return the best-scoring result.
    """
    settings = settings or LayoutSettings()
    resolved_candidate_count = (
        _resolve_layout_candidate_count(workflow)
        if candidate_count is None
        else max(1, int(candidate_count))
    )
    variants = _build_layout_candidates(workflow, settings, resolved_candidate_count)
    if len(variants) == 1:
        return _apply_layout_pass(workflow, variants[0], log=True)

    best_variant: LayoutSettings | None = None
    best_score: LayoutScore | None = None
    best_index = 0
    evaluated_count = 0
    stale_runs = 0

    for index, variant in enumerate(variants, start=1):
        evaluated_count = index
        candidate = deepcopy(workflow)
        logger.debug(
            "Running layout candidate %s/%s with x=%.2f y=%.2f",
            index,
            len(variants),
            variant.node_x_distance,
            variant.node_y_distance,
        )
        _apply_layout_pass(candidate, variant, log=False)
        score = _score_layout_candidate(candidate)
        logger.debug(
            "Candidate %s/%s scored %.2f (width=%.2f height=%.2f link=%.2f aspect=%.2f gap=%.2f)",
            index,
            len(variants),
            score.total,
            score.width,
            score.height,
            score.link_cost,
            score.aspect_cost,
            score.gap_cost,
        )
        if best_score is None or score.total < best_score.total:
            best_score = score
            best_variant = variant
            best_index = index
            stale_runs = 0
        else:
            stale_runs += 1

        if (
            index >= LAYOUT_CANDIDATE_MIN_EVALUATIONS
            and stale_runs >= LAYOUT_CANDIDATE_PATIENCE
        ):
            logger.debug(
                "Stopping layout candidate search after %s evaluations without improvement",
                index,
            )
            break

    if best_variant is None or best_score is None:
        raise RuntimeError("Layout candidate search did not produce a result")

    logger.info(
        "Selected best layout candidate with score %.2f (width=%.2f height=%.2f link=%.2f aspect=%.2f gap=%.2f)",
        best_score.total,
        best_score.width,
        best_score.height,
        best_score.link_cost,
        best_score.aspect_cost,
        best_score.gap_cost,
    )
    result = _apply_layout_pass(workflow, best_variant, log=True)
    result.layout_report = LayoutReport(
        candidate_count=evaluated_count,
        selected_candidate=best_index,
        score=best_score,
    )
    return result


def _apply_layout_pass(
    workflow: Workflow,
    settings: LayoutSettings | None = None,
    *,
    log: bool = True,
) -> Workflow:
    """
    Apply the layout algorithm to the workflow.
    This function orchestrates the six-phase pipeline.
    
    Args:
        workflow: The workflow to layout.
        
    Returns:
        The workflow with updated node positions and group bounding boxes.
    """
    settings = settings or LayoutSettings()
    if log:
        logger.info("Starting layout algorithm")
    
    try:
        # Phase 1: Group Membership
        logger.debug("Phase 1: Group Membership")
        _assign_groups(workflow)
        
        # Phase 1b: Position decorative nodes first so they form a stable
        # annotation column on the left edge of the workflow.
        logger.debug("Phase 1b: Decorative Nodes")
        decorative_right_edge = _position_decorative_nodes_left(workflow, settings)

        # Phase 2: Inter-Group Topology
        logger.debug("Phase 2: Inter-Group Topology")
        _order_groups(workflow)
        
        # Phase 3: Internal Layout (Sugiyama)
        logger.debug("Phase 3: Internal Layout")
        _layout_groups_internal(workflow, settings)
        
        # Phase 4: Global Positioning
        logger.debug("Phase 4: Global Positioning")
        _position_groups_globally(
            workflow,
            settings,
            start_x=max(50.0, decorative_right_edge + settings.group_h_gap),
        )
        
        # Phase 4b: Position ungrouped nodes (after groups)
        _position_ungrouped_nodes(
            workflow,
            settings,
            start_x_floor=decorative_right_edge + settings.group_h_gap,
        )
        
        # Phase 5: Bounding Box Update
        logger.debug("Phase 5: Bounding Box Update")
        _update_bounding_boxes(workflow, settings)
        
        if log:
            logger.info("Layout algorithm completed successfully")
        return workflow
        
    except Exception as e:
        logger.error(f"Layout algorithm failed: {e}", exc_info=True)
        raise


def _build_layout_candidates(
    workflow: Workflow,
    settings: LayoutSettings,
    candidate_count: int,
) -> list[LayoutSettings]:
    count = max(1, int(candidate_count))
    variants: list[LayoutSettings] = []
    profiles = _ordered_layout_profiles(workflow)

    for x_scale, y_scale in profiles[:count]:
        variants.append(
            LayoutSettings(
                settings.node_x_distance * x_scale,
                settings.node_y_distance * y_scale,
            )
        )

    if len(variants) >= count:
        return variants[:count]

    profile_index = 0
    while len(variants) < count:
        x_scale, y_scale = profiles[profile_index % len(profiles)]
        x_delta = 1.0 + (x_scale - 1.0) * 0.5
        y_delta = 1.0 + (y_scale - 1.0) * 0.5
        variants.append(
            LayoutSettings(
                settings.node_x_distance * x_delta,
                settings.node_y_distance * y_delta,
            )
        )
        profile_index += 1

    return variants


def _ordered_layout_profiles(workflow: Workflow) -> list[tuple[float, float]]:
    profiles = list(LAYOUT_CANDIDATE_PROFILES)
    width, height = _workflow_extent(workflow)
    ratio = width / max(1.0, height)

    if ratio >= LAYOUT_WIDE_WORKFLOW_RATIO:
        profiles.sort(key=lambda profile: (profile[0] >= 1.0, abs(profile[0] - 1.0), abs(profile[1] - 1.0)))
        return profiles

    if ratio <= LAYOUT_TALL_WORKFLOW_RATIO:
        profiles.sort(key=lambda profile: (profile[1] >= 1.0, abs(profile[1] - 1.0), abs(profile[0] - 1.0)))
        return profiles

    profiles.sort(key=lambda profile: (abs(profile[0] - 1.0) + abs(profile[1] - 1.0), abs(profile[0] - profile[1])))
    return profiles


def _resolve_layout_candidate_count(workflow: Workflow) -> int:
    node_count = len(workflow.nodes)
    if node_count <= LAYOUT_SMALL_WORKFLOW_LIMIT:
        return LAYOUT_CANDIDATE_MIN
    if node_count <= LAYOUT_LARGE_WORKFLOW_LIMIT:
        return LAYOUT_CANDIDATE_MID
    return LAYOUT_CANDIDATE_MAX


def _workflow_extent(workflow: Workflow) -> tuple[float, float]:
    left, top, right, bottom = _workflow_bounds(workflow)
    return max(0.0, right - left), max(0.0, bottom - top)


def _score_layout_candidate(workflow: Workflow) -> LayoutScore:
    left, top, right, bottom = _workflow_bounds(workflow)
    width = max(0.0, right - left)
    height = max(0.0, bottom - top)
    link_cost = sum(_link_length(workflow, link) for link in workflow.links.values())
    aspect_ratio = width / max(1.0, height)
    aspect_cost = max(0.0, aspect_ratio - LAYOUT_SCORE_ASPECT_RATIO) * LAYOUT_SCORE_ASPECT_WEIGHT
    gap_cost = _layout_gap_cost(workflow)
    total = (
        width * LAYOUT_SCORE_WIDTH_WEIGHT
        + height * LAYOUT_SCORE_HEIGHT_WEIGHT
        + link_cost * LAYOUT_SCORE_LINK_WEIGHT
        + aspect_cost
        + gap_cost
    )
    return LayoutScore(total, width, height, link_cost, aspect_cost, gap_cost)


def _layout_gap_cost(workflow: Workflow) -> float:
    anchors = sorted(
        {
            round(node.x, 3)
            for node in workflow.nodes.values()
        }
        | {
            round(group.bounding[0], 3)
            for group in workflow.groups
            if _has_positive_bounding(group)
        }
    )
    if len(anchors) < 2:
        return 0.0

    gap_cost = 0.0
    previous = anchors[0]
    for current in anchors[1:]:
        gap = current - previous
        if gap > LAYOUT_SCORE_GAP_THRESHOLD:
            gap_cost += (gap - LAYOUT_SCORE_GAP_THRESHOLD) * LAYOUT_SCORE_GAP_WEIGHT
        previous = current
    return gap_cost


def _workflow_bounds(workflow: Workflow) -> tuple[float, float, float, float]:
    left = math.inf
    top = math.inf
    right = -math.inf
    bottom = -math.inf

    for node in workflow.nodes.values():
        left = min(left, node.x)
        top = min(top, node.y)
        right = max(right, node.x + _node_visual_width(node))
        bottom = max(bottom, node.y + _node_visual_height(node))

    for group in workflow.groups:
        if not _has_positive_bounding(group):
            continue
        left = min(left, group.bounding[0])
        top = min(top, group.bounding[1])
        right = max(right, group.bounding[0] + group.bounding[2])
        bottom = max(bottom, group.bounding[1] + group.bounding[3])

    if math.isinf(left) or math.isinf(top) or math.isinf(right) or math.isinf(bottom):
        return 0.0, 0.0, 0.0, 0.0

    return left, top, right, bottom


def _link_length(workflow: Workflow, link: Link) -> float:
    source = workflow.nodes.get(link.source)
    target = workflow.nodes.get(link.target)
    if source is None or target is None:
        return 0.0

    source_x = source.x + _node_visual_width(source) / 2.0
    source_y = source.y + _node_visual_height(source) / 2.0
    target_x = target.x + _node_visual_width(target) / 2.0
    target_y = target.y + _node_visual_height(target) / 2.0
    return abs(target_x - source_x) + abs(target_y - source_y)


# ---------------------------------------------------------------------------
# Phase 1: Group Membership
# ---------------------------------------------------------------------------


def _assign_groups(workflow: Workflow) -> None:
    """
    Assign each node to the group whose bounding box contains it.
    Nodes outside every group are stored in workflow.ungrouped_nodes (implicit ungrouped set).
    When groups overlap, the first matching group in workflow order wins.
    """
    logger.debug("Assigning nodes to groups")
    
    # Clear previous assignment state so parser-time and layout-time grouping
    # cannot duplicate group membership when layout is called repeatedly.
    workflow.ungrouped_nodes.clear()
    for group in workflow.groups:
        group.nodes.clear()
    
    # For each node, find the first group that contains it (by original position)
    for node in workflow.nodes.values():
        if _is_decorative_node(node):
            continue
        assigned = False
        for group in workflow.groups:
            if _node_in_group(node, group):
                group.nodes.append(node)
                assigned = True
                break
        if not assigned:
            workflow.ungrouped_nodes.append(node)
    
    logger.debug(f"Assigned {len(workflow.nodes) - len(workflow.ungrouped_nodes)} nodes to {len(workflow.groups)} groups; {len(workflow.ungrouped_nodes)} ungrouped")


def _node_in_group(node: Node, group: Group) -> bool:
    """
    Check if a node's original position is inside a group's bounding box.
    The bounding box is [x, y, width, height].
    Returns True if node is within the group bounds, False otherwise.
    If group has no bounding box or it's invalid, returns False.
    """
    if not group.bounding or len(group.bounding) < 4:
        return False
    
    gx, gy, gw, gh = group.bounding
    return (gx <= node.x <= gx + gw) and (gy <= node.y <= gy + gh)


# ---------------------------------------------------------------------------
# Phase 2: Inter-Group Topology
# ---------------------------------------------------------------------------


def _order_groups(workflow: Workflow) -> None:
    """
    Determine the topological order of groups based on inter-group connections.
    Groups that provide data to other groups should be placed first.
    
    Modifies workflow.groups in place to establish a valid ordering.
    """
    logger.debug("Ordering groups by topology")
    
    if len(workflow.groups) <= 1:
        return
    
    # Build a dependency graph using group indices
    n_groups = len(workflow.groups)
    group_deps: list[set[int]] = [set() for _ in range(n_groups)]
    
    # Create a mapping from node id to group index
    node_to_group_idx: dict[int, int] = {}
    for gi, group in enumerate(workflow.groups):
        for node in group.nodes:
            node_to_group_idx[node.id] = gi
    
    # For each link, check if it crosses group boundaries
    for link in workflow.links.values():
        source_node = workflow.nodes.get(link.source)
        target_node = workflow.nodes.get(link.target)
        
        if not source_node or not target_node:
            continue
        
        source_gi = node_to_group_idx.get(source_node.id)
        target_gi = node_to_group_idx.get(target_node.id)
        
        if source_gi is not None and target_gi is not None and source_gi != target_gi:
            # target_group depends on source_group
            group_deps[target_gi].add(source_gi)
    
    # Topological sort using Kahn's algorithm
    ordered_indices: list[int] = []
    remaining = set(range(n_groups))
    deps = [set(d) for d in group_deps]
    
    while remaining:
        # Find groups with no remaining dependencies
        no_deps = [i for i in remaining if not deps[i]]
        
        if not no_deps:
            # Cycle detected or all remaining depend on each other
            ordered_indices.extend(remaining)
            break
        
        # Add these groups to ordered list
        ordered_indices.extend(no_deps)
        remaining -= set(no_deps)
        
        # Remove these groups from other groups' dependencies
        for i in remaining:
            deps[i] -= set(no_deps)
    
    # Update workflow.groups with the new order
    workflow.groups = [workflow.groups[i] for i in ordered_indices]
    
    logger.debug(f"Groups ordered: {[g.name for g in workflow.groups]}")


# ---------------------------------------------------------------------------
# Phase 3: Internal Layout (Sugiyama-style)
# ---------------------------------------------------------------------------


def _layout_groups_internal(workflow: Workflow, settings: LayoutSettings) -> None:
    """
    Arrange nodes within each group using a simplified Sugiyama layout.
    1. Layer assignment: longest path from any source
    2. Crossing minimisation: barycenter heuristic (two passes)
    3. Coordinate assignment on a grid
    """
    logger.debug("Laying out nodes within groups (Sugiyama)")
    
    for group in workflow.groups:
        if len(group.nodes) < 2:
            continue
        
        # Build adjacency within this group only
        node_ids = {n.id for n in group.nodes}
        # Build directed edges (source -> target) where both nodes in this group
        adj: dict[int, list[int]] = {nid: [] for nid in node_ids}
        rev_adj: dict[int, list[int]] = {nid: [] for nid in node_ids}
        for link in workflow.links.values():
            if link.source in node_ids and link.target in node_ids:
                adj[link.source].append(link.target)
                rev_adj[link.target].append(link.source)
        
        # --- 1. Layer assignment (longest path from sources) ---
        # Sources: nodes with no incoming edges within the group
        layers: dict[int, int] = {}
        # Start with sources at layer 0
        sources = [nid for nid in node_ids if not rev_adj[nid]]
        queue: list[tuple[int, int]] = [(nid, 0) for nid in sources]
        visited: set[int] = set()
        while queue:
            nid, layer = queue.pop(0)
            if nid in visited:
                continue
            visited.add(nid)
            layers[nid] = layer
            for succ in adj[nid]:
                # Only increase layer if this path gives a larger value
                queue.append((succ, layer + 1))
        
        # Any node not visited (in a cycle) gets layer 0 per spec
        for nid in node_ids:
            if nid not in layers:
                layers[nid] = 0
        
        # --- 2. Crossing minimisation (Barycenter heuristic) ---
        # Order nodes in each layer by barycenter of neighbours in adjacent layer
        # Build layer -> nodes mapping
        layer_to_nodes: dict[int, list[int]] = {}
        for nid, layer in layers.items():
            layer_to_nodes.setdefault(layer, []).append(nid)
        max_layer = max(layers.values()) if layers else 0
        
        # Two passes: forward (top to bottom) then backward (bottom to top)
        for pass_num in range(2):
            for layer in range(max_layer + 1):
                if layer not in layer_to_nodes:
                    continue
                nodes_in_layer = layer_to_nodes[layer]
                if len(nodes_in_layer) <= 1:
                    continue
                # Compute barycenter score for each node based on neighbours in next/prev layer
                avg_positions = []
                for nid in nodes_in_layer:
                    neighbours = []
                    if layer < max_layer:
                        neighbours.extend(adj.get(nid, []))
                    if layer > 0:
                        neighbours.extend(rev_adj.get(nid, []))
                    # Get vertical (Y) positions of neighbour nodes (original y)
                    if neighbours:
                        avg = sum(workflow.nodes[n].y for n in neighbours) / len(neighbours)
                    else:
                        avg = 0.0
                    avg_positions.append(avg)
                # Reorder nodes_in_layer by avg_positions ascending
                sorted_pairs = sorted(zip(nodes_in_layer, avg_positions), key=lambda p: p[1])
                layer_to_nodes[layer] = [p[0] for p in sorted_pairs]
        
        # --- 3. Coordinate assignment ---
        # Determine bounding box for group (original or computed)
        if group.bounding and len(group.bounding) >= 4:
            base_x, base_y = group.bounding[0], group.bounding[1]
        else:
            base_x = min(n.x for n in group.nodes) if group.nodes else 0
            base_y = min(n.y for n in group.nodes) if group.nodes else 0
        
        # Maximum node width in this group (for NODE_H_GAP calculation)
        max_node_w = max((n.size[0] for n in group.nodes), default=200)
        
        # Place nodes
        for layer in range(max_layer + 1):
            if layer not in layer_to_nodes:
                continue
            nodes_in_layer = layer_to_nodes[layer]
            # Sort active nodes (mode=4 bypassed) to the end
            active = [nid for nid in nodes_in_layer if workflow.nodes[nid].mode != 4]
            bypassed = [nid for nid in nodes_in_layer if workflow.nodes[nid].mode == 4]
            ordered_nids = active + bypassed
            
            for col, nid in enumerate(ordered_nids):
                node = workflow.nodes[nid]
                # X = base_x + layer * (max_node_w + NODE_H_GAP)
                # Y = base_y + col * (max_node_height + NODE_V_GAP)
                # Use average node height for column spacing, or node's own height
                node_h = node.size[1] if node.size[1] > 0 else 60
                node.x = base_x + layer * (max_node_w + settings.node_h_gap)
                node.y = base_y + col * (node_h + settings.node_v_gap)


# ---------------------------------------------------------------------------
# Phase 4: Global Positioning
# ---------------------------------------------------------------------------


def _position_groups_globally(
    workflow: Workflow,
    settings: LayoutSettings,
    start_x: float = 50.0,
) -> None:
    """
    Position groups relative to each other based on their topological order.
    Groups that come earlier in the flow are placed to the left.
    """
    logger.debug("Positioning groups globally")
    
    if not workflow.groups:
        return
    
    start_y = 50.0
    current_x = start_x
    current_y = start_y
    column_width = 0.0
    column_budget = _estimate_vertical_packing_budget(
        [group for group in workflow.groups if group.nodes],
        lambda group: _group_visual_height(group, settings),
        settings.group_v_gap,
    )
    
    for group in workflow.groups:
        if not group.nodes:
            continue
        
        min_x, min_y, max_x, max_y = _group_content_bounds(group)
        required_width = (max_x - min_x) + 2 * settings.group_padding
        required_height = (max_y - min_y) + 2 * settings.group_padding
        if _has_positive_bounding(group):
            g_width = max(group.bounding[2], required_width)
            g_height = max(group.bounding[3], required_height)
        else:
            g_width = required_width
            g_height = required_height

        if current_y > start_y and current_y + g_height > start_y + column_budget:
            current_x += column_width + settings.group_h_gap
            current_y = start_y
            column_width = 0.0
        
        offset_x = (current_x + settings.group_padding) - min_x
        offset_y = (current_y + settings.group_padding) - min_y
        
        for node in group.nodes:
            node.x += offset_x
            node.y += offset_y
        
        group.bounding = [current_x, current_y, g_width, g_height]
        
        current_y += g_height + settings.group_v_gap
        column_width = max(column_width, g_width)
        
    # The final bounding boxes are updated later; current_y/current_x only
    # affect placement.


def _position_ungrouped_nodes(
    workflow: Workflow,
    settings: LayoutSettings,
    start_x_floor: float = 50.0,
) -> None:
    """
    Position nodes that are not part of any group using vertical column packing.
    This keeps the workflow narrower by using the Y axis first.
    """
    if not workflow.ungrouped_nodes:
        return
    
    logger.debug(f"Positioning {len(workflow.ungrouped_nodes)} ungrouped nodes")
    
    # Sort by original y then x to preserve some ordering
    sorted_nodes = sorted(workflow.ungrouped_nodes, key=lambda n: (n.y, n.x))
    
    # Determine start position: if groups exist, start after the rightmost group extent
    max_right = 0.0
    for group in workflow.groups:
        if _has_positive_bounding(group):
            max_right = max(max_right, group.bounding[0] + group.bounding[2])
        elif group.nodes:
            group_max_x = max(n.x + n.size[0] for n in group.nodes)
            max_right = max(max_right, group_max_x)
    
    start_x = max(start_x_floor, max_right + settings.group_h_gap if max_right > 0 else 50)
    start_y = 50.0
    current_x = start_x
    current_y = start_y
    column_width = 0.0
    column_budget = _estimate_vertical_packing_budget(sorted_nodes, _node_visual_height, settings.node_v_gap)
    
    for node in sorted_nodes:
        node_w = _node_visual_width(node)
        node_h = _node_visual_height(node)
        if current_y > start_y and current_y + node_h > start_y + column_budget:
            current_x += column_width + settings.node_h_gap
            current_y = start_y
            column_width = 0.0

        node.x = current_x
        node.y = current_y
        current_y += node_h + settings.node_v_gap
        column_width = max(column_width, node_w)


def _position_decorative_nodes_left(workflow: Workflow, settings: LayoutSettings) -> float:
    """
    Position Note / Markdown / Label nodes as a left-side annotation column.

    Returns the right edge of the decorative column so later layout phases can
    start to its right.
    """
    logger.debug("Positioning decorative nodes on the left edge")

    decorative = [n for n in workflow.nodes.values() if _is_decorative_node(n)]
    if not decorative:
        return DECORATIVE_START_X

    decorative.sort(key=lambda n: (n.y, n.x, n.id))
    current_y = DECORATIVE_START_Y
    max_width = 0.0

    for node in decorative:
        node.x = DECORATIVE_START_X
        node.y = current_y
        current_y += _node_visual_height(node) + settings.node_v_gap
        max_width = max(max_width, _node_visual_width(node))

    return DECORATIVE_START_X + max_width


# ---------------------------------------------------------------------------
# Phase 6: Bounding Box Update
# ---------------------------------------------------------------------------


def _update_bounding_boxes(workflow: Workflow, settings: LayoutSettings) -> None:
    """
    Update all group bounding boxes without shrinking manually resized groups.

    Existing group rectangles are treated as user-authored containers. Layout
    may move them and expand them if needed, but it must not collapse a larger
    group back to a tight node fit.
    """
    logger.debug("Updating group bounding boxes")
    
    for group in workflow.groups:
        if not group.nodes:
            continue
        
        min_x, min_y, max_x, max_y = _group_content_bounds(group)
        content_left = min_x - settings.group_padding
        content_top = min_y - settings.group_padding
        content_right = max_x + settings.group_padding
        content_bottom = max_y + settings.group_padding

        if _has_positive_bounding(group):
            current_left, current_top, current_width, current_height = group.bounding
            current_right = current_left + current_width
            current_bottom = current_top + current_height
            next_left = min(current_left, content_left)
            next_top = min(current_top, content_top)
            next_right = max(current_right, content_right)
            next_bottom = max(current_bottom, content_bottom)
        else:
            next_left = content_left
            next_top = content_top
            next_right = content_right
            next_bottom = content_bottom

        group.bounding = [
            next_left,
            next_top,
            next_right - next_left,
            next_bottom - next_top,
        ]
        
        logger.debug(f"Group {group.name} bounding box updated to {group.bounding}")


def _group_content_bounds(group: Group) -> tuple[float, float, float, float]:
    """Return [left, top, right, bottom] bounds for the group's node content."""
    min_x = min(n.x for n in group.nodes)
    max_x = max(n.x + n.size[0] for n in group.nodes)
    min_y = min(n.y for n in group.nodes)
    max_y = max(n.y + n.size[1] for n in group.nodes)
    return min_x, min_y, max_x, max_y


def _has_positive_bounding(group: Group) -> bool:
    """Return True when the group has a usable, user-authored rectangle."""
    return bool(group.bounding and len(group.bounding) >= 4 and group.bounding[2] > 0 and group.bounding[3] > 0)


def _is_decorative_node(node: Node) -> bool:
    return node.type in {"Note", "MarkdownNote", "Label"}


def _node_visual_width(node: Node) -> float:
    return node.size[0] if node.size[0] > 0 else 200.0


def _node_visual_height(node: Node) -> float:
    return node.size[1] if node.size[1] > 0 else 60.0


def _group_visual_height(group: Group, settings: LayoutSettings) -> float:
    if _has_positive_bounding(group):
        return group.bounding[3]
    _, _, _, max_y = _group_content_bounds(group)
    min_y = min(n.y for n in group.nodes)
    return (max_y - min_y) + 2 * settings.group_padding


def _estimate_vertical_packing_budget(items, height_fn, gap: float) -> float:
    if not items:
        return 0.0

    heights = [max(1.0, float(height_fn(item))) for item in items]
    total_height = sum(heights) + gap * max(0, len(heights) - 1)
    target_columns = max(1, min(len(heights), int(math.sqrt(len(heights))) or 1))
    tallest = max(heights)
    return max(tallest, total_height / target_columns)


def _clamp_layout_distance(value: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return DEFAULT_NODE_X_DISTANCE

    if not math.isfinite(numeric):
        return DEFAULT_NODE_X_DISTANCE

    return max(LAYOUT_DISTANCE_MIN, min(LAYOUT_DISTANCE_MAX, numeric))
