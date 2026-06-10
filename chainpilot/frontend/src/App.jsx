import { useState, useEffect, useRef } from "react";
import "./App.css";

const API = "http://localhost:8002";

// ── Real-time simulation helpers ──────────────────────────────────────────────
const SIM_PHASE_CONFIG = {
  healthy:   { label: "🟢 Live · Healthy",   color: "#16A34A" },
  pre_shock: { label: "🟡 Live · Pre-shock", color: "#D97706" },
  shocked:   { label: "🔴 Live · Shocked",   color: "#DC2626" },
};
// Lightweight snapshot poll interval (ms) — faster than full dashboard
const SNAPSHOT_INTERVAL_MS = 3000;

const SEV_CONFIG = {
  CRITICAL: { color: "#DC2626", bg: "#FEF2F2", border: "#FECACA", label: "Critical" },
  WARNING:  { color: "#D97706", bg: "#FFFBEB", border: "#FDE68A", label: "Warning"  },
  HIGH:     { color: "#EA580C", bg: "#FFF7ED", border: "#FED7AA", label: "High"     },
  ACTIVE:   { color: "#16A34A", bg: "#F0FDF4", border: "#BBF7D0", label: "Active"   },
};

const TOOLS_ORDER = [
  "check_inventory_levels", "check_supplier_status", "check_price_feeds",
  "search_alternative_suppliers", "calculate_cost_impact",
  "get_affected_customer_orders", "adversarial_deliberation",
  "draft_rfq_email", "draft_slack_alert", "log_disruption_event"
];

const TOOL_LABELS = {
  "check_inventory_levels":       "Check inventory levels",
  "check_supplier_status":        "Check supplier status",
  "check_price_feeds":            "Scan price feeds",
  "search_alternative_suppliers": "Find alternative suppliers",
  "calculate_cost_impact":        "Calculate cost impact",
  "get_affected_customer_orders": "Assess customer exposure",
  "adversarial_deliberation":     "Advocate ⚔ Skeptic debate",
  "draft_rfq_email":              "Draft RFQ emails",
  "draft_slack_alert":            "Draft Slack alert",
  "log_disruption_event":         "Log audit trail",
};

// ── Sub-components ────────────────────────────────────────────────────────────

function StockBar({ pct, name, hours }) {
  const color = pct < 20 ? "#DC2626" : pct < 30 ? "#D97706" : "#16A34A";
  return (
    <div className="stock-bar-row">
      <div className="stock-bar-info">
        <span className="stock-name">{name}</span>
        <span className="stock-hours" style={{ color }}>{pct}% · {hours}h left</span>
      </div>
      <div className="stock-bar-bg">
        <div className="stock-bar-fill" style={{ width: `${Math.min(pct, 100)}%`, background: color }} />
      </div>
    </div>
  );
}

// FEATURE 2: Trust Meter
function TrustMeter({ stats }) {
  if (!stats) return null;
  const { current_threshold, base_threshold, min_threshold, max_threshold,
          total_decisions, good_outcomes, bad_outcomes, accuracy_pct } = stats;
  const range = max_threshold - min_threshold;
  const pos = ((current_threshold - min_threshold) / range) * 100;
  const color = current_threshold >= base_threshold ? "#16A34A" : current_threshold >= 30000 ? "#D97706" : "#DC2626";
  return (
    <div className="trust-meter">
      <div className="trust-header">
        <span className="trust-title">🧠 Agent Trust Level</span>
        <span className="trust-threshold" style={{ color }}>
          Auto-execute ≤ ${current_threshold.toLocaleString()}
        </span>
      </div>
      <div className="trust-bar-bg">
        <div className="trust-bar-fill" style={{ width: `${pos}%`, background: color }} />
        <div className="trust-baseline-marker" style={{ left: `${((base_threshold - min_threshold) / range) * 100}%` }} title="Baseline $50K" />
      </div>
      <div className="trust-labels">
        <span>${(min_threshold/1000).toFixed(0)}K cautious</span>
        <span>${(max_threshold/1000).toFixed(0)}K trusted</span>
      </div>
      {total_decisions > 0 && (
        <div className="trust-stats">
          <span>{total_decisions} decisions · {good_outcomes}✓ {bad_outcomes}✗ · {accuracy_pct}% accuracy</span>
        </div>
      )}
      {total_decisions === 0 && (
        <div className="trust-stats">No past decisions yet — starting at baseline</div>
      )}
    </div>
  );
}

