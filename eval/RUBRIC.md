# Eval Scoring Rubric

> **This file is committed BEFORE any scenarios are labeled.** The discipline is critical: if the rubric is retrofitted to whatever the system happens to output, the eval becomes a sycophancy test, not an evaluation. The git history of this file (and of `scenarios/`) is part of the eval's evidence.

> **Solo-labeler caveat.** A real eval would use 2-3 trained procurement-team raters with inter-rater reliability tracking. This rubric is mine; the scenarios are mine; the scoring is programmatic against this rubric. The bias is the rubric (mine); the scoring is deterministic. See `README.md` §Honest Disclosures.

---

## 1. Action-class taxonomy

ChainPilot's `final_action` field can take 4 values. The rubric below defines when each is the "correct" call:

### `immediate_switch`

**The right call when:**
- Stockout imminent (hours_to_stockout < 24) AND a viable alternative exists
- Primary supplier delay extends beyond stock cover AND switching is cheaper than the alternative's lead time × customer-penalty product
- PLATINUM-tier customer order at risk AND the only way to fulfill is to switch

**Not the right call when:**
- An alternative supplier exists but has a quality history flag (uncertainty HIGH) AND there's time to wait for the primary
- The cost premium for the switch exceeds the expected stockout penalty

### `partial_order`

**The right call when:**
- Partial fulfillment from a known supplier (primary OR known alternative) covers the most at-risk customer orders
- Risk is real but bounded, full switch is excessive insurance
- A reliable alternative exists for partial volume; primary remains preferred for the rest

**Not the right call when:**
- Scenario indicates total supply failure (e.g., a primary supplier shutdown rather than a delay)
- No viable alternative with sufficient capacity

### `wait_and_monitor`

**The right call when:**
- Primary supplier delay is bounded AND stock cover exceeds the delay
- Alternative suppliers are inferior on cost / lead time / quality
- The disruption resolves within a forecasted window

**Not the right call when:**
- Any PLATINUM customer order is at risk in <24h
- Stock will be depleted before primary supplier recovery
- A significantly better alternative exists at acceptable cost

### `emergency_spot_buy`

**The right call when:**
- All "normal-channel" alternatives have lead times exceeding the stockout window
- Spot-market premium is acceptable given the customer-penalty exposure
- Procurement-of-record is fundamentally compromised (primary supplier insolvent, port closure indefinite)

**Not the right call when:**
- A `immediate_switch` to a known alternative supplier is feasible (use that instead, cheaper, more reliable)
- Stock cover allows time for a structured procurement decision

### Tie-breaking between adjacent actions

When two actions are both defensible, the rubric tolerates either. Each scenario lists `expected.acceptable_actions` (a set). Scoring uses both strict-match (action == primary expected) and acceptable-set match (action ∈ acceptable_actions).

### Programmatic encoding (`eval/rubric_audit.py`)

The action-class rules above are codified as a deterministic `rubric_canonical_action()` function with rules evaluated in priority order, first match wins. Priority order, catastrophic → bounded → none:

1. **Stockout < 24h + viable alt** → `immediate_switch`
2. **Delay > stock cover + viable alt** → `immediate_switch`
3. **PLATINUM at risk < 24h + viable alt** → `immediate_switch`
4. **Delay > stock cover + NO alt** → `emergency_spot_buy`
5. **PLATINUM/GOLD at material risk + reliable alt + not catastrophic** → `partial_order`
6. Default → `wait_and_monitor`

Definitions used inside the rules:

- **Viable alt:** `len(input.alternative_suppliers) > 0` (the rubric tolerates any listed alt; lead-time vs stockout-window comparison is for `acceptable_suppliers` scoring, not action selection).
- **Reliable alt:** at least one `alternative_suppliers[i].uncertainty in ("LOW", "MEDIUM")`. HIGH-uncertainty cold-start suppliers don't support a partial-volume hedge.
- **PLATINUM/GOLD at material risk:** at least one `customer_orders_at_risk[i].tier in ("PLATINUM", "GOLD") and i.hours_to_deadline < primary_supplier.delay_days * 24`, the customer's deadline lands *before* primary recovery, so the order would actually be at risk if delay materializes.

This codification is the **bias-reduction self-check.** Every scenario's labeled `action_class` is verified to be in its `acceptable_actions` set AND the canonical action produced by these rules must also land in the same set. If the canonical action falls outside the set, EITHER a rule is missing OR a scenario is mislabeled, surfaced by `tests/test_scenarios.py::test_rubric_canonical_action_in_acceptable_set` before any LLM eval runs.

---

## 2. Supplier-choice acceptability

For each scenario where the recommended action requires choosing an alternative supplier:

**Acceptable suppliers** = top 2-3 of the available alternatives ranked by a composite score:

```
score = w_lead_time × (1 / lead_time_days) +
        w_cost × (1 / unit_cost_pct_baseline) +
        w_reliability × reliability_score +
        w_uncertainty × (0 if HIGH else 0.5 if MEDIUM else 1.0)
```

Default weights: `w_lead_time = 0.40`, `w_cost = 0.25`, `w_reliability = 0.20`, `w_uncertainty = 0.15`.

