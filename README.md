![ComfyUI FlowForge](assets/social-preview.png)

# ComfyUI FlowForge

Automatically rearranges nodes in a [ComfyUI](https://github.com/comfyanonymous/ComfyUI) workflow JSON file so that data flows left-to-right and connection lines stop crossing each other.

---

## The Problem

ComfyUI workflows grow organically. Nodes get added wherever there is space, moved around during iteration, and groups get reorganised. The result is a canvas where connection lines criss-cross in every direction — hard to read and hard to debug.

FlowForge reads a workflow JSON, computes a clean left-to-right layout using a graph algorithm, and writes the result back. **Only node positions and group bounding boxes are changed.** Every connection, setting, model reference, and widget value is preserved exactly.

---

## Requirements

## Installation

### 2 — Clone and set up

## Quick Start

### Features

- **Before/After View**: Side-by-side node editors showing original and layouted workflows
- **Visual Comparison**: See exactly how nodes are repositioned
- **Interactive Controls**: Open, optimize, layout, and save workflows with button clicks
- **Color-Coded Nodes**: Different node types are visually distinguished
- **Zoom & Pan**: Mouse wheel zoom, plus/minus buttons, and scrollbars for navigation

### Launching the GUI

## How It Works

FlowForge implements a six-phase pipeline:

### Phase 1 — Group Membership

Every node is assigned to the group whose bounding box contains it (using the node's original position). Nodes outside every group form an implicit ungrouped set. When groups overlap, the first matching group in workflow order wins.

### Phase 2 — Inter-Group Topology

A directed graph is built between groups: a group A gets an edge to group B whenever a link crosses from a node in A to a node in B. Before sorting, back-edges are removed by an iterative DFS with white/grey/black colouring — this turns any cyclic group graph into a DAG so the topological sort always produces a valid left-to-right column assignment (see [Known Limitations](#known-limitations) for why cycles arise). The DAG is then sorted using the longest-path algorithm (Kahn's BFS + depth tracking). Groups are assigned to columns from left to right in dependency order. Groups with no ordering relationship share the same column and are stacked vertically, sorted by their original vertical centroid to preserve the author's intended arrangement.

### Phase 3 — Internal Layout (Sugiyama)

Within each group, independently:

1. **Layer assignment** — each node receives a layer number equal to the longest path from any source node to it (`layer = max(layer[predecessor]) + 1`, with sources at layer 0). Uses a topological sort; nodes in cycles (rare in valid ComfyUI workflows) fall back to layer 0.
2. **Crossing minimisation** — nodes within each layer are reordered using the _barycenter heuristic_: each node's score is the average position of its neighbours in the adjacent layer. Two passes are run (forward then backward) to reduce edge crossings.
3. **Coordinate assignment** — nodes are placed on a grid: X increases by layer, Y increases by position within the layer. Bypassed nodes (`mode = 4`) are sorted to the end of their layer so they don't interrupt the active flow.

### Phase 4 — Global Positioning

The content size of every group is known after Phase 3. Column widths are determined by the widest group in each column. Groups are placed left-to-right by column and top-to-bottom within each column. Group padding is added around the content area. Node positions are translated from group-local coordinates to global canvas coordinates.

### Phase 5 — Decorative Nodes

Comment nodes (`Note`, `MarkdownNote`, `Label`) carry no dataflow edges and are excluded from the graph algorithm. After layout they are repositioned by computing the original offset vector from each decorative node to its nearest layout node (in original coordinates) and applying the same offset to the layout node's new position. This keeps notes visually attached to the nodes they describe.

### Phase 6 — Bounding Box Update

Each group's `bounding` rectangle is recalculated from the final positions of its member nodes plus the group padding.

### Spacing Defaults

All spacing is defined as module-level constants in `flowforge/layout.py` and can be adjusted:

| Constant        | Default | Description                                             |
| --------------- | ------- | ------------------------------------------------------- |
| `NODE_H_GAP`    | 80 px   | Horizontal gap between node columns within a group.     |
| `NODE_V_GAP`    | 40 px   | Vertical gap between nodes in the same column.          |
| `GROUP_H_GAP`   | 200 px  | Horizontal gap between group columns.                   |
| `GROUP_V_GAP`   | 100 px  | Vertical gap between groups stacked in the same column. |
| `GROUP_PADDING` | 50 px   | Padding inside a group's bounding box.                  |

---

## Optimizer (optional)

Pass `--optimize` to run a pre-layout pass that converts high-fanout `MODEL`, `CLIP`, and `VAE` connections into [Set/Get node pairs](https://github.com/kijai/ComfyUI-KJNodes) (comfyui-kjnodes must be installed in ComfyUI).

**When to use it:** workflows where a single loader node (e.g. `VAELoader`, `UNETLoader`, `CLIPLoader`) fans out to three or more downstream consumers typically have many crossing long-distance wires. Replacing these with Set/Get pairs:

- Eliminates the long wires entirely, which reduces crossing counts after layout.
- Breaks inter-group cycles that loader fan-out would otherwise create, allowing the layout algorithm to produce a strictly left-to-right result.

**What it does:** for every output of type `MODEL`, `CLIP`, or `VAE` with two or more downstream connections, one `SetNode` is inserted immediately after the source and one `GetNode` is inserted before each target. The original direct links are removed. The workflow runs identically in ComfyUI.

**Requirement:** comfyui-kjnodes must be installed in your ComfyUI instance, otherwise ComfyUI will show missing-node warnings on load.

---

## Known Limitations

**Residual back-edges after cycle breaking.** When two groups genuinely exchange data bidirectionally (rare in well-structured workflows), the DFS cycle-breaker removes the minimum number of back-edges to produce a DAG. The removed edges become right-to-left wires in the output — typically fewer than 20 % of links in affected workflows. Using `--optimize` often eliminates the root cause by converting loader fan-outs to Set/Get pairs before layout runs.

**Group overlap in the original workflow.** If a node's original position lies inside multiple overlapping group bounding boxes, it is assigned to the first group in workflow order. This is the same behaviour as ComfyUI itself.

**Sub-graph nodes.** Nodes whose type is a UUID (ComfyUI inline sub-graphs) are treated as opaque black boxes. Their internal nodes are not rearranged.

---

## Project Structure

```## License

```
