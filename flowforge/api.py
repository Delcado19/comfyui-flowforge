"""
aiohttp API server for ComfyUI FlowForge.
Provides endpoints for layout and optimization.
"""

import json
from copy import deepcopy
from typing import Any, Dict, List

from aiohttp import web

from .logger import setup_logger
from .parser import parse_comfyui_workflow
from .layout import LayoutReport, LayoutSettings, apply as apply_layout
from .model import Link, Node, Workflow
from .optimizer import optimize

logger = setup_logger(__name__)


@web.middleware
async def cors_middleware(request, handler):
    """Allow the local browser frontend to call the API from its own port."""
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)

    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Expose-Headers"] = "X-FlowForge-Layout-Stats"
    return response


async def layout_handler(request):
    """
    POST /layout
    Accepts a ComfyUI workflow JSON and returns the layouted version.
    """
    try:
        data = await request.json()
        logger.info("Received layout request")

        workflow_data, layout_settings = _extract_layout_request(data)

        # Parse workflow
        workflow = parse_comfyui_workflow(workflow_data)
        logger.debug(f"Parsed workflow with {len(workflow.nodes)} nodes")
        
        # Apply layout algorithm
        layouted = apply_layout(workflow, layout_settings)

        # Convert back to ComfyUI JSON format
        result = _workflow_to_comfyui_json(layouted)
        response = web.json_response(result)
        layout_report = getattr(layouted, "layout_report", None)
        if isinstance(layout_report, LayoutReport):
            response.headers["X-FlowForge-Layout-Stats"] = json.dumps({
                "candidate_count": layout_report.candidate_count,
                "selected_candidate": layout_report.selected_candidate,
                "score": {
                    "total": layout_report.score.total,
                    "width": layout_report.score.width,
                    "height": layout_report.score.height,
                    "link_cost": layout_report.score.link_cost,
                    "aspect_cost": layout_report.score.aspect_cost,
                    "gap_cost": layout_report.score.gap_cost,
                },
            })

        return response
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


def _workflow_to_comfyui_json(workflow: Workflow) -> Dict[str, Any]:
    """
    Convert a Workflow model back to ComfyUI JSON format.

    When the workflow came from a ComfyUI export, keep that export as the
    authoritative structure and patch only the fields FlowForge changes. This
    preserves custom node metadata, widget values, colors, flags, properties,
    extra data, and unknown future ComfyUI fields.
    """
    if workflow.source_json is None:
        return _workflow_to_minimal_json(workflow)

    result = deepcopy(workflow.source_json)
    result["nodes"] = _merge_nodes(result.get("nodes", []), workflow)
    result["links"] = _merge_links(result.get("links", []), workflow)
    result["groups"] = _merge_groups(result.get("groups", []), workflow)

    if workflow.nodes:
        result["last_node_id"] = max(int(result.get("last_node_id", 0)), max(workflow.nodes))
    if workflow.links:
        result["last_link_id"] = max(int(result.get("last_link_id", 0)), max(workflow.links))

    return result


def _workflow_to_minimal_json(workflow: Workflow) -> Dict[str, Any]:
    nodes = []
    for node in workflow.nodes.values():
        nodes.append(_new_node_json(node, workflow))
    
    return {
        "nodes": nodes,
        "links": [_link_to_json(link) for link in workflow.links.values()],
        "groups": [
            {
                "id": group.id,
                "name": group.name,
                "bounding": group.bounding,
            }
            for group in workflow.groups
        ],
        "last_node_id": max(workflow.nodes.keys()) if workflow.nodes else 0,
        "last_link_id": max(workflow.links.keys()) if workflow.links else 0,
        "revision": 0,
    }


def _extract_layout_request(data: Any) -> tuple[Any, LayoutSettings]:
    if isinstance(data, dict) and "workflow" in data:
        workflow_data = data["workflow"]
        return workflow_data, LayoutSettings.from_payload(data.get("layout"))

    return data, LayoutSettings.from_payload(None)


