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

The first implementation started with only linked ungrouped components that
were safe to move independently. Corpus diagnostics showed that this was too
conservative: the remaining width hotspots are mixed group/ungrouped flows, so
excluding every node with a direct physical group link fragmented Jibs and the
Flux2 VTON workflows into useless islands.

Phase 4 therefore keeps these exclusions:

- pinned nodes;
- decorative nodes;
- virtual Set/Get hubs;
- local primitive/control sources;
- terminal text previews.

Direct group incidence is now allowed. Groups themselves remain fixed
obstacles/anchors, and the candidate is still rejected globally if crossings,
RTL links, or movable overlaps increase.

Eligible ungrouped components are SCC-layered, wrapped into a bounded
horizontal band, and moved vertically only when needed to clear existing group
and node geometry. Direct group incidence no longer excludes a node, but the
active compaction graph still connects only eligible ungrouped nodes to each
other; groups are not yet structural vertices in the placement pass.

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
- workflow width must fall by at least 8%;
- workflow area may not increase.

Crossing or RTL improvements do not bypass the width or area requirements. This
keeps Phase 4 a compaction pass rather than allowing a structurally better but
nearly equally wide candidate to enter through a separate acceptance path.

This preserves Phase 3 as a safe fallback and lets the corpus show whether
pure-ungrouped band compaction is useful before attempting a more invasive mixed
group/ungrouped global layer model.

### Phase 4 diagnostic findings

The first corpus run produced no accepted Phase 4 changes. Targeted diagnostics
showed why:

- Jibs: 25 ungrouped nodes, but only 6 remained eligible after excluding
  Set/Get hubs, controls, previews, and direct group incidence; those 6 formed
  no linked component;
- Flux2 VTON 6.0.2: 7 of 9 ungrouped nodes were directly group-incident, leaving
  only one eligible node;
- Flux2 VTON 6.0.2.7: the remaining 3-node pure component already fit inside the
  target width band, so it offered 0% potential reduction;
- Amazing Z-Image: all 114 ungrouped nodes are explicitly pinned in the source
  workflow. The parser maps ComfyUI `flags.pinned` directly, so this is a real
  hard layout constraint rather than a parser artifact.

The Phase 4 experiment therefore now permits direct group incidence while still
respecting pinned nodes and the established local-placement special cases.
Amazing Z-Image is intentionally not compacted unless the product later gains an
explicit user option to ignore authored pins.

A targeted rerun after enabling direct group incidence confirmed that the filter
was only the first blocker:

- Jibs: eligibility increased to 12 nodes. One 4-node component became
  compactable with 11.8% estimated potential. Its proposal reduced crossings
  from 756 to 674, but width only changed from 5,977 to 5,909 px and RTL rose
  from 41 to 42, so it was rejected.
- Flux2 VTON 6.0.2: 8 of 9 ungrouped nodes are eligible and 7 are directly
  group-incident, but the pure eligible graph still has no component with the
  minimum node count.
- Flux2 VTON 6.0.2.7: 15 of 16 ungrouped nodes are eligible and 9 are directly
  group-incident. The largest 6-node pure component spans 3,784 px but predicts
  0% reduction inside the current target band.

These results show that direct-group eligibility alone does not make Phase 4 a
mixed structural graph. The diagnostics now additionally build a read-only
group-bridged connectivity graph: eligible ungrouped nodes are normal vertices,
groups are fixed connector supernodes, and physical group/group or
group/ungrouped links preserve connectivity. The reporter prints the number of
such mixed components plus the largest component's eligible-node count, group
count, and horizontal span. This graph is diagnostic only and does not move
groups.

The targeted group-bridged rerun confirmed that architecture:

- Jibs: the pure eligible graph had 2 linked components, while the group-bridged
  graph collapsed the relevant flow to 1 component containing 8 eligible
  ungrouped nodes and 14 groups across a 4,202 px span.
- Flux2 VTON 6.0.2: the pure eligible graph had no component large enough to
  attempt compaction, while the group-bridged graph produced 1 component with
  all 8 eligible ungrouped nodes, 4 groups, and a 6,175 px span.
- Flux2 VTON 6.0.2.7: the pure eligible graph had 3 linked components and a
  largest 6-node span of 3,784 px with 0% predicted reduction. The
  group-bridged graph produced 1 component with 14 eligible ungrouped nodes,
  5 groups, and a 7,982 px span.

This is direct evidence that the remaining width problem is not a pure
ungrouped-packing threshold issue. Groups are the structural connectors that
join the wide flow, so further Phase 4 threshold tuning would optimize the
wrong graph.

