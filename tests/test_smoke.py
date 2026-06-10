"""Day-1 smoke tests for the ChainPilot eval-harness scaffold.

These tests run in CI without any Claude API calls. They verify:
  - The eval package is importable and has the expected modules.
  - The instrumentation module's cost-calculation logic works (no network).
  - The RUBRIC.md and eval/README.md scaffolds are present and non-trivial.
  - The runners CLI exits with the documented "Day-1 scaffold" status.

Pipeline tests for `trust_engine.py`, `uncertainty_tracker.py`, etc. land Day 2-7
as separate test files. This smoke file is the bootstrapping baseline that
keeps CI green throughout Day 1.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Eval package surface ──────────────────────────────────────────────────────

def test_eval_package_importable():
    """The eval package and its modules import without errors."""
    from eval import instrumentation, runners, scorers  # noqa: F401


def test_rubric_committed_first():
    """RUBRIC.md exists. This is the bias-reduction discipline — without the rubric,
    scenarios are scored against retrofitted preferences."""
    rubric = REPO_ROOT / "eval" / "RUBRIC.md"
    assert rubric.is_file(), "eval/RUBRIC.md must be committed BEFORE any scenarios."
    content = rubric.read_text(encoding="utf-8")
    # Cheap sanity check: rubric covers the four action classes.
    for action in ("immediate_switch", "partial_order", "wait_and_monitor", "emergency_spot_buy"):
        assert action in content, f"RUBRIC.md must define '{action}' — see RUBRIC.md §1."


def test_eval_readme_exists():
    """eval/README.md exists and references the centerpiece A/B."""
    readme = REPO_ROOT / "eval" / "README.md"
    assert readme.is_file()
    text = readme.read_text(encoding="utf-8").lower()
    assert "a/b" in text or "lift" in text, "eval/README.md should reference the A/B / lift framework."


def test_scenarios_dir_exists_but_may_be_empty():
    """eval/scenarios/ directory exists (Day 1 ships with .gitkeep; scenarios land Day 2+)."""
    scenarios_dir = REPO_ROOT / "eval" / "scenarios"
    assert scenarios_dir.is_dir()


# ── Instrumentation (fully implemented Day 1) ─────────────────────────────────

def test_cost_for_call_sonnet_pricing():
    """Cost arithmetic for a known model."""
    from eval.instrumentation import cost_for_call

    # 1M input + 0.5M output @ sonnet-4-6: 3.00 * 1.0 + 15.00 * 0.5 = 10.50 USD
    cost = cost_for_call("claude-sonnet-4-6", 1_000_000, 500_000)
    assert abs(cost - 10.50) < 1e-9, f"Sonnet 1M/0.5M expected $10.50, got ${cost}"


def test_cost_for_call_haiku_pricing():
    """Haiku is meaningfully cheaper than Sonnet."""
    from eval.instrumentation import cost_for_call

    sonnet = cost_for_call("claude-sonnet-4-6", 100_000, 10_000)
    haiku = cost_for_call("claude-haiku-4-5", 100_000, 10_000)
    assert haiku < sonnet / 5, f"Haiku should be >5x cheaper than Sonnet at same tokens; got {haiku} vs {sonnet}"


def test_cost_for_call_unknown_model_falls_back():
    """An unknown model name doesn't raise — falls back to default-model pricing.

    Test design point: we want the eval to never crash on an unrecognized model.
    Surfacing the unknown-model count is a downstream RunTotals concern.
    """
    from eval.instrumentation import cost_for_call

    fallback = cost_for_call("claude-imaginary-9000", 1_000_000, 1_000_000)
    assert fallback > 0, "Unknown model should still return a positive cost via fallback."


def test_calltrace_make_trace_computes_cost():
    """make_trace() is the canonical constructor — it must compute cost_usd."""
    from eval.instrumentation import make_trace

    trace = make_trace(
        role="procurement",
        model="claude-sonnet-4-6",
        input_tokens=10_000,
        output_tokens=2_000,
        wall_clock_ms=4_300,
        scenario_id="scenario_001",
        branch="full",
        rep=1,
    )
    # 10K input + 2K output @ sonnet: 3.00 * 0.01 + 15.00 * 0.002 = 0.030 + 0.030 = 0.060
    assert abs(trace.cost_usd - 0.060) < 1e-9
    assert trace.role == "procurement"
    assert trace.branch == "full"
    assert trace.error is None


def test_aggregate_traces_rolls_up_per_role():
    """RunTotals carries per-role sub-totals — used in eval reports."""
    from eval.instrumentation import aggregate_traces, make_trace

    traces = [
        make_trace(role="procurement", model="claude-sonnet-4-6",
                   input_tokens=10_000, output_tokens=2_000, wall_clock_ms=4_300),
        make_trace(role="procurement", model="claude-sonnet-4-6",
                   input_tokens=12_000, output_tokens=2_500, wall_clock_ms=4_800),
        make_trace(role="risk", model="claude-sonnet-4-6",
                   input_tokens=8_000, output_tokens=1_500, wall_clock_ms=3_900),
    ]
    totals = aggregate_traces(traces)

    assert totals.n_calls == 3
    assert totals.n_errors == 0
    assert "procurement" in totals.by_role
    assert "risk" in totals.by_role
    assert totals.by_role["procurement"].n_calls == 2
    assert totals.by_role["risk"].n_calls == 1
    assert totals.total_input_tokens == 30_000
    assert totals.total_output_tokens == 6_000


def test_aggregate_traces_counts_errors():
    """A trace with error != None increments n_errors."""
    from eval.instrumentation import aggregate_traces, make_trace

    traces = [
        make_trace(role="procurement", model="claude-sonnet-4-6",
                   input_tokens=10_000, output_tokens=2_000, wall_clock_ms=4_300),
        make_trace(role="procurement", model="claude-sonnet-4-6",
                   input_tokens=0, output_tokens=0, wall_clock_ms=0,
                   error="anthropic.RateLimitError"),
    ]
    totals = aggregate_traces(traces)
    assert totals.n_calls == 2
    assert totals.n_errors == 1


# ── Runners CLI ───────────────────────────────────────────────────────────────

def test_runners_cli_dry_mode_exits_nonzero_with_actionable_message():
    """`--mode dry` is reserved for fixture-based scoring (Day-9+).

    The CLI should exit non-zero AND print an actionable message pointing to
    `make eval-small` as the live path. Verifies the user never sees a cryptic
    crash on an unimplemented mode.
    """
    result = subprocess.run(
        [sys.executable, "-m", "eval.runners", "--mode", "dry"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode != 0, (
        f"--mode dry should exit non-zero. stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # Message should reference EITHER "eval-small" (the actionable redirect)
    # OR "not yet implemented" (the explanation).
    assert any(p in result.stderr for p in ("eval-small", "not yet implemented", "Day")), (
        f"CLI should print actionable status; got stderr={result.stderr!r}"
    )
