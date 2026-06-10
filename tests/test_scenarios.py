"""Day-2 tests: scenario schema validation + rubric self-check audit.

These tests run in CI without LLM calls. They guard three properties:

  1. Schema conformance — every scenario_NNN.json validates against the
     Pydantic Scenario model in eval/schemas.py.
  2. Rubric self-consistency — every scenario's labeled action is producible
     by the rubric's own deterministic rules (the bias-reduction check).
  3. Tier invariants — Clear-Act scenarios canonical to immediate_switch,
     Clear-Wait to wait_and_monitor.

If any of these fail, the eval's evidentiary foundation is broken. Fix before
running expensive LLM eval runs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from eval.rubric_audit import rubric_canonical_action
from eval.schemas import SCENARIOS_DIR, load_scenario


# pytest collects parametrized tests at import time, so we need scenario discovery
# to happen at module level — collected once, reused for every parametrize call.
ALL_SCENARIO_PATHS = sorted(SCENARIOS_DIR.glob("scenario_*.json"))


# ── Discovery ─────────────────────────────────────────────────────────────────

def test_at_least_one_scenario_committed():
    """Day 2 ships scenarios; if zero are present, the scaffold is incomplete."""
    assert len(ALL_SCENARIO_PATHS) >= 1, (
        f"No scenario_*.json files found in {SCENARIOS_DIR}. Day 2 should ship at least one."
    )


# ── Schema validation ────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ALL_SCENARIO_PATHS, ids=lambda p: p.stem)
def test_scenario_validates_against_schema(path: Path):
    """Every scenario_NNN.json must parse cleanly under the Pydantic Scenario model.

    Errors here have a path-based message (e.g. 'input.primary_supplier.delay_days:
    must be >= 0') that pinpoints the malformed field.
    """
    load_scenario(path)


@pytest.mark.parametrize("path", ALL_SCENARIO_PATHS, ids=lambda p: p.stem)
def test_scenario_id_matches_filename(path: Path):
    """The 'id' field inside the JSON must match the filename stem.

    Catches copy-paste-and-rename bugs where someone duplicated scenario_001.json
    to scenario_011.json without updating the id field inside.
    """
    scenario = load_scenario(path)
    assert scenario.id == path.stem, (
        f"Scenario file {path.name} has id '{scenario.id}'; filename and id must agree."
    )


# ── Rubric self-check ───────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ALL_SCENARIO_PATHS, ids=lambda p: p.stem)
def test_rubric_canonical_action_in_acceptable_set(path: Path):
    """Bias-reduction discipline: the rubric's own deterministic rules must
    reproduce an action consistent with the scenario's `acceptable_actions` set.

    If THIS test fails on a scenario, one of two things is true:
      A. The rubric is incomplete and needs new rules added (most likely for
         Ambiguous-tier scenarios that the current rules don't cover well).
      B. The scenario was mislabeled relative to the rubric.
    Either way, surface BEFORE running expensive eval runs.
    """
    scenario = load_scenario(path)
    canonical = rubric_canonical_action(scenario.input)
    assert canonical in scenario.expected.acceptable_actions, (
        f"Scenario {scenario.id} ({scenario.tier}): "
        f"rubric_canonical_action returned '{canonical}', not in acceptable_actions "
        f"{scenario.expected.acceptable_actions}. "
        "Either the rubric needs more rules, or this scenario is inconsistently labeled."
    )


# ── Tier invariants ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ALL_SCENARIO_PATHS, ids=lambda p: p.stem)
def test_clear_act_scenarios_canonical_to_immediate_switch(path: Path):
    """Clear-Act scenarios should produce immediate_switch under the rubric's rules."""
    scenario = load_scenario(path)
    if scenario.tier != "clear_act":
        pytest.skip("not a clear_act scenario")
    canonical = rubric_canonical_action(scenario.input)
    assert canonical == "immediate_switch", (
        f"{scenario.id} is tagged clear_act but rubric_canonical_action returned '{canonical}'. "
        "Clear-act tier implies the rubric's rules should fire immediate_switch — "
        "either re-classify the scenario tier or add a rubric rule."
    )


@pytest.mark.parametrize("path", ALL_SCENARIO_PATHS, ids=lambda p: p.stem)
def test_clear_wait_scenarios_canonical_to_wait_and_monitor(path: Path):
    """Clear-Wait scenarios should produce wait_and_monitor under the rubric's rules."""
    scenario = load_scenario(path)
    if scenario.tier != "clear_wait":
        pytest.skip("not a clear_wait scenario")
    canonical = rubric_canonical_action(scenario.input)
    assert canonical == "wait_and_monitor", (
        f"{scenario.id} is tagged clear_wait but rubric_canonical_action returned '{canonical}'. "
        "Clear-wait tier implies no immediate_switch / emergency_spot_buy triggers should fire."
    )


@pytest.mark.parametrize("path", ALL_SCENARIO_PATHS, ids=lambda p: p.stem)
def test_ambiguous_scenarios_not_canonical_to_wait(path: Path):
    """Ambiguous scenarios should NOT canonical to wait_and_monitor.

    The Ambiguous tier exists for cases where action IS warranted but multiple
    actions are defensible. If a scenario tagged 'ambiguous' canonicals to
    wait_and_monitor, then either:
      - The scenario isn't actually ambiguous (it's a Clear-Wait that was mis-tagged), or
      - The rubric is missing a rule that should fire (most likely a partial_order
        or hedging rule).
    Surfaces tier-classification drift before it pollutes eval results.
    """
    scenario = load_scenario(path)
    if scenario.tier != "ambiguous":
        pytest.skip("not an ambiguous scenario")
    canonical = rubric_canonical_action(scenario.input)
    assert canonical != "wait_and_monitor", (
        f"{scenario.id} is tagged ambiguous but rubric_canonical_action returned "
        "'wait_and_monitor'. Ambiguous-tier scenarios should fire some action under "
        "the rubric's rules; if the rubric returns wait, either retag the scenario "
        "as clear_wait or add a missing rubric rule."
    )


# ── Tier balance ────────────────────────────────────────────────────────────

def test_day_2_scenario_balance():
    """Day 2 baseline: 5 Clear-Act + 5 Clear-Wait. Historical floor.

    Documents the Day-2 ship state. The minimum-5 floor stays because we never
    want to drop below this baseline.
    """
    by_tier: dict[str, int] = {}
    for path in ALL_SCENARIO_PATHS:
        scenario = load_scenario(path)
        by_tier[scenario.tier] = by_tier.get(scenario.tier, 0) + 1
    assert by_tier.get("clear_act", 0) >= 5, f"Expected ≥5 clear_act scenarios, got {by_tier}"
    assert by_tier.get("clear_wait", 0) >= 5, f"Expected ≥5 clear_wait scenarios, got {by_tier}"


def test_day_3_scenario_balance():
    """Day 3 adds 5 Ambiguous tier scenarios on top of Day 2's baseline."""
    by_tier: dict[str, int] = {}
    for path in ALL_SCENARIO_PATHS:
        scenario = load_scenario(path)
        by_tier[scenario.tier] = by_tier.get(scenario.tier, 0) + 1
    assert by_tier.get("ambiguous", 0) >= 5, f"Expected ≥5 ambiguous scenarios, got {by_tier}"


def test_day_4_scenario_balance():
    """Day 4 adds 6 Edge tier scenarios.

    The Edge tier deliberately spans multiple canonical actions (no-alt cases
    can produce emergency_spot_buy OR wait_and_monitor depending on delay-vs-cover;
    customer-tier extremes can produce immediate_switch OR wait_and_monitor).
    There's no single tier-invariant action class for Edge — the general
    test_rubric_canonical_action_in_acceptable_set covers correctness.
    """
    by_tier: dict[str, int] = {}
    for path in ALL_SCENARIO_PATHS:
        scenario = load_scenario(path)
        by_tier[scenario.tier] = by_tier.get(scenario.tier, 0) + 1
    assert by_tier.get("edge", 0) >= 6, f"Expected ≥6 edge scenarios, got {by_tier}"


def test_day_5_scenario_balance():
    """Day 5 adds 7 Adversarial tier scenarios.

    Adversarial scenarios deliberately span multiple canonical actions because
    each probes a distinct failure surface (boundary thresholds, prompt
    injection, conflicting signals, numerical extremes, cold-start suppliers).
    No tier-invariant action class — correctness is verified by the general
    rubric self-check.
    """
    by_tier: dict[str, int] = {}
    for path in ALL_SCENARIO_PATHS:
        scenario = load_scenario(path)
        by_tier[scenario.tier] = by_tier.get(scenario.tier, 0) + 1
    assert by_tier.get("adversarial", 0) >= 7, f"Expected ≥7 adversarial scenarios, got {by_tier}"


def test_day_6_scenario_balance():
    """Day 6 adds 6 Distribution Shift scenarios — the final tier.

    Distribution Shift scenarios probe schema-bounded edges (extreme counts,
    extreme values, identical entries, zero-time-to-deadline). Like other
    non-clear tiers, no single canonical action — correctness via rubric
    self-check.
    """
    by_tier: dict[str, int] = {}
    for path in ALL_SCENARIO_PATHS:
        scenario = load_scenario(path)
        by_tier[scenario.tier] = by_tier.get(scenario.tier, 0) + 1
    assert by_tier.get("distribution_shift", 0) >= 6, (
        f"Expected ≥6 distribution_shift scenarios, got {by_tier}"
    )


def test_all_six_tiers_represented():
    """End-of-Day-6 milestone — every tier from the scope spec has scenarios."""
    by_tier: dict[str, int] = {}
    for path in ALL_SCENARIO_PATHS:
        scenario = load_scenario(path)
        by_tier[scenario.tier] = by_tier.get(scenario.tier, 0) + 1
    required_tiers = {
        "clear_act", "clear_wait", "ambiguous", "edge", "adversarial", "distribution_shift",
    }
    missing = required_tiers - by_tier.keys()
    assert not missing, f"Tiers without scenarios: {missing}"


# ── Negative tests: schema rejects malformed scenarios ──────────────────────

def test_schema_rejects_action_class_not_in_acceptable_actions(tmp_path):
    """If a scenario claims action_class X but acceptable_actions omits X, reject."""
    bad_data = {
        "id": "scenario_999",
        "name": "Invalid: primary action not in acceptable set",
        "tier": "clear_act",
        "input": {
            "sku": "SKU-XX",
            "stock_pct": 0.5,
            "hours_to_stockout": 100,
            "primary_supplier": {
                "name": "Foo", "delay_days": 5, "reliability_score": 0.9, "uncertainty": "LOW",
            },
            "alternative_suppliers": [],
            "customer_orders_at_risk": [],
            "price_spike_pct": 0.0,
        },
        "expected": {
            "action_class": "immediate_switch",
            "acceptable_actions": ["wait_and_monitor"],
            "acceptable_suppliers": [],
            "severity": "LOW",
            "confidence_range": ["HIGH"],
            "swing_condition_keyword_hints": [],
        },
        "rubric_notes": "intentionally malformed for the negative test",
    }
    bad_path = tmp_path / "scenario_999.json"
    bad_path.write_text(json.dumps(bad_data), encoding="utf-8")
    with pytest.raises(ValidationError, match="acceptable_actions"):
        load_scenario(bad_path)


def test_schema_rejects_acceptable_supplier_not_in_input(tmp_path):
    """acceptable_suppliers may only name suppliers from input.alternative_suppliers."""
    bad_data = {
        "id": "scenario_998",
        "name": "Invalid: acceptable supplier not in input",
        "tier": "clear_act",
        "input": {
            "sku": "SKU-XX",
            "stock_pct": 0.05,
            "hours_to_stockout": 10,
            "primary_supplier": {
                "name": "Foo", "delay_days": 30, "reliability_score": 0.9, "uncertainty": "LOW",
            },
            "alternative_suppliers": [
                {"name": "RealAlt", "lead_time_days": 3, "unit_cost_pct_baseline": 1.1, "uncertainty": "MEDIUM"},
            ],
            "customer_orders_at_risk": [],
            "price_spike_pct": 0.0,
        },
        "expected": {
            "action_class": "immediate_switch",
            "acceptable_actions": ["immediate_switch"],
            "acceptable_suppliers": ["GhostAlt"],
            "severity": "CRITICAL",
            "confidence_range": ["HIGH"],
            "swing_condition_keyword_hints": [],
        },
        "rubric_notes": "intentionally malformed for the negative test",
    }
    bad_path = tmp_path / "scenario_998.json"
    bad_path.write_text(json.dumps(bad_data), encoding="utf-8")
    with pytest.raises(ValidationError, match="acceptable_suppliers"):
        load_scenario(bad_path)
