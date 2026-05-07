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

## Completed Work

- Implemented the initial layout algorithm for indexed workflow nodes.
- Initialized the Git repository and project structure.
- Added Python package modules under `flowforge/`.
- Added tests under `tests/`.
- Added Vue frontend under `frontend/`.
- Added roundtrip-safe workflow serialization.
- Added metadata-preservation and group-idempotency tests.

## Open Work

- Keep validating layout behavior against more real ComfyUI workflows before release tags.
- Improve visual comparison in the frontend.
- Exercise the first tagged GitHub release using the source-checkout release path.
- Expand documentation when behavior stabilizes.

## Notes

- Local ComfyUI can be used for read-only workflow evidence under `H:\ComfyUI-Easy-Install\ComfyUI`.
- Keep chat communication mostly German.
- Keep code and documentation artifacts in English.
