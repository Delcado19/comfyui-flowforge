"""
Tests for the layout algorithm.
"""

from copy import deepcopy

from flowforge.model import Node, Link, Group, Workflow
from flowforge.optimizer import optimize
import pytest
from flowforge.layout import (
    apply,
    apply_best_layout,
    LayoutSettings,
    LayoutScore,
    VIRTUAL_HUB_MAX_GAP,
    VIRTUAL_HUB_MIN_GAP,
    _assign_groups,
    _layout_groups_internal,
    _node_input_port_y,
    _node_output_port_y,
    _position_groups_globally,
    _position_linked_ungrouped_nodes,
    _position_text_previews_near_sources,
    _score_layout_candidate,
    _resolve_layout_candidate_count,
    _separate_unpinned_nodes_from_pinned_geometry,
    _assign_longest_path_layers,
    _compress_debug_sidecar_layers,
    _minimize_layer_crossings,
    _shrink_nodes_to_minimum_size,
    _update_bounding_boxes,
    _resolve_group_geometry_overlaps,
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


def test_internal_layout_uses_per_layer_widths():
    logger.info("Testing internal layout uses per-layer widths")
    wf = Workflow()
    source = Node(id=1, type="Loader", x=0, y=0, size=[220, 80], output_links=[10])
    middle = Node(id=2, type="Processor", x=0, y=0, size=[260, 100], input_links=[10], output_links=[11])
    wide = Node(id=3, type="SaveImageClean", x=0, y=0, size=[960, 500], input_links=[11])
    group = Group(id=1, name="Preview-heavy group", bounding=[0, 0, 2000, 500], nodes=[source, middle, wide])
    wf.groups = [group]
    wf.nodes = {1: source, 2: middle, 3: wide}
    wf.links = {
        10: Link(id=10, source=1, source_port=0, target=2, target_port=0, type="DATA"),
        11: Link(id=11, source=2, source_port=0, target=3, target_port=0, type="DATA"),
    }

    _layout_groups_internal(wf, LayoutSettings(50, 50))

    assert middle.x - source.x == source.size[0] + 50
    assert wide.x - middle.x == middle.size[0] + 50
    logger.info("Per-layer internal width test passed")


def test_nodes_shrink_to_compact_size_before_layout():
    logger.info("Testing compact node size preprocessing")
    wf = Workflow()
    # Real ComfyUI custom control: 1 slot input + 10 widget-inputs, 13 saved
    # widget values. Stacked rows = 1 + 13 = 14 single-line rows.
    # min_height = TITLE 26 + SLOT_OFFSET 8 + 14*20 + 13*4 + BOTTOM 12 = 378.
    large = Node(
        id=1,
        type="CustomControlPanel",
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


def test_image_io_nodes_keep_authored_size_before_layout():
    logger.info("Testing image I/O node size preservation")
    wf = Workflow()
    load_image = Node(id=1, type="LoadImage", x=0, y=0, size=[360, 260])
    save_image = Node(id=2, type="SaveImage", x=0, y=0, size=[480, 420])
    save_clean = Node(id=3, type="SaveImageClean", x=0, y=0, size=[960, 1100])
    wf.nodes = {1: load_image, 2: save_image, 3: save_clean}

    _shrink_nodes_to_minimum_size(wf)

    assert load_image.size == [360, 260]
    assert save_image.size == [480, 420]
    assert save_clean.size == [960, 1100]
    logger.info("Image I/O node size preservation test passed")


def test_annotation_nodes_keep_authored_size_before_layout():
    logger.info("Testing annotation node size preservation")
    wf = Workflow()
    note = Node(id=1, type="Note", x=0, y=0, size=[520, 260])
    markdown = Node(id=2, type="MarkdownNote", x=0, y=0, size=[640, 360])
    label = Node(id=3, type="Label (rgthree)", x=0, y=0, size=[780, 90])
    wf.nodes = {1: note, 2: markdown, 3: label}

    _shrink_nodes_to_minimum_size(wf)

    assert note.size == [520, 260]
    assert markdown.size == [640, 360]
    assert label.size == [780, 90]
    logger.info("Annotation node size preservation test passed")


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


def test_debug_sidecars_do_not_extend_group_layer_depth():
    logger.info("Testing debug sidecar layer compression")
    wf = Workflow()
    source = Node(id=1, type="MaskSource", x=0, y=0, size=[200, 80])
    composite = Node(id=2, type="MaskComposite", x=300, y=0, size=[220, 100])
    mask_image = Node(id=3, type="MaskToImage", x=600, y=0, size=[180, 60])
    preview = Node(id=4, type="PreviewImage", x=900, y=0, size=[260, 220])
    wf.nodes = {node.id: node for node in (source, composite, mask_image, preview)}
    adj = {1: [2], 2: [3], 3: [4], 4: []}
    rev_adj = {1: [], 2: [1], 3: [2], 4: [3]}

    layers = _assign_longest_path_layers(set(wf.nodes), adj, rev_adj)
    assert layers[4] == 3

    _compress_debug_sidecar_layers(layers, adj, rev_adj, wf)

    assert layers[2] == 1
    assert layers[3] == 1
    assert layers[4] == 1
    logger.info("Debug sidecar layer compression test passed")


def test_prompt_control_sidecars_stay_with_target_block():
    logger.info("Testing prompt control sidecar layer placement")
    wf = Workflow()
    resize = Node(id=1, type="ImageResize+", x=0, y=0, size=[200, 200])
    vl = Node(id=2, type="AILab_QwenVL_Advanced", x=300, y=0, size=[240, 400])
    manual = Node(id=3, type="Text Multiline", x=0, y=500, size=[220, 120])
    switch = Node(id=4, type="Any Switch (rgthree)", x=600, y=0, size=[220, 120])
    prefix = Node(id=5, type="Text Multiline", x=0, y=650, size=[220, 120])
    suffix = Node(id=6, type="Text Multiline", x=0, y=800, size=[220, 120])
    concat = Node(id=7, type="Text Concatenate", x=900, y=0, size=[220, 160])
    wf.nodes = {node.id: node for node in (resize, vl, manual, switch, prefix, suffix, concat)}
    adj = {
        1: [2],
        2: [4],
        3: [4],
        4: [7],
        5: [7],
        6: [7],
        7: [],
    }
    rev_adj = {
        1: [],
        2: [1],
        3: [],
        4: [2, 3],
        5: [],
        6: [],
        7: [4, 5, 6],
    }

    layers = _assign_longest_path_layers(set(wf.nodes), adj, rev_adj)
    assert layers[7] == 3

    _compress_debug_sidecar_layers(layers, adj, rev_adj, wf)

    assert layers[2] == 1
    assert layers[3] == layers[4] == 1
    assert layers[5] == layers[6] == layers[7] == 2
    logger.info("Prompt control sidecar layer placement test passed")


def test_text_preview_stays_near_source_output():
    logger.info("Testing text preview source anchoring")
    wf = Workflow()
    qwen = Node(id=1, type="AILab_QwenVL_Advanced", x=300, y=200, size=[200, 540])
    switch = Node(id=2, type="Any Switch (rgthree)", x=300, y=812, size=[200, 110])
    concat = Node(id=3, type="Text Concatenate", x=556, y=200, size=[200, 180])
    preview = Node(id=4, type="ShowText|pysssss", x=300, y=1000, size=[200, 134])
    wf.nodes = {node.id: node for node in (qwen, switch, concat, preview)}
    links = [
        Link(id=10, source=qwen.id, source_port=0, target=preview.id, target_port=0, type="STRING"),
        Link(id=11, source=qwen.id, source_port=1, target=switch.id, target_port=0, type="STRING"),
    ]
    for link in links:
        wf.links[link.id] = link
        wf.nodes[link.source].output_links.append(link.id)
        wf.nodes[link.target].input_links.append(link.id)

    _position_text_previews_near_sources(wf, LayoutSettings())

    assert preview.x > qwen.x + qwen.size[0]
    assert qwen.y < preview.y < qwen.y + qwen.size[1]
    assert not _test_rectangles_overlap(preview, concat)
    assert not _test_rectangles_overlap(preview, switch)
    logger.info("Text preview source anchoring test passed")


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


def test_groups_wrap_downward_after_soft_row_width():
    logger.info("Testing group rows wrap downward after soft width")
    wf = Workflow()
    wf.groups = []
    for index in range(4):
        group = Group(id=index + 1, name=f"group-{index + 1}", bounding=[0, 0, 420, 220])
        node = Node(id=index + 1, type="Node", x=0, y=0, size=[260, 120])
        group.nodes = [node]
        wf.groups.append(group)
        wf.nodes[node.id] = node

    _position_groups_globally(wf, LayoutSettings())

    assert wf.groups[0].bounding[1] == wf.groups[1].bounding[1]
    assert wf.groups[2].bounding[0] == wf.groups[0].bounding[0]
    assert wf.groups[2].bounding[1] > wf.groups[0].bounding[1]
    logger.info("Group row wrapping test passed")


def test_group_positioning_bounds_width_by_using_new_rows():
    logger.info("Testing group packing bounds width with new rows")
    wf = Workflow()
    wf.groups = []
    for index in range(5):
        group = Group(id=index + 1, name=f"group-{index + 1}", bounding=[0, 0, 420, 220])
        node = Node(id=index + 1, type="Node", x=0, y=0, size=[260, 120])
        group.nodes = [node]
        wf.groups.append(group)
        wf.nodes[node.id] = node

    _position_groups_globally(wf, LayoutSettings())

    layout_left = min(group.bounding[0] for group in wf.groups)
    layout_right = max(group.bounding[0] + group.bounding[2] for group in wf.groups)
    row_tops = {round(group.bounding[1], 3) for group in wf.groups}
    assert len(row_tops) > 1
    assert layout_right - layout_left < 1200.0
    assert wf.groups[-1].bounding[1] > wf.groups[0].bounding[1]
    logger.info("Bounded-width group packing test passed")


def test_large_group_sequence_wraps_before_long_horizontal_strip():
    logger.info("Testing large group sequence wraps before long horizontal strip")
    wf = Workflow()
    group_specs = [
        (1, "Reference", 360, 180),
        (2, "Realism", 900, 460),
        (3, "Canny", 700, 260),
        (4, "Upscale", 1500, 300),
        (5, "Outputs", 360, 520),
    ]
    for group_id, name, width, height in group_specs:
        group = Group(id=group_id, name=name, bounding=[0, 0, width, height])
        node = Node(id=group_id, type="Node", x=0, y=0, size=[width - 100, height - 80])
        group.nodes = [node]
        wf.groups.append(group)
        wf.nodes[node.id] = node

    _position_groups_globally(wf, LayoutSettings(50, 50))

    row_tops = {round(group.bounding[1], 3) for group in wf.groups}
    layout_left = min(group.bounding[0] for group in wf.groups)
    layout_right = max(group.bounding[0] + group.bounding[2] for group in wf.groups)
    total_group_width = sum(group.bounding[2] for group in wf.groups)
    total_gap_width = LayoutSettings(50, 50).group_h_gap * (len(wf.groups) - 1)

    assert len(row_tops) > 1
    assert layout_right - layout_left < total_group_width + total_gap_width
    logger.info("Large group sequence wrapping test passed")


def test_connected_groups_use_flow_columns():
    logger.info("Testing connected groups use dataflow columns")
    wf = Workflow()
    source_group = Group(id=1, name="Source", bounding=[0, 400, 420, 220])
    branch_a_group = Group(id=2, name="Branch A", bounding=[900, 40, 420, 220])
    branch_b_group = Group(id=3, name="Branch B", bounding=[900, 780, 420, 220])
    output_group = Group(id=4, name="Output", bounding=[1800, 420, 420, 220])
    wf.groups = [source_group, branch_a_group, branch_b_group, output_group]

    source = Node(id=1, type="Loader", x=40, y=440, size=[240, 100], output_links=[10, 11])
    branch_a = Node(id=2, type="Processor", x=940, y=80, size=[260, 120], input_links=[10], output_links=[12])
    branch_b = Node(id=3, type="Processor", x=940, y=820, size=[260, 120], input_links=[11], output_links=[13])
    output = Node(id=4, type="SaveImageClean", x=1840, y=460, size=[360, 160], input_links=[12, 13])
    source_group.nodes = [source]
    branch_a_group.nodes = [branch_a]
    branch_b_group.nodes = [branch_b]
    output_group.nodes = [output]
    wf.nodes = {node.id: node for node in (source, branch_a, branch_b, output)}
    wf.links = {
        10: Link(id=10, source=1, source_port=0, target=2, target_port=0, type="DATA"),
        11: Link(id=11, source=1, source_port=0, target=3, target_port=0, type="DATA"),
        12: Link(id=12, source=2, source_port=0, target=4, target_port=0, type="DATA"),
        13: Link(id=13, source=3, source_port=0, target=4, target_port=0, type="DATA"),
    }

    _position_groups_globally(wf, LayoutSettings(50, 50))

    assert source_group.bounding[0] < branch_a_group.bounding[0] < output_group.bounding[0]
    assert source_group.bounding[0] < branch_b_group.bounding[0] < output_group.bounding[0]
    assert branch_a_group.bounding[0] == branch_b_group.bounding[0]
    assert branch_b_group.bounding[1] > branch_a_group.bounding[1]
    logger.info("Connected group flow-column test passed")


def test_ungrouped_bridge_nodes_influence_group_flow_columns():
    logger.info("Testing ungrouped bridge nodes influence group flow columns")
    wf = Workflow()
    source = Node(id=1, type="LoadImage", x=20, y=20, size=[220, 120], output_links=[10])
    bridge = Node(id=2, type="ImageScaleToTotalPixels", x=500, y=40, size=[260, 120], input_links=[10], output_links=[11])
    target = Node(id=3, type="VAEEncode", x=1020, y=20, size=[240, 120], input_links=[11])
    source_group = Group(id=1, name="Source", bounding=[0, 0, 320, 220])
    target_group = Group(id=2, name="Target", bounding=[1000, 0, 360, 220])
    wf.groups = [target_group, source_group]
    wf.nodes = {node.id: node for node in (source, bridge, target)}
    wf.links = {
        10: Link(id=10, source=1, source_port=0, target=2, target_port=0, type="IMAGE"),
        11: Link(id=11, source=2, source_port=0, target=3, target_port=0, type="IMAGE"),
    }

    result = apply(wf, LayoutSettings(50, 50))
    groups_by_name = {group.name: group for group in result.groups}
    source_group = groups_by_name["Source"]
    target_group = groups_by_name["Target"]
    bridge = result.nodes[2]

    assert source_group.bounding[0] < target_group.bounding[0]
    assert bridge.x >= source_group.bounding[0] + source_group.bounding[2]
    assert bridge.x + bridge.size[0] <= target_group.bounding[0]
    assert all(bridge not in group.nodes for group in result.groups)
    logger.info("Ungrouped bridge flow-column test passed")


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


def test_group_geometry_clearance_moves_whole_groups():
    logger.info("Testing group geometry clearance")
    wf = Workflow()
    first = Group(id=1, name="mask", bounding=[100, 100, 420, 320])
    second = Group(id=2, name="prompt", bounding=[140, 260, 480, 340])
    wf.groups = [first, second]

    first.nodes = [
        Node(id=1, type="MaskSource", x=150, y=150, size=[160, 90]),
        Node(id=2, type="MaskPreview", x=150, y=270, size=[180, 90]),
    ]
    second.nodes = [
        Node(id=3, type="Prompt", x=190, y=310, size=[200, 100]),
        Node(id=4, type="TextEncode", x=430, y=310, size=[160, 100]),
    ]
    wf.nodes = {node.id: node for group in wf.groups for node in group.nodes}

    _resolve_group_geometry_overlaps(wf, LayoutSettings())

    assert not _test_rectangles_overlap_groups(first, second)
    assert second.bounding[1] > 260
    for node in second.nodes:
        assert second.bounding[0] <= node.x
        assert second.bounding[1] <= node.y
        assert node.x + node.size[0] <= second.bounding[0] + second.bounding[2]
        assert node.y + node.size[1] <= second.bounding[1] + second.bounding[3]
    logger.info("Group geometry clearance test passed")


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


def test_pinned_group_preserves_group_and_member_geometry():
    logger.info("Testing pinned group preservation")
    wf = Workflow()
    group = Group(id=1, name="controls", bounding=[600, 400, 760, 420], pinned=True)
    wf.groups = [group]
    prompt = Node(id=1, type="CLIPTextEncode", x=640, y=460, size=[420, 260], pinned=True)
    sampler = Node(id=2, type="KSampler", x=1080, y=500, size=[320, 340])
    outside = Node(id=3, type="PreviewImage", x=0, y=0, size=[240, 120])
    wf.nodes = {1: prompt, 2: sampler, 3: outside}
    wf.links[10] = Link(id=10, source=1, source_port=0, target=2, target_port=0, type="CONDITIONING")
    prompt.output_links.append(10)
    sampler.input_links.append(10)

    result = apply(wf)

    assert result.groups[0].bounding == [600, 400, 760, 420]
    assert result.nodes[1].x == 640
    assert result.nodes[1].y == 460
    assert result.nodes[1].size == [420, 260]
    assert result.nodes[2].x == 1080
    assert result.nodes[2].y == 500
    assert result.nodes[2].size == [320, 340]
    assert result.nodes[3].x != 0 or result.nodes[3].y != 0
    logger.info("Pinned group preservation test passed")


def test_pinned_ungrouped_node_preserves_position_and_size():
    logger.info("Testing pinned ungrouped node preservation")
    wf = Workflow()
    pinned = Node(id=1, type="PrimitiveNode", x=-300, y=900, size=[520, 380], pinned=True)
    movable = Node(id=2, type="PreviewImage", x=800, y=200, size=[280, 140])
    wf.nodes = {1: pinned, 2: movable}
    wf.links[10] = Link(id=10, source=1, source_port=0, target=2, target_port=0, type="IMAGE")
    pinned.output_links.append(10)
    movable.input_links.append(10)

    result = apply(wf)

    assert result.nodes[1].x == -300
    assert result.nodes[1].y == 900
    assert result.nodes[1].size == [520, 380]
    assert result.nodes[2].x != 800 or result.nodes[2].y != 200
    logger.info("Pinned ungrouped node preservation test passed")


def test_unpinned_nodes_clear_pinned_node_geometry():
    logger.info("Testing unpinned nodes clear pinned node geometry")
    wf = Workflow()
    pinned = Node(id=1, type="Seed (rgthree)", x=400, y=160, size=[260, 180], pinned=True)
    movable = Node(id=2, type="Context (Load Model)", x=400, y=180, size=[260, 260])
    target = Node(id=3, type="KSampler", x=900, y=160, size=[260, 300], input_count=1)
    wf.nodes = {1: pinned, 2: movable, 3: target}
    link = Link(id=10, source=2, source_port=0, target=3, target_port=0, type="MODEL")
    wf.links[link.id] = link
    movable.output_links.append(link.id)
    target.input_links.append(link.id)

    result = apply(wf)
    pinned = result.nodes[1]
    movable = result.nodes[2]

    assert (pinned.x, pinned.y) == (400, 160)
    assert not _test_rectangles_overlap(pinned, movable)
    logger.info("Pinned geometry clearance test passed")


def test_unpinned_nodes_clear_pinned_group_surface():
    logger.info("Testing unpinned nodes clear pinned group surface")
    wf = Workflow()
    pinned_group = Group(id=1, name="Pinned Controls", bounding=[320, 120, 460, 360], pinned=True)
    pinned_member = Node(id=1, type="VAELoader", x=360, y=160, size=[220, 80])
    movable = Node(id=2, type="Final Preview", x=40, y=40, size=[260, 140])
    wf.groups = [pinned_group]
    wf.nodes = {1: pinned_member, 2: movable}

    _assign_groups(wf)
    movable.x = 360
    movable.y = 220

    _separate_unpinned_nodes_from_pinned_geometry(wf, LayoutSettings())
    group = wf.groups[0]
    movable_rect = _test_node_rect(movable)
    group_rect = _test_group_rect(group)

    assert group.bounding == [320, 120, 460, 360]
    assert wf.nodes[1].x == 360
    assert wf.nodes[1].y == 160
    assert not _test_rects_overlap(movable_rect, group_rect)
    logger.info("Pinned group surface clearance test passed")


def test_movable_group_clears_later_sorted_pinned_group():
    logger.info("Testing movable groups clear later-sorted pinned groups")
    wf = Workflow()
    movable_group = Group(id=1, name="Output", bounding=[100, 40, 420, 260])
    pinned_group = Group(id=2, name="Pinned VAE", bounding=[120, 180, 500, 320], pinned=True)
    output = Node(id=1, type="Final Preview", x=140, y=80, size=[240, 120])
    vae = Node(id=2, type="VAELoader", x=160, y=220, size=[220, 80])
    movable_group.nodes = [output]
    pinned_group.nodes = [vae]
    wf.groups = [movable_group, pinned_group]
    wf.nodes = {1: output, 2: vae}

    _resolve_group_geometry_overlaps(wf, LayoutSettings())

    assert pinned_group.bounding == [120, 180, 500, 320]
    assert not _test_rectangles_overlap_groups(movable_group, pinned_group)
    assert output.y > 80
    logger.info("Later-sorted pinned group clearance test passed")


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


def test_linked_ungrouped_nodes_clear_group_columns():
    logger.info("Testing linked ungrouped placement clears group extent")
    wf = Workflow()
    # A group with two internal nodes that get laid out into a column.
    group = Group(id=1, name="loader", bounding=[100, 100, 600, 400])
    wf.groups = [group]
    loader = Node(id=1, type="Loader", x=120, y=120, size=[200, 80])
    refiner = Node(id=2, type="Refiner", x=380, y=120, size=[200, 80])
    wf.nodes[1] = loader
    wf.nodes[2] = refiner
    wf.links[10] = Link(id=10, source=1, source_port=0, target=2, target_port=0, type="MODEL")
    loader.output_links.append(10)
    refiner.input_links.append(10)

    # Linked ungrouped nodes — _has_internal_links() will be true.
    src = Node(id=3, type="Source", x=0, y=0, size=[200, 80])
    dst = Node(id=4, type="Sink", x=0, y=0, size=[200, 80])
    wf.nodes[3] = src
    wf.nodes[4] = dst
    wf.links[11] = Link(id=11, source=3, source_port=0, target=4, target_port=0, type="DATA")
    src.output_links.append(11)
    dst.input_links.append(11)

    result = apply(wf)

    group_right = result.groups[0].bounding[0] + result.groups[0].bounding[2]
    for node_id in (3, 4):
        node = result.nodes[node_id]
        assert node.x >= group_right, (
            f"ungrouped node {node_id} at x={node.x} overlaps group extent x<{group_right}"
        )
    logger.info("Linked ungrouped placement test passed")


def test_linked_ungrouped_nodes_use_per_layer_widths():
    logger.info("Testing linked ungrouped placement uses per-layer widths")
    wf = Workflow()
    source = Node(id=1, type="Wide Source", x=0, y=0, size=[900, 120], output_links=[10])
    middle = Node(id=2, type="Middle", x=0, y=0, size=[220, 100], input_links=[10], output_links=[11])
    target = Node(id=3, type="Target", x=0, y=0, size=[240, 100], input_links=[11])
    wf.nodes = {1: source, 2: middle, 3: target}
    wf.links = {
        10: Link(id=10, source=1, source_port=0, target=2, target_port=0, type="DATA"),
        11: Link(id=11, source=2, source_port=0, target=3, target_port=0, type="DATA"),
    }

    _position_linked_ungrouped_nodes(wf, [source, middle, target], LayoutSettings(50, 50), start_x=100)

    assert middle.x - source.x == source.size[0] + 50
    assert target.x - middle.x == middle.size[0] + 50
    logger.info("Linked ungrouped per-layer width test passed")


def test_external_ungrouped_source_moves_next_to_target_group():
    logger.info("Testing external source nodes anchor near target groups")
    wf = Workflow()
    source = Node(id=1, type="LoadImage", x=2400, y=0, size=[320, 260], output_links=[10])
    target = Node(id=2, type="VAEEncode", x=100, y=100, size=[260, 120], input_links=[10])
    group = Group(id=1, name="Encode", bounding=[60, 60, 420, 260])
    wf.nodes = {1: source, 2: target}
    wf.links = {10: Link(id=10, source=1, source_port=0, target=2, target_port=0, type="IMAGE")}
    wf.groups = [group]

    result = apply(wf, LayoutSettings())
    source = result.nodes[1]
    group = result.groups[0]

    assert source.x + source.size[0] < group.bounding[0]
    assert result.links[10].target == target.id
    logger.info("External source group anchoring test passed")


def test_primitive_control_nodes_stay_near_single_consumer():
    logger.info("Testing primitive control nodes stay near their consumer")
    wf = Workflow()
    sampler = Node(id=1, type="KSampler", x=900, y=400, size=[260, 300], input_count=8)
    seed = Node(id=2, type="Seed (rgthree)", x=-700, y=1200, size=[220, 120])
    cfg = Node(id=3, type="PrimitiveFloat", x=-500, y=1500, size=[180, 80])
    loader = Node(id=4, type="VAELoader", x=-900, y=100, size=[220, 80])
    wf.nodes = {1: sampler, 2: seed, 3: cfg, 4: loader}

    links = [
        Link(id=10, source=2, source_port=0, target=1, target_port=6, type="SEED"),
        Link(id=11, source=3, source_port=0, target=1, target_port=5, type="FLOAT"),
        Link(id=12, source=4, source_port=0, target=1, target_port=4, type="VAE"),
    ]
    for link in links:
        wf.links[link.id] = link
        wf.nodes[link.source].output_links.append(link.id)
        wf.nodes[link.target].input_links.append(link.id)

    result = apply(wf)
    sampler = result.nodes[1]
    seed = result.nodes[2]
    cfg = result.nodes[3]
    loader = result.nodes[4]

    assert seed.x < sampler.x
    assert cfg.x < sampler.x
    assert sampler.x - (seed.x + seed.size[0]) <= 80
    assert sampler.x - (cfg.x + cfg.size[0]) <= 80
    assert abs((seed.y + seed.size[1] / 2) - _node_input_port_y(sampler, 6)) < sampler.size[1]
    assert abs((cfg.y + cfg.size[1] / 2) - _node_input_port_y(sampler, 5)) < sampler.size[1]
    assert loader.x < sampler.x
    logger.info("Primitive control node proximity test passed")


def test_multi_output_control_nodes_stay_near_sampler_cluster():
    logger.info("Testing multi-output primitive controls stay near sampler clusters")
    wf = Workflow()
    seed = Node(id=1, type="PrimitiveInt", x=-900, y=2000, size=[280, 100])
    sampler_a = Node(id=2, type="SamplerCustom", x=900, y=100, size=[260, 360], input_count=8)
    sampler_b = Node(id=3, type="KSamplerAdvanced", x=1200, y=620, size=[300, 340], input_count=8)
    wf.nodes = {1: seed, 2: sampler_a, 3: sampler_b}

    links = [
        Link(id=10, source=1, source_port=0, target=2, target_port=7, type="INT"),
        Link(id=11, source=1, source_port=0, target=3, target_port=5, type="INT"),
    ]
    for link in links:
        wf.links[link.id] = link
        wf.nodes[link.source].output_links.append(link.id)
        wf.nodes[link.target].input_links.append(link.id)

    result = apply(wf)
    seed = result.nodes[1]
    sampler_a = result.nodes[2]
    sampler_b = result.nodes[3]

    sampler_left = min(sampler_a.x, sampler_b.x)
    sampler_port_mid = (
        _node_input_port_y(sampler_a, 7) + _node_input_port_y(sampler_b, 5)
    ) / 2

    assert seed.x < sampler_left
    assert sampler_left - (seed.x + seed.size[0]) <= 80
    assert abs((seed.y + seed.size[1] / 2) - sampler_port_mid) < 180
    logger.info("Multi-output control node proximity test passed")


def test_control_stack_avoids_existing_nodes_near_sampler():
    logger.info("Testing control stack avoids existing nodes near sampler")
    wf = Workflow()
    sampler = Node(id=1, type="KSampler", x=900, y=220, size=[260, 300], input_count=8)
    seed = Node(id=2, type="Seed (rgthree)", x=-700, y=1200, size=[220, 120])
    context = Node(id=3, type="Context (Load Model)", x=640, y=250, size=[260, 260], pinned=True)
    wf.nodes = {1: sampler, 2: seed, 3: context}

    link = Link(id=10, source=2, source_port=0, target=1, target_port=6, type="SEED")
    wf.links[link.id] = link
    seed.output_links.append(link.id)
    sampler.input_links.append(link.id)

    result = apply(wf)
    seed = result.nodes[2]
    context = result.nodes[3]
    sampler = result.nodes[1]

    assert _test_node_is_local_to_endpoint(seed, sampler)
    assert not _test_rectangles_overlap(seed, context)
    logger.info("Control stack collision avoidance test passed")


def test_control_nodes_can_anchor_to_pinned_target_group_edge():
    logger.info("Testing controls anchor outside pinned target groups")
    wf = Workflow()
    seed = Node(id=1, type="PrimitiveInt", x=-900, y=1400, size=[280, 100])
    sampler = Node(id=2, type="KSamplerAdvanced", x=900, y=220, size=[300, 340], input_count=8)
    wf.nodes = {1: seed, 2: sampler}
    wf.groups = [Group(id=1, name="Sampler Control", bounding=[820, 160, 520, 500], pinned=True)]

    link = Link(id=10, source=1, source_port=0, target=2, target_port=5, type="INT")
    wf.links[link.id] = link
    seed.output_links.append(link.id)
    sampler.input_links.append(link.id)

    result = apply(wf)
    seed = result.nodes[1]
    sampler = result.nodes[2]
    group = result.groups[0]

    assert group.bounding == [820, 160, 520, 500]
    assert (sampler.x, sampler.y) == (900, 220)
    assert seed.x < group.bounding[0]
    assert group.bounding[0] - (seed.x + seed.size[0]) <= 80
    logger.info("Pinned target group control anchor test passed")


def test_empty_latent_image_stays_near_sampler_input():
    logger.info("Testing EmptyLatentImage stays near sampler latent input")
    wf = Workflow()
    latent = Node(id=1, type="EmptyLatentImage", x=-900, y=1600, size=[220, 120])
    sampler = Node(id=2, type="KSampler", x=900, y=260, size=[260, 300], input_count=4)
    wf.nodes = {1: latent, 2: sampler}

    link = Link(id=10, source=1, source_port=0, target=2, target_port=3, type="LATENT")
    wf.links[link.id] = link
    latent.output_links.append(link.id)
    sampler.input_links.append(link.id)

    result = apply(wf)
    latent = result.nodes[1]
    sampler = result.nodes[2]

    assert latent.x < sampler.x
    assert sampler.x - (latent.x + latent.size[0]) <= 80
    assert abs((latent.y + latent.size[1] / 2) - _node_input_port_y(sampler, 3)) < sampler.size[1]
    logger.info("EmptyLatentImage sampler proximity test passed")


def test_grouped_control_node_does_not_stretch_group_to_external_sampler():
    logger.info("Testing grouped control nodes stay in their source group")
    wf = Workflow()
    load_group = Group(id=1, name="LOAD", bounding=[0, 0, 320, 260])
    sampler_group = Group(id=2, name="GO", bounding=[700, 0, 360, 420])
    latent = Node(id=1, type="EmptyLatentImageCustom", x=40, y=60, size=[220, 120])
    sampler = Node(id=2, type="KSampler", x=760, y=80, size=[260, 320], input_count=4)
    wf.nodes = {1: latent, 2: sampler}
    wf.groups = [load_group, sampler_group]
    link = Link(id=10, source=latent.id, source_port=0, target=sampler.id, target_port=3, type="LATENT")
    wf.links[link.id] = link
    latent.output_links.append(link.id)
    sampler.input_links.append(link.id)

    result = apply(wf)
    latent = result.nodes[1]
    load_group = next(group for group in result.groups if group.name == "LOAD")

    assert latent in load_group.nodes
    assert latent.x + latent.size[0] <= load_group.bounding[0] + load_group.bounding[2]
    assert load_group.bounding[2] < 500
    logger.info("Grouped control node source-group test passed")


def test_virtual_hubs_shift_sideways_to_avoid_covering_nodes():
    logger.info("Testing virtual Set/Get hubs avoid covering existing nodes")
    wf = Workflow()
    source = Node(id=1, type="UNETLoader", x=100, y=100, size=[200, 100], pinned=True)
    target = Node(id=2, type="KSampler", x=900, y=100, size=[260, 300], input_count=1, pinned=True)
    set_node = Node(id=3, type="SetNode", x=0, y=0, size=[200, 60])
    get_node = Node(id=4, type="GetNode", x=0, y=0, size=[200, 60])
    set_blocker = Node(id=5, type="CLIPSetLastLayer", x=340, y=100, size=[200, 60], pinned=True)
    get_blocker = Node(id=6, type="CLIPTextEncode", x=660, y=100, size=[200, 80], pinned=True)
    wf.nodes = {
        1: source,
        2: target,
        3: set_node,
        4: get_node,
        5: set_blocker,
        6: get_blocker,
    }

    links = [
        Link(id=10, source=1, source_port=0, target=3, target_port=0, type="MODEL"),
        Link(id=11, source=4, source_port=0, target=2, target_port=0, type="MODEL"),
    ]
    for link in links:
        wf.links[link.id] = link
        wf.nodes[link.source].output_links.append(link.id)
        wf.nodes[link.target].input_links.append(link.id)

    result = apply(wf, LayoutSettings(node_x_distance=80, node_y_distance=80))
    set_node = result.nodes[3]
    get_node = result.nodes[4]
    set_blocker = result.nodes[5]
    get_blocker = result.nodes[6]

    assert not _test_rectangles_overlap(set_node, set_blocker)
    assert not _test_rectangles_overlap(get_node, get_blocker)
    assert _test_node_is_local_to_endpoint(set_node, result.nodes[1])
    assert _test_node_is_local_to_endpoint(get_node, result.nodes[2])
    logger.info("Virtual hub overlap avoidance test passed")


def test_virtual_set_hub_uses_vertical_slot_before_far_horizontal_shift():
    logger.info("Testing virtual Set hubs prefer local vertical fallback slots")
    wf = Workflow()
    source = Node(
        id=1,
        type="Power Lora Loader",
        x=100,
        y=200,
        size=[200, 120],
        output_count=1,
        pinned=True,
    )
    set_node = Node(id=2, type="SetNode", x=1800, y=200, size=[200, 60])
    blockers = [
        Node(id=3, type="CLIPSetLastLayer", x=340, y=200, size=[200, 60], pinned=True),
        Node(id=4, type="CLIPSetLastLayer", x=100, y=100, size=[200, 60], pinned=True),
        Node(id=5, type="CLIPSetLastLayer", x=100, y=360, size=[200, 60], pinned=True),
    ]
    wf.nodes = {1: source, 2: set_node, **{node.id: node for node in blockers}}
    link = Link(id=10, source=source.id, source_port=0, target=set_node.id, target_port=0, type="MODEL")
    wf.links[link.id] = link
    source.output_links.append(link.id)
    set_node.input_links.append(link.id)

    result = apply(wf, LayoutSettings(node_x_distance=80, node_y_distance=80))
    set_node = result.nodes[2]
    source = result.nodes[1]

    assert set_node.x - (source.x + source.size[0]) <= VIRTUAL_HUB_MAX_GAP
    assert set_node.x < 600
    assert set_node.y != 200
    for blocker in blockers:
        assert not _test_rectangles_overlap(set_node, result.nodes[blocker.id])
    logger.info("Virtual Set hub local vertical fallback test passed")


def test_virtual_hubs_escape_pinned_group_when_endpoint_is_elsewhere():
    logger.info("Testing virtual hubs inside pinned groups still follow their endpoint")
    wf = Workflow()
    get_node = Node(id=1, type="GetNode", x=120, y=130, size=[200, 60])
    loader = Node(id=2, type="UNETLoaderGGUF", x=160, y=90, size=[220, 80], pinned=True)
    target = Node(id=3, type="Power Lora Loader", x=980, y=120, size=[260, 140], input_count=1)
    pinned_group = Group(id=1, name="Load Model", bounding=[80, 60, 420, 260], pinned=True)
    wf.nodes = {1: get_node, 2: loader, 3: target}
    wf.groups = [pinned_group]

    link = Link(id=10, source=get_node.id, source_port=0, target=target.id, target_port=0, type="MODEL")
    wf.links[link.id] = link
    get_node.output_links.append(link.id)
    target.input_links.append(link.id)

    result = apply(wf, LayoutSettings(node_x_distance=80, node_y_distance=80))
    get_node = result.nodes[1]
    loader = result.nodes[2]
    target = result.nodes[3]
    pinned_group = result.groups[0]

    assert pinned_group.bounding == [80, 60, 420, 260]
    assert (loader.x, loader.y) == (160, 90)
    assert _test_node_is_local_to_endpoint(get_node, target)
    assert get_node not in pinned_group.nodes
    logger.info("Pinned-group virtual hub escape test passed")


def test_virtual_hubs_do_not_influence_regular_group_layout():
    logger.info("Testing virtual hubs are excluded from regular group layout")
    wf = Workflow()
    source = Node(id=1, type="UNETLoaderGGUF", x=120, y=120, size=[220, 80])
    target = Node(id=2, type="Power Lora Loader", x=900, y=120, size=[260, 140], input_count=1)
    get_node = Node(id=3, type="GetNode", x=130, y=320, size=[200, 60])
    group = Group(id=1, name="Load Model", bounding=[80, 80, 420, 420])
    wf.nodes = {1: source, 2: target, 3: get_node}
    wf.groups = [group]

    link = Link(id=10, source=get_node.id, source_port=0, target=target.id, target_port=0, type="MODEL")
    wf.links[link.id] = link
    get_node.output_links.append(link.id)
    target.input_links.append(link.id)

    result = apply(wf, LayoutSettings(node_x_distance=80, node_y_distance=80))
    group = result.groups[0]
    get_node = result.nodes[3]
    target = result.nodes[2]

    assert source in group.nodes
    assert get_node not in group.nodes
    assert target.x - (get_node.x + get_node.size[0]) <= VIRTUAL_HUB_MAX_GAP
    logger.info("Virtual hub group-layout exclusion test passed")


def test_pinned_virtual_hub_still_follows_endpoint():
    logger.info("Testing pinned virtual hubs still follow their endpoint")
    wf = Workflow()
    get_node = Node(id=1, type="GetNode", x=120, y=130, size=[200, 60], pinned=True)
    target = Node(id=2, type="KSampler", x=980, y=120, size=[260, 220], input_count=1)
    wf.nodes = {1: get_node, 2: target}

    link = Link(id=10, source=get_node.id, source_port=0, target=target.id, target_port=0, type="MODEL")
    wf.links[link.id] = link
    get_node.output_links.append(link.id)
    target.input_links.append(link.id)

    result = apply(wf, LayoutSettings(node_x_distance=80, node_y_distance=80))
    get_node = result.nodes[1]
    target = result.nodes[2]

    assert get_node.x < target.x
    assert target.x - (get_node.x + get_node.size[0]) <= VIRTUAL_HUB_MAX_GAP
    logger.info("Pinned virtual hub endpoint-following test passed")


def test_virtual_set_get_hubs_stay_near_physical_endpoints():
    logger.info("Testing virtual Set/Get hub endpoint anchoring")
    wf = Workflow()
    source = Node(id=1, type="UNETLoader", x=0, y=0, size=[200, 60])
    wf.nodes[1] = source

    consumers = []
    for index in range(3):
        consumer = Node(
            id=2 + index,
            type="KSampler",
            x=900 + index * 220,
            y=400 + index * 260,
            size=[200, 100],
            input_count=2,
        )
        consumers.append(consumer)
        wf.nodes[consumer.id] = consumer
        link = Link(
            id=100 + index,
            source=1,
            source_port=0,
            target=consumer.id,
            target_port=1,
            type="MODEL",
        )
        wf.links[link.id] = link
        source.output_links.append(link.id)
        consumer.input_links.append(link.id)

    result = apply(optimize(wf), LayoutSettings(node_x_distance=80, node_y_distance=80))
    set_node = next(node for node in result.nodes.values() if node.type == "SetNode")
    get_nodes = [node for node in result.nodes.values() if node.type == "GetNode"]
    laid_out_source = result.nodes[1]

    set_link = result.links[set_node.input_links[0]]
    set_is_local = (
        VIRTUAL_HUB_MIN_GAP
        <= set_node.x - (laid_out_source.x + laid_out_source.size[0])
        <= VIRTUAL_HUB_MAX_GAP
    ) or abs((set_node.x + set_node.size[0] / 2) - (laid_out_source.x + laid_out_source.size[0] / 2)) <= laid_out_source.size[0] / 2
    assert set_is_local
    if _test_vertical_spans_overlap(set_node, laid_out_source) and set_node.x > laid_out_source.x:
        assert _node_input_port_y(set_node, set_link.target_port) == pytest.approx(
            _node_output_port_y(laid_out_source, set_link.source_port)
        )

    for get_node in get_nodes:
        link = result.links[get_node.output_links[0]]
        target = result.nodes[link.target]
        get_is_local = (
            VIRTUAL_HUB_MIN_GAP
            <= target.x - (get_node.x + get_node.size[0])
            <= VIRTUAL_HUB_MAX_GAP
        ) or abs((get_node.x + get_node.size[0] / 2) - (target.x + target.size[0] / 2)) <= target.size[0] / 2
        assert get_is_local
        if _test_vertical_spans_overlap(get_node, target) and get_node.x < target.x:
            assert _node_output_port_y(get_node, link.source_port) == pytest.approx(
                _node_input_port_y(target, link.target_port)
            )

    logger.info("Virtual Set/Get hub endpoint anchoring test passed")


def test_virtual_hubs_follow_endpoint_group_membership():
    logger.info("Testing virtual Set/Get hub group membership follows endpoints")
    wf = Workflow()
    source = Node(id=1, type="Power Lora Loader", x=100, y=100, size=[200, 100], output_count=1)
    target = Node(id=2, type="VAEDecode", x=900, y=120, size=[200, 100], input_count=2)
    group = Group(id=1, name="loaders", bounding=[0, 0, 500, 400], nodes=[source])
    wf.nodes = {1: source, 2: target}
    wf.groups = [group]
    wf.ungrouped_nodes = [target]
    for index in range(2):
        consumer = target if index == 0 else Node(id=3, type="PreviewImage", x=900, y=420, size=[200, 80], input_count=1)
        if consumer.id not in wf.nodes:
            wf.nodes[consumer.id] = consumer
            wf.ungrouped_nodes.append(consumer)
        link = Link(
            id=100 + index,
            source=1,
            source_port=0,
            target=consumer.id,
            target_port=1 if consumer.id == target.id else 0,
            type="VAE",
        )
        wf.links[link.id] = link
        source.output_links.append(link.id)
        consumer.input_links.append(link.id)

    result = apply(optimize(wf), LayoutSettings(node_x_distance=80, node_y_distance=80))
    loader_group = next(group for group in result.groups if group.name == "loaders")
    set_node = next(node for node in result.nodes.values() if node.type == "SetNode")
    get_nodes = [node for node in result.nodes.values() if node.type == "GetNode"]

    assert set_node in loader_group.nodes
    assert all(get_node not in loader_group.nodes for get_node in get_nodes)
    assert all(get_node in result.ungrouped_nodes for get_node in get_nodes)

    logger.info("Virtual Set/Get hub endpoint group membership test passed")


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


def _test_node_is_local_to_endpoint(node: Node, endpoint: Node) -> bool:
    side_gap = min(
        abs(node.x - (endpoint.x + endpoint.size[0])),
        abs(endpoint.x - (node.x + node.size[0])),
    )
    horizontal_overlap = not (
        node.x + node.size[0] < endpoint.x
        or endpoint.x + endpoint.size[0] < node.x
    )
    vertical_gap = min(
        abs(node.y - (endpoint.y + endpoint.size[1])),
        abs(endpoint.y - (node.y + node.size[1])),
    )
    vertical_overlap = _test_vertical_spans_overlap(node, endpoint)
    return (
        (side_gap <= VIRTUAL_HUB_MAX_GAP and vertical_overlap)
        or (vertical_gap <= VIRTUAL_HUB_MAX_GAP and horizontal_overlap)
    )


def _test_vertical_spans_overlap(a: Node, b: Node) -> bool:
    return not (a.y + a.size[1] < b.y or b.y + b.size[1] < a.y)


def _test_rectangles_overlap(a: Node, b: Node) -> bool:
    return not (
        a.x + a.size[0] <= b.x
        or b.x + b.size[0] <= a.x
        or a.y + a.size[1] <= b.y
        or b.y + b.size[1] <= a.y
    )


def _test_rectangles_overlap_groups(a: Group, b: Group) -> bool:
    return not (
        a.bounding[0] + a.bounding[2] <= b.bounding[0]
        or b.bounding[0] + b.bounding[2] <= a.bounding[0]
        or a.bounding[1] + a.bounding[3] <= b.bounding[1]
        or b.bounding[1] + b.bounding[3] <= a.bounding[1]
    )


def _test_node_rect(node: Node) -> tuple[float, float, float, float]:
    return (node.x, node.y, node.x + node.size[0], node.y + node.size[1])


def _test_group_rect(group: Group) -> tuple[float, float, float, float]:
    return (
        group.bounding[0],
        group.bounding[1],
        group.bounding[0] + group.bounding[2],
        group.bounding[1] + group.bounding[3],
    )


def _test_rects_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
