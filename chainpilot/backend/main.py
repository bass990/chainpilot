import sys, os, json, asyncio, threading, urllib.request, urllib.error, smtplib
from datetime import datetime
from email.mime.text import MIMEText
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.agent import detect_disruptions, run_disruption_pipeline
from backend.monitor import monitor_loop, get_event_log, clear_active_events, clear_event_log
from backend.trust_engine import get_trust_stats, record_outcome, reset_trust
from backend.uncertainty_tracker import (
    get_all_knowledge, annotate_supplier, reset_knowledge
)
import mock_data
from mock_data import INVENTORY, SUPPLIERS, PRICE_FEEDS, CUSTOMER_ORDERS
from config import SLACK_WEBHOOK_URL, SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM

_monitor_running = False
_sse_monitor_clients: list = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _monitor_running
    loop = asyncio.get_running_loop()

    def _on_monitor_event(event, result):
        payload = {"event": event, "result": result}
        for q in list(_sse_monitor_clients):
            loop.call_soon_threadsafe(q.put_nowait, payload)

    task = asyncio.create_task(monitor_loop(on_event=_on_monitor_event))
    _monitor_running = True
    try:
        yield
    finally:
        task.cancel()
        _monitor_running = False


app = FastAPI(title="ChainPilot API", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3002", "http://127.0.0.1:3002"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


# ── Email sending (fixed) ─────────────────────────────────────────────────────

def _send_smtp_email(to: str, subject: str, body: str) -> dict:
    """Send an email via SMTP. Falls back to demo mode if SMTP not configured."""
    if not SMTP_SERVER:
        # Demo mode — no real SMTP configured; simulate success and show the content
        return {
            "sent": True,
            "demo": True,
            "to": to,
            "subject": subject,
            "body": body,
            "note": "Demo mode: SMTP not configured. Email shown above but not transmitted."
        }
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to
        with smtplib.SMTP(SMTP_SERVER, int(SMTP_PORT)) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        return {"sent": True, "to": to, "subject": subject, "body": body}
    except Exception as e:
        return {
            "sent": False,
            "error": str(e),
            "to": to,
            "subject": subject,
            "body": body,
            "note": "SMTP error — check .env credentials. Email content preserved above."
        }


# ── Slack sending (fixed) ─────────────────────────────────────────────────────

def _send_slack(message: str) -> dict:
    """Post a message to Slack. Falls back to demo mode if webhook not configured."""
    if not SLACK_WEBHOOK_URL or SLACK_WEBHOOK_URL.startswith("optional"):
        return {
            "sent": True,
            "demo": True,
            "channel": "#procurement-alerts",
            "message": message,
            "note": "Demo mode: Slack webhook not configured. Message shown above but not transmitted."
        }
    try:
        payload = json.dumps({
            "text": message,
            "username": "ChainPilot",
            "icon_emoji": ":robot_face:"
        }).encode("utf-8")
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return {
                "sent": True,
                "channel": "#procurement-alerts",
                "message": message,
                "status_code": resp.status
            }
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {
            "sent": False,
            "error": f"HTTP {e.code}: {body}",
            "message": message,
            "note": "Slack webhook returned an error. Check the webhook URL in .env."
        }
    except Exception as e:
        return {
            "sent": False,
            "error": str(e),
            "message": message,
            "note": "Slack send failed. Check SLACK_WEBHOOK_URL in .env."
        }


@app.get("/realtime/snapshot")
def realtime_snapshot():
    """
    Lightweight endpoint — returns only the fields that change each tick.
    The frontend can poll this at high frequency (e.g. every 3s)
    for a live-data feel without fetching the full dashboard payload.
    """
    return {
        "sim_phase": mock_data._sim_phase,
        "sim_tick": mock_data._sim_tick,
        "inventory": [
            {
                "sku": k,
                "stock_units": round(v["stock_units"], 1),
                "stock_pct": round(v["stock_pct"] * 100, 1),
                "hours_to_stockout": v["hours_to_stockout"],
            }
            for k, v in INVENTORY.items()
        ],
        "price_feeds": [
            {
                "sku": k,
                "current_price": v["current_price"],
                "change_pct": round(v["change_pct"] * 100, 1),
            }
            for k, v in PRICE_FEEDS.items()
        ],
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "ChainPilot", "version": "2.0.0"}


@app.get("/dashboard")
def dashboard():
    alerts = []
    for sku, data in INVENTORY.items():
        if data["stock_pct"] < 0.25:
            alerts.append({"sku": sku, "name": data["name"],
                           "stock_pct": round(data["stock_pct"] * 100, 1),
                           "hours_to_stockout": data["hours_to_stockout"],
                           "severity": "CRITICAL" if data["stock_pct"] < 0.20 else "WARNING"})

    supplier_alerts = [
        {"name": s["name"], "status": s["status"], "delay_days": s.get("delay_days", 0),
         "reason": s.get("delay_reason", "")}
        for s in SUPPLIERS.values() if s["status"] != "ACTIVE"
    ]

    at_risk_skus = {a["sku"] for a in alerts}
    at_risk_orders = sorted(
        [o for o in CUSTOMER_ORDERS if o["sku"] in at_risk_skus],
        key=lambda o: o["delivery_date"]
    )
    next_delivery = None
    if at_risk_orders:
        o = at_risk_orders[0]
        deadline = datetime.strptime(o["delivery_date"], "%Y-%m-%d").replace(hour=18)
        hours_remaining = max(0, (deadline - datetime.now()).total_seconds() / 3600)
        next_delivery = {
            "customer": o["customer"], "order_id": o["order_id"],
            "delivery_date": o["delivery_date"],
            "hours_remaining": round(hours_remaining, 2),
            "sku": o["sku"], "customer_tier": o["customer_tier"],
            "penalty_per_day": o["contract_penalty_per_day"]
        }

    return {
        "inventory": [
            {"sku": k, "name": v["name"], "stock_pct": round(v["stock_pct"]*100,1),
             "hours_to_stockout": v["hours_to_stockout"]}
            for k, v in INVENTORY.items()
        ],
        "supplier_alerts": supplier_alerts,
        "price_feeds": [
            {"sku": k, "change_pct": round(v["change_pct"]*100,1), "current": v["current_price"]}
            for k, v in PRICE_FEEDS.items()
        ],
        "low_stock_alerts": alerts,
        "monitor_active": _monitor_running,
        "next_delivery": next_delivery,
        "trust_stats": get_trust_stats(),          # FEATURE 2: expose trust stats on dashboard
        "sim_phase": mock_data._sim_phase,         # real-time simulation phase
        "sim_tick": mock_data._sim_tick,           # total drift ticks since last reset
    }


@app.get("/pipeline/stream/{sku}")
async def pipeline_stream(sku: str):
    all_events = detect_disruptions()
    event = next((e for e in all_events if e.get("sku") == sku), None)
    if not event:
        event = {
            "type": "low_stock", "sku": sku,
            "name": INVENTORY.get(sku, {}).get("name", sku),
            "detected_value": f"{round(INVENTORY.get(sku, {}).get('stock_pct', 0)*100, 1)}% stock remaining",
            "hours_to_stockout": INVENTORY.get(sku, {}).get("hours_to_stockout", 0)
        }

    async def event_stream():
        def sse(evt, data):
            return f"event: {evt}\ndata: {json.dumps(data)}\n\n"

        yield sse("status", {"step": 0, "total": 10, "message": "Starting autonomous pipeline...", "event": event})
        await asyncio.sleep(0.1)

        progress_holder = {"step": 0, "message": "", "tool": None}
        tool_details_holder = {}
        _lock = threading.Lock()

        def on_progress(step, message, tool=None, detail=None):
            with _lock:
                progress_holder["step"] = step
                progress_holder["message"] = message
                progress_holder["tool"] = tool
                if tool and detail:
                    tool_details_holder[tool] = detail

        import concurrent.futures
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = loop.run_in_executor(pool, run_disruption_pipeline, event, on_progress)

            last_step = -1
            last_emitted_details: set = set()
            while not future.done():
                with _lock:
                    snapshot = dict(progress_holder)
                    details_snap = dict(tool_details_holder)
                current_step = snapshot.get("step", 0)
                if current_step != last_step:
                    last_step = current_step
                    yield sse("status", {
                        "step": current_step, "total": 10,
                        "message": snapshot.get("message", ""),
                        "tool": snapshot.get("tool")
                    })
                for tool_name, detail in details_snap.items():
                    if tool_name not in last_emitted_details:
                        last_emitted_details.add(tool_name)
                        yield sse("tool_detail", {"tool": tool_name, "detail": detail})
                await asyncio.sleep(0.4)

            result = await future
            with _lock:
                details_snap = dict(tool_details_holder)
            for tool_name, detail in details_snap.items():
                if tool_name not in last_emitted_details:
                    last_emitted_details.add(tool_name)
                    yield sse("tool_detail", {"tool": tool_name, "detail": detail})

        if result.get("success"):
            yield sse("status", {"step": 10, "total": 10, "message": "Pipeline complete."})
            yield sse("complete", {
                "pipeline_result": result["pipeline_result"],
                "needs_approval": result["needs_approval"],
                "cost_exposure": result["cost_exposure"],
                "approval_threshold": result.get("approval_threshold", 50000),
                "trust_stats": result.get("trust_stats", {}),
            })
        else:
            yield sse("error", {"message": result.get("error", "Pipeline failed")})

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/approve")
async def approve_actions(body: dict):
    """Execute approved actions with granular per-action approvals."""
    pipeline_result = body.get("pipeline_result", {})
    rfq_emails = pipeline_result.get("drafts", {}).get("rfq_emails", [])

    if "approvals" in body:
        approvals = body["approvals"]
        approve_slack = approvals.get("slack", False)
        approved_rfq_indices = approvals.get("rfq_emails", [])
    else:
        approved = body.get("approved", False)
        approve_slack = approved
        approved_rfq_indices = list(range(len(rfq_emails))) if approved else []

    # ── Send Slack ────────────────────────────────────────────────────────────
    slack_result = {"sent": False}
    if approve_slack:
        slack_message = pipeline_result.get("drafts", {}).get("slack", {}).get("message", "")
        if slack_message:
            slack_result = _send_slack(slack_message)
        else:
            slack_result = {"sent": False, "skipped_reason": "No Slack draft in pipeline result"}

    # ── Send emails ───────────────────────────────────────────────────────────
    email_results = []
    for idx in approved_rfq_indices:
        rfq = rfq_emails[idx] if idx < len(rfq_emails) else None
        if rfq and rfq.get("to"):
            email_results.append(_send_smtp_email(rfq["to"], rfq["subject"], rfq["body"]))
        elif rfq:
            email_results.append({"sent": False, "error": "No recipient address in RFQ draft"})

    any_approved = approve_slack or len(approved_rfq_indices) > 0
    return {
        "message": "Selected actions executed." if any_approved else "All actions cancelled.",
        "slack": slack_result,
        "emails": email_results,
        "approved_count": len(approved_rfq_indices) + (1 if approve_slack else 0)
    }


# ── FEATURE 2: Trust Engine endpoints ────────────────────────────────────────

@app.get("/trust")
def get_trust():
    """Return current trust stats and threshold."""
    return get_trust_stats()


@app.post("/trust/outcome")
async def record_trust_outcome(body: dict):
    """
    Record outcome of a past recommendation to update the trust-calibrated threshold.
    Body: {event_id, sku, recommended_supplier, cost_exposure, outcome, notes}
    outcome: 'good' | 'bad' | 'neutral'
    """
    result = record_outcome(
        event_id=body.get("event_id", "unknown"),
        sku=body.get("sku", ""),
        recommended_supplier=body.get("recommended_supplier", ""),
        cost_exposure=body.get("cost_exposure", 0),
        outcome=body.get("outcome", "neutral"),
        notes=body.get("notes", "")
    )
    return {"updated": True, "trust_stats": result}


@app.post("/trust/reset")
def reset_trust_endpoint():
    """Reset trust to baseline — for demo/testing."""
    return {"reset": True, "trust_stats": reset_trust()}


# ── FEATURE 3: Uncertainty / Supplier Knowledge endpoints ─────────────────────

@app.get("/knowledge")
def get_knowledge():
    """Return supplier knowledge base."""
    return get_all_knowledge()


@app.post("/knowledge/annotate")
async def annotate_supplier_endpoint(body: dict):
    """
    Human annotates a supplier with quality notes.
    Body: {supplier_name, quality_note, uncertainty}
    uncertainty: 'HIGH' | 'MEDIUM' | 'LOW'
    """
    result = annotate_supplier(
        supplier_name=body.get("supplier_name", ""),
        quality_note=body.get("quality_note", ""),
        uncertainty=body.get("uncertainty")
    )
    return {"updated": True, "supplier": result}


@app.post("/knowledge/reset")
def reset_knowledge_endpoint():
    """Reset supplier knowledge — for demo/testing."""
    reset_knowledge()
    return {"reset": True}


# ── Monitor / Events ──────────────────────────────────────────────────────────

@app.get("/events")
def get_events():
    return {"events": get_event_log()}


@app.get("/monitor/stream")
async def monitor_event_stream():
    q = asyncio.Queue()
    _sse_monitor_clients.append(q)

    async def event_stream():
        try:
            while True:
                payload = await q.get()
                yield f"event: monitor_complete\ndata: {json.dumps(payload, default=str)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if q in _sse_monitor_clients:
                _sse_monitor_clients.remove(q)

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Demo endpoints ────────────────────────────────────────────────────────────

@app.post("/demo/reset")
def demo_reset():
    clear_active_events()
    clear_event_log()
    return {"reset": True}


@app.post("/demo/pre-shock")
def demo_pre_shock():
    mock_data.apply_pre_shock()
    clear_active_events()
    clear_event_log()
    return {"reset": True, "state": "pre-shock"}


@app.post("/demo/inject-shock")
def demo_inject_shock():
    mock_data.apply_shock()
    clear_active_events()
    return {"injected": True, "state": "shocked"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8002, reload=True)
