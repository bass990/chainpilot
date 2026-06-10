import json, uuid, sys, os, concurrent.futures, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic
from config import MODEL, MAX_TOKENS, ANTHROPIC_API_KEY
from backend.tools import TOOLS, TOOL_MAP, PROCUREMENT_TOOLS, RISK_TOOLS, COMMS_TOOLS
from backend.trust_engine import get_current_threshold, get_trust_stats
from backend.uncertainty_tracker import (
    assess_recommendation_uncertainty, record_rfq_sent
)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _create_with_retry(max_retries=5, initial_wait=15, **kwargs):
    """Call client.messages.create with exponential backoff on rate limit errors."""
    wait = initial_wait
    for attempt in range(max_retries):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            time.sleep(wait)
            wait = min(wait * 2, 60)

# ── Specialist system prompts ─────────────────────────────────────────────────

_SYSTEM_PROCUREMENT = """You are the Procurement Specialist for ChainPilot. A supply chain disruption has been detected — analyze it and find the best supplier response.

Available tools:
- check_supplier_status() — current delays, reliability scores, and affected SKUs
- check_price_feeds() — current price vs. 30-day baseline per SKU
- search_alternative_suppliers(sku, quantity_needed) — ranked list of backup suppliers
- calculate_cost_impact(sku, disruption_duration_days, alt_unit_cost) — switching cost vs. waiting

Use your judgment about which tools to call and in what order. For a supplier delay, start with supplier status. For a price spike, start with price feeds. Only call calculate_cost_impact once you have identified a specific alternative supplier to compare against. You do not need to call every tool if the situation doesn't require it.

When you have enough information to make a recommendation, respond with ONLY this JSON (no other text):
{"recommended_supplier":"...","alt_unit_cost":0.0,"estimated_30d_exposure":0.0,"switching_premium":0.0,"supplier_options":[]}"""

_SYSTEM_RISK = """You are the Risk Assessment Specialist for ChainPilot. Quantify the inventory and customer exposure from a supply chain disruption.

Available tools:
- check_inventory_levels() — stock percentages and hours-to-stockout per SKU
- get_affected_customer_orders(sku) — customer orders at risk, sorted by revenue tier

Call check_inventory_levels first to understand urgency. Then use your judgment: if inventory shows no meaningful stockout risk (stock_pct above 50% and hours_to_stockout above 168), customer order exposure is likely negligible and you may skip the second tool. Otherwise call get_affected_customer_orders.

When done, respond with ONLY this JSON (no other text):
{"hours_to_stockout":0.0,"stock_pct":0.0,"customers_affected":0,"total_orders_at_risk":0,"top_customer":"","7day_penalty_exposure":0.0}"""

_SYSTEM_COMMS = """You are the Communications Specialist for ChainPilot. You will receive a full disruption analysis from Procurement and Risk specialists. Draft the appropriate communications.

Available tools:
- draft_rfq_email(supplier_name, supplier_email, sku, quantity, delivery_urgency_days) — draft a supplier RFQ
- draft_slack_alert(sku, severity, affected_customers, cost_exposure, action_taken) — draft internal Slack alert
- log_disruption_event(event_id, sku, severity, actions_taken, status) — write audit log entry

All communications must use draft_only=True.

Use your judgment: draft an RFQ to the recommended supplier from the procurement analysis. If a strong second alternative exists in supplier_options, draft a second RFQ. Assess severity from hours_to_stockout (CRITICAL < 24h, HIGH < 48h, else MEDIUM). Always draft the Slack alert and log the event."""

# ── FEATURE 1: Adversarial Deliberation prompts ───────────────────────────────

_SYSTEM_ADVOCATE = """You are the Procurement Advocate for ChainPilot. Your job is to argue FOR switching to the recommended alternative supplier immediately.

You have been given a full disruption analysis. Make the strongest possible case for acting NOW. Focus on:
- Stockout risk and customer penalties if action is delayed
- The recommended supplier's lead time advantage
- 30-day cost exposure if nothing is done
- Precedent for acting on similar disruptions

Respond with 3-5 bullet points making the case to act. Be specific with numbers from the analysis. End with: "VERDICT: Act immediately — [reason]."

Do NOT hedge. You are an advocate."""

