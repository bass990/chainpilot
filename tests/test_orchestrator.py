"""Day-8 tests for eval/orchestrator.py — end-to-end with mocked LLM calls.

Verifies that the orchestrator correctly:
  - Runs each (scenario × branch × rep) combination
  - Loads scenario expected fields for scoring
  - Produces ScenarioScores per result
  - Computes BranchMetrics per branch
  - Computes A/B lift when both branches are present
  - Aggregates cost across all calls
  - Renders a non-empty markdown report

No live LLM calls — mock client returns canned canned-JSON responses.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from eval.orchestrator import EvalRunReport, render_report, run_eval


REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_mock_client(responses_per_call: list[str]):
    """Mock Anthropic client that returns canned responses in order."""
    client = MagicMock()
    response_iter = iter(responses_per_call)

    def messages_create(**kwargs):
        try:
            text = next(response_iter)
        except StopIteration as e:
            raise AssertionError(
                f"More LLM calls than canned responses provided. "
                f"Last system: {kwargs.get('system', '')[:100]}"
            ) from e
        response = MagicMock()
        response.content = [MagicMock(type="text", text=text)]
        response.usage = MagicMock(input_tokens=200, output_tokens=100)
        return response

    client.messages.create = messages_create
    return client


# Canned responses for one stripped pipeline run (3 calls):
#   1. Procurement JSON
#   2. Risk JSON
#   3. Synthesizer JSON
_STRIPPED_CANNED = [
    '{"recommended_supplier": "FastBear Mfg", "alt_unit_cost": 1.18, '
    '"estimated_30d_exposure": 50000, "switching_premium": 5000, "supplier_options": []}',
    '{"hours_to_stockout": 4.5, "stock_pct": 5, "customers_affected": 1, '
    '"total_orders_at_risk": 1, "top_customer": "Apex", "7day_penalty_exposure": 100000}',
    '{"final_action": "immediate_switch", "recommended_supplier": "FastBear Mfg", '
    '"severity": "CRITICAL", "confidence": "HIGH", '
    '"swing_condition": "If the PLATINUM customer were not at risk, we would wait.", '
    '"rationale": "stockout imminent + PLATINUM exposure"}',
]

# Canned responses for one full pipeline run (5 calls):
#   1. Procurement, 2. Risk, 3. Advocate, 4. Skeptic, 5. Arbiter
_FULL_CANNED = [
    '{"recommended_supplier": "FastBear Mfg", "alt_unit_cost": 1.18, '
    '"estimated_30d_exposure": 50000, "switching_premium": 5000, "supplier_options": []}',
    '{"hours_to_stockout": 4.5, "stock_pct": 5, "customers_affected": 1, '
    '"total_orders_at_risk": 1, "top_customer": "Apex", "7day_penalty_exposure": 100000}',
    "Strong case for action: PLATINUM at 9h, stockout in 4.5h. VERDICT: Act immediately.",
    "Caution: alt has HIGH uncertainty, 18% premium. VERDICT: Wait / proceed cautiously.",
    '{"arbiter_recommendation": "Switch immediately", '
    '"strongest_advocate_point": "PLATINUM at 9h", '
    '"strongest_skeptic_point": "alt uncertainty", '
    '"swing_condition": "If the PLATINUM customer were not at risk, partial preferred.", '
    '"confidence": "MEDIUM", "final_action": "immediate_switch", "severity": "CRITICAL"}',
]


# ── End-to-end orchestrator tests ────────────────────────────────────────────

def test_orchestrator_runs_stripped_only():
    """1 scenario × 1 branch × 1 rep — 3 LLM calls total."""
    client = _make_mock_client(_STRIPPED_CANNED)
    report = run_eval(
        scenario_ids=["scenario_001"],
        branches=["stripped"],
        reps=1,
        mode="small",
        client=client,
    )

    assert isinstance(report, EvalRunReport)
    assert len(report.results) == 1
    assert len(report.scores) == 1
    assert report.total_llm_calls == 3
    assert report.stripped_metrics is not None
    assert report.full_metrics is None
    assert report.ab_lifts == []  # No A/B without both branches


def test_orchestrator_runs_both_branches():
    """1 scenario × 2 branches × 1 rep — 3 + 5 = 8 LLM calls total."""
    canned = _STRIPPED_CANNED + _FULL_CANNED
    client = _make_mock_client(canned)
    report = run_eval(
        scenario_ids=["scenario_001"],
        branches=["stripped", "full"],
        reps=1,
        mode="small",
        client=client,
    )

    assert len(report.results) == 2
    assert report.total_llm_calls == 8
    assert report.full_metrics is not None
    assert report.stripped_metrics is not None
    assert len(report.ab_lifts) == 6  # 6 metric families


def test_orchestrator_scores_action_correctly():
    """scenario_001's expected action is immediate_switch; both branches produced it."""
    canned = _STRIPPED_CANNED + _FULL_CANNED
    client = _make_mock_client(canned)
    report = run_eval(
        scenario_ids=["scenario_001"],
        branches=["stripped", "full"],
        reps=1,
        mode="small",
        client=client,
    )

    for score in report.scores:
        assert score.action_class.strict_match is True
        assert score.action_class.in_acceptable_set is True


