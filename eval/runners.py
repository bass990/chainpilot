"""Branch runners for the ChainPilot eval harness.

Implements the two pipelines that get A/B-compared:

  run_full_pipeline()    Procurement || Risk → Advocate ⚔ Skeptic ⚔ Arbiter
                         5 LLM calls per pipeline execution

  run_stripped_pipeline() Procurement || Risk → Synthesizer (no deliberation)
                         3 LLM calls per pipeline execution

Both return a ScenarioResult containing the final recommendation, the raw
agent outputs, and CallTrace records for cost/latency instrumentation.

EVAL MODE — disclosure
    The eval runs WITHOUT the production tool-use loop. Disruption context is
    rendered directly into the user message; the EVAL_MODE suffix appended to
    each specialist's system prompt instructs the LLM not to attempt tool calls.
    This means the eval tests the SPECIALIST PROMPTS and the deliberation
    architecture but NOT the production tool-use behavior. A faithful-replay
    runner with scenario-backed tools is a future Day-8+ extension.

CLI entry point: `python -m eval.runners --mode {dry,small,full,report-only} ...`
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eval.instrumentation import CallTrace, make_trace
from eval.prompts import (
    SYSTEM_ARBITER_EVAL,
    SYSTEM_ADVOCATE_EVAL,
    SYSTEM_PROCUREMENT_EVAL,
    SYSTEM_RISK_EVAL,
    SYSTEM_SKEPTIC_EVAL,
    SYSTEM_STRIPPED_SYNTHESIZER_EVAL,
)
from eval.schemas import Scenario


SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"
REPORTS_DIR = Path(__file__).resolve().parent / "reports"

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096


# ── ScenarioResult ────────────────────────────────────────────────────────────

@dataclass
class ScenarioResult:
    """Outcome of running one scenario through one branch (one rep)."""

    scenario_id: str
    branch: str
    rep: int
    final_action: str | None = None
    recommended_supplier: str | None = None
    severity: str | None = None
    confidence: str | None = None
    swing_condition: str | None = None
    raw_arbiter_output: dict[str, Any] | None = None
    raw_finalize_output: dict[str, Any] | None = None
    traces: list[CallTrace] = field(default_factory=list)
    error: str | None = None


# ── Scenario loading / discovery ──────────────────────────────────────────────

def load_scenario(scenario_id: str) -> dict[str, Any]:
    """Load a scenario JSON from eval/scenarios/.

    Returns the raw dict (not Pydantic) for backwards compat with Day-1 stubs.
    Internal callers wanting validated data should use _load_scenario_pydantic.
    """
    if not scenario_id.startswith("scenario_"):
        scenario_id = f"scenario_{scenario_id}"
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Scenario file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_scenarios(*, tier: str | None = None) -> list[str]:
    """Return scenario IDs available in eval/scenarios/, optionally filtered by tier."""
    if not SCENARIOS_DIR.is_dir():
        return []
    ids = []
    for p in sorted(SCENARIOS_DIR.glob("scenario_*.json")):
        if tier is None:
            ids.append(p.stem)
        else:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("tier") == tier:
                ids.append(p.stem)
    return ids


# ── LLM client (patchable for tests) ─────────────────────────────────────────

def _get_anthropic_client():
    """Return an Anthropic client. Patchable by tests via monkeypatch."""
    import anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Live LLM runs require a real API key; "
            "tests should monkeypatch eval.runners._get_anthropic_client."
        )
    return anthropic.Anthropic(api_key=api_key)


# ── Scenario rendering ────────────────────────────────────────────────────────

def render_disruption_context(scenario: Scenario) -> str:
    """Render a Scenario into a user-message string for specialist LLM calls.

    The render is deterministic — same scenario → same string. Tests rely on
    this for snapshot-style equality checks.
    """
    s = scenario.input

    alts_block = "\n".join(
        f"  - {a.name}: {a.lead_time_days}-day lead time, "
        f"{a.unit_cost_pct_baseline:.2f}× baseline cost, {a.uncertainty} uncertainty"
        for a in s.alternative_suppliers
    ) or "  (none listed)"

    orders_block = "\n".join(
        f"  - {o.customer} (tier {o.tier}): {o.hours_to_deadline:.1f} hours to deadline"
        for o in s.customer_orders_at_risk
    ) or "  (none listed)"

    return (
        f"DISRUPTION CONTEXT (provided directly — no tools available):\n"
        f"\n"
        f"SKU: {s.sku}\n"
        f"Stock: {s.stock_pct * 100:.1f}% remaining "
        f"({s.hours_to_stockout:.1f} hours to stockout at current burn rate)\n"
        f"\n"
        f"Primary supplier:\n"
        f"  Name: {s.primary_supplier.name}\n"
        f"  Delay: {s.primary_supplier.delay_days} days\n"
        f"  Reliability score: {s.primary_supplier.reliability_score:.2f}\n"
        f"  Quality uncertainty: {s.primary_supplier.uncertainty}\n"
        f"\n"
        f"Alternative suppliers:\n"
        f"{alts_block}\n"
        f"\n"
        f"Customer orders at risk:\n"
        f"{orders_block}\n"
        f"\n"
        f"Spot price change: {s.price_spike_pct * 100:+.1f}% vs 30-day baseline\n"
        f"\n"
        f"Respond with only the required JSON specified in your system instructions."
    )


# ── LLM call helper ──────────────────────────────────────────────────────────

def _call_llm(
    client: Any,
    *,
    system_prompt: str,
    user_message: str,
    scenario_id: str,
    branch: str,
    rep: int,
    role: str,
) -> tuple[str, CallTrace]:
    """Make one LLM call. Returns (response_text, CallTrace).

    Raises on rate limits and other Anthropic errors — the caller decides
    whether to retry or fail the scenario.
    """
    t_start = time.time()
    response = client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    wall_ms = int((time.time() - t_start) * 1000)

    # Extract text from response. Handles both real Anthropic responses (with
    # .content list of blocks) and mocked test responses.
    if hasattr(response, "content") and isinstance(response.content, list):
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    else:
        text = str(getattr(response, "content", ""))

    input_tokens = getattr(getattr(response, "usage", None), "input_tokens", 0)
    output_tokens = getattr(getattr(response, "usage", None), "output_tokens", 0)

    trace = make_trace(
        role=role,
        model=_MODEL,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        wall_clock_ms=wall_ms,
        scenario_id=scenario_id,
        branch=branch,
        rep=rep,
    )
    return text, trace


# ── JSON parsing ─────────────────────────────────────────────────────────────

def _parse_json_safe(text: str) -> dict[str, Any]:
    """Parse JSON from an LLM response. Robust to surrounding prose / code fences.

    Returns an empty dict on parse failure (the caller can detect this via
    missing required keys). A future hardening pass could add an LLM-as-judge
    repair step.
    """
    text = text.strip()
    if text.startswith("```"):
        # Strip markdown code fence
        lines = text.split("\n")
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1])
    # Find the first { and last } — handle "Here's the JSON: {...}" patterns
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        text = text[first_brace:last_brace + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


# ── Pipeline runners ─────────────────────────────────────────────────────────

def run_stripped_pipeline(
    scenario: Scenario,
    rep: int = 1,
    *,
    client: Any = None,
) -> ScenarioResult:
    """Branch B — Procurement || Risk → direct Synthesizer (no deliberation).

    3 LLM calls per run.

    Args:
        scenario: validated Pydantic Scenario instance
        rep: 1-indexed repetition number
        client: Anthropic client (None → constructed from env)

    Returns:
        ScenarioResult with final_action, recommended_supplier, severity,
        confidence, swing_condition populated from the Synthesizer JSON.
    """
    if client is None:
        client = _get_anthropic_client()

    user_msg = render_disruption_context(scenario)
    all_traces: list[CallTrace] = []

    # Stage 1: Procurement || Risk in parallel
    proc_text, proc_trace, risk_text, risk_trace = _parallel_specialists(
        client, user_msg, scenario.id, "stripped", rep,
    )
    all_traces.extend([proc_trace, risk_trace])

    proc_json = _parse_json_safe(proc_text)
    risk_json = _parse_json_safe(risk_text)

    # Stage 2: Synthesizer (direct, no deliberation)
    synth_context = (
        "Procurement Specialist output:\n"
        f"{json.dumps(proc_json, indent=2)}\n\n"
        "Risk Specialist output:\n"
        f"{json.dumps(risk_json, indent=2)}"
    )
    synth_text, synth_trace = _call_llm(
        client,
        system_prompt=SYSTEM_STRIPPED_SYNTHESIZER_EVAL,
        user_message=synth_context,
        scenario_id=scenario.id,
        branch="stripped",
        rep=rep,
        role="synthesizer",
    )
    all_traces.append(synth_trace)

    synth_json = _parse_json_safe(synth_text)

    return ScenarioResult(
        scenario_id=scenario.id,
        branch="stripped",
        rep=rep,
        final_action=synth_json.get("final_action"),
        recommended_supplier=synth_json.get("recommended_supplier"),
        severity=synth_json.get("severity"),
        confidence=synth_json.get("confidence"),
        swing_condition=synth_json.get("swing_condition"),
        raw_finalize_output=synth_json,
        traces=all_traces,
    )


def run_full_pipeline(
    scenario: Scenario,
    rep: int = 1,
    *,
    client: Any = None,
) -> ScenarioResult:
    """Branch A — full ChainPilot pipeline.

    Procurement || Risk → Advocate ⚔ Skeptic → Arbiter
    5 LLM calls per run.

    Returns:
        ScenarioResult with final_action and confidence from the Arbiter;
        recommended_supplier from the Procurement Specialist;
        swing_condition from the Arbiter.
        severity is left None — derived downstream by scorers.py (the
        production Arbiter prompt doesn't output severity directly).
    """
    if client is None:
        client = _get_anthropic_client()

    user_msg = render_disruption_context(scenario)
    all_traces: list[CallTrace] = []

    # Stage 1: Procurement || Risk
    proc_text, proc_trace, risk_text, risk_trace = _parallel_specialists(
        client, user_msg, scenario.id, "full", rep,
    )
    all_traces.extend([proc_trace, risk_trace])

    proc_json = _parse_json_safe(proc_text)
    risk_json = _parse_json_safe(risk_text)

    # Stage 2: Advocate || Skeptic on the analysis
    analysis_context = (
        "DISRUPTION ANALYSIS:\n\n"
        f"Procurement Specialist output:\n{json.dumps(proc_json, indent=2)}\n\n"
        f"Risk Specialist output:\n{json.dumps(risk_json, indent=2)}"
    )

    adv_text, adv_trace, skp_text, skp_trace = _parallel_deliberators(
        client, analysis_context, scenario.id, "full", rep,
    )
    all_traces.extend([adv_trace, skp_trace])

    # Stage 3: Arbiter synthesizes deliberation
    arbiter_context = (
        f"{analysis_context}\n\n"
        f"Advocate's argument:\n{adv_text}\n\n"
        f"Skeptic's argument:\n{skp_text}"
    )
    arb_text, arb_trace = _call_llm(
        client,
        system_prompt=SYSTEM_ARBITER_EVAL,
        user_message=arbiter_context,
        scenario_id=scenario.id,
        branch="full",
        rep=rep,
        role="arbiter",
    )
    all_traces.append(arb_trace)

    arb_json = _parse_json_safe(arb_text)

    return ScenarioResult(
        scenario_id=scenario.id,
        branch="full",
        rep=rep,
        final_action=arb_json.get("final_action"),
        recommended_supplier=proc_json.get("recommended_supplier"),
        severity=arb_json.get("severity"),  # eval-mode addendum to Arbiter prompt
        confidence=arb_json.get("confidence"),
        swing_condition=arb_json.get("swing_condition"),
        raw_arbiter_output=arb_json,
        traces=all_traces,
    )


def _parallel_specialists(
    client: Any,
    user_msg: str,
    scenario_id: str,
    branch: str,
    rep: int,
) -> tuple[str, CallTrace, str, CallTrace]:
    """Run Procurement and Risk specialists in parallel."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        proc_future = pool.submit(
            _call_llm, client,
            system_prompt=SYSTEM_PROCUREMENT_EVAL,
            user_message=user_msg,
            scenario_id=scenario_id, branch=branch, rep=rep, role="procurement",
        )
        risk_future = pool.submit(
            _call_llm, client,
            system_prompt=SYSTEM_RISK_EVAL,
            user_message=user_msg,
            scenario_id=scenario_id, branch=branch, rep=rep, role="risk",
        )
        proc_text, proc_trace = proc_future.result()
        risk_text, risk_trace = risk_future.result()
    return proc_text, proc_trace, risk_text, risk_trace


