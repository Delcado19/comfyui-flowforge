"""
Layout algorithm for ComfyUI FlowForge.
Implements the six-phase pipeline as described in the README.
"""

from .model import Node, Group, Workflow
from .logger import setup_logger

# Set up logger for this module
logger = setup_logger(__name__)

# Spacing defaults (can be adjusted)
NODE_H_GAP = 80   # Horizontal gap between node columns within a group
NODE_V_GAP = 40   # Vertical gap between nodes in the same column
GROUP_H_GAP = 200 # Horizontal gap between group columns
GROUP_V_GAP = 100 # Vertical gap between groups stacked in the same column
GROUP_PADDING = 50 # Padding inside a group's bounding box


def apply(workflow: Workflow) -> Workflow:
    """
    Apply the layout algorithm to the workflow.
    This function orchestrates the six-phase pipeline.
    
    Args:
        workflow: The workflow to layout.
        
    Returns:
        The workflow with updated node positions and group bounding boxes.
    """
    logger.info("Starting layout algorithm")
    
    try:
        # Phase 1: Group Membership
        logger.debug("Phase 1: Group Membership")
        _assign_groups(workflow)
        
        # Phase 2: Inter-Group Topology
        logger.debug("Phase 2: Inter-Group Topology")
        _order_groups(workflow)
        
        # Phase 3: Internal Layout (Sugiyama)
        logger.debug("Phase 3: Internal Layout")
        _layout_groups_internal(workflow)
        
        # Phase 4: Global Positioning
        logger.debug("Phase 4: Global Positioning")
        _position_groups_globally(workflow)
        
        # Phase 4b: Position ungrouped nodes (after groups)
        _position_ungrouped_nodes(workflow)
        
        # Phase 5: Decorative Nodes
        logger.debug("Phase 5: Decorative Nodes")
        _layout_decorative_nodes(workflow)
        
        # Phase 6: Bounding Box Update
        logger.debug("Phase 6: Bounding Box Update")
        _update_bounding_boxes(workflow)
        
        logger.info("Layout algorithm completed successfully")
        return workflow
        
    except Exception as e:
        logger.error(f"Layout algorithm failed: {e}", exc_info=True)
        raise


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


def _layout_groups_internal(workflow: Workflow) -> None:
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
                node.x = base_x + layer * (max_node_w + NODE_H_GAP)
                node.y = base_y + col * (node_h + NODE_V_GAP)


# ---------------------------------------------------------------------------
# Phase 4: Global Positioning
# ---------------------------------------------------------------------------


def _position_groups_globally(workflow: Workflow) -> None:
    """
    Position groups relative to each other based on their topological order.
    Groups that come earlier in the flow are placed to the left.
    """
    logger.debug("Positioning groups globally")
    
    if not workflow.groups:
        return
    
    start_x = 50.0
    start_y = 50.0
    
    for i, group in enumerate(workflow.groups):
        if not group.nodes:
            continue
        
        if group.bounding and len(group.bounding) >= 4:
            g_width = group.bounding[2]
            g_height = group.bounding[3]
        else:
            min_x = min(n.x for n in group.nodes)
            max_x = max(n.x + n.size[0] for n in group.nodes)
            min_y = min(n.y for n in group.nodes)
            max_y = max(n.y + n.size[1] for n in group.nodes)
            g_width = max_x - min_x
            g_height = max_y - min_y
            group.bounding = [min_x, min_y, g_width, g_height]
        
        current_min_x = min(n.x for n in group.nodes) if group.nodes else 0
        current_min_y = min(n.y for n in group.nodes) if group.nodes else 0
        
        offset_x = start_x - current_min_x
        offset_y = start_y - current_min_y
        
        for node in group.nodes:
            node.x += offset_x
            node.y += offset_y
        
        if group.bounding:
            group.bounding[0] = start_x
            group.bounding[1] = start_y
        
        start_x += g_width + GROUP_H_GAP
        
        if (i + 1) % 3 == 0:
            start_x = 50.0
            start_y += g_height + GROUP_V_GAP


