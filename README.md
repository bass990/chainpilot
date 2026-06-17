# ChainPilot, Autonomous Supply Chain Disruption Response

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](./chainpilot/requirements.txt)
[![Model: claude-sonnet-4-6](https://img.shields.io/badge/Model-claude--sonnet--4--6-orange.svg)](./chainpilot/config.py)

A multi-agent supply-chain monitoring system (1 orchestrator + 6 specialists) that watches inventory, supplier, and price feeds; runs an **adversarial deliberation** (Advocate ⚔ Skeptic ⚔ Arbiter) before recommending a response; calibrates its own approval threshold against past outcomes; and explicitly flags the suppliers it doesn't yet have history on. Built as a working FastAPI + React app with SSE-streamed agent reasoning, not a slide deck.

**[Architecture deep-dive](./docs/ARCHITECTURE.md)** · **[Run guide](./RUN_GUIDE.md)**

> **Status:** local prototype, runs end-to-end against the Anthropic API. No live demo deployed (see [Honest disclosure](#honest-disclosure)).

---

## The problem

A factory has a single Procurement Manager. At 9:14 AM a supplier emails to say a Shanghai port closure is delaying their next shipment by two weeks. At 9:31 AM, inventory monitoring flags SKU-4821 will stock out in 8.5 hours. At 9:42 AM, prices on the spot market spike. Three signals, one human, finite hours in the day, and Apex Manufacturing (the top-tier customer for that SKU) is expecting delivery before lunch tomorrow.

The naive response, "build an agent that handles supplier emails", misses the harder question: **what should an autonomous system actually be trusted to do here, and how does that trust get earned or revoked?**

ChainPilot is an attempt at that question, not the supplier-emails one.

---

## What this proves (AI Engineer signals)

| Signal | Where it shows up |
|---|---|
| **Multi-agent orchestration with explicit specialization** | 7 distinct system prompts: 1 Orchestrator + 6 specialists (Procurement, Risk, Advocate, Skeptic, Arbiter, Communications), each with its own tool subset. See [`backend/agent.py`](./chainpilot/backend/agent.py). |
| **Adversarial deliberation, not single-critic** | Two agents argue opposite sides BEFORE an Arbiter synthesizes, the human sees the debate, not just the conclusion. The Arbiter must name the *swing condition* (what would flip the verdict). |
| **Calibrated autonomy** | The auto-execute dollar threshold is not a constant, `trust_engine.py` adjusts it asymmetrically based on validated outcomes (good +$8K, bad -$20K; that ratio is deliberate, not arbitrary, see [ARCHITECTURE §4](./docs/ARCHITECTURE.md#4-trust-engine--why-asymmetric-scoring)). |
| **Explicit uncertainty modeling** | A persistent supplier knowledge graph (`supplier_knowledge.json`) flags what the agent doesn't know about each recommended supplier, surfaced in the UI as an Uncertainty badge, not buried in a probability score. |
| **Production-shaped surface** | FastAPI backend with 15 documented endpoints, two SSE streams (live monitor + pipeline step-by-step), React dashboard, retry-with-backoff on Anthropic rate limits, demo-mode degradation for Slack/SMTP. |
| **Human-in-the-loop gate, designed first-class** | All communications drafted with `draft_only=True`. Nothing leaves the system until a human clicks **Execute Selected**. Approval gates are tied to trust-calibrated dollar thresholds, not "is the LLM confident?". |

---

## System at a glance

```
                          ┌─────────────────────────────┐
                          │  Monitor (async poll loop)  │
                          │  inventory · prices · ETAs  │
                          └─────────────┬───────────────┘
                                        │ (SSE)
                                        ▼
                          ┌─────────────────────────────┐
                          │       Orchestrator          │
                          │  decides which specialists  │
                          └──────┬──────────────┬───────┘
                                 │              │
                       (parallel)│              │(parallel)
                                 ▼              ▼
                  ┌──────────────────┐   ┌──────────────────┐
                  │  Procurement      │   │  Risk            │
                  │  Specialist       │   │  Specialist      │
                  │  · suppliers      │   │  · inventory     │
                  │  · prices         │   │  · customers     │
                  │  · alternatives   │   │  · exposure      │
                  └────────┬─────────┘   └────────┬─────────┘
                           │   ┌─────────────────┘
                           ▼   ▼
                  ┌─────────────────────────────────┐
                  │  Adversarial Deliberation       │
                  │  ┌──────────┐    ┌────────────┐ │
                  │  │ Advocate │ ⚔ │  Skeptic   │ │   (run in parallel)
                  │  └─────┬────┘    └─────┬──────┘ │
                  │        └─────┬────────┘        │
                  │              ▼                  │
                  │         ┌──────────┐            │
                  │         │ Arbiter  │ ← names    │
                  │         └──────────┘   swing    │
                  │                        condition│
                  └──────────────┬──────────────────┘
                                 ▼
                  ┌─────────────────────────────────┐
                  │     Communications Agent        │
                  │  RFQ emails · Slack alert ·     │
                  │  audit log  (draft_only=True)   │
                  └──────────────┬──────────────────┘
                                 ▼
                  ┌─────────────────────────────────┐
                  │       Approval Gate             │
                  │  Trust-engine threshold ($)     │
                  │  + Uncertainty badge            │
                  │  → Human clicks Execute         │
                  └──────────────┬──────────────────┘
                                 ▼
                  ┌─────────────────────────────────┐
                  │  Slack / SMTP (live or demo)    │
                  │  Outcome feedback ─→ trust_engine
                  │  Annotations ─→ knowledge graph │
                  └─────────────────────────────────┘
```

Full step-by-step pipeline (11 tool calls) and per-endpoint surface are in [RUN_GUIDE.md](./RUN_GUIDE.md). Design-decision rationale (why these splits, why this order, why these tradeoffs) is in [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md).

---

## Three non-obvious design choices

### 1. Adversarial deliberation, not single-critic, not voting

The pipeline runs an **Advocate** and a **Skeptic** agent **in parallel**, each given the same disruption analysis but instructed to argue opposite sides without hedging. An **Arbiter** then reads both and produces a final recommendation with three required fields: `final_action`, `confidence`, and `swing_condition`, the single thing that would flip the decision.

Why this over the obvious alternatives:
- **vs. single critic.** A lone critic produces "looks fine" or "looks bad", both anchored to the proposal. Adversarial framing forces the system to surface the actual tradeoffs.
- **vs. N-of-M voting.** Voting averages over disagreement. Deliberation makes the disagreement *legible* to the human approver, they see the strongest case for action and the strongest case for restraint, plus the arbiter's reasoning.
- **vs. CoT in a single agent.** A single agent's "let me consider the other side" is performative; an actual adversarial pair under independent prompts is not. The Skeptic system prompt explicitly says *"Do NOT hedge. You are a skeptic."*

The `swing_condition` field is the most underrated part of the design, it gives the human a concrete falsification target rather than a confidence score.

### 2. Asymmetric trust calibration

The auto-execute dollar threshold starts at **$50,000** and adjusts on each validated outcome:

| Outcome | Threshold change | Why |
|---|---|---|
| Good (recommendation worked) | **+$8,000** | Earn autonomy slowly |
| Bad (recommendation harmed the business) | **-$20,000** | Lose autonomy fast |
| Neutral / unknown | unchanged | Don't reward what wasn't measured |

The **2.5× asymmetry** is the design, not a knob. A bad supply-chain decision is far more costly than a good one is valuable (loss aversion + irreversibility of execution + reputational damage with customers). Symmetric scoring would treat 50 good calls + 1 catastrophic call as net positive; the asymmetric model treats it correctly as net negative. Bounded at **[$10K, $200K]** so the threshold can never drift to fully-autonomous or fully-manual.

Full derivation in [ARCHITECTURE §4](./docs/ARCHITECTURE.md#4-trust-engine--why-asymmetric-scoring).

### 3. Explicit uncertainty modeling, not implicit confidence

LLM confidence scores ("I am 80% confident") are notoriously uncalibrated. ChainPilot uses a different mechanism: a persistent **supplier knowledge graph** (`supplier_knowledge.json`) that tracks what the system actually knows about each supplier. When the agent recommends a supplier with no history, it does not produce a low confidence score, it produces an **Uncertainty badge** listing the specific gaps:

```
Uncertainty: HIGH
Gaps:
  · No quality history on file
  · Delivery performance unknown
  · No past RFQ responses to compare
```

Humans can annotate suppliers (`POST /knowledge/annotate`) to drop uncertainty from HIGH → MEDIUM → LOW. The agent's epistemic state is *separate* from its proposal's plausibility, surfaced to the approver as data, not folded into a single probability number.

---

## Honest disclosure

Things this project is NOT, that an interviewer should know up front:

1. **Eval harness, built, run twice, and surfaced three architectural problems.** The system was evaluated on **34 hand-designed scenarios × 3 reps × 2 branches = 204 pipeline runs** across 6 tiers (Clear Act, Clear Wait, Ambiguous, Edge, Adversarial, Distribution Shifts) with a published rubric (`eval/RUBRIC.md`) committed BEFORE scenario labeling. Two independent runs (2026-06-03 and 2026-06-16) replicate the finding in the same direction and magnitude band.

   **Headline result:** the stripped pipeline (Procurement || Risk → direct synthesis, no Advocate ⚔ Skeptic ⚔ Arbiter) **beats** the full pipeline by **16.7 percentage points** on strict action accuracy (52.0% vs 35.3%, $3.93 LLM cost, 816 calls, ~62 min wall clock — most recent run). The original 2026-06-03 run measured -13.7pp on the same metric (46.1% vs 32.4%, $3.95); the deeper run replicates within nondeterminism.

   **Three specific failure modes the eval surfaced:**

   - **Compromise bias on decisive cases.** The Arbiter, when given Advocate ⚔ Skeptic disagreement, systematically picks `partial_order` as the "middle-ground" action — even on Clear-Act scenarios where the right answer is decisive. Per-tier strict accuracy on the 2026-06-16 run: Clear-Act **0% full vs 40% stripped** (-40pp); Distribution-Shift **17% full vs 83% stripped** (-67pp); Edge 28% full vs 50% stripped (-22pp). The Arbiter prompt's "synthesize both arguments" instruction creates the bias; the larger the input ambiguity, the more dominant the compromise mode becomes.
   - **Prompt-injection amplification.** Scenario_027 (injection in customer name field, structured `tier=GOLD`): stripped pipeline correctly used `tier=GOLD` and picked `immediate_switch` × 3 reps with HIGH confidence; full pipeline produced `partial_order` × 3 reps and the Arbiter's swing_conditions explicitly referenced the injected customer name's prose ("Sentinel Defense Systems has undisclosed SLA clauses…"). Each LLM stage that consumes the previous stage's output gives the injection more surface area.
   - **NEW (2026-06-16): swing-condition verbosity collapse.** FULL pipeline produces swing_conditions outside the operationally-useful 5-30 word range on **0% of runs** (97.1% for STRIPPED). The deliberation chain produces paragraph-length, multi-clause "The opposing view would be correct if: (1)…, (2)…, (3)…" prose that an oncall procurement analyst cannot scan in seconds. This is a downstream cost of deliberation that the original eval did not isolate — adding more LLM reasoning steps does NOT compress to a tighter action trigger; it bloats it. The swing_condition is supposed to be the artifact the human reviewer reaches for first; verbosity destroys its purpose.

   **Where deliberation helps:** genuinely Ambiguous scenarios — the **only** tier where FULL wins strict accuracy: 53.3% full vs 20.0% stripped (+33.3pp on the 2026-06-16 run; +40pp on the original run). The Arbiter usefully integrates disagreement when multiple actions are defensible. This is the architecture earning its cost — but on exactly one tier out of six.

   **Architectural decisions driven by these findings:**
   1. **Make deliberation conditional**, fire Advocate ⚔ Skeptic ⚔ Arbiter only when Procurement specialist confidence is MEDIUM/LOW (skip when HIGH). Expected to convert the −16.7pp aggregate penalty into a targeted +33pp boost on the Ambiguous tier where it earns its cost, while preserving STRIPPED behavior on Clear-Act and Distribution-Shift where deliberation is actively harmful.
   2. **Sanitize customer/supplier name fields** before they propagate to Advocate/Skeptic prompts, or hash them to opaque IDs for LLM consumption and join back at the UI layer.
   3. **Constrain Arbiter's swing_condition output to a single-sentence template** (subject + condition + flip-direction, <= 30 words) and add a post-generation truncate-or-regenerate guardrail. The current Arbiter prompt instructs "explain what would flip the recommendation" with no length budget; the eval shows the model takes that as license to write 60-200 word multi-clause justifications instead of action triggers.

   **Caveats that remain:** the rubric and scenarios are mine; a real eval would use 2-3 procurement-team raters with inter-rater reliability. The eval mode skips the production tool-use loop and renders disruption context directly into specialist user messages — tests the deliberation A/B but not tool-use behavior. The rubric is published in `eval/RUBRIC.md` for critique. Full reports at `eval/reports/run_20260603_172713.md` (original) and `eval/reports/run_20260616_062016.md` (replication).
2. **Trust-engine constants are calibrated to my intuition, not to business outcomes.** The $8K / $20K asymmetry and the $10K / $200K bounds reflect "loss aversion seems right here" rather than a regression over real disruption-outcome data. The shape of the rule is the contribution; the constants need validation.
3. **LLM-as-judge bias is not addressed.** When the Arbiter agent synthesizes Advocate vs Skeptic, there's no guard against Arbiter consistently siding with one role's framing (e.g., the side that uses more numbers). A bias audit on Arbiter verdicts is on the roadmap.
4. **Cost and latency are not measured.** Each disruption analysis runs ~6 LLM calls; I have not instrumented token usage or wall-clock latency. In production this would be the first thing I'd add.
5. **Mock data, not real feeds.** Inventory, supplier ETAs, prices, customer orders all come from `mock_data.py`. The system is shaped *as if* connected to ERP / WMS / price-feed APIs, but the integration layer is not built.
6. **Demo mode is the default for communications.** Without `.env` credentials, Slack alerts and RFQ emails are drafted and shown in the UI but never transmitted, by design, not by accident. Live transmission requires explicit configuration.
7. **No live deploy.** The system needs a long-running backend + a React dev server + an Anthropic API key. Standing this up on HF Spaces or Render is possible but non-trivial (multi-process, env vars, no demo without an API key in the environment). Deploying is deferred until the eval harness is built, so what's demoed is what can be defended.

This list is the system's audit trail. Each item is itemized on the roadmap; none of them is hidden in marketing copy.

---

## What I'd want before deploying this for real

If a procurement team were going to use this on a Monday morning, the additions I'd want, in priority order:

1. **Gold-scenario eval set + quality scoring.** Curate 30–50 historical disruption scenarios with the known-correct response; score each pipeline run on (a) action correctness, (b) supplier-choice quality, (c) human-effort reduction vs no system. Re-run on every prompt change.
2. **A/B test the deliberation step itself.** Half the runs go through Advocate ⚔ Skeptic ⚔ Arbiter; half go through a single Procurement Specialist → final recommendation. Compare on the eval set. If deliberation doesn't change recommendations *or* doesn't change human decisions, it's expensive theater.
3. **Cost ceiling per disruption.** Hard cap on (LLM calls × token spend) per pipeline run. Today the orchestrator can in principle loop on tools; in production this needs an explicit budget.
4. **Prompt-injection hardening.** Supplier emails and customer orders are untrusted input that flows into agent context. Mock data is benign; real data isn't. Inputs need sanitization and the system prompts need explicit instructions to ignore embedded directives.
5. **Audit log immutability.** `log_disruption_event()` writes to a JSON file. In a real procurement system this needs append-only storage with operator identity attached.
6. **Trust ledger calibration against real outcomes.** Re-derive the $8K / $20K asymmetry from a regression on actual disruption-resolution outcomes (procurement-team feedback × time × dollar impact). Replace the intuition baseline with data.
7. **Failover for the LLM provider.** Single-vendor dependency on Anthropic. A production system needs a degraded mode (cached recommendations or rule-based fallback) when the API is unavailable.

---

## Failure modes (where I expect this to break)

- **Ambiguous Advocate vs Skeptic.** When the analysis is genuinely a coin-flip, both agents argue weakly. The Arbiter's `confidence: LOW` field surfaces this, but the human still has to interpret it correctly. The temptation under time pressure is to act on weak Arbiter recommendations because the UI showed *something*.
- **Trust drift on tail outcomes.** A single catastrophic bad outcome drops the threshold by $20K. If the very next 3 disruptions happen to be small and successful, the threshold rises $24K and the system may auto-execute the next medium-stakes call before the lesson sticks.
- **Supplier cold-start lock-in.** Every new supplier starts at uncertainty HIGH, which the approval flow flags. If procurement is busy and approves the recommendation anyway without annotating the supplier, that supplier stays at HIGH forever and every future recommendation drags the same badge. The annotation flow is a discipline problem, not a code problem.
- **Brittle JSON contract.** Specialist agents are instructed to return JSON only. Any deviation (a markdown code fence, a "Here's the JSON:" preface) breaks the orchestrator. Robust parsing is mitigation, not elimination.
- **Mock data masking.** The pipeline assumes mock data and real data have the same schema. When wiring up real ERP feeds, schema drift will be the first failure surface.

---

## Quick start

The full operational guide is in [RUN_GUIDE.md](./RUN_GUIDE.md). Compact version:

```bash
# 1. API key
cd chainpilot
cp .env.example .env   # or create .env
# Add: ANTHROPIC_API_KEY=sk-ant-...

# 2. Backend (Terminal 1)
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --port 8002

# 3. Frontend (Terminal 2)
cd frontend
npm install && npm run dev
# → http://localhost:3002
```

Demo flow, API endpoint catalog, Slack/SMTP wiring, and Apple Silicon troubleshooting are all in the [run guide](./RUN_GUIDE.md).

---

## Repo structure

```
chainpilot_v7_realtime 3/
├── README.md                       ← you are here (portfolio front door)
├── RUN_GUIDE.md                    ← operational guide (setup, demo, endpoints)
├── docs/
│   └── ARCHITECTURE.md             ← design-decision deep dive
├── LICENSE                         ← MIT
└── chainpilot/                     ← the project root for execution
    ├── config.py                   ← model + thresholds + SMTP/Slack
    ├── mock_data.py                ← simulated ERP / WMS / price feeds
    ├── requirements.txt
    ├── .env                        ← API key + optional Slack/SMTP (gitignored)
    ├── trust_ledger.json           ← auto-created, threshold + history
    ├── supplier_knowledge.json     ← auto-created, supplier knowledge graph
    ├── backend/
    │   ├── tools.py                ← 10 tool functions + schemas
    │   ├── agent.py                ← 6 system prompts + orchestrator
    │   ├── trust_engine.py         ← asymmetric autonomy threshold
    │   ├── uncertainty_tracker.py  ← supplier knowledge graph
    │   ├── monitor.py              ← async poll loop
    │   └── main.py                 ← FastAPI: 15 endpoints + 2 SSE streams
    ├── frontend/
    │   └── src/
    │       ├── App.jsx             ← Dashboard + TrustMeter + DeliberationPanel + UncertaintyBadge
    │       └── App.css
    └── mac/                        ← Finder-friendly setup/start/stop scripts
```

---

## License

[MIT](./LICENSE). No real supply-chain data, supplier names, or customer relationships are present in this project, everything in `mock_data.py` is fabricated for demonstration.

---

## Author

Mamadou Bassirou Diallo · MS Business Analytics & AI, UT Dallas · [LinkedIn](https://www.linkedin.com/in/mamadou9905) · [GitHub](https://github.com/bass990)

