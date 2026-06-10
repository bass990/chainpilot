"""Day-8 tests for eval/scorers.py — deterministic scoring functions.

These tests don't touch the LLM or scenario files — they verify the scoring
arithmetic itself. The bias is the rubric (mine); the scoring is deterministic;
these tests prove the determinism.
"""
from __future__ import annotations

from eval.scorers import (
    BranchMetrics,
    ScenarioScores,
    aggregate_branch_metrics,
    compute_ab_lift,
    score_action_class,
    score_confidence,
    score_severity,
    score_supplier_choice,
    score_swing_condition,
)


# ── score_action_class ───────────────────────────────────────────────────────

def test_score_action_class_strict_match():
    """Strict match wins both flags."""
    s = score_action_class(
        actual_action="immediate_switch",
        expected_action="immediate_switch",
        acceptable_actions=["immediate_switch", "partial_order"],
    )
    assert s.strict_match is True
    assert s.in_acceptable_set is True


def test_score_action_class_acceptable_but_not_strict():
    """Wrong-but-acceptable scores acceptable-set but not strict."""
    s = score_action_class(
        actual_action="partial_order",
        expected_action="immediate_switch",
        acceptable_actions=["immediate_switch", "partial_order"],
    )
    assert s.strict_match is False
    assert s.in_acceptable_set is True


def test_score_action_class_neither():
    """Truly wrong action fails both."""
    s = score_action_class(
        actual_action="wait_and_monitor",
        expected_action="immediate_switch",
        acceptable_actions=["immediate_switch", "partial_order"],
    )
    assert s.strict_match is False
    assert s.in_acceptable_set is False


def test_score_action_class_none():
    """None action is False on both flags."""
    s = score_action_class(
        actual_action=None,
        expected_action="immediate_switch",
        acceptable_actions=["immediate_switch"],
    )
    assert s.strict_match is False
    assert s.in_acceptable_set is False


# ── score_supplier_choice ────────────────────────────────────────────────────

def test_supplier_in_set():
    s = score_supplier_choice(actual_supplier="Alt-A", acceptable_suppliers=["Alt-A", "Alt-B"])
    assert s.in_acceptable_set is True
    assert s.correctly_refrained is False


def test_supplier_out_of_set():
    s = score_supplier_choice(actual_supplier="Alt-X", acceptable_suppliers=["Alt-A", "Alt-B"])
    assert s.in_acceptable_set is False


def test_supplier_correctly_refrained_when_no_set():
    """Rubric says no supplier expected; system correctly didn't name one."""
    s = score_supplier_choice(actual_supplier=None, acceptable_suppliers=[])
    assert s.correctly_refrained is True


def test_supplier_incorrectly_named_when_no_set():
    """Rubric says no supplier expected; system named one anyway."""
    s = score_supplier_choice(actual_supplier="Some Alt", acceptable_suppliers=[])
    assert s.correctly_refrained is False


# ── score_severity ───────────────────────────────────────────────────────────

def test_severity_exact_match():
    assert score_severity(actual_severity="HIGH", expected_severity="HIGH") == 0.0


def test_severity_one_off():
    # HIGH=3, CRITICAL=4 → MAE = 1
    assert score_severity(actual_severity="HIGH", expected_severity="CRITICAL") == 1.0
    # CRITICAL=4, MEDIUM=2 → MAE = 2
    assert score_severity(actual_severity="CRITICAL", expected_severity="MEDIUM") == 2.0


def test_severity_none_returns_sentinel():
    """Missing severity returns 4.0 sentinel."""
    assert score_severity(actual_severity=None, expected_severity="HIGH") == 4.0


def test_severity_unparseable_returns_sentinel():
    assert score_severity(actual_severity="UNKNOWN", expected_severity="HIGH") == 4.0


# ── score_confidence ─────────────────────────────────────────────────────────

def test_confidence_in_range():
    assert score_confidence(actual_confidence="HIGH", confidence_range=["HIGH", "MEDIUM"]) is True


def test_confidence_out_of_range():
    assert score_confidence(actual_confidence="LOW", confidence_range=["HIGH"]) is False


def test_confidence_none():
    assert score_confidence(actual_confidence=None, confidence_range=["HIGH"]) is False


# ── score_swing_condition ────────────────────────────────────────────────────

