#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if ! command -v uv >/dev/null 2>&1; then
    echo "FlowForge requires uv, but uv was not found on PATH." >&2
    echo "Install uv from https://docs.astral.sh/uv/ and try again." >&2
    exit 1
fi

echo "Starting ComfyUI FlowForge..."
exec uv run flowforge-gui
