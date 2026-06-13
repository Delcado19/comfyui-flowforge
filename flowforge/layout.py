"""
Layout algorithm for ComfyUI FlowForge.
Implements the six-phase pipeline as described in the README.
"""

from copy import deepcopy
from collections.abc import Iterable
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
NODE_MIN_WIDTH = 200.0
NODE_MIN_HEIGHT = 60.0
NODE_TITLE_HEIGHT = 26.0
NODE_SLOT_OFFSET = 8.0
NODE_ROW_HEIGHT = 20.0
NODE_ROW_GAP = 4.0
NODE_BOTTOM_PADDING = 12.0
REROUTE_NODE_MIN_WIDTH = 40.0
REROUTE_NODE_MIN_HEIGHT = 40.0
DECORATIVE_NODE_MIN_WIDTH = 220.0
DECORATIVE_NODE_MIN_HEIGHT = 80.0
DECORATIVE_START_X = 20.0
DECORATIVE_START_Y = 50.0
VIRTUAL_HUB_MIN_GAP = 24.0
VIRTUAL_HUB_MAX_GAP = 48.0
VIRTUAL_HUB_VERTICAL_FALLBACK_STEPS = 8
LAYOUT_SCORE_WIDTH_WEIGHT = 2.5
LAYOUT_SCORE_HEIGHT_WEIGHT = 1.0
LAYOUT_SCORE_LINK_WEIGHT = 0.02
LAYOUT_SCORE_CROSSING_WEIGHT = 500.0
LAYOUT_SCORE_RIGHT_TO_LEFT_WEIGHT = 1000.0
LAYOUT_SCORE_ASPECT_WEIGHT = 120.0
LAYOUT_SCORE_ASPECT_RATIO = 1.35
LAYOUT_SCORE_GAP_WEIGHT = 0.12
LAYOUT_SCORE_GAP_THRESHOLD = 160.0
LAYOUT_GROUP_TARGET_ASPECT_RATIO = 2.2
LAYOUT_GROUP_ROW_MAX_AVERAGE_WIDTHS = 2.25
GROUP_HEADER_HEIGHT = 36.0
GROUP_INTERNAL_WRAP_MIN_LAYERS = 5
GROUP_INTERNAL_WRAP_TARGET_WIDTH = 900.0
GROUP_INTERNAL_WRAP_ASPECT_RATIO = 2.8
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
        The workflow with compact node sizes, updated node positions, and group bounding boxes.
    """
    settings = settings or LayoutSettings()
    if log:
        logger.info("Starting layout algorithm")
    
    try:
        # Phase 0: Compact nodes before any layout geometry is derived.
        logger.debug("Phase 0: Node Size Minimization")
        _shrink_nodes_to_minimum_size(workflow)

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

        # Phase 4c: Virtual Set/Get hubs should stay next to the real node
        # that owns their physical wire. The Set/Get relationship itself is
        # virtual, so normal graph layering has no useful edge to preserve.
        logger.debug("Phase 4c: Virtual Set/Get Hubs")
        _position_virtual_set_get_nodes(workflow, settings)
        _assign_virtual_hub_groups_to_endpoints(workflow)

        # Phase 4d: Pinned nodes are hard layout constraints. Move unpinned
        # nodes out from under them instead of allowing visual overlap.
        logger.debug("Phase 4d: Pinned Geometry Clearance")
        _separate_unpinned_nodes_from_pinned_geometry(workflow, settings)

        # Phase 4e: Clearance can move endpoints. Re-anchor local controls and
        # virtual hubs after that movement so they remain attached to the final
        # source/destination geometry.
        logger.debug("Phase 4e: Final Local Anchors")
        _position_control_nodes_near_targets(workflow, list(workflow.nodes.values()), settings)
        _position_text_previews_near_sources(workflow, settings)
        _position_virtual_set_get_nodes(workflow, settings)
        _assign_virtual_hub_groups_to_endpoints(workflow)
        
        # Phase 5: Bounding Box Update
        logger.debug("Phase 5: Bounding Box Update")
        _update_bounding_boxes(workflow, settings)

        # Phase 5b: Group rectangles are user-visible layout surfaces. Keep
        # nodes inside their own group, but move whole movable groups away from
        # other groups or foreign nodes when compact bounds collide.
        logger.debug("Phase 5b: Group Geometry Clearance")
        _resolve_group_geometry_overlaps(workflow, settings)

        # Phase 5c: Later group moves can expose another pinned-surface
        # overlap. Run one final node pass after group clearance as the last
        # geometry contract before serialization.
        logger.debug("Phase 5c: Final Pinned Geometry Clearance")
        _separate_unpinned_nodes_from_pinned_geometry(workflow, settings)
        
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
    crossing_cost = _layout_crossing_count(workflow) * LAYOUT_SCORE_CROSSING_WEIGHT
    right_to_left_cost = _layout_right_to_left_count(workflow) * LAYOUT_SCORE_RIGHT_TO_LEFT_WEIGHT
    total = (
        width * LAYOUT_SCORE_WIDTH_WEIGHT
        + height * LAYOUT_SCORE_HEIGHT_WEIGHT
        + link_cost * LAYOUT_SCORE_LINK_WEIGHT
        + crossing_cost
        + right_to_left_cost
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


def _layout_crossing_count(workflow: Workflow) -> int:
    """Count straight-line link crossings for choosing among layout candidates."""
    centers = {
        node.id: (
            node.x + _node_visual_width(node) / 2.0,
            node.y + _node_visual_height(node) / 2.0,
        )
        for node in workflow.nodes.values()
    }
    segments: list[tuple[int, int, tuple[float, float], tuple[float, float]]] = []
    for link in workflow.links.values():
        if link.source not in centers or link.target not in centers:
            continue
        segments.append((link.source, link.target, centers[link.source], centers[link.target]))

    crossings = 0
    for index, first in enumerate(segments):
        for second in segments[index + 1 :]:
            if first[0] in {second[0], second[1]} or first[1] in {second[0], second[1]}:
                continue
            if _segments_intersect(first[2], first[3], second[2], second[3]):
                crossings += 1
    return crossings


def _layout_right_to_left_count(workflow: Workflow) -> int:
    """Count links whose target center is left of the source center."""
    count = 0
    for link in workflow.links.values():
        source = workflow.nodes.get(link.source)
        target = workflow.nodes.get(link.target)
        if source is None or target is None:
            continue
        source_x = source.x + _node_visual_width(source) / 2.0
        target_x = target.x + _node_visual_width(target) / 2.0
        if target_x < source_x:
            count += 1
    return count


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
    
    # Virtual Set/Get hubs are endpoint anchors, not regular graph content.
    # Assign them only after their physical endpoint has its final position.
    for node in workflow.nodes.values():
        if _is_decorative_node(node) or _is_virtual_hub_node(node):
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


def _shrink_nodes_to_minimum_size(workflow: Workflow) -> None:
    """Shrink node rectangles to their compact layout size without growing them."""
    for node in workflow.nodes.values():
        if _is_pinned_node(workflow, node):
            continue
        if _preserves_authored_node_size(node):
            continue
        if not node.size or len(node.size) < 2:
            continue

        current_width = float(node.size[0])
        current_height = float(node.size[1])
        if current_width <= 0 or current_height <= 0:
            continue

        min_width, min_height = _minimum_node_size(node)
        node.size = [
            min(current_width, min_width),
            min(current_height, min_height),
        ]


def _minimum_node_size(node: Node) -> tuple[float, float]:
    """Return a conservative compact size for ComfyUI node geometry.

    Mirrors the frontend renderer in ``frontend/src/utils/nodeGeometry.ts``:
    inputs/widgets stack vertically below the title, outputs occupy the
    right-hand slot column, and the returned height must be at least the
    content height the renderer derives from the same node.
    """
    if _is_reroute_node(node):
        return REROUTE_NODE_MIN_WIDTH, REROUTE_NODE_MIN_HEIGHT

    if _is_decorative_node(node):
        return DECORATIVE_NODE_MIN_WIDTH, DECORATIVE_NODE_MIN_HEIGHT

    if node.collapsed:
        return NODE_MIN_WIDTH, NODE_MIN_HEIGHT

    slot_rows = max(0, node.input_count - node.widget_input_count)
    widget_heights = _widget_row_heights(
        node.widgets_values, node.widget_input_count
    )

    content_height = _stacked_rows_height(slot_rows, widget_heights)
    output_height = NODE_SLOT_OFFSET + node.output_count * NODE_ROW_HEIGHT

    min_height = max(
        NODE_MIN_HEIGHT,
        NODE_TITLE_HEIGHT
        + max(content_height, output_height)
        + NODE_BOTTOM_PADDING,
    )
    return NODE_MIN_WIDTH, min_height


def _preserves_authored_node_size(node: Node) -> bool:
    """Keep authored visual nodes at their saved ComfyUI size.

    Image I/O and annotation nodes often contain previews, text, labels, or
    custom save controls where the author's rectangle is meaningful UI state.
    """
    return _is_image_io_node(node) or _is_decorative_node(node)


def _widget_row_heights(values: list, widget_input_count: int) -> list[float]:
    """Heights of widget rows in render order.

    Saved ``widgets_values`` drive multiline-aware row heights. When the node
    declares more widget-inputs than it has saved values (common when widgets
    were never edited), the extra rows fall back to the single-row height.
    """
    heights = [_minimum_widget_row_height(value) for value in values]
    extra = max(0, widget_input_count - len(heights))
    heights.extend([NODE_ROW_HEIGHT] * extra)
    return heights


def _stacked_rows_height(slot_rows: int, widget_heights: list[float]) -> float:
    """Total height of stacked input/widget rows, matching the frontend."""
    row_heights: list[float] = [NODE_ROW_HEIGHT] * slot_rows + list(widget_heights)
    if not row_heights:
        return NODE_SLOT_OFFSET
    return (
        NODE_SLOT_OFFSET
        + sum(row_heights)
        + (len(row_heights) - 1) * NODE_ROW_GAP
    )


def _minimum_widget_row_height(value) -> float:
    if _is_multiline_widget_value(value):
        text = str(value)
        line_count = max(3, len(text.splitlines()) if text else 1)
        estimated_lines = math.ceil(len(text) / 54) if text else 1
        return min(180.0, max(60.0, max(line_count, estimated_lines) * 18.0 + 10.0))
    return NODE_ROW_HEIGHT


def _is_multiline_widget_value(value) -> bool:
    if isinstance(value, (dict, list)):
        return True
    if not isinstance(value, str):
        return False
    return "\n" in value or len(value) > 54


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
        if group.pinned or len(group.nodes) < 2:
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
        layers = _assign_longest_path_layers(node_ids, adj, rev_adj)
        _compress_debug_sidecar_layers(layers, adj, rev_adj, workflow)
        max_layer = max(layers.values()) if layers else 0
        
        # --- 2. Crossing minimisation (Barycenter heuristic) ---
        # Order nodes in each layer by barycenter of neighbours in adjacent layer
        # Build layer -> nodes mapping
        layer_to_nodes: dict[int, list[int]] = {}
        for nid, layer in layers.items():
            layer_to_nodes.setdefault(layer, []).append(nid)
        for layer_nodes in layer_to_nodes.values():
            layer_nodes.sort(key=lambda node_id: (workflow.nodes[node_id].y, workflow.nodes[node_id].x, node_id))
        
        _minimize_layer_crossings(layer_to_nodes, adj, rev_adj, workflow, max_layer)
        
        # --- 3. Coordinate assignment ---
        # Determine bounding box for group (original or computed)
        if group.bounding and len(group.bounding) >= 4:
            base_x, base_y = group.bounding[0], group.bounding[1]
        else:
            base_x = min(n.x for n in group.nodes) if group.nodes else 0
            base_y = min(n.y for n in group.nodes) if group.nodes else 0
        
        layer_positions = _internal_layer_positions(
            workflow,
            layer_to_nodes,
            max_layer,
            base_x,
            base_y,
            settings,
        )
        
        # Place nodes
        for layer in range(max_layer + 1):
            if layer not in layer_to_nodes:
                continue
            nodes_in_layer = layer_to_nodes[layer]
            # Sort active nodes (mode=4 bypassed) to the end
            active = [nid for nid in nodes_in_layer if workflow.nodes[nid].mode != 4]
            bypassed = [nid for nid in nodes_in_layer if workflow.nodes[nid].mode == 4]
            ordered_nids = active + bypassed
            
            layer_x, current_y = layer_positions[layer]
            for nid in ordered_nids:
                node = workflow.nodes[nid]
                node_h = _node_visual_height(node)
                if not _is_pinned_node(workflow, node):
                    node.x = layer_x
                    node.y = current_y
                current_y += node_h + settings.node_v_gap


def _internal_layer_positions(
    workflow: Workflow,
    layer_to_nodes: dict[int, list[int]],
    max_layer: int,
    base_x: float,
    base_y: float,
    settings: LayoutSettings,
) -> dict[int, tuple[float, float]]:
    """Use per-layer widths and wrap long internal chains into compact rows."""
    layer_widths = _internal_layer_widths(workflow, layer_to_nodes, max_layer)
    layer_heights = _internal_layer_heights(workflow, layer_to_nodes, max_layer, settings)
    columns_per_row = _internal_columns_per_row(layer_widths, layer_heights, settings)
    positions: dict[int, tuple[float, float]] = {}
    current_y = base_y

    for row_start in range(0, max_layer + 1, columns_per_row):
        row_layers = range(row_start, min(max_layer + 1, row_start + columns_per_row))
        current_x = base_x
        row_height = 0.0
        for layer in row_layers:
            positions[layer] = (current_x, current_y)
            row_height = max(row_height, layer_heights[layer])
            current_x += layer_widths[layer] + settings.node_h_gap
        current_y += row_height + settings.group_v_gap

    return positions


def _internal_layer_widths(
    workflow: Workflow,
    layer_to_nodes: dict[int, list[int]],
    max_layer: int,
) -> dict[int, float]:
    """Use each layer's own width so one large node does not widen all columns."""
    widths: dict[int, float] = {}
    for layer in range(max_layer + 1):
        widths[layer] = max(
            (
                _node_visual_width(workflow.nodes[node_id])
                for node_id in layer_to_nodes.get(layer, [])
                if node_id in workflow.nodes
            ),
            default=NODE_MIN_WIDTH,
        )
    return widths


