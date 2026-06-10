import sys, os
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mock_data import INVENTORY, SUPPLIERS, ALTERNATIVE_SUPPLIERS, CUSTOMER_ORDERS, PRICE_FEEDS
from config import STOCK_THRESHOLD_PERCENT, PRICE_SPIKE_THRESHOLD, SUPPLIER_DELAY_DAYS

# ── Tool functions ────────────────────────────────────────────────────────────

def check_inventory_levels() -> dict:
    alerts = []
    for sku, data in INVENTORY.items():
        if data["stock_pct"] < STOCK_THRESHOLD_PERCENT:
            alerts.append({
                "sku": sku,
                "name": data["name"],
                "stock_pct": round(data["stock_pct"] * 100, 1),
                "hours_to_stockout": data["hours_to_stockout"],
                "daily_need": data["daily_production_need"]
            })
    return {"inventory": INVENTORY, "low_stock_alerts": alerts, "threshold_pct": STOCK_THRESHOLD_PERCENT * 100}


def check_supplier_status() -> dict:
    delayed = [
        {"supplier_id": sid, **s}
        for sid, s in SUPPLIERS.items()
        if s["status"] == "DELAYED" and s["delay_days"] > SUPPLIER_DELAY_DAYS
    ]
    return {"suppliers": SUPPLIERS, "delayed_suppliers": delayed}


def check_price_feeds() -> dict:
    spikes = [
        {"sku": sku, **data}
        for sku, data in PRICE_FEEDS.items()
        if data["change_pct"] > PRICE_SPIKE_THRESHOLD
    ]
    return {"prices": PRICE_FEEDS, "price_spikes": spikes}


def search_alternative_suppliers(sku: str, quantity_needed: int) -> dict:
    alts = ALTERNATIVE_SUPPLIERS.get(sku, [])
    viable = [a for a in alts if a["available_qty"] >= quantity_needed]
    ranked = sorted(viable, key=lambda x: (x["lead_days"], x["unit_cost"]))
    return {
        "sku": sku,
        "quantity_needed": quantity_needed,
        "alternatives": ranked,
        "recommendation": ranked[0] if ranked else None
    }


def calculate_cost_impact(sku: str, disruption_duration_days: int, alt_unit_cost: float) -> dict:
    inv = INVENTORY.get(sku, {})
    preferred_cost = inv.get("unit_cost", 0)
    daily_need = inv.get("daily_production_need", 0)
    switching_premium = (alt_unit_cost - preferred_cost) * daily_need * disruption_duration_days
    lost_production_cost = daily_need * preferred_cost * disruption_duration_days
    total_30d_exposure = switching_premium + (lost_production_cost * 0.15)
    return {
        "sku": sku,
        "disruption_days": disruption_duration_days,
        "lost_production_cost_per_day": round(daily_need * preferred_cost, 2),
        "switching_premium_total": round(switching_premium, 2),
        "estimated_30d_exposure": round(total_30d_exposure, 2),
        "preferred_unit_cost": preferred_cost,
        "alt_unit_cost": alt_unit_cost,
        "cost_premium_per_unit": round(alt_unit_cost - preferred_cost, 2)
    }


def get_affected_customer_orders(sku: str) -> dict:
    affected = [o for o in CUSTOMER_ORDERS if o["sku"] == sku]
    total_penalty_exposure = sum(
        o["contract_penalty_per_day"] * 7 for o in affected
    )
    affected_sorted = sorted(affected, key=lambda x: x["annual_revenue"], reverse=True)
    return {
        "sku": sku,
        "affected_orders": affected_sorted,
        "total_orders_at_risk": len(affected),
        "total_qty_at_risk": sum(o["qty_ordered"] for o in affected),
        "7day_penalty_exposure": total_penalty_exposure,
        "top_customer": affected_sorted[0]["customer"] if affected_sorted else None
    }


