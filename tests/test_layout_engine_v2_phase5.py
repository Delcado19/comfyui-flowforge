"""Regression tests for Phase 5 mixed global-flow refinement."""

from flowforge.layout import LayoutSettings
from flowforge.layout_engine_v2 import EngineV2Score, _assign_scc_longest_path_layers
from flowforge.layout_engine_v2_phase5 import (
    _CenterFlowMetrics,
    _build_mixed_graph,
    _center_flow_metrics,
    _has_phase5_unmodeled_group_geometry,
    _mixed_candidate_is_better,
    _mixed_rejection_reason,
    _phase5_vertical_gaps,
    _place_mixed_global_flow,
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


def _mixed_chain_workflow() -> Workflow:
    workflow = Workflow()
    group_a_node = Node(id=1, type="GroupA", x=150, y=150, size=[200, 100])
    group_b_node = Node(id=2, type="GroupB", x=4_050, y=150, size=[200, 100])
    middle = Node(id=10, type="Middle", x=2_000, y=150, size=[200, 100])
    last = Node(id=11, type="Last", x=6_000, y=150, size=[200, 100])

    workflow.nodes = {
        group_a_node.id: group_a_node,
        group_b_node.id: group_b_node,
        middle.id: middle,
        last.id: last,
    }
    workflow.groups = [
        Group(
            id=100,
            name="A",
            nodes=[group_a_node],
            bounding=[100, 100, 300, 220],
        ),
        Group(
            id=200,
            name="B",
            nodes=[group_b_node],
            bounding=[4_000, 100, 300, 220],
        ),
    ]
    workflow.ungrouped_nodes = [middle, last]

    _attach_link(
        workflow,
        Link(id=1, source=1, source_port=0, target=10, target_port=0, type="DATA"),
    )
    _attach_link(
        workflow,
        Link(id=2, source=10, source_port=0, target=2, target_port=0, type="DATA"),
    )
    _attach_link(
        workflow,
        Link(id=3, source=2, source_port=0, target=11, target_port=0, type="DATA"),
    )
    return workflow


def test_mixed_graph_keeps_group_ungrouped_dependency_chain():
    workflow = _mixed_chain_workflow()

    specs, spec_by_vertex, adjacency, edge_weights = _build_mixed_graph(workflow)

    assert set(specs) == {
        ("group", 100),
        ("group", 200),
        ("node", 10),
        ("node", 11),
    }
    assert edge_weights
    layers = _assign_scc_longest_path_layers(set(spec_by_vertex), adjacency)
    by_spec = {spec_by_vertex[vertex]: layer for vertex, layer in layers.items()}

    assert by_spec[("group", 100)] == 0
    assert by_spec[("node", 10)] == 1
    assert by_spec[("group", 200)] == 2
    assert by_spec[("node", 11)] == 3


def test_mixed_global_placement_preserves_group_internal_geometry():
    workflow = _mixed_chain_workflow()
    extra = Node(id=12, type="Extra", x=6_500, y=450, size=[200, 100])
    workflow.nodes[extra.id] = extra
    workflow.ungrouped_nodes.append(extra)
    _attach_link(
        workflow,
        Link(id=4, source=11, source_port=0, target=12, target_port=0, type="DATA"),
    )

    group = workflow.groups[0]
    member = group.nodes[0]
    original_offset = (
        member.x - group.bounding[0],
        member.y - group.bounding[1],
    )
    original_span = max(
        node.x + node.size[0] for node in workflow.nodes.values()
    ) - min(node.x for node in workflow.nodes.values())

    changed = _place_mixed_global_flow(
        workflow,
        LayoutSettings(node_x_distance=100, node_y_distance=80, wrap_columns=True),
        workflow_width=7_000,
    )

    new_span = max(
        node.x + node.size[0] for node in workflow.nodes.values()
    ) - min(node.x for node in workflow.nodes.values())
    new_offset = (
        member.x - group.bounding[0],
        member.y - group.bounding[1],
    )

    assert changed
    assert new_span < original_span
    assert new_offset == original_offset
    assert workflow.groups[0].bounding[0] < workflow.nodes[10].x
    assert workflow.nodes[10].x < workflow.groups[1].bounding[0]
    assert workflow.groups[1].bounding[0] < workflow.nodes[11].x
    assert workflow.nodes[11].x < extra.x


def test_phase5_skips_pinned_group_geometry():
    workflow = _mixed_chain_workflow()
    extra = Node(id=12, type="Extra", x=6_500, y=450, size=[200, 100])
    workflow.nodes[extra.id] = extra
    workflow.ungrouped_nodes.append(extra)
    workflow.groups[0].pinned = True

    changed = _place_mixed_global_flow(
        workflow,
        LayoutSettings(),
        workflow_width=7_000,
    )

    assert not changed


def test_phase5_skips_unmodeled_empty_group_geometry():
    workflow = _mixed_chain_workflow()
    extra = Node(id=12, type="Extra", x=6_500, y=450, size=[200, 100])
    workflow.nodes[extra.id] = extra
    workflow.ungrouped_nodes.append(extra)
    workflow.groups.append(
        Group(
            id=300,
            name="Authored Empty Panel",
            nodes=[],
            bounding=[900, 900, 1_200, 800],
        )
    )

    assert _has_phase5_unmodeled_group_geometry(workflow)

    changed = _place_mixed_global_flow(
        workflow,
        LayoutSettings(),
        workflow_width=7_000,
    )

    assert not changed


def test_phase5_candidate_requires_full_strict_gate():
    baseline = _score(crossings=100, rtl=20, width=6_000, height=4_000)
    accepted = _score(crossings=90, rtl=18, width=5_000, height=4_100)
    narrow_gain = _score(crossings=80, rtl=15, width=5_800, height=3_800)
    area_regression = _score(crossings=80, rtl=15, width=5_000, height=5_000)
    crossing_regression = _score(crossings=101, rtl=20, width=4_000, height=4_000)
    rtl_regression = _score(crossings=100, rtl=21, width=4_000, height=4_000)

    assert _mixed_candidate_is_better(accepted, baseline)
    assert not _mixed_candidate_is_better(narrow_gain, baseline)
    assert not _mixed_candidate_is_better(area_regression, baseline)
    assert not _mixed_candidate_is_better(crossing_regression, baseline)
    assert not _mixed_candidate_is_better(rtl_regression, baseline)
    assert (
        _mixed_rejection_reason(narrow_gain, baseline)
        == "insufficient_final_width_reduction"
    )
    assert _mixed_rejection_reason(area_regression, baseline) == "area_regression"


def test_phase5_candidate_rejects_corpus_center_metric_regressions():
    baseline = _score(crossings=100, rtl=20, width=6_000, height=4_000)
    candidate = _score(crossings=90, rtl=18, width=5_000, height=4_100)
    baseline_center = _CenterFlowMetrics(crossings=44, right_to_left_links=11)

    crossing_regression = _CenterFlowMetrics(
        crossings=45,
        right_to_left_links=8,
    )
    rtl_regression = _CenterFlowMetrics(
        crossings=43,
        right_to_left_links=12,
    )
    safe_center = _CenterFlowMetrics(
        crossings=43,
        right_to_left_links=8,
    )

    assert (
        _mixed_rejection_reason(
            candidate,
            baseline,
            crossing_regression,
            baseline_center,
        )
        == "center_crossing_regression"
    )
    assert (
        _mixed_rejection_reason(
            candidate,
            baseline,
            rtl_regression,
            baseline_center,
        )
        == "center_rtl_regression"
    )
    assert _mixed_candidate_is_better(
        candidate,
        baseline,
        safe_center,
        baseline_center,
    )


def test_center_flow_metrics_match_center_segment_geometry():
    workflow = Workflow()
    nodes = [
        Node(id=1, x=0, y=0, size=[100, 100]),
        Node(id=2, x=0, y=200, size=[100, 100]),
        Node(id=3, x=400, y=0, size=[100, 100]),
        Node(id=4, x=400, y=200, size=[100, 100]),
    ]
    workflow.nodes = {node.id: node for node in nodes}

    _attach_link(
        workflow,
        Link(id=1, source=1, source_port=0, target=4, target_port=0),
    )
    _attach_link(
        workflow,
        Link(id=2, source=2, source_port=0, target=3, target_port=0),
    )

    metrics = _center_flow_metrics(workflow)

    assert metrics.crossings == 1
    assert metrics.right_to_left_links == 0



def test_phase5_vertical_gap_profiles_use_existing_spacing_controls():
    settings = LayoutSettings(
        node_x_distance=100,
        node_y_distance=80,
        wrap_columns=True,
    )

    assert _phase5_vertical_gaps(settings) == [100, 80, 40]


def test_stable_order_supports_compact_vertical_gap():
    workflow = _mixed_chain_workflow()
    extra = Node(id=12, type="Extra", x=6_500, y=450, size=[200, 100])
    workflow.nodes[extra.id] = extra
    workflow.ungrouped_nodes.append(extra)
    _attach_link(
        workflow,
        Link(id=4, source=11, source_port=0, target=12, target_port=0, type="DATA"),
    )

    changed = _place_mixed_global_flow(
        workflow,
        LayoutSettings(node_x_distance=100, node_y_distance=80),
        workflow_width=7_000,
        order_mode="stable",
        vertical_gap=40,
    )

    assert changed
    assert workflow.groups[0].bounding[0] < workflow.nodes[10].x
    assert workflow.nodes[10].x < workflow.groups[1].bounding[0]
    assert workflow.groups[1].bounding[0] < workflow.nodes[11].x
    assert workflow.nodes[11].x < extra.x
