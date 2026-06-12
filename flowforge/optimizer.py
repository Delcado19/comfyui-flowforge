"""
Optimizer for ComfyUI FlowForge.
Inserts SetNode/GetNode pairs for high-fanout MODEL/CLIP/VAE connections.
"""

from collections import deque
from copy import deepcopy
import math
from typing import Dict, List

from .model import Node, Link, Group, Workflow
from .logger import setup_logger

logger = setup_logger(__name__)

# Types that trigger optimization when fanout >= 2
OPTIMIZE_TYPES = {"MODEL", "CLIP", "VAE"}
NODE_INSERTION_COST = 40.0
LINK_INSERTION_COST = 4.0
HUB_NODE_WIDTH = 160.0
HUB_NODE_HEIGHT = 40.0
HUB_NODE_GAP = 80.0
FANOUT_SPAN_COST_FACTOR = 0.2
GROUP_CROSS_LINK_FACTOR = 1.15
LONG_SINGLE_LINK_MIN_DISTANCE = 900.0


def optimize(workflow: Workflow) -> Workflow:
    """
    Run optimizer pass: replace high-fanout connections with Set/Get node pairs.
    A high-fanout connection is when a single output port on a node connects to
    two or more downstream nodes via separate links.
    """
    logger.info("Starting optimizer")
    
    new_workflow = deepcopy(workflow)
    node_group_index = _build_node_group_index(new_workflow)
    
    # Build a map: (source_node_id, source_port) -> list of Link objects
    port_links: Dict[tuple, List[Link]] = {}
    for link in new_workflow.links.values():
        key = (link.source, link.source_port)
        port_links.setdefault(key, []).append(link)
    
    # Find (node, port) where the output type is optimizable and fanout >= 2.
    optimization_targets = []
    for (src_id, src_port), links in port_links.items():
        src_node = new_workflow.nodes.get(src_id)
        if not src_node:
            continue

        terminal_links, traversed_link_ids = _collect_effective_fanout(
            new_workflow,
            src_node,
            src_port,
            links,
        )
        if not _is_rewrite_candidate(new_workflow, terminal_links, node_group_index):
            continue
        if _fanout_touches_pinned_geometry(new_workflow, src_node, terminal_links):
            continue

        # Check that at least one traversed link has an optimizable type.
        traversed_links = [new_workflow.links[link_id] for link_id in traversed_link_ids if link_id in new_workflow.links]
        if any(link.type in OPTIMIZE_TYPES for link in traversed_links):
            current_cost = _current_graph_cost(new_workflow, traversed_link_ids, terminal_links, node_group_index)
            optimized_cost = _estimated_rewrite_cost(new_workflow, src_node, terminal_links)
            if optimized_cost < current_cost:
                optimization_targets.append((src_node.id, src_port))
    
    logger.info(f"Found {len(optimization_targets)} node output ports to optimize")
    
    # Apply the best savings candidate first, then recompute the remaining
    # candidates against the modified graph. This keeps the pass adaptive when
    # one rewrite changes the relative value of the others.
    remaining_targets = optimization_targets
    while remaining_targets:
        best_candidate: tuple[float, int, float, Node, int, List[Link], set[int]] | None = None

        for src_id, src_port in remaining_targets:
            src_node = new_workflow.nodes.get(src_id)
            if not src_node:
                continue

            current_links = [
                link
                for link in new_workflow.links.values()
                if link.source == src_node.id and link.source_port == src_port
            ]
            current_terminals, current_traversed = _collect_effective_fanout(
                new_workflow,
                src_node,
                src_port,
                current_links,
            )
            if not _is_rewrite_candidate(new_workflow, current_terminals, node_group_index):
                continue
            if _fanout_touches_pinned_geometry(new_workflow, src_node, current_terminals):
                continue
            if not any(
                new_workflow.links[link_id].type in OPTIMIZE_TYPES
                for link_id in current_traversed
                if link_id in new_workflow.links
            ):
                continue

            current_cost = _current_graph_cost(new_workflow, current_traversed, current_terminals, node_group_index)
            optimized_cost = _estimated_rewrite_cost(new_workflow, src_node, current_terminals)
            savings = current_cost - optimized_cost
            group_span = _terminal_group_span(node_group_index, current_terminals)
            spread = _fanout_vertical_span(new_workflow, current_terminals)
            logger.debug(
                "Cost check for %s:%s - current=%.2f optimized=%.2f savings=%.2f groups=%s span=%.2f",
                src_node.id,
                src_port,
                current_cost,
                optimized_cost,
                savings,
                group_span,
                spread,
            )
            if savings <= 0:
                continue

            if best_candidate is None or savings > best_candidate[0] or (
                savings == best_candidate[0]
                and (
                    group_span > best_candidate[1]
                    or (
                        group_span == best_candidate[1]
                        and (
                            spread > best_candidate[2]
                            or (
                                spread == best_candidate[2]
                                and (src_node.id, src_port) < (best_candidate[3].id, best_candidate[4])
                            )
                        )
                    )
                )
            ):
                best_candidate = (savings, group_span, spread, src_node, src_port, current_terminals, current_traversed)

        if best_candidate is None:
            break

        _, _, _, src_node, src_port, current_terminals, current_traversed = best_candidate
        _replace_port_with_set_get(new_workflow, src_node, src_port, current_terminals, current_traversed)
        remaining_targets = [
            (candidate_src_id, candidate_src_port)
            for candidate_src_id, candidate_src_port in remaining_targets
            if (candidate_src_id, candidate_src_port) != (src_node.id, src_port)
        ]
    
    logger.info("Optimization completed")
    return new_workflow