## Phase 5 shared Group/Ungrouped global graph

Phase 5 is now an isolated candidate on top of the complete Phase 4 result.

Its real graph vertices are:

- whole groups represented as supernodes;
- eligible ungrouped nodes.

Physical group/ungrouped links become directed mixed-graph edges. Existing
group-flow dependencies are also retained so bridge-only group relationships
are not lost. The graph then uses:

1. SCC condensation and longest-path layers;
2. virtual dummy vertices for long mixed edges;
3. weighted median sweeps;
4. weighted local transposition;
5. shared physical layer placement.

The first physical-placement prototype wrapped those layers into
boustrophedon rows. Targeted diagnostics showed that this was structurally
wrong for the strict Phase 5 quality contract:

- Jibs: width improved from 5,977 to 4,697 px, but crossings rose from 756 to
  1,411 and RTL from 41 to 46;
- Flux2 VTON 6.0.2: width improved from 6,723 to 3,331 px, but crossings rose
  from 31 to 62 and RTL from 6 to 13;
- Flux2 VTON 6.0.2.7: width improved from 9,162 to 4,232 px, but crossings rose
  from 44 to 82 and RTL from 11 to 21.

The height also grew substantially, especially for Flux2 VTON 6.0.2.7
(3,510 -> 9,444 px). The mixed dependency graph is therefore retained, but
physical placement now keeps graph layers monotonic from left to right in one
shared vertical band. Graph ordering and physical wrapping are treated as
separate concerns; row wrapping is no longer part of Phase 5 because reversing
alternate rows inherently introduces right-to-left flow and long cross-row
segments.

The first monotonic rerun removed the catastrophic graph regressions and showed
that the graph itself is useful:

- Jibs: 5,977 x 12,448 -> 8,747 x 9,087, crossings 756 -> 475, RTL 41 -> 29.
  Graph quality improved strongly, but the workflow became wider, so the strict
  width gate rejected it.
- Flux2 VTON 6.0.2: 6,723 x 4,861 -> 6,107 x 4,869, crossings 31 -> 41,
  RTL 6 -> 4. Width and area improved, but physical crossings regressed.
- Flux2 VTON 6.0.2.7: 9,162 x 3,510 -> 7,746 x 4,332, crossings 44 -> 36,
  RTL 11 -> 8. Width fell by about 15.5% and graph quality improved, but area
  grew by about 4.3%.

This isolates the remaining problem to physical realization inside the monotonic
layers rather than graph topology. Phase 5 now evaluates a small conservative
candidate matrix over the same mixed graph:

- weighted graph order and baseline-stable vertical order;
- standard vertical gap;
- node vertical gap;
- a compact gap equal to half the node gap, clamped to a 12 px minimum.

With default spacing this yields 100, 80, and 40 px gap variants. The strict
acceptance gate is unchanged; no candidate can trade crossings, RTL, overlaps,
width, or area outside the existing limits.

The targeted multi-variant rerun produced the first result accepted by the
original Phase 5 gate:

- Jibs: the best diagnostic proposal was `stable-gap-100`,
  `5,977 x 12,448 -> 8,747 x 8,895`, port crossings `756 -> 493`, port RTL
  `41 -> 29`. It was correctly rejected because width increased instead of
  falling by at least 8%.
- Flux2 VTON 6.0.2: the best diagnostic proposal was `stable-gap-40`,
  `6,723 x 4,861 -> 6,107 x 4,869`, port crossings `31 -> 40`, port RTL
  `6 -> 4`. It was correctly rejected for port-aware crossing regression.
- Flux2 VTON 6.0.2.7: `weighted-gap-40` was accepted by the port-aware gate,
  `9,162 x 3,510 -> 7,746 x 4,032`, port crossings `44 -> 43`, port RTL
  `11 -> 8`.

The subsequent full 81-workflow Optimize + Layout corpus exposed an important
metric mismatch. The historical corpus reference uses straight center-to-center
node segments, while the v2 engine score uses actual output/input port
positions. The Phase 5 run measured `4,138` center crossings and `331` center
RTL links versus the Phase 4 reference of `4,127 / 339`. In other words, the
port-aware gate allowed an aggregate `+11` regression in the benchmark crossing
metric even though port-aware crossings were locally protected.

Phase 5 therefore protects both geometries. A candidate must not increase
port-aware crossings or RTL links and must also not increase the node-center
crossings or RTL links used by the corpus reporter. The width and area gates are
unchanged. This keeps the engine's more realistic port-aware metric while making
the historical corpus reference an explicit safety constraint.

