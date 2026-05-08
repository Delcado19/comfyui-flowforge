"""Tests for the FlowForge release-readiness checker."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Sequence

from tools.check_release_ready import ReleaseCheckOptions, run_release_checks


class FakeRunner:
    def __init__(
        self,
        failures: dict[tuple[str, ...], str] | None = None,
        stdout: dict[tuple[str, ...], str] | None = None,
    ) -> None:
        self.failures = failures or {}
        self.stdout = stdout or {}
        self.commands: list[tuple[tuple[str, ...], Path]] = []

    def __call__(self, command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        key = tuple(command)
        self.commands.append((key, cwd))
        if key in self.failures:
            return subprocess.CompletedProcess(key, 1, "", self.failures[key])
        if key in self.stdout:
            return subprocess.CompletedProcess(key, 0, self.stdout[key], "")
        if key == ("git", "status", "--porcelain"):
            return subprocess.CompletedProcess(key, 0, "", "")
        if key == ("git", "tag", "--list", "v1.2.3"):
            return subprocess.CompletedProcess(key, 0, "v1.2.3\n", "")
        if key == ("git", "rev-parse", "HEAD"):
            return subprocess.CompletedProcess(key, 0, "abc123\n", "")
        if key == ("git", "ls-remote", "origin", "refs/heads/master", "refs/tags/v1.2.3"):
            return subprocess.CompletedProcess(
                key,
                0,
                "abc123\trefs/heads/master\nabc123\trefs/tags/v1.2.3\n",
                "",
            )
        if key == ("gh", "release", "view", "v1.2.3"):
            return subprocess.CompletedProcess(key, 0, "title:\tv1.2.3\n", "")
        if key[:4] == ("gh", "run", "list", "--limit"):
            return subprocess.CompletedProcess(
                key,
                0,
                json.dumps(
                    [
                        {
                            "databaseId": 1,
                            "headBranch": "master",
                            "headSha": "abc123",
                            "status": "completed",
                            "conclusion": "success",
                        },
                        {
                            "databaseId": 2,
                            "headBranch": "v1.2.3",
                            "headSha": "abc123",
                            "status": "completed",
                            "conclusion": "success",
                        },
                    ]
                ),
                "",
            )
        return subprocess.CompletedProcess(key, 0, "ok\n", "")


def test_release_checks_run_local_gates_and_workflow_validation():
    runner = FakeRunner()

    results = run_release_checks(ReleaseCheckOptions(workflow_roots=(Path("tests/fixtures"),)), runner=runner)

    assert all(result.ok for result in results)
    commands = [command for command, _ in runner.commands]
    assert ("uv", "sync", "--dev", "--frozen") in commands
    assert ("uv", "run", "pytest") in commands
    assert ("uv", "run", "ruff", "check", ".") in commands
    assert ("uv", "run", "mypy", "flowforge") in commands
    assert any(
        command[:4] == ("uv", "run", "python", "tools/validate_local_workflows.py")
        and Path(command[4]) == Path("tests/fixtures")
        for command in commands
    )


def test_release_checks_fail_dirty_tree_by_default():
    runner = FakeRunner(stdout={("git", "status", "--porcelain"): " M pyproject.toml\n"})

    results = run_release_checks(ReleaseCheckOptions(), runner=runner)

    assert not results[0].ok
    assert results[0].name == "git working tree"


def test_release_checks_verify_github_state_for_tag():
    runner = FakeRunner()

    results = run_release_checks(ReleaseCheckOptions(tag="v1.2.3", github=True), runner=runner)

    assert all(result.ok for result in results)
    result_names = {result.name for result in results}
    assert "local tag" in result_names
    assert "remote refs" in result_names
    assert "GitHub Release" in result_names
    assert "GitHub Actions (master)" in result_names
    assert "GitHub Actions (v1.2.3)" in result_names
