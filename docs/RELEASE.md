# Release And Deployment

FlowForge is currently a source-install alpha project. The supported deployment path is a local checkout run with `uv`; PyPI packaging, ComfyUI Registry packaging, and binary installers are not defined yet.

## Local Deployment

Use a source checkout on the target machine:

```powershell
git clone https://github.com/Delcado19/comfyui-flowforge.git
cd comfyui-flowforge
uv sync
uv run flowforge-gui
```

The GUI launcher starts the Python API and Vite frontend together. For command-line use, run `uv run flowforge layout input.json output.json` or `uv run flowforge optimize input.json output.json`.

## Release Scope

A release should include:

- A clean Git working tree.
- Updated version metadata in `pyproject.toml`.
- Updated release notes in `CHANGELOG.md`.
- Updated user-facing documentation when behavior, commands, limitations, dependencies, or validation requirements changed.
- Passing backend, frontend, and GitHub Actions validation gates.
- Frontend package assets staged with `tools/build_package_assets.py` when building a Python distribution.
- A focused Git tag and matching GitHub Release.

## Pre-Release Checklist

Run these checks from the repository root unless noted:

```powershell
git status --short --branch
uv sync --dev --frozen
uv run pytest
uv run ruff check .
uv run mypy flowforge
cd frontend
npm run typecheck
npm run build
cd ..
```

The same local gate set is available as one command:

```powershell
uv run python tools/check_release_ready.py
```

When a release changes workflow parsing, layout, optimizer behavior, or serialization, also run a read-only sanity check against local ComfyUI workflow JSON before tagging.

```powershell
uv run python tools/validate_local_workflows.py
```

## Publish Checklist

Use an annotated version boundary in Git and GitHub:

```powershell
git status --short --branch
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin master
git push origin vX.Y.Z
gh release create vX.Y.Z --title "vX.Y.Z" --notes "Release notes for vX.Y.Z."
gh run list --limit 5
```

After publishing the tag and GitHub Release, run the remote release gate:

```powershell
uv run python tools/check_release_ready.py --tag vX.Y.Z --github
```

Use the matching `CHANGELOG.md` entry as the source for concise GitHub release notes before publishing. Treat the local tag, remote tag, GitHub Release, and GitHub Actions status as separate release states that each need verification. CI runs on `master`, pull requests, and `v*` tags; wait for the relevant run to finish before treating a release as complete.

## Package Asset Checklist

When building Python release artifacts, prepare frontend assets before `uv build`:

```powershell
uv run python tools/build_package_assets.py
uv build
```

The generated `flowforge/frontend_dist` directory is ignored by Git and should be treated as a build artifact.

## Deferred Packaging Work

Before publishing outside source installs, define:

- Whether FlowForge should ship on PyPI, the ComfyUI Registry, or both.
- Which release artifacts should be published for each target.
- Which compatibility policy applies to ComfyUI workflow JSON versions and custom-node dependencies.
