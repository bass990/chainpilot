# ChainPilot, Run Guide

Operational guide: how to set up, run, and demo the system. For project framing and design rationale, see the [README](./README.md) and the [architecture deep-dive](./docs/ARCHITECTURE.md).

---

## What's Running

| Component | What it does | Port |
|---|---|---|
| FastAPI backend | Monitor loop + multi-agent pipeline + trust engine + approval gate | 8002 |
| React frontend | Live monitoring dashboard with deliberation viewer | 3002 |

---

## Setup

**Quick start (Mac):** see [`chainpilot/mac/README.md`](./chainpilot/mac/README.md) for the Finder-friendly walkthrough.

1. Edit `chainpilot/.env`, add your Anthropic API key (Slack + SMTP optional)
2. Double-click `chainpilot/mac/setup.command`, checks prerequisites, creates venv, installs dependencies
3. Double-click `chainpilot/mac/start.command`, launches backend + frontend; dashboard opens at `http://localhost:3002`
4. Double-click `chainpilot/mac/stop.command` if ports stay busy after a crash

**Manual (any platform):**

```bash
# From repo root
cd chainpilot

# 1. Add your API key
# Edit .env: ANTHROPIC_API_KEY=sk-ant-...

# 2. Backend (Terminal 1)
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --port 8002

# 3. Frontend (Terminal 2)
cd frontend
npm install && npm run dev
# Opens at http://localhost:3002
```

> **Apple Silicon (M1/M2/M3) note:** If `npm run dev` fails with
> `Cannot find module @rollup/rollup-darwin-arm64`, delete `node_modules`
> and `package-lock.json` and reinstall:
> ```bash
> cd frontend && rm -rf node_modules package-lock.json && npm install
> ```
> This is a known npm bug with optional native dependencies on ARM Macs.

---

## Demo Flow

1. Open `http://localhost:3002`, dashboard shows **all-green healthy state** with Trust Meter at baseline $50K.
2. Say: *"Supply chain is nominal, all systems green. Notice the trust meter: the agent starts at a $50K auto-execute threshold and earns or loses autonomy based on past performance."*
3. Click **⚡ Inject Supply Chain Shock**, inventory drops to crisis levels, pipeline fires immediately.
4. Say: *"SKU-4821 at 12%, supplier delayed 14 days, Apex Manufacturing due tomorrow."*
5. Watch the pipeline: 11 steps including **Advocate ⚔ Skeptic debate**, the adversarial deliberation step.
6. **Recommendation card** appears with the Arbiter's verdict, confidence, and the one condition that would flip the decision.
7. **Approval gate**, open the Deliberation panel to show the full debate; notice the Uncertainty badge flagging knowledge gaps on the recommended supplier.
8. **Execute Selected**, RFQs and Slack post (demo mode or live depending on `.env`).
9. **Rate the recommendation**, click ✓ Good call / ✗ Bad call; watch the Trust Meter update live.
10. Click **Reset Demo** to return to baseline.

---

## The Pipeline (11 steps)

```
check_inventory_levels()       → SKU-4821 at 12%, 8.5h to stockout
check_supplier_status()        → PrecisionParts Co. delayed 14d (Shanghai port)
check_price_feeds()            → SKU-4821 +10.7% vs baseline
search_alternative_suppliers() → FastBear (3d), GlobalParts (6d), QuickMfg (2d)
calculate_cost_impact()        → 30-day exposure, switching premium vs baseline
get_affected_customer_orders() → Apex Manufacturing (PLATINUM) due in <9 hours
adversarial_deliberation()     → Advocate argues act now; Skeptic urges caution; Arbiter synthesizes
draft_rfq_email()              → Professional RFQ to top alternative suppliers
draft_slack_alert()            → Procurement team notification
log_disruption_event()         → Audit trail entry
finalize_analysis()            → Structured recommendation: severity, confidence, action
```

---

## Agent Architecture