def _internal_layer_heights(
    workflow: Workflow,
    layer_to_nodes: dict[int, list[int]],
    max_layer: int,
    settings: LayoutSettings,
) -> dict[int, float]:
    heights: dict[int, float] = {}
    for layer in range(max_layer + 1):
        node_ids = [
            node_id
            for node_id in layer_to_nodes.get(layer, [])
            if node_id in workflow.nodes
        ]
        if not node_ids:
            heights[layer] = NODE_MIN_HEIGHT
            continue
        heights[layer] = sum(_node_visual_height(workflow.nodes[node_id]) for node_id in node_ids)
        heights[layer] += settings.node_v_gap * max(0, len(node_ids) - 1)
    return heights


def _internal_columns_per_row(
    layer_widths: dict[int, float],
    layer_heights: dict[int, float],
    settings: LayoutSettings,
) -> int:
    layer_count = len(layer_widths)
    if layer_count < GROUP_INTERNAL_WRAP_MIN_LAYERS:
        return max(1, layer_count)

    total_width = sum(layer_widths.values()) + settings.node_h_gap * max(0, layer_count - 1)
    max_height = max(layer_heights.values(), default=NODE_MIN_HEIGHT)
    width_budget = max(GROUP_INTERNAL_WRAP_TARGET_WIDTH, max_height * GROUP_INTERNAL_WRAP_ASPECT_RATIO)
    if total_width <= width_budget:
        return layer_count

    return max(2, min(layer_count, math.ceil(math.sqrt(layer_count))))


def _assign_longest_path_layers(
    node_ids: set[int],
    adj: dict[int, list[int]],
    rev_adj: dict[int, list[int]],
) -> dict[int, int]:
    """Assign DAG nodes to their longest-path layer; cyclic leftovers use 0."""
    indegree = {node_id: len(rev_adj[node_id]) for node_id in node_ids}
    layers: dict[int, int] = {node_id: 0 for node_id in node_ids if indegree[node_id] == 0}
    queue = [node_id for node_id in node_ids if indegree[node_id] == 0]
    processed: set[int] = set()

    while queue:
        node_id = queue.pop(0)
        processed.add(node_id)
        for target_id in adj[node_id]:
            layers[target_id] = max(layers.get(target_id, 0), layers[node_id] + 1)
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                queue.append(target_id)

    for node_id in node_ids - processed:
        layers[node_id] = 0

    return layers


def _compress_debug_sidecar_layers(
    layers: dict[int, int],
    adj: dict[int, list[int]],
    rev_adj: dict[int, list[int]],
    workflow: Workflow,
) -> None:
    """Keep preview/debug/control side branches from dictating group width."""
    for _ in range(max(1, len(layers))):
        changed = False
        for node_id, predecessors in rev_adj.items():
            if not predecessors or node_id not in layers:
                continue

            new_layer = max(
                layers.get(predecessor, 0)
                + _internal_edge_layer_cost(workflow, predecessor, node_id, adj)
                for predecessor in predecessors
            )
            if new_layer < layers[node_id]:
                layers[node_id] = new_layer
                changed = True
        if not changed:
            break

    for node_id, targets in adj.items():
        node = workflow.nodes[node_id]
        if rev_adj.get(node_id) or len(targets) != 1 or not _is_source_control_sidecar_node(node):
            continue
        target_id = targets[0]
        if target_id in layers:
            layers[node_id] = layers[target_id]


def _internal_edge_layer_cost(
    workflow: Workflow,
    source_id: int,
    target_id: int,
    adj: dict[int, list[int]],
) -> int:
    source = workflow.nodes[source_id]
    target = workflow.nodes[target_id]
    if _is_debug_sidecar_node(workflow, target, adj) or _is_switch_sidecar_node(target):
        return 0
    if _is_source_control_sidecar_node(source):
        return 0
    return 1


def _is_debug_sidecar_node(
    workflow: Workflow,
    node: Node,
    adj: dict[int, list[int]],
) -> bool:
    """Return True for display-only helper chains that should sit beside data."""
    if node.type in {"PreviewImage", "ShowText|pysssss"}:
        return True
    if node.type != "MaskToImage":
        return False
    targets = [workflow.nodes[target_id] for target_id in adj.get(node.id, []) if target_id in workflow.nodes]
    return bool(targets) and all(target.type == "PreviewImage" for target in targets)


def _is_switch_sidecar_node(node: Node) -> bool:
    return "switch" in node.type.lower()


def _is_source_control_sidecar_node(node: Node) -> bool:
    return node.type in {"Text Multiline"}


def _minimize_layer_crossings(
    layer_to_nodes: dict[int, list[int]],
    adj: dict[int, list[int]],
    rev_adj: dict[int, list[int]],
    workflow: Workflow,
    max_layer: int,
) -> None:
    """Order nodes within layers using forward/backward barycenter sweeps."""
    sweeps = (
        range(1, max_layer + 1),
        range(max_layer - 1, -1, -1),
    )
    for layers in sweeps:
        for layer in layers:
            nodes_in_layer = layer_to_nodes.get(layer, [])
            if len(nodes_in_layer) <= 1:
                continue

            position_by_node = {
                node_id: position
                for other_layer in (layer - 1, layer + 1)
                for position, node_id in enumerate(layer_to_nodes.get(other_layer, []))
            }

            def sort_key(node_id: int) -> tuple[float, float, float, int]:
                neighbours = [
                    neighbour
                    for neighbour in [*rev_adj.get(node_id, []), *adj.get(node_id, [])]
                    if neighbour in position_by_node
                ]
                if neighbours:
                    barycenter = sum(position_by_node[neighbour] for neighbour in neighbours) / len(neighbours)
                else:
                    barycenter = float("inf")
                node = workflow.nodes[node_id]
                return (barycenter, node.y, node.x, node_id)

            layer_to_nodes[layer] = sorted(nodes_in_layer, key=sort_key)


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

    if _has_group_flow_edges(workflow):
        _position_groups_by_flow_layers(workflow, settings, start_x)
        return
    
    start_y = 50.0
    current_x = start_x
    current_y = start_y
    row_height = 0.0
    movable_groups = [group for group in workflow.groups if group.nodes and not group.pinned]
    target_row_width = _estimate_group_row_width(movable_groups, settings)

    for group in workflow.groups:
        if not group.nodes or group.pinned:
            continue

        min_x, min_y, _max_x, _max_y = _group_content_bounds(group)
        g_width, g_height = _required_group_size(group, settings)

        row_has_content = current_x > start_x
        starts_next_row = (
            row_has_content
            and current_x + g_width > start_x + target_row_width
        )
        if starts_next_row:
            current_x = start_x
            current_y += row_height + settings.group_v_gap
            row_height = 0.0
        
        offset_x = (current_x + settings.group_padding) - min_x
        offset_y = (current_y + _group_top_padding(settings)) - min_y

        for node in group.nodes:
            if _is_pinned_node(workflow, node):
                continue
            node.x += offset_x
            node.y += offset_y
        
        group.bounding = [current_x, current_y, g_width, g_height]
        
        current_x += g_width + settings.group_h_gap
        row_height = max(row_height, g_height)
        
    # The final bounding boxes are updated later; current_y/current_x only
    # affect placement.