def _replace_port_with_set_get(
    workflow: Workflow,
    src_node: Node,
    src_port: int,
    terminal_links: List[Link],
    traversed_link_ids: set[int],
) -> None:
    """
    Replace all connections from a given output port with a SetNode and per-target GetNodes.
    """
    # Use the first terminal link's type for the new links (they should all be same).
    link_type = terminal_links[0].type if terminal_links else None
    if not link_type:
        logger.warning(f"No link type for node {src_node.id} port {src_port}, skipping")
        return

    set_x, set_y = _set_node_position(workflow, src_node, terminal_links)
    
    # Create a SetNode to replace the fanout
    set_node_id = src_node.id + 1000000  # Ensure unique high IDs
    # Avoid collision by incrementing if already exists
    while set_node_id in workflow.nodes:
        set_node_id += 1
    
    set_node = Node(
        id=set_node_id,
        type="SetNode",
        x=set_x,
        y=set_y,
        size=[HUB_NODE_WIDTH, HUB_NODE_HEIGHT],
        mode=0,
        order=0,
        widgets_values=[f"opt_{src_node.id}_{src_port}"]
    )
    workflow.nodes[set_node_id] = set_node
    logger.debug(f"Created SetNode {set_node_id} for source node {src_node.id} port {src_port}")
    
    # Connect source node to SetNode (single link, replaces fanout)
    set_link_id = max(workflow.links.keys()) + 1 if workflow.links else 1000000
    while set_link_id in workflow.links:
        set_link_id += 1
    set_link = Link(
        id=set_link_id,
        source=src_node.id,
        source_port=src_port,
        target=set_node_id,
        target_port=0,
        type=link_type
    )
    workflow.links[set_link_id] = set_link
    src_node.output_links.append(set_link_id)
    set_node.input_links.append(set_link_id)
    
    # For each original terminal target, insert a GetNode. KJNodes Set/Get
    # pairs communicate by matching widget value, so no physical Set->Get link
    # is needed; omitting it is the point of the wire cleanup.
    for orig_link in terminal_links:
        target_id = orig_link.target
        target_node = workflow.nodes.get(target_id)
        if not target_node:
            logger.warning(f"Target node {target_id} not found, skipping")
            continue
        
        # Create GetNode
        get_node_id = target_id + 2000000
        while get_node_id in workflow.nodes:
            get_node_id += 1
        get_x, get_y = _get_node_position(target_node)
        get_node = Node(
            id=get_node_id,
            type="GetNode",
            x=get_x,
            y=get_y,
            size=[HUB_NODE_WIDTH, HUB_NODE_HEIGHT],
            mode=0,
            order=0,
            widgets_values=[f"opt_{src_node.id}_{src_port}"]
        )
        workflow.nodes[get_node_id] = get_node
        logger.debug(f"Created GetNode {get_node_id} for target {target_id}")
        
        # Remove the original link (source -> target)
        old_link_id = orig_link.id
        orig_source_node = workflow.nodes.get(orig_link.source)
        if old_link_id in workflow.links:
            del workflow.links[old_link_id]
        # Clean up node link references
        if orig_source_node and old_link_id in orig_source_node.output_links:
            orig_source_node.output_links.remove(old_link_id)
        if old_link_id in target_node.input_links:
            target_node.input_links.remove(old_link_id)
        
        # Connect GetNode -> original target (renamed link)
        new_link_id = old_link_id + 3000000 if old_link_id else max(workflow.links.keys()) + 1
        while new_link_id in workflow.links:
            new_link_id += 1
        new_link = Link(
            id=new_link_id,
            source=get_node_id,
            source_port=0,
            target=target_id,
            target_port=orig_link.target_port,
            type=link_type
        )
        workflow.links[new_link_id] = new_link
        get_node.output_links.append(new_link_id)
        target_node.input_links.append(new_link_id)
        
        logger.debug(f"Rewired: {src_node.id}:{src_port} -> SetNode -> GetNode -> {target_id}")

    _remove_traversed_links_and_cleanup_reroutes(workflow, traversed_link_ids)