def test_swing_condition_perfect():
    """All hints present, length OK, negation structure present."""
    s = score_swing_condition(
        actual_text="If the PLATINUM customer deadline were extended, we would prefer to wait.",
        keyword_hints=["PLATINUM", "deadline"],
    )
    assert s.keyword_coverage == 2
    assert s.keyword_total == 2
    assert s.length_in_range is True
    assert s.has_negation_structure is True


def test_swing_condition_too_short():
    """Length below 5 words — vague."""
    s = score_swing_condition(
        actual_text="High risk", keyword_hints=["risk"],
    )
    assert s.length_in_range is False


def test_swing_condition_too_long():
    """Length over 30 words — essay."""
    long_text = " ".join(["word"] * 35)
    s = score_swing_condition(actual_text=long_text, keyword_hints=[])
    assert s.length_in_range is False


def test_swing_condition_no_negation():
    """No 'if/would/unless/were' — confidence hedge instead of flip condition."""
    s = score_swing_condition(
        actual_text="The recommendation has medium confidence due to supplier risk and uncertainty.",
        keyword_hints=["supplier", "uncertainty"],
    )
    assert s.has_negation_structure is False
    assert s.keyword_coverage == 2


def test_swing_condition_none_text():
    s = score_swing_condition(actual_text=None, keyword_hints=["X"])
    assert s.keyword_coverage == 0
    assert s.length_in_range is False
    assert s.has_negation_structure is False


def test_swing_condition_partial_keyword_coverage():
    """Only some hints present."""
    s = score_swing_condition(
        actual_text="If the PLATINUM order arrives on time, no action needed.",
        keyword_hints=["PLATINUM", "deadline", "supplier"],
    )
    assert s.keyword_coverage == 1  # only PLATINUM
    assert s.keyword_total == 3


# ── aggregate_branch_metrics ─────────────────────────────────────────────────

def _make_scores(
    strict: bool, acceptable: bool, sev_mae: float, conf_correct: bool,
    sup_in_set: bool, sup_refrain: bool, sup_expected_set: list[str],
    swing_coverage: int, swing_total: int, swing_length: bool, swing_neg: bool,
    tier: str,
) -> tuple[ScenarioScores, str]:
    from eval.scorers import (
        ActionClassScore,
        SupplierChoiceScore,
        SwingConditionScore,
    )
    return (
        ScenarioScores(
            scenario_id="s",
            branch="full",
            rep=1,
            action_class=ActionClassScore(
                strict_match=strict, in_acceptable_set=acceptable,
                expected="x", actual="y",
            ),
            supplier_choice=SupplierChoiceScore(
                in_acceptable_set=sup_in_set,
                correctly_refrained=sup_refrain,
                expected_set=sup_expected_set,
                actual=None,
            ),
            severity_mae=sev_mae,
            confidence_correct=conf_correct,
            swing_condition=SwingConditionScore(
                keyword_coverage=swing_coverage,
                keyword_total=swing_total,
                length_in_range=swing_length,
                has_negation_structure=swing_neg,
                raw_text="",
            ),
        ),
        tier,
    )


def test_aggregate_empty_returns_zeros():
    m = aggregate_branch_metrics("full", [])
    assert m.n_scenarios == 0
    assert m.strict_action_accuracy == 0.0


def test_aggregate_two_scenarios_one_correct():
    scores = [
        _make_scores(True, True, 0.0, True, True, False, ["X"], 1, 1, True, True, "clear_act"),
        _make_scores(False, False, 1.0, False, False, False, ["Y"], 0, 1, False, False, "clear_wait"),
    ]
    m = aggregate_branch_metrics("full", scores)
    assert m.n_scenarios == 2
    assert m.strict_action_accuracy == 0.5
    assert m.acceptable_action_accuracy == 0.5
    assert m.supplier_in_set_rate == 0.5
    assert m.severity_mae_mean == 0.5
    assert m.confidence_correct_rate == 0.5
    assert m.swing_condition_keyword_coverage_mean == 0.5


def test_aggregate_per_tier_breakdown():
    scores = [
        _make_scores(True, True, 0.0, True, True, False, ["X"], 1, 1, True, True, "clear_act"),
        _make_scores(True, True, 0.0, True, True, False, ["X"], 1, 1, True, True, "clear_act"),
        _make_scores(False, False, 1.0, False, False, False, ["Y"], 0, 1, False, False, "ambiguous"),
    ]
    m = aggregate_branch_metrics("full", scores)
    assert m.per_tier_strict_accuracy["clear_act"] == 1.0
    assert m.per_tier_strict_accuracy["ambiguous"] == 0.0


