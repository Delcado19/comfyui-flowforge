# Optimizer Analysis Notes

Maintainer reference for the Set/Get fanout optimizer and its interaction with
layout quality. The corpus figures below are point-in-time measurements over
`example-workflows`; reproduce them before relying on exact numbers:

```powershell
uv run python tools/report_layout_quality.py example-workflows --top 12
uv run python tools/report_layout_quality.py example-workflows --optimize --top 12
```

## Optimizer Corpus Analysis

Goal: find general optimizer improvements (especially high-connection nodes and
Checkpoint Loader handling), not workflow-specific rules.

Corpus summary:

- Discovered UI workflows: 130.
- Failures: 0.
- Layout-only aggregate:
  - nodes: 5644
  - links: 6502
  - straight-line crossings: 7008 -> 9117
  - right-to-left links: 744 -> 421
- Optimize + Layout aggregate:
  - nodes: 5644 -> 6365
  - links: 6502 -> 6689
  - straight-line crossings: 7008 -> 5305
  - right-to-left links: 744 -> 444
- Per-workflow comparison, layout-only vs Optimize + Layout:
  - crossing improvements: 69 workflows.
  - crossing regressions: 1 workflow.
  - unchanged crossings: 60 workflows.
  - RTL improvements: 4 workflows.
  - RTL regressions: 19 workflows.
  - unchanged RTL: 107 workflows.

The optimizer improves the aggregate crossing count heavily, but it is not
monotonic per workflow. RTL links slightly increase in the aggregate after
optimization.

## High-Fanout Findings

Analysis over effective fanout, following reroute chains:

- fanout ports with 2+ terminal consumers: 1412.
- fanout ports with 2+ consumers touching currently optimized types (`MODEL`, `CLIP`, `VAE`): 400.
- currently rewritable fanout ports: 146.
- currently rewritable long-single optimized ports: 45.
- actual optimizer corpus delta:
  - nodes +721
  - links +187
  - SetNode +189
  - GetNode +534

Reasons for 2+ fanout ports:

- `type_not_optimized`: 1012 ports, aggregate wire cost 3113268.
- `transformer_target`: 159 ports, aggregate wire cost 480351.
- `rewritable`: 146 ports, aggregate wire cost 1657272.
- `pinned_geometry`: 53 ports, aggregate wire cost 593996.
- `cost_not_beneficial`: 42 ports, aggregate wire cost 39062.

Top source types by fanout port count:

- `Reroute`: 104 ports, aggregate wire cost 621368.
- `AILab_ImageResize`: 92 ports, aggregate wire cost 199535.
- `GetImageSize`: 89 ports, aggregate wire cost 104617.
- `Power Lora Loader (rgthree)`: 84 ports, aggregate wire cost 593015.
- `VAEEncodeTiled`: 81 ports, aggregate wire cost 149791.
- `CLIPLoaderGGUF`: 76 ports, aggregate wire cost 230178.
- `VAELoader`: 62 ports, aggregate wire cost 1103034.
- `GrowMask`: 62 ports, aggregate wire cost 187699.
- `VAEDecodeTiled`: 56 ports, aggregate wire cost 391029.
- `CLIPTextEncode`: 47 ports, aggregate wire cost 227441.

Terminal link types across fanout ports:

- `IMAGE`: 866 terminal links.
- `VAE`: 441 terminal links.
- `CLIP`: 393 terminal links.
- `MODEL`: 310 terminal links.
- `INT`: 278 terminal links.
- `CONDITIONING`: 268 terminal links.
- `LATENT`: 236 terminal links.
- `MASK`: 227 terminal links.
- `STRING`: 163 terminal links.

Important interpretation: most high-fanout clutter is outside the current
optimizer allowlist. The current optimizer is intentionally narrow (`MODEL`,
`CLIP`, `VAE`) and misses large fanouts for `IMAGE`, `CONDITIONING`, `LATENT`,
`MASK`, `INT`, `STRING`, and custom types.

## Checkpoint Loader Handling

The optimizer is not node-name based. It considers any traversed link of type
`MODEL`, `CLIP`, or `VAE`, so Checkpoint Loader nodes are included through their
output link types.

Checkpoint-related optimizer observations:

- optimized-typed checkpoint output ports observed: 36.
- by node type:
  - `CheckpointLoaderSimple`: 33.
  - `CheckpointLoader|pysssss`: 3.
- by terminal type:
  - `VAE`: 27.
  - `CLIP`: 16.
  - `MODEL`: 15.