def _is_rewrite_candidate(
    workflow: Workflow,
    terminal_links: List[Link],
    node_group_index: Dict[int, int],
) -> bool:
    """Return True when Set/Get is useful without hiding local transformers."""
    optimizable_links = [link for link in terminal_links if link.type in OPTIMIZE_TYPES]
    if not optimizable_links:
        return False

    # LoRA and similar pass-through transformer nodes should stay physically
    # visible in the producer chain. Rewriting before them creates local Get
    # nodes in source panels instead of placing Set nodes on the transformed
    # output, which is visually misleading in ComfyUI workflows.
    if any(_is_transformer_target(workflow, link) for link in optimizable_links):
        return False

    if len(optimizable_links) >= 2:
        return True

    link = optimizable_links[0]
    source_group = node_group_index.get(link.source)
    target_group = node_group_index.get(link.target)
    if source_group is None or target_group is None or source_group == target_group:
        return False
    return _link_cost(workflow, link, node_group_index) >= LONG_SINGLE_LINK_MIN_DISTANCE


def _is_transformer_target(workflow: Workflow, link: Link) -> bool:
    target = workflow.nodes.get(link.target)
    if target is None:
        return False
    link_type = str(link.type or "").upper()
    node_type = target.type.lower()
    if "lora" in node_type:
        return True
    return any(
        output_link is not None and str(output_link.type or "").upper() == link_type
        for output_link in (workflow.links.get(link_id) for link_id in target.output_links)
    )


def _collect_effective_fanout(
    workflow: Workflow,
    src_node: Node,
    src_port: int,
    direct_links: List[Link],
) -> tuple[List[Link], set[int]]:
    """
    Follow reroute chains from a source output port and return the terminal links.

    The returned terminal links are the links that end at real consumer nodes.
    Traversed links include both the terminal links and any reroute chain links
    that were walked to reach them.
    """
    terminal_links: List[Link] = []
    traversed_link_ids: set[int] = set()
    visited_nodes: set[int] = set()
    queue = deque(link for link in direct_links if link.source == src_node.id and link.source_port == src_port)

    while queue:
        link = queue.popleft()
        if link.id in traversed_link_ids:
            continue

        traversed_link_ids.add(link.id)
        target_node = workflow.nodes.get(link.target)
        if target_node is None:
            terminal_links.append(link)
            continue

        if _is_reroute_node(target_node):
            if target_node.id in visited_nodes:
                continue
            visited_nodes.add(target_node.id)
            for next_link_id in target_node.output_links:
                next_link = workflow.links.get(next_link_id)
                if next_link and next_link.source == target_node.id:
                    queue.append(next_link)
            continue

        terminal_links.append(link)

    return terminal_links, traversed_link_ids


def _remove_traversed_links_and_cleanup_reroutes(
    workflow: Workflow,
    traversed_link_ids: set[int],
) -> None:
    """
    Remove every traversed link and delete now-orphaned reroute nodes.
    """
    for link_id in traversed_link_ids:
        link = workflow.links.pop(link_id, None)
        if not link:
            continue

        source_node = workflow.nodes.get(link.source)
        target_node = workflow.nodes.get(link.target)

        if source_node and link_id in source_node.output_links:
            source_node.output_links.remove(link_id)
        if target_node and link_id in target_node.input_links:
            target_node.input_links.remove(link_id)

    dead_reroutes: List[int] = []
    for node_id, node in workflow.nodes.items():
        if node is None or not _is_reroute_node(node):
            continue
        if node.input_links or node.output_links:
            continue
        dead_reroutes.append(node_id)

    for node_id in dead_reroutes:
        del workflow.nodes[node_id]


def _is_reroute_node(node: Node | None) -> bool:
    return bool(node and isinstance(node.type, str) and "reroute" in node.type.lower())


def _current_graph_cost(
    workflow: Workflow,
    traversed_link_ids: set[int],
    terminal_links: List[Link],
    node_group_index: Dict[int, int],
) -> float:
    cost = 0.0
    for link_id in traversed_link_ids:
        link = workflow.links.get(link_id)
        if not link:
            continue
        cost += _link_cost(workflow, link, node_group_index) + LINK_INSERTION_COST
    cost += _fanout_vertical_span(workflow, terminal_links) * FANOUT_SPAN_COST_FACTOR
    return cost


