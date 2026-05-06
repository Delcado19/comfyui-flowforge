"""
Optimizer for ComfyUI FlowForge.
Inserts SetNode/GetNode pairs for high-fanout MODEL/CLIP/VAE connections.
"""

import logging
from typing import Dict, List
from .model import Node, Link, Workflow
from .logger import setup_logger

logger = setup_logger(__name__)

# Types that trigger optimization when fanout >= 2
OPTIMIZE_TYPES = {"MODEL", "CLIP", "VAE"}

def optimize(workflow: Workflow) -> Workflow:
    """
    Run optimizer pass: replace high-fanout connections with Set/Get node pairs.
    A high-fanout connection is when a single output port on a node connects to
    two or more downstream nodes via separate links.
    """
    logger.info("Starting optimizer")
    
    # Create a copy to avoid modifying during iteration
    new_workflow = Workflow()
    new_workflow.nodes = workflow.nodes.copy()
    new_workflow.links = workflow.links.copy()
    new_workflow.groups = workflow.groups.copy()
    
    # Build a map: (source_node_id, source_port) -> list of Link objects
    port_links: Dict[tuple, List[Link]] = {}
    for link in workflow.links.values():
        key = (link.source, link.source_port)
        port_links.setdefault(key, []).append(link)
    
    # Find (node, port) where the output type is optimizable and fanout >= 2
    optimization_targets = []
    for (src_id, src_port), links in port_links.items():
        if len(links) < 2:
            continue
        # Check that at least one link has an optimizable type
        if any(l.type in OPTIMIZE_TYPES for l in links):
            src_node = workflow.nodes.get(src_id)
            if src_node:
                optimization_targets.append((src_node, src_port, links))
    
    logger.info(f"Found {len(optimization_targets)} node output ports to optimize")
    
    # Apply optimizations (each for a specific source node + output port)
    for src_node, src_port, src_links in optimization_targets:
        _replace_port_with_set_get(new_workflow, src_node, src_port, src_links)
    
    logger.info("Optimization completed")
    return new_workflow


def _replace_port_with_set_get(workflow: Workflow, src_node: Node, src_port: int, src_links: List[Link]) -> None:
    """
    Replace all connections from a given output port with a SetNode and per-target GetNodes.
    """
    # Use the first link's type for the new links (they should all be same)
    link_type = src_links[0].type if src_links else None
    if not link_type:
        logger.warning(f"No link type for node {src_node.id} port {src_port}, skipping")
        return
    
    # Create a SetNode to replace the fanout
    set_node_id = src_node.id + 1000000  # Ensure unique high IDs
    # Avoid collision by incrementing if already exists
    while set_node_id in workflow.nodes:
        set_node_id += 1
    
    set_node = Node(
        id=set_node_id,
        type="SetNode",
        x=src_node.x + 200,
        y=src_node.y,
        size=[160, 40],  # approximate
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
    
    # For each original target, insert a GetNode
    for orig_link in src_links:
        target_id = orig_link.target
        target_node = workflow.nodes.get(target_id)
        if not target_node:
            logger.warning(f"Target node {target_id} not found, skipping")
            continue
        
        # Create GetNode
        get_node_id = target_id + 2000000
        while get_node_id in workflow.nodes:
            get_node_id += 1
        get_node = Node(
            id=get_node_id,
            type="GetNode",
            x=target_node.x - 200,
            y=target_node.y,
            size=[160, 40],
            mode=0,
            order=0,
            widgets_values=[f"opt_{src_node.id}_{src_port}"]
        )
        workflow.nodes[get_node_id] = get_node
        logger.debug(f"Created GetNode {get_node_id} for target {target_id}")
        
        # Remove the original link (source -> target)
        old_link_id = orig_link.id
        if old_link_id in workflow.links:
            del workflow.links[old_link_id]
        # Clean up node link references
        if old_link_id in src_node.output_links:
            src_node.output_links.remove(old_link_id)
        if old_link_id in target_node.input_links:
            target_node.input_links.remove(old_link_id)
        
        # Connect SetNode -> GetNode
        set_to_get_id = set_link_id + 500000 + get_node_id  # unique
        while set_to_get_id in workflow.links:
            set_to_get_id += 1
        set_to_get_link = Link(
            id=set_to_get_id,
            source=set_node_id,
            source_port=0,
            target=get_node_id,
            target_port=0,
            type=link_type
        )
        workflow.links[set_to_get_id] = set_to_get_link
        set_node.output_links.append(set_to_get_id)
        get_node.input_links.append(set_to_get_id)
        
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