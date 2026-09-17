# Layout Engine v2

This document describes the experimental second-generation layout engine on the
`layout-engine-v2` branch. It is intentionally separate from the release-facing
README until corpus validation is complete.

## Goal

The legacy layout pipeline has mature handling for ComfyUI-specific geometry:
groups, pinned surfaces, ungrouped bridge nodes, controls, text previews,
virtual Set/Get hubs, node compaction, and collision clearance. The v2 work does
not replace those contracts. It replaces the weakest part of the layout search:
structural crossing minimization and candidate scoring.

The design goal is that a v2 structural refinement is never mandatory. For each
spacing/wrapping candidate, FlowForge first produces the normal legacy layout,
then tries the v2 refinement on a copy. The refined copy is kept only when the
v2 score improves. This keeps the legacy candidate as a local safety fallback.

## Current v2 Pipeline

For every legacy layout candidate:

1. Run the existing six-phase layout pass.
2. Find fully movable groups whose internal graph contains links.
3. Condense strongly connected components (SCCs) with Tarjan's algorithm.
4. Assign longest-path layers on the condensed DAG.
5. Preserve the existing debug/control sidecar layer compression rules.
6. Expand links that skip layers with in-memory dummy vertices.
7. Run repeated directional median sweeps over adjacent layers.
8. Run local adjacent-node transposition passes while crossings improve.
9. Remove dummy vertices from the physical layout and assign node coordinates.
10. Re-run the existing bounding-box, group-overlap, pinned-clearance,
    control, text-preview, and virtual-hub geometry contracts.
11. Score both the legacy and refined versions and retain the lower-cost one.
12. Select the best candidate using the v2 score.

Dummy vertices are layout-only objects. They never become ComfyUI nodes and are
never serialized.

## Port-Aware Candidate Score

The legacy candidate scorer measures physical links between node centres. The
v2 scorer measures each physical link from the source output socket to the
target input socket using the same port geometry helpers used by FlowForge's
placement code.

The v2 score currently combines:

- straight-line port-to-port crossings;
- right-to-left physical links;
- overlaps between movable, non-decorative, non-virtual nodes;
- total Manhattan cable length;
- workflow width and height;
- a wide-aspect penalty.

Crossings, right-to-left links, and movable overlaps receive substantially
higher weights than small compactness differences.

## SCC Handling

The legacy longest-path layerer falls cyclic leftovers back to layer zero. v2
instead computes strongly connected components first. Every SCC is treated as a
single vertex while assigning global layers. Nodes inside an SCC share the same
layer because a cycle cannot be represented as strictly left-to-right without
breaking at least one edge.

This prevents an unrelated cycle from collapsing the rest of a connected
subgraph back into the source layer.

## Long-Edge Dummy Vertices

A link from layer 0 to layer 4 participates in crossing minimization at every
intermediate boundary:

```text
source -> dummy@1 -> dummy@2 -> dummy@3 -> target
```

The dummy chain exists only while calculating vertical order. This makes a long
wire influence layers 1, 2, and 3 instead of appearing only as a distant
source/target relationship.

## Safety Constraints

The structural refiner currently skips a group when:

- the group is pinned;
- any member node is pinned;
- the group contains fewer than two nodes;
- the group has no internal physical links;
- the group contains decorative or virtual Set/Get hub nodes.

Existing bridge, local-control, text-preview, virtual-hub, pinned-surface, and
group-overlap passes remain authoritative after refinement.

## Current Scope Boundary

This first implementation deliberately refines **group internals only**.
Ungrouped linked flows and top-level group ordering still use the mature legacy
placement. Extending the same SCC/dummy/sweep mechanism to those surfaces is a
follow-up only after the group-internal implementation passes the repository CI
and the `example-workflows` corpus is re-measured.

## Validation

Repository CI must remain green:

```powershell
uv run pytest
uv run ruff check .
uv run mypy flowforge
npm run typecheck
npm run build
```

New regression tests cover:

- SCC condensation and downstream longest-path assignment;
- dummy-vertex participation for long crossing edges;
- port-aware right-to-left detection;
- pinned-group refinement blocking;
- graph/link preservation through v2 candidate selection.

Before merging v2, reproduce the read-only corpus reports for both layout-only
and Optimize + Layout and compare them with the documented baseline in
`docs/OPTIMIZER_NOTES.md`.

## Documentation Status

The release-facing README is intentionally unchanged while this branch remains
experimental. If corpus validation confirms v2 as the new default, README.md,
CHANGELOG.md, and any affected frontend documentation must be updated before the
PR is marked ready for review.
