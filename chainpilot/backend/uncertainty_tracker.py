"""
ChainPilot Uncertainty Tracker — Agent Knowledge Graph

The agent explicitly flags what it doesn't know and builds a persistent
knowledge base of supplier trust from past interactions.

When the agent makes a recommendation about a supplier it has no quality
history with, it says so. Humans can annotate those gaps. Over time the
agent's uncertainty on well-known suppliers drops; new/untested suppliers
stay flagged until proven.

Stored in supplier_knowledge.json — persists across runs.
"""

import json, os, threading
from datetime import datetime, timezone

_KB_PATH = os.path.join(os.path.dirname(__file__), "supplier_knowledge.json")
_lock = threading.Lock()

# Default uncertainty level for a supplier we've never seen before
_DEFAULT_UNCERTAINTY = "HIGH"


def _load() -> dict:
    if os.path.exists(_KB_PATH):
        try:
            with open(_KB_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {"suppliers": {}, "gaps": []}


def _save(kb: dict):
    with open(_KB_PATH, "w") as f:
        json.dump(kb, f, indent=2)


def get_supplier_knowledge(supplier_name: str) -> dict:
    """Return what we know (or don't know) about a supplier."""
    with _lock:
        kb = _load()
        sup = kb["suppliers"].get(supplier_name)
        if sup:
            return sup
        return {
            "supplier": supplier_name,
            "uncertainty": _DEFAULT_UNCERTAINTY,
            "interactions": 0,
            "quality_notes": [],
            "known_gaps": [
                "No quality history on file",
                "Delivery performance unknown",
                "No past RFQ responses to compare"
            ]
        }


def get_all_knowledge() -> dict:
    with _lock:
        return _load()


def record_rfq_sent(supplier_name: str, sku: str, event_id: str):
    """Record that we sent an RFQ to this supplier — first data point."""
    with _lock:
        kb = _load()
        sup = kb["suppliers"].setdefault(supplier_name, {
            "supplier": supplier_name,
            "uncertainty": _DEFAULT_UNCERTAINTY,
            "interactions": 0,
            "quality_notes": [],
            "known_gaps": [
                "No quality history on file",
                "Delivery performance unknown",
                "No past RFQ responses to compare"
            ],
            "rfq_history": []
        })
        sup["interactions"] = sup.get("interactions", 0) + 1
        sup.setdefault("rfq_history", []).append({
            "event_id": event_id,
            "sku": sku,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "outcome": "pending"
        })
        # After first contact, uncertainty drops slightly to MEDIUM
        if sup["interactions"] == 1 and sup["uncertainty"] == _DEFAULT_UNCERTAINTY:
            sup["known_gaps"] = [g for g in sup.get("known_gaps", [])
                                  if "RFQ" not in g]
        _save(kb)


def annotate_supplier(supplier_name: str, quality_note: str, uncertainty: str = None) -> dict:
    """
    Human annotates a supplier with quality notes.
    uncertainty: 'HIGH' | 'MEDIUM' | 'LOW'
    """
    with _lock:
        kb = _load()
        sup = kb["suppliers"].setdefault(supplier_name, {
            "supplier": supplier_name,
            "uncertainty": _DEFAULT_UNCERTAINTY,
            "interactions": 0,
            "quality_notes": [],
            "known_gaps": []
        })
        sup.setdefault("quality_notes", []).append({
            "note": quality_note,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        })
        if uncertainty and uncertainty in ("HIGH", "MEDIUM", "LOW"):
            sup["uncertainty"] = uncertainty
            # Reduce known_gaps as uncertainty drops
            if uncertainty == "LOW":
                sup["known_gaps"] = []
            elif uncertainty == "MEDIUM":
                sup["known_gaps"] = [g for g in sup.get("known_gaps", [])
                                      if "No quality" not in g]
        _save(kb)
        return sup


def assess_recommendation_uncertainty(recommended_supplier: str,
                                       alt_suppliers: list) -> dict:
    """
    Given a recommended supplier and the alternatives considered,
    return an uncertainty assessment the agent must include in its
    final recommendation.

    Returns dict with: uncertainty_level, gaps, flagged_unknowns
    """
    with _lock:
        kb = _load()

    rec_knowledge = kb["suppliers"].get(recommended_supplier, None)
    rec_uncertainty = rec_knowledge["uncertainty"] if rec_knowledge else _DEFAULT_UNCERTAINTY
    rec_gaps = rec_knowledge.get("known_gaps", [
        "No quality history on file",
        "Delivery performance unknown"
    ]) if rec_knowledge else [
        "No quality history on file",
        "Delivery performance unknown",
        "No past RFQ responses to compare"
    ]

    alt_flags = []
    for alt_name in alt_suppliers:
        alt_k = kb["suppliers"].get(alt_name, None)
        if not alt_k or alt_k.get("uncertainty") == "HIGH":
            alt_flags.append(f"{alt_name}: no quality history")

    return {
        "recommended_supplier": recommended_supplier,
        "uncertainty_level": rec_uncertainty,
        "known_gaps": rec_gaps,
        "alternative_unknowns": alt_flags,
        "recommendation_caveat": (
            f"Uncertainty: {rec_uncertainty}. "
            + (f"Gaps: {'; '.join(rec_gaps)}." if rec_gaps else "No known gaps.")
        )
    }


def reset_knowledge():
    """Reset supplier knowledge base — for demo/testing."""
    with _lock:
        _save({"suppliers": {}, "gaps": []})