A group always moves as one rectangle with every member node, preserving its
internal geometry. The established finalizer then re-applies control, text
preview, virtual Set/Get, group-overlap, and pinned-geometry contracts.

The first Phase 5 experiment deliberately skips any workflow that contains
pinned group geometry or pinned ungrouped nodes. It also skips authored
positive-size groups with no assigned nodes because those rectangles are not yet
represented by the mixed graph. This keeps authored pins and unmodeled group
surfaces as hard constraints and excludes Amazing Z-Image from automatic
reorganization.

Phase 5 uses a strict acceptance gate:

- movable overlaps may not increase;
- port-aware crossings may not increase;
- node-center corpus crossings may not increase;
- port-aware RTL links may not increase;
- node-center corpus RTL links may not increase;
- workflow width must fall by at least 8%;
- workflow area may not increase.

Crossing or RTL improvements in either metric do not bypass the width/area
requirements.

Targeted diagnostics are available with:

```powershell
uv run python tools/report_phase5_diagnostics.py example-workflows --optimize `
  --match "Jibs_Ultimate" `
  --match "6.0.2.7" `
  --match "6.0.2 (Codex)"

# Corpus review: print only Phase 5 candidates that pass the complete gate.
uv run python tools/report_phase5_diagnostics.py example-workflows --optimize --accepted-only

# Architecture review: print only workflows where Phase 5 built physical candidates.
uv run python tools/report_phase5_diagnostics.py example-workflows --optimize --attempted-only

# Geometry review for a selected workflow/proposal.
uv run python tools/report_phase5_diagnostics.py example-workflows --optimize `
  --match "Virtual Try-On 6.0 (Codex)" --geometry

# Compare every physical Phase 5 variant for one selected workflow.
uv run python tools/report_phase5_diagnostics.py example-workflows --optimize `
  --match "Reference Identity 1MP + 2x" --variants

# Export one exact diagnostic candidate without changing production selection.
uv run python tools/report_phase5_diagnostics.py example-workflows --optimize `
  --match "Reference Identity 1MP + 2x" `
  --export-variant "weighted-anchoredx-gap-40" `
  --output "phase5-review/Flux2-9B-anchoredx.json"

# Experimental Anchored-X + original Phase 4 Y diagnostic.
uv run python tools/report_phase5_diagnostics.py example-workflows --optimize `
  --match "Reference Identity 1MP + 2x" --variants
uv run python tools/report_phase5_diagnostics.py example-workflows --optimize `
  --match "Reference Identity 1MP + 2x" `
  --export-variant "weighted-anchoredxy" `
  --output "phase5-review/Flux2-9B-anchoredxy.json"
```

## Current Scope Boundary

The active runtime now reaches Phase 5. Phase 4 remains the complete fallback
baseline, and Phase 3 remains underneath it. Bridge, external-source, control,
text-preview, virtual-hub, decorative, and pin-specific contracts are still
authoritative after mixed placement.

Phase 5 is still experimental. The first full 81-workflow corpus run measured
4,138 center crossings / 331 center RTL links, which missed the Phase 4 crossing
reference of 4,127 / 339 by 11 crossings while improving RTL by 8. That run
revealed the port-aware versus center-metric acceptance mismatch described
above.

The dual-metric rerun validated the safety contract:

- Jibs remained rejected for insufficient width reduction even though both
  crossing metrics and both RTL metrics improved.
- Flux2 VTON 6.0.2 remained rejected for port-aware crossing regression; its
  center crossings also regressed from 44 to 52.
- Flux2 VTON 6.0.2.7 was rejected by the new center-crossing gate:
  port crossings improved from 44 to 43 and port RTL from 11 to 8, but center
  crossings regressed from 46 to 60.

The full 81-workflow Optimize + Layout corpus then measured 4,124 center
crossings / 335 center RTL links. This improves the Phase 4 reference of
4,127 / 339 by 3 crossings and 4 RTL links, so the dual-metric Phase 5 gate
passes the corpus safety check.

The accepted-only corpus review found 37 attempted Phase 5 workflows but only
one accepted result: `Flux.1 Kontext dev Virtual Try-On 6.0 (Codex)` with
`stable-gap-80`. Its measured geometry improved width from 6,350 to 5,810 px,
port crossings from 43 to 38, center crossings from 53 to 50, port RTL from 10
to 6, and center RTL from 5 to 1.

