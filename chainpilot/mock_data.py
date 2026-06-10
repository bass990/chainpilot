"""
Mock data feeds simulating a real inventory + supplier system.
In production these would be live API calls to ERP, supplier portals, etc.

Design decisions:
- Delivery dates are computed relative to today so the demo always feels live.
- apply_shock() also updates PRICE_FEEDS so price-spike detection fires.
- Alternative supplier available_qty covers a full 30-day order (quantity_needed
  = daily_production_need * 30) so search_alternative_suppliers() never silently
  filters out every candidate.
- Cost exposure in the shocked scenario is calibrated to exceed $50,000 so the
  human approval gate always fires.
"""

import random
import math
from datetime import datetime, timedelta

# ── Real-time simulation state ────────────────────────────────────────────────
# Tracks the current "phase" so the drift engine knows how to behave.
# Phases: "healthy" → "pre_shock" → "shocked"
_sim_phase: str = "healthy"
_sim_tick: int = 0          # increments every drift_tick() call


def _date(days_from_today: int) -> str:
    """Return a date string N days from today — keeps the demo evergreen."""
    return (datetime.now() + timedelta(days=days_from_today)).strftime("%Y-%m-%d")


# ── Shocked state patches (crisis levels) ────────────────────────────────────
# SKU-4821: 8.5 hours to stockout — CRITICAL
# SKU-7703: 26 hours — WARNING
# SKU-5541: 74 hours — borderline
# Suppliers SUP-001 (primary for SKU-4821) and SUP-003 both delayed.
# Price feeds also updated so SKU-4821 triggers price-spike detection (>15%).

_SHOCKED_INVENTORY_PATCH = {
    "SKU-4821": {"stock_units": 42,   "stock_pct": 0.12, "hours_to_stockout": 8.5},
    "SKU-7703": {"stock_units": 1100, "stock_pct": 0.22, "hours_to_stockout": 26.4},
    "SKU-5541": {"stock_units": 68,   "stock_pct": 0.31, "hours_to_stockout": 74.0},
}

_SHOCKED_SUPPLIERS_PATCH = {
    "SUP-001": {"status": "DELAYED", "delay_days": 14, "delay_reason": "Port congestion — Shanghai"},
    "SUP-003": {"status": "DELAYED", "delay_days": 5,  "delay_reason": "Raw material shortage — steel alloy"},
}

# Price feeds react to the shock — SKU-4821 spikes 23.4% above baseline,
# crossing the 15% threshold so price-spike detection fires alongside low-stock.
_SHOCKED_PRICE_PATCH = {
    "SKU-4821": {"current_price": 153.00, "baseline_30d": 124.00, "change_pct": 0.234},
    "SKU-7703": {"current_price": 1.04,   "baseline_30d": 0.85,   "change_pct": 0.224},
}

# ── Healthy state patches (pre-shock baseline) ────────────────────────────────

_HEALTHY_INVENTORY_PATCH = {
    "SKU-4821": {"stock_units": 1326, "stock_pct": 0.78, "hours_to_stockout": 220.0},
    "SKU-7703": {"stock_units": 4050, "stock_pct": 0.81, "hours_to_stockout": 243.0},
    "SKU-5541": {"stock_units": 187,  "stock_pct": 0.85, "hours_to_stockout": 204.0},
}

_HEALTHY_SUPPLIERS_PATCH = {
    "SUP-001": {"status": "ACTIVE", "delay_days": 0, "delay_reason": ""},
    "SUP-003": {"status": "ACTIVE", "delay_days": 0, "delay_reason": ""},
}

_HEALTHY_PRICE_PATCH = {
    "SKU-4821": {"current_price": 126.48, "baseline_30d": 124.00, "change_pct": 0.020},
    "SKU-7703": {"current_price": 0.87,   "baseline_30d": 0.85,   "change_pct": 0.024},
}

# ── Live data dicts (mutated in place by apply_pre_shock / apply_shock) ───────
# Start in healthy state.