- by decision:
  - `rewritable`: 6.
  - `below_threshold`: 13.
  - `transformer_target`: 13.
  - `cost_not_beneficial`: 4.

Examples:

- `SDXL 1.0\TooReal Studio - SDXL screenshot to realistic human.json`
  - `CheckpointLoaderSimple` node `4:2`, VAE fanout 6, rewritable.
  - Optimize delta: nodes 26 -> 33, links 42 -> 43, Set +1, Get +6.
  - Layout crossings 43 -> 17 with Optimize + Layout.
- `SDXL 1.0\SDXL 1.0 - Simple 2-step Workflow.json`
  - Optimize delta: nodes 24 -> 34, links 34 -> 37, Set +3, Get +7.
  - Rewrites came from `Context Switch (rgthree)` VAE fanout and `Power Lora Loader (rgthree)` MODEL/CLIP fanouts, not directly from the Checkpoint Loader.
  - Layout crossings 29 -> 14 with Optimize + Layout.
- `SDXL 1.0\TooReal Studio - SDXL screenshot to realistic human-layouted.json`
  - `CheckpointLoaderSimple` node `4:2`, VAE fanout 6, rewritable.
  - Layout crossings 85 -> 39 with Optimize + Layout, but RTL 4 -> 5 and height grew.

Conclusion: Checkpoint Loader nodes are covered, but only when their typed
outputs satisfy the same general optimizer gates. There is no missing Checkpoint
Loader special-case.

## Notable Regressions And What-If

The only crossing regression from Optimize + Layout:

- `Z-Image Turbo\Z-Image Base - Ultra Workflow V2 [Aitrepreneur].json`
  - crossings 10 -> 26.
  - RTL 16 -> 16.
  - nodes 182 -> 191, links 141 -> 144.
  - Set +3, Get +6.
  - The suspicious candidates are small 2-consumer `CLIP` rewrites on `Power Lora Loader (rgthree)` outputs with low savings, while larger non-optimized `IMAGE` fanouts remain untouched.

Global minimum-savings what-if, compared to layout-only:

- `min_savings=0`: crossings 5305, RTL 444, crossing regressions 1, Set +189, Get +534.
- `min_savings=250`: crossings 5309, RTL 442, crossing regressions 1, Set +183, Get +522.
- `min_savings=500`: crossings 5325, RTL 443, crossing regressions 1, Set +181, Get +519.
- `min_savings=1000`: crossings 5327, RTL 443, crossing regressions 1, Set +179, Get +515.
- `min_savings=2000`: crossings 5439, RTL 441, crossing regressions 0, Set +164, Get +483.

Interpretation: a blunt global savings threshold can remove the one crossing
regression at around 2000, but it also gives up some aggregate crossing
improvement. This is a possible safety gate, but not obviously the best final
approach.

## Likely Next Work

Prefer general optimizer improvements, not workflow-specific rules:

1. Add optimizer analysis/regression tests for edge cases:
   - CheckpointLoaderSimple VAE fanout is rewritten when beneficial.
   - CheckpointLoaderSimple CLIP fanout is skipped when the cost model says it is not beneficial.
   - CheckpointLoaderSimple MODEL fanout into LoRA/pass-through transformer is skipped.
   - pinned source/consumer/group still blocks generated hubs.
   - low-savings two-target fanouts do not regress global layout quality.
   - non-optimized high-fanout types are documented/covered so behavior is explicit.
2. Consider extending the optimizer from a fixed `MODEL`/`CLIP`/`VAE` allowlist toward a configurable or guarded allowlist for additional high-clutter types, especially `IMAGE`, `CONDITIONING`, `LATENT`, and `MASK`.
   - Do not assume all types are safe until Set/Get runtime semantics are verified.
   - If expanded, keep transformer and pinned-geometry guards.
3. Consider a conservative rewrite gate that combines local savings with fanout/group span, not only raw wire cost. The `min_savings=2000` what-if is useful evidence but too blunt by itself.
4. Consider enhancing `tools/report_layout_quality.py` with optimizer-candidate diagnostics so future analysis does not require ad hoc inline scripts.

## Layout Overlap Notes

When auditing node overlaps across the corpus, separate pinned from movable
nodes first: the large majority of corpus overlaps involve **pinned** nodes,
which is by design (pinned nodes keep their saved position even if authored
overlapping). Only movable (unpinned, ungrouped) overlaps are real layout bugs.
Filter on the pinned flag and count only movable pairs.