The weights reflect a "stockout risk dominates cost considerations under disruption" stance, which is the rubric's domain prior. **Sensitivity to these weights is itself a finding the eval should surface.**

**Scenarios with no acceptable suppliers** (the supplier pool is genuinely too poor): correct behavior is to recommend `wait_and_monitor` and NOT name an alternative. Scoring marks this as "system correctly refrained."

---

## 3. Severity-rating rules

ChainPilot's `severity` field takes 4 values: CRITICAL / HIGH / MEDIUM / LOW. Mapped to integers for MAE scoring: CRITICAL=4, HIGH=3, MEDIUM=2, LOW=1.

### CRITICAL, any of:
- `hours_to_stockout < 24`
- A PLATINUM-tier customer order at risk in <24h
- Cost exposure > $50K AND no within-stock-cover recovery path

### HIGH, either:
- `hours_to_stockout` in 24-72h
- HIGH-tier customer at risk in <48h
- Cost exposure in $20K-$50K
- Primary supplier on a known unreliability streak

### MEDIUM, either:
- Stock cover 72h-168h AND delay extends slightly into that window
- Cost exposure $5K-$20K
- Customer-tier exposure on STANDARD or below

### LOW, either:
- Bounded delay within stock cover comfortably
- No customer order at material risk
- Cost premium well under $5K

Borderline cases: the rubric picks the higher severity when ambiguous (system should err toward caution). Scenarios specify the rubric-expected severity in `expected.severity`.

---

## 4. Confidence-level expectations

ChainPilot's Arbiter outputs `confidence: HIGH | MEDIUM | LOW`.

The rubric's expectation for each scenario is encoded in `expected.confidence_range`, a list of acceptable values.

### Heuristics for the expected confidence:

- **HIGH** when: scenario is clear (Clear Act or Clear Wait tier), alternatives are well-defined, no major uncertainty flags
- **MEDIUM** when: scenario is the Genuinely Ambiguous tier; multiple defensible actions; tradeoff is real
- **LOW** when: scenario is the Adversarial tier with cold-start suppliers or inputs the system has never seen; or when the deliberation reveals genuine disagreement between Advocate and Skeptic

**Scoring:** confidence ∈ `expected.confidence_range` → correct (binary).

**The MEDIUM-confidence trap:** the system might default to MEDIUM as a hedge. The Ambiguous tier scenarios test whether MEDIUM is actually being used appropriately (vs as a cop-out).

---

## 5. `swing_condition` quality

The Arbiter outputs a `swing_condition` field, the one fact that, if true, would flip the recommendation.

**Programmatic scoring**: `expected.swing_condition_keyword_hints` lists 2-4 keywords; the swing_condition string is scored on:
- **Keyword coverage:** did the model name at least 1 of the expected keywords?
- **Length:** within 5-30 words? Too short → vague; too long → essay
- **Negation structure:** does it state a flip condition ("if X were true, I would recommend Y instead") vs a hedge ("there is some uncertainty")?

**Note:** this is the most subjective scoring dimension. The eval reports both the raw text and the keyword-coverage score. A reviewer reading the report can disagree with the score directly.

---

## 6. The action-class confusion matrix

For each scenario, the system's `final_action` is one of 4 classes. The eval reports:
- **Strict-match accuracy:** action == primary expected action
- **Acceptable-set accuracy:** action ∈ acceptable_actions
- **Confusion matrix:** rows = expected action; columns = system action

The confusion matrix surfaces the *direction* of failures, not just the rate. Common patterns to watch for:
- **Over-acting:** expected `wait_and_monitor` but system says `immediate_switch` → false alarm
- **Under-acting:** expected `immediate_switch` but system says `wait_and_monitor` → missed risk
- **Confusing partial with immediate:** action class confusion that suggests a threshold-tuning issue

The interview narrative depends on which pattern shows up.

---

## 7. Reproducibility & versioning

- This rubric is versioned via git history. Any change to a scoring rule, weight, or threshold should be a commit with a clear rationale.
- Scenarios reference this rubric implicitly, they assume the rubric defined above. If the rubric changes, scenario `expected` fields may need to be re-derived.
- Eval reports record the rubric's git SHA at the time of run, so historical results are interpretable.

---

## 8. What this rubric does NOT cover

These are deliberate scoping boundaries, not oversights:

- **Long-horizon outcomes.** The rubric scores the recommendation at decision time; not whether the recommendation would actually have produced a good outcome over weeks (mock data doesn't extend that far).
- **Process quality** (e.g., did the agent call the right tools in the right order?). The eval scores the recommendation, not the pipeline's internal mechanics.
- **Communication quality** (RFQ email tone, Slack alert phrasing). Out of scope; would require LLM-as-judge.
- **Trust-engine outcome-validation logic** beyond what `tests/test_trust_engine.py` covers in isolation.
- **Cross-cultural / cross-industry generalization.** The rubric encodes a U.S. industrial-supply-chain prior; results don't transfer to (e.g.) fresh-food procurement without rubric adaptation.

---

*Rubric v1 · Phase 2 eval-harness build · Day 1, 2026-06-01*

*This file MUST be committed before scenario_001.json. If you see scenario files but no rubric commit predating them, the eval has lost its bias-reduction discipline, STOP and re-anchor the rubric first.*
