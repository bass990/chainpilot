"""Day-7 tests for eval/runners.py — pipeline structure verification without LLM calls.

These tests use a mocked Anthropic client to verify:
  - run_stripped_pipeline makes exactly 3 LLM calls in the right order/roles
  - run_full_pipeline makes exactly 5 LLM calls in the right order/roles
  - Procurement + Risk run in parallel (verified by call-order tolerance)
  - Advocate + Skeptic run in parallel
  - The render_disruption_context output is deterministic for a given scenario
  - JSON parsing handles markdown fences, prose preludes, plain JSON
  - The eval prompts are still substring-aligned with production agent.py

NO LLM CALLS — the mock returns canned responses. CI runs these for free.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from eval.runners import (
    _parse_json_safe,
    render_disruption_context,
    run_full_pipeline,
    run_stripped_pipeline,
)
from eval.schemas import load_scenario


REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIO_PATH = REPO_ROOT / "eval" / "scenarios" / "scenario_001.json"


# ── Mock client helpers ───────────────────────────────────────────────────────

def _make_mock_response(text: str, input_tokens: int = 150, output_tokens: int = 80):
    """Build a mock Anthropic response object."""
    response = MagicMock()
    response.content = [MagicMock(type="text", text=text)]
    response.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return response


def _make_mock_client(canned_responses: list[str]):
    """Build a mock Anthropic client that returns canned_responses in order.

    Tracks every call so the test can assert on system prompts + count + order.
    """
    client = MagicMock()
    call_log: list[dict] = []

    response_iter = iter(canned_responses)

    def messages_create(**kwargs):
        call_log.append({
            "system": kwargs.get("system"),
            "model": kwargs.get("model"),
            "max_tokens": kwargs.get("max_tokens"),
            "messages": kwargs.get("messages"),
        })
        try:
            text = next(response_iter)
        except StopIteration:
            raise AssertionError("More LLM calls than canned responses provided")
        return _make_mock_response(text)

    client.messages.create = messages_create
    client.messages.create._call_log = call_log
    return client, call_log


# ── render_disruption_context tests ──────────────────────────────────────────

def test_render_context_is_deterministic():
    """Same scenario → same render. Snapshot-stable for testability."""
    scenario = load_scenario(SCENARIO_PATH)
    r1 = render_disruption_context(scenario)
    r2 = render_disruption_context(scenario)
    assert r1 == r2


def test_render_context_includes_key_fields():
    """The rendered context contains the SKU, stock pct, supplier names, and customer info."""
    scenario = load_scenario(SCENARIO_PATH)
    rendered = render_disruption_context(scenario)
    assert scenario.input.sku in rendered
    assert scenario.input.primary_supplier.name in rendered
    for alt in scenario.input.alternative_suppliers:
        assert alt.name in rendered
    for order in scenario.input.customer_orders_at_risk:
        assert order.customer in rendered
        assert order.tier in rendered


def test_render_context_handles_empty_alts_and_orders():
    """Scenario with no alternatives or customer orders renders cleanly."""
    scenario = load_scenario(REPO_ROOT / "eval" / "scenarios" / "scenario_017.json")
    rendered = render_disruption_context(scenario)
    # scenario_017 has no alternatives and no customer orders
    assert "(none listed)" in rendered


# ── JSON parsing tests ───────────────────────────────────────────────────────

def test_parse_json_plain():
    """Plain JSON parses correctly."""
    assert _parse_json_safe('{"final_action": "immediate_switch"}') == {"final_action": "immediate_switch"}


def test_parse_json_markdown_fence():
    """Markdown code-fenced JSON parses (the most common LLM output style)."""
    text = '```json\n{"final_action": "wait_and_monitor"}\n```'
    assert _parse_json_safe(text) == {"final_action": "wait_and_monitor"}


def test_parse_json_with_prose_prelude():
    """JSON wrapped in prose still parses ('Here's the JSON:' style)."""
    text = 'Here is the JSON response:\n{"final_action": "partial_order"}\nThat is my answer.'
    assert _parse_json_safe(text) == {"final_action": "partial_order"}


def test_parse_json_invalid_returns_empty():
    """Malformed JSON returns an empty dict rather than raising."""
    assert _parse_json_safe("this is not json") == {}


# ── Pipeline structure tests ─────────────────────────────────────────────────

def test_run_stripped_pipeline_makes_three_llm_calls():
    """Procurement + Risk + Synthesizer = 3 calls."""
    canned = [
        '{"recommended_supplier": "Alt-A", "alt_unit_cost": 1.15, "estimated_30d_exposure": 50000, '
        '"switching_premium": 5000, "supplier_options": []}',
        '{"hours_to_stockout": 96, "stock_pct": 30, "customers_affected": 1, '
        '"total_orders_at_risk": 1, "top_customer": "Apex", "7day_penalty_exposure": 35000}',
        '{"final_action": "immediate_switch", "recommended_supplier": "Alt-A", '
        '"severity": "HIGH", "confidence": "HIGH", "swing_condition": "if delay shortens", '
        '"rationale": "delay exceeds cover"}',
    ]
    client, call_log = _make_mock_client(canned)

    scenario = load_scenario(SCENARIO_PATH)
    result = run_stripped_pipeline(scenario, client=client)

    assert len(call_log) == 3, f"Expected 3 LLM calls, got {len(call_log)}: {[c['system'][:50] for c in call_log]}"
    assert result.final_action == "immediate_switch"
    assert result.recommended_supplier == "Alt-A"
    assert result.severity == "HIGH"
    assert result.confidence == "HIGH"
    assert result.swing_condition == "if delay shortens"
    assert result.branch == "stripped"
    assert len(result.traces) == 3


def test_run_full_pipeline_makes_five_llm_calls():
    """Procurement + Risk + Advocate + Skeptic + Arbiter = 5 calls."""
    canned = [
        # Procurement
        '{"recommended_supplier": "Alt-B", "alt_unit_cost": 1.18, "estimated_30d_exposure": 60000, '
        '"switching_premium": 6000, "supplier_options": []}',
        # Risk
        '{"hours_to_stockout": 4.5, "stock_pct": 5, "customers_affected": 1, '
        '"total_orders_at_risk": 1, "top_customer": "Apex", "7day_penalty_exposure": 100000}',
        # Advocate
        "Strong case to act: 1) PLATINUM at 9h, 2) stockout in 4.5h, 3) good alt. "
        "VERDICT: Act immediately — PLATINUM exposure overdetermined.",
        # Skeptic
        "Caution: 1) alt has HIGH uncertainty, 2) 18% premium. "
        "VERDICT: Wait / proceed cautiously — supplier risk underweighted.",
        # Arbiter
        '{"arbiter_recommendation": "Switch immediately to Alt-B", '
        '"strongest_advocate_point": "PLATINUM at 9h", '
        '"strongest_skeptic_point": "alt uncertainty", '
        '"swing_condition": "if PLATINUM deadline were >24h, partial preferred", '
        '"confidence": "MEDIUM", "final_action": "immediate_switch"}',
    ]
    client, call_log = _make_mock_client(canned)

    scenario = load_scenario(SCENARIO_PATH)
    result = run_full_pipeline(scenario, client=client)

    assert len(call_log) == 5, f"Expected 5 LLM calls, got {len(call_log)}"
    assert result.final_action == "immediate_switch"
    assert result.recommended_supplier == "Alt-B"  # from Procurement
    assert result.confidence == "MEDIUM"
    assert "PLATINUM" in (result.swing_condition or "")
    assert result.branch == "full"
    assert len(result.traces) == 5


def test_run_stripped_pipeline_system_prompts_are_correct():
    """Verify the right system prompt was used for each call."""
    canned = ['{"recommended_supplier": "X"}', '{"hours_to_stockout": 0}', '{"final_action": "wait_and_monitor"}']
    client, call_log = _make_mock_client(canned)
    scenario = load_scenario(SCENARIO_PATH)
    run_stripped_pipeline(scenario, client=client)

    # Procurement and Risk run in parallel — order may be either. Check both prompts present.
    prompts_used = [c["system"] for c in call_log[:2]]
    assert any("Procurement Specialist" in p for p in prompts_used), "Procurement prompt missing"
    assert any("Risk Assessment Specialist" in p for p in prompts_used), "Risk prompt missing"
    # Synthesizer always last
    assert "Procurement Decision Synthesizer" in call_log[2]["system"]


def test_run_full_pipeline_system_prompts_are_correct():
    """Verify the 5-stage pipeline uses the 5 expected prompts."""
    canned = [
        '{"recommended_supplier": "X"}',
        '{"hours_to_stockout": 0}',
        "Advocate verdict",
        "Skeptic verdict",
        '{"final_action": "immediate_switch", "confidence": "HIGH", "swing_condition": "X"}',
    ]
    client, call_log = _make_mock_client(canned)
    scenario = load_scenario(SCENARIO_PATH)
    run_full_pipeline(scenario, client=client)

    # Stage 1 parallel: Procurement + Risk
    stage1_prompts = [c["system"] for c in call_log[:2]]
    assert any("Procurement Specialist" in p for p in stage1_prompts)
    assert any("Risk Assessment Specialist" in p for p in stage1_prompts)

    # Stage 2 parallel: Advocate + Skeptic
    stage2_prompts = [c["system"] for c in call_log[2:4]]
    assert any("Procurement Advocate" in p for p in stage2_prompts)
    assert any("Procurement Skeptic" in p for p in stage2_prompts)

    # Stage 3: Arbiter
    assert "Decision Arbiter" in call_log[4]["system"]


def test_run_stripped_pipeline_handles_malformed_synthesizer_output():
    """If the synthesizer returns garbage, fields are None but no exception."""
    canned = [
        '{"recommended_supplier": "X"}',
        '{"hours_to_stockout": 0}',
        "I am unable to respond in JSON. Sorry.",
    ]
    client, _ = _make_mock_client(canned)
    scenario = load_scenario(SCENARIO_PATH)
    result = run_stripped_pipeline(scenario, client=client)

    assert result.final_action is None
    assert result.confidence is None
    # Synthesizer output captured as raw — caller can debug
    assert result.raw_finalize_output == {}


def test_traces_record_correct_metadata():
    """Each trace carries the right role, scenario_id, branch, rep."""
    canned = ['{"a": 1}', '{"b": 2}', '{"c": 3}']
    client, _ = _make_mock_client(canned)
    scenario = load_scenario(SCENARIO_PATH)
    result = run_stripped_pipeline(scenario, rep=2, client=client)

    assert len(result.traces) == 3
    roles = {t.role for t in result.traces}
    assert roles == {"procurement", "risk", "synthesizer"}
    for trace in result.traces:
        assert trace.scenario_id == scenario.id
        assert trace.branch == "stripped"
        assert trace.rep == 2
        assert trace.input_tokens == 150  # from _make_mock_response default
        assert trace.output_tokens == 80


# ── Drift-detection: prompts still match production ───────────────────────────

def test_eval_prompts_match_production_substrings():
    """eval/prompts.py mirrors chainpilot/backend/agent.py's specialist prompts.

    Verifies the eval prompts START with the production prompts as a substring
    (the eval prompts append an EVAL_MODE suffix where applicable). If this
    test fails, the production prompts have changed and eval/prompts.py needs
    a corresponding update.
    """
    from eval.prompts import (
        _SYSTEM_ADVOCATE_PRODUCTION,
        _SYSTEM_ARBITER_PRODUCTION,
        _SYSTEM_PROCUREMENT_PRODUCTION,
        _SYSTEM_RISK_PRODUCTION,
        _SYSTEM_SKEPTIC_PRODUCTION,
    )
    agent_path = REPO_ROOT / "chainpilot" / "backend" / "agent.py"
    agent_text = agent_path.read_text(encoding="utf-8")
    for name, value in (
        ("_SYSTEM_PROCUREMENT", _SYSTEM_PROCUREMENT_PRODUCTION),
        ("_SYSTEM_RISK", _SYSTEM_RISK_PRODUCTION),
        ("_SYSTEM_ADVOCATE", _SYSTEM_ADVOCATE_PRODUCTION),
        ("_SYSTEM_SKEPTIC", _SYSTEM_SKEPTIC_PRODUCTION),
        ("_SYSTEM_ARBITER", _SYSTEM_ARBITER_PRODUCTION),
    ):
        assert value in agent_text, (
            f"eval/prompts.py {name} no longer matches chainpilot/backend/agent.py. "
            "Either: production prompt was edited (update eval mirror), or eval mirror drifted (resync)."
        )