def draft_rfq_email(supplier_name: str, supplier_email: str, sku: str,
                    quantity: int, delivery_urgency_days: int,
                    draft_only: bool = True, urgent: bool = False) -> dict:
    inv = INVENTORY.get(sku, {})
    subject = f"Urgent RFQ — {inv.get('name', sku)} — {quantity} units needed in {delivery_urgency_days} days"
    body = f"""Dear {supplier_name} Procurement Team,

We are experiencing a critical supply disruption for {inv.get('name', sku)} (SKU: {sku}).

We urgently require:
- Quantity: {quantity:,} units
- Required delivery: Within {delivery_urgency_days} business days
- Delivery location: [Your facility address]
- Quality specification: Per our standard drawing {sku}-SPEC-v3

Please confirm:
1. Availability of {quantity:,} units
2. Your best price per unit
3. Confirmed lead time

This is a critical production situation. Please respond within 4 hours.

Best regards,
Procurement Team
[Company Name]
[Contact: procurement@company.example.com]"""
    return {"success": True, "to": supplier_email, "subject": subject, "body": body, "draft_only": True}


def draft_slack_alert(sku: str, severity: str, affected_customers: int,
                      cost_exposure: float, action_taken: str,
                      draft_only: bool = True) -> dict:
    inv = INVENTORY.get(sku, {})
    emoji = {"CRITICAL": "🚨", "HIGH": "⚠️", "MEDIUM": "📋", "LOW": "ℹ️"}.get(severity, "📋")
    message = (
        f"{emoji} *SUPPLY CHAIN ALERT — {severity}*\n"
        f"*SKU:* {sku} — {inv.get('name', 'Unknown')}\n"
        f"*Stock:* {round(INVENTORY.get(sku, {}).get('stock_pct', 0)*100, 1)}% remaining\n"
        f"*Hours to stockout:* {INVENTORY.get(sku, {}).get('hours_to_stockout', 0)}\n"
        f"*Customers at risk:* {affected_customers}\n"
        f"*30-day exposure:* ${cost_exposure:,.0f}\n"
        f"*Agent action:* {action_taken}\n"
        f"*Approval required:* <http://localhost:8002/approve|Click here to review>"
    )
    return {"success": True, "channel": "#procurement-alerts", "message": message, "draft_only": True}


def log_disruption_event(event_id: str, sku: str, severity: str,
                         actions_taken: list, status: str, **kwargs) -> dict:
    entry = {
        "event_id": event_id,
        "sku": sku,
        "severity": severity,
        "actions": actions_taken,
        "status": status,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    }
    return {"success": True, "logged": entry}


def finalize_analysis(severity: str, sku: str, disruption_type: str,
                      cost_exposure: float, customers_affected: int,
                      recommended_supplier: str, recommended_action: str,
                      needs_human_approval: bool, actions_completed: list,
                      confidence: str) -> dict:
    return {
        "severity": severity,
        "sku": sku,
        "disruption_type": disruption_type,
        "cost_exposure": cost_exposure,
        "customers_affected": customers_affected,
        "recommended_supplier": recommended_supplier,
        "recommended_action": recommended_action,
        "needs_human_approval": needs_human_approval,
        "actions_completed": actions_completed,
        "confidence": confidence
    }