Visual inspection still rejected that result. The main flow was numerically
cleaner, but the canvas was visibly fragmented by isolated top-level blocks,
large empty vertical bands, and long cross-canvas wires. This is a false
positive for the current numeric gate: width, area, crossings, and RTL are not
sufficient to protect overall visual cohesion.

The geometry rerun isolated the false positive more precisely. Node geometry
actually improved: node bounds fell from 6,300 x 3,236 to 5,810 x 2,568,
approximate node density rose from 0.136 to 0.186, and the largest node-only
horizontal gap fell from 460 to 300 px. The visible fragmentation instead came
from three authored positive-size groups with no assigned member nodes:
`Prompt`, `Sampler`, and `Reference Image 1`. The Phase 5 mixed graph did
not represent those empty rectangles, but the normal overlap finalizer moved
them downward by 1,128, 608, and 282 px respectively after the modeled flow was
repositioned.

Phase 5 therefore now treats positive-size empty groups as an explicit scope
constraint, just like pinned geometry. Such workflows fall back to the complete
Phase 4 result until mixed placement models those authored group rectangles as
real obstacles/vertices. This is deliberately not a visual-density threshold or
height heuristic: Phase 5 simply does not reorganize geometry that its graph
cannot represent.

The `--geometry` diagnostic remains available for future false positives. It
reports node bounds, approximate node density, largest empty X/Y bands, empty
groups, and the top-level groups/ungrouped nodes that actually moved.

The corpus and Phase 5 diagnostic reporters explicitly configure redirected
stdout/stderr as UTF-8 so Windows PowerShell pipelines do not fall back to
cp1252 when workflow or group names contain Unicode symbols or emoji. Long
local report captures should be written below `logs/`, which is already
gitignored.

### Final Phase 5 safety validation

After the empty-group scope guard, the accepted-only corpus diagnostic reported:

- selected: 81;
- attempted: 14;
- accepted: 0;
- rejected: 14;
- skipped: 67.

The final full 81-workflow Optimize + Layout corpus then measured exactly
`4,127` center crossings and `339` center RTL links, with 0 failures. This is
identical to the Phase 4 reference. Phase 5 is therefore currently a safe
no-op on the corpus: it introduces no structural regression, but it also has no
accepted corpus improvement after the visually invalid candidate was excluded.

Further Phase 5 work should therefore focus on expanding what the mixed graph
can model—most notably authored empty group rectangles—or on improving physical
realization for the remaining attempted workflows. The diagnostic reporter
supports `--attempted-only` and prints aggregate attempted-rejection and skipped
reason counts so the next architecture decision can be based on the dominant
failure mode rather than another global heuristic.

The first attempted-only rerun after the empty-group guard produced 14 physical
candidate workflows:

- 8 rejected for port-aware crossing regression;
- 6 rejected for insufficient final width reduction.

Several width-rejected workflows already improved crossings and RTL materially,
including Qwen VTON v11/v12/v15 and ZIT multi-style. Inspection of the physical
realizer showed a concrete width mechanism: every real vertex in one mixed
dependency layer shared the same X coordinate, and the next layer advanced by
the widest rectangle in the entire previous layer. A single wide group could
therefore push an unrelated narrow parallel chain to the right.

Phase 5 now evaluates an additional `compactx` physical realization for every
existing weighted/stable order and vertical-gap variant. It preserves the same
layer assignment and vertical order, but assigns X per real vertex. Each vertex
is placed only as far right as required by already placed forward predecessors
and vertically overlapping geometry. Unrelated vertical lanes may therefore use
different X positions instead of inheriting the widest rectangle in their
dependency layer. Direct forward dependencies remain left-to-right, and the
complete existing Phase 5 acceptance gate remains authoritative. The original
shared-layer X realization is retained unchanged as a baseline variant.

The first `compactx` corpus rerun produced one numerically accepted candidate:
`Flux.2 klein 9B Reference Identity 1MP + 2x` with
`weighted-compactx-gap-40`. It improved width from 5,810 to 4,850 px, port
crossings from 82 to 78, center crossings from 93 to 92, port RTL from 12 to 10,
and center RTL from 6 to 4 while keeping total workflow area slightly smaller.
Visual inspection still rejected the result because the workflow became
vertically dispersed: FAST MODE, QUALITY MODE, and output regions formed distant
islands connected by long cross-canvas wires. This is a second, distinct false
positive: all represented geometry is valid, but the current gate does not yet
protect visual cohesion or link-span growth.

The optional `--geometry` diagnostic now also reports total Manhattan
port-to-port link length and the longest individual port link before/after the
proposal. On the visually rejected `Flux.2 klein 9B Reference Identity 1MP +
2x` candidate, however, those all-link metrics increased by only about 1.8%
and 1.1% respectively, so they do not explain the severe visual islanding well.

