"""Scoring functions for the ChainPilot eval harness.

Five metric families per eval/RUBRIC.md:

  1. score_action_class()       — strict + acceptable-set match
  2. score_supplier_choice()    — in-set membership against rubric-defined acceptable suppliers
  3. score_severity()           — MAE on the CRITICAL=4..LOW=1 scale
  4. score_confidence()         — binary in-range check
  5. score_swing_condition()    — keyword coverage + length + negation-structure heuristics

Plus aggregation:
  - aggregate_branch_metrics()  — rolls up per-scenario scores into branch summaries
  - compute_ab_lift()           — full vs stripped lift per metric family

All scorers are PROGRAMMATIC — no LLM-as-judge. The bias is the rubric (mine);
the scoring is deterministic. The eval report can be re-derived from raw outputs
without re-spending on LLM calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Severity mapping (RUBRIC.md §3) ────────────────────────────────────────────

_SEVERITY_TO_INT: dict[str, int] = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1,
}


# ── Per-scenario scoring ──────────────────────────────────────────────────────

@dataclass
class ActionClassScore:
    """Result of scoring one scenario's action_class."""

    strict_match: bool
    in_acceptable_set: bool
    expected: str
    actual: str | None


def score_action_class(
    *,
    actual_action: str | None,
    expected_action: str,
    acceptable_actions: list[str],
) -> ActionClassScore:
    """Score the system's chosen action_class against the rubric.

    Per RUBRIC.md §1 and §6 — strict + acceptable-set scoring.
    """
    if actual_action is None:
        return ActionClassScore(
            strict_match=False,
            in_acceptable_set=False,
            expected=expected_action,
            actual=None,
        )
    return ActionClassScore(
        strict_match=(actual_action == expected_action),
        in_acceptable_set=(actual_action in acceptable_actions),
        expected=expected_action,
        actual=actual_action,
    )


@dataclass
class SupplierChoiceScore:
    """Result of scoring one scenario's supplier recommendation."""

    in_acceptable_set: bool
    correctly_refrained: bool
    expected_set: list[str]
    actual: str | None


def score_supplier_choice(
    *,
    actual_supplier: str | None,
    acceptable_suppliers: list[str],
) -> SupplierChoiceScore:
    """Score the recommended supplier per RUBRIC.md §2.

    Handles the "correctly refrained" case: when the rubric says no acceptable
    supplier exists (acceptable_suppliers == []), the system should NOT name
    one. Naming one is wrong; refraining is correct.
    """
    if not acceptable_suppliers:
        # The rubric expects no supplier recommendation.
        correctly_refrained = actual_supplier in (None, "", "none", "N/A")
        return SupplierChoiceScore(
            in_acceptable_set=False,  # N/A — no acceptable set
            correctly_refrained=correctly_refrained,
            expected_set=[],
            actual=actual_supplier,
        )

    return SupplierChoiceScore(
        in_acceptable_set=(actual_supplier in acceptable_suppliers),
        correctly_refrained=False,
        expected_set=acceptable_suppliers,
        actual=actual_supplier,
    )


def score_severity(
    *,
    actual_severity: str | None,
    expected_severity: str,
) -> float:
    """Score severity rating per RUBRIC.md §3.

    Maps CRITICAL=4 ... LOW=1 → returns absolute error (0-3).
    Returns 4.0 (max + 1 sentinel) if actual is None or unparseable.
    """
    if actual_severity not in _SEVERITY_TO_INT:
        return 4.0  # Sentinel value — actual unavailable / unparseable
    expected_int = _SEVERITY_TO_INT.get(expected_severity)
    if expected_int is None:
        raise ValueError(f"Unknown expected severity: {expected_severity!r}")
    return float(abs(_SEVERITY_TO_INT[actual_severity] - expected_int))


def score_confidence(
    *,
    actual_confidence: str | None,
    confidence_range: list[str],
) -> bool:
    """Score confidence per RUBRIC.md §4 — binary in-range check."""
    if actual_confidence is None:
        return False
    return actual_confidence in confidence_range


@dataclass
class SwingConditionScore:
    """Result of scoring one scenario's swing_condition string."""

    keyword_coverage: int
    keyword_total: int
    length_in_range: bool
    has_negation_structure: bool
    raw_text: str


