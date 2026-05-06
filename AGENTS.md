# FlowForge Agents

This file defines repository-local agent behavior for ComfyUI FlowForge.

## Communication And Artifacts

- Use German for chat-facing communication with the maintainer.
- Keep code, comments, docstrings, documentation files, commit messages, and release notes in English.
- Inspect the real working tree before summarizing project status or changing files.
- Do not discard unrelated user changes.

## Documentation Maintenance Agent

The Documentation Maintenance Agent is responsible for keeping every project-facing document accurate whenever behavior, commands, dependencies, workflows, validation, UI behavior, or release state changes.

Use the detailed policy in [docs/DOCUMENTATION_MAINTENANCE.md](docs/DOCUMENTATION_MAINTENANCE.md).

### Required Scope

Check these documentation surfaces on every non-trivial change:

- [README.md](README.md) - user-facing overview, install, commands, behavior, limitations, and project structure.
- [frontend/README.md](frontend/README.md) - frontend setup, data model, scripts, and UI behavior.
- [CLAUDE.md](CLAUDE.md) - maintainer notes, local ComfyUI facts, workflow-format assumptions, and communication rules.
- [memory.md](memory.md) - local planning notes when the maintainer explicitly wants them kept as part of the project record.
- [pyproject.toml](pyproject.toml) and [frontend/package.json](frontend/package.json) - metadata and command surfaces that documentation must match.
- Any future files under `docs/`.

### Documentation Definition Of Done

A change is not complete until the agent has either updated the affected documentation or explicitly recorded that no documentation change was needed.

At minimum, verify:

```powershell
uv run pytest
uv run ruff check .
uv run mypy flowforge
npm run typecheck
npm run build
```

If a command cannot be run, record the blocker and the affected documentation risk in the final handoff.

## Git Operations Agent

The Git Operations Agent owns routine repository hygiene and commit preparation.

### Responsibilities

- Inspect `git status --short --branch` before and after changes.
- Identify tracked files that should become ignored local artifacts.
- Keep `.gitignore` aligned with generated files, caches, build outputs, private workflows, local ComfyUI data, and editor/tool state.
- Stage only files that belong to the current task.
- Review the staged diff before commit.
- Commit with a focused English message.

### Never Do Without Explicit Maintainer Request

- `git reset --hard`
- `git clean`
- Force pushes
- Branch deletion
- Tag deletion
- History rewrite
- Reverting unrelated user work

### Current Local Artifact Policy

Do not track private or local workflow material:

- `example-workflows/`
- `local-workflows/`
- `private-workflows/`
- `*.private.json`
- `*.local.json`
- `*_layouted.json`

Do not track generated runtime or tooling output:

- Python caches and virtual environments
- `logs/`
- frontend `node_modules/`, `dist/`, and Vite caches
- local editor or agent-tool state such as `.kilo/`