INVENTORY = {
    # daily_production_need = 85 → quantity_needed (30d) = 2,550
    # unit_cost = $124.00
    # alt supplier FastBear at $149/unit → 30d exposure = ~$51,884 → approval gate fires
    "SKU-4821": {
        "name": "Precision bearing assembly",
        "stock_units": 1326,
        "safety_stock": 350,
        "stock_pct": 0.78,
        "daily_production_need": 85,
        "hours_to_stockout": 220.0,
        "unit_cost": 124.00,
        "preferred_supplier": "SUP-001",
    },
    # daily_production_need = 800 → quantity_needed (30d) = 24,000
    "SKU-7703": {
        "name": "Stainless steel fastener M8",
        "stock_units": 4050,
        "safety_stock": 5000,
        "stock_pct": 0.81,
        "daily_production_need": 800,
        "hours_to_stockout": 243.0,
        "unit_cost": 0.85,
        "preferred_supplier": "SUP-003",
    },
    # daily_production_need = 10 → quantity_needed (30d) = 300
    "SKU-5541": {
        "name": "Servo motor controller",
        "stock_units": 187,
        "safety_stock": 220,
        "stock_pct": 0.85,
        "daily_production_need": 10,
        "hours_to_stockout": 204.0,
        "unit_cost": 420.00,
        "preferred_supplier": "SUP-004",
    },
    # daily_production_need = 40 → quantity_needed (30d) = 1,200
    "SKU-2210": {
        "name": "Hydraulic seal kit",
        "stock_units": 620,
        "safety_stock": 400,
        "stock_pct": 0.89,
        "daily_production_need": 40,
        "hours_to_stockout": 534.0,
        "unit_cost": 38.50,
        "preferred_supplier": "SUP-002",
    },
}

SUPPLIERS = {
    "SUP-001": {
        "name": "PrecisionParts Co.",
        "status": "ACTIVE",
        "delay_days": 0,
        "delay_reason": "",
        "contact_email": "orders@precisionparts.example.com",
        "reliability_score": 4.8,
        "sku_coverage": ["SKU-4821", "SKU-7703"],
    },
    "SUP-002": {
        "name": "HydroSeal Industries",
        "status": "ACTIVE",
        "delay_days": 0,
        "delay_reason": "",
        "contact_email": "orders@hydroseal.example.com",
        "reliability_score": 4.5,
        "sku_coverage": ["SKU-2210"],
    },
    "SUP-003": {
        "name": "MetalForge Ltd.",
        "status": "ACTIVE",
        "delay_days": 0,
        "delay_reason": "",
        "contact_email": "supply@metalforge.example.com",
        "reliability_score": 4.2,
        "sku_coverage": ["SKU-7703", "SKU-4821"],
    },
    "SUP-004": {
        "name": "TechMotion Electronics",
        "status": "ACTIVE",
        "delay_days": 0,
        "delay_reason": "",
        "contact_email": "orders@techmotion.example.com",
        "reliability_score": 4.6,
        "sku_coverage": ["SKU-5541"],
    },
}


def apply_pre_shock():
    """Reset live data to healthy baseline."""
    global _sim_phase, _sim_tick
    for sku, patch in _HEALTHY_INVENTORY_PATCH.items():
        INVENTORY[sku].update(patch)
    for sid, patch in _HEALTHY_SUPPLIERS_PATCH.items():
        SUPPLIERS[sid].update(patch)
    for sku, patch in _HEALTHY_PRICE_PATCH.items():
        PRICE_FEEDS[sku].update(patch)
    _sim_phase = "healthy"
    _sim_tick = 0


def apply_shock():
    """Apply crisis-level values to simulate a supply chain shock."""
    global _sim_phase
    for sku, patch in _SHOCKED_INVENTORY_PATCH.items():
        INVENTORY[sku].update(patch)
    for sid, patch in _SHOCKED_SUPPLIERS_PATCH.items():
        SUPPLIERS[sid].update(patch)
    for sku, patch in _SHOCKED_PRICE_PATCH.items():
        PRICE_FEEDS[sku].update(patch)
    _sim_phase = "shocked"


