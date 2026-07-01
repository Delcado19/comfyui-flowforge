# Top-level Dataflow-Column Row Wrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `apply_best_layout` from producing x-axis-dominant workflows by wrapping the two dataflow-column placement paths (connected groups, linked ungrouped nodes) into boustrophedon rows once they get too wide, instead of growing width unbounded.

**Architecture:** One new pure helper (`_wrap_layer_columns`) replaces the unbounded `current_x` increment in `_position_groups_by_flow_layers` and `_node_layer_x_positions`. A new `wrap_columns` flag on `LayoutSettings` gates it, and `_build_layout_candidates` tries one wrapped candidate alongside the existing scale-profile candidates so the existing scorer (`_score_layout_candidate`) picks whichever geometry is actually better — no fixed always-on switch.

**Tech Stack:** Python 3, pytest, `flowforge/layout.py` (pure functions, no I/O), `tests/test_layout.py`.

## Global Constraints

- No changes to `Workflow`/`Node`/`Group` models (`flowforge/model.py`) — only `pos`/`bounding` fields are ever written by layout code, and that stays true.
- `LayoutSettings.wrap_columns` is internal-only: do not add it to `LayoutSettings.from_payload` — it is a candidate-search strategy, not a user-facing spacing knob.
- Annotation/label/note nodes (anything not linked into the dataflow graph) are explicitly out of scope for this change — do not add logic that considers their size or position.
- Every new/changed function needs a one-line docstring or comment explaining *why*, per this repo's `AGENTS.md` documentation policy, not what (identifiers already say what).
- Spec: `docs/superpowers/specs/2026-07-01-toplevel-row-wrap-design.md` (committed `d904b92`, corrected after prototyping caught a wrong Qwen root-cause claim — read it for full background before starting).

---

### Task 1: Shared wrap helper, row-width cap, and settings flag

**Files:**
- Modify: `flowforge/layout.py` (constants block near line 61, `LayoutSettings` class near line 72, new functions inserted immediately before `_clamp_layout_distance` near line 3246)
- Test: `tests/test_layout.py`

**Interfaces:**
- Produces: `_wrap_layer_columns(layer_sizes: dict[int, tuple[float, float]], row_width_cap: float, base_x: float, base_y: float, h_gap: float, v_gap: float) -> dict[int, tuple[float, float]]` — pure function, maps layer index to final `(x, y)`.
- Produces: `_toplevel_wrap_row_width_cap(layer_sizes: dict[int, tuple[float, float]]) -> float` — soft width cap for a set of layer sizes.
- Produces: `TOPLEVEL_WRAP_TARGET_ASPECT_RATIO` module constant (`2.8`).
- Produces: `LayoutSettings.wrap_columns: bool` field, default `False`.
- Consumes: existing `LAYOUT_GROUP_ROW_MAX_AVERAGE_WIDTHS` constant (module-level, already defined at line 57).

- [ ] **Step 1: Write the failing unit test for `_wrap_layer_columns`**

Add to `tests/test_layout.py`, after `test_layer_crossing_minimization_uses_adjacent_layer_order` (ends at line 465):

