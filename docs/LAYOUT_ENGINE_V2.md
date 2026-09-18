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

## Phase 2.5 corpus result

The compact internal-layer change did not materially reduce the largest workflow
dimensions. On the 81-workflow corpus:

| Mode | Phase 2 | Phase 2.5 |
| --- | ---: | ---: |
| Layout only crossings / RTL | 6,052 / 379 | 6,049 / 386 |
| Optimize + Layout crossings / RTL | 4,153 / 318 | 4,130 / 333 |

The large workflow dimensions were essentially unchanged. This showed that the
dominant width bias was not the node-level group refiner alone. The existing
top-level group and linked ungrouped placement paths both create dataflow
columns, and wrapping was only available through a candidate flag that was not
winning for the problematic workflows.

## Phase 3 compact global flow

Phase 3 keeps the complete Phase 2 result as its baseline and creates one
additional compact global-flow candidate when workflow width is at least 7,000
pixels.

The compact candidate:

1. forces the existing boustrophedon wrap mode for top-level group flow;
2. forces the same wrap mode for linked ungrouped dataflow;
3. re-applies weighted Phase 2 group ordering;
4. re-runs the normal geometry finalizers;
5. is accepted immediately if the normal Phase 2 quality key improves;
6. otherwise requires at least 18% width reduction while allowing only tightly
   bounded crossing/RTL regressions and at most 15% growth in width+height.

Small workflows therefore keep the existing layout path unchanged. The compact
pass is an optional post-process candidate and cannot overwrite the Phase 2
baseline unless its trade-off satisfies the explicit guardrails.

## Phase 3 corpus result

The first compact global-flow pass produced only a narrow improvement:

| Mode | Phase 2.5 | Phase 3 |
| --- | ---: | ---: |
| Layout only crossings / RTL | 6,049 / 386 | 6,044 / 386 |
| Optimize + Layout crossings / RTL | 4,130 / 333 | 4,127 / 339 |

The main visible geometry change was the Z-Image Base Ultra workflow:

- Layout Only: `19,318 x 17,462` -> `19,190 x 12,160`;
- Optimize + Layout: `19,062 x 16,116` -> `18,934 x 12,228`.

Other major cases such as Flux Edit Ultra, Luneva, Qwen Edit, and Jibs remained
essentially unchanged in size. This shows that simply forcing the existing wrap
geometry is not a general solution for the width bias.

### Structure diagnostics

The quality reporter now supports an optional structural width diagnosis:

```powershell
uv run python tools/report_layout_quality.py example-workflows --top 20 --structure
uv run python tools/report_layout_quality.py example-workflows --optimize --top 20 --structure
```

For every printed large workflow it reports:

- horizontal span of groups and ungrouped nodes;
- group-flow and ungrouped-flow layer counts;
- number of distinct physical X columns;
- widest group and widest ungrouped node;
- which group/node owns the left and right horizontal workflow edges.

The next layout change should be based on these measurements rather than making
global wrap thresholds more aggressive.

## Width root-cause findings

The structural diagnostics showed that workflow width is not caused by one
single mechanism.

Representative Optimize + Layout results:

- Flux Edit Ultra: groups span 7,031 px, ungrouped span 8,318 px;
- Z-Image Base Ultra: groups span 8,720 px, ungrouped span only 440 px;
- Luneva: groups span 7,826 px, ungrouped span 5,822 px;
- Amazing Z-Image: ungrouped span 12,220 px across 20 flow layers / 43 columns;
- Jibs: groups span 3,090 px while ungrouped flow spans 5,977 px.

The legacy pipeline also places all decorative Note/Markdown/Label nodes in a
left annotation column and then starts every group and ungrouped flow to the
right of the widest annotation. Very wide documentation nodes can therefore
create a large empty horizontal margin even though they carry no dataflow.

Phase 3 now evaluates a separate decorative-compaction candidate for workflows
that are already at least 7,000 px wide. It moves movable decorative annotations
above the graph while preserving every non-decorative node/group coordinate.
Because decorative nodes carry no graph ordering, this candidate does not alter
crossings or RTL by itself and is still subject to the normal compactness gate.

The structure reporter also prints decorative-node count/span and the widest
decorative node so corpus measurements can confirm how much width comes from
annotations versus actual flow geometry.

## Phase 3 final compactness result

After separating structural candidate selection from decorative compaction, the
Optimize + Layout corpus returned to the structurally better result:

- crossings: `4,127`;
- RTL links: `339`.

Decorative compaction then reduced large workflow dimensions without changing
crossings, RTL, or physical link length. Representative results:

- Flux Edit Ultra: `18,288 x 13,211` -> `10,549 x 14,833`;
- Z-Image Base Ultra: `18,934 x 12,228` -> `10,364 x 14,076`;
- Luneva: `13,200 x 16,536` -> `11,108 x 10,610`;
- Qwen Edit: `13,024 x 7,967` -> `7,985 x 9,527`.

The remaining wide workflows are now dominated by actual ungrouped flow rather
than decorative margins:

- Amazing Z-Image: ungrouped span `12,220` px across 20 flow layers / 43 columns;
- Jibs: ungrouped span `5,977` px while grouped span is only `3,090` px;
- several Flux2 VTON workflows also have ungrouped spans larger than their
  grouped spans.

## Phase 4 conservative ungrouped-flow compaction

Phase 4 is an isolated post-process on top of the complete Phase 3 result.

The first implementation deliberately targets only linked ungrouped components
that are safe to move independently. It excludes:

- pinned nodes;
- decorative nodes;
- virtual Set/Get hubs;
- local primitive/control sources;
- terminal text previews;
- nodes with a direct physical link to a group.

Remaining pure ungrouped components are SCC-layered, wrapped into a bounded
horizontal band, and moved vertically only when needed to clear existing group
and node geometry.

The width target for a component is constrained by:

- its widest layer;
- the current grouped horizontal span;
- 62% of the current workflow width.

Components are skipped unless this predicts at least a 10% horizontal
reduction.

The Phase 4 acceptance gate is intentionally stricter than earlier compactness
passes:

- movable overlaps may not increase;
- crossings may not increase;
- RTL links may not increase;
- if graph quality is unchanged, workflow width must fall by at least 8%;
- workflow area may not increase.

This preserves Phase 3 as a safe fallback and lets the corpus show whether
pure-ungrouped band compaction is useful before attempting a more invasive mixed
group/ungrouped global layer model.

## Current Scope Boundary

The active engine now refines movable group internals and top-level group order,
then optionally compacts the global group and linked-ungrouped flow with the
existing wrap geometry. Bridge, external-source, control, and virtual-hub
special cases still remain authoritative.

If the Phase 3 corpus confirms useful width reduction without losing the Phase 2
quality gains, the next structural target is a true SCC/dummy/sweep ordering pass
for ungrouped linked flow rather than only compact placement.

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