_SYSTEM_SKEPTIC = """You are the Procurement Skeptic for ChainPilot. Your job is to argue AGAINST switching suppliers immediately.

You have been given a full disruption analysis. Make the strongest possible case for waiting or proceeding cautiously. Focus on:
- Unknown quality risk of the alternative supplier
- Whether the cost premium is actually justified
- Whether stock cover is as dire as reported
- Whether partial orders are better than a full switch
- Whether the preferred supplier might recover faster than projected

Respond with 3-5 bullet points urging caution. Be specific. End with: "VERDICT: Wait / proceed cautiously — [reason]."

Do NOT hedge. You are a skeptic."""

_SYSTEM_ARBITER = """You are the Decision Arbiter for ChainPilot. You have read arguments from both a Procurement Advocate and a Procurement Skeptic. Synthesize them into a final recommendation.

Weigh both arguments. Then produce a final recommendation that:
1. Acknowledges the strongest point from each side
2. States a clear recommended action
3. Identifies what would need to be true for the opposing view to be correct
4. Assigns a confidence level: HIGH (advocate clearly wins), MEDIUM (close call), or LOW (genuine uncertainty)

Respond with ONLY this JSON (no other text):
{"arbiter_recommendation":"...","strongest_advocate_point":"...","strongest_skeptic_point":"...","swing_condition":"...","confidence":"HIGH|MEDIUM|LOW","final_action":"immediate_switch|partial_order|wait_and_monitor|emergency_spot_buy"}"""

_SYSTEM_ORCHESTRATOR_TEMPLATE = """You are the ChainPilot Orchestrator. A supply chain disruption has been detected. Your job is to coordinate the right specialists to assess and respond to it.

Available specialists:
- run_specialist_analysis(sku, quantity_needed, run_procurement, run_risk) — runs Procurement and/or Risk agents simultaneously
- run_adversarial_deliberation(sku, procurement_summary, risk_summary) — runs Advocate vs Skeptic debate then Arbiter synthesis; call after specialist analysis
- run_communications_draft(sku, event_id, procurement_summary, risk_summary) — drafts RFQ emails, Slack alert, and audit log
- finalize_analysis(...) — synthesizes all results into the final recommendation; always call this last

Disruption context:
{disruption_type_rules}

Guidelines:
- For most disruptions, set both run_procurement and run_risk to True.
- ALWAYS run run_adversarial_deliberation after specialist analysis.
- Pass the full JSON result strings from specialist analysis as context to deliberation and communications.
- Always call finalize_analysis as your final step.
- Current auto-execute threshold (trust-calibrated): ${approval_threshold:,}. Set needs_human_approval=True if exposure >= this amount.
- Default quantity_needed: daily_production_need x 30."""

_ORCH_RULES_BY_TYPE = {
    "low_stock": (
        "CRITICAL if stockout < 24 hours. Both RFQ emails must be marked URGENT. "
        "Prioritize fastest lead time over lowest cost."
    ),
    "supplier_delay": (
        "CRITICAL if delay_days exceeds current stock cover in days. "
        "HIGH if delay_days <= stock cover. "
        "Calculate break-even: switching to alt supplier vs. waiting for preferred."
    ),
    "price_spike": (
        "Spike > 20%: recommend spot purchase; draft RFQ to cheaper alternative. "
        "Spike 15-20%: draft RFQ only if alternative is materially cheaper. "
        "Focus analysis on 30-day cost exposure, not stockout urgency."
    ),
}

# ── Orchestrator meta-tool schemas ────────────────────────────────────────────

_FINALIZE_SCHEMA = next(t for t in TOOLS if t["name"] == "finalize_analysis")

