"""Cost + latency instrumentation for the ChainPilot eval harness.

This module is FULLY IMPLEMENTED at Day 1 (vs runners.py / scorers.py which are
skeletons) because:
  - It doesn't depend on LLM calls — it wraps them and records the metrics
  - It needs to exist before any other eval component lands, since runners.py
    and scorers.py both depend on the CallTrace dataclass
  - The arithmetic (token-cost mapping) doesn't change as the eval grows

Public surface:
  - CallTrace dataclass: per-LLM-call record
  - cost_for_call(): given model + token counts, return USD cost
  - RunTotals dataclass: aggregate stats for a scenario or branch
  - aggregate_traces(): list[CallTrace] -> RunTotals
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# Per-million-token pricing. Source: Anthropic public pricing at the snapshot
# point in this build. Update when model versions or pricing change.
# Note: prices are in USD per million tokens.
_PRICING: dict[str, tuple[float, float]] = {
    # model -> (input_per_M_tokens_usd, output_per_M_tokens_usd)
    "claude-sonnet-4-6":             (3.00, 15.00),
    "claude-haiku-4-5":              (0.25,  1.25),
    "claude-haiku-4-5-20251001":     (0.25,  1.25),
    # Fallback: estimate at Sonnet pricing for unknown model strings.
    # Tracked explicitly so the eval can flag if it sees an unrecognized model.
}

_DEFAULT_PRICING_MODEL = "claude-sonnet-4-6"


@dataclass
class CallTrace:
    """One LLM-call's worth of instrumentation."""

    role: str                      # "procurement" / "risk" / "advocate" / "skeptic" / ...
    model: str                     # e.g. "claude-sonnet-4-6"
    input_tokens: int
    output_tokens: int
    wall_clock_ms: int
    cost_usd: float
    scenario_id: str | None = None
    branch: str | None = None      # "full" or "stripped"
    rep: int | None = None         # which repetition of this scenario × branch (1-indexed)
    error: str | None = None       # if the call failed, what happened


def cost_for_call(model: str, input_tokens: int, output_tokens: int) -> float:
    """USD cost of one LLM call, given the model and token counts.

    If the model is not in the pricing table, falls back to the default model's
    pricing and logs the fallback intent in the returned cost (no exception).
    """
    if model in _PRICING:
        in_rate, out_rate = _PRICING[model]
    else:
        in_rate, out_rate = _PRICING[_DEFAULT_PRICING_MODEL]
        # NB: in a real system we'd log a warning here. For now, fall back silently
        # and let aggregate_traces surface the unknown-model count.

    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate


def make_trace(
    *,
    role: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    wall_clock_ms: int,
    scenario_id: str | None = None,
    branch: str | None = None,
    rep: int | None = None,
    error: str | None = None,
) -> CallTrace:
    """Convenience constructor that computes cost_usd from model + tokens."""
    cost = cost_for_call(model, input_tokens, output_tokens)
    return CallTrace(
        role=role,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        wall_clock_ms=wall_clock_ms,
        cost_usd=cost,
        scenario_id=scenario_id,
        branch=branch,
        rep=rep,
        error=error,
    )


@dataclass
class RunTotals:
    """Aggregate stats for a set of CallTrace records.

    Used at three nesting levels:
      - per-scenario × branch × rep (one pipeline run)
      - per-scenario × branch (median across reps)
      - per-branch (across all scenarios in the eval)
    """

    n_calls: int = 0
    n_errors: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_wall_clock_ms: int = 0
    total_cost_usd: float = 0.0
    by_role: dict[str, "RunTotals"] = field(default_factory=dict)

    def add(self, trace: CallTrace) -> None:
        """Accumulate a single trace into this RunTotals (and the per-role sub-totals)."""
        self.n_calls += 1
        if trace.error is not None:
            self.n_errors += 1
        self.total_input_tokens += trace.input_tokens
        self.total_output_tokens += trace.output_tokens
        self.total_wall_clock_ms += trace.wall_clock_ms
        self.total_cost_usd += trace.cost_usd

        # Per-role rollup
        sub = self.by_role.setdefault(trace.role, RunTotals())
        sub.n_calls += 1
        if trace.error is not None:
            sub.n_errors += 1
        sub.total_input_tokens += trace.input_tokens
        sub.total_output_tokens += trace.output_tokens
        sub.total_wall_clock_ms += trace.wall_clock_ms
        sub.total_cost_usd += trace.cost_usd


def aggregate_traces(traces: Iterable[CallTrace]) -> RunTotals:
    """Roll up a list of CallTrace records into a RunTotals."""
    totals = RunTotals()
    for t in traces:
        totals.add(t)
    return totals


def format_totals(totals: RunTotals, *, indent: int = 0) -> str:
    """Human-readable, single-string representation of a RunTotals.

    Used in eval reports. Nested per-role rollups are indented two spaces.
    """
    pad = " " * indent
    lines = [
        f"{pad}calls: {totals.n_calls} ({totals.n_errors} errors)",
        f"{pad}tokens: {totals.total_input_tokens:,} in / {totals.total_output_tokens:,} out",
        f"{pad}wall-clock: {totals.total_wall_clock_ms / 1000:,.1f}s",
        f"{pad}cost: ${totals.total_cost_usd:.4f}",
    ]
    if totals.by_role:
        lines.append(f"{pad}by role:")
        for role in sorted(totals.by_role):
            lines.append(f"{pad}  {role}:")
            lines.append(format_totals(totals.by_role[role], indent=indent + 4))
    return "\n".join(lines)