The diagnostic therefore also measures the geometry at the exact abstraction
level Phase 5 moves: real mixed-graph vertices and mixed edges. It reports
mixed-vertex bounds, approximate mixed-vertex density, largest free X/Y bands,
weighted mixed-edge Manhattan length, and the longest mixed edge. This avoids
diluting top-level dispersion with many internal links inside large groups.
These measurements remain diagnostic only; no new acceptance threshold is
introduced until they are compared across the attempted corpus.

The reporter also supports `--variants` for one targeted workflow. It prints
all physical Phase 5 candidates, their existing gate result, and their mixed
edge-length/max-edge metrics. This is intended to distinguish a bad candidate
selection from a missing safety gate: if another already-generated variant
passes the current gate with materially better cohesion, candidate ranking
should be fixed before adding a new rejection rule.

For the visually rejected Flux 9B candidate, the 12-way comparison ruled out a
simple ranking bug. Every shared-layer X variant kept weighted mixed-edge length
roughly flat or slightly lower (`-2.9% .. -0.1%`) but failed the crossing/RTL
safety gate. Every compact-X variant reduced width/RTL more aggressively but
increased weighted mixed-edge length by about `+10% .. +13%`; only
`weighted-compactx-gap-40` passed the current gate. The problem is therefore
the physical X realization itself, not selection among equivalent accepted
candidates.

Phase 5 now evaluates an additional `anchoredx` realization. It starts from
the exact earliest-feasible Compact-X schedule, keeps the same compact right
boundary, direct forward-edge constraints, and horizontal ordering of vertically
overlapping rectangles, then uses available horizontal slack to move vertices
back toward their Phase 4 X positions. This targets weakly constrained branches
that Compact-X otherwise drags all the way to the left while preserving the
compact width envelope. The existing `layer` and `compactx` realizations remain
unchanged for comparison.

On the Flux 9B review case, `weighted-anchoredx-gap-40` also passes the full
existing gate at the same 4,850 x 3,308 size. It keeps center crossings at 92,
has 81 port crossings, and reduces weighted mixed-edge growth from the Compact-X
candidate's +10.3% to only +1.7%. Production selection still prefers Compact-X
because its port crossing/RTL metrics are numerically lower. The diagnostic
reporter can therefore export an exact named variant with `--export-variant`
and `--output` for visual review without changing production candidate ranking.

Visual review of that anchored-X export showed a substantial horizontal
improvement, but still failed overall: QUALITY MODE remained far below the main
workflow, FAST MODE remained below the central region, and long vertical/diagonal
links crossed large empty bands. This isolates the remaining failure to the
vertical realization. Phase 5 still rebuilds Y from a shared `base_y` and
stacks every real vertex in each dependency layer, even when the Phase 4
vertical positions were already coherent.

For diagnosis only, the reporter includes two experimental
`*-anchoredxy` variants. They use the same Anchored-X realization but preserve
the Phase 4 Y coordinate of every real mixed vertex before the normal finalizer.
On the Flux 9B case this exact-Y experiment failed strongly: the weighted
variant expanded to 6,730 x 2,774, raised port crossings from 82 to 105 and
center crossings from 93 to 113, and increased weighted mixed-edge length by
19%. Preserving every original Y coordinate therefore creates too many vertical
overlaps for compact X placement and simply pushes geometry back out horizontally.

A second diagnostic-only Y experiment, `*-anchoredx-yband-gap-*`, preserves
the Phase 5 vertical order and spacing inside each dependency layer but shifts
the entire stacked layer toward its Phase 4 vertical band using the median
per-vertex Y offset. This is a middle ground between rebuilding every layer from
one global `base_y` and restoring every individual Phase 4 Y coordinate. The
Y-band candidates are also excluded from production Phase 5 selection until
their metrics and visual output are reviewed.

The existing safety gates should remain unchanged until a candidate produces
both measurable and visually acceptable improvement.

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
- crossing-first group-level candidate selection;
- shared Group/Ungrouped dependency layering;
- preservation of group-internal geometry during mixed placement;
- strict Phase 5 width/area and crossing/RTL acceptance gates;
- Phase 5 pin-constraint blocking.

## Documentation Status

README.md and CHANGELOG.md remain intentionally unchanged while this branch is
experimental. If corpus validation confirms v2 as the new default, release-facing
documentation and any affected frontend documentation must be updated before the
pull request is marked ready for review.