function PipelineTracker({ toolsCalled, activeStep, totalSteps, message, toolDetails = {} }) {
  return (
    <div className="pipeline-tracker">
      <div className="pipeline-header">
        <span className="pipeline-title">Agent Pipeline</span>
        <span className="pipeline-step">{activeStep}/{totalSteps}</span>
      </div>
      <div className="pipeline-progress-bar">
        <div className="pipeline-progress-fill" style={{ width: `${(activeStep / totalSteps) * 100}%` }} />
      </div>
      <div className="pipeline-tools">
        {TOOLS_ORDER.map((tool, i) => {
          const done = toolsCalled.includes(tool);
          const isActive = !done && message?.toLowerCase().includes(TOOL_LABELS[tool]?.split(" ")[0].toLowerCase());
          const isDebate = tool === "adversarial_deliberation";
          return (
            <div key={tool} className={`pipeline-tool ${done ? "done" : isActive ? "active" : ""} ${isDebate ? "debate-tool" : ""}`}>
              <div className="tool-dot">
                {done ? (isDebate ? "⚔" : "✓") : isActive ? <div className="tool-spinner" /> : i + 1}
              </div>
              <div className="tool-label-wrap">
                <span className="tool-label">{TOOL_LABELS[tool]}</span>
                {done && toolDetails[tool] && (
                  <span className="tool-detail">{toolDetails[tool]}</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {message && <div className="pipeline-msg">{message}</div>}
    </div>
  );
}

const SEV_BORDER = { CRITICAL: "#DC2626", HIGH: "#EA580C", MEDIUM: "#D97706", LOW: "#16A34A" };

// FEATURE 3: Uncertainty badge
function UncertaintyBadge({ uncertainty }) {
  if (!uncertainty) return null;
  const { uncertainty_level, known_gaps, recommendation_caveat } = uncertainty;
  const colors = { HIGH: "#DC2626", MEDIUM: "#D97706", LOW: "#16A34A" };
  const color = colors[uncertainty_level] || "#6B7280";
  return (
    <div className="uncertainty-badge" style={{ borderLeftColor: color }}>
      <div className="unc-header">
        <span className="unc-icon">⚠</span>
        <span className="unc-title">Agent Knowledge Gap</span>
        <span className="unc-level" style={{ background: color }}>{uncertainty_level} uncertainty</span>
      </div>
      {known_gaps?.length > 0 && (
        <ul className="unc-gaps">
          {known_gaps.map((g, i) => <li key={i}>{g}</li>)}
        </ul>
      )}
      <div className="unc-caveat">{recommendation_caveat}</div>
    </div>
  );
}

// FEATURE 1: Deliberation panel
function DeliberationPanel({ deliberation }) {
  const [open, setOpen] = useState(false);
  if (!deliberation?.arbiter_result) return null;
  const { advocate_argument, skeptic_argument, arbiter_result } = deliberation;
  const { strongest_advocate_point, strongest_skeptic_point, swing_condition,
          confidence, final_action, arbiter_recommendation } = arbiter_result;
  const confColor = { HIGH: "#16A34A", MEDIUM: "#D97706", LOW: "#DC2626" }[confidence] || "#6B7280";
  return (
    <div className="deliberation-panel">
      <div className="delib-header" onClick={() => setOpen(o => !o)} style={{ cursor: "pointer" }}>
        <span className="delib-title">⚔ Adversarial Deliberation</span>
        <div className="delib-badges">
          <span className="delib-action">{final_action?.replace(/_/g, " ")}</span>
          <span className="delib-conf" style={{ background: confColor }}>{confidence} confidence</span>
        </div>
        <span className="delib-toggle">{open ? "▲" : "▼"}</span>
      </div>
      {open && (
        <div className="delib-body">
          <div className="delib-arbiter">
            <div className="delib-arbiter-label">Arbiter Verdict</div>
            <div className="delib-arbiter-text">{arbiter_recommendation}</div>
            {swing_condition && (
              <div className="delib-swing">
                <strong>Would reconsider if:</strong> {swing_condition}
              </div>
            )}
          </div>
          <div className="delib-sides">
            <div className="delib-side advocate">
              <div className="side-label">✅ Advocate (strongest point)</div>
              <div className="side-text">{strongest_advocate_point}</div>
              <details>
                <summary>Full argument</summary>
                <pre className="side-full">{advocate_argument}</pre>
              </details>
            </div>
            <div className="delib-side skeptic">
              <div className="side-label">🚫 Skeptic (strongest point)</div>
              <div className="side-text">{strongest_skeptic_point}</div>
              <details>
                <summary>Full argument</summary>
                <pre className="side-full">{skeptic_argument}</pre>
              </details>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function RecommendationCard({ summary, exposure }) {
  if (!summary) return null;
  const { severity, confidence, recommended_action, recommended_supplier } = summary;
  const borderColor = SEV_BORDER[severity] || "#6B7280";
  return (
    <div className="recommendation-card" style={{ borderLeftColor: borderColor }}>
      <div className="rec-header">
        <span className="rec-title">Agent Recommendation</span>
        <div className="rec-badges">
          <span className="rec-severity-badge" style={{ background: borderColor }}>{severity}</span>
          <span className="rec-confidence">{confidence} confidence</span>
        </div>
      </div>
      <p className="rec-action-text">"{recommended_action}"</p>
      <div className="rec-meta">
        <span>Recommended supplier: <strong>{recommended_supplier}</strong></span>
        {exposure > 0 && <span>30-day exposure: <strong>${Math.round(exposure).toLocaleString()}</strong></span>}
      </div>
    </div>
  );
}

// FEATURE 2: Outcome feedback widget
function OutcomeFeedback({ pipelineResult, onFeedbackSent }) {
  const [outcome, setOutcome] = useState(null);
  const [note, setNote] = useState("");
  const [sent, setSent] = useState(false);

  const summary = pipelineResult?.pipeline_result?.structured_summary;
  if (!summary) return null;

  const submit = async () => {
    await fetch(`${API}/trust/outcome`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        event_id: pipelineResult?.pipeline_result?.event_id,
        sku: summary.sku,
        recommended_supplier: summary.recommended_supplier,
        cost_exposure: summary.cost_exposure,
        outcome,
        notes: note
      })
    });
    setSent(true);
    if (onFeedbackSent) onFeedbackSent();
  };

  if (sent) return (
    <div className="feedback-sent">✓ Outcome recorded — agent trust level updated</div>
  );

  return (
    <div className="outcome-feedback">
      <div className="feedback-title">📊 Rate this recommendation (updates agent trust)</div>
      <div className="feedback-options">
        {["good", "neutral", "bad"].map(o => (
          <button key={o} className={`feedback-btn ${outcome === o ? "selected" : ""} ${o}`}
            onClick={() => setOutcome(o)}>
            {o === "good" ? "✓ Good call" : o === "neutral" ? "~ Neutral" : "✗ Bad call"}
          </button>
        ))}
      </div>
      {outcome && (
        <>
          <input className="feedback-note" placeholder="Optional note..." value={note} onChange={e => setNote(e.target.value)} />
          <button className="feedback-submit" onClick={submit}>Submit Feedback</button>
        </>
      )}
    </div>
  );
}

function ApprovalGate({ result, exposure, onApprove, onReject, autoDetected, approvalThreshold, onFeedbackSent }) {
  const drafts = result?.pipeline_result?.drafts || {};
  const costAnalysis = result?.pipeline_result?.cost_analysis || {};
  const customerImpact = result?.pipeline_result?.customer_impact || {};
  const summary = result?.pipeline_result?.structured_summary || null;
  const deliberation = result?.pipeline_result?.deliberation || null;
  const uncertainty = result?.pipeline_result?.uncertainty || null;
  const rfqEmails = drafts.rfq_emails || [];

  const [approveSlack, setApproveSlack] = useState(!!drafts.slack);
  const [approvedRfqs, setApprovedRfqs] = useState(rfqEmails.map((_, i) => i));
  const toggleRfq = (i) =>
    setApprovedRfqs(prev => prev.includes(i) ? prev.filter(x => x !== i) : [...prev, i]);
  const noneSelected = !approveSlack && approvedRfqs.length === 0;

  return (
    <div className="approval-gate">
      <div className="approval-header">
        <div className="approval-icon">⚠</div>
        <div>
          <div className="approval-title">Human Approval Required</div>
          <div className="approval-sub">
            {autoDetected
              ? "Autonomously detected and analyzed by monitor"
              : `Exposure exceeds $${(approvalThreshold || 50000).toLocaleString()} trust threshold — select actions to execute`}
          </div>
        </div>
      </div>

      <RecommendationCard summary={summary} exposure={exposure} />

      {/* FEATURE 1: Deliberation panel */}
      <DeliberationPanel deliberation={deliberation} />

      {/* FEATURE 3: Uncertainty badge */}
      {uncertainty && uncertainty.uncertainty_level !== "LOW" && (
        <UncertaintyBadge uncertainty={uncertainty} />
      )}

      <div className="approval-stats">
        <div className="approval-stat">
          <div className="astat-val">${Math.round(exposure).toLocaleString()}</div>
          <div className="astat-lbl">30-day exposure</div>
        </div>
        <div className="approval-stat">
          <div className="astat-val">{customerImpact.total_orders_at_risk || 0}</div>
          <div className="astat-lbl">orders at risk</div>
        </div>
        <div className="approval-stat">
          <div className="astat-val">{rfqEmails.length}</div>
          <div className="astat-lbl">RFQs drafted</div>
        </div>
        <div className="approval-stat">
          <div className="astat-val">${Math.round(costAnalysis.cost_premium_per_unit || 0)}</div>
          <div className="astat-lbl">premium/unit</div>
        </div>
      </div>

      <div className="approval-actions-list">
        {rfqEmails.map((rfq, i) => (
          <label key={i} className="approval-check">
            <input type="checkbox" checked={approvedRfqs.includes(i)} onChange={() => toggleRfq(i)} />
            <div className="approval-check-body">
              <div className="approval-check-title">RFQ Email → {rfq.to || `Supplier ${i + 1}`}</div>
              <div className="approval-check-sub">{rfq.subject}</div>
              <pre className="draft-body">{rfq.body?.slice(0, 220)}...</pre>
            </div>
          </label>
        ))}
        {drafts.slack && (
          <label className="approval-check">
            <input type="checkbox" checked={approveSlack} onChange={e => setApproveSlack(e.target.checked)} />
            <div className="approval-check-body">
              <div className="approval-check-title">Slack Alert → #procurement-alerts</div>
              <pre className="slack-msg">{drafts.slack.message}</pre>
            </div>
          </label>
        )}
      </div>

      <div className="approval-actions">
        <button className="approve-btn" disabled={noneSelected}
          onClick={() => onApprove({ slack: approveSlack, rfq_emails: approvedRfqs })}>
          Execute Selected Actions
        </button>
        <button className="reject-btn" onClick={onReject}>Cancel All</button>
      </div>
    </div>
  );
}

// ── Main App ──────────────────────────────────────────────────────────────────

export default function App() {
  const [dashboard, setDashboard] = useState(null);
  const [phase, setPhase] = useState("pre-shock");
  const [activeStep, setActiveStep] = useState(0);
  const [toolsCalled, setToolsCalled] = useState([]);
  const [toolDetails, setToolDetails] = useState({});
  const [progressMsg, setProgressMsg] = useState("");
  const [pipelineResult, setPipelineResult] = useState(null);
  const [costExposure, setCostExposure] = useState(0);
  const [approvalThreshold, setApprovalThreshold] = useState(50000);
  const [approved, setApproved] = useState(null);
  const [autoDetected, setAutoDetected] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(null);
  const [trustStats, setTrustStats] = useState(null);
  const [simPhase, setSimPhase] = useState("healthy");
  const [liveDataPulse, setLiveDataPulse] = useState(false);
  const [liveInventory, setLiveInventory] = useState(null);
  const [livePrices, setLivePrices] = useState(null);
  const pollRef = useRef(null);
  const snapshotRef = useRef(null);
  const countdownRef = useRef(null);

  const fetchDashboard = () => {
    fetch(`${API}/dashboard`)
      .then(r => r.json())
      .then(data => {
        setDashboard(data);
        if (data.trust_stats) setTrustStats(data.trust_stats);
        if (data.sim_phase) setSimPhase(data.sim_phase);
      })
      .catch(() => {});
  };

  // Fast lightweight snapshot poll — updates inventory & prices every 3s
  const fetchSnapshot = () => {
    fetch(`${API}/realtime/snapshot`)
      .then(r => r.json())
      .then(data => {
        setSimPhase(data.sim_phase || "healthy");
        setLiveInventory(data.inventory || null);
        setLivePrices(data.price_feeds || null);
        // Pulse the live indicator on each successful tick
        setLiveDataPulse(true);
        setTimeout(() => setLiveDataPulse(false), 600);
      })
      .catch(() => {});
  };

  useEffect(() => {
    fetch(`${API}/demo/pre-shock`, { method: "POST" }).finally(() => {
      fetchDashboard();
      setPhase("pre-shock");
    });
    pollRef.current = setInterval(fetchDashboard, 8000);
    snapshotRef.current = setInterval(fetchSnapshot, SNAPSHOT_INTERVAL_MS);
    return () => {
      clearInterval(pollRef.current);
      clearInterval(snapshotRef.current);
    };
  }, []);

  const nextDelivery = dashboard?.next_delivery;

  useEffect(() => {
    if (!nextDelivery) return;
    setSecondsLeft(Math.round(nextDelivery.hours_remaining * 3600));
    countdownRef.current = setInterval(() => {
      setSecondsLeft(prev => (prev !== null && prev > 0 ? prev - 1 : 0));
    }, 1000);
    return () => clearInterval(countdownRef.current);
  }, [nextDelivery?.order_id]);

  const fmtCountdown = (s) => {
    if (s <= 0) return "0m 0s";
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    return h > 0 ? `${h}h ${m}m` : `${m}m ${sec}s`;
  };

  useEffect(() => {
    const monitorSse = new EventSource(`${API}/monitor/stream`);
    monitorSse.addEventListener("monitor_complete", (e) => {
      const data = JSON.parse(e.data);
      const result = data.result;
      if (!result?.success) return;
      setPipelineResult(result);
      setCostExposure(result.cost_exposure || 0);
      setApprovalThreshold(result.approval_threshold || 50000);
      if (result.trust_stats) setTrustStats(result.trust_stats);
      setToolsCalled(result.pipeline_result?.tools_called || TOOLS_ORDER);
      setActiveStep(10);
      setAutoDetected(true);
      // Don't hijack if user is already in approval or done
      setPhase(prev => {
        if (prev === "approval" || prev === "done") return prev;
        return result.needs_approval ? "approval" : "done";
      });
    });
    return () => monitorSse.close();
  }, []);

  const injectShock = async () => {
    await fetch(`${API}/demo/inject-shock`, { method: "POST" });
    await fetchDashboard();
    setPhase("monitor");
    triggerPipeline("SKU-4821");
  };

  const triggerPipeline = (sku = "SKU-4821") => {
    setPhase("running");
    setToolsCalled([]);
    setToolDetails({});
    setActiveStep(0);
    setProgressMsg("Starting autonomous pipeline...");
    setPipelineResult(null);

    const evtSource = new EventSource(`${API}/pipeline/stream/${sku}`);

    evtSource.addEventListener("status", (e) => {
      const data = JSON.parse(e.data);
      setActiveStep(data.step || 0);
      setProgressMsg(data.message || "");
      if (data.tool) setToolsCalled(prev => prev.includes(data.tool) ? prev : [...prev, data.tool]);
    });

    evtSource.addEventListener("tool_detail", (e) => {
      const data = JSON.parse(e.data);
      setToolDetails(prev => ({ ...prev, [data.tool]: data.detail }));
    });

    evtSource.addEventListener("complete", (e) => {
      evtSource.close();
      const data = JSON.parse(e.data);
      setPipelineResult(data);
      setCostExposure(data.cost_exposure || 0);
      setApprovalThreshold(data.approval_threshold || 50000);
      if (data.trust_stats) setTrustStats(data.trust_stats);
      setToolsCalled(TOOLS_ORDER);
      setPhase(data.needs_approval ? "approval" : "done");
    });

    evtSource.addEventListener("error", () => {
      evtSource.close();
      setProgressMsg("Connection error. Is backend running on port 8002?");
    });
  };

  const handleApprove = async (approvals) => {
    const res = await fetch(`${API}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        approvals,
        pipeline_result: pipelineResult?.pipeline_result || {}
      })
    });
    const result = await res.json();
    setApproved(result);
    setPhase("done");
  };

  const handleReject = () => { setApproved(false); setPhase("done"); };

  const reset = async () => {
    await fetch(`${API}/demo/pre-shock`, { method: "POST" });
    setPhase("pre-shock");
    setToolsCalled([]);
    setToolDetails({});
    setActiveStep(0);
    setPipelineResult(null);
    setApproved(null);
    setAutoDetected(false);
    fetchDashboard();
  };

  const alerts = dashboard?.low_stock_alerts || [];

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-logo">
          <div className="header-pulse" />
          <span className="header-brand">ChainPilot</span>
          <span className="header-version">v2</span>
        </div>
        <div className="header-tagline">Autonomous Supply Chain Monitor</div>
        {/* Real-time simulation phase badge */}
        <div className="sim-phase-badge" style={{ color: SIM_PHASE_CONFIG[simPhase]?.color || "#6B7280" }}>
          <span
            className="sim-live-dot"
            style={{
              background: SIM_PHASE_CONFIG[simPhase]?.color || "#6B7280",
              boxShadow: liveDataPulse ? `0 0 8px 3px ${SIM_PHASE_CONFIG[simPhase]?.color}66` : "none",
              transition: "box-shadow 0.3s ease",
            }}
          />
          {SIM_PHASE_CONFIG[simPhase]?.label || "Live"}
        </div>
        <div className={`monitor-status ${phase === "running" ? "active" : ""}`}>
          <div className="status-dot" />
          {phase === "running" ? "Pipeline running" : phase === "approval" ? "Awaiting approval" : "Monitoring"}
        </div>
        {phase !== "pre-shock" && (
          <button className="header-reset" onClick={reset}>Reset Demo</button>
        )}
      </header>

      <main className="app-main">
        <div className="main-grid">

          {/* LEFT — Dashboard feeds */}
          <div className="left-panel">
            {nextDelivery && secondsLeft !== null && secondsLeft > 0 && (
              <div className="countdown-banner">
                <div className="countdown-label">DELIVERY AT RISK</div>
                <div className="countdown-customer">{nextDelivery.customer} — {nextDelivery.order_id}</div>
                <div className="countdown-timer">{fmtCountdown(secondsLeft)}</div>
                <div className="countdown-sub">until {nextDelivery.delivery_date} · ${nextDelivery.penalty_per_day.toLocaleString()}/day penalty</div>
              </div>
            )}

            {/* FEATURE 2: Trust meter on left panel */}
            <TrustMeter stats={trustStats} />

            <div className="panel-section">
              <div className="section-label">
                Inventory Status
                {liveInventory && (
                  <span className="live-tag" style={{ marginLeft: 8, fontSize: "0.7rem", color: "#16A34A", fontWeight: 600 }}>
                    ● LIVE
                  </span>
                )}
              </div>
              {(liveInventory || dashboard?.inventory)?.map(item => {
                // Merge live stock_pct/hours into the dashboard name record
                const base = dashboard?.inventory?.find(d => d.sku === item.sku) || item;
                return (
                  <StockBar key={item.sku} pct={item.stock_pct}
                    name={base.name || item.sku} hours={item.hours_to_stockout} />
                );
              }) || <div className="loading">Loading...</div>}
            </div>

            <div className="panel-section">
              <div className="section-label">Supplier Status</div>
              {dashboard?.supplier_alerts?.length > 0
                ? dashboard.supplier_alerts.map((s, i) => (
                  <div key={i} className="supplier-alert">
                    <span className="supplier-name">{s.name}</span>
                    <span className="supplier-delay">{s.delay_days}d delay — {s.reason}</span>
                  </div>
                ))
                : <div className="all-clear">All suppliers active</div>
              }
            </div>

            <div className="panel-section">
              <div className="section-label">
                Price Feeds
                {livePrices && (
                  <span className="live-tag" style={{ marginLeft: 8, fontSize: "0.7rem", color: "#16A34A", fontWeight: 600 }}>
                    ● LIVE
                  </span>
                )}
              </div>
              {(livePrices || dashboard?.price_feeds)?.map(p => (
                <div key={p.sku} className="price-row">
                  <span className="price-sku">{p.sku}</span>
                  {p.current_price != null && (
                    <span style={{ color: "#6B7280", fontSize: "0.8rem", marginRight: 6 }}>
                      ${p.current_price}
                    </span>
                  )}
                  <span className="price-change" style={{ color: p.change_pct > 10 ? "#DC2626" : "#16A34A" }}>
                    {p.change_pct > 0 ? "+" : ""}{p.change_pct}%
                  </span>
                </div>
              ))}
            </div>

            {phase === "pre-shock" && (
              <button className="inject-shock-btn" onClick={injectShock}>
                ⚡ Inject Supply Chain Shock
              </button>
            )}

            {alerts.length > 0 && phase === "monitor" && (
              <div className="alert-banner">
                <div className="alert-title">🚨 {alerts.length} alert(s) detected</div>
                {alerts.map((a, i) => (
                  <div key={i} className="alert-item">
                    <span>{a.name}</span>
                    <button className="trigger-btn" onClick={() => triggerPipeline(a.sku)}>
                      Trigger Pipeline →
                    </button>
                  </div>
                ))}
              </div>
            )}

            {phase === "monitor" && alerts.length === 0 && (
              <button className="demo-trigger-btn" onClick={() => triggerPipeline("SKU-4821")}>
                Simulate Disruption (SKU-4821) →
              </button>
            )}
          </div>

          {/* RIGHT — Pipeline / Approval / Done */}
          <div className="right-panel">
            {(phase === "pre-shock" || phase === "monitor") && (
              <div className="idle-state">
                <div className="idle-icon">◉</div>
                <div className="idle-title">
                  {phase === "pre-shock" ? "Supply chain nominal" : "Agent is watching"}
                </div>
                <div className="idle-sub">
                  {phase === "pre-shock"
                    ? "All systems healthy — inject a shock to trigger the agent"
                    : "Trigger a disruption to see the autonomous pipeline execute"}
                </div>
              </div>
            )}

            {(phase === "running" || (phase !== "monitor" && toolsCalled.length > 0)) && (
              <PipelineTracker
                toolsCalled={toolsCalled}
                activeStep={activeStep}
                totalSteps={10}
                message={progressMsg}
                toolDetails={toolDetails}
              />
            )}

            {phase === "approval" && pipelineResult && (
              <ApprovalGate
                result={pipelineResult}
                exposure={costExposure}
                approvalThreshold={approvalThreshold}
                onApprove={handleApprove}
                onReject={handleReject}
                autoDetected={autoDetected}
                onFeedbackSent={fetchDashboard}
              />
            )}

            {phase === "done" && (
              <div className="done-state">
                <div className="done-icon">{approved === false ? "✕" : "✓"}</div>
                <div className="done-title">
                  {approved === false ? "Actions cancelled" : "Selected actions executed"}
                </div>
                <div className="done-sub">
                  {approved === false
                    ? "No actions taken."
                    : `${approved?.emails?.filter(e => e.sent).length || 0} RFQ(s) sent · ${approved?.slack?.sent ? "Slack posted" : "Slack skipped"} · Audit logged`}
                </div>
                <RecommendationCard
                  summary={pipelineResult?.pipeline_result?.structured_summary}
                  exposure={costExposure}
                />
                <DeliberationPanel deliberation={pipelineResult?.pipeline_result?.deliberation} />
                <div className="done-stats">
                  <div className="done-stat">
                    <div className="ds-val">{toolsCalled.length}</div>
                    <div className="ds-lbl">tools called</div>
                  </div>
                  <div className="done-stat">
                    <div className="ds-val">${Math.round(costExposure).toLocaleString()}</div>
                    <div className="ds-lbl">exposure managed</div>
                  </div>
                  <div className="done-stat">
                    <div className="ds-val">{approved?.emails?.filter(e => e.sent).length || 0}</div>
                    <div className="ds-lbl">RFQs sent</div>
                  </div>
                </div>

                {/* FEATURE 2: Outcome feedback */}
                {approved && approved !== false && (
                  <OutcomeFeedback pipelineResult={pipelineResult} onFeedbackSent={fetchDashboard} />
                )}
              </div>
            )}

            {phase === "done" && approved && approved !== false && (
              (approved?.emails?.some(e => e.sent) || approved?.slack?.sent) && (
                <div className="sent-outbox">
                  <div className="outbox-title">Sent Items</div>
                  {approved?.emails?.map((email, i) => (
                    <div key={i} className="outbox-item">
                      <div className="outbox-item-header">
                        <span className={`outbox-badge ${email.sent ? "email-badge" : "failed-badge"}`}>
                          {email.sent ? "EMAIL" : "FAILED"}
                        </span>
                        <span className="outbox-to">→ {email.to}</span>
                        {email.demo && <span className="outbox-demo">DEMO MODE</span>}
                      </div>
                      {email.error && <div className="outbox-error">{email.error}</div>}
                      {email.note && <div className="outbox-note">{email.note}</div>}
                      <div className="outbox-subject">{email.subject}</div>
                      <pre className="outbox-body">{email.body}</pre>
                    </div>
                  ))}
                  {approved?.slack && (
                    <div className="outbox-item">
                      <div className="outbox-item-header">
                        <span className={`outbox-badge ${approved.slack.sent ? "slack-badge" : "failed-badge"}`}>
                          {approved.slack.sent ? "SLACK" : "SLACK FAILED"}
                        </span>
                        <span className="outbox-to">→ {approved.slack.channel || "#procurement-alerts"}</span>
                        {approved.slack.demo && <span className="outbox-demo">DEMO MODE</span>}
                      </div>
                      {approved.slack.error && <div className="outbox-error">{approved.slack.error}</div>}
                      {approved.slack.note && <div className="outbox-note">{approved.slack.note}</div>}
                      <pre className="outbox-body">{approved.slack.message}</pre>
                    </div>
                  )}
                </div>
              )
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