def _position_groups_by_flow_layers(
    workflow: Workflow,
    settings: LayoutSettings,
    start_x: float,
) -> None:
    """Place connected groups in dataflow columns to shorten cross-group wires."""
    group_sizes = {
        group.id: _required_group_size(group, settings)
        for group in workflow.groups
        if group.nodes and not group.pinned
    }
    if not group_sizes:
        return

    layers = _group_flow_layers(workflow)
    layer_to_groups: dict[int, list[Group]] = {}
    for group in workflow.groups:
        if group.id in group_sizes:
            layer_to_groups.setdefault(layers.get(group.id, 0), []).append(group)

    layer_x_positions: dict[int, float] = {}
    current_x = start_x
    flow_gap = _flow_group_h_gap(workflow, settings)
    for layer in sorted(layer_to_groups):
        layer_x_positions[layer] = current_x
        layer_width = max(group_sizes[group.id][0] for group in layer_to_groups[layer])
        current_x += layer_width + flow_gap

    for layer in sorted(layer_to_groups):
        current_y = 50.0
        for group in sorted(
            layer_to_groups[layer],
            key=lambda item: (_group_flow_order_key(workflow, item, layers), item.bounding[1], item.bounding[0], item.id),
        ):
            g_width, g_height = group_sizes[group.id]
            min_x, min_y, _max_x, _max_y = _group_content_bounds(group)
            new_x = layer_x_positions[layer]
            new_y = current_y
            offset_x = (new_x + settings.group_padding) - min_x
            offset_y = (new_y + _group_top_padding(settings)) - min_y

            for node in group.nodes:
                if _is_pinned_node(workflow, node):
                    continue
                node.x += offset_x
                node.y += offset_y

            group.bounding = [new_x, new_y, g_width, g_height]
            current_y += g_height + settings.group_v_gap


def _has_group_flow_edges(workflow: Workflow) -> bool:
    adj, _rev_adj = _group_flow_adjacency(workflow)
    return any(targets for targets in adj.values())


def _required_group_size(group: Group, settings: LayoutSettings) -> tuple[float, float]:
    min_x, min_y, max_x, max_y = _group_content_bounds(group)
    return (
        (max_x - min_x) + 2 * settings.group_padding,
        (max_y - min_y) + _group_top_padding(settings) + _group_bottom_padding(settings),
    )


def _group_top_padding(settings: LayoutSettings) -> float:
    """Reserve the visible ComfyUI group title band above member nodes."""
    return max(settings.group_padding, GROUP_HEADER_HEIGHT)


def _group_bottom_padding(settings: LayoutSettings) -> float:
    return settings.group_padding


def _flow_group_h_gap(workflow: Workflow, settings: LayoutSettings) -> float:
    """Reserve enough inter-group space for bridge nodes between flow columns."""
    if not _has_movable_group_bridge_flow_edges(workflow):
        return settings.group_h_gap

    bridge_gap = _bridge_side_gap(settings)
    group_by_node_id = _group_by_node_id(workflow)
    bridge_width = max(
        (
            _node_visual_width(node)
            for node in workflow.nodes.values()
            if _has_direct_group_incident_link(workflow, node, group_by_node_id)
        ),
        default=NODE_MIN_WIDTH,
    )
    return max(settings.group_h_gap, bridge_width + bridge_gap * 2.0)


def _has_movable_group_bridge_flow_edges(workflow: Workflow) -> bool:
    group_by_node_id = _group_by_node_id(workflow)
    group_by_id = {group.id: group for group in workflow.groups}
    for group in workflow.groups:
        if not group.nodes or _group_has_fixed_geometry(group):
            continue
        for node in group.nodes:
            reachable = _reachable_target_groups_from_node(
                workflow,
                node.id,
                group.id,
                group_by_node_id,
            )
            for target_group_id, via_bridge in reachable.items():
                target_group = group_by_id.get(target_group_id)
                if (
                    via_bridge
                    and target_group is not None
                    and not _group_has_fixed_geometry(target_group)
                ):
                    return True
    return False


def _has_direct_group_incident_link(
    workflow: Workflow,
    node: Node,
    group_by_node_id: dict[int, Group],
) -> bool:
    if node.id in group_by_node_id or _is_decorative_node(node) or _is_virtual_hub_node(node):
        return False
    return any(
        (link := workflow.links.get(link_id)) is not None
        and link.source in group_by_node_id
        for link_id in node.input_links
    ) or any(
        (link := workflow.links.get(link_id)) is not None
        and link.target in group_by_node_id
        for link_id in node.output_links
    )


def _group_flow_layers(workflow: Workflow) -> dict[int, int]:
    """Assign group columns by longest inter-group dependency path."""
    group_ids = {group.id for group in workflow.groups if group.nodes}
    adj, rev_adj = _group_flow_adjacency(workflow)

    layers: dict[int, int] = {
        group_id: 0
        for group_id in group_ids
        if not rev_adj[group_id]
    }
    queue = list(layers)
    while queue:
        group_id = queue.pop(0)
        for target_id in sorted(adj[group_id]):
            next_layer = layers[group_id] + 1
            if next_layer > layers.get(target_id, -1):
                layers[target_id] = next_layer
            rev_adj[target_id].discard(group_id)
            if not rev_adj[target_id]:
                queue.append(target_id)

    for group_id in group_ids:
        if group_id in layers:
            continue
        processed_predecessors = [
            source_id
            for source_id, targets in adj.items()
            if group_id in targets and source_id in layers
        ]
        if processed_predecessors:
            layers[group_id] = max(layers[source_id] + 1 for source_id in processed_predecessors)
        else:
            layers[group_id] = 0
    return layers


def _group_flow_adjacency(workflow: Workflow) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """Build group dependencies, following ungrouped bridge nodes when needed."""
    group_by_node_id = _group_by_node_id(workflow)
    group_by_id = {group.id: group for group in workflow.groups}
    group_ids = {group.id for group in workflow.groups if group.nodes}
    adj: dict[int, set[int]] = {group_id: set() for group_id in group_ids}
    rev_adj: dict[int, set[int]] = {group_id: set() for group_id in group_ids}

    for group in workflow.groups:
        if group.id not in group_ids:
            continue
        for node in group.nodes:
            for target_group_id, via_bridge in _reachable_target_groups_from_node(
                workflow,
                node.id,
                group.id,
                group_by_node_id,
            ).items():
                if target_group_id == group.id:
                    continue
                target_group = group_by_id.get(target_group_id)
                if (
                    via_bridge
                    and target_group is not None
                    and (_group_has_fixed_geometry(group) or _group_has_fixed_geometry(target_group))
                ):
                    continue
                adj[group.id].add(target_group_id)
                rev_adj[target_group_id].add(group.id)

    return adj, rev_adj


def _reachable_target_groups_from_node(
    workflow: Workflow,
    start_node_id: int,
    source_group_id: int,
    group_by_node_id: dict[int, Group],
) -> dict[int, bool]:
    """Find downstream groups without letting ungrouped bridge nodes hide flow."""
    targets: dict[int, bool] = {}
    queue = [
        (link.target, False)
        for link_id in workflow.nodes[start_node_id].output_links
        if (link := workflow.links.get(link_id)) is not None
    ]
    visited: set[int] = set()

    while queue:
        node_id, via_bridge = queue.pop(0)
        if node_id in visited or node_id not in workflow.nodes:
            continue
        visited.add(node_id)

        group = group_by_node_id.get(node_id)
        if group is not None:
            if group.id != source_group_id:
                targets[group.id] = targets.get(group.id, True) and via_bridge
            continue

        node = workflow.nodes[node_id]
        if _is_decorative_node(node) or _is_virtual_hub_node(node):
            continue
        queue.extend(
            (link.target, True)
            for link_id in node.output_links
            if (link := workflow.links.get(link_id)) is not None
        )

    return targets


def _group_flow_order_key(
    workflow: Workflow,
    group: Group,
    layers: dict[int, int],
) -> float:
    """Use neighbouring group positions as a stable vertical ordering hint."""
    group_by_node_id = _group_by_node_id(workflow)
    centres: list[float] = []
    for link in workflow.links.values():
        source_group = group_by_node_id.get(link.source)
        target_group = group_by_node_id.get(link.target)
        if source_group is None or target_group is None or source_group is target_group:
            continue
        if source_group is group and layers.get(target_group.id, 0) != layers.get(group.id, 0):
            centres.append(_group_original_center_y(target_group))
        elif target_group is group and layers.get(source_group.id, 0) != layers.get(group.id, 0):
            centres.append(_group_original_center_y(source_group))
    if not centres:
        return _group_original_center_y(group)
    return sum(centres) / len(centres)


def _group_original_center_y(group: Group) -> float:
    if _has_positive_bounding(group):
        return group.bounding[1] + group.bounding[3] / 2.0
    if not group.nodes:
        return 0.0
    top = min(node.y for node in group.nodes)
    bottom = max(node.y + _node_visual_height(node) for node in group.nodes)
    return top + (bottom - top) / 2.0


def _position_ungrouped_nodes(
    workflow: Workflow,
    settings: LayoutSettings,
    nodes: list[Node] | None = None,
    start_x_floor: float = 50.0,
) -> float:
    """
    Position nodes that are not part of any group using vertical column packing.
    This keeps the workflow narrower by using the Y axis first.
    """
    nodes_to_position = workflow.ungrouped_nodes if nodes is None else nodes
    nodes_to_position = [node for node in nodes_to_position if not _is_pinned_node(workflow, node)]
    if not nodes_to_position:
        return start_x_floor

    logger.debug(f"Positioning {len(nodes_to_position)} ungrouped nodes")

    start_x = _ungrouped_start_x(workflow, settings, start_x_floor)
    bridge_right, bridge_node_ids = _position_group_bridge_nodes(workflow, nodes_to_position, settings)
    nodes_to_position = [node for node in nodes_to_position if node.id not in bridge_node_ids]
    if not nodes_to_position:
        return max(start_x_floor, bridge_right)

    if _has_internal_links(workflow, nodes_to_position):
        current_right = _position_linked_ungrouped_nodes(workflow, nodes_to_position, settings, start_x)
        return max(
            current_right,
            bridge_right,
            _position_external_sources_near_group_targets(workflow, nodes_to_position, settings),
            _position_control_nodes_near_targets(workflow, nodes_to_position, settings),
        )

    sorted_nodes = _order_ungrouped_nodes_by_flow(workflow, nodes_to_position)

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

    return max(
        current_x + column_width,
        bridge_right,
        _position_external_sources_near_group_targets(workflow, nodes_to_position, settings),
        _position_control_nodes_near_targets(workflow, nodes_to_position, settings),
    )