def score_swing_condition(
    *,
    actual_text: str | None,
    keyword_hints: list[str],
) -> SwingConditionScore:
    """Score the swing_condition string per RUBRIC.md §5.

    Three signals (each interpretable on its own):
      - keyword coverage: hint substrings present (case-insensitive)
      - length in range: 5-30 words (terse signals "vague"; long signals "essay")
      - negation structure: contains 'if', 'would', 'unless', 'were' — signals a
        proper flip-condition rather than a confidence hedge
    """
    text = (actual_text or "").strip()
    text_lower = text.lower()
    keyword_total = len(keyword_hints)
    keyword_coverage = sum(
        1 for hint in keyword_hints if hint.lower() in text_lower
    )
    word_count = len(text.split())
    length_in_range = 5 <= word_count <= 30
    has_negation_structure = any(
        marker in text_lower for marker in (" if ", " would ", " unless ", " were ", "if ", "would ")
    )
    return SwingConditionScore(
        keyword_coverage=keyword_coverage,
        keyword_total=keyword_total,
        length_in_range=length_in_range,
        has_negation_structure=has_negation_structure,
        raw_text=text,
    )


# ── Per-scenario aggregate ────────────────────────────────────────────────────

@dataclass
class ScenarioScores:
    """All five metric-family scores for one scenario × one branch × one rep."""

    scenario_id: str
    branch: str
    rep: int
    action_class: ActionClassScore
    supplier_choice: SupplierChoiceScore
    severity_mae: float
    confidence_correct: bool
    swing_condition: SwingConditionScore


def score_scenario_result(
    scenario_expected: dict[str, Any],
    result: Any,  # ScenarioResult — typed as Any to avoid runners ↔ scorers cycle
) -> ScenarioScores:
    """Apply all five metric families to one ScenarioResult.

    `scenario_expected` is the `expected` dict from the scenario JSON.
    """
    return ScenarioScores(
        scenario_id=result.scenario_id,
        branch=result.branch,
        rep=result.rep,
        action_class=score_action_class(
            actual_action=result.final_action,
            expected_action=scenario_expected["action_class"],
            acceptable_actions=scenario_expected["acceptable_actions"],
        ),
        supplier_choice=score_supplier_choice(
            actual_supplier=result.recommended_supplier,
            acceptable_suppliers=scenario_expected.get("acceptable_suppliers", []),
        ),
        severity_mae=score_severity(
            actual_severity=result.severity,
            expected_severity=scenario_expected["severity"],
        ),
        confidence_correct=score_confidence(
            actual_confidence=result.confidence,
            confidence_range=scenario_expected["confidence_range"],
        ),
        swing_condition=score_swing_condition(
            actual_text=result.swing_condition,
            keyword_hints=scenario_expected.get("swing_condition_keyword_hints", []),
        ),
    )


# ── Branch-level aggregation ──────────────────────────────────────────────────

@dataclass
class BranchMetrics:
    """Aggregate metrics across all scenarios for one branch (full or stripped)."""

    branch: str
    n_scenarios: int
    strict_action_accuracy: float
    acceptable_action_accuracy: float
    supplier_in_set_rate: float
    supplier_refrain_rate: float
    severity_mae_mean: float
    confidence_correct_rate: float
    swing_condition_keyword_coverage_mean: float
    swing_condition_length_in_range_rate: float
    swing_condition_negation_structure_rate: float
    per_tier_strict_accuracy: dict[str, float] = field(default_factory=dict)