```
Orchestrator
 ├── parallel → Procurement Specialist  (inventory, suppliers, prices, alternatives, cost)
 ├── parallel → Risk Specialist         (customer orders, exposure, affected accounts)
 ↓ (both complete)
 ├── Adversarial Deliberation
 │    ├── parallel → Advocate Agent     (argues for immediate action)
 │    ├── parallel → Skeptic Agent      (argues for caution)
 │    └── Arbiter Agent                 (synthesizes → verdict + swing_condition)
 ↓
 ├── Communications Agent               (RFQ emails, Slack alert, audit log)
 ↓
 └── finalize_analysis()                → RecommendationCard + DeliberationPanel
```

For *why* this shape (specialist-first then deliberation, parallel within stages but serial between, what was rejected): see [ARCHITECTURE §2](./docs/ARCHITECTURE.md#2-why-specialist-first-deliberation-second-not-parallel-everything).

---

## API Endpoints

| Endpoint | What it does |
|---|---|
| `GET /dashboard` | Full dashboard state (inventory, suppliers, prices, events, pipeline) |
| `GET /realtime/snapshot` | Lightweight live snapshot (inventory + prices only, polls every 3s) |
| `GET /monitor/stream` | SSE stream, disruptions detected by the autonomous monitor |
| `GET /pipeline/stream/{sku}` | SSE stream, step-by-step pipeline progress for a given SKU |
| `POST /analyze` | Manually trigger the analysis pipeline |
| `POST /approve` | Execute approved actions: `{approvals: {slack, rfq_emails: [...]}}` |
| `GET /events` | Event log |
| `POST /demo/inject-shock` | Apply crisis state to mock data (low stock, delays, price spikes) |
| `POST /demo/pre-shock` | Reset mock data to healthy baseline |
| `GET /trust` | Current trust stats and threshold |
| `POST /trust/outcome` | Record recommendation outcome: `{event_id, sku, outcome, notes}` |
| `POST /trust/reset` | Reset trust to baseline |
| `GET /knowledge` | Supplier knowledge base |
| `POST /knowledge/annotate` | Annotate supplier: `{supplier_name, quality_note, uncertainty}` |
| `POST /knowledge/reset` | Reset knowledge base |

---

## Slack and Email

Both work in **demo mode** (content shown in UI without transmission) when credentials are not configured. To enable live sending:

```env
# Real Slack webhook
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/REAL/WEBHOOK

# Mailtrap sandbox (recommended for testing)
SMTP_SERVER=sandbox.smtp.mailtrap.io
SMTP_PORT=2525
SMTP_USER=your_mailtrap_user
SMTP_PASS=your_mailtrap_pass
SMTP_FROM=chainpilot@demo.com
```

When credentials are configured, the system sends real emails via SMTP and real Slack messages via the webhook. On failure, the UI shows the error and preserves the message content.

---

## Project Structure (inside `chainpilot/`)

```
chainpilot/
├── config.py                  ← Model, thresholds, SMTP/Slack config
├── mock_data.py               ← Inventory, suppliers, customers, prices + state patches
├── requirements.txt
├── .env                       ← Your API key + optional Slack/SMTP
├── trust_ledger.json          ← Auto-created: dynamic approval threshold history
├── supplier_knowledge.json    ← Auto-created: agent's knowledge graph of suppliers
├── backend/
│   ├── tools.py               ← 10 tool functions + schemas
│   ├── agent.py               ← Multi-agent pipeline: orchestrator + 5 specialist agents
│   ├── trust_engine.py        ← Dynamic approval threshold
│   ├── uncertainty_tracker.py ← Supplier knowledge graph
│   ├── monitor.py             ← Async polling loop (the always-on watcher)
│   └── main.py                ← FastAPI: all endpoints including /trust and /knowledge
└── frontend/
    └── src/
        ├── App.jsx            ← Dashboard + TrustMeter + DeliberationPanel + UncertaintyBadge + OutcomeFeedback
        └── App.css            ← Industrial dark aesthetic
```

---

