# Packaging

FlowForge still supports source-checkout development first, but the GUI can now run from packaged frontend assets when they are present inside the Python package.

## Frontend Asset Strategy

The GUI launcher resolves frontend assets in this order:

1. `flowforge/frontend_dist` inside the installed Python package.
2. `frontend/dist` in a source checkout.
3. The Vite development server when no built frontend is available.

This keeps local development fast while allowing package and release builds to run without a live Vite server.

## Build Package Assets

From the repository root:

```powershell
uv run python tools/build_package_assets.py
```

The script runs `npm run build` in `frontend/`, then copies `frontend/dist` into `flowforge/frontend_dist` for packaging. If `frontend/dist` already exists and should be reused:

```powershell
uv run python tools/build_package_assets.py --skip-build
```

`flowforge/frontend_dist` is generated and ignored by Git. Do not commit generated frontend assets unless the release policy changes.

## Python Package Build

After staging frontend assets:

```powershell
uv build
```

The package metadata includes `flowforge/frontend_dist/**/*`, so wheels and source distributions built after asset staging can serve the GUI directly from installed package files.

## Deferred Distribution Decisions

Before publishing outside GitHub source checkouts, decide:

- Whether the first public package target is PyPI, the ComfyUI Registry, or both.
- Whether release artifacts should include prebuilt wheels, source distributions, or a simple source archive.
- Which compatibility policy applies to ComfyUI workflow JSON versions and optional custom-node dependencies.