def _estimated_rewrite_cost(workflow: Workflow, src_node: Node, terminal_links: List[Link]) -> float:
    set_node = _synthetic_set_node(workflow, src_node, terminal_links)
    cost = NODE_INSERTION_COST
    cost += _point_distance(_node_center(src_node), _node_center(set_node)) + LINK_INSERTION_COST

    for terminal_link in terminal_links:
        target_node = workflow.nodes.get(terminal_link.target)
        if target_node is None:
            continue

        get_node = _synthetic_get_node(target_node)
        cost += NODE_INSERTION_COST
        cost += _point_distance(_node_center(get_node), _node_center(target_node)) + LINK_INSERTION_COST

    return cost


def _synthetic_set_node(workflow: Workflow, src_node: Node, terminal_links: List[Link]) -> Node:
    set_x, set_y = _set_node_position(workflow, src_node, terminal_links)
    return Node(
        id=-1,
        type="SetNode",
        x=set_x,
        y=set_y,
        size=[HUB_NODE_WIDTH, HUB_NODE_HEIGHT],
    )


def _synthetic_get_node(target_node: Node) -> Node:
    get_x, get_y = _get_node_position(target_node)
    return Node(
        id=-1,
        type="GetNode",
        x=get_x,
        y=get_y,
        size=[HUB_NODE_WIDTH, HUB_NODE_HEIGHT],
    )


def _node_center(node: Node) -> tuple[float, float]:
    width = node.size[0] if node.size and len(node.size) >= 1 and node.size[0] > 0 else 200.0
    height = node.size[1] if node.size and len(node.size) >= 2 and node.size[1] > 0 else 60.0
    return node.x + width / 2, node.y + height / 2


def _point_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _link_cost(workflow: Workflow, link: Link, node_group_index: Dict[int, int] | None = None) -> float:
    source_node = workflow.nodes.get(link.source)
    target_node = workflow.nodes.get(link.target)
    if source_node is None or target_node is None:
        return 0.0
    distance = _point_distance(_node_center(source_node), _node_center(target_node))
    if node_group_index is None:
        return distance

    source_group = node_group_index.get(source_node.id)
    target_group = node_group_index.get(target_node.id)
    if source_group is None or target_group is None:
        return distance * GROUP_CROSS_LINK_FACTOR
    if source_group != target_group:
        return distance * GROUP_CROSS_LINK_FACTOR
    return distance


def _set_node_position(workflow: Workflow, src_node: Node, terminal_links: List[Link]) -> tuple[float, float]:
    del workflow, terminal_links
    return src_node.x + _node_width(src_node) + HUB_NODE_GAP, _node_center(src_node)[1] - HUB_NODE_HEIGHT / 2


def _get_node_position(target_node: Node) -> tuple[float, float]:
    return (
        target_node.x - HUB_NODE_WIDTH - HUB_NODE_GAP,
        _node_center(target_node)[1] - HUB_NODE_HEIGHT / 2,
    )


def _fanout_vertical_span(workflow: Workflow, terminal_links: List[Link]) -> float:
    target_centers: List[float] = []
    for terminal_link in terminal_links:
        target_node = workflow.nodes.get(terminal_link.target)
        if target_node is not None:
            target_centers.append(_node_center(target_node)[1])

    if len(target_centers) < 2:
        return 0.0

    return max(target_centers) - min(target_centers)


def _build_node_group_index(workflow: Workflow) -> Dict[int, int]:
    index: Dict[int, int] = {}
    for group_index, group in enumerate(workflow.groups):
        for node in group.nodes:
            index[node.id] = group_index
    return index


def _terminal_group_span(node_group_index: Dict[int, int], terminal_links: List[Link]) -> int:
    groups: set[int] = set()
    for terminal_link in terminal_links:
        group_index = node_group_index.get(terminal_link.target)
        if group_index is not None:
            groups.add(group_index)
    return len(groups)


def _fanout_touches_pinned_geometry(workflow: Workflow, src_node: Node, terminal_links: List[Link]) -> bool:
    if _is_pinned_node(workflow, src_node):
        return True

    for terminal_link in terminal_links:
        target_node = workflow.nodes.get(terminal_link.target)
        if target_node is not None and _is_pinned_node(workflow, target_node):
            return True
    return False


def _is_pinned_node(workflow: Workflow, node: Node) -> bool:
    if node.pinned:
        return True
    return any(
        group.pinned and _node_belongs_to_group(node, group)
        for group in workflow.groups
    )


def _node_belongs_to_group(node: Node, group: Group) -> bool:
    if group.nodes:
        return any(member.id == node.id for member in group.nodes)
    return _node_in_group(node, group)


def _node_in_group(node: Node, group: Group) -> bool:
    if not group.bounding or len(group.bounding) < 4:
        return False
    gx, gy, gw, gh = group.bounding
    return gx <= node.x <= gx + gw and gy <= node.y <= gy + gh


def _node_width(node: Node) -> float:
    if node.size and len(node.size) >= 1 and node.size[0] > 0:
        return float(node.size[0])
    return 200.0
