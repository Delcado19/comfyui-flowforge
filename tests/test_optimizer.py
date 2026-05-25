"""
Tests for the optimizer module.
"""

import pytest
from flowforge.model import Node, Link, Group, Workflow
from flowforge.optimizer import optimize, _build_node_group_index, _link_cost
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
    assert len(optimized.links) == 4
    
    # SetNode should be connected to source
    set_node = set_nodes[0]
    assert len(set_node.input_links) == 1
    assert len(set_node.output_links) == 0
    assert set_node.input_links[0] in optimized.links
    assert optimized.links[set_node.input_links[0]].source == 1
    assert set_node.x > optimized.nodes[1].x

    source_center_y = optimized.nodes[1].y + optimized.nodes[1].size[1] / 2
    assert set_node.y + set_node.size[1] / 2 == pytest.approx(source_center_y)
    
    # Each GetNode connected to its consumer
    for get_node in get_nodes:
        assert len(get_node.input_links) == 0
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
    assert len(set_node.output_links) == 0
    assert optimized.links[set_node.input_links[0]].source == 1

    get_targets = {
        optimized.links[get_node.output_links[0]].target
        for get_node in get_nodes
    }
    assert get_targets == {3, 4, 5}
    assert all(len(get_node.input_links) == 0 for get_node in get_nodes)
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


def test_optimize_skips_upstream_transformer_fanout():
    logger.info("Testing optimizer skips fanout into pass-through transformer nodes")
    wf = Workflow()
    clip = Node(id=1, type="CLIPLoaderGGUF", x=0, y=0, size=[220, 80])
    lora = Node(id=2, type="Power Lora Loader (rgthree)", x=320, y=0, size=[260, 220])
    text = Node(id=3, type="TextEncodeQwenImageEditPlus", x=900, y=160, size=[260, 180])
    negative = Node(id=4, type="CLIPTextEncode", x=1500, y=220, size=[220, 120])
    wf.nodes = {1: clip, 2: lora, 3: text, 4: negative}
    wf.groups = [
        Group(id=1, name="models", nodes=[clip, lora], bounding=[-40, -40, 700, 340]),
        Group(id=2, name="core", nodes=[text, negative], bounding=[860, 120, 960, 380]),
    ]

    links = [
        Link(id=10, source=clip.id, source_port=0, target=lora.id, target_port=1, type="CLIP"),
        Link(id=11, source=clip.id, source_port=0, target=text.id, target_port=0, type="CLIP"),
        Link(id=12, source=lora.id, source_port=1, target=negative.id, target_port=0, type="CLIP"),
    ]
    for link in links:
        wf.links[link.id] = link
        wf.nodes[link.source].output_links.append(link.id)
        wf.nodes[link.target].input_links.append(link.id)

    optimized = optimize(wf)

    set_sources = {
        optimized.links[node.input_links[0]].source
        for node in optimized.nodes.values()
        if node.type == "SetNode"
    }
    assert clip.id not in set_sources
    assert lora.id in set_sources
    logger.info("Upstream transformer fanout skip test passed")


def test_optimize_long_single_cross_group_model_link():
    logger.info("Testing optimizer rewrites long single cross-group links")
    wf = Workflow()
    lora = Node(id=1, type="Power Lora Loader (rgthree)", x=0, y=0, size=[260, 220])
    sampler = Node(id=2, type="KSampler", x=1600, y=120, size=[320, 360])
    wf.nodes = {1: lora, 2: sampler}
    wf.groups = [
        Group(id=1, name="models", nodes=[lora], bounding=[-40, -40, 360, 320]),
        Group(id=2, name="core", nodes=[sampler], bounding=[1560, 80, 420, 460]),
    ]
    link = Link(id=10, source=lora.id, source_port=0, target=sampler.id, target_port=0, type="MODEL")
    wf.links[link.id] = link
    lora.output_links.append(link.id)
    sampler.input_links.append(link.id)

    optimized = optimize(wf)

    set_nodes = [node for node in optimized.nodes.values() if node.type == "SetNode"]
    get_nodes = [node for node in optimized.nodes.values() if node.type == "GetNode"]
    assert len(set_nodes) == 1
    assert len(get_nodes) == 1
    assert optimized.links[set_nodes[0].input_links[0]].source == lora.id
    assert optimized.links[get_nodes[0].output_links[0]].target == sampler.id
    logger.info("Long single cross-group link optimization test passed")


def test_optimize_skips_fanout_touching_pinned_group():
    logger.info("Testing optimizer skips fanout that touches pinned geometry")
    wf = Workflow()
    source = Node(id=1, type="UNETLoader", x=100, y=100, size=[200, 60])
    target_a = Node(id=2, type="KSampler", x=900, y=100, size=[200, 100])
    target_b = Node(id=3, type="KSampler", x=1200, y=420, size=[200, 100])
    pinned_group = Group(id=1, name="control", bounding=[40, 40, 1480, 640], pinned=True)
    wf.nodes = {1: source, 2: target_a, 3: target_b}
    wf.groups = [pinned_group]

    for index, target in enumerate((target_a, target_b), start=10):
        link = Link(
            id=index,
            source=source.id,
            source_port=0,
            target=target.id,
            target_port=0,
            type="MODEL",
        )
        wf.links[link.id] = link
        source.output_links.append(link.id)
        target.input_links.append(link.id)

    optimized = optimize(wf)

    assert [node.type for node in optimized.nodes.values()].count("SetNode") == 0
    assert [node.type for node in optimized.nodes.values()].count("GetNode") == 0
    assert len(optimized.links) == len(wf.links)
    logger.info("Pinned geometry fanout skip test passed")


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


def test_link_cost_penalizes_cross_group_links():
    logger.info("Testing group-aware link cost")
    wf = Workflow()
    source = Node(id=1, type="UNETLoader", x=0, y=0, size=[200, 60])
    target = Node(id=2, type="KSampler", x=400, y=200, size=[200, 100])
    wf.nodes[1] = source
    wf.nodes[2] = target
    wf.groups = [Group(id=1, name="group", nodes=[source, target], bounding=[0, 0, 800, 400])]

    same_group_cost = _link_cost(wf, Link(id=1, source=1, source_port=0, target=2, target_port=0, type="MODEL"), _build_node_group_index(wf))

    cross_wf = Workflow()
    cross_source = Node(id=1, type="UNETLoader", x=0, y=0, size=[200, 60])
    cross_target = Node(id=2, type="KSampler", x=400, y=200, size=[200, 100])
    cross_wf.nodes[1] = cross_source
    cross_wf.nodes[2] = cross_target
    cross_wf.groups = [
        Group(id=1, name="source", nodes=[cross_source], bounding=[0, 0, 240, 120]),
        Group(id=2, name="target", nodes=[cross_target], bounding=[360, 160, 240, 200]),
    ]

    cross_group_cost = _link_cost(
        cross_wf,
        Link(id=1, source=1, source_port=0, target=2, target_port=0, type="MODEL"),
        _build_node_group_index(cross_wf),
    )

    assert cross_group_cost > same_group_cost
    logger.info("Group-aware link cost test passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
