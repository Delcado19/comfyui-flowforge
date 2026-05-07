"""
Tests for the optimizer module.
"""

import pytest
from flowforge.model import Node, Link, Workflow
from flowforge.optimizer import optimize
from flowforge.logger import setup_logger

logger = setup_logger(__name__)


def create_fanout_workflow() -> Workflow:
    """
    Create a workflow with a loader node that fans out to 3 consumers.
    """
    wf = Workflow()
    # Source node (MODEL output)
    source = Node(id=1, type="UNETLoader", x=0, y=0, size=[200, 60])
    wf.nodes[1] = source
    # Three consumer nodes
    consumers = []
    for i in range(3):
        cid = 2 + i
        consumer = Node(id=cid, type="KSampler", x=400 + i*250, y=i*150, size=[200, 100])
        consumers.append(consumer)
        wf.nodes[cid] = consumer
    # Links: source output port 0 (MODEL) -> each consumer input
    base_link_id = 100
    for i, consumer in enumerate(consumers):
        link = Link(
            id=base_link_id + i,
            source=1,
            source_port=0,
            target=consumer.id,
            target_port=0,
            type="MODEL"
        )
        wf.links[link.id] = link
        source.output_links.append(link.id)
        consumer.input_links.append(link.id)
    wf.groups = []  # no groups needed
    return wf


def create_reroute_fanout_workflow() -> Workflow:
    """
    Create a workflow where fanout happens after a Reroute node.
    """
    wf = Workflow()
    source = Node(id=1, type="UNETLoader", x=0, y=0, size=[200, 60])
    reroute = Node(id=2, type="Reroute", x=240, y=0, size=[100, 40])
    wf.nodes[1] = source
    wf.nodes[2] = reroute

    consumers = []
    for i in range(3):
        cid = 3 + i
        consumer = Node(id=cid, type="KSampler", x=500 + i * 250, y=i * 120, size=[200, 100])
        consumers.append(consumer)
        wf.nodes[cid] = consumer

    links = [
        Link(id=100, source=1, source_port=0, target=2, target_port=0, type="MODEL"),
        Link(id=101, source=2, source_port=0, target=3, target_port=0, type="MODEL"),
        Link(id=102, source=2, source_port=0, target=4, target_port=0, type="MODEL"),
        Link(id=103, source=2, source_port=0, target=5, target_port=0, type="MODEL"),
    ]
    for link in links:
        wf.links[link.id] = link
        wf.nodes[link.source].output_links.append(link.id)
        wf.nodes[link.target].input_links.append(link.id)

    wf.groups = []
    return wf


def create_short_fanout_workflow() -> Workflow:
    """
    Create a workflow where fanout exists but the rewrite is too expensive.
    """
    wf = Workflow()
    source = Node(id=1, type="UNETLoader", x=0, y=0, size=[200, 60])
    wf.nodes[1] = source
    consumers = []
    for i in range(2):
        cid = 2 + i
        consumer = Node(id=cid, type="KSampler", x=80 + i * 20, y=i * 20, size=[160, 80])
        consumers.append(consumer)
        wf.nodes[cid] = consumer

    for i, consumer in enumerate(consumers):
        link = Link(
            id=110 + i,
            source=1,
            source_port=0,
            target=consumer.id,
            target_port=0,
            type="MODEL",
        )
        wf.links[link.id] = link
        source.output_links.append(link.id)
        consumer.input_links.append(link.id)

    wf.groups = []
    return wf