def test_orchestrator_handles_pipeline_error():
    """Pipeline errors don't crash the orchestrator — they go in result.error."""
    # Provide too few responses; the mock will raise on the second call
    client = _make_mock_client(["{}"])  # Only 1 response; pipeline needs 3
    report = run_eval(
        scenario_ids=["scenario_001"],
        branches=["stripped"],
        reps=1,
        mode="small",
        client=client,
    )

    assert len(report.results) == 1
    assert report.results[0].error is not None
    assert "AssertionError" in report.results[0].error or "More LLM calls" in report.results[0].error
    # Scoring should skip failed results
    assert len(report.scores) == 0


def test_orchestrator_cost_aggregation():
    """Per-call costs sum to report.total_cost_usd."""
    canned = _STRIPPED_CANNED + _FULL_CANNED
    client = _make_mock_client(canned)
    report = run_eval(
        scenario_ids=["scenario_001"],
        branches=["stripped", "full"],
        reps=1,
        mode="small",
        client=client,
    )

    # Each mock trace has 200 input + 100 output tokens on Sonnet ($3 + $15 per M)
    # → 0.0006 + 0.0015 = $0.0021 per call. 8 calls → $0.0168
    assert report.total_llm_calls == 8
    assert abs(report.total_cost_usd - 8 * 0.0021) < 0.0001


# ── Report rendering tests ──────────────────────────────────────────────────

def test_render_report_includes_headline_fields():
    """The headline section has timestamp, mode, scenario count, costs."""
    canned = _STRIPPED_CANNED + _FULL_CANNED
    client = _make_mock_client(canned)
    report = run_eval(
        scenario_ids=["scenario_001"],
        branches=["stripped", "full"],
        reps=1,
        mode="small",
        client=client,
    )
    md = render_report(report)

    assert "# ChainPilot Eval Run" in md
    assert "scenario_001" in md
    assert "stripped" in md
    assert "full" in md
    assert "Total cost" in md
    assert "Total LLM calls" in md


def test_render_report_includes_branch_metrics_table():
    canned = _STRIPPED_CANNED + _FULL_CANNED
    client = _make_mock_client(canned)
    report = run_eval(
        scenario_ids=["scenario_001"],
        branches=["stripped", "full"],
        reps=1,
        mode="small",
        client=client,
    )
    md = render_report(report)

    assert "## Branch metrics" in md
    assert "strict action accuracy" in md
    assert "Full pipeline" in md
    assert "Stripped pipeline" in md


def test_render_report_includes_ab_lift_table():
    canned = _STRIPPED_CANNED + _FULL_CANNED
    client = _make_mock_client(canned)
    report = run_eval(
        scenario_ids=["scenario_001"],
        branches=["stripped", "full"],
        reps=1,
        mode="small",
        client=client,
    )
    md = render_report(report)

    assert "## A/B lift" in md
    assert "Lift (abs)" in md
    assert "Strict action accuracy" in md


def test_render_report_includes_per_scenario_details():
    canned = _STRIPPED_CANNED + _FULL_CANNED
    client = _make_mock_client(canned)
    report = run_eval(
        scenario_ids=["scenario_001"],
        branches=["stripped", "full"],
        reps=1,
        mode="small",
        client=client,
    )
    md = render_report(report)

    assert "## Per-scenario detail" in md
    assert "### scenario_001" in md
    assert "final_action" in md
    assert "FastBear Mfg" in md  # canned recommended_supplier
