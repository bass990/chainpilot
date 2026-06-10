"""Rubric self-check: codify RUBRIC.md §1's action-class rules as a deterministic
function, then verify every scenario's label is consistent with what the rubric
would produce on its own.

WHY this matters:
    The hardest critique of a solo eval is "you wrote scenarios AND the rubric;
    you're scoring against your own preferences." The bias-reduction discipline
    has two pieces:
      1. Commit RUBRIC.md BEFORE labeling scenarios (Day 1).
      2. Run a self-check that says "given this input, the rubric's own rules
         would produce action class X" — and verify the labeled action is in
         the scenario's acceptable_actions set.
    If the rubric's deterministic rules can't reproduce a scenario's label,
    EITHER the rubric is incomplete (needs new rules) OR the scenario is
    mislabeled. Either way, surface it BEFORE running expensive eval runs.

The function below mirrors RUBRIC.md §1. If the rubric ever changes, this file
changes with it — and the test suite catches inconsistencies the next time
`pytest tests/` runs.
"""
from __future__ import annotations

from eval.schemas import ActionClass, ScenarioInput


# Thresholds named here mirror RUBRIC.md §1 + §3. Changing a threshold here
# without updating the rubric prose (or vice versa) is exactly the kind of drift
# the self-check is designed to catch.
_IMMINENT_STOCKOUT_HOURS = 24
_PLATINUM_AT_RISK_HOURS = 24


def _has_viable_alt(scenario_input: ScenarioInput) -> bool:
    """Whether any alternative supplier could reduce future risk.

    The rubric's prose ("a viable alternative exists") doesn't require the alt's
    lead time to beat the current stockout window — even an alternative that
    arrives AFTER stockout still ends the disruption sooner than waiting for the
    primary's 30-day delay. So "viable" here means "any alternative listed."

    Scenarios where the procurement system should refrain from naming a supplier
    have alternative_suppliers = [] in the input.
    """
    return len(scenario_input.alternative_suppliers) > 0


def _has_reliable_alt(scenario_input: ScenarioInput) -> bool:
    """Whether any alternative supplier has LOW or MEDIUM uncertainty.

    Used by the partial_order rule (Day 3+): partial_order is a "test the supplier
    with a small order" call, which is only rational when the supplier isn't a
    cold-start unknown. HIGH-uncertainty alts can't support a partial-volume
    hedge.
    """
    return any(
        alt.uncertainty in ("LOW", "MEDIUM")
        for alt in scenario_input.alternative_suppliers
    )


def _has_platinum_at_risk(scenario_input: ScenarioInput) -> bool:
    """Whether a PLATINUM-tier order is at near-term risk per RUBRIC.md §1."""
    return any(
        o.tier == "PLATINUM" and o.hours_to_deadline < _PLATINUM_AT_RISK_HOURS
        for o in scenario_input.customer_orders_at_risk
    )


def _has_high_tier_at_material_risk(scenario_input: ScenarioInput) -> bool:
    """Whether a PLATINUM or GOLD order is at material risk.

    "Material risk" per the partial_order rule: customer deadline lands BEFORE
    the primary's expected recovery (delivery would need to come from a
    different source). A GOLD customer with a deadline 200h out when the primary
    is delayed 168h is NOT at material risk — their order fulfills from the
    delayed-but-recovered primary shipment.
    """
    delay_hours = scenario_input.primary_supplier.delay_days * 24
    return any(
        o.tier in ("PLATINUM", "GOLD") and o.hours_to_deadline < delay_hours
        for o in scenario_input.customer_orders_at_risk
    )


def _delay_exceeds_stock_cover(scenario_input: ScenarioInput) -> bool:
    """Whether the primary supplier's delay extends beyond current stock cover."""
    delay_hours = scenario_input.primary_supplier.delay_days * 24
    return delay_hours > scenario_input.hours_to_stockout


def rubric_canonical_action(scenario_input: ScenarioInput) -> ActionClass:
    """Return the action class RUBRIC.md §1's rules would deterministically produce.

    Maps the prose rules to executable conditions. Rules evaluated in priority
    order — the first matching condition wins. If no rule fires, the default is
    `wait_and_monitor` (the "everything is bounded, nothing to do" residual).

    Priority order (catastrophic → bounded → none):
      1. Stockout imminent + alt           → immediate_switch
      2. Delay > cover + alt               → immediate_switch
      3. PLATINUM at risk <24h + alt       → immediate_switch
      4. Delay > cover + no alt            → emergency_spot_buy
      5. PLATINUM/GOLD at material risk +
         reliable alt + not catastrophic   → partial_order   (Day 3+)
      6. (none of the above)               → wait_and_monitor
    """
    has_alt = _has_viable_alt(scenario_input)
    stock_imminent = scenario_input.hours_to_stockout < _IMMINENT_STOCKOUT_HOURS
    delay_exceeds = _delay_exceeds_stock_cover(scenario_input)
    platinum_now = _has_platinum_at_risk(scenario_input)

    # Rule 1 — RUBRIC.md §1 immediate_switch, condition 1:
    #   "Stockout imminent (hours_to_stockout < 24) AND a viable alternative exists"
    if stock_imminent and has_alt:
        return "immediate_switch"

    # Rule 2 — RUBRIC.md §1 immediate_switch, condition 2:
    #   "Primary supplier delay extends beyond stock cover AND a viable alternative exists"
    # (the cost-vs-penalty comparison from the prose is not in the structured input
    #  schema; we treat any viable alt as enough when delay exceeds cover)
    if delay_exceeds and has_alt:
        return "immediate_switch"

    # Rule 3 — RUBRIC.md §1 immediate_switch, condition 3:
    #   "PLATINUM customer at risk AND the only way to fulfill is to switch"
    if platinum_now and has_alt:
        return "immediate_switch"

    # Rule 4 — RUBRIC.md §1 emergency_spot_buy:
    #   "All normal-channel alternatives have lead times exceeding the stockout window"
    # We map this to "delay exceeds cover AND no alternatives in the input at all."
    # If there ARE alternatives but they're insufficient, the system might still
    # rationally pick emergency_spot_buy; the rubric tolerates it via acceptable_actions.
    if delay_exceeds and not has_alt:
        return "emergency_spot_buy"

    # Rule 5 (Day 3) — RUBRIC.md §1 partial_order:
    #   "Risk is real but bounded — full switch is excessive insurance"
    #   "Partial fulfillment from a known supplier covers the most at-risk customer orders"
    #   "A reliable alternative exists for partial volume"
    #
    # Programmatic encoding:
    #   - Not catastrophic (rules 1-4 didn't fire)
    #   - There's at least one PLATINUM/GOLD customer at MATERIAL risk
    #     (deadline lands before primary recovery)
    #   - At least one alternative supplier is RELIABLE (LOW or MEDIUM uncertainty)
    # Rationale for material-risk threshold: a high-tier customer whose deadline
    # comfortably exceeds the primary delay is NOT actually at risk — they get
    # their delivery from the recovered primary on time. Without a material-risk
    # threshold, the partial_order rule would mis-fire on Clear-Wait scenarios
    # that happen to have a GOLD-tier customer with a far-out deadline.
    if _has_high_tier_at_material_risk(scenario_input) and _has_reliable_alt(scenario_input):
        return "partial_order"

    # Default — RUBRIC.md §1 wait_and_monitor:
    #   "Primary supplier delay is bounded AND stock cover exceeds the delay"
    return "wait_and_monitor"