```python
def test_wrap_layer_columns_wraps_and_alternates_direction():
    logger.info("Testing shared layer-column wrap helper")
    layer_sizes = {
        0: (300.0, 100.0),
        1: (300.0, 100.0),
        2: (300.0, 100.0),
        3: (300.0, 100.0),
    }
    # Row cap fits exactly 2 layers per row: 300 + 50 (gap) + 300 = 650 <= 700,
    # a third layer would push it to 1000 > 700.
    positions = _wrap_layer_columns(
        layer_sizes, row_width_cap=700.0, base_x=0.0, base_y=0.0, h_gap=50.0, v_gap=20.0
    )

    # Row 0 (even index): left-to-right in natural layer order.
    assert positions[0] == (0.0, 0.0)
    assert positions[1] == (350.0, 0.0)
    # Row 1 (odd index): right-to-left, so layer 2 (continues the dataflow
    # from row 0's last layer) lands at the row's right edge, adjacent to
    # where layer 1 ended, instead of a long diagonal cable back to the left.
    assert positions[2] == (350.0, 120.0)
    assert positions[3] == (0.0, 120.0)
    logger.info("Layer-column wrap helper test passed")


def test_wrap_layer_columns_single_row_when_cap_not_exceeded():
    logger.info("Testing layer-column wrap helper stays single-row under cap")
    layer_sizes = {0: (300.0, 100.0), 1: (300.0, 100.0)}

    positions = _wrap_layer_columns(
        layer_sizes, row_width_cap=math.inf, base_x=10.0, base_y=10.0, h_gap=50.0, v_gap=20.0
    )

    assert positions == {0: (10.0, 10.0), 1: (360.0, 10.0)}
    logger.info("Layer-column wrap helper single-row test passed")


def test_wrap_layer_columns_handles_empty_input():
    logger.info("Testing layer-column wrap helper with no layers")
    assert _wrap_layer_columns({}, 500.0, 0.0, 0.0, 50.0, 20.0) == {}
    logger.info("Layer-column wrap helper empty-input test passed")
```

- [ ] **Step 2: Add the required imports**

In `tests/test_layout.py`, add `math` import and add `_wrap_layer_columns` to the existing `from flowforge.layout import (...)` block (starts at line 10):

```python
import math
```

(add this line near the top with the other imports, e.g. right after `from copy import deepcopy` at line 5)

