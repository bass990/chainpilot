"""Orchestrator + report renderer for the ChainPilot eval harness.

run_eval()         Run scenarios × branches × reps, score, render report.
render_report()    Render results + scores + lifts as markdown.

The orchestrator is the integration layer between runners.py (pipeline execution),
scorers.py (programmatic scoring), and instrumentation.py (cost/latency).

Modes:
  eval-small  Default 5 scenarios × 2 branches × 1 rep (~30 LLM calls, ~$0.50-$1)
  eval         Full 40 scenarios × 2 branches × 3 reps (~$14-43)
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eval.instrumentation import CallTrace, aggregate_traces
from eval.runners import (
    REPORTS_DIR,
    SCENARIOS_DIR,
    ScenarioResult,
    run_full_pipeline,
    run_stripped_pipeline,
)
from eval.schemas import Scenario, load_scenario
from eval.scorers import (
    ABLiftResult,
    BranchMetrics,
    ScenarioScores,
    aggregate_branch_metrics,
    compute_ab_lift,
    score_scenario_result,
)


# Default 5-scenario selection for eval-small: covers 5 tiers, diverse difficulty.
# 1 = simplest baseline; 17 = no-alts edge; 11 = ambiguous partial_order;
# 23 = numerical boundary; 27 = prompt injection.
DEFAULT_EVAL_SMALL_SCENARIO_IDS = [
    "scenario_001",  # Clear-Act baseline
    "scenario_006",  # Clear-Wait baseline
    "scenario_011",  # Ambiguous (partial_order)
    "scenario_017",  # Edge (no alternatives)
    "scenario_027",  # Adversarial (prompt injection)
]


@dataclass
class EvalRunReport:
    """Top-level run record. Serialized to JSON for re-rendering reports."""

    run_id: str
    timestamp_utc: str
    mode: str
    scenario_ids: list[str]
    branches: list[str]
    reps_per_scenario: int

    # Per-scenario × branch × rep raw results (one entry per pipeline run)
    results: list[ScenarioResult]
    # Per-scenario × branch × rep scoring breakdown
    scores: list[ScenarioScores]

    # Branch-level aggregates
    full_metrics: BranchMetrics | None = None
    stripped_metrics: BranchMetrics | None = None

    # A/B lift (only populated when both branches were run)
    ab_lifts: list[ABLiftResult] = field(default_factory=list)

    # Cost summary
    total_cost_usd: float = 0.0
    total_wall_clock_s: float = 0.0
    total_llm_calls: int = 0


# ── Orchestration ─────────────────────────────────────────────────────────────

def run_eval(
    *,
    scenario_ids: list[str],
    branches: list[str],
    reps: int = 1,
    mode: str = "small",
    client: Any = None,
) -> EvalRunReport:
    """Run the eval over a Cartesian product of scenarios × branches × reps.

    Args:
        scenario_ids: list of scenario IDs (e.g. ["scenario_001", ...])
        branches: subset of ["full", "stripped"]
        reps: number of repetitions per scenario × branch
        mode: report-tag string ("small" / "full" / custom)
        client: Anthropic client (None → constructed from env)

    Returns:
        EvalRunReport with raw results, scores, branch metrics, A/B lift, costs.
    """
    t_start = time.time()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Load + cache scenarios
    scenarios: dict[str, Scenario] = {}
    raw_scenarios: dict[str, dict[str, Any]] = {}
    for sid in scenario_ids:
        path = SCENARIOS_DIR / f"{sid}.json"
        scenarios[sid] = load_scenario(path)
        raw_scenarios[sid] = json.loads(path.read_text(encoding="utf-8"))

    # Run pipelines
    all_results: list[ScenarioResult] = []
    for sid in scenario_ids:
        scenario = scenarios[sid]
        for branch in branches:
            for rep in range(1, reps + 1):
                runner = run_full_pipeline if branch == "full" else run_stripped_pipeline
                try:
                    result = runner(scenario, rep=rep, client=client)
                except Exception as exc:  # noqa: BLE001 — surface any pipeline error
                    # Create a failed-result placeholder so the report can show it
                    result = ScenarioResult(
                        scenario_id=sid,
                        branch=branch,
                        rep=rep,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                all_results.append(result)

    # Score
    all_scores: list[ScenarioScores] = []
    for result in all_results:
        if result.error is not None:
            continue  # Skip scoring on failed runs
        expected = raw_scenarios[result.scenario_id]["expected"]
        all_scores.append(score_scenario_result(expected, result))

    # Per-branch aggregation
    full_metrics: BranchMetrics | None = None
    stripped_metrics: BranchMetrics | None = None
    for branch_name in branches:
        branch_scores: list[tuple[ScenarioScores, str]] = [
            (s, raw_scenarios[s.scenario_id]["tier"])
            for s in all_scores
            if s.branch == branch_name
        ]
        if branch_name == "full":
            full_metrics = aggregate_branch_metrics("full", branch_scores)
        elif branch_name == "stripped":
            stripped_metrics = aggregate_branch_metrics("stripped", branch_scores)

    # A/B lift (only when both branches present)
    lifts: list[ABLiftResult] = []
    if full_metrics is not None and stripped_metrics is not None:
        lifts = compute_ab_lift(full_metrics=full_metrics, stripped_metrics=stripped_metrics)

    # Cost rollup
    all_traces: list[CallTrace] = []
    for r in all_results:
        all_traces.extend(r.traces)
    totals = aggregate_traces(all_traces)

    wall_clock_s = time.time() - t_start

    return EvalRunReport(
        run_id=run_id,
        timestamp_utc=timestamp,
        mode=mode,
        scenario_ids=scenario_ids,
        branches=branches,
        reps_per_scenario=reps,
        results=all_results,
        scores=all_scores,
        full_metrics=full_metrics,
        stripped_metrics=stripped_metrics,
        ab_lifts=lifts,
        total_cost_usd=totals.total_cost_usd,
        total_wall_clock_s=wall_clock_s,
        total_llm_calls=totals.n_calls,
    )


# ── Report rendering ─────────────────────────────────────────────────────────

def render_report(report: EvalRunReport) -> str:
    """Render an EvalRunReport as markdown.

    The report has four sections: headline, branch metrics, A/B lift, per-scenario.
    """
    lines: list[str] = []

    # Headline
    lines.append(f"# ChainPilot Eval Run — {report.run_id}")
    lines.append("")
    lines.append(f"- **Mode:** `{report.mode}`")
    lines.append(f"- **Timestamp (UTC):** {report.timestamp_utc}")
    lines.append(f"- **Scenarios:** {len(report.scenario_ids)} ({', '.join(report.scenario_ids)})")
    lines.append(f"- **Branches:** {', '.join(report.branches)}")
    lines.append(f"- **Reps per scenario × branch:** {report.reps_per_scenario}")
    lines.append(f"- **Total LLM calls:** {report.total_llm_calls}")
    lines.append(f"- **Total cost:** ${report.total_cost_usd:.4f}")
    lines.append(f"- **Total wall clock:** {report.total_wall_clock_s:.1f}s")
    lines.append("")

    # Branch metrics
    lines.append("## Branch metrics")
    lines.append("")
    if report.full_metrics is None and report.stripped_metrics is None:
        lines.append("_No metrics — both branches produced no scorable results._")
        lines.append("")
    else:
        lines.append("| Metric | Full pipeline | Stripped pipeline |")
        lines.append("|---|---|---|")

        def _fmt(v: float, fmt: str = ".3f") -> str:
            return format(v, fmt)

        full = report.full_metrics
        strip = report.stripped_metrics

        def _both(key: str, fmt: str = ".3f") -> str:
            f_val = _fmt(getattr(full, key)) if full else "—"
            s_val = _fmt(getattr(strip, key)) if strip else "—"
            return f"| {key.replace('_', ' ')} | {f_val} | {s_val} |"

        for k in (
            "strict_action_accuracy",
            "acceptable_action_accuracy",
            "supplier_in_set_rate",
            "supplier_refrain_rate",
            "severity_mae_mean",
            "confidence_correct_rate",
            "swing_condition_keyword_coverage_mean",
            "swing_condition_length_in_range_rate",
            "swing_condition_negation_structure_rate",
        ):
            lines.append(_both(k))
        lines.append("")

        # Per-tier breakdown
        all_tiers = set()
        if full:
            all_tiers.update(full.per_tier_strict_accuracy.keys())
        if strip:
            all_tiers.update(strip.per_tier_strict_accuracy.keys())
        if all_tiers:
            lines.append("### Strict action accuracy by tier")
            lines.append("")
            lines.append("| Tier | Full | Stripped |")
            lines.append("|---|---|---|")
            for tier in sorted(all_tiers):
                f_val = (
                    _fmt(full.per_tier_strict_accuracy.get(tier, 0.0))
                    if full and tier in full.per_tier_strict_accuracy else "—"
                )
                s_val = (
                    _fmt(strip.per_tier_strict_accuracy.get(tier, 0.0))
                    if strip and tier in strip.per_tier_strict_accuracy else "—"
                )
                lines.append(f"| {tier} | {f_val} | {s_val} |")
            lines.append("")

    # A/B lift
    lines.append("## A/B lift — adversarial deliberation vs stripped pipeline")
    lines.append("")
    if not report.ab_lifts:
        lines.append("_Lift not computed — both branches required for A/B._")
        lines.append("")
    else:
        lines.append("| Metric | Full | Stripped | Lift (abs) | Interpretation |")
        lines.append("|---|---|---|---|---|")
        for lift in report.ab_lifts:
            lines.append(
                f"| {lift.metric} | "
                f"{lift.full_pipeline_value:.3f} | "
                f"{lift.stripped_pipeline_value:.3f} | "
                f"{lift.lift_absolute:+.3f} | "
                f"{lift.interpretation} |"
            )
        lines.append("")

    # Per-scenario detail
    lines.append("## Per-scenario detail")
    lines.append("")
    by_sid: dict[str, list[ScenarioResult]] = {}
    for r in report.results:
        by_sid.setdefault(r.scenario_id, []).append(r)
    for sid in sorted(by_sid):
        lines.append(f"### {sid}")
        lines.append("")
        for r in by_sid[sid]:
            lines.append(f"**Branch {r.branch}, rep {r.rep}:**")
            if r.error:
                lines.append(f"- ERROR: `{r.error}`")
            else:
                lines.append(f"- final_action: `{r.final_action}`")
                lines.append(f"- recommended_supplier: `{r.recommended_supplier}`")
                lines.append(f"- severity: `{r.severity}`")
                lines.append(f"- confidence: `{r.confidence}`")
                swing = r.swing_condition or ""
                if len(swing) > 200:
                    swing = swing[:200] + "…"
                lines.append(f"- swing_condition: {swing}")
                # Call-level cost
                cost = sum(t.cost_usd for t in r.traces)
                lines.append(f"- run cost: ${cost:.4f} ({len(r.traces)} LLM calls)")
            lines.append("")

    # Footer
    lines.append("---")
    lines.append("")
    lines.append("_Run-level JSON saved at `eval/reports/latest_run.json` for re-rendering._")
    lines.append("")
    lines.append(
        "_Eval mode trade-off (per `eval/runners.py`): production tool-use loop is "
        "skipped; scenario context is rendered directly into specialist user messages. "
        "The eval measures the deliberation A/B, not the tool-use behavior._"
    )

    return "\n".join(lines)


# ── Persistence ──────────────────────────────────────────────────────────────

def save_run(report: EvalRunReport, *, report_path: Path | None = None) -> Path:
    """Save a run report as JSON (raw) + markdown (rendered).

    Returns the path to the markdown report.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # Markdown
    md = render_report(report)
    md_path = report_path or (REPORTS_DIR / f"run_{report.run_id}.md")
    md_path.write_text(md, encoding="utf-8")

    # JSON snapshot for re-rendering
    json_path = REPORTS_DIR / "latest_run.json"
    json_path.write_text(_serialize_report(report), encoding="utf-8")

    return md_path


def _serialize_report(report: EvalRunReport) -> str:
    """Serialize an EvalRunReport to JSON. Handles dataclasses via dict conversion."""
    def to_dict(obj: Any) -> Any:
        if hasattr(obj, "__dataclass_fields__"):
            return {k: to_dict(getattr(obj, k)) for k in obj.__dataclass_fields__}
        if isinstance(obj, list):
            return [to_dict(i) for i in obj]
        if isinstance(obj, dict):
            return {k: to_dict(v) for k, v in obj.items()}
        return obj

    return json.dumps(to_dict(report), indent=2, default=str)
