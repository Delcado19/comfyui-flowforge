"""
API endpoint tests using aiohttp test client.
"""

import pytest
import json
from aiohttp import web
from flowforge.api import create_app, layout_handler, optimize_handler, _workflow_to_comfyui_json
from flowforge.parser import parse_comfyui_workflow
from flowforge.layout import apply as apply_layout
from flowforge.logger import setup_logger

logger = setup_logger(__name__)

SIMPLE_WORKFLOW = {
    "nodes": [
        {
            "id": 1,
            "type": "LoadImage",
            "pos": [100, 100],
            "size": [200, 100],
            "mode": 0,
            "order": 0,
            "inputs": [],
            "outputs": [{"name": "IMAGE", "links": [10]}]
        },
        {
            "id": 2,
            "type": "PreviewImage",
            "pos": [500, 200],
            "size": [200, 100],
            "mode": 0,
            "order": 1,
            "inputs": [{"name": "images", "link": 10}],
            "outputs": []
        }
    ],
    "links": [[10, 1, 0, 2, 0, "IMAGE"]],
    "groups": []
}


@pytest.fixture
async def client(aiohttp_client):
    """Create a test client for the aiohttp app."""
    app = create_app()
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_health_endpoint(client):
    logger.info("Testing /health endpoint")
    resp = await client.get('/health')
    assert resp.status == 200
    data = await resp.json()
    assert data['status'] == 'ok'
    logger.info("Health endpoint OK")


@pytest.mark.asyncio
async def test_layout_endpoint(client):
    logger.info("Testing /layout endpoint")
    resp = await client.post('/layout', json=SIMPLE_WORKFLOW)
    assert resp.status == 200
    data = await resp.json()
    
    # Validate structure
    assert 'nodes' in data
    assert 'links' in data
    assert 'groups' in data
    assert len(data['nodes']) == 2
    # Positions should have changed (ungrouped nodes now get positioned)
    node1 = next(n for n in data['nodes'] if n['id'] == 1)
    node2 = next(n for n in data['nodes'] if n['id'] == 2)
    # At least one node should have moved from original
    assert node1['pos'] != [100, 100] or node2['pos'] != [500, 200]
    logger.info("Layout endpoint OK – positions updated")


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_optimize_endpoint(client):
    logger.info("Testing /optimize endpoint")
    # Build a fanout workflow
    wf = {
        "nodes": [
            {"id": 1, "type": "UNETLoader", "pos": [0,0], "size":[200,60], "mode":0, "order":0, "inputs":[], "outputs":[{"links":[100]}]},
            {"id": 2, "type": "KSampler", "pos": [400,0], "size":[200,100], "mode":0, "order":1, "inputs":[{"link":100}], "outputs":[]},
            {"id": 3, "type": "KSampler", "pos": [400,200], "size":[200,100], "mode":0, "order":2, "inputs":[{"link":101}], "outputs":[]},
            {"id": 4, "type": "KSampler", "pos": [400,400], "size":[200,100], "mode":0, "order":3, "inputs":[{"link":102}], "outputs":[]},
        ],
        "links": [
            [100, 1, 0, 2, 0, "MODEL"],
            [101, 1, 0, 3, 0, "MODEL"],
            [102, 1, 0, 4, 0, "MODEL"]
        ],
        "groups": []
    }
    resp = await client.post('/optimize', json=wf)
    assert resp.status == 200
    data = await resp.json()
    
    # Should have added SetNode + 3 GetNodes
    node_types_set = {n['type'] for n in data['nodes']}
    assert 'SetNode' in node_types_set
    # Count GetNode instances in the nodes list
    get_count = sum(1 for n in data['nodes'] if n['type'] == 'GetNode')
    assert get_count >= 3
    logger.info("Optimize endpoint OK – Set/Get nodes inserted")


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_layout_preserves_links(client):
    logger.info("Testing that layout preserves all links")
    resp = await client.post('/layout', json=SIMPLE_WORKFLOW)
    assert resp.status == 200
    data = await resp.json()
    
    # Link structure unchanged
    assert len(data['links']) == 1
    link = data['links'][0]
    assert link[0] == 10  # link id
    assert link[1] == 1   # source node
    assert link[2] == 0   # source port
    assert link[3] == 2   # target node
    assert link[4] == 0   # target port
    assert link[5] == "IMAGE"
    logger.info("Link preservation test passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])