Add `_wrap_layer_columns,` to the `from flowforge.layout import (` block (alphabetical position does not matter, this codebase's import block is not sorted — add it next to `_update_bounding_boxes` at line 34).

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_layout.py -k wrap_layer_columns -v`
Expected: FAIL with `ImportError: cannot import name '_wrap_layer_columns'`

- [ ] **Step 4: Add the constant**

In `flowforge/layout.py`, find this block (near line 58-61):

```python
GROUP_INTERNAL_WRAP_MIN_LAYERS = 5
GROUP_INTERNAL_WRAP_TARGET_WIDTH = 900.0
GROUP_INTERNAL_WRAP_ASPECT_RATIO = 2.8
```

Change to:

```python
GROUP_INTERNAL_WRAP_MIN_LAYERS = 5
GROUP_INTERNAL_WRAP_TARGET_WIDTH = 900.0
GROUP_INTERNAL_WRAP_ASPECT_RATIO = 2.8
TOPLEVEL_WRAP_TARGET_ASPECT_RATIO = 2.8
```

- [ ] **Step 5: Add the `wrap_columns` field to `LayoutSettings`**

Find (near line 72-78):

```python
    node_x_distance: float = DEFAULT_NODE_X_DISTANCE
    node_y_distance: float = DEFAULT_NODE_Y_DISTANCE

    def __post_init__(self) -> None:
```

Change to:

```python
    node_x_distance: float = DEFAULT_NODE_X_DISTANCE
    node_y_distance: float = DEFAULT_NODE_Y_DISTANCE
    wrap_columns: bool = False

    def __post_init__(self) -> None:
```

- [ ] **Step 6: Add the two new functions**

Find `_clamp_layout_distance` (near line 3246):

```python
def _clamp_layout_distance(value: float) -> float:
```

Insert immediately before it:

```python
def _toplevel_wrap_row_width_cap(layer_sizes: dict[int, tuple[float, float]]) -> float:
    """Soft width cap so unbounded dataflow columns wrap downward, mirroring
    the existing _estimate_group_row_width heuristic for disconnected groups."""
    if not layer_sizes:
        return 0.0

    widths = [width for width, _height in layer_sizes.values()]
    total_area = sum(width * height for width, height in layer_sizes.values())
    max_width = max(widths)
    average_width = sum(widths) / len(widths)
    width_from_area = math.sqrt(total_area * TOPLEVEL_WRAP_TARGET_ASPECT_RATIO)
    width_cap = max_width + average_width * LAYOUT_GROUP_ROW_MAX_AVERAGE_WIDTHS
    return max(max_width, min(width_from_area, width_cap))


def _wrap_layer_columns(
    layer_sizes: dict[int, tuple[float, float]],
    row_width_cap: float,
    base_x: float,
    base_y: float,
    h_gap: float,
    v_gap: float,
) -> dict[int, tuple[float, float]]:
    """Pack dataflow layer columns into boustrophedon rows once a row would
    exceed row_width_cap.

    Layers here have real data links to their neighbouring layer (unlike the
    group-internal or disconnected-group wraps, which reset to the left
    margin on every new row). Resetting here would draw a long diagonal
    cable from the last item of one row back to the first item of the next,
    so alternate rows fill right-to-left instead, keeping the layer that
    ends one row physically adjacent to the layer that starts the next.
    """
    positions: dict[int, tuple[float, float]] = {}
    if not layer_sizes:
        return positions

    rows: list[list[int]] = [[]]
    row_width = 0.0
    for layer in sorted(layer_sizes):
        width, _height = layer_sizes[layer]
        addition = width if not rows[-1] else width + h_gap
        if rows[-1] and row_width + addition > row_width_cap:
            rows.append([])
            row_width = 0.0
            addition = width
        rows[-1].append(layer)
        row_width += addition

    current_y = base_y
    for row_index, row_layers in enumerate(rows):
        ordered_row = list(reversed(row_layers)) if row_index % 2 == 1 else row_layers
        current_x = base_x
        for layer in ordered_row:
            width, _height = layer_sizes[layer]
            positions[layer] = (current_x, current_y)
            current_x += width + h_gap
        row_height = max(layer_sizes[layer][1] for layer in row_layers)
        current_y += row_height + v_gap

    return positions


```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_layout.py -k wrap_layer_columns -v`
Expected: `3 passed`

- [ ] **Step 8: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/ -q --ignore=tests/test_example_workflows.py`
Expected: all tests pass (the `test_example_workflows.py` corpus test depends on a local, gitignored workflow corpus and a pre-existing, unrelated file-count assertion — ignore it for this step; it is revisited in Task 4's full-suite run for completeness, not because this task could affect it).

- [ ] **Step 9: Commit**

```bash
git add flowforge/layout.py tests/test_layout.py
git commit -m "Add shared boustrophedon row-wrap helper for dataflow columns"
```

---

### Task 2: Wrap connected-group flow columns

**Files:**
- Modify: `flowforge/layout.py`, function `_position_groups_by_flow_layers` (near line 1156)
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: `_wrap_layer_columns(...)`, `_toplevel_wrap_row_width_cap(...)`, `LayoutSettings.wrap_columns` from Task 1.
- No new public names produced; `_position_groups_by_flow_layers`'s external behavior (signature, side effects on `group.bounding` and node `x`/`y`) is unchanged when `wrap_columns=False` (the default).

- [ ] **Step 1: Write the failing integration test**

Add to `tests/test_layout.py`, right after `test_connected_groups_use_flow_columns` (ends at line 594):

```python
def test_connected_group_columns_wrap_downward_after_soft_width():
    logger.info("Testing connected group flow-columns wrap downward")
    wf = Workflow()
    nodes = [
        Node(id=index, type="Node", x=0, y=0, size=[260, 120])
        for index in range(1, 7)
    ]
    wf.nodes = {node.id: node for node in nodes}
    wf.links = {}
    for index in range(1, 6):
        link = Link(id=index, source=index, source_port=0, target=index + 1, target_port=0, type="DATA")
        wf.links[index] = link
        wf.nodes[index].output_links.append(index)
        wf.nodes[index + 1].input_links.append(index)
    wf.groups = [
        Group(id=index, name=f"group-{index}", bounding=[0, 0, 420, 220], nodes=[wf.nodes[index]])
        for index in range(1, 7)
    ]

    settings = LayoutSettings(50, 50, wrap_columns=True)
    _position_groups_globally(wf, settings)

    row_tops = {round(group.bounding[1], 3) for group in wf.groups}
    layout_left = min(group.bounding[0] for group in wf.groups)
    layout_right = max(group.bounding[0] + group.bounding[2] for group in wf.groups)
    total_group_width = sum(group.bounding[2] for group in wf.groups)

    assert len(row_tops) > 1, "expected the connected group chain to wrap into more than one row"
    assert layout_right - layout_left < total_group_width, "expected wrapping to bound width below the unwrapped sum"
    logger.info("Connected group flow-column wrap test passed")


def test_connected_group_columns_stay_single_row_without_wrap_flag():
    logger.info("Testing connected group flow-columns stay unwrapped by default")
    wf = Workflow()
    nodes = [
        Node(id=index, type="Node", x=0, y=0, size=[260, 120])
        for index in range(1, 7)
    ]
    wf.nodes = {node.id: node for node in nodes}
    wf.links = {}
    for index in range(1, 6):
        link = Link(id=index, source=index, source_port=0, target=index + 1, target_port=0, type="DATA")
        wf.links[index] = link
        wf.nodes[index].output_links.append(index)
        wf.nodes[index + 1].input_links.append(index)
    wf.groups = [
        Group(id=index, name=f"group-{index}", bounding=[0, 0, 420, 220], nodes=[wf.nodes[index]])
        for index in range(1, 7)
    ]

    _position_groups_globally(wf, LayoutSettings(50, 50))

    row_tops = {round(group.bounding[1], 3) for group in wf.groups}
    assert len(row_tops) == 1, "default settings (wrap_columns=False) must stay single-row"
    logger.info("Connected group flow-column default-unwrapped test passed")
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_layout.py -k connected_group_columns -v`
Expected: `test_connected_group_columns_wrap_downward_after_soft_width` FAILS (`len(row_tops) > 1` is false — everything is still on one row), `test_connected_group_columns_stay_single_row_without_wrap_flag` PASSES already (no behavior change yet, this one documents current behavior as a baseline).

- [ ] **Step 3: Wire the helper into `_position_groups_by_flow_layers`**

Find (near line 1156-1204):

```python
def _position_groups_by_flow_layers(
    workflow: Workflow,
    settings: LayoutSettings,
    start_x: float,
) -> None:
    """Place connected groups in dataflow columns to shorten cross-group wires."""
    group_sizes = {
        group.id: _required_group_size(group, settings)
        for group in workflow.groups
        if group.nodes and not group.pinned
    }
    if not group_sizes:
        return

    layers = _group_flow_layers(workflow)
    layer_to_groups: dict[int, list[Group]] = {}
    for group in workflow.groups:
        if group.id in group_sizes:
            layer_to_groups.setdefault(layers.get(group.id, 0), []).append(group)

    layer_x_positions: dict[int, float] = {}
    current_x = start_x
    flow_gap = _flow_group_h_gap(workflow, settings)
    for layer in sorted(layer_to_groups):
        layer_x_positions[layer] = current_x
        layer_width = max(group_sizes[group.id][0] for group in layer_to_groups[layer])
        current_x += layer_width + flow_gap

    for layer in sorted(layer_to_groups):
        current_y = 50.0
        for group in sorted(
            layer_to_groups[layer],
            key=lambda item: (_group_flow_order_key(workflow, item, layers), item.bounding[1], item.bounding[0], item.id),
        ):
            g_width, g_height = group_sizes[group.id]
            min_x, min_y, _max_x, _max_y = _group_content_bounds(group)
            new_x = layer_x_positions[layer]
            new_y = current_y
            offset_x = (new_x + settings.group_padding) - min_x
            offset_y = (new_y + _group_top_padding(settings)) - min_y

            for node in group.nodes:
                if _is_pinned_node(workflow, node):
                    continue
                node.x += offset_x
                node.y += offset_y

            group.bounding = [new_x, new_y, g_width, g_height]
            current_y += g_height + settings.group_v_gap
```

Replace with:

```python
def _position_groups_by_flow_layers(
    workflow: Workflow,
    settings: LayoutSettings,
    start_x: float,
) -> None:
    """Place connected groups in dataflow columns to shorten cross-group wires."""
    group_sizes = {
        group.id: _required_group_size(group, settings)
        for group in workflow.groups
        if group.nodes and not group.pinned
    }
    if not group_sizes:
        return

    layers = _group_flow_layers(workflow)
    layer_to_groups: dict[int, list[Group]] = {}
    for group in workflow.groups:
        if group.id in group_sizes:
            layer_to_groups.setdefault(layers.get(group.id, 0), []).append(group)

    flow_gap = _flow_group_h_gap(workflow, settings)
    layer_sizes: dict[int, tuple[float, float]] = {}
    for layer, groups_in_layer in layer_to_groups.items():
        layer_width = max(group_sizes[group.id][0] for group in groups_in_layer)
        layer_height = sum(group_sizes[group.id][1] for group in groups_in_layer)
        layer_height += settings.group_v_gap * max(0, len(groups_in_layer) - 1)
        layer_sizes[layer] = (layer_width, layer_height)

    row_width_cap = (
        _toplevel_wrap_row_width_cap(layer_sizes) if settings.wrap_columns else math.inf
    )
    layer_positions = _wrap_layer_columns(
        layer_sizes, row_width_cap, start_x, 50.0, flow_gap, settings.group_v_gap
    )

    for layer in sorted(layer_to_groups):
        row_x, row_y = layer_positions[layer]
        current_y = row_y
        for group in sorted(
            layer_to_groups[layer],
            key=lambda item: (_group_flow_order_key(workflow, item, layers), item.bounding[1], item.bounding[0], item.id),
        ):
            g_width, g_height = group_sizes[group.id]
            min_x, min_y, _max_x, _max_y = _group_content_bounds(group)
            new_x = row_x
            new_y = current_y
            offset_x = (new_x + settings.group_padding) - min_x
            offset_y = (new_y + _group_top_padding(settings)) - min_y

            for node in group.nodes:
                if _is_pinned_node(workflow, node):
                    continue
                node.x += offset_x
                node.y += offset_y

            group.bounding = [new_x, new_y, g_width, g_height]
            current_y += g_height + settings.group_v_gap
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_layout.py -k connected_group_columns -v`
Expected: `2 passed`

- [ ] **Step 5: Run the full `test_layout.py` file to confirm no regressions**

Run: `uv run pytest tests/test_layout.py -q`
Expected: all pass (this file has 66 tests after Task 1 and this task's additions; none of the pre-existing ones pass `wrap_columns=True` explicitly, so none should change behavior)

- [ ] **Step 6: Commit**

```bash
git add flowforge/layout.py tests/test_layout.py
git commit -m "Wrap connected-group flow columns into boustrophedon rows"
```

---

### Task 3: Wrap linked ungrouped-node flow columns

**Files:**
- Modify: `flowforge/layout.py`, functions `_position_linked_ungrouped_nodes` and `_node_layer_x_positions` (near lines 2012-2056)
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: `_wrap_layer_columns(...)`, `_toplevel_wrap_row_width_cap(...)`, `LayoutSettings.wrap_columns` from Task 1.
- Changes `_node_layer_x_positions`'s return type from `dict[int, float]` to `dict[int, tuple[float, float]]`. This function is only called from `_position_linked_ungrouped_nodes` (verified: no other call sites, not exported/tested directly), so this is a safe internal-only signature change.

- [ ] **Step 1: Write the failing integration test**

Add to `tests/test_layout.py`, right after `test_linked_ungrouped_nodes_use_per_layer_widths` (ends at line 1187):

```python
def test_linked_ungrouped_nodes_wrap_downward_after_soft_width():
    logger.info("Testing linked ungrouped node columns wrap downward")
    wf = Workflow()
    nodes = [
        Node(id=index, type=f"Node{index}", x=0, y=0, size=[300, 100])
        for index in range(1, 7)
    ]
    wf.nodes = {node.id: node for node in nodes}
    wf.links = {}
    for index in range(1, 6):
        link = Link(id=index, source=index, source_port=0, target=index + 1, target_port=0, type="DATA")
        wf.links[index] = link
        wf.nodes[index].output_links.append(index)
        wf.nodes[index + 1].input_links.append(index)

    settings = LayoutSettings(50, 50, wrap_columns=True)
    _position_linked_ungrouped_nodes(wf, nodes, settings, start_x=100)

    row_tops = {round(node.y, 3) for node in nodes}
    layout_left = min(node.x for node in nodes)
    layout_right = max(node.x + node.size[0] for node in nodes)
    total_node_width = sum(node.size[0] for node in nodes)

    assert len(row_tops) > 1, "expected the linked ungrouped node chain to wrap into more than one row"
    assert layout_right - layout_left < total_node_width, "expected wrapping to bound width below the unwrapped sum"
    logger.info("Linked ungrouped node column wrap test passed")


def test_linked_ungrouped_nodes_stay_single_row_without_wrap_flag():
    logger.info("Testing linked ungrouped node columns stay unwrapped by default")
    wf = Workflow()
    nodes = [
        Node(id=index, type=f"Node{index}", x=0, y=0, size=[300, 100])
        for index in range(1, 7)
    ]
    wf.nodes = {node.id: node for node in nodes}
    wf.links = {}
    for index in range(1, 6):
        link = Link(id=index, source=index, source_port=0, target=index + 1, target_port=0, type="DATA")
        wf.links[index] = link
        wf.nodes[index].output_links.append(index)
        wf.nodes[index + 1].input_links.append(index)

    _position_linked_ungrouped_nodes(wf, nodes, LayoutSettings(50, 50), start_x=100)

    row_tops = {round(node.y, 3) for node in nodes}
    assert len(row_tops) == 1, "default settings (wrap_columns=False) must stay single-row"
    logger.info("Linked ungrouped node column default-unwrapped test passed")
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_layout.py -k linked_ungrouped_nodes_wrap -v`
Expected: `test_linked_ungrouped_nodes_wrap_downward_after_soft_width` FAILS, `test_linked_ungrouped_nodes_stay_single_row_without_wrap_flag` PASSES already.

- [ ] **Step 3: Wire the helper into `_position_linked_ungrouped_nodes`**

Find (near line 2012-2038):

```python
def _position_linked_ungrouped_nodes(
    workflow: Workflow,
    nodes: list[Node],
    settings: LayoutSettings,
    start_x: float,
) -> float:
    """Position linked ungrouped nodes in dataflow layers."""
    layers = _ungrouped_flow_layers(workflow, nodes)
    layer_to_nodes: dict[int, list[Node]] = {}
    for node in nodes:
        layer_to_nodes.setdefault(layers[node.id], []).append(node)

    layer_x_positions = _node_layer_x_positions(layer_to_nodes, start_x, settings)
    current_right = start_x
    for layer in sorted(layer_to_nodes):
        layer_nodes = sorted(layer_to_nodes[layer], key=lambda node: (_ungrouped_barycenter(workflow, node, layers), node.y, node.x, node.id))
        x = layer_x_positions[layer]
        y = 50.0
        column_width = 0.0
        for node in layer_nodes:
            node.x = x
            node.y = y
            y += _node_visual_height(node) + settings.node_v_gap
            column_width = max(column_width, _node_visual_width(node))
        current_right = max(current_right, x + column_width)

    return current_right
```

Replace with:

```python
def _position_linked_ungrouped_nodes(
    workflow: Workflow,
    nodes: list[Node],
    settings: LayoutSettings,
    start_x: float,
) -> float:
    """Position linked ungrouped nodes in dataflow layers."""
    layers = _ungrouped_flow_layers(workflow, nodes)
    layer_to_nodes: dict[int, list[Node]] = {}
    for node in nodes:
        layer_to_nodes.setdefault(layers[node.id], []).append(node)

    layer_positions = _node_layer_x_positions(layer_to_nodes, start_x, settings)
    current_right = start_x
    for layer in sorted(layer_to_nodes):
        layer_nodes = sorted(layer_to_nodes[layer], key=lambda node: (_ungrouped_barycenter(workflow, node, layers), node.y, node.x, node.id))
        x, y = layer_positions[layer]
        column_width = 0.0
        for node in layer_nodes:
            node.x = x
            node.y = y
            y += _node_visual_height(node) + settings.node_v_gap
            column_width = max(column_width, _node_visual_width(node))
        current_right = max(current_right, x + column_width)

    return current_right
```

- [ ] **Step 4: Wire the helper into `_node_layer_x_positions`**

Find (near line 2041-2056):

```python
def _node_layer_x_positions(
    layer_to_nodes: dict[int, list[Node]],
    start_x: float,
    settings: LayoutSettings,
) -> dict[int, float]:
    """Use per-layer node widths for ungrouped dataflow columns."""
    positions: dict[int, float] = {}
    current_x = start_x
    for layer in sorted(layer_to_nodes):
        positions[layer] = current_x
        layer_width = max(
            (_node_visual_width(node) for node in layer_to_nodes[layer]),
            default=NODE_MIN_WIDTH,
        )
        current_x += layer_width + settings.node_h_gap
    return positions
```

Replace with:

```python
def _node_layer_x_positions(
    layer_to_nodes: dict[int, list[Node]],
    start_x: float,
    settings: LayoutSettings,
) -> dict[int, tuple[float, float]]:
    """Use per-layer node widths for ungrouped dataflow columns, wrapping into
    boustrophedon rows once a row grows too wide (see _wrap_layer_columns)."""
    layer_sizes: dict[int, tuple[float, float]] = {}
    for layer, nodes in layer_to_nodes.items():
        width = max((_node_visual_width(node) for node in nodes), default=NODE_MIN_WIDTH)
        height = sum(_node_visual_height(node) for node in nodes)
        height += settings.node_v_gap * max(0, len(nodes) - 1)
        layer_sizes[layer] = (width, height)

    row_width_cap = (
        _toplevel_wrap_row_width_cap(layer_sizes) if settings.wrap_columns else math.inf
    )
    return _wrap_layer_columns(
        layer_sizes, row_width_cap, start_x, 50.0, settings.node_h_gap, settings.node_v_gap
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_layout.py -k linked_ungrouped_nodes -v`
Expected: all pass, including the pre-existing `test_linked_ungrouped_nodes_use_per_layer_widths` and `test_ungrouped_bridge_nodes_influence_group_flow_columns`-adjacent tests.

- [ ] **Step 6: Run the full `test_layout.py` file to confirm no regressions**

Run: `uv run pytest tests/test_layout.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add flowforge/layout.py tests/test_layout.py
git commit -m "Wrap linked ungrouped-node flow columns into boustrophedon rows"
```

---

### Task 4: Feed a wrapped candidate into the layout scorer

**Files:**
- Modify: `flowforge/layout.py`, function `_build_layout_candidates` (near line 344-378, offsets will have shifted after Tasks 1-3's insertions — locate by function name, not line number)
- Modify: `tests/test_layout.py` (update one pre-existing test)

**Interfaces:**
- Consumes: `LayoutSettings(..., wrap_columns=True)` from Task 1, `_position_groups_by_flow_layers`/`_position_linked_ungrouped_nodes` wrap behavior from Tasks 2-3.
- `_build_layout_candidates` now returns `requested_count + 1` variants instead of `requested_count` (the extra one is the prepended wrap variant). `apply_best_layout` and callers already iterate whatever `_build_layout_candidates` returns, so no other call site changes.

- [ ] **Step 1: Update `_build_layout_candidates`**

Find (near line 344-378):

```python
def _build_layout_candidates(
    workflow: Workflow,
    settings: LayoutSettings,
    candidate_count: int,
) -> list[LayoutSettings]:
    count = max(1, int(candidate_count))
    variants: list[LayoutSettings] = []
    profiles = _ordered_layout_profiles(workflow)

    for x_scale, y_scale in profiles[:count]:
        variants.append(
            LayoutSettings(
                settings.node_x_distance * x_scale,
                settings.node_y_distance * y_scale,
            )
        )

    if len(variants) >= count:
        return variants[:count]

    profile_index = 0
    while len(variants) < count:
        x_scale, y_scale = profiles[profile_index % len(profiles)]
        x_delta = 1.0 + (x_scale - 1.0) * 0.5
        y_delta = 1.0 + (y_scale - 1.0) * 0.5
        variants.append(
            LayoutSettings(
                settings.node_x_distance * x_delta,
                settings.node_y_distance * y_delta,
            )
        )
        profile_index += 1

    return variants
```

Replace with:

```python
def _build_layout_candidates(
    workflow: Workflow,
    settings: LayoutSettings,
    candidate_count: int,
) -> list[LayoutSettings]:
    count = max(1, int(candidate_count))
    variants: list[LayoutSettings] = []
    profiles = _ordered_layout_profiles(workflow)

    # Always try one boustrophedon-column-wrap variant first. It is
    # guaranteed to be evaluated (LAYOUT_CANDIDATE_MIN_EVALUATIONS keeps the
    # first 3 candidates regardless of the patience-based early stop), so a
    # workflow that does not need wrapping just loses to a later non-wrap
    # candidate in _score_layout_candidate at the cost of one extra pass.
    variants.append(
        LayoutSettings(
            settings.node_x_distance,
            settings.node_y_distance,
            wrap_columns=True,
        )
    )
    target_count = count + 1

    for x_scale, y_scale in profiles[:count]:
        variants.append(
            LayoutSettings(
                settings.node_x_distance * x_scale,
                settings.node_y_distance * y_scale,
            )
        )

    if len(variants) >= target_count:
        return variants[:target_count]

    profile_index = 0
    while len(variants) < target_count:
        x_scale, y_scale = profiles[profile_index % len(profiles)]
        x_delta = 1.0 + (x_scale - 1.0) * 0.5
        y_delta = 1.0 + (y_scale - 1.0) * 0.5
        variants.append(
            LayoutSettings(
                settings.node_x_distance * x_delta,
                settings.node_y_distance * y_delta,
            )
        )
        profile_index += 1

    return variants
```

- [ ] **Step 2: Run the existing candidate-search tests to see the expected failure**

Run: `uv run pytest tests/test_layout.py::test_best_layout_tries_multiple_candidates_and_picks_best -v`
Expected: FAIL — `attempts[:5]` no longer matches because a new wrap-candidate entry is now first in the sequence.

- [ ] **Step 3: Update the test's expectation**

In `tests/test_layout.py`, find (near line 1697-1703):

```python
    assert attempts[:5] == [
        (85.0, 115.0, False),
        (80.0, 100.0, False),
        (70.0, 90.0, False),
        (100.0, 100.0, False),
        (100.0, 80.0, False),
    ]
```

Replace with:

```python
    assert attempts[:6] == [
        (100.0, 100.0, False),  # forced boustrophedon-wrap candidate, tried first
        (85.0, 115.0, False),
        (80.0, 100.0, False),
        (70.0, 90.0, False),
        (100.0, 100.0, False),
        (100.0, 80.0, False),
    ]
```

Leave the rest of the test (`attempts[-1] == (70.0, 90.0, True)` and the final `result.nodes[2]` assertions) unchanged — the winning candidate does not change, only the list of attempted candidates gains one leading entry.

- [ ] **Step 4: Run that test again to verify it passes**

Run: `uv run pytest tests/test_layout.py::test_best_layout_tries_multiple_candidates_and_picks_best -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest tests/ -q --ignore=tests/test_example_workflows.py`
Expected: all pass. (`test_example_workflows.py` depends on a local, gitignored workflow corpus with a pre-existing unrelated file-count assertion failure — not caused by this change; confirm by checking `uv run pytest tests/test_example_workflows.py -q` shows the same single pre-existing failure as before this plan's changes, not a new one.)

- [ ] **Step 6: Commit**

```bash
git add flowforge/layout.py tests/test_layout.py
git commit -m "Try a boustrophedon-wrap layout candidate in the candidate search"
```

---

### Task 5: Changelog entry

**Files:**
- Modify: `CHANGELOG.md`

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Add an `[Unreleased]` entry**

In `CHANGELOG.md`, find:

```markdown
## [Unreleased]

## [0.3.0] - 2026-06-13
```

Replace with:

```markdown
## [Unreleased]

### Changed

- Wrap connected-group and linked-ungrouped-node dataflow columns into boustrophedon (snake) rows once they exceed a soft width cap, instead of growing width unbounded while leaving vertical space unused. The layout candidate search tries this alongside the existing spacing variants and keeps whichever scores better.

## [0.3.0] - 2026-06-13
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "Add changelog entry for dataflow-column row wrapping"
```
