# Changelog

All notable changes to ComfyUI FlowForge are documented here.

## [Unreleased]

### Added

- Release-readiness checker for local gates, workflow validation, tags, GitHub Releases, and GitHub Actions.
- Read-only layout quality report for real workflow folders.
- Link-category breakdowns in read-only layout quality reports.

### Changed

- Arrange linked ungrouped nodes by dataflow layers to reduce long right-to-left wires in real workflows.
- Correct internal layout layer assignment to use the deepest dependency path.
- Refine internal layer ordering with adjacent-layer barycenter sweeps to reduce grouped wire crossings.

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
