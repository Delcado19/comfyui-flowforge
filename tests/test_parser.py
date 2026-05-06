"""
Tests for the parser module.
"""

import pytest

from flowforge.parser import parse_comfyui_workflow
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


def test_parse_simple_workflow():
    logger.info("Testing parse_simple_workflow")
    workflow = parse_comfyui_workflow(SIMPLE_WORKFLOW)
    
    assert len(workflow.nodes) == 2
    assert 1 in workflow.nodes
    assert 2 in workflow.nodes
    assert len(workflow.links) == 1
    assert 10 in workflow.links
    
    node1 = workflow.nodes[1]
    assert node1.type == "LoadImage"
    assert node1.x == 100.0
    assert node1.y == 100.0
    assert node1.size == [200.0, 100.0]
    
    link = workflow.links[10]
    assert link.source == 1
    assert link.source_port == 0
    assert link.target == 2
    assert link.target_port == 0
    assert link.type == "IMAGE"
    
    logger.info("Simple workflow parsing test passed")


def test_parse_workflow_with_groups():
    logger.info("Testing parse_workflow_with_groups")
    wf_data = {
        "nodes": [
            {"id": 1, "type": "CheckpointLoader", "pos": [50, 50], "size": [200, 60], "mode": 0, "order": 0, "inputs": [], "outputs": [{"links": [100]}]},
            {"id": 2, "type": "VAEDecode", "pos": [400, 150], "size": [200, 60], "mode": 0, "order": 1, "inputs": [{"link": 100}], "outputs": []}
        ],
        "links": [[100, 1, 0, 2, 0, "VAE"]],
        "groups": [
            {"id": 1, "name": "Loader Group", "bounding": [0, 0, 300, 200]}
        ]
    }
    workflow = parse_comfyui_workflow(wf_data)
    
    # Should have 1 group (from JSON) and 1 ungrouped node (node 2 outside bounding box)
    assert len(workflow.groups) == 1
    group = workflow.groups[0]
    assert group.name == "Loader Group"
    assert len(group.nodes) == 1  # Only node 1 inside bounding
    assert workflow.ungrouped_nodes == [workflow.nodes[2]]
    
    logger.info("Workflow with groups test passed")


def test_size_dict_handling():
    logger.info("Testing size dict handling")
    # Some ComfyUI workflows use dict format for size
    wf_data = {
        "nodes": [
            {
                "id": 1,
                "type": "TestNode",
                "pos": [0, 0],
                "size": {"width": 150, "height": 80},
                "mode": 0,
                "order": 0,
                "inputs": [],
                "outputs": []
            }
        ],
        "links": [],
        "groups": []
    }
    workflow = parse_comfyui_workflow(wf_data)
    node = workflow.nodes[1]
    assert node.size == [150.0, 80.0]
    logger.info("Size dict handling test passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