ORCHESTRATOR_TOOLS = [
    {
        "name": "run_specialist_analysis",
        "description": (
            "Runs Procurement and/or Risk Specialist agents simultaneously. "
            "Set run_procurement=True to find alternative suppliers and calculate 30-day cost exposure. "
            "Set run_risk=True to quantify inventory stockout risk and customer order exposure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sku":             {"type": "string"},
                "quantity_needed": {"type": "integer"},
                "run_procurement": {"type": "boolean"},
                "run_risk":        {"type": "boolean"}
            },
            "required": ["sku", "quantity_needed", "run_procurement", "run_risk"]
        }
    },
    {
        "name": "run_adversarial_deliberation",
        "description": (
            "Runs Advocate vs Skeptic debate followed by Arbiter synthesis. "
            "The Advocate argues for immediate action; the Skeptic argues for caution. "
            "The Arbiter synthesizes both into a sharpened final recommendation with confidence level. "
            "Call after run_specialist_analysis and before run_communications_draft."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sku":                 {"type": "string"},
                "procurement_summary": {"type": "string"},
                "risk_summary":        {"type": "string"}
            },
            "required": ["sku", "procurement_summary", "risk_summary"]
        }
    },
    {
        "name": "run_communications_draft",
        "description": (
            "Runs the Communications Specialist agent. Drafts RFQ emails, Slack alert, and audit log. "
            "Must be called after run_adversarial_deliberation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sku":                 {"type": "string"},
                "event_id":            {"type": "string"},
                "procurement_summary": {"type": "string"},
                "risk_summary":        {"type": "string"}
            },
            "required": ["sku", "event_id", "procurement_summary", "risk_summary"]
        }
    },
    _FINALIZE_SCHEMA,
]


# ── Specialist agent functions ────────────────────────────────────────────────

def _run_procurement_agent(sku: str, quantity_needed: int, on_progress=None) -> dict:
    def log(step, msg, tool=None, detail=None):
        if on_progress:
            on_progress(step, msg, tool, detail)

    messages = [{"role": "user", "content": f"Analyze procurement options for SKU {sku}. Quantity needed: {quantity_needed} units."}]
    result = {"tools_called": [], "cost_analysis_raw": {}, "supplier_options": []}
    tool_map = {t["name"]: TOOL_MAP[t["name"]] for t in PROCUREMENT_TOOLS}

    for _ in range(10):
        response = _create_with_retry(
            model=MODEL, max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": _SYSTEM_PROCUREMENT, "cache_control": {"type": "ephemeral"}}],
            tools=PROCUREMENT_TOOLS, messages=messages,
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"}
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    try:
                        result.update(json.loads(block.text))
                    except json.JSONDecodeError:
                        pass
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                fn = tool_map.get(block.name)
                try:
                    tool_result = fn(**block.input) if fn else {"error": f"Unknown tool: {block.name}"}
                except TypeError as e:
                    tool_result = {"error": f"Tool call failed: {e}", "success": False}
                result["tools_called"].append(block.name)
                if block.name == "calculate_cost_impact":
                    result["cost_analysis_raw"] = tool_result
                elif block.name == "search_alternative_suppliers":
                    alts = tool_result.get("alternatives", [])
                    result["supplier_options"] = alts
                    if alts:
                        result.setdefault("recommended_supplier", alts[0].get("name", ""))
                        result.setdefault("alt_unit_cost", alts[0].get("unit_cost", 0))
                if block.name == "check_supplier_status":
                    delayed = [s for s in tool_result.get("delayed_suppliers", []) if sku in s.get("sku_coverage", [])]
                    detail = f"{delayed[0]['name']} DELAYED {delayed[0]['delay_days']}d · {delayed[0].get('delay_reason','')}" if delayed else "Preferred supplier active — no delays"
                    log(3, "Checked supplier status", block.name, detail)
                elif block.name == "check_price_feeds":
                    feed = tool_result.get("prices", {}).get(sku, {})
                    detail = f"+{round(feed.get('change_pct',0)*100,1)}% vs 30-day baseline · ${feed.get('current_price',0)}/unit" if feed else "No price data"
                    log(4, "Scanned price feeds", block.name, detail)
                elif block.name == "search_alternative_suppliers":
                    alts = tool_result.get("alternatives", [])
                    detail = f"{len(alts)} alternatives · {alts[0]['name']} ${alts[0]['unit_cost']}/unit · {alts[0]['lead_days']}-day lead" if alts else "No viable alternatives found"
                    log(5, "Found alternative suppliers", block.name, detail)
                elif block.name == "calculate_cost_impact":
                    exp = tool_result.get("estimated_30d_exposure", 0)
                    prem = tool_result.get("switching_premium_total", 0)
                    detail = f"30-day exposure: ${exp:,.0f} · switching premium: ${prem:,.0f}"
                    log(6, "Calculated cost impact", block.name, detail)
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(tool_result)})
            messages.append({"role": "user", "content": tool_results})

    if not result.get("estimated_30d_exposure") and result.get("cost_analysis_raw"):
        result["estimated_30d_exposure"] = result["cost_analysis_raw"].get("estimated_30d_exposure", 0)
        result["switching_premium"] = result["cost_analysis_raw"].get("switching_premium_total", 0)
    return result


