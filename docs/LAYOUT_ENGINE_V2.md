# Layout Engine v2

This document describes the experimental second-generation layout engine on the
`layout-engine-v2` branch. It remains separate from release-facing documentation
until corpus validation is complete.

## Goal

The legacy layout pipeline already contains mature ComfyUI-specific handling for
groups, pinned surfaces, ungrouped bridge nodes, controls, text previews,
virtual Set/Get hubs, node compaction, collision clearance, and serialization.
Layout engine v2 keeps those contracts and replaces the weakest part of the
pipeline: structural crossing minimization and candidate selection.

The safety rule is simple: every new structural pass works on a copy and may be
rejected in favour of the already valid baseline layout.

## Active Pipeline

The active runtime now has two refinement levels.

### 1. Node-level v2 refinement

For every normal layout candidate FlowForge:

1. runs the established six-phase layout pass;
2. finds fully movable groups with internal links;
3. condenses strongly connected components with Tarjan's algorithm;
4. assigns longest-path layers on the SCC DAG;
5. preserves debug/control sidecar compression rules;
6. expands long internal edges with virtual dummy vertices;
7. runs repeated directional median sweeps;
8. runs local adjacent transposition while crossings improve;
9. assigns physical node coordinates;
10. re-runs the established group, pin, control, preview, and virtual-hub
    geometry contracts;
11. compares refined and baseline candidates with port-aware metrics.

Dummy vertices exist only in the ordering graph and are never serialized.

### 2. Weighted group-level refinement

After the best node-level v2 result is selected, FlowForge treats whole movable
groups as a second graph:

1. build the existing inter-group dependency graph, including dependencies that
   pass through ungrouped bridge nodes;
2. condense SCCs and assign group flow layers;
3. weight direct group dependencies by physical wire multiplicity;
4. retain bridge-only group dependencies with unit weight;
5. expand long group dependencies through intermediate layers with virtual
   group dummy vertices;
6. run repeated weighted-median sweeps;
7. run weighted local transposition, where a crossing between edges of weights
   `a` and `b` contributes `a * b`;
8. change only the vertical order of movable groups inside an existing flow
   layer, preserving each group's x coordinate and internal geometry;
9. skip a physical layer when it contains a pinned/fixed group;
10. re-run the normal geometry finalizers;
11. accept the group-level result only when the global port-aware quality key
    improves.

The active Phase 2 quality comparison is lexicographic:

1. movable overlaps;
2. straight-line port-to-port crossings;
3. right-to-left links;
4. total Manhattan cable length;
5. width plus height;
6. width;
7. height.

This prevents a compact layout or shorter cables from buying an additional
physical crossing at the same overlap count.

## Port-Aware Metrics

The v2 scorer measures physical links from the actual source output socket to
the actual target input socket rather than from node centre to node centre.
Metrics include:

- straight-line port-to-port crossings;
- right-to-left physical links;
- overlaps between movable, non-decorative, non-virtual nodes;
- total Manhattan cable length;
- workflow width and height;
- a wide-aspect diagnostic penalty.

## SCC Handling

The legacy longest-path layerer falls cyclic leftovers back to layer zero. v2
first computes strongly connected components. Every SCC shares one layer while
the condensed component graph receives normal longest-path layers.

The same SCC-aware layer assignment is reused at group level so a group cycle
does not collapse unrelated downstream group structure.

## Long-Edge Dummy Vertices

A dependency from layer 0 to layer 4 participates in every intermediate crossing
boundary:

```text
source -> dummy@1 -> dummy@2 -> dummy@3 -> target
```

The same mechanism is used for node-level and group-level ordering. Dummy
vertices never become ComfyUI workflow objects.

## Safety Constraints

Node-level structural refinement skips a group when:

- the group is pinned;
- any member node is pinned;
- the group contains fewer than two nodes;
- the group has no internal physical links;
- the group contains decorative or virtual Set/Get hub nodes.

Group-level refinement:

- preserves group x coordinates and internal node geometry;
- skips physical layers that contain a fixed/pinned group;
- does not change graph topology;
- is accepted only when its global port-aware quality key improves.

The existing bridge, local-control, text-preview, virtual-hub, pinned-surface,
and group-overlap passes remain authoritative after every refinement.

## Corpus Results

The current local corpus contains 81 UI workflows, 3,793 nodes, and 4,357
physical links.

### Master vs first v2 implementation

| Mode | Master crossings | v2 crossings | Master RTL | v2 RTL |
| --- | ---: | ---: | ---: | ---: |
| Layout only | 6,390 | 6,068 | 461 | 379 |
| Optimize + Layout | 4,320 | 4,395 | 372 | 318 |