# ── Tool schemas ──────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "check_inventory_levels",
        "description": "Poll current inventory levels for all SKUs and identify items below safety stock threshold.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "check_supplier_status",
        "description": "Check status of all active suppliers and identify delays.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "check_price_feeds",
        "description": "Check current prices vs 30-day baseline and flag spikes above threshold.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "search_alternative_suppliers",
        "description": "Find and rank alternative suppliers for a disrupted SKU.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "quantity_needed": {"type": "integer"}
            },
            "required": ["sku", "quantity_needed"]
        }
    },
    {
        "name": "calculate_cost_impact",
        "description": "Calculate the full financial impact of a supply disruption.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "disruption_duration_days": {"type": "integer"},
                "alt_unit_cost": {"type": "number"}
            },
            "required": ["sku", "disruption_duration_days", "alt_unit_cost"]
        }
    },
    {
        "name": "get_affected_customer_orders",
        "description": "Identify all customer orders at risk from the disruption.",
        "input_schema": {
            "type": "object",
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"]
        }
    },
    {
        "name": "draft_rfq_email",
        "description": "Draft a Request for Quotation email to an alternative supplier.",
        "input_schema": {
            "type": "object",
            "properties": {
                "supplier_name": {"type": "string"},
                "supplier_email": {"type": "string"},
                "sku": {"type": "string"},
                "quantity": {"type": "integer"},
                "delivery_urgency_days": {"type": "integer"},
                "draft_only": {"type": "boolean"},
                "urgent": {"type": "boolean"}
            },
            "required": ["supplier_name", "supplier_email", "sku", "quantity", "delivery_urgency_days"]
        }
    },
    {
        "name": "draft_slack_alert",
        "description": "Draft a Slack alert for the procurement team.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "severity": {"type": "string", "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
                "affected_customers": {"type": "integer"},
                "cost_exposure": {"type": "number"},
                "action_taken": {"type": "string"},
                "draft_only": {"type": "boolean"}
            },
            "required": ["sku", "severity", "affected_customers", "cost_exposure", "action_taken"]
        }
    },
    {
        "name": "log_disruption_event",
        "description": "Log the disruption event and all actions taken to the audit trail.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "sku": {"type": "string"},
                "severity": {"type": "string"},
                "actions_taken": {"type": "array", "items": {"type": "string"}},
                "status": {"type": "string"}
            },
            "required": ["event_id", "sku", "severity", "actions_taken", "status"]
        }
    },
    {
        "name": "finalize_analysis",
        "description": (
            "REQUIRED: Call this as your absolute final step after completing all other tools. "
            "Records the structured summary of the disruption response. "
            "You MUST call this tool — do not end your turn without calling it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "severity":             {"type": "string", "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
                "sku":                  {"type": "string"},
                "disruption_type":      {"type": "string", "enum": ["low_stock", "supplier_delay", "price_spike"]},
                "cost_exposure":        {"type": "number"},
                "customers_affected":   {"type": "integer"},
                "recommended_supplier": {"type": "string"},
                "recommended_action":   {"type": "string"},
                "needs_human_approval": {"type": "boolean"},
                "actions_completed":    {"type": "array", "items": {"type": "string"}},
                "confidence":           {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]}
            },
            "required": [
                "severity", "sku", "disruption_type", "cost_exposure", "customers_affected",
                "recommended_supplier", "recommended_action", "needs_human_approval",
                "actions_completed", "confidence"
            ]
        }
    }
]

TOOL_MAP = {
    "check_inventory_levels": check_inventory_levels,
    "check_supplier_status": check_supplier_status,
    "check_price_feeds": check_price_feeds,
    "search_alternative_suppliers": search_alternative_suppliers,
    "calculate_cost_impact": calculate_cost_impact,
    "get_affected_customer_orders": get_affected_customer_orders,
    "draft_rfq_email": draft_rfq_email,
    "draft_slack_alert": draft_slack_alert,
    "log_disruption_event": log_disruption_event,
    "finalize_analysis": finalize_analysis,
}

# ── Per-agent tool subsets ────────────────────────────────────────────────────

PROCUREMENT_TOOLS = [t for t in TOOLS if t["name"] in {
    "check_supplier_status", "check_price_feeds",
    "search_alternative_suppliers", "calculate_cost_impact",
}]

RISK_TOOLS = [t for t in TOOLS if t["name"] in {
    "check_inventory_levels", "get_affected_customer_orders",
}]

COMMS_TOOLS = [t for t in TOOLS if t["name"] in {
    "draft_rfq_email", "draft_slack_alert", "log_disruption_event",
}]