def _run_risk_agent(sku: str, on_progress=None) -> dict:
    def log(step, msg, tool=None, detail=None):
        if on_progress:
            on_progress(step, msg, tool, detail)

    messages = [{"role": "user", "content": f"Assess inventory risk and customer exposure for SKU {sku}."}]
    result = {"tools_called": [], "customer_impact_raw": {}}
    tool_map = {t["name"]: TOOL_MAP[t["name"]] for t in RISK_TOOLS}

    for _ in range(10):
        response = _create_with_retry(
            model=MODEL, max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": _SYSTEM_RISK, "cache_control": {"type": "ephemeral"}}],
            tools=RISK_TOOLS, messages=messages,
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"}
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    try:
                        result.update(json.loads(block.text))
                    except json.JSONDecodeError:
                        pass
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                fn = tool_map.get(block.name)
                try:
                    tool_result = fn(**block.input) if fn else {"error": f"Unknown tool: {block.name}"}
                except TypeError as e:
                    tool_result = {"error": f"Tool call failed: {e}", "success": False}
                result["tools_called"].append(block.name)
                if block.name == "get_affected_customer_orders":
                    result["customer_impact_raw"] = tool_result
                if block.name == "check_inventory_levels":
                    alert = next((a for a in tool_result.get("low_stock_alerts", []) if a["sku"] == sku), None)
                    detail = f"{alert['stock_pct']}% stock · {alert['hours_to_stockout']}h to stockout" if alert else f"{round(tool_result.get('inventory',{}).get(sku,{}).get('stock_pct',0)*100,1)}% stock — within threshold"
                    log(2, "Checked inventory levels", block.name, detail)
                elif block.name == "get_affected_customer_orders":
                    n = tool_result.get("total_orders_at_risk", 0)
                    top = tool_result.get("top_customer", "")
                    orders = tool_result.get("affected_orders", [])
                    earliest = min((o["delivery_date"] for o in orders), default="")
                    daily_pen = tool_result.get("7day_penalty_exposure", 0) / 7
                    detail = f"{n} orders at risk · ${daily_pen:,.0f}/day penalties · {top} due {earliest}" if n else "No customer orders at risk"
                    log(7, "Assessed customer exposure", block.name, detail)
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(tool_result)})
            messages.append({"role": "user", "content": tool_results})
    return result


# ── FEATURE 1: Adversarial Deliberation ──────────────────────────────────────

