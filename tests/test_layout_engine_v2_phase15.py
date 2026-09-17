"""Regression tests for boundary-aware layout engine v2.1 behavior."""

import flowforge.layout_engine_v2_phase15 as phase15
from flowforge.layout import LayoutSettings
from flowforge.layout_engine_v2 import EngineV2Score
from flowforge.model import Group, Link, Node, Workflow


def _attach_link(workflow: Workflow, link: Link) -> None:
    workflow.links[link.id] = link
    workflow.nodes[link.source].output_links.append(link.id)
    workflow.nodes[link.target].input_links.append(link.id)


def test_score_order_is_crossing_first_after_overlap_safety():
    fewer_crossings = EngineV2Score(
        total=999_999.0,
        crossings=4,
        right_to_left_links=20,
        movable_overlaps=0,
        link_length=99_999.0,
        width=9_000.0,
        height=9_000.0,
    )
    fewer_rtl_but_more_crossings = EngineV2Score(
        total=1.0,
        crossings=5,
        right_to_left_links=0,
        movable_overlaps=0,
        link_length=1.0,
        width=1.0,
        height=1.0,
    )

    assert phase15._score_order_key(fewer_crossings) < phase15._score_order_key(
        fewer_rtl_but_more_crossings
    )


def test_boundary_anchors_drive_target_order_from_external_geometry():
    workflow = Workflow()
    workflow.nodes = {
        1: Node(id=1, type="InternalSource", x=100, y=200, size=[200, 80], output_count=2),
        2: Node(id=2, type="TargetA", x=500, y=0, size=[200, 80], input_count=2),
        3: Node(id=3, type="TargetB", x=500, y=400, size=[200, 80], input_count=2),
        4: Node(id=4, type="ExternalTop", x=-500, y=0, size=[200, 80], output_count=1),
        5: Node(id=5, type="ExternalBottom", x=-500, y=400, size=[200, 80], output_count=1),
    }
    internal_links = [
        Link(id=10, source=1, source_port=0, target=2, target_port=0, type="DATA"),
        Link(id=11, source=1, source_port=1, target=3, target_port=0, type="DATA"),
    ]
    boundary_links = [
        Link(id=12, source=4, source_port=0, target=3, target_port=1, type="DATA"),
        Link(id=13, source=5, source_port=0, target=2, target_port=1, type="DATA"),
    ]
    layers = {1: 0, 2: 1, 3: 1}

    ordered = phase15._minimize_crossings_with_boundaries(
        workflow,
        layers,
        internal_links,
        boundary_links,
    )

    real_target_order = [vertex for vertex in ordered[1] if isinstance(vertex, int)]
    assert real_target_order.index(3) < real_target_order.index(2)


def test_group_refinement_reverts_when_incident_crossings_increase(monkeypatch):
    workflow = Workflow()
    source = Node(id=1, type="InternalSource", x=100, y=250, size=[200, 80], output_count=2)
    top = Node(id=2, type="Top", x=500, y=100, size=[200, 80], input_count=2)
    bottom = Node(id=3, type="Bottom", x=500, y=400, size=[200, 80], input_count=2)
    external_top = Node(id=4, type="ExternalTop", x=-500, y=100, size=[200, 80], output_count=1)
    external_bottom = Node(
        id=5,
        type="ExternalBottom",
        x=-500,
        y=400,
        size=[200, 80],
        output_count=1,
    )
    workflow.nodes = {
        1: source,
        2: top,
        3: bottom,
        4: external_top,
        5: external_bottom,
    }
    for link in [
        Link(id=10, source=1, source_port=0, target=2, target_port=0, type="DATA"),
        Link(id=11, source=1, source_port=1, target=3, target_port=0, type="DATA"),
        Link(id=12, source=4, source_port=0, target=2, target_port=1, type="DATA"),
        Link(id=13, source=5, source_port=0, target=3, target_port=1, type="DATA"),
    ]:
        _attach_link(workflow, link)

    group = Group(
        id=1,
        name="Pipeline",
        nodes=[source, top, bottom],
        bounding=[0, 0, 900, 600],
    )
    workflow.groups = [group]
    original_positions = {node.id: (node.x, node.y) for node in group.nodes}
    original_bounding = list(group.bounding)

    monkeypatch.setattr(
        phase15,
        "_minimize_crossings_with_boundaries",
        lambda *_args, **_kwargs: {0: [1], 1: [3, 2]},
    )

    changed = phase15._refine_group_boundary_aware(
        workflow,
        group,
        LayoutSettings(node_x_distance=60, node_y_distance=60),
    )

    assert not changed
    assert {node.id: (node.x, node.y) for node in group.nodes} == original_positions
    assert group.bounding == original_bounding
