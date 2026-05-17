# Changelog

All notable changes to ComfyUI FlowForge are documented here.

## [Unreleased]

### Added

- Release-readiness checker for local gates, workflow validation, tags, GitHub Releases, and GitHub Actions.
- Read-only layout quality report for real workflow folders.
- Link-category breakdowns in read-only layout quality reports.
- Frontend toolbar action for running the Set/Get optimizer and layout as one cleanup flow.
- Optional Set/Get optimizer mode for read-only layout quality reports.

### Changed

- Arrange linked ungrouped nodes by dataflow layers to reduce long right-to-left wires in real workflows.
- Correct internal layout layer assignment to use the deepest dependency path.
- Refine internal layer ordering with adjacent-layer barycenter sweeps to reduce grouped wire crossings.
- Compact saved node sizes before layout, keep long text/widget nodes tall enough for their visible content, and shrink group rectangles around optimized group-local node layouts.
- Stack variable-height nodes cumulatively inside group-local layers so compact layouts do not overlap nodes in the same layer.
- Use virtual Set/Get pairing without physical `SetNode -> GetNode` links when optimizing high-fanout wiring.

### Fixed

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

[Unreleased]: https://github.com/Delcado19/comfyui-flowforge/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Delcado19/comfyui-flowforge/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Delcado19/comfyui-flowforge/releases/tag/v0.1.0
