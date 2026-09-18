"""Regression tests for Phase 5 mixed global-flow refinement."""

from flowforge.layout import LayoutSettings
from flowforge.layout_engine_v2 import (
    EngineV2Score,
    _assign_scc_longest_path_layers,
    _score_engine_v2,
)
from flowforge.layout_engine_v2_phase5 import (
    _CenterFlowMetrics,
    _build_mixed_graph,
    _center_flow_metrics,
    _has_phase5_unmodeled_group_geometry,
    _mixed_candidate_is_better,
    _mixed_rejection_reason,
    _phase5_compressed_yband_candidate_variants,
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


def _parallel_mixed_workflow() -> Workflow:
    workflow = Workflow()
    wide_source = Node(id=1, type="WideSource", x=150, y=125, size=[200, 50])
    narrow_source = Node(id=2, type="NarrowSource", x=150, y=265, size=[200, 50])
    top_mid = Node(id=10, type="TopMid", x=3_000, y=125, size=[200, 100])
    bottom_mid = Node(id=11, type="BottomMid", x=3_000, y=265, size=[200, 100])
    top_last = Node(id=13, type="TopLast", x=4_500, y=125, size=[20, 100])
    bottom_last = Node(id=12, type="BottomLast", x=4_500, y=265, size=[200, 100])

    workflow.nodes = {
        wide_source.id: wide_source,
        narrow_source.id: narrow_source,
        top_mid.id: top_mid,
        bottom_mid.id: bottom_mid,
        top_last.id: top_last,
        bottom_last.id: bottom_last,
    }
    workflow.groups = [
        Group(
            id=100,
            name="Wide",
            nodes=[wide_source],
            bounding=[100, 100, 1_000, 100],
        ),
        Group(
            id=200,
            name="Narrow",
            nodes=[narrow_source],
            bounding=[100, 240, 300, 100],
        ),
    ]
    workflow.ungrouped_nodes = [top_mid, bottom_mid, top_last, bottom_last]

    _attach_link(
        workflow,
        Link(id=1, source=1, source_port=0, target=10, target_port=0, type="DATA"),
    )
    _attach_link(
        workflow,
        Link(id=2, source=2, source_port=0, target=11, target_port=0, type="DATA"),
    )
    _attach_link(
        workflow,
        Link(id=3, source=10, source_port=0, target=13, target_port=0, type="DATA"),
    )
    _attach_link(
        workflow,
        Link(id=4, source=11, source_port=0, target=12, target_port=0, type="DATA"),
    )
    return workflow


def test_compact_horizontal_mode_does_not_inherit_widest_parallel_layer():
    layer_workflow = _parallel_mixed_workflow()
    compact_workflow = _parallel_mixed_workflow()
    settings = LayoutSettings(node_x_distance=100, node_y_distance=80)

    assert _place_mixed_global_flow(
        layer_workflow,
        settings,
        workflow_width=7_000,
        order_mode="stable",
        horizontal_mode="layer",
        vertical_gap=40,
    )
    assert _place_mixed_global_flow(
        compact_workflow,
        settings,
        workflow_width=7_000,
        order_mode="stable",
        horizontal_mode="compact",
        vertical_gap=40,
    )

    assert compact_workflow.nodes[11].x < layer_workflow.nodes[11].x
    assert compact_workflow.nodes[12].x < layer_workflow.nodes[12].x
    assert compact_workflow.nodes[10].x == layer_workflow.nodes[10].x

    for link in compact_workflow.links.values():
        source = compact_workflow.nodes[link.source]
        target = compact_workflow.nodes[link.target]
        assert source.x + source.size[0] < target.x


def test_anchored_horizontal_mode_uses_compact_width_with_baseline_slack():
    compact_workflow = _parallel_mixed_workflow()
    anchored_workflow = _parallel_mixed_workflow()
    settings = LayoutSettings(node_x_distance=100, node_y_distance=80)

    assert _place_mixed_global_flow(
        compact_workflow,
        settings,
        workflow_width=7_000,
        order_mode="stable",
        horizontal_mode="compact",
        vertical_gap=40,
    )
    assert _place_mixed_global_flow(
        anchored_workflow,
        settings,
        workflow_width=7_000,
        order_mode="stable",
        horizontal_mode="anchored",
        vertical_gap=40,
    )

    assert anchored_workflow.nodes[11].x > compact_workflow.nodes[11].x
    assert anchored_workflow.nodes[12].x >= compact_workflow.nodes[12].x

    compact_right = max(
        [
            *(group.bounding[0] + group.bounding[2] for group in compact_workflow.groups),
            *(
                node.x + node.size[0]
                for node in compact_workflow.ungrouped_nodes
            ),
        ]
    )
    anchored_right = max(
        [
            *(group.bounding[0] + group.bounding[2] for group in anchored_workflow.groups),
            *(
                node.x + node.size[0]
                for node in anchored_workflow.ungrouped_nodes
            ),
        ]
    )
    assert anchored_right <= compact_right + 1e-9

    for link in anchored_workflow.links.values():
        source = anchored_workflow.nodes[link.source]
        target = anchored_workflow.nodes[link.target]
        assert source.x + source.size[0] < target.x


def test_baseline_vertical_mode_preserves_phase4_y_positions():
    workflow = _parallel_mixed_workflow()
    settings = LayoutSettings(node_x_distance=100, node_y_distance=80)
    original_group_y = {group.id: group.bounding[1] for group in workflow.groups}
    original_node_y = {node.id: node.y for node in workflow.ungrouped_nodes}

    assert _place_mixed_global_flow(
        workflow,
        settings,
        workflow_width=7_000,
        order_mode="weighted",
        horizontal_mode="anchored",
        vertical_mode="baseline",
    )

    assert {
        group.id: group.bounding[1]
        for group in workflow.groups
    } == original_group_y
    assert {
        node.id: node.y
        for node in workflow.ungrouped_nodes
    } == original_node_y


def test_layer_anchor_vertical_mode_preserves_layer_spacing_near_baseline_band():
    workflow = _parallel_mixed_workflow()
    workflow.nodes[11].y = 600
    settings = LayoutSettings(node_x_distance=100, node_y_distance=80)

    assert _place_mixed_global_flow(
        workflow,
        settings,
        workflow_width=7_000,
        order_mode="stable",
        horizontal_mode="anchored",
        vertical_gap=40,
        vertical_mode="layer_anchor",
    )

    top_mid = workflow.nodes[10]
    bottom_mid = workflow.nodes[11]
    assert bottom_mid.y - top_mid.y == 140
    assert (top_mid.y + bottom_mid.y) / 2.0 == 362.5


def test_layer_anchor_strength_interpolates_toward_phase4_band():
    workflow = _parallel_mixed_workflow()
    workflow.nodes[11].y = 600
    settings = LayoutSettings(node_x_distance=100, node_y_distance=80)

    assert _place_mixed_global_flow(
        workflow,
        settings,
        workflow_width=7_000,
        order_mode="stable",
        horizontal_mode="anchored",
        vertical_gap=40,
        vertical_mode="layer_anchor",
        vertical_anchor_strength=0.5,
    )

    top_mid = workflow.nodes[10]
    bottom_mid = workflow.nodes[11]
    assert bottom_mid.y - top_mid.y == 140
    assert (top_mid.y + bottom_mid.y) / 2.0 == 266.25


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



def test_compressed_yband_variants_include_minimum_gap():
    workflow = _parallel_mixed_workflow()
    settings = LayoutSettings(node_x_distance=100, node_y_distance=80)

    candidates = _phase5_compressed_yband_candidate_variants(
        workflow,
        settings,
        _score_engine_v2(workflow),
    )
    names = {candidate.name for candidate in candidates}

    assert len(candidates) == 12
    assert "weighted-anchoredx-yband25-gap-40" in names
    assert "weighted-anchoredx-yband25-gap-12" in names
    assert "stable-anchoredx-yband75-gap-40" in names
    assert "stable-anchoredx-yband75-gap-12" in names


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