def _run_adversarial_deliberation(sku: str, procurement_summary: str,
                                   risk_summary: str, on_progress=None) -> dict:
    def log(step, msg, tool=None, detail=None):
        if on_progress:
            on_progress(step, msg, tool, detail)

    context = (
        f"DISRUPTION: SKU {sku}\n\n"
        f"PROCUREMENT ANALYSIS:\n{procurement_summary}\n\n"
        f"RISK ASSESSMENT:\n{risk_summary}"
    )

    log(5, "Adversarial deliberation — Advocate arguing for action...", "adversarial_deliberation")

    def _run_advocate():
        resp = _create_with_retry(
            model=MODEL, max_tokens=1024,
            system=[{"type": "text", "text": _SYSTEM_ADVOCATE, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"Make the case for immediate action.\n\n{context}"}],
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"}
        )
        return resp.content[0].text if resp.content else ""

    def _run_skeptic():
        resp = _create_with_retry(
            model=MODEL, max_tokens=1024,
            system=[{"type": "text", "text": _SYSTEM_SKEPTIC, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"Make the case for caution.\n\n{context}"}],
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"}
        )
        return resp.content[0].text if resp.content else ""

    with concurrent.futures.ThreadPoolExecutor() as pool:
        f_adv = pool.submit(_run_advocate)
        f_skp = pool.submit(_run_skeptic)
        advocate_argument = f_adv.result()
        skeptic_argument  = f_skp.result()

    log(6, "Arbiter synthesizing Advocate vs Skeptic debate...", "adversarial_deliberation",
        "Both sides argued — arbiter weighing evidence")

    arbiter_prompt = (
        f"{context}\n\n"
        f"ADVOCATE ARGUMENT:\n{advocate_argument}\n\n"
        f"SKEPTIC ARGUMENT:\n{skeptic_argument}\n\n"
        "Weigh both arguments and produce your synthesis as JSON."
    )
    arbiter_resp = _create_with_retry(
        model=MODEL, max_tokens=1024,
        system=[{"type": "text", "text": _SYSTEM_ARBITER, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": arbiter_prompt}],
        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"}
    )
    arbiter_text = arbiter_resp.content[0].text if arbiter_resp.content else "{}"
    try:
        arbiter_result = json.loads(arbiter_text)
    except json.JSONDecodeError:
        arbiter_result = {"arbiter_recommendation": arbiter_text, "confidence": "MEDIUM", "final_action": "immediate_switch"}

    detail = f"Verdict: {arbiter_result.get('final_action','?')} · confidence: {arbiter_result.get('confidence','?')}"
    log(6, "Deliberation complete", "adversarial_deliberation", detail)

    return {
        "advocate_argument": advocate_argument,
        "skeptic_argument": skeptic_argument,
        "arbiter_result": arbiter_result,
        "deliberation_confidence": arbiter_result.get("confidence", "MEDIUM"),
        "final_action": arbiter_result.get("final_action", "immediate_switch")
    }


def _run_communications_agent(sku: str, event_id: str,
                               procurement_summary: str, risk_summary: str,
                               on_progress=None) -> dict:
    def log(step, msg, tool=None, detail=None):
        if on_progress:
            on_progress(step, msg, tool, detail)

    messages = [{
        "role": "user",
        "content": (
            f"Draft communications for SKU {sku}, Event ID {event_id}.\n\n"
            f"PROCUREMENT ANALYSIS:\n{procurement_summary}\n\n"
            f"RISK ASSESSMENT:\n{risk_summary}\n\n"
            "Review the context above and draft the appropriate supplier and stakeholder communications."
        )
    }]
    result = {"tools_called": [], "rfq_emails": [], "slack_draft": {}, "audit_log": {}}
    tool_map = {t["name"]: TOOL_MAP[t["name"]] for t in COMMS_TOOLS}

    for _ in range(10):
        response = _create_with_retry(
            model=MODEL, max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": _SYSTEM_COMMS, "cache_control": {"type": "ephemeral"}}],
            tools=COMMS_TOOLS, messages=messages,
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"}
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                fn = tool_map.get(block.name)
                try:
                    tool_result = fn(**block.input) if fn else {"error": f"Unknown tool: {block.name}"}
                except TypeError as e:
                    tool_result = {"error": f"Tool call failed — unexpected param: {e}", "success": False}
                result["tools_called"].append(block.name)
                if block.name == "draft_rfq_email":
                    result["rfq_emails"].append(tool_result)
                    supplier_name = block.input.get("supplier_name", "")
                    if supplier_name:
                        record_rfq_sent(supplier_name, sku, event_id)  # FEATURE 3
                elif block.name == "draft_slack_alert":
                    result["slack_draft"] = tool_result
                elif block.name == "log_disruption_event":
                    result["audit_log"] = tool_result
                if block.name == "draft_rfq_email":
                    log(8, "Drafted RFQ email", block.name, f"RFQ drafted to {block.input.get('supplier_name','supplier')}")
                elif block.name == "draft_slack_alert":
                    log(8, "Drafted Slack alert", block.name, f"{block.input.get('severity','')} severity · {block.input.get('affected_customers',0)} customers affected")
                elif block.name == "log_disruption_event":
                    log(9, "Logged audit trail", block.name, f"Audit trail saved · {tool_result.get('logged',{}).get('event_id','')}")
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(tool_result)})
            messages.append({"role": "user", "content": tool_results})
    return result


# ── Orchestrator ──────────────────────────────────────────────────────────────

def _build_orchestrator_prompt(disruption_type: str, approval_threshold: int) -> str:
    rules = _ORCH_RULES_BY_TYPE.get(disruption_type, _ORCH_RULES_BY_TYPE["low_stock"])
    return _SYSTEM_ORCHESTRATOR_TEMPLATE.format(
        disruption_type_rules=rules,
        approval_threshold=approval_threshold
    )


def _run_orchestrator(trigger_event: dict, event_id: str,
                      system_prompt: str, approval_threshold: int,
                      on_progress=None) -> dict:
    def log(step, msg, tool=None, detail=None):
        if on_progress:
            on_progress(step, msg, tool, detail)

    def _run_parallel_specialists(sku, quantity_needed, run_procurement, run_risk):
        if run_procurement:
            log(3, "Running Procurement Agent — finding alternatives...", "run_procurement_analysis")
        if run_risk:
            log(5, "Running Risk Agent — assessing customer exposure...", "run_risk_assessment")
        with concurrent.futures.ThreadPoolExecutor() as pool:
            futures = {}
            if run_procurement:
                futures["procurement"] = pool.submit(_run_procurement_agent, sku=sku, quantity_needed=quantity_needed, on_progress=on_progress)
            if run_risk:
                futures["risk"] = pool.submit(_run_risk_agent, sku=sku, on_progress=on_progress)
            return {k: f.result() for k, f in futures.items()}

    orchestrator_tool_map = {
        "run_specialist_analysis":      lambda **kw: _run_parallel_specialists(**kw),
        "run_adversarial_deliberation": lambda **kw: _run_adversarial_deliberation(on_progress=on_progress, **kw),
        "run_communications_draft":     lambda **kw: _run_communications_agent(on_progress=on_progress, **kw),
        "finalize_analysis":            TOOL_MAP["finalize_analysis"],
    }

    orch_step_map = {
        "run_specialist_analysis":      (3, "Running specialist analysis..."),
        "run_adversarial_deliberation": (5, "Running adversarial deliberation..."),
        "run_communications_draft":     (8, "Running Communications Agent..."),
        "finalize_analysis":            (9, "Finalizing analysis..."),
    }

    pipeline_result = {
        "event_id": event_id,
        "trigger": trigger_event,
        "disruption_type": trigger_event.get("type", "low_stock"),
        "tools_called": [],
        "drafts": {},
        "cost_analysis": {},
        "customer_impact": {},
        "deliberation": {},
        "uncertainty": {},
        "final_summary": None,
        "approval_threshold": approval_threshold,
    }

    messages = [{
        "role": "user",
        "content": (
            f"DISRUPTION ALERT — Event ID: {event_id}\n\n"
            f"Trigger: {json.dumps(trigger_event, indent=2)}\n\n"
            "Assess this disruption and coordinate the appropriate response. "
            "Use run_specialist_analysis first, then run_adversarial_deliberation to sharpen the recommendation, "
            "then run_communications_draft, then call finalize_analysis to complete the pipeline."
        )
    }]

    for _ in range(10):
        response = _create_with_retry(
            model=MODEL, max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            tools=ORCHESTRATOR_TOOLS, messages=messages,
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"}
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    pipeline_result["final_summary"] = block.text
            if "structured_summary" not in pipeline_result:
                pipeline_result["incomplete"] = True
            break

        if response.stop_reason == "tool_use":
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            for block in tool_use_blocks:
                step_num, step_msg = orch_step_map.get(block.name, (5, f"Running {block.name}..."))
                log(step_num, step_msg, block.name)

            def _execute_specialist(block):
                fn = orchestrator_tool_map.get(block.name)
                result = fn(**block.input) if fn else {"error": f"Unknown tool: {block.name}"}
                return block, result

            with concurrent.futures.ThreadPoolExecutor() as pool:
                futures = [pool.submit(_execute_specialist, b) for b in tool_use_blocks]
                executed = [f.result() for f in concurrent.futures.as_completed(futures)]

            tool_results = []
            for block, specialist_result in executed:
                if block.name == "run_specialist_analysis":
                    if "procurement" in specialist_result:
                        p = specialist_result["procurement"]
                        pipeline_result["cost_analysis"] = p.get("cost_analysis_raw", {})
                        pipeline_result["tools_called"].extend(p.get("tools_called", []))
                        # FEATURE 3: uncertainty assessment
                        recommended = p.get("recommended_supplier", "")
                        alt_names = [a.get("name", "") for a in p.get("supplier_options", [])]
                        if recommended:
                            pipeline_result["uncertainty"] = assess_recommendation_uncertainty(recommended, alt_names)
                    if "risk" in specialist_result:
                        r = specialist_result["risk"]
                        pipeline_result["customer_impact"] = r.get("customer_impact_raw", {})
                        pipeline_result["tools_called"].extend(r.get("tools_called", []))
                elif block.name == "run_adversarial_deliberation":
                    pipeline_result["deliberation"] = specialist_result
                    pipeline_result["tools_called"].append("adversarial_deliberation")
                elif block.name == "run_communications_draft":
                    if specialist_result.get("rfq_emails"):
                        pipeline_result["drafts"]["rfq_emails"] = specialist_result["rfq_emails"]
                    if specialist_result.get("slack_draft"):
                        pipeline_result["drafts"]["slack"] = specialist_result["slack_draft"]
                    pipeline_result["tools_called"].extend(specialist_result.get("tools_called", []))
                elif block.name == "finalize_analysis":
                    pipeline_result["structured_summary"] = specialist_result
                    pipeline_result["tools_called"].append("finalize_analysis")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(specialist_result, default=str)
                })
            messages.append({"role": "user", "content": tool_results})
    else:
        pipeline_result["incomplete"] = True
        pipeline_result["incomplete_reason"] = "Orchestrator turn limit (10) reached without finalize_analysis"

    return pipeline_result