def _position_group_bridge_nodes(
    workflow: Workflow,
    nodes: list[Node],
    settings: LayoutSettings,
) -> tuple[float, set[int]]:
    """Place ungrouped bridge nodes in the gaps between their connected groups."""
    group_by_node_id = _group_by_node_id(workflow)
    if not group_by_node_id:
        return 0.0, set()
    movable_group_ids = {
        group.id
        for group in workflow.groups
        if group.nodes and not _group_has_fixed_geometry(group)
    }

    candidates = [
        node
        for node in nodes
        if _is_group_bridge_eligible_node(workflow, node, group_by_node_id)
    ]
    if not candidates:
        return 0.0, set()
    if not movable_group_ids and not any(
        _bridge_has_fixed_incident_group_pair(workflow, node, group_by_node_id)
        for node in candidates
    ):
        return 0.0, set()

    group_layers = _group_flow_layers(workflow)
    source_layers, target_layers = _bridge_group_layer_sets(
        workflow,
        candidates,
        group_by_node_id,
        group_layers,
        movable_group_ids,
    )
    bridge_node_ids = {
        node.id
        for node in candidates
        if _should_position_as_group_bridge(
            workflow,
            node,
            group_by_node_id,
            source_layers[node.id],
            target_layers[node.id],
        )
        if not _is_external_group_source_node(workflow, node, group_by_node_id)
    }
    if not bridge_node_ids:
        return 0.0, set()

    slot_to_nodes: dict[int, list[Node]] = {}
    for node in candidates:
        if node.id not in bridge_node_ids:
            continue
        slot = _bridge_slot_index(source_layers[node.id], target_layers[node.id])
        slot_to_nodes.setdefault(slot, []).append(node)

    fixed_rects = _bridge_fixed_rects(workflow, bridge_node_ids)
    placed_rects: list[tuple[float, float, float, float]] = []
    current_right = 0.0
    for slot in sorted(slot_to_nodes):
        current_y = 50.0
        slot_nodes = sorted(
            slot_to_nodes[slot],
            key=lambda node: (
                _bridge_node_desired_y(workflow, node, group_by_node_id) + _node_visual_height(node) / 2.0,
                node.y,
                node.x,
                node.id,
            ),
        )
        slot_width = max(_node_visual_width(node) for node in slot_nodes)
        fallback_x = _bridge_slot_x(workflow, group_layers, slot, settings, slot_width)
        for node in slot_nodes:
            node_w = _node_visual_width(node)
            node_h = _node_visual_height(node)
            x = _bridge_node_x(
                workflow,
                node,
                group_by_node_id,
                settings,
                fallback_x,
                node_w,
            )
            desired_y = _bridge_node_desired_y(workflow, node, group_by_node_id)
            y = _resolve_bridge_y(
                x,
                max(current_y, desired_y),
                node_w,
                node_h,
                fixed_rects,
                placed_rects,
                settings,
            )
            node.x = x
            node.y = y
            placed_rects.append(_node_rect(node))
            current_y = y + node_h + settings.node_v_gap
            current_right = max(current_right, node.x + node_w)

    return current_right, bridge_node_ids


def _is_group_bridge_eligible_node(
    workflow: Workflow,
    node: Node,
    group_by_node_id: dict[int, Group],
) -> bool:
    return (
        node.id not in group_by_node_id
        and not _is_pinned_node(workflow, node)
        and not _is_decorative_node(node)
        and not _is_virtual_hub_node(node)
    )


def _should_position_as_group_bridge(
    workflow: Workflow,
    node: Node,
    group_by_node_id: dict[int, Group],
    source_layers: set[int],
    target_layers: set[int],
) -> bool:
    # Source-only chains are normal ungrouped dataflow, not group bridges.
    # Treating every downstream node as a bridge collapses whole workflows into
    # one tall slot beside the loader groups.
    if source_layers and target_layers:
        return True
    return _bridge_has_fixed_incident_group_pair(workflow, node, group_by_node_id)


def _bridge_group_layer_sets(
    workflow: Workflow,
    nodes: list[Node],
    group_by_node_id: dict[int, Group],
    group_layers: dict[int, int],
    movable_group_ids: set[int],
) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    node_ids = {node.id for node in nodes}
    source_layers: dict[int, set[int]] = {node_id: set() for node_id in node_ids}
    target_layers: dict[int, set[int]] = {node_id: set() for node_id in node_ids}
    predecessors: dict[int, set[int]] = {node_id: set() for node_id in node_ids}
    successors: dict[int, set[int]] = {node_id: set() for node_id in node_ids}

    for link in workflow.links.values():
        if link.source in node_ids and link.target in node_ids:
            successors[link.source].add(link.target)
            predecessors[link.target].add(link.source)
        if (
            link.target in node_ids
            and (source_group := group_by_node_id.get(link.source)) is not None
            and source_group.id in movable_group_ids
        ):
            source_layers[link.target].add(group_layers.get(source_group.id, 0))
        if (
            link.source in node_ids
            and (target_group := group_by_node_id.get(link.target)) is not None
            and target_group.id in movable_group_ids
        ):
            target_layers[link.source].add(group_layers.get(target_group.id, 0))

    for _ in range(max(1, len(node_ids))):
        changed = False
        for node_id in node_ids:
            for predecessor in predecessors[node_id]:
                before = len(source_layers[node_id])
                source_layers[node_id].update(source_layers[predecessor])
                changed = changed or len(source_layers[node_id]) != before
            for successor in successors[node_id]:
                before = len(target_layers[node_id])
                target_layers[node_id].update(target_layers[successor])
                changed = changed or len(target_layers[node_id]) != before
        if not changed:
            break

    return source_layers, target_layers


def _bridge_slot_index(source_layers: set[int], target_layers: set[int]) -> int:
    source_layer = max(source_layers) if source_layers else None
    target_layer = min(target_layers) if target_layers else None
    if source_layer is not None and target_layer is not None:
        return max(source_layer, target_layer - 1)
    if source_layer is not None:
        return source_layer
    if target_layer is not None:
        return max(0, target_layer - 1)
    return 0


def _bridge_slot_x(
    workflow: Workflow,
    group_layers: dict[int, int],
    slot: int,
    settings: LayoutSettings,
    width: float,
) -> float:
    gap = _bridge_side_gap(settings)
    previous_groups = [
        group
        for group in workflow.groups
        if _has_positive_bounding(group) and group_layers.get(group.id, 0) <= slot
    ]

    next_groups = [
        group
        for group in workflow.groups
        if _has_positive_bounding(group) and group_layers.get(group.id, 0) > slot
    ]

    if previous_groups and next_groups:
        right = max(group.bounding[0] + group.bounding[2] for group in previous_groups)
        left = min(group.bounding[0] for group in next_groups)
        slot_left = right + gap
        slot_right = left - gap
        if slot_right - slot_left >= width:
            return slot_left + (slot_right - slot_left - width) / 2.0
        return slot_left

    if previous_groups:
        right = max(group.bounding[0] + group.bounding[2] for group in previous_groups)
        return right + gap

    if next_groups:
        left = min(group.bounding[0] for group in next_groups)
        return left - width - gap

    return 50.0


def _bridge_node_x(
    workflow: Workflow,
    node: Node,
    group_by_node_id: dict[int, Group],
    settings: LayoutSettings,
    fallback_x: float,
    width: float,
) -> float:
    fixed_gap_x = _bridge_fixed_incident_gap_x(
        workflow,
        node,
        group_by_node_id,
        settings,
        width,
    )
    return fixed_gap_x if fixed_gap_x is not None else fallback_x


def _bridge_has_fixed_incident_group_pair(
    workflow: Workflow,
    node: Node,
    group_by_node_id: dict[int, Group],
) -> bool:
    incident_groups = _bridge_direct_incident_groups(workflow, node, group_by_node_id)
    return len(incident_groups) >= 2 and any(_group_has_fixed_geometry(group) for group in incident_groups)


def _bridge_fixed_incident_gap_x(
    workflow: Workflow,
    node: Node,
    group_by_node_id: dict[int, Group],
    settings: LayoutSettings,
    width: float,
) -> float | None:
    incident_groups = _bridge_direct_incident_groups(workflow, node, group_by_node_id)
    if not _bridge_has_fixed_incident_group_pair(workflow, node, group_by_node_id):
        return None

    # Pinned groups cannot be moved by bridge-derived dependencies, but bridge
    # nodes should still use the actual visible gap around that fixed surface.
    rects = sorted(
        (_group_rect(group) for group in incident_groups if _has_positive_bounding(group)),
        key=lambda rect: (rect[0] + rect[2]) / 2.0,
    )
    gap = _bridge_side_gap(settings)
    best_x: float | None = None
    best_available = 0.0
    for left_rect, right_rect in zip(rects, rects[1:]):
        slot_left = left_rect[2] + gap
        slot_right = right_rect[0] - gap
        available = slot_right - slot_left
        if available >= width and available > best_available:
            best_x = slot_left + (available - width) / 2.0
            best_available = available

    return best_x


def _bridge_direct_incident_groups(
    workflow: Workflow,
    node: Node,
    group_by_node_id: dict[int, Group],
) -> list[Group]:
    groups: dict[int, Group] = {}
    for link_id in node.input_links:
        link = workflow.links.get(link_id)
        if link is None:
            continue
        group = group_by_node_id.get(link.source)
        if group is not None:
            groups[group.id] = group

    for link_id in node.output_links:
        link = workflow.links.get(link_id)
        if link is None:
            continue
        group = group_by_node_id.get(link.target)
        if group is not None:
            groups[group.id] = group

    return list(groups.values())


def _bridge_side_gap(settings: LayoutSettings) -> float:
    return max(24.0, settings.node_h_gap * 0.5)


def _bridge_node_desired_y(
    workflow: Workflow,
    node: Node,
    _group_by_node_id: dict[int, Group],
) -> float:
    centers: list[float] = []
    for link_id in node.input_links:
        link = workflow.links.get(link_id)
        if link is None:
            continue
        centers.append(_bridge_source_port_y(workflow, link))
    for link_id in node.output_links:
        link = workflow.links.get(link_id)
        if link is None:
            continue
        centers.append(_bridge_target_port_y(workflow, link))
    if not centers:
        return node.y
    return (sum(centers) / len(centers)) - _node_visual_height(node) / 2.0


