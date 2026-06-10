"""Pydantic schemas for ChainPilot eval scenarios.

Every scenario in eval/scenarios/*.json must validate against `Scenario` below.
The schema is the structural contract between scenario authors (me, today) and
the eval runners (Day 4+) that consume these files.

WHY Pydantic and not jsonschema:
  - Pydantic 2 is already in the dep tree via FastAPI; no new dep.
  - Error messages are concrete ("input.primary_supplier.delay_days: must be >= 0")
    instead of jsonschema's path-only failures.
  - Cross-field validators (e.g. "primary action must be in acceptable_actions")
    are first-class.

USAGE:
    from pathlib import Path
    from eval.schemas import load_scenario

    scenario = load_scenario(Path("eval/scenarios/scenario_001.json"))
    # raises pydantic.ValidationError on a malformed file
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Severity / confidence / tier vocabularies ─────────────────────────────────

ActionClass = Literal["immediate_switch", "partial_order", "wait_and_monitor", "emergency_spot_buy"]
Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
Confidence = Literal["HIGH", "MEDIUM", "LOW"]
Uncertainty = Literal["HIGH", "MEDIUM", "LOW"]
CustomerTier = Literal["PLATINUM", "GOLD", "SILVER", "STANDARD"]
ScenarioTier = Literal[
    "clear_act", "clear_wait", "ambiguous", "edge", "adversarial", "distribution_shift",
]


# ── Input — the disruption description fed to ChainPilot ──────────────────────

class PrimarySupplier(BaseModel):
    """The current supplier-of-record for the SKU and the state of its delivery."""

    name: str
    delay_days: int = Field(ge=0, description="Days the next shipment is delayed beyond schedule.")
    reliability_score: float = Field(ge=0.0, le=1.0)
    uncertainty: Uncertainty


class AlternativeSupplier(BaseModel):
    """A supplier ChainPilot could switch to. Multiple are typical."""

    name: str
    lead_time_days: int = Field(ge=0, description="Days from order to delivery.")
    unit_cost_pct_baseline: float = Field(gt=0, description="Cost relative to primary baseline (1.20 = +20%).")
    uncertainty: Uncertainty


class CustomerOrder(BaseModel):
    """A pending customer order whose fulfillment is at risk if we don't act."""

    customer: str
    tier: CustomerTier
    hours_to_deadline: float = Field(ge=0)


class ScenarioInput(BaseModel):
    """The full disruption-state payload."""

    sku: str
    stock_pct: float = Field(ge=0.0, le=1.0, description="Current inventory as fraction of full stock.")
    hours_to_stockout: float = Field(ge=0, description="Projected hours until stock hits zero at current burn rate.")
    primary_supplier: PrimarySupplier
    alternative_suppliers: list[AlternativeSupplier] = Field(default_factory=list)
    customer_orders_at_risk: list[CustomerOrder] = Field(default_factory=list)
    price_spike_pct: float = Field(default=0.0, description="Spot-price change vs 30-day baseline.")


# ── Expected — the rubric-labeled "correct" output ────────────────────────────

class ScenarioExpected(BaseModel):
    """What the rubric says the system should output for this scenario."""

    action_class: ActionClass = Field(description="Primary expected action.")
    acceptable_actions: list[ActionClass] = Field(
        min_length=1,
        description="Action classes the rubric tolerates for this scenario (must include action_class).",
    )
    acceptable_suppliers: list[str] = Field(
        default_factory=list,
        description="Suppliers from input.alternative_suppliers that are rubric-acceptable choices. "
                    "Empty when the action doesn't require picking one.",
    )
    severity: Severity
    confidence_range: list[Confidence] = Field(
        min_length=1,
        description="Confidence levels the rubric tolerates given the scenario's ambiguity.",
    )
    swing_condition_keyword_hints: list[str] = Field(
        default_factory=list,
        description="Keywords expected to appear in the Arbiter's swing_condition string.",
    )

    @field_validator("acceptable_actions")
    @classmethod
    def actions_unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("acceptable_actions must not contain duplicates.")
        return v

    @model_validator(mode="after")
    def primary_in_acceptable(self) -> "ScenarioExpected":
        if self.action_class not in self.acceptable_actions:
            raise ValueError(
                f"action_class '{self.action_class}' must be in acceptable_actions "
                f"{self.acceptable_actions}. The primary expected action is always "
                "one of the rubric-acceptable options."
            )
        return self


# ── The top-level scenario ────────────────────────────────────────────────────

class Scenario(BaseModel):
    """One eval scenario. Validates structure, internal consistency, and vocabularies."""

    id: str = Field(pattern=r"^scenario_\d{3}$", description="Format: 'scenario_NNN' zero-padded to 3 digits.")
    name: str = Field(min_length=1)
    tier: ScenarioTier
    input: ScenarioInput
    expected: ScenarioExpected
    rubric_notes: str = Field(min_length=1, description="Human-readable rationale for the expected labels.")

    @model_validator(mode="after")
    def acceptable_suppliers_match_input(self) -> "Scenario":
        """If acceptable_suppliers is non-empty, every name must appear in input.alternative_suppliers."""
        if not self.expected.acceptable_suppliers:
            return self
        alt_names = {alt.name for alt in self.input.alternative_suppliers}
        unknown = set(self.expected.acceptable_suppliers) - alt_names
        if unknown:
            raise ValueError(
                f"acceptable_suppliers refers to {sorted(unknown)} which are not in "
                f"input.alternative_suppliers ({sorted(alt_names)}). Scenarios must be self-consistent."
            )
        return self


# ── Loaders ──────────────────────────────────────────────────────────────────

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"


def load_scenario(path: Path | str) -> Scenario:
    """Load and validate one scenario JSON. Raises pydantic.ValidationError on failure."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return Scenario.model_validate(data)


def load_all_scenarios(*, tier: ScenarioTier | None = None) -> list[Scenario]:
    """Load every scenario_NNN.json in eval/scenarios/, optionally filtered by tier."""
    if not SCENARIOS_DIR.is_dir():
        return []
    results = []
    for p in sorted(SCENARIOS_DIR.glob("scenario_*.json")):
        s = load_scenario(p)
        if tier is None or s.tier == tier:
            results.append(s)
    return results
