"""Build and stage frontend assets for Python packaging."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"
PACKAGE_FRONTEND_DIST = ROOT / "flowforge" / "frontend_dist"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the Vue frontend and copy dist files into the Python package tree.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Copy an existing frontend/dist directory without running npm run build.",
    )
    args = parser.parse_args()

    if not args.skip_build:
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if npm is None:
            raise FileNotFoundError("npm was not found on PATH")
        subprocess.run([npm, "run", "build"], cwd=FRONTEND_DIR, check=True)

    index_file = FRONTEND_DIST / "index.html"
    if not index_file.exists():
        raise FileNotFoundError(f"Missing built frontend entrypoint: {index_file}")

    if PACKAGE_FRONTEND_DIST.exists():
        shutil.rmtree(PACKAGE_FRONTEND_DIST)
    shutil.copytree(FRONTEND_DIST, PACKAGE_FRONTEND_DIST)
    print(f"Copied frontend assets to {PACKAGE_FRONTEND_DIST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
