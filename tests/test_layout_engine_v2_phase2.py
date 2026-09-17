"""Regression tests for v2 group-level crossing refinement."""

from flowforge.layout import LayoutSettings
from flowforge.layout_engine_v2 import EngineV2Score
from flowforge.layout_engine_v2_phase2 import (
    _apply_group_layer_order,
    _group_edge_weights,
    _quality_key,
    _weighted_boundary_crossings,
    _weighted_group_order,
    _weighted_median,
)
from flowforge.model import Group, Link, Node, Workflow


def _attach_link(workflow: Workflow, link: Link) -> None:
    workflow.links[link.id] = link
    workflow.nodes[link.source].output_links.append(link.id)
    workflow.nodes[link.target].input_links.append(link.id)


def _single_node_group(group_id: int, node_id: int, y: float, *, pinned: bool = False) -> Group:
    node = Node(id=node_id, type=f"Node{node_id}", x=0, y=y, size=[200, 80])
    return Group(
        id=group_id,
        name=f"Group{group_id}",
        nodes=[node],
        bounding=[0, y, 300, 180],
        pinned=pinned,
    )


def test_group_edge_weights_preserve_direct_wire_multiplicity():
    workflow = Workflow()
    source_group = _single_node_group(1, 1, 0)
    target_group = _single_node_group(2, 2, 0)
    workflow.groups = [source_group, target_group]
    workflow.nodes = {1: source_group.nodes[0], 2: target_group.nodes[0]}

    for link_id in range(1, 4):
        _attach_link(
            workflow,
            Link(
                id=link_id,
                source=1,
                source_port=link_id - 1,
                target=2,
                target_port=link_id - 1,
                type="DATA",
            ),
        )

    weights = _group_edge_weights(workflow, {1, 2}, {1: [2], 2: []})

    assert weights[(1, 2)] == 3


def test_weighted_median_prefers_high_multiplicity_neighbour():
    assert _weighted_median([(0, 1), (1, 5)]) == 1.0


def test_weighted_group_order_removes_simple_inter_group_crossing():
    workflow = Workflow()
    groups = [
        _single_node_group(1, 1, 0),
        _single_node_group(2, 2, 300),
        _single_node_group(3, 3, 0),
        _single_node_group(4, 4, 300),
    ]
    workflow.groups = groups
    workflow.nodes = {group.nodes[0].id: group.nodes[0] for group in groups}
    layers = {1: 0, 2: 0, 3: 1, 4: 1}
    weights = {(1, 4): 3, (2, 3): 1}

    ordered = _weighted_group_order(workflow, layers, weights, {1, 2, 3, 4})
    edges = [(1, 4, 3), (2, 3, 1)]

    assert _weighted_boundary_crossings(ordered, edges, 0, 1) == 0


def test_fixed_group_prevents_physical_reorder_of_its_layer():
    workflow = Workflow()
    fixed = _single_node_group(1, 1, 0, pinned=True)
    movable = _single_node_group(2, 2, 300)
    workflow.groups = [fixed, movable]
    workflow.nodes = {1: fixed.nodes[0], 2: movable.nodes[0]}
    original_positions = {
        1: (fixed.bounding[1], fixed.nodes[0].y),
        2: (movable.bounding[1], movable.nodes[0].y),
    }

    changed = _apply_group_layer_order(
        workflow,
        {1: 0, 2: 0},
        {0: [2, 1]},
        {2},
        LayoutSettings(),
    )

    assert not changed
    assert (fixed.bounding[1], fixed.nodes[0].y) == original_positions[1]
    assert (movable.bounding[1], movable.nodes[0].y) == original_positions[2]


def test_group_phase_quality_key_is_crossing_first_after_overlap_safety():
    fewer_crossings = EngineV2Score(
        total=999999.0,
        crossings=4,
        right_to_left_links=20,
        movable_overlaps=0,
        link_length=100000.0,
        width=10000.0,
        height=10000.0,
    )
    shorter_but_crossed = EngineV2Score(
        total=1.0,
        crossings=5,
        right_to_left_links=0,
        movable_overlaps=0,
        link_length=1.0,
        width=100.0,
        height=100.0,
    )

    assert _quality_key(fewer_crossings) < _quality_key(shorter_but_crossed)