The first v2 implementation therefore improved Layout Only by 322 crossings and
82 RTL links, but Optimize + Layout regressed by 75 crossings while still
reducing RTL by 54.

The strongest node-level gain was:

- `within_group x within_group`: 873 -> 607 in Layout Only.

The main remaining categories were inter-group related, especially:

- `group->group x group->group`;
- `group->group x within_group`;
- `group->group x ungrouped->group`.

### Rejected Phase 1.5 boundary experiment

A boundary-anchor experiment was tested and removed from the active runtime after
corpus measurement.

Its measured result was:

| Mode | Phase 1.5 crossings | Phase 1.5 RTL |
| --- | ---: | ---: |
| Layout only | 5,921 | 428 |
| Optimize + Layout | 4,443 | 359 |

Although it improved Layout Only crossings relative to the first v2 pass, it
made Optimize + Layout worse than both master and the first v2 result. It also
did not solve the main real-world regression:
`Jibs_Ultimate_SD_Upscale_SDXL_V18_Workflow.json` remained at 1,167 crossings in
Layout Only and 1,055 crossings with Optimize + Layout.

The experiment and its tests were therefore removed instead of accumulating
dead layout logic in the branch. Git history preserves the implementation and
benchmark evidence.

### Phase 2 corpus result

The weighted group-level pass was measured on the same 81-workflow corpus:

| Mode | Master | First v2 | Phase 2 |
| --- | ---: | ---: | ---: |
| Layout only crossings / RTL | 6,390 / 461 | 6,068 / 379 | 6,052 / 379 |
| Optimize + Layout crossings / RTL | 4,320 / 372 | 4,395 / 318 | 4,153 / 318 |

Phase 2 therefore preserves the first-v2 RTL gains, slightly improves Layout
Only crossings, and improves Optimize + Layout by 242 crossings relative to the
first v2 implementation and by 167 relative to master.

The strongest real-world regression also changed materially after Optimize:
`Jibs_Ultimate_SD_Upscale_SDXL_V18_Workflow.json` improved from 960 crossings
on master and 1,056 on first v2 to 853 on Phase 2. Layout Only remains a known
weak case at 1,168 crossings, so group ordering alone does not solve the
unoptimized geometry.

### Phase 2.5 compact placement

Corpus inspection also exposed a strong left-to-right width bias. The node-level
v2 refiner correctly computed SCC layers and crossing order, but then placed
every physical layer into a fresh X column, discarding the compact internal
layer placement already available in the legacy engine.

Phase 2.5 keeps the v2 structural ordering but reuses the existing compact
internal layer placement when assigning physical coordinates. Candidate
selection also adds width guardrails:

- reject more than 35% width growth when the candidate provides only a minor
  crossing/RTL improvement;
- allow width growth for a significant graph-quality gain or when it completely
  eliminates crossings/RTL links;
- do not let compactness override existing structural flow invariants on its own.

The intent is to remove pathological horizontal expansion without replacing the
crossing-aware ordering logic with a purely geometric packing heuristic.

## Current Scope Boundary

The active engine now refines movable group internals and top-level group order.
Ungrouped linked dataflow still uses the legacy placement, including its bridge
and external-source special cases.

After Phase 2.5 compactness is re-measured, the next structural target is
ungrouped linked flow ordering using the same SCC/dummy/sweep core. Phase 3
should not begin until the compactness guard is shown to preserve the Phase 2
crossing and RTL gains.

## Deferred TODOs

- Add user-facing Undo/Redo for destructive workflow transformations such as
  Layout and Optimize. The implementation should restore the complete workflow
  state from before the action rather than trying to reverse individual geometry
  mutations. This is intentionally deferred until the v2 layout engine and its
  frontend integration are stable.

## Validation

Repository CI must remain green:

```powershell
uv run pytest
uv run ruff check .
uv run mypy flowforge
npm run typecheck
npm run build
```

Regression coverage includes:

- SCC condensation and downstream longest-path assignment;
- dummy-vertex participation for long node edges;
- port-aware RTL detection;
- pinned-group node-level refinement blocking;
- graph/link preservation through v2 selection;
- direct group-edge wire multiplicity;
- weighted group medians;
- weighted group crossing elimination;
- fixed-group protection at group level;
- crossing-first group-level candidate selection.

## Documentation Status

README.md and CHANGELOG.md remain intentionally unchanged while this branch is
experimental. If corpus validation confirms v2 as the new default, release-facing
documentation and any affected frontend documentation must be updated before the
pull request is marked ready for review.
