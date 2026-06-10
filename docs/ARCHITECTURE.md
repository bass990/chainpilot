# ChainPilot Architecture, Design Decisions

This document is the why-not-Y companion to the [README](../README.md). Each section names a non-obvious choice in the system and walks through the alternatives that were considered and rejected.

---

## 1. Why multi-agent over a single agent with all tools

**The obvious alternative.** One Claude agent, all 10 tools in its toolbox, a single system prompt: *"Respond to supply-chain disruptions. Use any tool you need."*

**Why that's worse for this problem.** Three reasons:

- **Tool-routing degrades with toolbox size.** With 10 tools available, a single agent has to pick which one to call at each step. Empirically (in the literature and in this project's earlier prototypes), tool-selection error rate rises non-linearly with toolbox size. Splitting tools across two specialists with ~4 tools each keeps each agent's routing problem small.
- **System-prompt mode-collapse.** A single prompt that says *"think about procurement and risk and communications and deliberation"* collapses to one of those modes (usually the first or the highest-stakes) and treats the others perfunctorily. Separate prompts force separate reasoning.
- **Parallelism.** Procurement and Risk specialists have no data dependency on each other; they can (and do) run concurrently via `concurrent.futures`. A single agent serializes the same work.

**What I gave up.** Latency from inter-agent message-passing, additional token spend (each specialist re-reads the disruption context), and a small loss of "global awareness", Procurement doesn't see Risk's intermediate reasoning. The Orchestrator's final synthesis step gets that back.

**Where this breaks.** If specialists need to negotiate (e.g., Procurement wants more inventory data that Risk has and didn't share), the current architecture has to round-trip through the Orchestrator. For a more complex domain, an agent-to-agent message bus would replace orchestrator-mediated communication.

---

## 2. Why specialist-first, deliberation-second (not parallel everything)

**The obvious alternative.** Run all six agents, Procurement, Risk, Advocate, Skeptic, Arbiter, Communications, in parallel, then synthesize.

**Why that's worse.** Advocate and Skeptic *cannot* run before Procurement and Risk. Their entire job is to argue about the *analysis the specialists produced*. Running them in parallel means they argue about nothing, they fall back on priors and produce generic argumentation.

The pipeline is therefore staged:

```
Stage 1 (parallel):   Procurement || Risk      , produce analysis
Stage 2 (parallel):   Advocate    || Skeptic   , argue over analysis
Stage 3 (serial):     Arbiter                   , synthesize debate
Stage 4 (serial):     Communications            , draft outputs
```

Within stages, parallelism is real. Between stages, serial ordering is required by the data dependency.

---

## 3. Why adversarial deliberation (vs. single critic, vs. ensemble voting)

The deliberation step is the project's most opinionated design choice. The alternatives that were considered:

### vs. single critic

A common pattern: Procurement makes a proposal, a Critic agent reviews it, the Critic returns "ship it" or "revise."

The problem: the Critic is *anchored to the proposal*. It evaluates against the proposal's framing, does this look right *given* what was proposed. It does not generate the strongest case for *the opposite action*. Adversarial framing forces the Skeptic to construct the best argument against the proposal even when the proposal looks fine, which is the only way to surface the genuine tradeoff space.

### vs. ensemble voting (N-of-M)

Run five agents, take majority vote. The problem: voting **averages over disagreement** and loses the structure of *why* agents disagreed. When the vote is 3-2 the human approver sees a coin-flip; when adversarial deliberation produces `confidence: MEDIUM`, the human sees both arguments laid out and a `swing_condition` field that says exactly what would flip the call.

### vs. chain-of-thought in a single agent

"Let me consider the other side" inside a single agent's reasoning is **performative**. The same agent that proposed the action is now arguing against it, and the human-eval research consistently shows this produces weak counter-arguments. Two agents under independent system prompts, with explicit *"do NOT hedge"* instructions, produce qualitatively different counter-cases. (See the [`_SYSTEM_SKEPTIC` prompt](../chainpilot/backend/agent.py), the prompt enforces the role, not the model.)

### The Arbiter's `swing_condition` field

The Arbiter is required to return a JSON field called `swing_condition`: the single fact that, if true, would flip the recommended action. This is the *most underrated piece of the design*. It gives the human approver a **falsification target** rather than a confidence score. "I would have approved if you'd said X" or "X turned out to be true after all" is now a structured event the trust engine can learn from, a falsifiable swing condition is more useful than a number between 0 and 1.

---

## 4. Trust engine, why asymmetric scoring

The autonomy threshold starts at $50,000 and adjusts on each validated outcome:

| Event | Delta | Floor | Ceiling |
|---|---|---|---|
| Good outcome | **+$8,000** |  | $200,000 |
| Bad outcome | **-$20,000** | $10,000 |  |
| Neutral | 0 |  |  |

The 2.5× asymmetry between bad-outcome penalty and good-outcome reward is the design.

### Why asymmetric

Three independent reasons converge on penalty > reward:

1. **Loss aversion is rational here, not a bias.** The expected dollar loss from a bad supply-chain decision (stockout × customer-tier penalty × reputational damage with that customer) is structurally larger than the expected dollar value of a good decision (cost savings vs. doing nothing). Symmetric scoring would mis-weight these.
2. **Irreversibility.** A bad recommendation that gets auto-executed sends an RFQ to the wrong supplier and triggers commitments. The good-outcome counterpart, a recommendation that gets approved and saves money, doesn't generate the same magnitude of downstream commitment. Asymmetry matches asymmetry.
3. **Trust takes longer to build than to lose, in human organizations.** The threshold dynamics should mirror the real procurement team's psychology, if the system makes one bad call, leadership will pull back authority sharply; the system has to earn it back slowly. Mirroring this dynamic in the engine makes its behavior predictable to the procurement team.

### Why bounded

The threshold is hard-bounded at **[$10K, $200K]**. The bounds are non-negotiable design constraints:

- Below $10K → the system might as well be fully autonomous; loses the discipline of always checking the highest-judgment small calls.
- Above $200K → the system gets to commit hundreds of thousands of dollars without a human ever seeing the recommendation; this is an organizational risk no calibration model should be able to relax.

The trust engine *can* recommend approaching the bounds; it *cannot* exceed them.

### What's wrong with this (honest)

The $8K and $20K constants are calibrated to **my intuition about the procurement domain**, not regressed from real outcome data. The *shape of the rule* (asymmetric, bounded, learning from validated outcomes) is the contribution that survives review; the *specific constants* are the part a procurement team would need to refit against their own data before deploying. The trust ledger logs every delta with timestamp and event ID so this regression is straightforward to run later.

---

## 5. Uncertainty as data, not as confidence

LLM confidence scores are **uncalibrated**, a well-known problem in the literature and visible in this project's earlier prototypes. "I am 80% confident" from an LLM does not mean the LLM is correct 80% of the time.

ChainPilot instead tracks supplier knowledge as **structured data**, not as a confidence number:

```json
{
  "supplier": "FastBear Manufacturing",
  "uncertainty": "HIGH",
  "interactions": 0,
  "quality_notes": [],
  "known_gaps": [
    "No quality history on file",
    "Delivery performance unknown",
    "No past RFQ responses to compare"
  ]
}
```

The recommendation's uncertainty is then derived from what's known and not known about the *specific entity* being recommended, separate from how plausible the recommendation looks. A high-quality argument for an unknown supplier is exactly the situation where the human approver should be most engaged, and the uncertainty data surfaces it explicitly.

### Why a separate knowledge graph

The natural alternative is to fold this into the agent's reasoning ("if you don't know about the supplier, lower your confidence"). The problem: every agent invocation would re-derive uncertainty from scratch, inconsistently. A persistent JSON file (`supplier_knowledge.json`) gives the system a stable epistemic baseline that humans can annotate and that the agent reads, doesn't generate.

### What this enables

Two emergent behaviors come from this design:

1. **Humans can teach the system who's reliable.** `POST /knowledge/annotate` lets a procurement manager say "FastBear is HIGH uncertainty for now" or "Established Co is LOW uncertainty after 3 good shipments." The agent's future recommendations reflect this.
2. **Uncertainty decays as evidence accumulates.** First RFQ to a new supplier drops one of three uncertainty gaps. Three documented good shipments + human annotation drops the supplier to LOW. The system doesn't pretend to know what it learned without evidence.

---

## 6. Why server-sent events for streaming (not WebSockets, not polling)

The dashboard needs three live surfaces:

| Surface | Cadence | Direction |
|---|---|---|
| Realtime snapshot (inventory, prices) | every 3s | server → client |
| Disruption monitor | event-driven | server → client |
| Pipeline progress per SKU | event-driven, ~6 events per run | server → client |

All three are **one-way, server-pushed, browser-displayed**. WebSockets buy bidirectional communication that nothing in this UI needs. Polling at 3-second cadence works for the snapshot but doesn't fit the event-driven streams. **SSE** is the right shape: HTTP, browser-native via `EventSource`, no extra client library, robust to network blips because the browser auto-reconnects.

The cost is that SSE doesn't support binary frames (irrelevant here) and lacks subprotocol negotiation (irrelevant here). The benefit is operational simplicity, `/monitor/stream` and `/pipeline/stream/{sku}` are just FastAPI endpoints that yield events. No connection bookkeeping, no broadcast fan-out, no protocol upgrade dance.

---

## 7. HITL gate, what gets approved and how

Every recommendation passes through a structured Approval Gate before any action is taken externally. The gate has three pieces of state visible to the approver:

1. **The arbitrated recommendation**, the Arbiter's verdict, confidence, and swing condition.
2. **The trust-calibrated threshold**, the current dollar autonomy threshold from the trust engine. Recommendations below it are flagged auto-executable; recommendations above it require explicit approval. The approver sees the threshold, the recommendation's exposure, and the delta.
3. **The uncertainty badge**, the recommended supplier's knowledge graph entry, with specific gaps if any.

All draft communications (RFQ emails, Slack alerts) are produced with `draft_only=True`. The system can show the human what it *would* send; it cannot send anything until **Execute Selected** is clicked. After execution, the **Outcome Feedback** widget asks the approver to rate the recommendation good / bad / neutral, which feeds back into the trust engine.

### What's deliberately not on the approval surface

- **No raw LLM token output.** The approver sees structured fields, not prose. The agent reasoning is in the Deliberation panel as a separate disclosure, not as the primary view.
- **No confidence number alone.** A confidence pill exists, but never as the primary signal, paired with the swing condition and uncertainty gaps every time.
- **No "approve all suggested actions" button.** RFQ emails and Slack alerts are approved individually. Bundling them would create a path where Slack went out but the RFQ didn't, with no clean rollback.

---

## 8. What's missing, the Phase 2 roadmap

The honest gaps in this design, in priority order. Each is a tractable Phase 2 build, not an architectural rewrite.

### Eval harness (single highest-value upgrade)

Gold-scenario dataset: 30–50 historical or simulated disruption scenarios with the known-correct response. Metrics:

- **Recommendation correctness**, did the system pick the right action class (immediate_switch, partial_order, wait_and_monitor, emergency_spot_buy)?
- **Supplier-choice quality**, did the system pick a defensible alternative?
- **Trust-score calibration**, over a sequence of scored outcomes, does the threshold land where the optimal threshold should be?
- **Deliberation A/B**, does running Advocate ⚔ Skeptic change the recommendation versus a Procurement-only baseline? If not, the deliberation is theater.

### LLM-as-judge bias audit

When the Arbiter synthesizes Advocate vs Skeptic, does it systematically favor one role? A controlled experiment: swap the role labels (Advocate's prompt now arrives labeled "Skeptic" and vice-versa) and re-run on the eval set. If Arbiter verdicts shift with the label, the Arbiter is biased by the label, not by the argument quality. The fix is either a blinded evaluation step or a different Arbiter prompt.

### Cost and latency instrumentation

Tokens per agent role × disruption × outcome. Wall-clock latency per pipeline stage. Today: not measured. In production: the first dashboard a procurement IT team asks for.

### Trust ledger calibration

Re-derive the $8K / $20K asymmetry as a regression on real disruption outcome data once the eval harness exists. Replace the intuition baseline with data.

### Failure-mode taxonomy

Catalog the known failure surfaces (listed in the [README](../README.md#failure-modes)) and add explicit guard rails, JSON-parsing recovery, brittle-contract tests, cold-start lock-in detection, ambiguous-deliberation handling.

### Production input sanitization

Prompt-injection hardening on any user-supplied or third-party input that flows into agent context (supplier emails, customer order notes). Not relevant for the current mock-data demo; mandatory before any live integration.

---

## Reading order for an interviewer

If you're reading this to evaluate the project as a portfolio piece:

1. **The README.** Read the *Three non-obvious design choices* and *Honest disclosure* sections, that's the core of the senior-judgment story.
2. **This document.** Sections 3 (adversarial deliberation) and 4 (asymmetric scoring) are the highest-content sections. Section 8 is the audit trail.
3. **The code.** Start with [`backend/agent.py`](../chainpilot/backend/agent.py) for the system prompts, then [`backend/trust_engine.py`](../chainpilot/backend/trust_engine.py) for the asymmetric-scoring implementation, then [`backend/uncertainty_tracker.py`](../chainpilot/backend/uncertainty_tracker.py) for the knowledge-graph design.

This document is the substance.