# ── Public interface ──────────────────────────────────────────────────────────

def run_disruption_pipeline(trigger_event: dict, on_progress=None) -> dict:
    def log(step, msg, tool=None, detail=None):
        if on_progress:
            on_progress(step, msg, tool, detail)

    event_id = f"EVT-{str(uuid.uuid4())[:8].upper()}"
    log(1, "Disruption detected — starting response pipeline...", None)

    # FEATURE 2: Live trust-calibrated threshold
    approval_threshold = get_current_threshold()
    trust_stats = get_trust_stats()
    threshold_detail = (
        f"Based on {trust_stats['total_decisions']} past decisions · accuracy: {trust_stats['accuracy_pct']}%"
        if trust_stats["total_decisions"] > 0
        else "Starting at baseline $50,000"
    )
    log(2, f"Trust threshold: ${approval_threshold:,}", None, threshold_detail)

    disruption_type = trigger_event.get("type", "low_stock")
    system_prompt = _build_orchestrator_prompt(disruption_type, approval_threshold)
    pipeline_result = _run_orchestrator(trigger_event, event_id, system_prompt, approval_threshold, on_progress)

    exposure = pipeline_result.get("cost_analysis", {}).get("estimated_30d_exposure", 0)
    needs_approval = exposure >= approval_threshold
    pipeline_result["trust_stats"] = trust_stats

    log(10, "Pipeline complete — awaiting human approval." if needs_approval else "Pipeline complete.", None)

    return {
        "success": True,
        "pipeline_result": pipeline_result,
        "needs_approval": needs_approval,
        "approval_threshold": approval_threshold,
        "cost_exposure": exposure,
        "trust_stats": trust_stats,
    }