def test_aggregate_supplier_refrain_split():
    """Scenarios with no expected supplier are scored separately from those with."""
    scores = [
        # Scenario A: had-set, system picked right one
        _make_scores(True, True, 0.0, True, True, False, ["Alt-A"], 1, 1, True, True, "clear_act"),
        # Scenario B: no-set, system correctly refrained
        _make_scores(True, True, 0.0, True, False, True, [], 1, 1, True, True, "clear_wait"),
        # Scenario C: no-set, system named one anyway
        _make_scores(True, True, 0.0, True, False, False, [], 1, 1, True, True, "clear_wait"),
    ]
    m = aggregate_branch_metrics("full", scores)
    assert m.supplier_in_set_rate == 1.0  # 1/1 had-set scenarios scored
    assert m.supplier_refrain_rate == 0.5  # 1/2 no-set scenarios refrained correctly


# ── compute_ab_lift ──────────────────────────────────────────────────────────

def _make_branch(strict: float, acceptable: float, sev_mae: float, name: str) -> BranchMetrics:
    return BranchMetrics(
        branch=name,
        n_scenarios=5,
        strict_action_accuracy=strict,
        acceptable_action_accuracy=acceptable,
        supplier_in_set_rate=0.8,
        supplier_refrain_rate=0.5,
        severity_mae_mean=sev_mae,
        confidence_correct_rate=0.7,
        swing_condition_keyword_coverage_mean=0.5,
        swing_condition_length_in_range_rate=0.9,
        swing_condition_negation_structure_rate=0.6,
    )


def test_ab_lift_full_wins_strict_accuracy():
    full = _make_branch(strict=0.9, acceptable=0.95, sev_mae=0.5, name="full")
    strip = _make_branch(strict=0.7, acceptable=0.8, sev_mae=0.5, name="stripped")
    lifts = compute_ab_lift(full_metrics=full, stripped_metrics=strip)

    # Find the strict-action-accuracy lift
    strict_lift = next(lift for lift in lifts if "Strict action" in lift.metric)
    assert strict_lift.lift_absolute > 0
    assert "deliberation adds value" in strict_lift.interpretation


def test_ab_lift_stripped_wins():
    full = _make_branch(strict=0.6, acceptable=0.7, sev_mae=0.5, name="full")
    strip = _make_branch(strict=0.9, acceptable=0.95, sev_mae=0.5, name="stripped")
    lifts = compute_ab_lift(full_metrics=full, stripped_metrics=strip)

    strict_lift = next(lift for lift in lifts if "Strict action" in lift.metric)
    assert strict_lift.lift_absolute < 0
    assert "LOSES" in strict_lift.interpretation


def test_ab_lift_no_difference():
    full = _make_branch(strict=0.8, acceptable=0.9, sev_mae=0.5, name="full")
    strip = _make_branch(strict=0.8, acceptable=0.9, sev_mae=0.5, name="stripped")
    lifts = compute_ab_lift(full_metrics=full, stripped_metrics=strip)

    strict_lift = next(lift for lift in lifts if "Strict action" in lift.metric)
    assert abs(strict_lift.lift_absolute) < 0.01
    assert "does not add measurable value" in strict_lift.interpretation


def test_ab_lift_severity_inverted_higher_better():
    """Severity MAE is lower-is-better, so positive lift = full LOSES."""
    full = _make_branch(strict=0.8, acceptable=0.9, sev_mae=1.5, name="full")
    strip = _make_branch(strict=0.8, acceptable=0.9, sev_mae=0.5, name="stripped")
    lifts = compute_ab_lift(full_metrics=full, stripped_metrics=strip)

    severity_lift = next(lift for lift in lifts if "Severity" in lift.metric)
    # Full's MAE is higher (worse), so the lift IS positive but means full loses
    assert severity_lift.lift_absolute > 0
    assert "LOSES" in severity_lift.interpretation


def test_ab_lift_returns_one_per_metric_family():
    full = _make_branch(strict=0.8, acceptable=0.9, sev_mae=0.5, name="full")
    strip = _make_branch(strict=0.8, acceptable=0.9, sev_mae=0.5, name="stripped")
    lifts = compute_ab_lift(full_metrics=full, stripped_metrics=strip)
    # Should be 6 metric families per current scorers.compute_ab_lift
    assert len(lifts) == 6
    metric_labels = {lift.metric for lift in lifts}
    assert "Strict action accuracy" in metric_labels
    assert "Severity MAE (lower=better)" in metric_labels
    assert "Confidence-range correct rate" in metric_labels