def aggregate_branch_metrics(
    branch: str,
    scenario_scores: list[tuple[ScenarioScores, str]],  # (scores, tier)
) -> BranchMetrics:
    """Roll up per-scenario scores into branch-level aggregates.

    `scenario_scores` is a list of (ScenarioScores, tier_string) pairs.
    """
    n = len(scenario_scores)
    if n == 0:
        # Avoid division-by-zero on empty input — return zeroed metrics.
        return BranchMetrics(
            branch=branch,
            n_scenarios=0,
            strict_action_accuracy=0.0,
            acceptable_action_accuracy=0.0,
            supplier_in_set_rate=0.0,
            supplier_refrain_rate=0.0,
            severity_mae_mean=0.0,
            confidence_correct_rate=0.0,
            swing_condition_keyword_coverage_mean=0.0,
            swing_condition_length_in_range_rate=0.0,
            swing_condition_negation_structure_rate=0.0,
        )

    strict_hits = sum(1 for s, _ in scenario_scores if s.action_class.strict_match)
    accept_hits = sum(1 for s, _ in scenario_scores if s.action_class.in_acceptable_set)

    # Supplier metric splits by "had-acceptable-set" vs "expected-to-refrain"
    suppliers_with_set = [s for s, _ in scenario_scores if s.supplier_choice.expected_set]
    suppliers_no_set = [s for s, _ in scenario_scores if not s.supplier_choice.expected_set]
    supplier_in_set_rate = (
        sum(1 for s in suppliers_with_set if s.supplier_choice.in_acceptable_set) / len(suppliers_with_set)
        if suppliers_with_set else 0.0
    )
    supplier_refrain_rate = (
        sum(1 for s in suppliers_no_set if s.supplier_choice.correctly_refrained) / len(suppliers_no_set)
        if suppliers_no_set else 0.0
    )

    severity_mae_mean = sum(s.severity_mae for s, _ in scenario_scores) / n
    confidence_correct_rate = sum(1 for s, _ in scenario_scores if s.confidence_correct) / n

    swing_coverage_mean = (
        sum(s.swing_condition.keyword_coverage / max(1, s.swing_condition.keyword_total)
            for s, _ in scenario_scores)
        / n
    )
    swing_length_rate = sum(1 for s, _ in scenario_scores if s.swing_condition.length_in_range) / n
    swing_negation_rate = sum(1 for s, _ in scenario_scores if s.swing_condition.has_negation_structure) / n

    # Per-tier breakdown
    per_tier: dict[str, list[bool]] = {}
    for scores, tier in scenario_scores:
        per_tier.setdefault(tier, []).append(scores.action_class.strict_match)
    per_tier_strict = {tier: sum(v) / len(v) for tier, v in per_tier.items() if v}

    return BranchMetrics(
        branch=branch,
        n_scenarios=n,
        strict_action_accuracy=strict_hits / n,
        acceptable_action_accuracy=accept_hits / n,
        supplier_in_set_rate=supplier_in_set_rate,
        supplier_refrain_rate=supplier_refrain_rate,
        severity_mae_mean=severity_mae_mean,
        confidence_correct_rate=confidence_correct_rate,
        swing_condition_keyword_coverage_mean=swing_coverage_mean,
        swing_condition_length_in_range_rate=swing_length_rate,
        swing_condition_negation_structure_rate=swing_negation_rate,
        per_tier_strict_accuracy=per_tier_strict,
    )


# ── A/B lift ──────────────────────────────────────────────────────────────────

@dataclass
class ABLiftResult:
    """The centerpiece deliverable of the eval — measured deliberation lift."""

    metric: str
    full_pipeline_value: float
    stripped_pipeline_value: float
    lift_absolute: float
    lift_relative: float | None
    interpretation: str


def compute_ab_lift(
    *,
    full_metrics: BranchMetrics,
    stripped_metrics: BranchMetrics,
) -> list[ABLiftResult]:
    """Compute deliberation A/B lift across all metric families.

    Returns one ABLiftResult per metric. interpretation uses the four-outcome
    framework from the scope spec.
    """
    # Metric → (full_value, stripped_value, higher-is-better, label)
    metrics = [
        ("strict_action_accuracy", full_metrics.strict_action_accuracy,
         stripped_metrics.strict_action_accuracy, True, "Strict action accuracy"),
        ("acceptable_action_accuracy", full_metrics.acceptable_action_accuracy,
         stripped_metrics.acceptable_action_accuracy, True, "Acceptable-set action accuracy"),
        ("supplier_in_set_rate", full_metrics.supplier_in_set_rate,
         stripped_metrics.supplier_in_set_rate, True, "Supplier in-set rate"),
        ("severity_mae", full_metrics.severity_mae_mean,
         stripped_metrics.severity_mae_mean, False, "Severity MAE (lower=better)"),
        ("confidence_correct_rate", full_metrics.confidence_correct_rate,
         stripped_metrics.confidence_correct_rate, True, "Confidence-range correct rate"),
        ("swing_keyword_coverage", full_metrics.swing_condition_keyword_coverage_mean,
         stripped_metrics.swing_condition_keyword_coverage_mean, True, "Swing-condition keyword coverage"),
    ]

    results = []
    for key, full_val, strip_val, higher_better, label in metrics:
        lift_abs = full_val - strip_val
        # For higher-is-better metrics, positive lift = full wins.
        # For lower-is-better (MAE), positive lift = full LOSES (more error).
        full_wins = (higher_better and lift_abs > 0.01) or (not higher_better and lift_abs < -0.01)
        full_loses = (higher_better and lift_abs < -0.01) or (not higher_better and lift_abs > 0.01)

        if abs(lift_abs) < 0.01:
            interpretation = "≈0 — deliberation does not add measurable value on this metric"
        elif full_wins:
            interpretation = f"full pipeline beats stripped by {abs(lift_abs):.3f} — deliberation adds value"
        elif full_loses:
            interpretation = f"full pipeline LOSES to stripped by {abs(lift_abs):.3f} — investigate"
        else:
            interpretation = "indeterminate"

        rel = None
        if strip_val != 0:
            rel = lift_abs / strip_val

        results.append(ABLiftResult(
            metric=label,
            full_pipeline_value=full_val,
            stripped_pipeline_value=strip_val,
            lift_absolute=lift_abs,
            lift_relative=rel,
            interpretation=interpretation,
        ))

    return results
