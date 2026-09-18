"""Regression tests for conservative ungrouped-flow compaction."""

from flowforge.layout import LayoutSettings
from flowforge.layout_engine_v2 import EngineV2Score
from flowforge.layout_engine_v2_phase4 import (
    _compact_pure_ungrouped_components,
    _eligible_ungrouped_nodes,
    _group_bridged_component_rows,
    _phase4_exclusion_counts,
    _ungrouped_candidate_is_better,
    _ungrouped_rejection_reason,
)
from flowforge.model import Group, Link, Node, Workflow


def _score(
    *,
    crossings: int,
    rtl: int,
    width: float,
    height: float,
    overlaps: int = 0,
) -> EngineV2Score:
    return EngineV2Score(
        total=0.0,
        crossings=crossings,
        right_to_left_links=rtl,
        movable_overlaps=overlaps,
        link_length=10_000.0,
        width=width,
        height=height,
    )


def _attach_link(workflow: Workflow, link: Link) -> None:
    workflow.links[link.id] = link
    workflow.nodes[link.source].output_links.append(link.id)
    workflow.nodes[link.target].input_links.append(link.id)


def test_ungrouped_candidate_requires_full_compaction_gate():
    baseline = _score(crossings=100, rtl=20, width=6_000, height=4_000)
    compact = _score(crossings=100, rtl=20, width=5_000, height=4_200)
    crossing_regression = _score(crossings=101, rtl=20, width=4_000, height=4_000)
    rtl_regression = _score(crossings=100, rtl=21, width=4_000, height=4_000)
    quality_gain_too_narrow = _score(
        crossings=90,
        rtl=20,
        width=5_800,
        height=3_800,
    )
    quality_gain_area_regression = _score(
        crossings=90,
        rtl=20,
        width=5_000,
        height=5_000,
    )

    assert _ungrouped_candidate_is_better(compact, baseline)
    assert not _ungrouped_candidate_is_better(crossing_regression, baseline)
    assert not _ungrouped_candidate_is_better(rtl_regression, baseline)
    assert not _ungrouped_candidate_is_better(quality_gain_too_narrow, baseline)
    assert not _ungrouped_candidate_is_better(quality_gain_area_regression, baseline)
    assert (
        _ungrouped_rejection_reason(quality_gain_too_narrow, baseline)
        == "insufficient_final_width_reduction"
    )
    assert (
        _ungrouped_rejection_reason(quality_gain_area_regression, baseline)
        == "area_regression"
    )


def test_eligible_ungrouped_nodes_include_direct_group_incident_nodes():
    workflow = Workflow()
    grouped = Node(id=1, type="Grouped", x=100, y=100, size=[200, 100])
    pure_a = Node(id=2, type="PureA", x=2_000, y=100, size=[200, 100])
    pure_b = Node(id=3, type="PureB", x=2_500, y=100, size=[200, 100])
    incident = Node(id=4, type="Incident", x=3_000, y=100, size=[200, 100])
    workflow.nodes = {
        grouped.id: grouped,
        pure_a.id: pure_a,
        pure_b.id: pure_b,
        incident.id: incident,
    }
    workflow.groups = [
        Group(
            id=10,
            name="Grouped",
            nodes=[grouped],
            bounding=[50, 50, 300, 220],
        )
    ]
    workflow.ungrouped_nodes = [pure_a, pure_b, incident]

    _attach_link(
        workflow,
        Link(id=1, source=2, source_port=0, target=3, target_port=0, type="DATA"),
    )
    _attach_link(
        workflow,
        Link(id=2, source=1, source_port=0, target=4, target_port=0, type="DATA"),
    )

    eligible_ids = {node.id for node in _eligible_ungrouped_nodes(workflow)}

    assert eligible_ids == {2, 3, 4}


