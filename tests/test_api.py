"""
API endpoint tests using aiohttp test client.
"""

from copy import deepcopy

import pytest

from flowforge.api import create_app
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
    assert resp.headers["Access-Control-Allow-Origin"] == "*"
    data = await resp.json()
    assert data['status'] == 'ok'
    logger.info("Health endpoint OK")


@pytest.mark.asyncio
async def test_cors_preflight(client):
    resp = await client.options(
        "/layout",
        headers={
            "Origin": "http://127.0.0.1:5176",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )

    assert resp.status == 204
    assert resp.headers["Access-Control-Allow-Origin"] == "*"
    assert "POST" in resp.headers["Access-Control-Allow-Methods"]
    assert "Content-Type" in resp.headers["Access-Control-Allow-Headers"]


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
async def test_layout_endpoint_accepts_custom_spacing(client):
    logger.info("Testing /layout endpoint with custom spacing")
    workflow = deepcopy(SIMPLE_WORKFLOW)
    workflow["groups"] = [
        {
            "id": 7,
            "title": "Load Model",
            "bounding": [0, 0, 1000, 1000],
        }
    ]

    compact_resp = await client.post("/layout", json=workflow)
    roomy_resp = await client.post(
        "/layout",
        json={
            "workflow": workflow,
            "layout": {"min_node_distance": 160},
        },
    )

    assert compact_resp.status == 200
    assert roomy_resp.status == 200

    compact = await compact_resp.json()
    roomy = await roomy_resp.json()

    compact_node1 = next(n for n in compact["nodes"] if n["id"] == 1)
    compact_node2 = next(n for n in compact["nodes"] if n["id"] == 2)
    roomy_node1 = next(n for n in roomy["nodes"] if n["id"] == 1)
    roomy_node2 = next(n for n in roomy["nodes"] if n["id"] == 2)

    compact_dx = compact_node2["pos"][0] - compact_node1["pos"][0]
    roomy_dx = roomy_node2["pos"][0] - roomy_node1["pos"][0]
    assert roomy_dx > compact_dx
    assert roomy["groups"][0]["bounding"][2] >= compact["groups"][0]["bounding"][2]
    assert roomy["groups"][0]["bounding"][3] >= compact["groups"][0]["bounding"][3]
    logger.info("Custom spacing request OK")


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


@pytest.mark.asyncio
async def test_layout_preserves_comfyui_metadata(client):
    logger.info("Testing that layout preserves ComfyUI workflow metadata")
    workflow = deepcopy(SIMPLE_WORKFLOW)
    workflow.update(
        {
            "version": 0.4,
            "config": {"links_ontop": True},
            "extra": {"ds": {"scale": 0.75, "offset": [12, 34]}},
            "models": [{"name": "example.safetensors"}],
            "revision": 42,
            "x_custom_top_level": {"keep": True},
            "groups": [
                {
                    "id": 7,
                    "title": "Preserved Group",
                    "bounding": [0, 0, 1000, 1000],
                    "color": "#334455",
                    "locked": True,
                }
            ],
        }
    )
    workflow["nodes"][0].update(
        {
            "widgets_values": ["image.png"],
            "properties": {"Node name for S&R": "LoadImage"},
            "flags": {"collapsed": False},
            "color": "#112233",
            "bgcolor": "#445566",
            "x_custom_node_field": "preserve-me",
        }
    )

    resp = await client.post("/layout", json=workflow)
    assert resp.status == 200
    data = await resp.json()

    assert data["config"] == workflow["config"]
    assert data["extra"] == workflow["extra"]
    assert data["models"] == workflow["models"]
    assert data["revision"] == 42
    assert data["x_custom_top_level"] == {"keep": True}

    node = next(n for n in data["nodes"] if n["id"] == 1)
    assert node["widgets_values"] == ["image.png"]
    assert node["properties"] == {"Node name for S&R": "LoadImage"}
    assert node["flags"] == {"collapsed": False}
    assert node["color"] == "#112233"
    assert node["bgcolor"] == "#445566"
    assert node["x_custom_node_field"] == "preserve-me"
    assert node["outputs"][0]["name"] == "IMAGE"

    group = data["groups"][0]
    assert group["title"] == "Preserved Group"
    assert group["color"] == "#334455"
    assert group["locked"] is True
    assert group["bounding"] != [0, 0, 1000, 1000]
    assert group["bounding"][2] >= 1000
    assert group["bounding"][3] >= 1000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