def _merge_nodes(source_nodes: List[Dict[str, Any]], workflow: Workflow) -> List[Dict[str, Any]]:
    nodes_by_id = {
        node_data.get("id"): deepcopy(node_data)
        for node_data in source_nodes
        if isinstance(node_data, dict)
    }
    merged_nodes = []

    for node in workflow.nodes.values():
        node_data = nodes_by_id.pop(node.id, None)
        if node_data is None:
            node_data = _new_node_json(node, workflow)
        else:
            node_data["pos"] = [node.x, node.y]
            if node.widgets_values or "widgets_values" in node_data:
                node_data["widgets_values"] = deepcopy(node.widgets_values)
            _sync_node_link_refs(node_data, node, workflow)
        merged_nodes.append(node_data)

    return merged_nodes


def _merge_links(source_links: List[List[Any]], workflow: Workflow) -> List[List[Any]]:
    original_order = [
        link_data[0]
        for link_data in source_links
        if isinstance(link_data, list) and link_data and link_data[0] in workflow.links
    ]
    remaining = sorted(link_id for link_id in workflow.links if link_id not in original_order)
    return [_link_to_json(workflow.links[link_id]) for link_id in original_order + remaining]


def _merge_groups(source_groups: List[Dict[str, Any]], workflow: Workflow) -> List[Dict[str, Any]]:
    groups_by_id = {
        group_data.get("id"): deepcopy(group_data)
        for group_data in source_groups
        if isinstance(group_data, dict)
    }
    merged_groups = []

    for group in workflow.groups:
        group_data = groups_by_id.get(group.id, {"id": group.id, "name": group.name})
        group_data["bounding"] = group.bounding
        merged_groups.append(group_data)

    return merged_groups


def _sync_node_link_refs(node_data: Dict[str, Any], node: Node, workflow: Workflow) -> None:
    links_by_input = {
        link.target_port: link.id
        for link in workflow.links.values()
        if link.target == node.id
    }
    links_by_output: Dict[int, List[int]] = {}
    for link in workflow.links.values():
        if link.source == node.id:
            links_by_output.setdefault(link.source_port, []).append(link.id)

    inputs = node_data.get("inputs")
    if isinstance(inputs, list):
        for port_index, input_data in enumerate(inputs):
            if isinstance(input_data, dict) and port_index in links_by_input:
                input_data["link"] = links_by_input[port_index]

    outputs = node_data.get("outputs")
    if isinstance(outputs, list):
        for port_index, output_data in enumerate(outputs):
            if isinstance(output_data, dict):
                output_data["links"] = sorted(links_by_output.get(port_index, []))


def _new_node_json(node: Node, workflow: Workflow) -> Dict[str, Any]:
    node_data: Dict[str, Any] = {
        "id": node.id,
        "type": node.type,
        "pos": [node.x, node.y],
        "size": node.size,
        "mode": node.mode,
        "order": node.order,
        "inputs": _new_inputs_json(node, workflow),
        "outputs": _new_outputs_json(node, workflow),
    }
    if node.widgets_values:
        node_data["widgets_values"] = deepcopy(node.widgets_values)
    return node_data


def _new_inputs_json(node: Node, workflow: Workflow) -> List[Dict[str, Any]]:
    inputs = []
    for link_id in node.input_links:
        link = workflow.links.get(link_id)
        if link:
            inputs.append(
                {
                    "name": f"input_{link.target_port}",
                    "type": link.type,
                    "link": link_id,
                }
            )
    return inputs


def _new_outputs_json(node: Node, workflow: Workflow) -> List[Dict[str, Any]]:
    output_links: Dict[int, List[Link]] = {}
    for link_id in node.output_links:
        link = workflow.links.get(link_id)
        if link:
            output_links.setdefault(link.source_port, []).append(link)

    return [
        {
            "name": f"output_{port_index}",
            "type": links[0].type,
            "links": [link.id for link in links],
        }
        for port_index, links in sorted(output_links.items())
    ]


def _link_to_json(link: Link) -> List[Any]:
    return [
        link.id,
        link.source,
        link.source_port,
        link.target,
        link.target_port,
        link.type if link.type else "",
    ]


def create_app() -> web.Application:
    """
    Create and configure the aiohttp application.
    """
    app = web.Application(middlewares=[cors_middleware])
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