def test_compact_pure_ungrouped_chain_reuses_group_horizontal_band():
    workflow = Workflow()
    grouped = Node(id=1, type="Grouped", x=100, y=100, size=[400, 150])
    workflow.nodes[grouped.id] = grouped
    workflow.groups = [
        Group(
            id=10,
            name="Main",
            nodes=[grouped],
            bounding=[50, 50, 3_000, 600],
        )
    ]

    chain = []
    for index in range(6):
        node_id = index + 2
        node = Node(
            id=node_id,
            type=f"Node{node_id}",
            x=3_500 + index * 1_000,
            y=100,
            size=[300, 120],
            input_count=1 if index else 0,
            output_count=1 if index < 5 else 0,
        )
        workflow.nodes[node_id] = node
        chain.append(node)
    workflow.ungrouped_nodes = list(chain)

    for index in range(5):
        _attach_link(
            workflow,
            Link(
                id=index + 1,
                source=chain[index].id,
                source_port=0,
                target=chain[index + 1].id,
                target_port=0,
                type="DATA",
            ),
        )

    original_span = (
        max(node.x + node.size[0] for node in chain)
        - min(node.x for node in chain)
    )

    changed = _compact_pure_ungrouped_components(
        workflow,
        LayoutSettings(node_x_distance=100, node_y_distance=80, wrap_columns=True),
        workflow_width=7_000,
    )

    compact_span = (
        max(node.x + node.size[0] for node in chain)
        - min(node.x for node in chain)
    )

    assert changed
    assert compact_span < original_span
    assert compact_span <= 4_500
    group = workflow.groups[0]
    group_rect = (
        group.bounding[0],
        group.bounding[1],
        group.bounding[0] + group.bounding[2],
        group.bounding[1] + group.bounding[3],
    )
    for node in chain:
        node_rect = (node.x, node.y, node.x + node.size[0], node.y + node.size[1])
        assert (
            node_rect[2] <= group_rect[0]
            or group_rect[2] <= node_rect[0]
            or node_rect[3] <= group_rect[1]
            or group_rect[3] <= node_rect[1]
        )


def test_phase4_diagnostics_report_group_incidence_without_excluding_it():
    workflow = Workflow()
    grouped = Node(id=1, type="Grouped", x=100, y=100, size=[200, 100])
    incident = Node(id=2, type="Incident", x=500, y=100, size=[200, 100])
    workflow.nodes = {1: grouped, 2: incident}
    workflow.groups = [
        Group(id=10, name="Grouped", nodes=[grouped], bounding=[50, 50, 300, 220])
    ]
    workflow.ungrouped_nodes = [incident]
    _attach_link(
        workflow,
        Link(id=1, source=1, source_port=0, target=2, target_port=0, type="DATA"),
    )

    counts = _phase4_exclusion_counts(workflow)
    eligible_ids = {node.id for node in _eligible_ungrouped_nodes(workflow)}

    assert counts["direct_group_nodes"] == 1
    assert incident.id in eligible_ids


def test_group_bridged_diagnostics_connect_ungrouped_nodes_through_fixed_groups():
    workflow = Workflow()
    group_a_node = Node(id=1, type="GroupA", x=500, y=100, size=[200, 100])
    group_b_node = Node(id=2, type="GroupB", x=1_500, y=100, size=[200, 100])
    first = Node(id=10, type="First", x=100, y=100, size=[200, 100])
    middle = Node(id=11, type="Middle", x=1_000, y=100, size=[200, 100])
    last = Node(id=12, type="Last", x=2_000, y=100, size=[200, 100])
    workflow.nodes = {
        group_a_node.id: group_a_node,
        group_b_node.id: group_b_node,
        first.id: first,
        middle.id: middle,
        last.id: last,
    }
    workflow.groups = [
        Group(id=100, name="A", nodes=[group_a_node], bounding=[450, 50, 300, 220]),
        Group(id=200, name="B", nodes=[group_b_node], bounding=[1_450, 50, 300, 220]),
    ]
    workflow.ungrouped_nodes = [first, middle, last]

    _attach_link(
        workflow,
        Link(id=1, source=10, source_port=0, target=1, target_port=0, type="DATA"),
    )
    _attach_link(
        workflow,
        Link(id=2, source=1, source_port=0, target=11, target_port=0, type="DATA"),
    )
    _attach_link(
        workflow,
        Link(id=3, source=11, source_port=0, target=2, target_port=0, type="DATA"),
    )
    _attach_link(
        workflow,
        Link(id=4, source=2, source_port=0, target=12, target_port=0, type="DATA"),
    )

    eligible = _eligible_ungrouped_nodes(workflow)
    rows = _group_bridged_component_rows(
        workflow,
        {node.id: node for node in eligible},
    )

    assert len(rows) == 1
    node_ids, group_ids, span = rows[0]
    assert node_ids == {10, 11, 12}
    assert group_ids == {100, 200}
    assert span == 2_100
