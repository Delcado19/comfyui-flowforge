"""Regression tests for the second-generation layout engine."""

from flowforge.layout import LayoutSettings
from flowforge.layout_engine_v2 import (
    EngineV2Score,
    _aspect_cost,
    _assign_scc_longest_path_layers,
    _boundary_crossings,
    _candidate_is_better,
    _group_can_be_refined,
    _minimize_crossings_with_dummies,
    _refine_group,
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



def test_aspect_cost_penalizes_tall_and_wide_shapes_symmetrically():
    balanced_width = 1350.0
    balanced_height = 1000.0
    tall_width = 400.0
    tall_height = 3000.0
    wide_width = 4000.0
    wide_height = 1000.0

    assert _aspect_cost(balanced_width, balanced_height) == 0.0
    assert _aspect_cost(tall_width, tall_height) > 0.0
    assert _aspect_cost(wide_width, wide_height) > 0.0


def test_port_aware_score_does_not_prefer_extreme_tower_just_for_reduced_width():
    balanced = Workflow()
    balanced.nodes = {
        1: Node(id=1, type="Balanced", x=0, y=0, size=[1350, 1000]),
    }

    tower = Workflow()
    tower.nodes = {
        1: Node(id=1, type="Tower", x=0, y=0, size=[400, 3000]),
    }

    assert _score_engine_v2(balanced).total < _score_engine_v2(tower).total

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


def _score(
    *,
    total: float,
    crossings: int,
    rtl: int,
    width: float,
    height: float = 1000.0,
    overlaps: int = 0,
) -> EngineV2Score:
    return EngineV2Score(
        total=total,
        crossings=crossings,
        right_to_left_links=rtl,
        movable_overlaps=overlaps,
        link_length=10_000.0,
        width=width,
        height=height,
    )


def test_realistic_balanced_canvas_scores_better_than_tower_regression():
    # Regression values are rounded from the real 57-node VTO workflow that
    # exposed the tower-layout failure. The tower saves crossings but adds RTL
    # links and severely degrades the canvas aspect ratio.
    balanced_total = (
        171 * 2_000.0
        + 15 * 3_000.0
        + 153_350.0 * 0.02
        + 6_782.0 * 2.5
        + 4_687.0
        + _aspect_cost(6_782.0, 4_687.0)
    )
    tower_total = (
        160 * 2_000.0
        + 20 * 3_000.0
        + 146_496.0 * 0.02
        + 5_601.0 * 2.5
        + 7_255.0
        + _aspect_cost(5_601.0, 7_255.0)
    )

    assert balanced_total < tower_total


def test_candidate_guard_rejects_large_width_growth_for_minor_quality_gain():
    incumbent = _score(total=1000.0, crossings=100, rtl=20, width=1000.0)
    wider = _score(total=900.0, crossings=99, rtl=20, width=1500.0)

    assert not _candidate_is_better(wider, incumbent)


def test_candidate_guard_allows_large_width_growth_for_significant_crossing_gain():
    incumbent = _score(total=1000.0, crossings=100, rtl=20, width=1000.0)
    wider = _score(total=900.0, crossings=90, rtl=20, width=1500.0)

    assert _candidate_is_better(wider, incumbent)


def test_candidate_guard_allows_width_growth_when_it_eliminates_all_rtl():
    incumbent = _score(total=1000.0, crossings=0, rtl=1, width=1000.0)
    wider = _score(total=900.0, crossings=0, rtl=0, width=1500.0)

    assert _candidate_is_better(wider, incumbent)


def test_group_refinement_reuses_compact_internal_layer_wrapping():
    workflow = Workflow()
    nodes = {
        node_id: Node(
            id=node_id,
            type=f"Node{node_id}",
            x=(node_id - 1) * 300,
            y=0,
            size=[200, 80],
            input_count=1 if node_id > 1 else 0,
            output_count=1 if node_id < 6 else 0,
        )
        for node_id in range(1, 7)
    }
    workflow.nodes = nodes
    for link_id, source_id in enumerate(range(1, 6), start=1):
        _attach_link(
            workflow,
            Link(
                id=link_id,
                source=source_id,
                source_port=0,
                target=source_id + 1,
                target_port=0,
                type="DATA",
            ),
        )
    group = Group(
        id=1,
        name="Long chain",
        nodes=list(nodes.values()),
        bounding=[0, 0, 2000, 400],
    )
    workflow.groups = [group]

    changed = _refine_group(
        workflow,
        group,
        LayoutSettings(node_x_distance=80, node_y_distance=80),
    )

    assert changed
    assert group.bounding[2] < 1200
    assert max(node.y for node in nodes.values()) > min(node.y for node in nodes.values())