def _bridge_source_port_y(workflow: Workflow, link: Link) -> float:
    source = workflow.nodes.get(link.source)
    if source is None:
        return 0.0
    return _node_output_port_y(source, link.source_port)


def _bridge_target_port_y(workflow: Workflow, link: Link) -> float:
    target = workflow.nodes.get(link.target)
    if target is None:
        return 0.0
    return _node_input_port_y(target, link.target_port)


def _resolve_bridge_y(
    x: float,
    y: float,
    width: float,
    height: float,
    fixed_rects: list[tuple[float, float, float, float]],
    placed_rects: list[tuple[float, float, float, float]],
    settings: LayoutSettings,
) -> float:
    for _ in range(len(fixed_rects) + len(placed_rects) + 1):
        collision_bottom = _bridge_collision_bottom(x, y, width, height, fixed_rects, placed_rects)
        if collision_bottom is None:
            return y
        y = max(y + settings.node_v_gap, collision_bottom + settings.node_v_gap)
    return y


def _bridge_collision_bottom(
    x: float,
    y: float,
    width: float,
    height: float,
    fixed_rects: list[tuple[float, float, float, float]],
    placed_rects: list[tuple[float, float, float, float]],
) -> float | None:
    candidate = (x, y, x + width, y + height)
    for rect in [*fixed_rects, *placed_rects]:
        if _rects_overlap(candidate, _expand_rect(rect, 6.0)):
            return rect[3]
    return None


def _bridge_fixed_rects(
    workflow: Workflow,
    bridge_node_ids: set[int],
) -> list[tuple[float, float, float, float]]:
    rects = [
        _group_rect(group)
        for group in workflow.groups
        if _has_positive_bounding(group)
    ]
    rects.extend(
        _node_rect(node)
        for node in workflow.nodes.values()
        if node.id not in bridge_node_ids
        and (_is_decorative_node(node) or _is_pinned_node(workflow, node))
    )
    return rects


def _ungrouped_start_x(
    workflow: Workflow, settings: LayoutSettings, start_x_floor: float
) -> float:
    """Return the leftmost x for ungrouped nodes that clears every placed group.

    Without this clearance both the linked-layer placement and the vertical
    column packing collapse into the group's first column, overlapping the
    group's nodes horizontally.
    """
    max_right = 0.0
    for group in workflow.groups:
        if _has_positive_bounding(group):
            max_right = max(max_right, group.bounding[0] + group.bounding[2])
        elif group.nodes:
            group_max_x = max(n.x + _node_visual_width(n) for n in group.nodes)
            max_right = max(max_right, group_max_x)
    if max_right <= 0.0:
        return start_x_floor
    return max(start_x_floor, max_right + settings.group_h_gap)


def _has_internal_links(workflow: Workflow, nodes: list[Node]) -> bool:
    node_ids = {node.id for node in nodes}
    return any(link.source in node_ids and link.target in node_ids for link in workflow.links.values())


def _position_linked_ungrouped_nodes(
    workflow: Workflow,
    nodes: list[Node],
    settings: LayoutSettings,
    start_x: float,
) -> float:
    """Position linked ungrouped nodes in dataflow layers."""
    layers = _ungrouped_flow_layers(workflow, nodes)
    layer_to_nodes: dict[int, list[Node]] = {}
    for node in nodes:
        layer_to_nodes.setdefault(layers[node.id], []).append(node)

    layer_x_positions = _node_layer_x_positions(layer_to_nodes, start_x, settings)
    current_right = start_x
    for layer in sorted(layer_to_nodes):
        layer_nodes = sorted(layer_to_nodes[layer], key=lambda node: (_ungrouped_barycenter(workflow, node, layers), node.y, node.x, node.id))
        x = layer_x_positions[layer]
        y = 50.0
        column_width = 0.0
        for node in layer_nodes:
            node.x = x
            node.y = y
            y += _node_visual_height(node) + settings.node_v_gap
            column_width = max(column_width, _node_visual_width(node))
        current_right = max(current_right, x + column_width)

    return current_right


def _node_layer_x_positions(
    layer_to_nodes: dict[int, list[Node]],
    start_x: float,
    settings: LayoutSettings,
) -> dict[int, float]:
    """Use per-layer node widths for ungrouped dataflow columns."""
    positions: dict[int, float] = {}
    current_x = start_x
    for layer in sorted(layer_to_nodes):
        positions[layer] = current_x
        layer_width = max(
            (_node_visual_width(node) for node in layer_to_nodes[layer]),
            default=NODE_MIN_WIDTH,
        )
        current_x += layer_width + settings.node_h_gap
    return positions


def _position_external_sources_near_group_targets(
    workflow: Workflow,
    nodes: list[Node],
    settings: LayoutSettings,
) -> float:
    """Pull ungrouped source nodes beside the grouped blocks they feed."""
    group_by_node_id = _group_by_node_id(workflow)
    current_right = 0.0
    for node in sorted(nodes, key=lambda item: (item.y, item.x, item.id)):
        if not _is_external_group_source_node(workflow, node, group_by_node_id):
            continue

        targets = [
            workflow.nodes[link.target]
            for link_id in node.output_links
            if (link := workflow.links.get(link_id)) is not None
            and link.target in workflow.nodes
            and link.target in group_by_node_id
        ]
        if not targets:
            continue

        x, y = _resolve_external_source_position(workflow, node, targets, group_by_node_id, settings)
        node.x = x
        node.y = y
        current_right = max(current_right, node.x + _node_visual_width(node))
    return current_right


def _is_external_group_source_node(
    workflow: Workflow,
    node: Node,
    group_by_node_id: dict[int, Group],
) -> bool:
    if (
        node.input_links
        or not node.output_links
        or node.id in group_by_node_id
        or _is_pinned_node(workflow, node)
        or _is_decorative_node(node)
        or _is_reroute_node(node)
        or _is_set_node(node)
        or _is_get_node(node)
    ):
        return False

    return any(
        (link := workflow.links.get(link_id)) is not None
        and link.target in group_by_node_id
        for link_id in node.output_links
    )


def _resolve_external_source_position(
    workflow: Workflow,
    node: Node,
    targets: list[Node],
    group_by_node_id: dict[int, Group],
    settings: LayoutSettings,
) -> tuple[float, float]:
    left, top, right, bottom = _external_source_anchor_bounds(targets, group_by_node_id)
    node_w = _node_visual_width(node)
    node_h = _node_visual_height(node)
    gap = max(24.0, settings.node_h_gap * 0.5)
    center_y = top + max(0.0, (bottom - top - node_h) / 2.0)
    center_x = left + max(0.0, (right - left - node_w) / 2.0)
    candidates = [
        (left - node_w - gap, center_y),
        (center_x, top - node_h - gap),
        (center_x, bottom + gap),
        (right + gap, center_y),
    ]
    ignored_node_ids = {node.id}
    for x, y in candidates:
        if _first_node_collision(workflow, x, y, node_w, node_h, ignored_node_ids) is None:
            return x, y
    return left - node_w - gap, center_y


def _external_source_anchor_bounds(
    targets: list[Node],
    group_by_node_id: dict[int, Group],
) -> tuple[float, float, float, float]:
    """Use target group surfaces so later group padding does not cover sources."""
    groups = {
        group.id: group
        for target in targets
        if (group := group_by_node_id.get(target.id)) is not None
    }
    if groups:
        left = min(group.bounding[0] for group in groups.values())
        top = min(group.bounding[1] for group in groups.values())
        right = max(group.bounding[0] + group.bounding[2] for group in groups.values())
        bottom = max(group.bounding[1] + group.bounding[3] for group in groups.values())
        return left, top, right, bottom

    left = min(target.x for target in targets)
    top = min(target.y for target in targets)
    right = max(target.x + _node_visual_width(target) for target in targets)
    bottom = max(target.y + _node_visual_height(target) for target in targets)
    return left, top, right, bottom


def _position_control_nodes_near_targets(
    workflow: Workflow,
    nodes: list[Node],
    settings: LayoutSettings,
) -> float:
    """Place small primitive control nodes beside their downstream consumers."""
    node_ids = {node.id for node in nodes}
    group_by_node_id = _group_by_node_id(workflow)
    controls_by_anchor: dict[tuple[int, ...], list[tuple[Node, list[tuple[int, Node, Link]]]]] = {}

    for node in nodes:
        if not _is_control_source_node(workflow, node):
            continue
        if _is_pinned_node(workflow, node):
            continue

        target_links = _control_target_links(workflow, node, node_ids)
        target_links = _local_control_target_links(node, target_links, group_by_node_id)
        if not target_links:
            continue

        sampler_links = [
            (target_port, target, link)
            for target_port, target, link in target_links
            if _is_sampler_node(target)
        ]
        anchor_links = sampler_links or target_links
        anchor_key = tuple(sorted({target.id for _, target, _ in anchor_links}))
        controls_by_anchor.setdefault(anchor_key, []).append((node, anchor_links))

    current_right = 0.0
    gap = max(24.0, settings.node_h_gap * 0.5)
    stack_gap = max(12.0, settings.node_v_gap * 0.25)

    for entries in controls_by_anchor.values():
        targets = {target.id: target for _, target_links in entries for _, target, _ in target_links}
        if not targets:
            continue

        entries.sort(key=lambda item: (_control_desired_center(item[1]), item[0].y, item[0].id))
        control_width = max(_node_visual_width(node) for node, _ in entries)
        total_height = sum(_node_visual_height(node) for node, _ in entries)
        total_height += stack_gap * max(0, len(entries) - 1)
        x, y = _resolve_control_stack_position(
            workflow,
            entries,
            targets,
            control_width,
            total_height,
            gap,
            stack_gap,
        )

        for node, _ in entries:
            node.x = x
            node.y = y
            y += _node_visual_height(node) + stack_gap
            current_right = max(current_right, node.x + _node_visual_width(node))

    return current_right


def _local_control_target_links(
    node: Node,
    target_links: list[tuple[int, Node, Link]],
    group_by_node_id: dict[int, Group],
) -> list[tuple[int, Node, Link]]:
    """Keep grouped control nodes from stretching their group toward external consumers."""
    source_group = group_by_node_id.get(node.id)
    if source_group is None:
        return target_links
    return [
        (target_port, target, link)
        for target_port, target, link in target_links
        if group_by_node_id.get(target.id) is source_group
    ]


