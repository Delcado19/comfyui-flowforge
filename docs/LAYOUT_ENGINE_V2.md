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
v2 selection policy improves. This keeps the legacy candidate as a local safety
fallback.

## Current v2 Pipeline

For every legacy layout candidate:

1. Run the existing six-phase layout pass.
2. Find fully movable groups whose internal graph contains links.
3. Condense strongly connected components (SCCs) with Tarjan's algorithm.
4. Assign longest-path layers on the condensed DAG.
5. Preserve the existing debug/control sidecar layer compression rules.
6. Expand links that skip layers with in-memory dummy vertices.
7. Add fixed boundary anchors for physical links entering or leaving a group.
8. Expand boundary links across intermediate layers with in-memory dummy chains.
9. Run repeated directional median sweeps over adjacent layers.
10. Run local adjacent-node transposition passes while crossings improve,
    including the fixed boundary layers in the local crossing count.
11. Reject a group refinement if its physical incident crossing count increases.
12. Remove dummy and boundary vertices from the physical layout and assign node
    coordinates.
13. Re-run the existing bounding-box, group-overlap, pinned-clearance,
    control, text-preview, and virtual-hub geometry contracts.
14. Compare the legacy and refined versions with crossing-first candidate gates.
15. Select the best candidate with the same crossing-first policy.

Dummy and boundary vertices are layout-only objects. They never become ComfyUI
nodes and are never serialized.

## Boundary-Aware Group Refinement

The first v2 implementation optimized only links whose source and target were
inside the same group. Corpus comparison showed that this reduced pure
within-group crossings but could move an otherwise well-placed boundary node
away from its external neighbours.

Phase 1.5 models incoming and outgoing physical links as fixed anchors outside
the left and right group boundaries. Their vertical order comes from the actual
external socket geometry. Dummy chains carry that order through every internal
layer to the connected node.

After the proposed node order is placed, FlowForge counts all physical
crossings where at least one segment touches the group. If the count increased,
the group geometry is restored exactly to its pre-refinement state.

This local guard is intentionally stricter than a weighted score: a shorter or
more compact group cannot buy an increase in incident crossings.

## Crossing-First Candidate Selection

The port-aware v2 metrics are still collected for diagnostics, but candidate
selection is now lexicographic instead of relying only on a weighted sum:

1. movable overlaps;
2. straight-line port-to-port crossings;
3. right-to-left physical links;
4. total Manhattan cable length;
5. workflow area;
6. width plus height;
7. the original weighted v2 score as the final tie-breaker.

This means a candidate with fewer right-to-left links, shorter cables, or a
smaller canvas cannot replace another candidate if it introduces an additional
physical crossing at the same overlap count.

## Port-Aware Geometry

The legacy candidate scorer measures physical links between node centres. The
v2 metrics measure each physical link from the source output socket to the
target input socket using the same port geometry helpers used by FlowForge's
placement code.

The metrics include:

- straight-line port-to-port crossings;
- right-to-left physical links;
- overlaps between movable, non-decorative, non-virtual nodes;
- total Manhattan cable length;
- workflow width and height;
- a wide-aspect penalty used by the diagnostic weighted score.

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

In addition, Phase 1.5 rejects an individual group refinement when its incident
physical crossing count increases, and final candidate selection rejects a
crossing regression when overlap safety is unchanged.

## Corpus Baseline and First v2 Comparison

The current local corpus contains 81 UI workflows, 3,793 nodes, and 4,357
physical links. The same corpus was measured on `master` and on the first v2
implementation before Phase 1.5.

| Mode | Master crossings | v2 crossings | Master RTL | v2 RTL |
| --- | ---: | ---: | ---: | ---: |
| Layout only | 6,390 | 6,068 | 461 | 379 |
| Optimize + Layout | 4,320 | 4,395 | 372 | 318 |

The first v2 implementation therefore improved layout-only crossings by 322 and
RTL links by 82, while Optimize + Layout regressed by 75 crossings despite
improving RTL by 54.

Category comparison identified the main boundary problem:

- `within_group x within_group`: 873 -> 607 in layout-only;
- `group->group x group->group`: 1,485 -> 1,301 in layout-only;
- `group->group x within_group`: 941 -> 1,153 in layout-only;
- `group->group x within_group`: 799 -> 940 with Optimize + Layout.

The SDXL workflow `Jibs_Ultimate_SD_Upscale_SDXL_V18_Workflow.json` was the
largest single regression: v2 added about 100 crossings relative to master in
both modes. It is the primary real-world regression case for Phase 1.5.

Phase 1.5 must now be re-measured on the same 81-workflow corpus before the
engine proceeds to top-level group ordering.

## Current Scope Boundary

The engine still deliberately changes structural ordering only inside movable
groups. Ungrouped linked flows and top-level group ordering continue to use the
legacy placement.

If Phase 1.5 removes the boundary regression without sacrificing the current
within-group gains, the next step is a weighted group-level Sugiyama pass:
SCC condensation, group dependency layers, weighted inter-group edges, dummy
vertices for long group edges, repeated sweeps, and local transpose refinement.

## Validation

Repository CI must remain green:

```powershell
uv run pytest
uv run ruff check .
uv run mypy flowforge
npm run typecheck
npm run build
```

Regression tests now cover:

- SCC condensation and downstream longest-path assignment;
- dummy-vertex participation for long crossing edges;
- port-aware right-to-left detection;
- pinned-group refinement blocking;
- graph/link preservation through v2 candidate selection;
- crossing-first candidate ordering;
- boundary-anchor influence from external socket geometry;
- rejection and exact rollback of a group refinement that increases incident
  crossings.

Before merging v2, reproduce the read-only corpus reports for both Layout Only
and Optimize + Layout and compare them with `master` on the same workflow set.

## Documentation Status

The release-facing README is intentionally unchanged while this branch remains
experimental. If corpus validation confirms v2 as the new default, README.md,
CHANGELOG.md, and any affected frontend documentation must be updated before the
PR is marked ready for review.