def drift_tick():
    """
    Called every POLL_INTERVAL_SECONDS by the backend monitor loop.

    Applies small, realistic random fluctuations to INVENTORY stock levels
    and PRICE_FEEDS so the dashboard shows live-feeling data even when no
    shock has been triggered.

    Healthy phase:  mild consumption drift + tiny price noise.
    Shocked phase:  faster stock burn + accelerating price climb.
    """
    global _sim_tick
    _sim_tick += 1
    phase = _sim_phase

    for sku, item in INVENTORY.items():
        dpn = item.get("daily_production_need", 0)
        if dpn == 0:
            continue

        # Hourly consumption between ticks (poll interval ≈ 15s → ~0.004 h)
        # We exaggerate slightly so the demo shows visible movement.
        hours_per_tick = 0.05 if phase == "shocked" else 0.02
        units_consumed = dpn / 24 * hours_per_tick

        # Add ±10% random noise
        units_consumed *= random.uniform(0.9, 1.1)

        new_stock = max(0, item["stock_units"] - units_consumed)
        item["stock_units"] = round(new_stock, 1)

        # Recompute hours_to_stockout
        hourly_need = dpn / 24
        item["hours_to_stockout"] = round(new_stock / hourly_need, 1) if hourly_need else 9999

        # Recompute stock_pct relative to a nominal max (safety_stock * 4)
        nominal_max = item.get("safety_stock", dpn * 30)
        item["stock_pct"] = round(min(1.0, new_stock / (nominal_max * 4 + 0.01)), 3)

    # Price drift: small sine wave + noise so charts look organic
    for sku, feed in PRICE_FEEDS.items():
        base = feed["baseline_30d"]
        # Shocked SKUs drift upward; others oscillate near baseline
        if phase == "shocked" and sku in ("SKU-4821", "SKU-7703"):
            trend = 0.0008 * _sim_tick          # slow upward creep each tick
            noise = random.uniform(-0.003, 0.005)
        else:
            trend = 0.0
            noise = math.sin(_sim_tick * 0.4 + hash(sku) % 10) * 0.002 + random.uniform(-0.001, 0.001)

        new_change = max(-0.30, min(0.50, feed["change_pct"] + trend + noise))
        feed["change_pct"] = round(new_change, 4)
        feed["current_price"] = round(base * (1 + new_change), 2)


# ── Alternative suppliers ─────────────────────────────────────────────────────
# CRITICAL: available_qty must exceed quantity_needed (daily_need * 30) for
# search_alternative_suppliers() to return results instead of an empty list.
#
# SKU-4821: quantity_needed = 85 * 30 = 2,550
#   FastBear:   3,200 ✓  (fastest lead, highest unit cost — Advocate's pick)
#   GlobalParts: 5,000 ✓  (slower but $18 cheaper per unit — Skeptic's point)
#   QuickMfg:   2,600 ✓  (barely covers, premium price — emergency-only)
#
# SKU-7703: quantity_needed = 800 * 30 = 24,000
#   All three exceed 24,000 ✓
#
# SKU-5541: quantity_needed = 10 * 30 = 300
#   Both exceed 300 ✓

ALTERNATIVE_SUPPLIERS = {
    "SKU-4821": [
        {
            "name": "FastBear Inc.",
            "contact_email": "sales@fastbear.example.com",
            "lead_days": 3,
            "unit_cost": 149.00,      # $25 premium — pushes 30d exposure to ~$51,884 → gate fires
            "quality_tier": "A",
            "min_order_qty": 100,
            "available_qty": 3200,    # covers 2,550 needed ✓
        },
        {
            "name": "GlobalParts Ltd.",
            "contact_email": "procurement@globalparts.example.com",
            "lead_days": 6,
            "unit_cost": 135.00,      # only $11 premium — cheaper but 3 more days (Skeptic's argument)
            "quality_tier": "A-",
            "min_order_qty": 200,
            "available_qty": 5000,    # covers 2,550 needed ✓
        },
        {
            "name": "QuickMfg Co.",
            "contact_email": "orders@quickmfg.example.com",
            "lead_days": 2,
            "unit_cost": 167.00,      # highest premium — fastest but most expensive
            "quality_tier": "B+",
            "min_order_qty": 50,
            "available_qty": 2600,    # just covers 2,550 needed ✓
        },
    ],
    "SKU-7703": [
        {
            "name": "SteelDirect Corp.",
            "contact_email": "sales@steeldirect.example.com",
            "lead_days": 5,
            "unit_cost": 0.97,
            "quality_tier": "A",
            "min_order_qty": 5000,
            "available_qty": 50000,   # covers 24,000 needed ✓
        },
        {
            "name": "FastenerWorld Ltd.",
            "contact_email": "orders@fastenerworld.example.com",
            "lead_days": 3,
            "unit_cost": 1.05,
            "quality_tier": "A-",
            "min_order_qty": 2000,
            "available_qty": 30000,   # covers 24,000 needed ✓
        },
        {
            "name": "BulkMetal Co.",
            "contact_email": "procurement@bulkmetal.example.com",
            "lead_days": 8,
            "unit_cost": 0.91,
            "quality_tier": "B+",
            "min_order_qty": 10000,
            "available_qty": 100000,  # covers 24,000 needed ✓
        },
    ],
    "SKU-5541": [
        {
            "name": "MotionTech Supply",
            "contact_email": "sales@motiontech.example.com",
            "lead_days": 7,
            "unit_cost": 465.00,
            "quality_tier": "A",
            "min_order_qty": 10,
            "available_qty": 400,     # covers 300 needed ✓
        },
        {
            "name": "ServoSource Inc.",
            "contact_email": "orders@servosource.example.com",
            "lead_days": 12,
            "unit_cost": 440.00,
            "quality_tier": "A",
            "min_order_qty": 25,
            "available_qty": 500,     # covers 300 needed ✓
        },
    ],
}

