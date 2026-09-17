"""Regression tests for the second-generation layout engine."""

from flowforge.layout import LayoutSettings
from flowforge.layout_engine_v2 import (
    _assign_scc_longest_path_layers,
    _boundary_crossings,
    _group_can_be_refined,
    _minimize_crossings_with_dummies,
    _score_engine_v2,
    apply_best_layout,
)
from flowforge.model import Group, Link, Node, Workflow


def _attach_link(workflow: Workflow, link: Link) -> None:
    workflow.links[link.id] = link
    workflow.nodes[link.source].output_links.append(link.id)
    workflow.nodes[link.target].input_links.append(link.id)


def test_scc_layering_condenses_cycle_before_longest_path_assignment():
    node_ids = {1, 2, 3, 4}
    adj = {
        1: [2],
        2: [1, 3],
        3: [4],
        4: [],
    }

    layers = _assign_scc_longest_path_layers(node_ids, adj)

    assert layers[1] == layers[2]
    assert layers[3] == layers[1] + 1
    assert layers[4] == layers[3] + 1


def test_dummy_vertices_make_long_edges_participate_in_crossing_order():
    workflow = Workflow()
    workflow.nodes = {
        1: Node(id=1, type="SourceA", x=0, y=0, size=[200, 80]),
        2: Node(id=2, type="SourceB", x=0, y=200, size=[200, 80]),
        3: Node(id=3, type="TargetA", x=800, y=0, size=[200, 80]),
        4: Node(id=4, type="TargetB", x=800, y=200, size=[200, 80]),
    }
    links = [
        Link(id=10, source=1, source_port=0, target=4, target_port=0, type="DATA"),
        Link(id=11, source=2, source_port=0, target=3, target_port=0, type="DATA"),
    ]
    layers = {1: 0, 2: 0, 3: 2, 4: 2}

    ordered = _minimize_crossings_with_dummies(workflow, layers, links)
    expanded_edges = []
    for link in links:
        dummy = ("dummy", link.id, 1)
        expanded_edges.extend([(link.source, dummy), (dummy, link.target)])

    crossings = _boundary_crossings(ordered, expanded_edges, 0, 1)
    crossings += _boundary_crossings(ordered, expanded_edges, 1, 2)

    assert crossings == 0
    assert any(not isinstance(vertex, int) for vertex in ordered[1])


def test_port_aware_score_counts_right_to_left_links_from_socket_geometry():
    workflow = Workflow()
    source = Node(id=1, type="Source", x=400, y=0, size=[200, 100], output_count=1)
    target = Node(id=2, type="Target", x=100, y=0, size=[200, 100], input_count=1)
    workflow.nodes = {1: source, 2: target}
    _attach_link(
        workflow,
        Link(id=1, source=1, source_port=0, target=2, target_port=0, type="DATA"),
    )

    score = _score_engine_v2(workflow)

    assert score.right_to_left_links == 1
    assert score.link_length > 0


def test_pinned_group_is_not_structurally_refined():
    workflow = Workflow()
    first = Node(id=1, type="A", x=0, y=0, size=[200, 80])
    second = Node(id=2, type="B", x=300, y=0, size=[200, 80])
    workflow.nodes = {1: first, 2: second}
    group = Group(id=1, name="Pinned", nodes=[first, second], bounding=[0, 0, 600, 300], pinned=True)
    workflow.groups = [group]

    assert not _group_can_be_refined(workflow, group)


def test_apply_best_layout_preserves_graph_and_reports_selected_candidate():
    workflow = Workflow()
    nodes = {
        1: Node(id=1, type="Source", x=0, y=0, size=[200, 80], output_count=2),
        2: Node(id=2, type="Upper", x=300, y=0, size=[200, 80], input_count=1, output_count=1),
        3: Node(id=3, type="Lower", x=300, y=200, size=[200, 80], input_count=1, output_count=1),
        4: Node(id=4, type="Target", x=600, y=100, size=[200, 80], input_count=2),
    }
    workflow.nodes = nodes
    for link in [
        Link(id=10, source=1, source_port=0, target=2, target_port=0, type="DATA"),
        Link(id=11, source=1, source_port=1, target=3, target_port=0, type="DATA"),
        Link(id=12, source=2, source_port=0, target=4, target_port=0, type="DATA"),
        Link(id=13, source=3, source_port=0, target=4, target_port=1, type="DATA"),
    ]:
        _attach_link(workflow, link)
    workflow.groups = [
        Group(id=1, name="Pipeline", nodes=list(nodes.values()), bounding=[0, 0, 900, 500])
    ]

    original_links = {
        link_id: (link.source, link.source_port, link.target, link.target_port, link.type)
        for link_id, link in workflow.links.items()
    }
    result = apply_best_layout(
        workflow,
        LayoutSettings(node_x_distance=60, node_y_distance=60),
        candidate_count=1,
    )

    assert {
        link_id: (link.source, link.source_port, link.target, link.target_port, link.type)
        for link_id, link in result.links.items()
    } == original_links
    assert result.layout_report is not None
    assert result.layout_report.candidate_count >= 1
    assert result.layout_report.selected_candidate >= 1