def _parallel_deliberators(
    client: Any,
    analysis_context: str,
    scenario_id: str,
    branch: str,
    rep: int,
) -> tuple[str, CallTrace, str, CallTrace]:
    """Run Advocate and Skeptic in parallel."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        adv_future = pool.submit(
            _call_llm, client,
            system_prompt=SYSTEM_ADVOCATE_EVAL,
            user_message=analysis_context,
            scenario_id=scenario_id, branch=branch, rep=rep, role="advocate",
        )
        skp_future = pool.submit(
            _call_llm, client,
            system_prompt=SYSTEM_SKEPTIC_EVAL,
            user_message=analysis_context,
            scenario_id=scenario_id, branch=branch, rep=rep, role="skeptic",
        )
        adv_text, adv_trace = adv_future.result()
        skp_text, skp_trace = skp_future.result()
    return adv_text, adv_trace, skp_text, skp_trace


def run_dry_score(*, report_path: Path | None = None) -> dict[str, Any]:
    """Day-7 placeholder — scoring lands Day 8."""
    raise NotImplementedError(
        "run_dry_score lands Day 8 alongside the scorers.py implementations."
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m eval.runners",
        description="ChainPilot eval harness — branch runners.",
    )
    p.add_argument(
        "--mode",
        choices=["dry", "small", "full", "report-only"],
        required=True,
        help="dry: no LLM, score fixtures (Day-8). small: 5 scenarios × 1 rep. "
             "full: 40 × 2 × 3. report-only: regenerate report.",
    )
    p.add_argument("--scenario-count", type=int, default=None)
    p.add_argument("--reps", type=int, default=None)
    p.add_argument("--branches", default="stripped,full")
    p.add_argument("--report", type=Path, default=None)
    p.add_argument("--from", type=Path, dest="from_path", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.mode == "dry":
        print(
            "dry mode: scoring saved fixtures without LLM calls — not yet implemented. "
            "Use `make eval-small` instead (small live LLM cost ~$0.50-$1).",
            file=sys.stderr,
        )
        return 1

    if args.mode in ("small", "full"):
        # Day-8: orchestrator is wired. Live LLM calls happen here.
        from eval.orchestrator import (
            DEFAULT_EVAL_SMALL_SCENARIO_IDS,
            run_eval,
            save_run,
        )

        branches = [b.strip() for b in args.branches.split(",") if b.strip()]
        if args.mode == "small":
            scenario_ids = DEFAULT_EVAL_SMALL_SCENARIO_IDS[: args.scenario_count or 5]
            reps = args.reps or 1
        else:
            # full mode — all scenarios × all branches × default 3 reps
            scenario_ids = sorted(p.stem for p in SCENARIOS_DIR.glob("scenario_*.json"))
            if args.scenario_count:
                scenario_ids = scenario_ids[: args.scenario_count]
            reps = args.reps or 3

        print(f"Running eval-{args.mode}: {len(scenario_ids)} scenarios × {len(branches)} branches × {reps} reps", file=sys.stderr)
        expected_calls = sum(
            (5 if b == "full" else 3) for b in branches
        ) * len(scenario_ids) * reps
        print(f"Expected LLM calls: {expected_calls} (stripped=3 / full=5 per pipeline run)", file=sys.stderr)
        print(f"Scenarios: {', '.join(scenario_ids)}", file=sys.stderr)
        print(f"Branches: {', '.join(branches)}", file=sys.stderr)
        print("---", file=sys.stderr)

        report = run_eval(
            scenario_ids=scenario_ids,
            branches=branches,
            reps=reps,
            mode=args.mode,
        )
        path = save_run(report, report_path=args.report)
        print(f"\nReport saved to: {path}", file=sys.stderr)
        print(f"Total cost: ${report.total_cost_usd:.4f}", file=sys.stderr)
        print(f"Total wall clock: {report.total_wall_clock_s:.1f}s", file=sys.stderr)
        return 0

    if args.mode == "report-only":
        # Re-render from latest_run.json — useful when scoring logic changes
        # but you don't want to re-spend on LLM calls.
        print(
            "report-only mode lands Day-9 (separates re-rendering from re-running). "
            "Until then, the markdown report is generated at run time.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
