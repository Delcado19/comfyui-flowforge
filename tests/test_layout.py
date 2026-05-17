"""
Tests for the layout algorithm.
"""

from copy import deepcopy

from flowforge.model import Node, Link, Group, Workflow
import pytest
from flowforge.layout import (
    apply,
    apply_best_layout,
    LayoutSettings,
    LayoutScore,
    _assign_groups,
    _layout_groups_internal,
    _position_groups_globally,
    _score_layout_candidate,
    _resolve_layout_candidate_count,
    _assign_longest_path_layers,
    _minimize_layer_crossings,
    _shrink_nodes_to_minimum_size,
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
    
    _layout_groups_internal(wf, LayoutSettings())
    
    # Check that nodes were assigned to layers
    # Node 1 should be leftmost, node 4 rightmost
    assert wf.nodes[1].x < wf.nodes[2].x
    assert wf.nodes[2].x < wf.nodes[4].x
    assert wf.nodes[3].x < wf.nodes[4].x
    logger.info("Internal layout test passed")


def test_internal_layout_stacks_variable_height_nodes_without_overlap():
    logger.info("Testing variable-height internal layer packing")
    wf = Workflow()
    tall = Node(id=1, type="Tall", x=0, y=0, size=[200, 260])
    short = Node(id=2, type="Short", x=0, y=20, size=[200, 60])
    target = Node(id=3, type="Target", x=400, y=0, size=[200, 60])
    wf.nodes = {1: tall, 2: short, 3: target}
    wf.links = {
        10: Link(id=10, source=1, source_port=0, target=3, target_port=0, type="DATA"),
        11: Link(id=11, source=2, source_port=0, target=3, target_port=1, type="DATA"),
    }
    wf.groups = [Group(id=1, name="packed", bounding=[0, 0, 900, 700], nodes=[tall, short, target])]

    _layout_groups_internal(wf, LayoutSettings(node_x_distance=40, node_y_distance=40))

    same_layer_nodes = sorted([wf.nodes[1], wf.nodes[2]], key=lambda node: node.y)
    first, second = same_layer_nodes
    assert first.y + first.size[1] + 40 <= second.y
    logger.info("Variable-height internal layer packing test passed")


def test_nodes_shrink_to_compact_size_before_layout():
    logger.info("Testing compact node size preprocessing")
    wf = Workflow()
    # Real ComfyUI SaveImageClean: 1 slot input + 10 widget-inputs, 13 saved
    # widget values. Stacked rows = 1 + 13 = 14 single-line rows.
    # min_height = TITLE 26 + SLOT_OFFSET 8 + 14*20 + 13*4 + BOTTOM 12 = 378.
    large = Node(
        id=1,
        type="SaveImageClean",
        x=0,
        y=0,
        size=[960, 1100],
        input_count=11,
        widget_input_count=10,
        output_count=0,
        widgets_values=[""] * 13,
    )
    already_small = Node(id=2, type="VAELoader", x=0, y=0, size=[140, 60], input_count=1, output_count=1)
    reroute = Node(id=3, type="Reroute", x=0, y=0, size=[100, 80], input_count=1, output_count=1)
    wf.nodes = {1: large, 2: already_small, 3: reroute}

    _shrink_nodes_to_minimum_size(wf)

    assert large.size == [200.0, 378.0]
    assert already_small.size == [140.0, 60.0]
    assert reroute.size == [40.0, 40.0]
    logger.info("Compact node size preprocessing test passed")


def test_node_compaction_stacks_slot_and_widget_rows_for_ksampler():
    logger.info("Testing KSampler stacked compaction matches renderer")
    wf = Workflow()
    # Real modern KSampler: 4 slot inputs (model, positive, negative,
    # latent_image) + 6 widget-inputs (seed, steps, cfg, sampler_name,
    # scheduler, denoise), with 7 saved values (extra control_after_generate).
    # Stacked rows = 4 + 7 = 11 single-line rows.
    # min_height = 26 + 8 + 11*20 + 10*4 + 12 = 306.
    ksampler = Node(
        id=1,
        type="KSampler",
        x=0,
        y=0,
        size=[300, 720],
        input_count=10,
        widget_input_count=6,
        output_count=1,
        widgets_values=[957297162658210, "fixed", 5, 1, "euler", "simple", 1],
    )
    wf.nodes = {1: ksampler}

    _shrink_nodes_to_minimum_size(wf)

    assert ksampler.size == [200.0, 306.0]
    logger.info("KSampler stacked compaction test passed")


def test_node_compaction_preserves_long_text_widget_height():
    logger.info("Testing long text widget compact height")
    wf = Workflow()
    # Modern CLIPTextEncode: 1 slot input (clip) + 1 widget-input (text).
    # The long prompt forces the widget row to multiline height.
    node = Node(
        id=1,
        type="CLIPTextEncode",
        x=0,
        y=0,
        size=[440, 580],
        input_count=2,
        widget_input_count=1,
        output_count=1,
        widgets_values=["long prompt " * 40],
    )
    wf.nodes = {1: node}

    _shrink_nodes_to_minimum_size(wf)

    assert node.size[0] == 200.0
    # Multiline widget contributes at least 60 px; height must clearly exceed
    # the single-line stacked result (1 slot row + 1 widget row = 76 px content).
    assert node.size[1] >= 200.0
    logger.info("Long text widget compact height test passed")


def test_longest_path_layer_assignment_uses_deepest_dependency():
    logger.info("Testing longest-path layer assignment")
    node_ids = {1, 2, 3, 4, 5}
    adj = {
        1: [2, 3],
        2: [4],
        3: [5],
        4: [5],
        5: [],
    }
    rev_adj = {
        1: [],
        2: [1],
        3: [1],
        4: [2],
        5: [3, 4],
    }

    layers = _assign_longest_path_layers(node_ids, adj, rev_adj)

    assert layers[1] == 0
    assert layers[2] == 1
    assert layers[3] == 1
    assert layers[4] == 2
    assert layers[5] == 3
    logger.info("Longest-path layer assignment test passed")


def test_layer_crossing_minimization_uses_adjacent_layer_order():
    logger.info("Testing internal layer crossing minimization")
    wf = Workflow()
    for node_id, y in [(1, 0), (2, 200), (3, 0), (4, 200)]:
        wf.nodes[node_id] = Node(id=node_id, type=f"Node{node_id}", x=0, y=y, size=[100, 60])
    layer_to_nodes = {0: [1, 2], 1: [3, 4]}
    adj = {1: [4], 2: [3], 3: [], 4: []}
    rev_adj = {1: [], 2: [], 3: [2], 4: [1]}

    _minimize_layer_crossings(layer_to_nodes, adj, rev_adj, wf, max_layer=1)

    assert layer_to_nodes[1] == [4, 3]
    logger.info("Internal layer crossing minimization test passed")


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
    
    _position_groups_globally(wf, LayoutSettings())
    
    # group2 should still be placed after group1; this layout now prefers Y
    # packing, but can start a new column if the packed height budget is full.
    min_x_g1 = min(n.x for n in group1.nodes)
    min_x_g2 = min(n.x for n in group2.nodes)
    min_y_g1 = min(n.y for n in group1.nodes)
    min_y_g2 = min(n.y for n in group2.nodes)
    assert min_y_g2 > min_y_g1 or min_x_g2 > min_x_g1
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
    
    _update_bounding_boxes(wf, LayoutSettings())
    
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


def test_layout_compacts_resized_group_container():
    logger.info("Testing resized group container compaction")
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

    assert result.groups[0].bounding[2] < 900
    assert result.groups[0].bounding[3] < 500
    gx, gy, gw, gh = result.groups[0].bounding
    for node in result.groups[0].nodes:
        assert gx <= node.x
        assert gy <= node.y
        assert node.x + node.size[0] <= gx + gw
        assert node.y + node.size[1] <= gy + gh

    logger.info("Resized group container compaction test passed")


def test_decorative_nodes_move_to_left_column():
    logger.info("Testing decorative node placement")
    wf = Workflow()
    note = Node(id=1, type="Note", x=700, y=500, size=[260, 120])
    markdown = Node(id=2, type="MarkdownNote", x=900, y=100, size=[320, 180])
    regular = Node(id=3, type="LoadImage", x=200, y=200, size=[200, 120])
    wf.nodes[note.id] = note
    wf.nodes[markdown.id] = markdown
    wf.nodes[regular.id] = regular
    wf.groups = [Group(id=1, name="workflow", bounding=[150, 150, 900, 700])]

    apply(wf)

    assert note.x == 20.0
    assert markdown.x == 20.0
    assert markdown.y < note.y
    assert regular.x > markdown.x
    logger.info("Decorative node placement test passed")


def test_ungrouped_nodes_are_ordered_by_dataflow():
    logger.info("Testing ungrouped dataflow ordering")
    wf = Workflow()
    source = Node(id=1, type="Source", x=600, y=200, size=[120, 80])
    middle = Node(id=2, type="Middle", x=300, y=100, size=[120, 80])
    target = Node(id=3, type="Target", x=0, y=0, size=[120, 80])
    wf.nodes = {1: source, 2: middle, 3: target}
    links = [
        Link(id=10, source=1, source_port=0, target=2, target_port=0, type="DATA"),
        Link(id=11, source=2, source_port=0, target=3, target_port=0, type="DATA"),
    ]
    for link in links:
        wf.links[link.id] = link

    result = apply(wf)

    assert result.nodes[1].x <= result.nodes[2].x <= result.nodes[3].x
    logger.info("Ungrouped dataflow ordering test passed")


def test_spacing_setting_expands_layout_and_group():
    logger.info("Testing configurable x/y spacing")
    wf = Workflow()
    group = Group(id=1, name="load", bounding=[0, 0, 1000, 1000])
    wf.groups = [group]

    n1 = Node(id=1, type="NodeA", x=0, y=0, size=[120, 80], mode=0, order=0)
    n2 = Node(id=2, type="NodeB", x=200, y=0, size=[120, 80], mode=0, order=1)
    n3 = Node(id=3, type="NodeC", x=200, y=180, size=[120, 80], mode=0, order=2)
    n4 = Node(id=4, type="NodeD", x=400, y=80, size=[120, 80], mode=0, order=3)
    wf.nodes[1] = n1
    wf.nodes[2] = n2
    wf.nodes[3] = n3
    wf.nodes[4] = n4
    links = [
        Link(id=10, source=1, source_port=0, target=2, target_port=0, type="DATA"),
        Link(id=11, source=1, source_port=0, target=3, target_port=0, type="DATA"),
        Link(id=12, source=2, source_port=0, target=4, target_port=0, type="DATA"),
        Link(id=13, source=3, source_port=0, target=4, target_port=1, type="DATA"),
    ]
    for link in links:
        wf.links[link.id] = link
    n1.output_links.extend([10, 11])
    n2.input_links.append(10)
    n2.output_links.append(12)
    n3.input_links.append(11)
    n3.output_links.append(13)
    n4.input_links.extend([12, 13])

    compact = apply(deepcopy(wf), LayoutSettings(node_x_distance=40, node_y_distance=40))
    roomy = apply(deepcopy(wf), LayoutSettings(node_x_distance=160, node_y_distance=160))

    compact_dx = compact.nodes[2].x - compact.nodes[1].x
    roomy_dx = roomy.nodes[2].x - roomy.nodes[1].x
    compact_dy = compact.nodes[3].y - compact.nodes[2].y
    roomy_dy = roomy.nodes[3].y - roomy.nodes[2].y
    assert roomy_dx > compact_dx
    assert roomy_dy > compact_dy
    assert roomy.groups[0].bounding[2] >= compact.groups[0].bounding[2]
    assert roomy.groups[0].bounding[3] >= compact.groups[0].bounding[3]
    logger.info("Configurable spacing test passed")


def test_best_layout_tries_multiple_candidates_and_picks_best(monkeypatch):
    logger.info("Testing best-of layout candidate selection")
    wf = Workflow()
    n1 = Node(id=1, type="NodeA", x=0, y=0, size=[120, 80], mode=0, order=0)
    n2 = Node(id=2, type="NodeB", x=240, y=0, size=[120, 80], mode=0, order=1)
    wf.nodes[1] = n1
    wf.nodes[2] = n2
    link = Link(id=10, source=1, source_port=0, target=2, target_port=0, type="DATA")
    wf.links[10] = link
    n1.output_links.append(10)
    n2.input_links.append(10)

    attempts = []

    def fake_apply_layout_pass(workflow, settings=None, *, log=True):
        attempts.append((round(settings.node_x_distance, 2), round(settings.node_y_distance, 2), log))
        workflow.nodes[1].x = 0
        workflow.nodes[1].y = 0
        workflow.nodes[2].x = settings.node_x_distance + settings.node_y_distance
        workflow.nodes[2].y = settings.node_y_distance
        return workflow

    monkeypatch.setattr("flowforge.layout._apply_layout_pass", fake_apply_layout_pass)

    result = apply_best_layout(
        wf,
        LayoutSettings(node_x_distance=100, node_y_distance=100),
        candidate_count=5,
    )

    assert attempts[:5] == [
        (85.0, 115.0, False),
        (80.0, 100.0, False),
        (70.0, 90.0, False),
        (100.0, 100.0, False),
        (100.0, 80.0, False),
    ]
    assert attempts[-1] == (70.0, 90.0, True)
    assert result.nodes[2].x == 160.0
    assert result.nodes[2].y == 90.0
    logger.info("Best-of layout candidate selection test passed")


def test_layout_candidate_count_scales_with_workflow_size():
    logger.info("Testing dynamic candidate count selection")
    small = Workflow()
    for index in range(1, 4):
        small.nodes[index] = Node(id=index, type=f"Small{index}", x=0, y=0, size=[100, 60])

    medium = Workflow()
    for index in range(1, 18):
        medium.nodes[index] = Node(id=index, type=f"Medium{index}", x=0, y=0, size=[100, 60])

    large = Workflow()
    for index in range(1, 40):
        large.nodes[index] = Node(id=index, type=f"Large{index}", x=0, y=0, size=[100, 60])

    assert _resolve_layout_candidate_count(small) == 3
    assert _resolve_layout_candidate_count(medium) == 5
    assert _resolve_layout_candidate_count(large) == 7
    logger.info("Dynamic candidate count test passed")


def test_best_layout_stops_after_stale_candidates(monkeypatch):
    logger.info("Testing early stop in layout candidate search")
    wf = Workflow()
    wf.nodes[1] = Node(id=1, type="NodeA", x=0, y=0, size=[120, 80], mode=0, order=0)
    wf.nodes[2] = Node(id=2, type="NodeB", x=240, y=0, size=[120, 80], mode=0, order=1)
    wf.nodes[3] = Node(id=3, type="NodeC", x=480, y=0, size=[120, 80], mode=0, order=2)

    attempts = []
    totals = iter([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0])

    def fake_apply_layout_pass(workflow, settings=None, *, log=True):
        attempts.append((round(settings.node_x_distance, 2), round(settings.node_y_distance, 2), log))
        workflow.nodes[1].x = settings.node_x_distance
        workflow.nodes[2].x = settings.node_x_distance + 100
        workflow.nodes[3].x = settings.node_x_distance + 200
        return workflow

    def fake_score_layout_candidate(workflow):
        total = next(totals)
        return LayoutScore(
            total=total,
            width=100.0,
            height=100.0,
            link_cost=0.0,
            aspect_cost=0.0,
            gap_cost=0.0,
        )

    monkeypatch.setattr("flowforge.layout._apply_layout_pass", fake_apply_layout_pass)
    monkeypatch.setattr("flowforge.layout._score_layout_candidate", fake_score_layout_candidate)

    result = apply_best_layout(wf, LayoutSettings(node_x_distance=100, node_y_distance=100), candidate_count=7)

    assert len(attempts) == 4
    assert attempts[-1][2] is True
    assert result.layout_report is not None
    assert result.layout_report.candidate_count == 3
    assert result.layout_report.selected_candidate == 1
    assert result.nodes[1].x == attempts[0][0]
    logger.info("Early stop in layout candidate search test passed")


def test_layout_score_penalizes_overly_wide_layouts():
    logger.info("Testing aspect penalty in layout scoring")
    wide = Workflow()
    wide.nodes[1] = Node(id=1, type="NodeA", x=0, y=0, size=[100, 100])
    wide.nodes[2] = Node(id=2, type="NodeB", x=500, y=0, size=[100, 100])

    tall = Workflow()
    tall.nodes[1] = Node(id=1, type="NodeA", x=0, y=0, size=[100, 100])
    tall.nodes[2] = Node(id=2, type="NodeB", x=300, y=500, size=[100, 100])

    wide_score = _score_layout_candidate(wide)
    tall_score = _score_layout_candidate(tall)

    assert wide_score.width == 600
    assert wide_score.height == 100
    assert tall_score.width == 400
    assert tall_score.height == 600
    assert wide_score.total > tall_score.total
    assert wide_score.aspect_cost > 0
    assert tall_score.aspect_cost == 0
    logger.info("Aspect penalty scoring test passed")


def test_layout_score_penalizes_large_horizontal_gaps():
    logger.info("Testing gap penalty in layout scoring")
    packed = Workflow()
    packed.nodes[1] = Node(id=1, type="NodeA", x=0, y=0, size=[100, 100])
    packed.nodes[2] = Node(id=2, type="NodeB", x=120, y=0, size=[100, 100])
    packed.nodes[3] = Node(id=3, type="NodeC", x=260, y=0, size=[100, 100])

    gapped = Workflow()
    gapped.nodes[1] = Node(id=1, type="NodeA", x=0, y=0, size=[100, 100])
    gapped.nodes[2] = Node(id=2, type="NodeB", x=320, y=0, size=[100, 100])
    gapped.nodes[3] = Node(id=3, type="NodeC", x=720, y=0, size=[100, 100])

    packed_score = _score_layout_candidate(packed)
    gapped_score = _score_layout_candidate(gapped)

    assert packed_score.gap_cost == 0
    assert gapped_score.gap_cost > 0
    assert gapped_score.total > packed_score.total
    logger.info("Gap penalty scoring test passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