def _resolve_control_stack_position(
    workflow: Workflow,
    entries: list[tuple[Node, list[tuple[int, Node, Link]]]],
    targets: dict[int, Node],
    control_width: float,
    total_height: float,
    gap: float,
    stack_gap: float,
) -> tuple[float, float]:
    left, top, right, bottom = _control_anchor_bounds(workflow, targets.values())
    center_x = left + max(0.0, (right - left - control_width) / 2.0)
    desired_y = _control_stack_top(entries, stack_gap)
    candidates = [
        (left - control_width - gap, desired_y),
        (center_x, top - total_height - gap),
        (center_x, bottom + gap),
        (right + gap, desired_y),
    ]
    ignored_node_ids = {node.id for node, _ in entries}

    for x, y in candidates:
        if not _control_stack_collides(
            workflow,
            entries,
            x,
            y,
            control_width,
            stack_gap,
            ignored_node_ids,
        ):
            return x, y

    return left - control_width - gap, desired_y


def _control_anchor_bounds(workflow: Workflow, targets: Iterable[Node]) -> tuple[float, float, float, float]:
    left = math.inf
    top = math.inf
    right = -math.inf
    bottom = -math.inf
    for target in targets:
        target_left = _control_target_left_edge(workflow, target)
        target_top = _control_target_top_edge(workflow, target)
        target_right = _control_target_right_edge(workflow, target)
        target_bottom = _control_target_bottom_edge(workflow, target)
        left = min(left, target_left)
        top = min(top, target_top)
        right = max(right, target_right)
        bottom = max(bottom, target_bottom)
    return left, top, right, bottom


def _control_stack_collides(
    workflow: Workflow,
    entries: list[tuple[Node, list[tuple[int, Node, Link]]]],
    x: float,
    y: float,
    control_width: float,
    stack_gap: float,
    ignored_node_ids: set[int],
) -> bool:
    current_y = y
    for node, _ in entries:
        if _first_node_collision(
            workflow,
            x,
            current_y,
            control_width,
            _node_visual_height(node),
            ignored_node_ids,
        ) is not None:
            return True
        current_y += _node_visual_height(node) + stack_gap
    return False


def _control_target_links(
    workflow: Workflow,
    node: Node,
    _movable_node_ids: set[int],
) -> list[tuple[int, Node, Link]]:
    target_links: list[tuple[int, Node, Link]] = []
    for link_id in node.output_links:
        link = workflow.links.get(link_id)
        if link is None or not _is_control_link_for_node(workflow, node, link):
            continue
        target = workflow.nodes.get(link.target)
        if target is None:
            continue
        target_links.append((link.target_port, target, link))
    return target_links


def _control_target_left_edge(workflow: Workflow, target: Node) -> float:
    pinned_groups = [
        group for group in workflow.groups if group.pinned and _node_in_group(target, group)
    ]
    if pinned_groups:
        return min(group.bounding[0] for group in pinned_groups)
    return target.x


def _control_target_top_edge(workflow: Workflow, target: Node) -> float:
    pinned_groups = [
        group for group in workflow.groups if group.pinned and _node_in_group(target, group)
    ]
    if pinned_groups:
        return min(group.bounding[1] for group in pinned_groups)
    return target.y


def _control_target_right_edge(workflow: Workflow, target: Node) -> float:
    pinned_groups = [
        group for group in workflow.groups if group.pinned and _node_in_group(target, group)
    ]
    if pinned_groups:
        return max(group.bounding[0] + group.bounding[2] for group in pinned_groups)
    return target.x + _node_visual_width(target)


def _control_target_bottom_edge(workflow: Workflow, target: Node) -> float:
    pinned_groups = [
        group for group in workflow.groups if group.pinned and _node_in_group(target, group)
    ]
    if pinned_groups:
        return max(group.bounding[1] + group.bounding[3] for group in pinned_groups)
    return target.y + _node_visual_height(target)


def _control_stack_top(
    entries: list[tuple[Node, list[tuple[int, Node, Link]]]],
    stack_gap: float,
) -> float:
    total_height = sum(_node_visual_height(node) for node, _ in entries)
    total_gap = stack_gap * max(0, len(entries) - 1)
    desired_centers = [_control_desired_center(target_links) for _, target_links in entries]
    if not desired_centers:
        return 0.0
    center = sum(desired_centers) / len(desired_centers)
    return center - (total_height + total_gap) / 2.0


def _control_desired_center(target_links: list[tuple[int, Node, Link]]) -> float:
    if not target_links:
        return 0.0
    centers = [
        _node_input_port_y(target, target_port)
        for target_port, target, _ in target_links
    ]
    return sum(centers) / len(centers)


def _is_control_source_node(workflow: Workflow, node: Node) -> bool:
    if (
        node.input_links
        or not node.output_links
        or _is_decorative_node(node)
        or _is_reroute_node(node)
        or _is_set_node(node)
        or _is_get_node(node)
    ):
        return False

    links = [workflow.links.get(link_id) for link_id in node.output_links]
    existing_links = [link for link in links if link is not None]
    if not existing_links:
        return False

    return all(_is_control_link_for_node(workflow, node, link) for link in existing_links)


def _is_primitive_control_link(link: Link) -> bool:
    return str(link.type or "").upper() in {
        "BOOLEAN",
        "COMBO",
        "FLOAT",
        "INT",
        "SEED",
        "STRING",
    }


def _is_control_link_for_node(workflow: Workflow, node: Node, link: Link) -> bool:
    if _is_primitive_control_link(link):
        return True
    if not _is_sampler_control_source(node):
        return False
    target = workflow.nodes.get(link.target)
    return target is not None and _is_sampler_node(target)


def _is_sampler_control_source(node: Node) -> bool:
    node_type = node.type.lower()
    return any(
        token in node_type
        for token in ("seed", "emptylatent", "empty latent", "latentimage", "latent image")
    )


def _is_sampler_node(node: Node) -> bool:
    return "sampler" in node.type.lower()


def _position_text_previews_near_sources(workflow: Workflow, settings: LayoutSettings) -> None:
    """Keep terminal text preview nodes near the output they display."""
    gap = max(24.0, settings.node_h_gap * 0.5)
    for node in sorted(workflow.nodes.values(), key=lambda item: (item.y, item.x, item.id)):
        if not _is_terminal_text_preview_node(node) or _is_pinned_node(workflow, node):
            continue
        source = _single_input_source(workflow, node)
        if source is None:
            continue
        link = workflow.links.get(node.input_links[0])
        if link is None:
            continue
        desired_y = _node_output_port_y(source, link.source_port) - _node_input_port_offset(node, link.target_port)
        node.x, node.y = _resolve_text_preview_position(workflow, node, source, desired_y, gap)


def _resolve_text_preview_position(
    workflow: Workflow,
    node: Node,
    source: Node,
    desired_y: float,
    gap: float,
) -> tuple[float, float]:
    node_w = _node_visual_width(node)
    node_h = _node_visual_height(node)
    source_w = _node_visual_width(source)
    source_h = _node_visual_height(source)
    centered_x = source.x + max(0.0, (source_w - node_w) / 2.0)
    candidates = [
        (source.x + source_w + gap, desired_y),
        (source.x + source_w + gap, source.y + max(0.0, (source_h - node_h) / 2.0)),
        (centered_x, source.y - node_h - gap),
        (centered_x, source.y + source_h + gap),
        (source.x - node_w - gap, desired_y),
    ]
    ignored_node_ids = {node.id, source.id}
    for x, y in candidates:
        if _first_node_collision(
            workflow,
            x,
            y,
            node_w,
            node_h,
            ignored_node_ids,
        ) is None:
            return x, y
    return node.x, node.y


def _is_terminal_text_preview_node(node: Node) -> bool:
    return node.type == "ShowText|pysssss" and bool(node.input_links) and not node.output_links


def _position_virtual_set_get_nodes(workflow: Workflow, settings: LayoutSettings) -> None:
    """Keep KJNodes Set/Get hubs directly beside their physical endpoint."""
    gap = _virtual_hub_gap(settings)
    placed_hub_ids: set[int] = set()

    set_groups: dict[int, list[tuple[int, Node, Node]]] = {}
    get_groups: dict[int, list[tuple[int, Node, Node]]] = {}
    for node in workflow.nodes.values():
        if _is_set_node(node):
            source = _single_input_source(workflow, node)
            if source is not None:
                source_port = workflow.links[node.input_links[0]].source_port
                set_groups.setdefault(source.id, []).append((source_port, node, source))
        elif _is_get_node(node):
            target = _single_output_target(workflow, node)
            if target is not None:
                target_port = workflow.links[node.output_links[0]].target_port
                get_groups.setdefault(target.id, []).append((target_port, node, target))

    for entries in set_groups.values():
        entries.sort(key=lambda item: (item[0], item[1].id))
        total_height = sum(_node_visual_height(node) for _, node, _ in entries)
        total_gap = gap * max(0, len(entries) - 1)
        desired_centers = [
            _node_output_port_y(source, source_port) - _node_input_port_offset(node, 0)
            + _node_visual_height(node) / 2.0
            for source_port, node, source in entries
        ]
        y = (sum(desired_centers) / len(desired_centers)) - (total_height + total_gap) / 2.0
        for source_port, node, source in entries:
            preferred_x = source.x + _node_visual_width(source) + gap
            node.x, node.y = _resolve_virtual_hub_position(
                workflow,
                node,
                preferred_x,
                y,
                direction=1,
                gap=gap,
                endpoint=source,
                placed_hub_ids=placed_hub_ids,
            )
            placed_hub_ids.add(node.id)
            y += _node_visual_height(node) + gap

    for entries in get_groups.values():
        entries.sort(key=lambda item: (item[0], item[1].id))
        total_height = sum(_node_visual_height(node) for _, node, _ in entries)
        total_gap = gap * max(0, len(entries) - 1)
        desired_centers = [
            _node_input_port_y(target, target_port) - _node_output_port_offset(node, 0)
            + _node_visual_height(node) / 2.0
            for target_port, node, target in entries
        ]
        y = (sum(desired_centers) / len(desired_centers)) - (total_height + total_gap) / 2.0
        for target_port, node, target in entries:
            preferred_x = target.x - _node_visual_width(node) - gap
            node.x, node.y = _resolve_virtual_hub_position(
                workflow,
                node,
                preferred_x,
                y,
                direction=-1,
                gap=gap,
                endpoint=target,
                placed_hub_ids=placed_hub_ids,
            )
            placed_hub_ids.add(node.id)
            y += _node_visual_height(node) + gap


