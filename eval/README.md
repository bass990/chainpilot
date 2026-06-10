# ChainPilot Eval Harness

> **Status (Day 1):** scaffold complete; runners + scorers are skeletons that raise `NotImplementedError`; first scenarios + scoring logic land Day 2-5; full eval ships at end of Week 3.

The eval harness measures whether ChainPilot's multi-agent pipeline actually produces good supply-chain disruption recommendations, including the centerpiece question: **does adversarial deliberation add value, or is it expensive theater?**

This README explains what the eval does, what each metric means, and how to run the eval locally.

For the methodology design + decisions: see [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §8 and the [eval scope spec in `phase2/`](../../phase2/03_ChainPilot_eval_harness_scope_spec.md).

---

## What the eval measures

### Five metric families

1. **Action-class accuracy.** The system outputs `final_action ∈ {immediate_switch, partial_order, wait_and_monitor, emergency_spot_buy}`. The eval scores it both strict-match (action == primary expected) and acceptable-set match (action ∈ rubric-defined acceptable set). Reports a confusion matrix per scenario tier.

2. **Supplier-choice quality.** When the action requires picking an alternative, is the recommended supplier in the rubric-defined acceptable set? Includes the "correctly refrained" case for scenarios with no good alternatives.

3. **Severity-rating MAE.** Mean absolute error between system severity (CRITICAL=4 ... LOW=1) and rubric-expected severity. Per-tier breakdown.

4. **Deliberation A/B lift.** *The centerpiece.* Each scenario runs through both:
   - **Branch A (full pipeline):** Procurement || Risk → Advocate ⚔ Skeptic ⚔ Arbiter → finalize_analysis
   - **Branch B (stripped pipeline):** Procurement || Risk → direct finalize_analysis (no deliberation)
   
   Lift = (Branch A accuracy) − (Branch B accuracy). Computed per metric family.

5. **Trust-engine calibration.** Run scenarios in sequence; verify threshold drift correlates with rubric-judged outcome quality.

### Scenario design

40 hand-designed scenarios across 6 tiers:

| Tier | Count | Purpose |
|---|---|---|
| Clear Act | 7 | Stockout imminent + good alts → unambiguous `immediate_switch` |
| Clear Wait | 7 | Stable stock + bounded delay → unambiguous `wait_and_monitor` |
| Genuinely Ambiguous | 7 | Mid stock + mid delay + tradeoff alts; tests `swing_condition` quality |
| Edge cases | 6 | No alternatives; high uncertainty; customer-tier extremes |
| Adversarial | 7 | Cold-start suppliers; trust-threshold boundaries; JSON-output corruption patterns |
| Distribution shifts | 6 | Inputs outside mock_data.py's natural distribution |

### Rubric

[`RUBRIC.md`](./RUBRIC.md) defines what "correct" means for each scoring dimension. **The rubric is committed BEFORE any scenarios are labeled**, git history is part of the eval's bias-reduction evidence.

---

## How to run

### Prerequisites

```bash
# From the repo root:
pip install -e ".[dev]"
```

This installs `pytest`, `ruff`, and any eval-specific dev deps.

For the LLM-calling targets (`eval-small`, `eval`), set your Anthropic API key:

```bash
# .env at chainpilot/.env, or environment:
export ANTHROPIC_API_KEY=sk-ant-...
```

### Make targets

```bash
make eval-dry      # No LLM. Scores saved fixture outputs. Fast, free.
make eval-small    # 5 scenarios x 1 rep. ~$1, ~30 seconds.
make eval          # 40 scenarios x 2 branches x 3 reps. ~$14-43, ~10 minutes.
make eval-report   # Regenerate the markdown report from the most recent run.
```

**Always start with `make eval-dry` after any code change**, it catches scoring-logic bugs without spending a cent on LLM calls.

### CI

- **[`ci.yml`](../.github/workflows/ci.yml)**, runs ruff + smoke tests on every push/PR. **No LLM calls.** Free.
- **[`eval.yml`](../.github/workflows/eval.yml)**, manual-trigger only via the Actions tab. Requires `ANTHROPIC_API_KEY` as a GitHub secret. Runs `make eval-small` or `make eval` per the dropdown.

---

## What each output file means

```
eval/
├── RUBRIC.md                    ← scoring rules (commit BEFORE scenarios)
├── README.md                    ← this file
├── runners.py                   ← branch A / branch B implementations
├── scorers.py                   ← 5 metric families
├── instrumentation.py           ← CallTrace + cost/latency rollup
├── scenarios/
│   ├── scenario_001.json        ← 40 scenarios at completion
│   └── ...
└── reports/
    ├── run_YYYY-MM-DD.md        ← human-readable per-run report
    ├── latest_run.json          ← machine-readable raw results (gitignored)
    └── latest_dry.md            ← latest dry-run output
```

### `scenarios/scenario_NNN.json` schema

```json
{
  "id": "scenario_001",
  "name": "Clear-Act: critical stockout + long delay + good alternatives",
  "tier": "clear_act",
  "input": { ... },
  "expected": {
    "action_class": "immediate_switch",
    "acceptable_actions": ["immediate_switch", "emergency_spot_buy"],
    "acceptable_suppliers": ["FastBear Mfg", "GlobalParts"],
    "severity": "CRITICAL",
    "confidence_range": ["MEDIUM", "HIGH"],
    "swing_condition_keyword_hints": ["customer", "deadline", "PLATINUM"]
  },
  "rubric_notes": "(human-readable rationale)"
}
```

### `reports/run_YYYY-MM-DD.md` structure

```
# Eval Run YYYY-MM-DD

## Headline
- Action-class accuracy: full = X.X%, stripped = Y.Y%, lift = +Z.Z pp
- Supplier in-set: full = X.X%, stripped = Y.Y%, lift = +Z.Z pp
- Severity MAE: full = X.XX, stripped = Y.YY, lift improvement = -Z.ZZ
- Cost: $XX.XX (full) + $YY.YY (stripped) = $ZZ.ZZ total
- Wall-clock: XXm YYs

## Per-tier breakdown
[table]

## Per-metric confusion matrices
[tables]

## Selected example diffs
[5 scenarios where branches disagreed, with raw outputs]

## Failure modes verified
- Brittle JSON contract: hit on N/40 scenarios → fixed by retry-with-backoff
- Cold-start supplier: ...
[etc.]

## Rubric SHA: <git-sha-of-RUBRIC.md-at-run-time>
## Scenarios SHA: <hash-of-scenarios-dir>
```

---

## Honest disclosures (carried forward into ChainPilot README post-build)

1. **The rubric is mine.** A real eval would use 2-3 trained procurement-team raters with inter-rater reliability. This is the closest defensible methodology solo.
2. **Programmatic scoring, not LLM-as-judge.** Action-class is binary matching; supplier in-set is set membership; severity MAE is arithmetic. No LLM judges any output. The bias is the rubric (mine); the scoring is deterministic.
3. **`acceptable_actions` and `acceptable_suppliers` are SETS, not points.** Where rubric judgment is itself ambiguous, the eval tolerates either choice.
4. **A/B is fully stripped.** Branch B has no Advocate/Skeptic/Arbiter at all. Alternative designs (e.g., partial stripping, only the Arbiter) are documented as "could test later" but the fully-stripped baseline is the cleanest A vs Not-A.
5. **Cost is real.** Per full eval run: ~$14-43. The build itself will run the full eval 5-8 times. Total build LLM spend: $50-150.
6. **Long-horizon outcomes not scored.** The rubric scores decisions at decision time; not whether the recommendation would actually have produced a good outcome over weeks.

---

## Status flags

When the eval is complete and the ChainPilot README is updated with measured results, the project's Honest Disclosure section gets rewritten:

- **Before:** "Honest Disclosure #1, no eval harness yet."
- **After:** "Honest Disclosure #1, eval harness built and run. Deliberation A/B lift = X.X pp on Y scenarios. Interpretation: [Z]."

That rewrite is the centerpiece portfolio milestone.

---

*Eval harness · v0.1 (scaffold) · Phase 2 build · Day 1, 2026-06-01*
