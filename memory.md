# Project Notes

## Budget

- Total budget: EUR 1000
- Allocation:
  - Development: 60%
  - Tests: 20%
  - Documentation: 20%

## Current Status

- Python package, CLI, API server, layout algorithm, optimizer, tests, and Vue frontend exist.
- Workflow layout is designed to preserve ComfyUI workflow metadata and update only layout-relevant fields.
- The frontend stores and sends full ComfyUI workflow JSON instead of a separate custom connection format.
- Documentation maintenance is now governed by `AGENTS.md` and `docs/DOCUMENTATION_MAINTENANCE.md`.
- Local workflow validation is repeatable through `uv run python tools/validate_local_workflows.py`.
- Release readiness is repeatable through `uv run python tools/check_release_ready.py`.
- Read-only layout quality reporting is repeatable through `uv run python tools/report_layout_quality.py example-workflows`.
- Layout quality reports include laid-out crossing and right-to-left link-category breakdowns.

## Completed Work

- Implemented the initial layout algorithm for indexed workflow nodes.
- Initialized the Git repository and project structure.
- Added Python package modules under `flowforge/`.
- Added tests under `tests/`.
- Added Vue frontend under `frontend/`.
- Added roundtrip-safe workflow serialization.
- Added metadata-preservation and group-idempotency tests.
- Published the first source-checkout GitHub release as `v0.1.0`.
- Published the second source-checkout GitHub release as `v0.2.0`.
- Added a frontend before/after layout comparison overlay.
- Added GitHub Actions CI for backend, frontend, and fixture workflow validation.
- Pinned uv development and CI parity to Python 3.12 through `.python-version`.
- Added `tools/check_release_ready.py` to run local release gates and optional tag/GitHub verification.
- Validated 95 read-only workflows from `example-workflows` without overwriting originals.
- Added layout quality reporting for real workflow folders.
- Improved linked ungrouped node placement so real-workflow reports show fewer right-to-left links and fewer straight-line crossings in the largest Z-Image outlier.
- Corrected internal layout layer assignment to use deepest dependency paths, reducing right-to-left links in grouped real workflows.
- Refined internal grouped layer ordering with adjacent-layer barycenter sweeps, reducing real-workflow straight-line crossings.

## Future Improvements

- Use the layout-quality report to identify extreme real-workflow layout cases.
- Expand documentation when behavior stabilizes.
- Improve crossing-aware group placement by scoring alternate group orders and column breaks against expected inter-group crossings.
- Add a group-internal aspect-ratio optimizer so large groups can try compact, wide, and tall internal profiles before the final score is chosen.
- Introduce a stable layout mode that keeps existing node positions as much as possible and only moves nodes enough to resolve overlaps and severe outliers.
- Respect native ComfyUI pin state during layout: nodes with `flags.pinned: true`, groups with `flags.pinned: true`, and nodes inside pinned groups should keep their saved geometry. The frontend can toggle node/group pins and clear all pins through classic pin-icon buttons.
- Primitive control nodes such as seed, CFG, string controls, and sampler setup sources such as `EmptyLatentImage` should stay near their downstream consumer or sampler cluster instead of being left in distant source columns. Virtual Set/Get hubs are endpoint adornments, not regular graph nodes: exclude them from group/dataflow placement, keep them near their physical endpoint, prefer side placement when clear, then same-side vertical slots, then above/below placement, then sideways fallback. This applies even if they are inside a pinned group or carry their own pin flag.
- Sampler control stacks must not overlay context nodes. Treat pinned node geometry as a hard obstacle, clear unpinned nodes away from it, and run a final local-anchor pass after clearance so controls and virtual hubs follow the final endpoint positions.
- Set/Get optimization should not rewrite fanouts that touch pinned nodes or nodes inside pinned groups; pinned control panels should keep their visible structure.
- The capped group-row heuristic is not enough for the current optimized SDXL/controlnet-style workflow: the maintainer still sees an overly wide result with `layout only` at `x=y=50`, with less than the desired 25% width reduction. Next work should add a workflow-wide width target or multi-row group scheduler instead of only capping the soft row width.

## Notes

- Local ComfyUI can be used for read-only workflow evidence under `H:\ComfyUI-Easy-Install\ComfyUI`.
- Keep chat communication mostly German.
- Keep code and documentation artifacts in English.