def _resolve_virtual_hub_position(
    workflow: Workflow,
    node: Node,
    preferred_x: float,
    preferred_y: float,
    *,
    direction: int,
    gap: float,
    endpoint: Node,
    placed_hub_ids: set[int],
) -> tuple[float, float]:
    """Choose a local endpoint-adjacent hub position before falling back sideways."""
    endpoint_w = _node_visual_width(endpoint)
    endpoint_h = _node_visual_height(endpoint)
    node_w = _node_visual_width(node)
    node_h = _node_visual_height(node)
    centered_x = endpoint.x + max(0.0, (endpoint_w - node_w) / 2.0)
    candidates = [
        (preferred_x, preferred_y),
        (centered_x, endpoint.y - node_h - gap),
        (centered_x, endpoint.y + endpoint_h + gap),
    ]
    ignored_node_ids = {node.id, endpoint.id}
    for x, y in candidates:
        if _first_virtual_hub_collision(
            workflow,
            node,
            x,
            y,
            ignored_node_ids,
            placed_hub_ids,
        ) is None:
            return x, y

    for x, y in _virtual_hub_vertical_fallback_candidates(
        preferred_x,
        preferred_y,
        node_h,
        gap,
    ):
        if _first_virtual_hub_collision(
            workflow,
            node,
            x,
            y,
            ignored_node_ids,
            placed_hub_ids,
        ) is None:
            return x, y

    return (
        _resolve_virtual_hub_x(
            workflow,
            node,
            preferred_x,
            preferred_y,
            direction=direction,
            gap=gap,
            endpoint=endpoint,
            placed_hub_ids=placed_hub_ids,
        ),
        preferred_y,
    )


def _virtual_hub_vertical_fallback_candidates(
    x: float,
    preferred_y: float,
    node_height: float,
    gap: float,
) -> list[tuple[float, float]]:
    """Try same-side vertical slots before allowing a virtual hub to drift sideways."""
    step = max(node_height + gap, gap)
    candidates: list[tuple[float, float]] = []
    for index in range(1, VIRTUAL_HUB_VERTICAL_FALLBACK_STEPS + 1):
        candidates.append((x, preferred_y + step * index))
        candidates.append((x, preferred_y - step * index))
    return candidates


def _resolve_virtual_hub_x(
    workflow: Workflow,
    node: Node,
    preferred_x: float,
    y: float,
    *,
    direction: int,
    gap: float,
    endpoint: Node,
    placed_hub_ids: set[int],
) -> float:
    """Move a virtual hub sideways until it no longer covers a visible node."""
    x = preferred_x
    ignored_node_ids = {node.id, endpoint.id}
    for _ in range(len(workflow.nodes) + 1):
        collision = _first_virtual_hub_collision(
            workflow,
            node,
            x,
            y,
            ignored_node_ids,
            placed_hub_ids,
        )
        if collision is None:
            return x
        if direction < 0:
            x = min(x, collision.x - _node_visual_width(node) - gap)
        else:
            x = max(x, collision.x + _node_visual_width(collision) + gap)
    return x


def _first_virtual_hub_collision(
    workflow: Workflow,
    node: Node,
    x: float,
    y: float,
    ignored_node_ids: set[int],
    placed_hub_ids: set[int],
) -> Node | None:
    if _overlaps_fixed_geometry(workflow, x, y, _node_visual_width(node), _node_visual_height(node), ignored_node_ids):
        return node

    for other in workflow.nodes.values():
        if other.id in ignored_node_ids:
            continue
        if (_is_set_node(other) or _is_get_node(other)) and other.id not in placed_hub_ids:
            continue
        if _rectangles_overlap(
            x,
            y,
            _node_visual_width(node),
            _node_visual_height(node),
            other.x,
            other.y,
            _node_visual_width(other),
            _node_visual_height(other),
            padding=6.0,
        ):
            return other
    return None


def _first_node_collision(
    workflow: Workflow,
    x: float,
    y: float,
    width: float,
    height: float,
    ignored_node_ids: set[int],
) -> Node | None:
    if _overlaps_fixed_geometry(workflow, x, y, width, height, ignored_node_ids):
        return next(
            (
                node
                for node in workflow.nodes.values()
                if _is_pinned_node(workflow, node) and node.id not in ignored_node_ids
            ),
            next(iter(workflow.nodes.values()), None),
        )

    for other in workflow.nodes.values():
        if other.id in ignored_node_ids:
            continue
        if _rectangles_overlap(
            x,
            y,
            width,
            height,
            other.x,
            other.y,
            _node_visual_width(other),
            _node_visual_height(other),
            padding=6.0,
        ):
            return other
    return None


def _rectangles_overlap(
    left_a: float,
    top_a: float,
    width_a: float,
    height_a: float,
    left_b: float,
    top_b: float,
    width_b: float,
    height_b: float,
    *,
    padding: float = 0.0,
) -> bool:
    return not (
        left_a + width_a + padding <= left_b
        or left_b + width_b + padding <= left_a
        or top_a + height_a + padding <= top_b
        or top_b + height_b + padding <= top_a
    )


def _assign_virtual_hub_groups_to_endpoints(workflow: Workflow) -> None:
    """Assign virtual hubs to the group of the real node they physically touch."""
    if not workflow.groups:
        return

    endpoint_by_hub_id: dict[int, Node] = {}
    for node in workflow.nodes.values():
        if _is_set_node(node):
            endpoint = _single_input_source(workflow, node)
        elif _is_get_node(node):
            endpoint = _single_output_target(workflow, node)
        else:
            endpoint = None
        if endpoint is not None:
            endpoint_by_hub_id[node.id] = endpoint

    if not endpoint_by_hub_id:
        return

    group_by_node_id: dict[int, Group] = {}
    for group in workflow.groups:
        for node in group.nodes:
            if node.id not in endpoint_by_hub_id:
                group_by_node_id[node.id] = group

    for group in workflow.groups:
        group.nodes = [node for node in group.nodes if node.id not in endpoint_by_hub_id]
    workflow.ungrouped_nodes = [
        node for node in workflow.ungrouped_nodes if node.id not in endpoint_by_hub_id
    ]

    for hub_id, endpoint in endpoint_by_hub_id.items():
        hub = workflow.nodes[hub_id]
        endpoint_group = group_by_node_id.get(endpoint.id)
        if endpoint_group is None:
            workflow.ungrouped_nodes.append(hub)
        else:
            endpoint_group.nodes.append(hub)


def _separate_unpinned_nodes_from_pinned_geometry(workflow: Workflow, settings: LayoutSettings) -> None:
    fixed_rects = _fixed_geometry_rects(workflow)
    if not fixed_rects:
        return

    movable_nodes = [
        node for node in workflow.nodes.values()
        if not _is_pinned_node(workflow, node) and not _is_virtual_hub_node(node)
    ]
    gap = max(12.0, settings.node_v_gap * 0.25)

    for node in sorted(movable_nodes, key=lambda item: (item.y, item.x, item.id)):
        ignored_node_ids = {node.id}
        for _ in range(len(fixed_rects) + 1):
            collision = _first_overlap_with_fixed_geometry(workflow, node, ignored_node_ids)
            if collision is None:
                break
            node.y = collision[3] + gap


def _first_overlap_with_fixed_geometry(
    workflow: Workflow,
    node: Node,
    ignored_node_ids: set[int],
) -> tuple[float, float, float, float] | None:
    rect = _node_rect(node)
    for fixed_rect in _fixed_geometry_rects(workflow, ignored_node_ids):
        if _rects_overlap(rect, _expand_rect(fixed_rect, 6.0)):
            return fixed_rect
    return None


def _virtual_hub_gap(settings: LayoutSettings) -> float:
    return max(VIRTUAL_HUB_MIN_GAP, min(VIRTUAL_HUB_MAX_GAP, settings.node_h_gap * 0.5))


def _single_input_source(workflow: Workflow, node: Node) -> Node | None:
    if len(node.input_links) != 1:
        return None
    link = workflow.links.get(node.input_links[0])
    if link is None:
        return None
    return workflow.nodes.get(link.source)


def _single_output_target(workflow: Workflow, node: Node) -> Node | None:
    if len(node.output_links) != 1:
        return None
    link = workflow.links.get(node.output_links[0])
    if link is None:
        return None
    return workflow.nodes.get(link.target)


def _node_input_port_y(node: Node, port_index: int) -> float:
    return node.y + _node_input_port_offset(node, port_index)


def _node_output_port_y(node: Node, port_index: int) -> float:
    return node.y + _node_output_port_offset(node, port_index)


def _node_input_port_offset(node: Node, port_index: int) -> float:
    base_offset = 6.0 if _is_reroute_node(node) else NODE_SLOT_OFFSET
    row_gap = 0.0 if _is_reroute_node(node) else NODE_ROW_GAP
    return NODE_TITLE_HEIGHT + base_offset + NODE_ROW_HEIGHT / 2.0 + port_index * (
        NODE_ROW_HEIGHT + row_gap
    )


def _node_output_port_offset(node: Node, port_index: int) -> float:
    base_offset = 6.0 if _is_reroute_node(node) else NODE_SLOT_OFFSET
    return NODE_TITLE_HEIGHT + base_offset + NODE_ROW_HEIGHT / 2.0 + port_index * NODE_ROW_HEIGHT


def _order_ungrouped_nodes_by_flow(workflow: Workflow, nodes: list[Node]) -> list[Node]:
    """Order ungrouped nodes by local dataflow layer, then original position."""
    layers = _ungrouped_flow_layers(workflow, nodes)
    return sorted(nodes, key=lambda node: (layers[node.id], node.y, node.x, node.id))


def _ungrouped_flow_layers(workflow: Workflow, nodes: list[Node]) -> dict[int, int]:
    """Return longest-path dataflow layers for a set of ungrouped nodes."""
    node_ids = {node.id for node in nodes}
    if len(node_ids) <= 1:
        return {node.id: 0 for node in nodes}

    adj: dict[int, list[int]] = {node_id: [] for node_id in node_ids}
    rev_adj: dict[int, list[int]] = {node_id: [] for node_id in node_ids}
    for link in workflow.links.values():
        if link.source in node_ids and link.target in node_ids:
            adj[link.source].append(link.target)
            rev_adj[link.target].append(link.source)

    layers: dict[int, int] = {}
    sources = [node_id for node_id in node_ids if not rev_adj[node_id]]
    queue: list[tuple[int, int]] = [(node_id, 0) for node_id in sources]
    while queue:
        node_id, layer = queue.pop(0)
        if layer <= layers.get(node_id, -1):
            continue
        layers[node_id] = layer
        for target_id in adj[node_id]:
            queue.append((target_id, layer + 1))

    for node_id in node_ids:
        layers.setdefault(node_id, 0)

    return layers