def _position_ungrouped_nodes(workflow: Workflow) -> None:
    """
    Position nodes that are not part of any group in a simple grid layout.
    Placed to the right of all groups or starting at (50,50) if no groups.
    """
    if not workflow.ungrouped_nodes:
        return
    
    logger.debug(f"Positioning {len(workflow.ungrouped_nodes)} ungrouped nodes")
    
    # Sort by original y then x to preserve some ordering
    sorted_nodes = sorted(workflow.ungrouped_nodes, key=lambda n: (n.y, n.x))
    
    # Determine start position: if groups exist, start after the rightmost group extent
    max_right = 0.0
    max_bottom = 0.0
    for group in workflow.groups:
        if group.nodes:
            group_max_x = max(n.x + n.size[0] for n in group.nodes)
            group_max_y = max(n.y + n.size[1] for n in group.nodes)
            max_right = max(max_right, group_max_x)
            max_bottom = max(max_bottom, group_max_y)
    
    start_x = max_right + GROUP_H_GAP if max_right > 0 else 50
    start_y = 50.0
    
    # Determine column count based on screen width or constant
    cols = 3
    max_node_w = max((n.size[0] for n in sorted_nodes if n.size[0] > 0), default=200)
    
    for i, node in enumerate(sorted_nodes):
        col = i // cols
        row = i % cols
        node.x = start_x + col * (max_node_w + NODE_H_GAP)
        node.y = start_y + row * (node.size[1] + NODE_V_GAP)


# ---------------------------------------------------------------------------
# Phase 5: Decorative Nodes
# ---------------------------------------------------------------------------


def _layout_decorative_nodes(workflow: Workflow) -> None:
    """
    Position decorative nodes (Note, MarkdownNote, Label) by preserving their
    original offset from the nearest layout node.
    """
    logger.debug("Positioning decorative nodes")
    
    DECORATIVE_TYPES = {"Note", "MarkdownNote", "Label"}
    
    # Find all decorative nodes
    decorative = [n for n in workflow.nodes.values() if n.type in DECORATIVE_TYPES]
    layout_nodes = [n for n in workflow.nodes.values() if n.type not in DECORATIVE_TYPES]
    
    if not decorative or not layout_nodes:
        return
    
    for dec_node in decorative:
        # Find the nearest layout node by original distance
        nearest = None
        min_dist = float('inf')
        for lay_node in layout_nodes:
            dx = dec_node.x - lay_node.x
            dy = dec_node.y - lay_node.y
            dist = (dx*dx + dy*dy) ** 0.5
            if dist < min_dist:
                min_dist = dist
                nearest = lay_node
        if nearest:
            # Apply the same offset vector to the layout node's new position
            offset_x = dec_node.x - nearest.x
            offset_y = dec_node.y - nearest.y
            dec_node.x = nearest.x + offset_x
            dec_node.y = nearest.y + offset_y
            logger.debug(f"Positioned decorative node {dec_node.id} near {nearest.id} with offset ({offset_x:.0f}, {offset_y:.0f})")


# ---------------------------------------------------------------------------
# Phase 6: Bounding Box Update
# ---------------------------------------------------------------------------


def _update_bounding_boxes(workflow: Workflow) -> None:
    """
    Update all group bounding boxes to tightly fit their nodes.
    """
    logger.debug("Updating group bounding boxes")
    
    for group in workflow.groups:
        if not group.nodes:
            continue
        
        min_x = min(n.x for n in group.nodes)
        max_x = max(n.x + n.size[0] for n in group.nodes)
        min_y = min(n.y for n in group.nodes)
        max_y = max(n.y + n.size[1] for n in group.nodes)
        
        group.bounding = [
            min_x - GROUP_PADDING,
            min_y - GROUP_PADDING,
            (max_x - min_x) + 2 * GROUP_PADDING,
            (max_y - min_y) + 2 * GROUP_PADDING
        ]
        
        logger.debug(f"Group {group.name} bounding box updated to {group.bounding}")
