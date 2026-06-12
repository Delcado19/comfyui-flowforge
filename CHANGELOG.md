# Changelog

All notable changes to ComfyUI FlowForge are documented here.

## [Unreleased]

### Changed

- Keep `LoadImage*`, `SaveImage*`, `Note`, `MarkdownNote`, and label nodes at their saved ComfyUI size instead of compacting preview, save-control, or annotation nodes to the generic minimum rectangle.
- Treat pinned group rectangles as fixed layout obstacles for movable nodes, local control stacks, virtual Set/Get hubs, and movable groups so layout cleanup does not cover pinned control areas.
- Prefer layout candidates with fewer straight-line crossings when compactness candidates otherwise compete.
- Use per-layer internal group column widths so one large preview or save node does not force every group column to the same width.
- Wrap long internal group layer chains into additional rows so compact refinement groups use vertical space instead of becoming wide strips.
- Place connected groups in inter-group dataflow columns, follow eligible bridge paths through ungrouped nodes for movable groups, and use per-layer widths for linked ungrouped columns, reducing stretched layouts caused by large source or preview nodes.
- Keep ungrouped bridge nodes between their movable source and target group columns, reserve enough compact spacing for them, and align them to the connected ports instead of the target group's visual center.
- Wrap groups into capped-width rows before the workflow becomes a long horizontal strip, reducing overly wide layouts when vertical space is available.
- Reserve group title/header clearance in backend bounds and frontend rendering so nodes do not intrude into group headers.
- Pull ungrouped source nodes beside the grouped blocks they feed so loader and model-source nodes no longer widen the workflow from the far right with right-to-left links.
- Allow existing groups to be renamed from the canvas group title bar.

## [0.2.1] - 2026-05-25

### Added

- Release-readiness checker for local gates, workflow validation, tags, GitHub Releases, and GitHub Actions.
- Read-only layout quality report for real workflow folders.
- Link-category breakdowns in read-only layout quality reports.
- Frontend toolbar action for running the Set/Get optimizer and layout as one cleanup flow.
- Pin buttons for nodes and groups plus a canvas toolbar action to unpin every pinned node and group.
- Optional Set/Get optimizer mode for read-only layout quality reports.
- Local regression coverage for the `example-workflows` corpus, including layout roundtrip checks and Optimize + Layout aggregate quality budgets when the corpus is present.

### Changed

- Arrange linked ungrouped nodes by dataflow layers to reduce long right-to-left wires in real workflows.
- Correct internal layout layer assignment to use the deepest dependency path.
- Refine internal layer ordering with adjacent-layer barycenter sweeps to reduce grouped wire crossings.
- Respect ComfyUI `flags.pinned: true` on nodes and groups so pinned control areas keep their saved position and size during layout.
- Keep primitive control nodes such as seed, CFG, and text/string controls beside their downstream consumer or sampler cluster instead of leaving them in distant source columns.
- Keep sampler setup sources such as `EmptyLatentImage` close to the sampler input they feed.
- Move virtual Set/Get hubs sideways when their ideal port-adjacent position would cover an existing node.
- Prefer same-side vertical fallback slots for virtual Set/Get hubs before moving them horizontally across the workflow.
- Let unpinned virtual Set/Get hubs follow their physical endpoint even if their old position placed them inside a pinned group.
- Treat virtual Set/Get hubs as endpoint anchors during layout even when their own ComfyUI pin flag is set.
- Exclude virtual Set/Get hubs from regular group and dataflow placement so they behave as local source/destination adornments instead of graph nodes.
- Place sampler control stacks, such as seed/CFG/latent setup nodes, in collision-free local slots around their endpoint instead of blindly overlaying nearby context nodes.
- Move unpinned nodes out from under pinned node geometry and re-anchor local controls and virtual hubs after that clearance pass.
- Keep grouped control nodes inside their own group when their consumers are external, preventing loader/control groups from stretching across the workflow.
- Start a new group column for very wide groups instead of stacking them under narrow groups.
- Skip Set/Get fanout rewrites when the source or any terminal consumer is pinned or inside a pinned group, keeping pinned control areas visually stable.
- Skip upstream Set/Get rewrites into LoRA or same-type pass-through transformer nodes and rewrite long single cross-group `MODEL`/`CLIP`/`VAE` links when the Set/Get pair is cheaper.
- Keep debug previews and prompt switch/control side branches from forcing extra group-layout columns.
- Compact saved node sizes before layout, keep long text/widget nodes tall enough for their visible content, and shrink group rectangles around optimized group-local node layouts.
- Stack variable-height nodes cumulatively inside group-local layers so compact layouts do not overlap nodes in the same layer.
- Use virtual Set/Get pairing without physical `SetNode -> GetNode` links when optimizing high-fanout wiring.
- Keep optimized `SetNode` and `GetNode` hubs beside their physical source/consumer ports and in the endpoint's group after layout.

### Fixed

- Move whole unpinned groups after compact bounding-box updates when group surfaces would overlap another group or a node outside the group.
- Match the compact node-height formula to the canvas renderer by stacking slot inputs and widget rows instead of taking their maximum, so laid-out nodes no longer overlap their neighbours when widgets push real render height past the reserved slot.
- Place linked ungrouped nodes to the right of every positioned group instead of dropping them into the group's first column, eliminating systematic horizontal overlaps between ungrouped and grouped nodes.

## [0.2.0] - 2026-05-08

### Added

- GitHub Actions CI for backend tests, Ruff, Mypy, fixture workflow validation, frontend typecheck, and frontend build.
- Before/after layout comparison overlay in the frontend.
- Regression coverage for the GUI launcher's frontend development server command.
- Packaged frontend asset support for Python distribution builds.
- GUI smoke coverage for built frontend serving and API proxying.
- An anonymized realistic ComfyUI UI workflow fixture with groups, reroute links, notes, subgraph metadata, and unknown fields.

### Changed

- Pinned local development and CI parity to Python 3.12.
- Fixed fixture workflow link IDs so local validation and CI reject duplicate ComfyUI link identifiers.
- Run frontend dev and build scripts through the Vite JavaScript API with local config so builds do not inherit unrelated parent TypeScript configuration.
- Renamed the comparison toolbar action to `Before/After` for clearer previous-layout toggling.

## [0.1.0] - 2026-05-07

### Added

- Initial alpha source-checkout release.
- Python CLI and API server for layout and optimization.
- Vue workflow canvas with pan, zoom, minimap, group editing, live layout spacing controls, and workflow save support.
- Metadata-preserving ComfyUI UI workflow roundtrip serialization.
- Cost-based optimizer for high-fanout `MODEL`, `CLIP`, and `VAE` links.
- Local workflow validation tooling for ComfyUI UI workflow JSON.
- Release and documentation maintenance notes.

[Unreleased]: https://github.com/Delcado19/comfyui-flowforge/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/Delcado19/comfyui-flowforge/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Delcado19/comfyui-flowforge/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Delcado19/comfyui-flowforge/releases/tag/v0.1.0
