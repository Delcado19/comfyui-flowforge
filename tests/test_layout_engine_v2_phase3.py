"""Regression tests for compact global-flow refinement."""

from flowforge.layout import LayoutSettings
from flowforge.layout_engine_v2 import EngineV2Score
from flowforge.layout_engine_v2_phase3 import (
    _compact_candidate_is_better,
    _compact_decorative_nodes_above,
    _compact_global_flow,
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


def test_compact_candidate_accepts_large_width_reduction_with_small_quality_cost():
    baseline = _score(crossings=200, rtl=40, width=10_000, height=5_000)
    compact = _score(crossings=203, rtl=43, width=7_500, height=6_000)

    assert _compact_candidate_is_better(compact, baseline)


def test_compact_candidate_rejects_excessive_crossing_regression():
    baseline = _score(crossings=200, rtl=40, width=10_000, height=5_000)
    compact = _score(crossings=220, rtl=40, width=7_000, height=6_000)

    assert not _compact_candidate_is_better(compact, baseline)


def test_compact_global_flow_wraps_long_group_chain():
    workflow = Workflow()
    groups = []
    for node_id in range(1, 9):
        node = Node(
            id=node_id,
            type=f"Node{node_id}",
            x=(node_id - 1) * 500,
            y=100,
            size=[220, 100],
            input_count=1 if node_id > 1 else 0,
            output_count=1 if node_id < 8 else 0,
        )
        workflow.nodes[node_id] = node
        groups.append(
            Group(
                id=node_id,
                name=f"Group {node_id}",
                nodes=[node],
                bounding=[(node_id - 1) * 500, 50, 320, 220],
            )
        )
    workflow.groups = groups

    for link_id, source_id in enumerate(range(1, 8), start=1):
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

    original_width = (
        max(group.bounding[0] + group.bounding[2] for group in workflow.groups)
        - min(group.bounding[0] for group in workflow.groups)
    )

    _compact_global_flow(
        workflow,
        LayoutSettings(node_x_distance=80, node_y_distance=80, wrap_columns=True),
    )

    compact_width = (
        max(group.bounding[0] + group.bounding[2] for group in workflow.groups)
        - min(group.bounding[0] for group in workflow.groups)
    )

    assert compact_width < original_width * 0.75


def test_compact_decorative_nodes_move_above_graph_without_moving_graph():
    workflow = Workflow()
    note = Node(id=1, type="MarkdownNote", x=20, y=50, size=[8_000, 300])
    source = Node(id=2, type="Source", x=9_000, y=200, size=[220, 100])
    target = Node(id=3, type="Target", x=9_500, y=200, size=[220, 100])
    workflow.nodes = {1: note, 2: source, 3: target}
    workflow.ungrouped_nodes = [note, source, target]

    source_before = (source.x, source.y)
    target_before = (target.x, target.y)

    changed = _compact_decorative_nodes_above(workflow, LayoutSettings())

    assert changed
    assert (source.x, source.y) == source_before
    assert (target.x, target.y) == target_before
    assert note.x == source.x
    assert note.y + note.size[1] < min(source.y, target.y)