def detect_disruptions() -> list:
    from backend.tools import check_inventory_levels, check_supplier_status, check_price_feeds
    events = []
    inv = check_inventory_levels()
    for alert in inv.get("low_stock_alerts", []):
        events.append({"type": "low_stock", "sku": alert["sku"], "name": alert["name"],
                        "detected_value": f"{alert['stock_pct']}% stock remaining",
                        "hours_to_stockout": alert["hours_to_stockout"]})
    sup = check_supplier_status()
    for s in sup.get("delayed_suppliers", []):
        events.append({"type": "supplier_delay", "supplier": s["name"], "delay_days": s["delay_days"],
                        "reason": s.get("delay_reason", "Unknown"),
                        "sku": s.get("sku_coverage", ["Unknown"])[0] if s.get("sku_coverage") else "Unknown",
                        "detected_value": f"{s['delay_days']} day delay"})
    prices = check_price_feeds()
    for spike in prices.get("price_spikes", []):
        events.append({"type": "price_spike", "sku": spike["sku"],
                        "detected_value": f"+{round(spike['change_pct']*100,1)}% price increase"})
    return events


if __name__ == "__main__":
    events = detect_disruptions()
    if events:
        print(f"Detected {len(events)} disruption(s). Running pipeline for each...")
        for event in events:
            result = run_disruption_pipeline(event)
            print(json.dumps(result, indent=2, default=str))
    else:
        print("No disruptions detected.")
