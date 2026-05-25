"""Regression coverage for the repository's bundled ComfyUI workflow corpus."""

from pathlib import Path

import pytest

from flowforge.layout_quality import WorkflowQualitySummary, build_quality_summary
from flowforge.workflow_validation import WorkflowValidationSummary, validate_workflow_files


EXAMPLE_WORKFLOW_ROOT = Path(__file__).resolve().parents[1] / "example-workflows"
MINIMUM_EXAMPLE_WORKFLOWS = 90

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_example_workflows_roundtrip_layout_safely():
    summary = validate_workflow_files(EXAMPLE_WORKFLOW_ROOT)

    assert EXAMPLE_WORKFLOW_ROOT.exists(), f"Missing workflow corpus: {EXAMPLE_WORKFLOW_ROOT}"
    assert summary.discovered_files >= MINIMUM_EXAMPLE_WORKFLOWS
    assert summary.checked_workflows == summary.discovered_files
    assert summary.ok, _format_validation_failures(summary)


def test_example_workflow_quality_reports_cover_layout_only_and_optimizer():
    layout_only = build_quality_summary(EXAMPLE_WORKFLOW_ROOT)
    optimized = build_quality_summary(EXAMPLE_WORKFLOW_ROOT, optimize_first=True)

    assert layout_only.ok, _format_quality_failures(layout_only)
    assert optimized.ok, _format_quality_failures(optimized)
    assert optimized.checked_workflows == layout_only.checked_workflows

    # The real workflow corpus is intentionally used as a coarse regression budget:
    # layout-only can trade crossings for directionality, but optimizer mode should
    # keep both aggregate straight-line crossings and right-to-left links no worse
    # than the saved source workflows.
    assert _total_laid_out_crossings(optimized) <= _total_original_crossings(optimized)
    assert _total_laid_out_right_to_left_links(optimized) <= _total_original_right_to_left_links(optimized)


def _total_original_crossings(summary: WorkflowQualitySummary) -> int:
    return sum(report.original.link_crossings for report in summary.reports)


def _total_laid_out_crossings(summary: WorkflowQualitySummary) -> int:
    return sum(report.laid_out.link_crossings for report in summary.reports)


def _total_original_right_to_left_links(summary: WorkflowQualitySummary) -> int:
    return sum(report.original.right_to_left_links for report in summary.reports)


def _total_laid_out_right_to_left_links(summary: WorkflowQualitySummary) -> int:
    return sum(report.laid_out.right_to_left_links for report in summary.reports)


def _format_validation_failures(summary: WorkflowValidationSummary) -> str:
    return "\n".join(f"{failure.path}: {failure.message}" for failure in summary.failures[:20])


def _format_quality_failures(summary: WorkflowQualitySummary) -> str:
    return "\n".join(f"{failure.path}: {failure.message}" for failure in summary.failures[:20])
