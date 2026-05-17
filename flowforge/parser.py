"""
Parser for ComfyUI workflow JSON files.
Converts the JSON into the FlowForge Workflow model.
"""

from copy import deepcopy

from .model import Node, Link, Group, Workflow
from .logger import setup_logger

logger = setup_logger(__name__)

def parse_comfyui_workflow(json_data: dict) -> Workflow:
    """
    Parse a ComfyUI workflow JSON into a Workflow model.
    
    Args:
        json_data: The parsed JSON dictionary from a ComfyUI workflow file.
        
    Returns:
        A Workflow model with nodes, links, and groups.
    """
    logger.info("Parsing ComfyUI workflow JSON")
    
    workflow = Workflow(source_json=deepcopy(json_data))
    
    # Parse nodes
    nodes_data = json_data.get('nodes', [])
    logger.debug(f"Found {len(nodes_data)} nodes")
    
    for node_data in nodes_data:
        node_id = node_data.get('id')
        if node_id is None:
            logger.warning("Node missing id, skipping")
            continue
            
        node_type = node_data.get('type', '')
        # Position: ComfyUI uses [x, y]
        pos = node_data.get('pos', [0.0, 0.0])
        size = node_data.get('size', [0.0, 0.0])
        # Size might be a dict {width, height} or list [w, h]
        if isinstance(size, dict):
            size = [size.get('width', 0.0), size.get('height', 0.0)]
        mode = node_data.get('mode', 0)
        order = node_data.get('order', 0)
        inputs = node_data.get('inputs', [])
        outputs = node_data.get('outputs', [])
        flags = node_data.get('flags', {})
        widgets_values = deepcopy(node_data.get('widgets_values', []))
        
        node = Node(
            id=node_id,
            type=node_type,
            x=float(pos[0]),
            y=float(pos[1]),
            size=[float(size[0]), float(size[1])],
            mode=mode,
            order=order,
            input_count=len(inputs) if isinstance(inputs, list) else 0,
            output_count=len(outputs) if isinstance(outputs, list) else 0,
            collapsed=bool(flags.get('collapsed')) if isinstance(flags, dict) else False,
            widgets_values=widgets_values,
        )
        workflow.nodes[node_id] = node
        logger.debug(f"Parsed node {node_id}: {node_type} at ({node.x}, {node.y})")
    
    # Parse links
    links_data = json_data.get('links', [])
    logger.debug(f"Found {len(links_data)} links")
    
    for link_data in links_data:
        # Format: [link_id, source_id, source_port, target_id, target_port, type]
        if len(link_data) < 6:
            logger.warning(f"Link data too short: {link_data}, skipping")
            continue
        link_id = link_data[0]
        source_id = link_data[1]
        source_port = link_data[2]
        target_id = link_data[3]
        target_port = link_data[4]
        link_type = link_data[5] if len(link_data) > 5 else None
        
        link = Link(
            id=link_id,
            source=source_id,
            source_port=source_port,
            target=target_id,
            target_port=target_port,
            type=link_type
        )
        workflow.links[link_id] = link
        # Update node's link lists
        if source_id in workflow.nodes:
            workflow.nodes[source_id].output_links.append(link_id)
        if target_id in workflow.nodes:
            workflow.nodes[target_id].input_links.append(link_id)
        logger.debug(f"Parsed link {link_id}: {source_id}:{source_port} -> {target_id}:{target_port}")
    
    # Parse groups
    groups_data = json_data.get('groups', [])
    logger.debug(f"Found {len(groups_data)} groups")
    
    for group_data in groups_data:
        group_id = group_data.get('id')
        if group_id is None:
            logger.warning("Group missing id, skipping")
            continue
        name = group_data.get('name', f'group_{group_id}')
        # Bounding box: [x, y, width, height]
        bounding = group_data.get('bounding', [0.0, 0.0, 0.0, 0.0])
        if isinstance(bounding, dict):
            # Sometimes it might be a dict? We'll assume list for now.
            # Convert dict to list if needed, but ComfyUI uses list.
            pass
        # Ensure we have 4 values
        if len(bounding) != 4:
            logger.warning(f"Group {group_id} bounding box has {len(bounding)} values, expected 4. Using [0,0,0,0]")
            bounding = [0.0, 0.0, 0.0, 0.0]
        
        group = Group(
            id=group_id,
            name=name,
            bounding=[float(bounding[0]), float(bounding[1]), float(bounding[2]), float(bounding[3])]
        )
        workflow.groups.append(group)
        logger.debug(f"Parsed group {group_id}: {name} at bounding {group.bounding}")
    
    # After parsing, assign nodes to groups based on bounding box.
    logger.debug("Assigning nodes to groups based on bounding box")
    for node in workflow.nodes.values():
        assigned = False
        for group in workflow.groups:
            if _node_in_group_bounding(node, group):
                group.nodes.append(node)
                assigned = True
                break
        if not assigned:
            # Node is not in any group; add to ungrouped set (implicit)
            workflow.ungrouped_nodes.append(node)
    
    logger.info(f"Parsed workflow with {len(workflow.nodes)} nodes, {len(workflow.links)} links, {len(workflow.groups)} groups, {len(workflow.ungrouped_nodes)} ungrouped nodes")
    return workflow


def _node_in_group_bounding(node: Node, group: Group) -> bool:
    """
    Check if a node's position is inside a group's bounding box.
    The bounding box is [x, y, width, height] where (x, y) is the top-left corner.
    """
    # Node position is (node.x, node.y)
    # Group bounding box: [x, y, width, height]
    gx, gy, gw, gh = group.bounding
    return (gx <= node.x <= gx + gw) and (gy <= node.y <= gy + gh)
