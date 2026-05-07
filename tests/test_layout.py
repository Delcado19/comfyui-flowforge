"""
Tests for the layout algorithm.
"""

from flowforge.model import Node, Link, Group, Workflow
import pytest
from flowforge.layout import (
    apply,
    _assign_groups,
    _layout_groups_internal,
    _position_groups_globally,
    _update_bounding_boxes,
)
from flowforge.logger import setup_logger

logger = setup_logger(__name__)


def create_simple_workflow() -> Workflow:
    """Create a simple workflow with two nodes and one link."""
    wf = Workflow()
    n1 = Node(id=1, type="LoadImage", x=100, y=100, size=[200, 100], mode=0, order=0)
    n2 = Node(id=2, type="PreviewImage", x=500, y=200, size=[200, 100], mode=0, order=1)
    wf.nodes[1] = n1
    wf.nodes[2] = n2
    link = Link(id=10, source=1, source_port=0, target=2, target_port=0, type="IMAGE")
    wf.links[10] = link
    n1.output_links.append(10)
    n2.input_links.append(10)
    # Add default ungrouped group
    wf.groups = [Group(id=0, name="ungrouped", bounding=[0,0,0,0])]
    return wf


def test_apply_basic_layout():
    logger.info("Testing apply() basic layout")
    wf = create_simple_workflow()
    result = apply(wf)
    
    # Nodes should have new positions (ungrouped nodes get positioned)
    assert result.nodes[1].x != 100 or result.nodes[1].y != 100, "Node 1 did not move"
    assert result.nodes[2].x != 500 or result.nodes[2].y != 200, "Node 2 did not move"
    
    # Links preserved
    assert len(result.links) == 1
    assert 10 in result.links
    
    # No groups defined; ungrouped nodes should be positioned
    assert len(result.ungrouped_nodes) == 2
    logger.info("Basic layout test passed")


def test_group_assignment():
    logger.info("Testing group assignment")
    wf = Workflow()
    n1 = Node(id=1, type="TestNode1", x=100, y=100, size=[100, 100])
    n2 = Node(id=2, type="TestNode2", x=400, y=200, size=[100, 100])
    wf.nodes[1] = n1
    wf.nodes[2] = n2
    group = Group(id=1, name="test", bounding=[0, 0, 500, 500])
    wf.groups = [group]
    
    _assign_groups(wf)
    
    assert len(group.nodes) == 2
    assert n1 in group.nodes
    assert n2 in group.nodes
    logger.info("Group assignment test passed")


def test_group_assignment_is_idempotent():
    logger.info("Testing group assignment idempotency")
    wf = Workflow()
    node = Node(id=1, type="TestNode", x=100, y=100, size=[100, 100])
    group = Group(id=1, name="test", bounding=[0, 0, 500, 500], nodes=[node])
    wf.nodes[1] = node
    wf.groups = [group]

    _assign_groups(wf)
    _assign_groups(wf)

    assert group.nodes == [node]
    assert wf.ungrouped_nodes == []
    logger.info("Group assignment idempotency test passed")


def test_internal_layout_stages():
    logger.info("Testing internal layout stages")
    wf = Workflow()
    # Create 4 nodes in a diamond pattern
    n1 = Node(id=1, type="NodeA", x=0, y=0, size=[100, 60])
    n2 = Node(id=2, type="NodeB", x=200, y=0, size=[100, 60])
    n3 = Node(id=3, type="NodeC", x=200, y=200, size=[100, 60])
    n4 = Node(id=4, type="NodeD", x=400, y=100, size=[100, 60])
    for n in [n1, n2, n3, n4]:
        wf.nodes[n.id] = n
    # Links: 1->2, 1->3, 2->4, 3->4
    links_data = [(10,1,0,2,0), (11,1,0,3,0), (12,2,0,4,0), (13,3,0,4,0)]
    for lid, src, sp, tgt, tp in links_data:
        link = Link(id=lid, source=src, source_port=sp, target=tgt, target_port=tp, type="DATA")
        wf.links[lid] = link
        wf.nodes[src].output_links.append(lid)
        wf.nodes[tgt].input_links.append(lid)
    wf.groups = [Group(id=0, name="ungrouped", bounding=[0,0,0,0])]
    for n in wf.nodes.values():
        wf.groups[0].nodes.append(n)
    
    _layout_groups_internal(wf)
    
    # Check that nodes were assigned to layers
    # Node 1 should be leftmost, node 4 rightmost
    assert wf.nodes[1].x < wf.nodes[2].x
    assert wf.nodes[2].x < wf.nodes[4].x
    assert wf.nodes[3].x < wf.nodes[4].x
    logger.info("Internal layout test passed")


def test_global_positioning():
    logger.info("Testing global positioning")
    wf = Workflow()
    group1 = Group(id=1, name="A", bounding=[0, 0, 300, 200])
    group2 = Group(id=2, name="B", bounding=[400, 0, 300, 200])
    wf.groups = [group1, group2]
    # Dummy nodes
    for g in wf.groups:
        n = Node(id=g.id*10, type="Dummy", x=0, y=0, size=[100, 60])
        g.nodes.append(n)
        wf.nodes[n.id] = n
    
    _position_groups_globally(wf)
    
    # group2 should be to the right of group1
    min_x_g1 = min(n.x for n in group1.nodes)
    min_x_g2 = min(n.x for n in group2.nodes)
    assert min_x_g2 > min_x_g1
    logger.info("Global positioning test passed")


def test_bounding_box_update():
    logger.info("Testing bounding box update")
    wf = Workflow()
    group = Group(id=1, name="test", bounding=[0,0,0,0])
    wf.groups = [group]
    n1 = Node(id=1, type="A", x=100, y=100, size=[200, 100])
    n2 = Node(id=2, type="B", x=150, y=250, size=[150, 80])
    group.nodes = [n1, n2]
    wf.nodes[1] = n1
    wf.nodes[2] = n2
    
    _update_bounding_boxes(wf)
    
    b = group.bounding
    # Should encompass both nodes plus padding
    assert b[0] <= 100 - 50  # x with padding
    assert b[1] <= 100 - 50  # y with padding
    # Width: max right - min left + 2*padding
    expected_w = (max(100+200, 150+150) - min(100, 150)) + 2*50
    expected_h = (max(100+100, 250+80) - min(100, 250)) + 2*50
    assert abs(b[2] - expected_w) < 1
    assert abs(b[3] - expected_h) < 1
    logger.info("Bounding box update test passed")


def test_layout_preserves_resized_group_container():
    logger.info("Testing resized group container preservation")
    wf = Workflow()
    group = Group(id=1, name="Load Model", bounding=[0, 0, 900, 500])
    wf.groups = [group]

    n1 = Node(id=1, type="Loader", x=100, y=100, size=[180, 80])
    n2 = Node(id=2, type="Consumer", x=420, y=150, size=[220, 120])
    for node in [n1, n2]:
        wf.nodes[node.id] = node
    link = Link(id=10, source=1, source_port=0, target=2, target_port=0, type="MODEL")
    wf.links[10] = link
    n1.output_links.append(10)
    n2.input_links.append(10)

    result = apply(wf)

    assert result.groups[0].bounding[2] >= 900
    assert result.groups[0].bounding[3] >= 500
    gx, gy, gw, gh = result.groups[0].bounding
    for node in result.groups[0].nodes:
        assert gx <= node.x
        assert gy <= node.y
        assert node.x + node.size[0] <= gx + gw
        assert node.y + node.size[1] <= gy + gh

    logger.info("Resized group container preservation test passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