def _ungrouped_barycenter(workflow: Workflow, node: Node, layers: dict[int, int]) -> float:
    """Return original-position barycenter for adjacent ungrouped nodes."""
    neighbours: list[Node] = []
    for link in workflow.links.values():
        other_id: int | None = None
        if link.source == node.id and link.target in layers:
            other_id = link.target
        elif link.target == node.id and link.source in layers:
            other_id = link.source
        if other_id is not None and other_id in workflow.nodes:
            neighbours.append(workflow.nodes[other_id])

    if not neighbours:
        return node.y
    return sum(neighbour.y for neighbour in neighbours) / len(neighbours)


def _position_decorative_nodes_left(workflow: Workflow, settings: LayoutSettings) -> float:
    """
    Position Note / Markdown / Label nodes as a left-side annotation column.

    Returns the right edge of the decorative column so later layout phases can
    start to its right.
    """
    logger.debug("Positioning decorative nodes on the left edge")

    decorative = [
        n
        for n in workflow.nodes.values()
        if _is_decorative_node(n) and not _is_pinned_node(workflow, n)
    ]
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
    Update all group bounding boxes to the compact bounds of their contents.
    """
    logger.debug("Updating group bounding boxes")
    
    for group in workflow.groups:
        if not group.nodes or group.pinned:
            continue
        
        min_x, min_y, max_x, max_y = _group_content_bounds(group)
        content_left = min_x - settings.group_padding
        content_top = min_y - _group_top_padding(settings)
        content_right = max_x + settings.group_padding
        content_bottom = max_y + _group_bottom_padding(settings)

        group.bounding = [
            content_left,
            content_top,
            content_right - content_left,
            content_bottom - content_top,
        ]
        
        logger.debug(f"Group {group.name} bounding box updated to {group.bounding}")


def _resolve_group_geometry_overlaps(workflow: Workflow, settings: LayoutSettings) -> None:
    """Move whole movable groups until group surfaces no longer overlap."""
    groups = [group for group in workflow.groups if _has_positive_bounding(group)]
    groups.sort(key=lambda group: (group.bounding[1], group.bounding[0], group.id))
    fixed_group_obstacles = [
        group for group in groups if _group_has_fixed_geometry(group)
    ]
    grouped_node_ids = {node.id for group in workflow.groups for node in group.nodes}
    fixed_node_obstacles = [
        _node_rect(node)
        for node in workflow.nodes.values()
        if node.id not in grouped_node_ids
    ]
    placed_groups: list[Group] = []

    for group in groups:
        if _group_has_fixed_geometry(group):
            placed_groups.append(group)
            continue

        member_ids = {node.id for node in group.nodes}
        for _ in range(len(workflow.groups) + len(workflow.nodes) + 1):
            group_rect = _group_rect(group)
            obstacles = [
                _group_rect(other)
                for other in [*fixed_group_obstacles, *placed_groups]
                if other is not group
                if _rects_overlap(group_rect, _group_rect(other))
            ]
            obstacles.extend(rect for rect in fixed_node_obstacles if _rects_overlap(group_rect, rect))
            obstacles.extend(
                _node_rect(node)
                for other in placed_groups
                for node in other.nodes
                if node.id not in member_ids and _rects_overlap(group_rect, _node_rect(node))
            )

            if not obstacles:
                break

            lowest_obstacle_bottom = max(rect[3] for rect in obstacles)
            delta_y = (lowest_obstacle_bottom + settings.group_v_gap) - group.bounding[1]
            _move_group_geometry(group, 0.0, max(delta_y, settings.group_v_gap))

        placed_groups.append(group)


def _group_has_fixed_geometry(group: Group) -> bool:
    """Return True when moving the group would violate a pin contract."""
    return group.pinned or any(node.pinned for node in group.nodes)


def _group_by_node_id(workflow: Workflow) -> dict[int, Group]:
    return {node.id: group for group in workflow.groups for node in group.nodes}


def _move_group_geometry(group: Group, delta_x: float, delta_y: float) -> None:
    """Move a group rectangle and all member nodes as one visual unit."""
    group.bounding[0] += delta_x
    group.bounding[1] += delta_y
    for node in group.nodes:
        node.x += delta_x
        node.y += delta_y


def _group_rect(group: Group) -> tuple[float, float, float, float]:
    x, y, width, height = group.bounding
    return x, y, x + width, y + height


def _node_rect(node: Node) -> tuple[float, float, float, float]:
    return (
        node.x,
        node.y,
        node.x + _node_visual_width(node),
        node.y + _node_visual_height(node),
    )


def _rects_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _overlaps_fixed_geometry(
    workflow: Workflow,
    x: float,
    y: float,
    width: float,
    height: float,
    ignored_node_ids: set[int],
) -> bool:
    """Return True when a candidate rectangle covers pinned layout geometry."""
    candidate = (x, y, x + width, y + height)
    return any(
        _rects_overlap(candidate, _expand_rect(fixed_rect, 6.0))
        for fixed_rect in _fixed_geometry_rects(workflow, ignored_node_ids)
    )


def _fixed_geometry_rects(
    workflow: Workflow,
    ignored_node_ids: set[int] | None = None,
) -> list[tuple[float, float, float, float]]:
    """Pinned nodes and pinned groups are hard obstacles for movable geometry."""
    ignored_node_ids = ignored_node_ids or set()
    rects = [
        _group_rect(group)
        for group in workflow.groups
        if group.pinned and _has_positive_bounding(group)
    ]
    rects.extend(
        _node_rect(node)
        for node in workflow.nodes.values()
        if _is_pinned_node(workflow, node)
        and not _is_virtual_hub_node(node)
        and node.id not in ignored_node_ids
    )
    return rects


def _expand_rect(
    rect: tuple[float, float, float, float],
    padding: float,
) -> tuple[float, float, float, float]:
    return (
        rect[0] - padding,
        rect[1] - padding,
        rect[2] + padding,
        rect[3] + padding,
    )


def _group_content_bounds(group: Group) -> tuple[float, float, float, float]:
    """Return [left, top, right, bottom] bounds for the group's node content."""
    min_x = min(n.x for n in group.nodes)
    max_x = max(n.x + _node_visual_width(n) for n in group.nodes)
    min_y = min(n.y for n in group.nodes)
    max_y = max(n.y + _node_visual_height(n) for n in group.nodes)
    return min_x, min_y, max_x, max_y


def _has_positive_bounding(group: Group) -> bool:
    """Return True when the group has a usable, user-authored rectangle."""
    return bool(group.bounding and len(group.bounding) >= 4 and group.bounding[2] > 0 and group.bounding[3] > 0)


def _is_pinned_node(workflow: Workflow, node: Node) -> bool:
    """Return True when ComfyUI pin flags should keep a node in place."""
    if node.pinned:
        return True
    return any(
        group.pinned and _node_belongs_to_group(node, group)
        for group in workflow.groups
    )


def _node_belongs_to_group(node: Node, group: Group) -> bool:
    """Use assigned membership first so overlaps do not become implicit pins."""
    if group.nodes:
        return any(member.id == node.id for member in group.nodes)
    return _node_in_group(node, group)


def _is_decorative_node(node: Node) -> bool:
    normalized_type = "".join(character for character in node.type.lower() if character.isalnum())
    return "note" in normalized_type or normalized_type.startswith("label")


def _is_image_io_node(node: Node) -> bool:
    normalized_type = "".join(character for character in node.type.lower() if character.isalnum())
    return normalized_type.startswith("loadimage") or normalized_type.startswith("saveimage")


def _is_set_node(node: Node) -> bool:
    return node.type == "SetNode"


def _is_get_node(node: Node) -> bool:
    return node.type == "GetNode"


def _is_virtual_hub_node(node: Node) -> bool:
    return _is_set_node(node) or _is_get_node(node)


def _is_reroute_node(node: Node) -> bool:
    return "reroute" in node.type.lower()


def _node_visual_width(node: Node) -> float:
    return node.size[0] if node.size[0] > 0 else 200.0


def _node_visual_height(node: Node) -> float:
    return node.size[1] if node.size[1] > 0 else 60.0


def _node_center(node: Node) -> tuple[float, float]:
    return node.x + _node_visual_width(node) / 2.0, node.y + _node_visual_height(node) / 2.0


def _group_visual_height(group: Group, settings: LayoutSettings) -> float:
    _, _, _, max_y = _group_content_bounds(group)
    min_y = min(n.y for n in group.nodes)
    return (max_y - min_y) + _group_top_padding(settings) + _group_bottom_padding(settings)


def _estimate_vertical_packing_budget(items, height_fn, gap: float) -> float:
    if not items:
        return 0.0

    heights = [max(1.0, float(height_fn(item))) for item in items]
    total_height = sum(heights) + gap * max(0, len(heights) - 1)
    # Prefer using vertical space before widening the workflow, but still allow
    # larger workflows to open extra columns before long vertical stacks create
    # avoidable right-to-left links.
    target_columns = max(1, min(len(heights), int(math.sqrt(len(heights)) / 1.25) or 1))
    tallest = max(heights)
    return max(tallest, total_height / target_columns)


def _estimate_group_row_width(groups: list[Group], settings: LayoutSettings) -> float:
    """Return a soft row width so group layout can wrap downward."""
    if not groups:
        return 0.0

    sizes: list[tuple[float, float]] = []
    for group in groups:
        min_x, _min_y, max_x, max_y = _group_content_bounds(group)
        sizes.append(
            (
                (max_x - min_x) + 2 * settings.group_padding,
                (max_y - _min_y) + _group_top_padding(settings) + _group_bottom_padding(settings),
            )
        )

    total_area = sum(width * height for width, height in sizes)
    max_width = max(width for width, _ in sizes)
    average_width = sum(width for width, _ in sizes) / len(sizes)
    width_from_area = math.sqrt(total_area * LAYOUT_GROUP_TARGET_ASPECT_RATIO)
    width_cap = max_width + average_width * LAYOUT_GROUP_ROW_MAX_AVERAGE_WIDTHS
    minimum_pair_width = (
        sum(width for width, _ in sizes[:2]) + settings.group_h_gap
        if len(sizes) > 1
        else max_width
    )
    return max(max_width, minimum_pair_width, min(width_from_area, width_cap))


def _clamp_layout_distance(value: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return DEFAULT_NODE_X_DISTANCE

    if not math.isfinite(numeric):
        return DEFAULT_NODE_X_DISTANCE

    return max(LAYOUT_DISTANCE_MIN, min(LAYOUT_DISTANCE_MAX, numeric))
