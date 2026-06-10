"""
ChainPilot Trust Engine — Confidence-Calibrated Autonomy

The agent earns or loses the right to act autonomously based on past performance.
Instead of a fixed $50K threshold, the approval threshold rises when the agent
is right and falls when its recommendation turns out to be poor.

This is stored in a JSON file (trust_ledger.json) so it persists across runs.
"""

import json, os, threading
from datetime import datetime, timezone

_LEDGER_PATH = os.path.join(os.path.dirname(__file__), "trust_ledger.json")
_lock = threading.Lock()

# Threshold bounds
_MIN_THRESHOLD = 10_000    # Never auto-execute below $10K (always some oversight)
_MAX_THRESHOLD = 200_000   # Never auto-execute above $200K regardless of trust score
_BASE_THRESHOLD = 50_000   # Starting point

# Scoring: +1 outcome point for "good" outcome, -2 for "bad" (asymmetric — bad outcomes cost more)
_GOOD_OUTCOME_BOOST    =  8_000   # Threshold rises $8K per validated good recommendation
_BAD_OUTCOME_PENALTY   = 20_000   # Threshold drops $20K per bad outcome


def _load() -> dict:
    if os.path.exists(_LEDGER_PATH):
        try:
            with open(_LEDGER_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "current_threshold": _BASE_THRESHOLD,
        "total_decisions": 0,
        "good_outcomes": 0,
        "bad_outcomes": 0,
        "history": []
    }


def _save(ledger: dict):
    with open(_LEDGER_PATH, "w") as f:
        json.dump(ledger, f, indent=2)


def get_current_threshold() -> int:
    with _lock:
        return _load().get("current_threshold", _BASE_THRESHOLD)


def get_trust_stats() -> dict:
    with _lock:
        ledger = _load()
        total = ledger.get("total_decisions", 0)
        good  = ledger.get("good_outcomes", 0)
        bad   = ledger.get("bad_outcomes", 0)
        accuracy = round(good / total * 100, 1) if total > 0 else None
        return {
            "current_threshold": ledger.get("current_threshold", _BASE_THRESHOLD),
            "base_threshold": _BASE_THRESHOLD,
            "min_threshold": _MIN_THRESHOLD,
            "max_threshold": _MAX_THRESHOLD,
            "total_decisions": total,
            "good_outcomes": good,
            "bad_outcomes": bad,
            "accuracy_pct": accuracy,
            "history": ledger.get("history", [])[-10:]   # last 10 events
        }


def record_outcome(event_id: str, sku: str, recommended_supplier: str,
                   cost_exposure: float, outcome: str, notes: str = "") -> dict:
    """
    Record the outcome of an agent recommendation.

    outcome: "good"  — recommendation was followed and worked well
             "bad"   — recommendation led to a poor result
             "neutral" — approved but outcome unknown / not yet measured

    Returns updated trust stats.
    """
    with _lock:
        ledger = _load()
        old_threshold = ledger.get("current_threshold", _BASE_THRESHOLD)

        ledger["total_decisions"] = ledger.get("total_decisions", 0) + 1

        if outcome == "good":
            ledger["good_outcomes"] = ledger.get("good_outcomes", 0) + 1
            new_threshold = min(_MAX_THRESHOLD, old_threshold + _GOOD_OUTCOME_BOOST)
            delta_desc = f"+${_GOOD_OUTCOME_BOOST:,} (good outcome)"
        elif outcome == "bad":
            ledger["bad_outcomes"] = ledger.get("bad_outcomes", 0) + 1
            new_threshold = max(_MIN_THRESHOLD, old_threshold - _BAD_OUTCOME_PENALTY)
            delta_desc = f"-${_BAD_OUTCOME_PENALTY:,} (bad outcome)"
        else:
            new_threshold = old_threshold
            delta_desc = "no change (neutral)"

        ledger["current_threshold"] = new_threshold
        ledger.setdefault("history", []).append({
            "event_id": event_id,
            "sku": sku,
            "recommended_supplier": recommended_supplier,
            "cost_exposure": cost_exposure,
            "outcome": outcome,
            "notes": notes,
            "threshold_before": old_threshold,
            "threshold_after": new_threshold,
            "delta": delta_desc,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        })

        _save(ledger)
        return get_trust_stats()


def reset_trust():
    """Reset to baseline — useful for demo/testing."""
    with _lock:
        ledger = {
            "current_threshold": _BASE_THRESHOLD,
            "total_decisions": 0,
            "good_outcomes": 0,
            "bad_outcomes": 0,
            "history": []
        }
        _save(ledger)
    return ledger