def test_optimize_fanout():
    logger.info("Testing optimize() on high-fanout MODEL connection")
    wf = create_fanout_workflow()
    assert len(wf.nodes) == 4
    assert sum(len(n.output_links) for n in wf.nodes.values()) == 3
    
    optimized = optimize(wf)
    
    # Should have inserted SetNode and GetNodes
    assert len(optimized.nodes) > 4
    # Count SetNode and GetNode
    set_nodes = [n for n in optimized.nodes.values() if n.type == "SetNode"]
    get_nodes = [n for n in optimized.nodes.values() if n.type == "GetNode"]
    assert len(set_nodes) == 1, f"Expected 1 SetNode, got {len(set_nodes)}"
    assert len(get_nodes) == 3, f"Expected 3 GetNodes, got {len(get_nodes)}"
    
    # SetNode should be connected to source
    set_node = set_nodes[0]
    assert len(set_node.input_links) == 1
    assert set_node.input_links[0] in optimized.links
    assert optimized.links[set_node.input_links[0]].source == 1
    assert set_node.x > optimized.nodes[1].x

    target_centers = [
        optimized.nodes[target_id].y + optimized.nodes[target_id].size[1] / 2
        for target_id in (2, 3, 4)
    ]
    expected_center_y = sorted(target_centers)[len(target_centers) // 2]
    assert set_node.y + set_node.size[1] / 2 == pytest.approx(expected_center_y)
    
    # Each GetNode connected to its consumer
    for get_node in get_nodes:
        assert len(get_node.output_links) == 1
        consumer_link = optimized.links[get_node.output_links[0]]
        assert consumer_link.target in {2,3,4}
    
    logger.info("Optimizer fanout test passed")


def test_optimize_reroute_fanout():
    logger.info("Testing optimize() on fanout behind a Reroute")
    wf = create_reroute_fanout_workflow()

    optimized = optimize(wf)

    set_nodes = [n for n in optimized.nodes.values() if n.type == "SetNode"]
    get_nodes = [n for n in optimized.nodes.values() if n.type == "GetNode"]
    reroute_nodes = [n for n in optimized.nodes.values() if n.type == "Reroute"]

    assert len(set_nodes) == 1
    assert len(get_nodes) == 3
    assert len(reroute_nodes) == 0

    set_node = set_nodes[0]
    assert len(set_node.input_links) == 1
    assert optimized.links[set_node.input_links[0]].source == 1

    get_targets = {
        optimized.links[get_node.output_links[0]].target
        for get_node in get_nodes
    }
    assert get_targets == {3, 4, 5}
    logger.info("Reroute fanout optimization test passed")


def test_optimize_skips_low_value_fanout():
    logger.info("Testing that optimizer skips short fanout when it is not cost-effective")
    wf = create_short_fanout_workflow()

    optimized = optimize(wf)

    set_nodes = [n for n in optimized.nodes.values() if n.type == "SetNode"]
    get_nodes = [n for n in optimized.nodes.values() if n.type == "GetNode"]
    assert len(set_nodes) == 0
    assert len(get_nodes) == 0
    assert len(optimized.links) == len(wf.links)
    logger.info("Low-value fanout correctly skipped")


def test_optimize_preserves_other_types():
    logger.info("Testing that non-MODEL/CLIP/VAE types are not optimized")
    wf = Workflow()
    n1 = Node(id=1, type="LoadImage", x=0, y=0, size=[100, 60])
    n2 = Node(id=2, type="PreviewImage", x=300, y=0, size=[100, 60])
    wf.nodes[1] = n1
    wf.nodes[2] = n2
    link = Link(id=10, source=1, source_port=0, target=2, target_port=0, type="IMAGE")
    wf.links[10] = link
    n1.output_links.append(10)
    n2.input_links.append(10)
    wf.groups = []
    
    optimized = optimize(wf)
    
    # No SetNode/GetNode should be added
    set_nodes = [n for n in optimized.nodes.values() if n.type == "SetNode"]
    get_nodes = [n for n in optimized.nodes.values() if n.type == "GetNode"]
    assert len(set_nodes) == 0
    assert len(get_nodes) == 0
    # Original nodes still there
    assert 1 in optimized.nodes
    assert 2 in optimized.nodes
    logger.info("Non-optimizable type preservation test passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