# ── Customer orders ───────────────────────────────────────────────────────────
# Delivery dates are relative to today so the demo is always live.
# Tier penalty curve is steep — PLATINUM feels 4x more urgent than SILVER.
# Apex Manufacturing (PLATINUM, due tomorrow) is the anchor for the countdown timer.
#
# Tier    | Penalty/day | Annual revenue
# PLATINUM|  $8,500     | $2.2M+
# GOLD    |  $2,400     | $650K–900K
# SILVER  |    $750     | $200K–400K

CUSTOMER_ORDERS = [
    {
        "order_id": "ORD-8821",
        "customer": "Apex Manufacturing",
        "customer_tier": "PLATINUM",
        "sku": "SKU-4821",
        "qty_ordered": 200,
        "delivery_date": _date(1),          # tomorrow — triggers countdown banner
        "contract_penalty_per_day": 8500,
        "annual_revenue": 2_200_000,
    },
    {
        "order_id": "ORD-8834",
        "customer": "TechDrive Corp",
        "customer_tier": "PLATINUM",
        "sku": "SKU-4821",
        "qty_ordered": 180,
        "delivery_date": _date(2),
        "contract_penalty_per_day": 7200,
        "annual_revenue": 1_850_000,
    },
    {
        "order_id": "ORD-8851",
        "customer": "MidWest Robotics",
        "customer_tier": "GOLD",
        "sku": "SKU-4821",
        "qty_ordered": 150,
        "delivery_date": _date(4),
        "contract_penalty_per_day": 2800,
        "annual_revenue": 920_000,
    },
    {
        "order_id": "ORD-8860",
        "customer": "RapidAssembly LLC",
        "customer_tier": "GOLD",
        "sku": "SKU-4821",
        "qty_ordered": 120,
        "delivery_date": _date(6),
        "contract_penalty_per_day": 2100,
        "annual_revenue": 680_000,
    },
    {
        "order_id": "ORD-8874",
        "customer": "Vertex Aerospace",
        "customer_tier": "SILVER",
        "sku": "SKU-4821",
        "qty_ordered": 90,
        "delivery_date": _date(8),
        "contract_penalty_per_day": 850,
        "annual_revenue": 390_000,
    },
    {
        "order_id": "ORD-8889",
        "customer": "CoastalFab Inc.",
        "customer_tier": "SILVER",
        "sku": "SKU-4821",
        "qty_ordered": 60,
        "delivery_date": _date(10),
        "contract_penalty_per_day": 650,
        "annual_revenue": 210_000,
    },
]

# ── Price feeds ───────────────────────────────────────────────────────────────
# Healthy state: minor fluctuations only — no spikes above 15% threshold.
# Shocked state: SKU-4821 and SKU-7703 spike above 15% (patched by apply_shock).

PRICE_FEEDS = {
    "SKU-4821": {"current_price": 126.48, "baseline_30d": 124.00, "change_pct": 0.020},
    "SKU-7703": {"current_price": 0.87,   "baseline_30d": 0.85,   "change_pct": 0.024},
    "SKU-5541": {"current_price": 428.50, "baseline_30d": 420.00, "change_pct": 0.020},
    "SKU-2210": {"current_price": 38.50,  "baseline_30d": 38.50,  "change_pct": 0.000},
}
