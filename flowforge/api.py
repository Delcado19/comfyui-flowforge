"""
aiohttp API server for ComfyUI FlowForge.
Provides endpoints for layout and optimization.
"""

import logging
import json
from aiohttp import web
from typing import Dict, Any
from .parser import parse_comfyui_workflow
from .layout import apply as apply_layout
from .optimizer import optimize
from .logger import setup_logger

logger = setup_logger(__name__)

async def layout_handler(request):
    """
    POST /layout
    Accepts a ComfyUI workflow JSON and returns the layouted version.
    """
    try:
        data = await request.json()
        logger.info("Received layout request")
        
        # Parse workflow
        workflow = parse_comfyui_workflow(data)
        logger.debug(f"Parsed workflow with {len(workflow.nodes)} nodes")
        
        # Apply layout algorithm
        layouted = apply_layout(workflow)
        
        # Convert back to ComfyUI JSON format
        result = _workflow_to_comfyui_json(layouted)
        
        return web.json_response(result)
    except Exception as e:
        logger.error(f"Layout error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def optimize_handler(request):
    """
    POST /optimize
    Accepts a ComfyUI workflow JSON and returns the optimized version
    (with Set/Get nodes inserted for high fanout).
    """
    try:
        data = await request.json()
        logger.info("Received optimize request")
        
        # Parse workflow
        workflow = parse_comfyui_workflow(data)
        
        # Run optimizer
        optimized = optimize(workflow)
        
        # Convert back to ComfyUI JSON format
        result = _workflow_to_comfyui_json(optimized)
        
        return web.json_response(result)
    except Exception as e:
        logger.error(f"Optimize error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def health_handler(request):
    """
    GET /health - simple health check
    """
    return web.json_response({"status": "ok"})


def _workflow_to_comfyui_json(workflow) -> Dict[str, Any]:
    """
    Convert a Workflow model back to ComfyUI JSON format.
    """
    nodes = []
    for node in workflow.nodes.values():
        node_dict = {
            "id": node.id,
            "type": node.type,
            "pos": [node.x, node.y],
            "size": node.size,
            "mode": node.mode,
            "order": node.order,
            "inputs": [],  # We'll need to reconstruct inputs from links
            "outputs": []
        }
        # Reconstruct inputs and outputs from link data
        # For each input link on this node, find the link object
        for link_id in node.input_links:
            link = workflow.links.get(link_id)
            if link:
                node_dict["inputs"].append({
                    "name": f"input_{link.source_port}",  # Simplified; real ComfyUI has names
                    "type": link.type,
                    "link": link_id
                })
        for link_id in node.output_links:
            link = workflow.links.get(link_id)
            if link:
                node_dict["outputs"].append({
                    "name": f"output_{link.source_port}",
                    "type": link.type,
                    "links": [link_id]
                })
        nodes.append(node_dict)
    
    links = []
    for link in workflow.links.values():
        # ComfyUI link format: [link_id, source_node, source_port, target_node, target_port, "TYPE"]
        links.append([
            link.id,
            link.source,
            link.source_port,
            link.target,
            link.target_port,
            link.type if link.type else ""
        ])
    
    groups = []
    for group in workflow.groups:
        groups.append({
            "id": group.id,
            "name": group.name,
            "bounding": group.bounding
        })
    
    return {
        "nodes": nodes,
        "links": links,
        "groups": groups,
        "last_node_id": max(workflow.nodes.keys()) if workflow.nodes else 0,
        "last_link_id": max(workflow.links.keys()) if workflow.links else 0,
        "revision": 0
    }


def create_app() -> web.Application:
    """
    Create and configure the aiohttp application.
    """
    app = web.Application()
    app.add_routes([
        web.post('/layout', layout_handler),
        web.post('/optimize', optimize_handler),
        web.get('/health', health_handler),
    ])
    return app


if __name__ == '__main__':
    # Run the server
    app = create_app()
    port = 8000
    logger.info(f"Starting FlowForge API server on port {port}")
    web.run_app(app, port=port)