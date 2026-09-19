"""Regression tests for the second-generation layout engine."""

from flowforge.layout import LayoutSettings
from flowforge.layout_engine_v2 import (
    EngineV2Score,
    _aspect_cost,
    _assign_scc_longest_path_layers,
    _balanced_group_flow_candidate,
    _boundary_crossings,
    _candidate_is_better,
    _compact_candidate_beats_authored,
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



def test_balanced_group_flow_compacts_top_level_groups_and_preserves_nested_offsets():
    workflow = Workflow()
    workflow.nodes = {
        1: Node(id=1, type="Inner", x=120, y=120, size=[100, 80]),
        2: Node(id=2, type="Outer", x=420, y=120, size=[100, 80]),
        3: Node(id=3, type="Source", x=1520, y=120, size=[100, 80]),
        4: Node(id=4, type="Target", x=3020, y=120, size=[100, 80]),
    }
    workflow.groups = [
        Group(id=10, name="Outer", bounding=[0, 0, 700, 500]),
        Group(id=11, name="Inner", bounding=[80, 80, 220, 220]),
        Group(id=20, name="Source", bounding=[1480, 80, 300, 220]),
        Group(id=30, name="Target", bounding=[2980, 80, 300, 220]),
    ]
    _attach_link(
        workflow,
        Link(id=100, source=3, source_port=0, target=2, target_port=0, type="DATA"),
    )
    _attach_link(
        workflow,
        Link(id=101, source=2, source_port=0, target=4, target_port=0, type="DATA"),
    )

    original_width = _score_engine_v2(workflow).width
    original_child_offset = (
        workflow.groups[1].bounding[0] - workflow.groups[0].bounding[0],
        workflow.groups[1].bounding[1] - workflow.groups[0].bounding[1],
    )
    original_inner_node_offset = (
        workflow.nodes[1].x - workflow.groups[1].bounding[0],
        workflow.nodes[1].y - workflow.groups[1].bounding[1],
    )

    candidate = _balanced_group_flow_candidate(
        workflow,
        LayoutSettings(node_x_distance=40, node_y_distance=40),
    )

    assert candidate is not None
    outer, inner, source, target = candidate.groups
    assert _score_engine_v2(candidate).width < original_width
    assert outer.bounding[0] < source.bounding[0] < target.bounding[0]
    assert (
        inner.bounding[0] - outer.bounding[0],
        inner.bounding[1] - outer.bounding[1],
    ) == original_child_offset
    assert (
        candidate.nodes[1].x - inner.bounding[0],
        candidate.nodes[1].y - inner.bounding[1],
    ) == original_inner_node_offset


def test_balanced_refined_compacts_ordinary_group_but_preserves_nested_subtree():
    workflow = Workflow()
    workflow.nodes = {
        1: Node(id=1, type="Inner", x=120, y=120, size=[100, 80]),
        2: Node(id=2, type="Outer", x=420, y=120, size=[100, 80]),
        3: Node(id=3, type="A", x=1520, y=120, size=[100, 80]),
        4: Node(id=4, type="B", x=2120, y=120, size=[100, 80]),
        5: Node(id=5, type="C", x=2720, y=120, size=[100, 80]),
    }
    workflow.groups = [
        Group(id=10, name="Outer", bounding=[0, 0, 700, 500]),
        Group(id=11, name="Inner", bounding=[80, 80, 220, 220]),
        Group(id=20, name="Ordinary", bounding=[1480, 80, 1500, 420]),
    ]
    _attach_link(
        workflow,
        Link(id=100, source=3, source_port=0, target=4, target_port=0, type="DATA"),
    )
    _attach_link(
        workflow,
        Link(id=101, source=4, source_port=0, target=5, target_port=0, type="DATA"),
    )

    original_child_offset = (
        workflow.groups[1].bounding[0] - workflow.groups[0].bounding[0],
        workflow.groups[1].bounding[1] - workflow.groups[0].bounding[1],
    )
    original_ordinary_width = workflow.groups[2].bounding[2]

    candidate = _balanced_group_flow_candidate(
        workflow,
        LayoutSettings(node_x_distance=40, node_y_distance=40),
        refine_internals=True,
    )

    assert candidate is not None
    outer, inner, ordinary = candidate.groups
    assert ordinary.bounding[2] < original_ordinary_width
    assert (
        inner.bounding[0] - outer.bounding[0],
        inner.bounding[1] - outer.bounding[1],
    ) == original_child_offset


def test_balanced_group_flow_compacts_ungrouped_bridge_without_tower_growth():
    workflow = Workflow()
    workflow.nodes = {
        1: Node(id=1, type="Source", x=100, y=100, size=[100, 80]),
        2: Node(id=2, type="Bridge", x=1200, y=120, size=[100, 80]),
        3: Node(id=3, type="Target", x=2400, y=100, size=[100, 80]),
    }
    workflow.groups = [
        Group(id=10, name="Source", bounding=[50, 50, 300, 220]),
        Group(id=20, name="Target", bounding=[2350, 50, 300, 220]),
    ]
    _attach_link(
        workflow,
        Link(id=100, source=1, source_port=0, target=2, target_port=0, type="DATA"),
    )
    _attach_link(
        workflow,
        Link(id=101, source=2, source_port=0, target=3, target_port=0, type="DATA"),
    )

    original_score = _score_engine_v2(workflow)
    candidate = _balanced_group_flow_candidate(
        workflow,
        LayoutSettings(node_x_distance=40, node_y_distance=40),
    )

    assert candidate is not None
    score = _score_engine_v2(candidate)
    assert score.width < original_score.width
    assert score.height <= original_score.height + 100
    assert candidate.nodes[2].y == workflow.nodes[2].y


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


def test_compact_candidate_can_trade_two_crossings_for_material_geometry_gain():
    authored = EngineV2Score(
        total=412847.11,
        crossings=171,
        right_to_left_links=15,
        movable_overlaps=0,
        link_length=153350.0,
        width=6782.0,
        height=4687.0,
    )
    compact = EngineV2Score(
        total=414561.00,
        crossings=173,
        right_to_left_links=15,
        movable_overlaps=0,
        link_length=139728.0,
        width=6191.0,
        height=4290.0,
    )

    assert compact.total > authored.total
    assert _compact_candidate_beats_authored(compact, authored)


def test_compact_candidate_rejects_crossing_or_rtl_regression_beyond_guard():
    authored = EngineV2Score(
        total=1000.0,
        crossings=100,
        right_to_left_links=10,
        movable_overlaps=0,
        link_length=10000.0,
        width=5000.0,
        height=3500.0,
    )
    too_many_crossings = EngineV2Score(
        total=900.0,
        crossings=103,
        right_to_left_links=10,
        movable_overlaps=0,
        link_length=8000.0,
        width=4300.0,
        height=3000.0,
    )
    worse_rtl = EngineV2Score(
        total=900.0,
        crossings=102,
        right_to_left_links=11,
        movable_overlaps=0,
        link_length=8000.0,
        width=4300.0,
        height=3000.0,
    )

    assert not _compact_candidate_beats_authored(too_many_crossings, authored)
    assert not _compact_candidate_beats_authored(worse_rtl, authored)


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
