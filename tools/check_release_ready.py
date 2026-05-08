"""Run FlowForge release-readiness checks."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend"
DEFAULT_WORKFLOW_ROOT = ROOT / "tests" / "fixtures"

CommandRunner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class CheckResult:
    """A single release-readiness check result."""

    name: str
    ok: bool
    message: str


@dataclass(frozen=True)
class ReleaseCheckOptions:
    """Configuration for release-readiness checks."""

    workflow_roots: tuple[Path, ...] = (DEFAULT_WORKFLOW_ROOT,)
    include_layouted: bool = False
    tag: str | None = None
    github: bool = False
    allow_dirty: bool = False


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FlowForge release-readiness checks.")
    parser.add_argument(
        "--workflow-root",
        action="append",
        type=Path,
        dest="workflow_roots",
        help=f"Workflow root to validate. Can be repeated. Default: {DEFAULT_WORKFLOW_ROOT}",
    )
    parser.add_argument(
        "--include-layouted",
        action="store_true",
        help="Include generated *_layouted.json workflow files in validation.",
    )
    parser.add_argument("--tag", help="Release tag to verify locally, for example v0.2.0.")
    parser.add_argument(
        "--github",
        action="store_true",
        help="Verify remote refs, GitHub Release, and GitHub Actions. Requires --tag.",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Do not fail when the working tree has tracked or untracked changes.",
    )
    args = parser.parse_args()

    if args.github and not args.tag:
        parser.error("--github requires --tag")

    workflow_roots = tuple(args.workflow_roots) if args.workflow_roots else (DEFAULT_WORKFLOW_ROOT,)
    options = ReleaseCheckOptions(
        workflow_roots=workflow_roots,
        include_layouted=args.include_layouted,
        tag=args.tag,
        github=args.github,
        allow_dirty=args.allow_dirty,
    )

    results = run_release_checks(options)
    for result in results:
        status = "OK" if result.ok else "FAIL"
        print(f"[{status}] {result.name}: {result.message}")

    return 0 if all(result.ok for result in results) else 1


def run_release_checks(
    options: ReleaseCheckOptions,
    *,
    runner: CommandRunner | None = None,
) -> list[CheckResult]:
    """Run all configured release-readiness checks."""
    if runner is None:
        runner = _run_command

    results: list[CheckResult] = []

    results.append(_check_working_tree_clean(options, runner))
    results.extend(
        [
            _check_command("uv sync", ["uv", "sync", "--dev", "--frozen"], ROOT, runner),
            _check_command("pytest", ["uv", "run", "pytest"], ROOT, runner),
            _check_command("ruff", ["uv", "run", "ruff", "check", "."], ROOT, runner),
            _check_command("mypy", ["uv", "run", "mypy", "flowforge"], ROOT, runner),
            _check_command("frontend typecheck", [_npm(), "run", "typecheck"], FRONTEND_DIR, runner),
            _check_command("frontend build", [_npm(), "run", "build"], FRONTEND_DIR, runner),
        ]
    )

    for workflow_root in options.workflow_roots:
        command = ["uv", "run", "python", "tools/validate_local_workflows.py", str(workflow_root)]
        if options.include_layouted:
            command.append("--include-layouted")
        results.append(_check_command(f"workflow validation ({workflow_root})", command, ROOT, runner))

    if options.tag is not None:
        results.append(_check_local_tag(options.tag, runner))

    if options.github:
        assert options.tag is not None
        results.extend(_check_github_state(options.tag, runner))

    return results


def _check_working_tree_clean(options: ReleaseCheckOptions, runner: CommandRunner) -> CheckResult:
    completed = runner(["git", "status", "--porcelain"], ROOT)
    if completed.returncode != 0:
        return _failed_command_result("git working tree", completed)
    if completed.stdout.strip() and not options.allow_dirty:
        return CheckResult("git working tree", False, "working tree has changes")
    if completed.stdout.strip():
        return CheckResult("git working tree", True, "changes allowed by --allow-dirty")
    return CheckResult("git working tree", True, "clean")


def _check_command(
    name: str,
    command: Sequence[str],
    cwd: Path,
    runner: CommandRunner,
) -> CheckResult:
    completed = runner(command, cwd)
    if completed.returncode != 0:
        return _failed_command_result(name, completed)
    return CheckResult(name, True, "passed")


def _check_local_tag(tag: str, runner: CommandRunner) -> CheckResult:
    completed = runner(["git", "tag", "--list", tag], ROOT)
    if completed.returncode != 0:
        return _failed_command_result("local tag", completed)
    if completed.stdout.strip() != tag:
        return CheckResult("local tag", False, f"missing {tag}")
    return CheckResult("local tag", True, tag)


def _check_github_state(tag: str, runner: CommandRunner) -> list[CheckResult]:
    results: list[CheckResult] = []

    head = runner(["git", "rev-parse", "HEAD"], ROOT)
    if head.returncode != 0:
        results.append(_failed_command_result("current commit", head))
        return results
    head_sha = head.stdout.strip()

    remote_refs = runner(["git", "ls-remote", "origin", "refs/heads/master", f"refs/tags/{tag}"], ROOT)
    if remote_refs.returncode != 0:
        results.append(_failed_command_result("remote refs", remote_refs))
    else:
        output = remote_refs.stdout
        missing = [
            ref
            for ref in ("refs/heads/master", f"refs/tags/{tag}")
            if ref not in output
        ]
        if missing:
            results.append(CheckResult("remote refs", False, f"missing {', '.join(missing)}"))
        else:
            results.append(CheckResult("remote refs", True, "master and tag found"))

    release = runner(["gh", "release", "view", tag], ROOT)
    if release.returncode != 0:
        results.append(_failed_command_result("GitHub Release", release))
    else:
        results.append(CheckResult("GitHub Release", True, tag))

    runs = runner(
        [
            "gh",
            "run",
            "list",
            "--limit",
            "30",
            "--json",
            "status,conclusion,headBranch,headSha,databaseId",
        ],
        ROOT,
    )
    if runs.returncode != 0:
        results.append(_failed_command_result("GitHub Actions", runs))
    else:
        results.extend(_check_actions_json(runs.stdout, head_sha, tag))

    return results


def _check_actions_json(output: str, head_sha: str, tag: str) -> list[CheckResult]:
    try:
        runs = json.loads(output)
    except json.JSONDecodeError as exc:
        return [CheckResult("GitHub Actions", False, f"invalid JSON: {exc}")]

    results: list[CheckResult] = []
    for ref in ("master", tag):
        matching = [
            run
            for run in runs
            if run.get("headSha") == head_sha
            and run.get("headBranch") == ref
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
        ]
        if matching:
            results.append(CheckResult(f"GitHub Actions ({ref})", True, "completed success"))
        else:
            results.append(CheckResult(f"GitHub Actions ({ref})", False, "no successful completed run found"))
    return results


def _failed_command_result(name: str, completed: subprocess.CompletedProcess[str]) -> CheckResult:
    message = (completed.stderr or completed.stdout or f"exit code {completed.returncode}").strip()
    return CheckResult(name, False, message.splitlines()[-1] if message else f"exit code {completed.returncode}")


def _run_command(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def _npm() -> str:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm is None:
        return "npm"
    return npm


if __name__ == "__main__":
    sys.exit(main())
