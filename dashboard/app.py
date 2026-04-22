"""
fastapi-alertengine · Observability Dashboard
──────────────────────────────────────────────
A Datadog-lite, production-grade Streamlit app.

Run:
    streamlit run dashboard/app.py

Environment variables:
    ALERTENGINE_BASE_URL   — backend base URL  (default: http://localhost:8000)
    ALERTENGINE_SERVICE    — default service name (default: default)
    ALERTENGINE_REFRESH_S  — auto-refresh interval in seconds (default: 10)
"""

import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

# ── Configuration ─────────────────────────────────────────────────────────────

BASE_URL = os.getenv("ALERTENGINE_BASE_URL", "http://localhost:8000").rstrip("/")
REFRESH_S = int(os.getenv("ALERTENGINE_REFRESH_S", "10"))
DEFAULT_SERVICE = os.getenv("ALERTENGINE_SERVICE", "default")
MAX_QUEUE_SIZE = 10_000

TIME_RANGES: Dict[str, int] = {
    "5 min":    5,
    "15 min":   15,
    "1 hour":   60,
    "6 hours":  360,
    "24 hours": 1440,
}

# ── Incident thresholds (heuristic constants) ─────────────────────────────────
# Used in build_incident_timeline, build_root_cause, and generate_insights.
_LATENCY_WARN_MS: float = 500.0    # warning threshold for P95 latency
_LATENCY_CRIT_MS: float = 1000.0   # critical threshold for P95 latency
_ERR_WARN: float = 0.05            # 5% — warning threshold for error rate
_ERR_CRIT: float = 0.10            # 10% — critical threshold for error rate
_TRAFFIC_CHANGE_PCT: float = 30.0  # ±30% req/min swing triggers TRAFFIC_CHANGE

# ── Page setup ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="AlertEngine · Observability",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global CSS ────────────────────────────────────────────────────────────────

st.markdown(
    """
<style>
/* ─── global tone ─── */
[data-testid="stAppViewContainer"] { background: #0d1117; }
[data-testid="stSidebar"]          { background: #161b22; border-right: 1px solid #21262d; }
[data-testid="stSidebar"] h3       { color: #58a6ff; }

/* ─── metric cards ─── */
.ae-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 1.1rem 1.3rem 1rem;
    margin-bottom: 0.1rem;
    min-height: 100px;
}
.ae-label {
    font-size: 0.72rem;
    font-weight: 600;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    margin-bottom: 0.35rem;
}
.ae-value {
    font-size: 2rem;
    font-weight: 700;
    line-height: 1.1;
}

/* ─── status colours ─── */
.c-ok       { color: #3fb950; }
.c-warning  { color: #e3b341; }
.c-critical { color: #f85149; }
.c-blue     { color: #58a6ff; }
.c-muted    { color: #8b949e; }

/* ─── section headers ─── */
.ae-section {
    font-size: 0.72rem;
    font-weight: 700;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    padding-bottom: 0.35rem;
    border-bottom: 1px solid #21262d;
    margin-bottom: 0.9rem;
    margin-top: 0.2rem;
}

/* ─── alert rows ─── */
.ae-alert {
    border-radius: 6px;
    padding: 0.65rem 1rem;
    margin-bottom: 0.45rem;
    background: #0d1117;
}
.ae-alert-ok       { border-left: 4px solid #3fb950; }
.ae-alert-warning  { border-left: 4px solid #e3b341; }
.ae-alert-critical { border-left: 4px solid #f85149; }
.ae-alert-unknown  { border-left: 4px solid #8b949e; }

.ae-alert-title { font-weight: 700; font-size: 0.95rem; }
.ae-alert-meta  { color: #8b949e; font-size: 0.78rem; margin-top: 0.15rem; }
.ae-alert-body  { color: #c9d1d9; font-size: 0.85rem; margin-top: 0.25rem; }

/* ─── system intelligence ─── */
.ae-intelligence-box {
    background: #0f1923;
    border: 1px solid #1f4068;
    border-radius: 10px;
    padding: 1rem 1.3rem;
    margin-bottom: 0.8rem;
}
.ae-insight-item {
    color: #c9d1d9;
    font-size: 0.88rem;
    padding: 0.25rem 0;
    line-height: 1.5;
}

/* ─── system summary card ─── */
.ae-summary-card {
    background: linear-gradient(135deg, #161b22 0%, #0f1923 100%);
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 1rem 1.5rem;
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
    gap: 1.2rem;
    flex-wrap: wrap;
}
.ae-summary-status {
    font-size: 1.45rem;
    font-weight: 700;
    line-height: 1.1;
}
.ae-summary-meta {
    color: #8b949e;
    font-size: 0.80rem;
    margin-top: 0.2rem;
}
.ae-summary-pill {
    display: inline-block;
    background: #21262d;
    border-radius: 999px;
    padding: 0.15rem 0.7rem;
    font-size: 0.78rem;
    margin-right: 0.4rem;
    color: #c9d1d9;
}

/* ─── active incident card ─── */
.ae-incident-card {
    border-radius: 10px;
    padding: 1rem 1.3rem;
    margin-bottom: 0.6rem;
    background: #0d1117;
}
.ae-incident-ok       { border-left: 5px solid #3fb950; }
.ae-incident-warning  { border-left: 5px solid #e3b341; }
.ae-incident-critical { border-left: 5px solid #f85149; }
.ae-incident-unknown  { border-left: 5px solid #8b949e; }
.ae-incident-title    { font-weight: 700; font-size: 1.0rem; }
.ae-incident-service  { color: #58a6ff; font-size: 0.82rem; margin-top: 0.2rem; }
.ae-incident-meta     { color: #8b949e; font-size: 0.78rem; margin-top: 0.15rem; }
.ae-incident-body     { color: #c9d1d9; font-size: 0.85rem; margin-top: 0.4rem; line-height: 1.6; }

/* ─── top signals ─── */
.ae-signal-item {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 8px;
    padding: 0.55rem 1rem;
    margin-bottom: 0.35rem;
    font-size: 0.85rem;
    color: #c9d1d9;
}
.ae-signal-path { font-weight: 600; color: #58a6ff; }

/* ─── delta / what changed ─── */
.ae-delta-box {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 8px;
    padding: 0.65rem 1.1rem;
    margin-bottom: 0.8rem;
    font-size: 0.85rem;
    color: #c9d1d9;
}
.ae-delta-up   { color: #f85149; }
.ae-delta-down { color: #3fb950; }
.ae-delta-flat { color: #8b949e; }

/* ─── action hints ─── */
.ae-hint-box {
    background: #0f1923;
    border: 1px solid #1f4068;
    border-left: 4px solid #58a6ff;
    border-radius: 8px;
    padding: 0.6rem 1rem;
    margin-bottom: 0.6rem;
    font-size: 0.84rem;
    color: #c9d1d9;
}

/* ─── incident timeline ─── */
.ae-timeline-event {
    border-left: 3px solid #30363d;
    padding: 0.4rem 0.8rem;
    margin-bottom: 0.4rem;
    background: #0d1117;
    border-radius: 0 6px 6px 0;
}
.ae-timeline-ts {
    font-size: 0.74rem;
    color: #8b949e;
    font-family: monospace;
    margin-right: 0.5rem;
}
.ae-timeline-type {
    font-size: 0.71rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-right: 0.4rem;
}
.ae-timeline-msg {
    font-size: 0.85rem;
    color: #c9d1d9;
    margin-top: 0.1rem;
}
.ae-timeline-empty {
    color: #8b949e;
    font-size: 0.85rem;
    padding: 0.4rem 0;
}

/* ─── root cause card ─── */
.ae-rootcause-box {
    background: #12181f;
    border: 1px solid #30363d;
    border-left: 4px solid #e3b341;
    border-radius: 8px;
    padding: 0.9rem 1.1rem;
    font-size: 0.85rem;
    margin-bottom: 0.6rem;
}
.ae-rootcause-label {
    font-size: 0.70rem;
    font-weight: 700;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    margin-bottom: 0.3rem;
}
.ae-rootcause-value {
    font-size: 0.95rem;
    font-weight: 600;
    color: #c9d1d9;
    margin-bottom: 0.5rem;
    line-height: 1.4;
}
.ae-rootcause-meta {
    color: #8b949e;
    font-size: 0.80rem;
    line-height: 1.7;
}
</style>
""",
    unsafe_allow_html=True,
)

# ── Data fetching ─────────────────────────────────────────────────────────────


@st.cache_data(ttl=REFRESH_S)
def fetch_health() -> Optional[Dict[str, Any]]:
    try:
        r = requests.get(f"{BASE_URL}/health/alerts", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=REFRESH_S)
def fetch_metrics(service: str, last_n_buckets: int) -> List[Dict[str, Any]]:
    try:
        r = requests.get(
            f"{BASE_URL}/metrics/history",
            params={"service": service, "last_n_buckets": last_n_buckets},
            timeout=5,
        )
        r.raise_for_status()
        return r.json().get("metrics", [])
    except Exception:
        return []


@st.cache_data(ttl=REFRESH_S)
def fetch_ingestion() -> Optional[Dict[str, Any]]:
    try:
        r = requests.get(f"{BASE_URL}/metrics/ingestion", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None



@st.cache_data(ttl=REFRESH_S)
def fetch_timeline(service: str, since: float = 0.0) -> list:
    """Fetch real incident events from the backend append-only timeline."""
    try:
        r = requests.get(
            f"{BASE_URL}/incidents/timeline",
            params={"service": service, "since": since, "limit": 50},
            timeout=5,
        )
        r.raise_for_status()
        return r.json().get("events", [])
    except Exception:
        return []


@st.cache_data(ttl=REFRESH_S)
def fetch_engine_status() -> Optional[Dict[str, Any]]:
    try:
        r = requests.get(f"{BASE_URL}/__alertengine/status", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────


def compute_health_score(p95_ms: float, error_rate: float) -> int:
    """Produce a 0-100 score; 100 = perfect."""
    score = 100
    if p95_ms > 3000:
        score -= 50
    elif p95_ms > 1000:
        score -= 25
    elif p95_ms > 500:
        score -= 10
    score -= int(error_rate * 200)  # 50% error rate ≡ -100
    return max(0, min(100, score))


def status_emoji(status: str) -> str:
    return {"ok": "✅", "warning": "⚠️", "critical": "🔴"}.get(status, "❓")


def status_css(status: str) -> str:
    return {"ok": "c-ok", "warning": "c-warning", "critical": "c-critical"}.get(
        status, "c-muted"
    )


def fmt_ts(ts: Optional[float]) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def card(label: str, value: str, color_class: str = "c-blue") -> str:
    return (
        f'<div class="ae-card">'
        f'<div class="ae-label">{label}</div>'
        f'<div class="ae-value {color_class}">{value}</div>'
        f"</div>"
    )


def build_timeseries_df(metrics: List[Dict[str, Any]]) -> pd.DataFrame:
    if not metrics:
        return pd.DataFrame()
    df = pd.DataFrame(metrics)
    df["ts"] = pd.to_datetime(df["bucket_ts"], unit="s")
    df["is_error"] = df["status_group"].isin(["4xx", "5xx"]).astype(int)
    df["error_count"] = df["count"] * df["is_error"]
    df["weighted_lat"] = df["avg_latency_ms"] * df["count"]
    agg = (
        df.groupby("ts")
        .agg(
            total_requests=("count", "sum"),
            error_requests=("error_count", "sum"),
            weighted_lat=("weighted_lat", "sum"),
            total_count=("count", "sum"),
            max_latency_ms=("max_latency_ms", "max"),
        )
        .reset_index()
    )
    safe_total = agg["total_count"].replace(0, 1)
    agg["avg_latency_ms"] = (agg["weighted_lat"] / safe_total).round(2)
    agg["error_rate_pct"] = (agg["error_requests"] / safe_total * 100).round(2)
    return agg.sort_values("ts").reset_index(drop=True)


def build_endpoint_df(metrics: List[Dict[str, Any]]) -> pd.DataFrame:
    if not metrics:
        return pd.DataFrame()
    df = pd.DataFrame(metrics)
    df["is_error"] = df["status_group"].isin(["4xx", "5xx"]).astype(int)
    df["error_count"] = df["count"] * df["is_error"]
    df["weighted_lat"] = df["avg_latency_ms"] * df["count"]
    grp = (
        df.groupby(["path", "method"])
        .agg(
            request_count=("count", "sum"),
            error_count=("error_count", "sum"),
            weighted_lat=("weighted_lat", "sum"),
            total_count=("count", "sum"),
            max_latency_ms=("max_latency_ms", "max"),
        )
        .reset_index()
    )
    safe_total = grp["total_count"].replace(0, 1)
    grp["avg_latency_ms"] = (grp["weighted_lat"] / safe_total).round(1)
    grp["max_latency_ms"] = grp["max_latency_ms"].round(1)
    grp["error_rate_pct"] = (grp["error_count"] / safe_total * 100).round(1)
    grp["impact_score"] = (grp["request_count"] * grp["avg_latency_ms"]).astype(int)
    return (
        grp[["path", "method", "request_count", "avg_latency_ms", "max_latency_ms",
             "error_rate_pct", "impact_score"]]
        .sort_values("impact_score", ascending=False)
        .reset_index(drop=True)
    )


def generate_insights(p95: float, error_rate: float, ep_df: pd.DataFrame) -> List[str]:
    """Translate raw metrics into human-readable operational insights."""
    insights: List[str] = []

    if p95 > 2000:
        insights.append("⚠️ System latency is degrading across multiple endpoints")
    elif p95 > 1000:
        insights.append("⚡ Latency is elevated — approaching warning threshold")

    if error_rate > 0.05:
        insights.append("⚠️ Error rate is above normal operating baseline")
    elif error_rate > 0.02:
        insights.append("🔶 Error rate is slightly elevated — worth monitoring")

    if ep_df is not None and not ep_df.empty:
        top = ep_df.iloc[0]
        insights.append(
            f"🔥 Primary hotspot: <code>{top['path']}</code> "
            f"({top['avg_latency_ms']:.0f} ms avg latency, impact score {top['impact_score']})"
        )

    if not insights:
        insights.append("✅ System operating within normal thresholds")

    return insights


def _action_hint(ep_df: pd.DataFrame, p95: float, error_rate: float) -> str:
    """Return a single actionable investigation hint."""
    if ep_df is not None and not ep_df.empty:
        if p95 > _LATENCY_CRIT_MS:
            top = ep_df.iloc[0]
            return f"👉 Investigate <code>{top['path']}</code> for performance degradation"
        high_err = ep_df[ep_df["error_rate_pct"] > _ERR_WARN * 100]
        if not high_err.empty:
            worst = high_err.iloc[0]
            return f"👉 Check <code>{worst['path']}</code> — {worst['error_rate_pct']:.1f}% error rate"
    return "✅ System healthy — no immediate action required"


def build_incident_timeline(
    ts_df: pd.DataFrame,
    ep_df: pd.DataFrame,
    health: Optional[Dict],
) -> List[Dict[str, Any]]:
    """
    Reconstruct a causal sequence of system degradation events from metrics.

    Each returned dict is a TimelineEvent with keys:
        timestamp, event_type, severity, message

    Events are ordered chronologically; ALERT_TRIGGERED is always last.
    """
    if health is None:
        return []

    h_met = health.get("metrics", {})
    p95 = float(h_met.get("overall_p95_ms", 0.0))
    error_rate = float(h_met.get("error_rate", 0.0))
    error_rate_pct = error_rate * 100
    status = health.get("status", "unknown")

    # Derive a base timestamp for simulated ordering.
    base_ts: Optional[Any] = None
    if not ts_df.empty and "ts" in ts_df.columns:
        base_ts = ts_df["ts"].iloc[-1]

    def _ts_label(offset_min: int) -> str:
        if base_ts is not None:
            t = base_ts - pd.Timedelta(minutes=offset_min)
            return t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)
        return f"T-{offset_min}m" if offset_min > 0 else "now"

    events: List[Dict[str, Any]] = []

    # ── TRAFFIC_CHANGE (earliest signal) ──────────────────────────────────────
    if not ts_df.empty and len(ts_df) > 1:
        latest = ts_df.iloc[-1]
        prev = ts_df.iloc[-2]
        prev_req = float(prev["total_requests"]) if float(prev["total_requests"]) != 0 else 1.0
        delta_pct = (float(latest["total_requests"]) - prev_req) / prev_req * 100
        if abs(delta_pct) > _TRAFFIC_CHANGE_PCT:
            events.append(
                {
                    "timestamp": _ts_label(4),
                    "event_type": "TRAFFIC_CHANGE",
                    "severity": "info",
                    "message": f"Traffic change detected: {delta_pct:+.1f}%",
                }
            )

    # ── LATENCY_SPIKE ─────────────────────────────────────────────────────────
    if p95 > _LATENCY_CRIT_MS:
        events.append(
            {
                "timestamp": _ts_label(3),
                "event_type": "LATENCY_SPIKE",
                "severity": "critical",
                "message": f"P95 latency exceeded {p95:.0f}ms",
            }
        )
    elif p95 > _LATENCY_WARN_MS:
        events.append(
            {
                "timestamp": _ts_label(3),
                "event_type": "LATENCY_SPIKE",
                "severity": "warning",
                "message": f"P95 latency exceeded {p95:.0f}ms",
            }
        )

    # ── ERROR_SPIKE ───────────────────────────────────────────────────────────
    if error_rate_pct > _ERR_CRIT * 100:
        events.append(
            {
                "timestamp": _ts_label(2),
                "event_type": "ERROR_SPIKE",
                "severity": "critical",
                "message": f"Error rate spiked to {error_rate_pct:.1f}%",
            }
        )
    elif error_rate_pct > _ERR_WARN * 100:
        events.append(
            {
                "timestamp": _ts_label(2),
                "event_type": "ERROR_SPIKE",
                "severity": "warning",
                "message": f"Error rate spiked to {error_rate_pct:.1f}%",
            }
        )

    # ── ENDPOINT_DEGRADATION (top-25% by impact_score, up to 3) ──────────────
    if ep_df is not None and not ep_df.empty:
        p75 = ep_df["impact_score"].quantile(0.75)
        top_degraded = ep_df[ep_df["impact_score"] >= p75].head(3)
        for _, row in top_degraded.iterrows():
            sev = (
                "critical"
                if row["error_rate_pct"] > 10 or row["avg_latency_ms"] > 1000
                else "warning"
            )
            events.append(
                {
                    "timestamp": _ts_label(1),
                    "event_type": "ENDPOINT_DEGRADATION",
                    "severity": sev,
                    "message": (
                        f"Endpoint {row['path']} showing degradation "
                        f"(latency {row['avg_latency_ms']:.0f}ms, "
                        f"error {row['error_rate_pct']:.1f}%)"
                    ),
                }
            )

    # ── ALERT_TRIGGERED (always last, only when alerting) ────────────────────
    if status in ("warning", "critical"):
        events.append(
            {
                "timestamp": _ts_label(0),
                "event_type": "ALERT_TRIGGERED",
                "severity": "critical",
                "message": f"Alert triggered: system entered {status} state",
            }
        )

    return events


def build_root_cause(
    ep_df: pd.DataFrame,
    health: Optional[Dict],
) -> Dict[str, str]:
    """
    Apply heuristic rules to identify the primary root cause hypothesis.

    Rules are evaluated in the following order (first match wins):
        1. Combined failure  — error_rate >= 5% AND p95 >= 1000ms → "Very High"
        2. Error dominant    — error_rate >= 5%                    → "High"
        3. Latency dominant  — p95 >= 1000ms AND error_rate < 5%  → "High"
        4. Default / stable  — no threshold breached               → "Low"

    The combined rule is checked before the error-only rule because it is a
    strict superset and produces a more accurate classification.

    Returns a dict with keys: root_cause, service, signal, confidence.
    """
    if health is None:
        return {
            "root_cause": "No data — backend unreachable",
            "service": "unknown",
            "signal": "None",
            "confidence": "None",
        }

    h_met = health.get("metrics", {})
    p95 = float(h_met.get("overall_p95_ms", 0.0))
    error_rate = float(h_met.get("error_rate", 0.0))
    error_rate_pct = error_rate * 100
    svc = health.get("service_name", "unknown")

    has_errors = error_rate >= _ERR_WARN
    has_latency = p95 >= _LATENCY_CRIT_MS

    def _top_by(col: str) -> Optional[Any]:
        if ep_df is not None and not ep_df.empty:
            return ep_df.sort_values(col, ascending=False).iloc[0]
        return None

    # Rule 3 — Combined failure (checked first: most specific)
    if has_errors and has_latency:
        row = _top_by("impact_score")
        endpoint = row["path"] if row is not None else "unknown endpoint"
        return {
            "root_cause": (
                f"{endpoint} contributing to combined latency and error degradation"
            ),
            "service": svc,
            "signal": "Multi-factor failure (latency + errors)",
            "confidence": "Very High",
        }

    # Rule 1 — Error dominant
    if has_errors:
        row = _top_by("error_rate_pct")
        if row is not None:
            endpoint, rate = row["path"], float(row["error_rate_pct"])
        else:
            endpoint, rate = "unknown endpoint", error_rate_pct
        return {
            "root_cause": f"{endpoint} showing elevated error rate ({rate:.1f}%)",
            "service": svc,
            "signal": "Error rate breach",
            "confidence": "High",
        }

    # Rule 2 — Latency dominant
    if has_latency:
        row = _top_by("avg_latency_ms")
        if row is not None:
            endpoint, ms = row["path"], float(row["avg_latency_ms"])
        else:
            endpoint, ms = "unknown endpoint", p95
        return {
            "root_cause": f"{endpoint} exhibiting high latency ({ms:.0f}ms)",
            "service": svc,
            "signal": "Latency degradation",
            "confidence": "High",
        }

    # Rule 4 — Default / stable
    return {
        "root_cause": "No dominant failing endpoint detected",
        "service": svc,
        "signal": "System stable or noise-level variance",
        "confidence": "Low",
    }


# ── Chart theme ───────────────────────────────────────────────────────────────

_CHART_BASE = dict(
    paper_bgcolor="#0d1117",
    plot_bgcolor="#0d1117",
    font=dict(color="#c9d1d9", size=11),
    margin=dict(l=8, r=8, t=32, b=8),
    height=210,
    xaxis=dict(gridcolor="#21262d", showgrid=True, zeroline=False, showline=False),
    yaxis=dict(gridcolor="#21262d", showgrid=True, zeroline=False, showline=False),
    showlegend=False,
    hovermode="x unified",
)


def _chart_title(text: str) -> dict:
    return dict(text=text, font=dict(size=12, color="#8b949e"), x=0, xanchor="left", pad=dict(l=4))


def empty_chart(title: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**_CHART_BASE, title=_chart_title(title))
    fig.add_annotation(
        text="No data available",
        xref="paper", yref="paper", x=0.5, y=0.5,
        showarrow=False, font=dict(color="#8b949e", size=13),
    )
    return fig


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### ⚡ AlertEngine")
    st.caption("Observability Dashboard")
    st.markdown("---")

    service = st.text_input(
        "Service", value=DEFAULT_SERVICE,
        help="Service name tag (matches ALERTENGINE_SERVICE on the backend)",
    )
    time_range_label = st.selectbox(
        "Time Range", list(TIME_RANGES.keys()), index=1,
    )
    last_n_buckets = TIME_RANGES[time_range_label]
    st.markdown("---")
    auto_refresh = st.toggle("Auto-refresh", value=True)
    if auto_refresh:
        st.caption(f"Refreshing every {REFRESH_S} s")

    if st.button("🔄  Refresh now", use_container_width=True):
        st.cache_data.clear()

    st.markdown("---")
    st.markdown(f"**Backend**  \n`{BASE_URL}`")


# ── Fetch data ────────────────────────────────────────────────────────────────

health = fetch_health()
metrics = fetch_metrics(service, last_n_buckets)
ingestion = fetch_ingestion()
engine_status = fetch_engine_status()

ts_df = build_timeseries_df(metrics)
ep_df = build_endpoint_df(metrics)

# Derived values
if health:
    h_status = health.get("status", "unknown")
    h_met = health.get("metrics", {})
    h_p95 = float(h_met.get("overall_p95_ms", 0.0))
    h_err = float(h_met.get("error_rate", 0.0))
    h_n = int(h_met.get("sample_size", 0))
    h_ts = health.get("timestamp")
    h_anomaly = float(h_met.get("anomaly_score", 0.0))
    h_svc = health.get("service_name", service)
    h_inst = health.get("instance_id", "default")
else:
    h_status = "unknown"
    h_p95 = h_err = h_anomaly = 0.0
    h_n = 0
    h_ts = h_svc = h_inst = None

rpm = int(ts_df["total_requests"].iloc[-1]) if not ts_df.empty else 0
h_score = compute_health_score(h_p95, h_err)

# Engine status extras (onboarding / demo mode)
demo_mode = bool((engine_status or {}).get("demo_mode", False))
engine_mode = (engine_status or {}).get("mode", "unknown")
actions_enabled = bool((engine_status or {}).get("actions_enabled", False))

if not health and not metrics and not ingestion:
    st.error(
        "⚠️  Backend unreachable — verify `ALERTENGINE_BASE_URL` and that the server is running."
    )

err_pct = h_err * 100

# ─────────────────────────────────────────────────────────────────────────────
# TITLE BAR  +  SYSTEM SUMMARY (below title)
# ─────────────────────────────────────────────────────────────────────────────

col_title, col_ts = st.columns([5, 1])
with col_title:
    st.markdown(f"## ⚡  AlertEngine  ·  `{service}`")
with col_ts:
    st.markdown(
        f'<div style="text-align:right;color:#8b949e;font-size:0.8rem;padding-top:1.2rem">'
        f'Updated {datetime.now().strftime("%H:%M:%S")}</div>',
        unsafe_allow_html=True,
    )

# ── System Summary card ───────────────────────────────────────────────────────
_demo_badge = (
    '<span class="ae-summary-pill" style="background:#1f4068;color:#58a6ff">🧪 Demo data</span>'
    if demo_mode else ""
)
_mode_badge = f'<span class="ae-summary-pill">{engine_mode} mode</span>'
_svc_badge  = f'<span class="ae-summary-pill">service: {h_svc or service}</span>'
_action_badge = (
    '<span class="ae-summary-pill" style="background:#1a2f1a;color:#3fb950">actions ON</span>'
    if actions_enabled else ""
)

_summary_status_map = {
    "ok":       ("✅ System Normal",   "c-ok"),
    "warning":  ("⚠️ Degraded",        "c-warning"),
    "critical": ("🔴 Critical",        "c-critical"),
    "unknown":  ("❓ Unknown",         "c-muted"),
}
_sum_label, _sum_cls = _summary_status_map.get(h_status, ("❓ Unknown", "c-muted"))

st.markdown(
    f'<div class="ae-summary-card">'
    f'<div>'
    f'  <div class="ae-summary-status {_sum_cls}">{_sum_label}</div>'
    f'  <div class="ae-summary-meta">'
    f'    P95 {h_p95:.0f} ms &nbsp;·&nbsp; Error rate {err_pct:.1f}%'
    f'    &nbsp;·&nbsp; Health {h_score}/100 &nbsp;·&nbsp; {h_n} samples'
    f'  </div>'
    f'</div>'
    f'<div style="margin-left:auto;display:flex;flex-wrap:wrap;gap:0.3rem;align-items:center">'
    f'  {_mode_badge}{_svc_badge}{_demo_badge}{_action_badge}'
    f'</div>'
    f'</div>',
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 · SYSTEM INTELLIGENCE  +  ACTION HINTS
# ─────────────────────────────────────────────────────────────────────────────

st.markdown('<div class="ae-section">System Intelligence</div>', unsafe_allow_html=True)

intel_col, hint_col = st.columns([3, 2])

with intel_col:
    insights = generate_insights(h_p95, h_err, ep_df)
    items_html = "".join(
        f'<div class="ae-insight-item">• {item}</div>' for item in insights
    )
    st.markdown(
        f'<div class="ae-intelligence-box">{items_html}</div>',
        unsafe_allow_html=True,
    )

with hint_col:
    st.markdown('<div class="ae-section">What Should I Look At?</div>', unsafe_allow_html=True)
    hint_text = _action_hint(ep_df, h_p95, h_err)
    st.markdown(
        f'<div class="ae-hint-box">{hint_text}</div>',
        unsafe_allow_html=True,
    )
    if not actions_enabled:
        st.markdown(
            '<div class="ae-hint-box" style="border-left-color:#e3b341">'
            "💡 <strong>Tip:</strong> Enable incident actions:<br>"
            "<code>from fastapi_alertengine import actions_router</code><br>"
            "<code>app.include_router(actions_router)</code>"
            "</div>",
            unsafe_allow_html=True,
        )

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1b · INCIDENT TIMELINE  +  ROOT CAUSE
# Placed above the Active Incident card as the storytelling entry point.
# ─────────────────────────────────────────────────────────────────────────────

# ── Real incident timeline from backend ───────────────────────────────
_raw_timeline = fetch_timeline(service)
# Fallback to synthetic if no real events yet (memory mode / new deploy)
if _raw_timeline:
    _timeline_events = _raw_timeline
else:
    _timeline_events = build_incident_timeline(ts_df, ep_df, health)


_root_cause = build_root_cause(ep_df, health)

# Only render the section when there is something meaningful to show.
if _timeline_events or (health and h_status in ("warning", "critical")):
    st.markdown('<div class="ae-section">Incident Timeline</div>', unsafe_allow_html=True)

    tl_col, rc_col = st.columns([3, 2])

    # ── Timeline events ───────────────────────────────────────────────────────
    with tl_col:
        _SEV_COLOR = {"info": "#58a6ff", "warning": "#e3b341", "critical": "#f85149"}
        _SEV_ICON  = {"info": "ℹ️",      "warning": "⚠️",      "critical": "🔴"}
        if _timeline_events:
            for _ev in reversed(_timeline_events):
                _sev   = _ev.get("severity", "info")
                _color = _SEV_COLOR.get(_sev, "#8b949e")
                _icon  = _SEV_ICON.get(_sev, "·")
                st.markdown(
                    f'<div class="ae-timeline-event" style="border-left-color:{_color}">'
                    f'  <span class="ae-timeline-ts">{__import__("datetime").datetime.fromtimestamp(float(_ev["timestamp"])).strftime("%H:%M:%S") if _ev.get("timestamp") and str(_ev.get("timestamp","")).replace(".","").isdigit() else (_ev.get("ts_str") or _ev.get("timestamp") or "—")}</span>'
                    f'  <span class="ae-timeline-type" style="color:{_color}">'
                    f'    {_ev.get("event_type", "")}'
                    f'  </span>'
                    f'  <div class="ae-timeline-msg">{_icon} {_ev.get("message", "")}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                '<div class="ae-timeline-empty">✅ No incident events detected in the current window.</div>',
                unsafe_allow_html=True,
            )

    # ── Root cause ────────────────────────────────────────────────────────────
    with rc_col:
        st.markdown('<div class="ae-section">Root Cause</div>', unsafe_allow_html=True)
        _conf_color = {
            "Very High": "#f85149",
            "High":      "#e3b341",
            "Low":       "#3fb950",
            "None":      "#8b949e",
        }.get(_root_cause.get("confidence", "Low"), "#8b949e")
        st.markdown(
            f'<div class="ae-rootcause-box">'
            f'  <div class="ae-rootcause-label">Likely Root Cause</div>'
            f'  <div class="ae-rootcause-value">{_root_cause["root_cause"]}</div>'
            f'  <div class="ae-rootcause-meta">'
            f'    <strong>Affected Service:</strong> {_root_cause["service"]}<br>'
            f'    <strong>Primary Signal:</strong> {_root_cause["signal"]}<br>'
            f'    <strong>Confidence:</strong>'
            f'    <span style="color:{_conf_color}">&nbsp;{_root_cause["confidence"]}</span>'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True,
        )

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 · ACTIVE INCIDENT CARD  (reworked from "Alert Status")
# ─────────────────────────────────────────────────────────────────────────────

st.markdown('<div class="ae-section">Active Incident</div>', unsafe_allow_html=True)

inc_left, inc_right = st.columns([3, 2])

with inc_left:
    if health:
        inc_cls = f"ae-incident-{h_status}" if h_status in ("ok", "warning", "critical") else "ae-incident-unknown"

        incident_summary = {
            "ok":       ("System operating normally",           "Normal Operations",  "No active issues detected"),
            "warning":  ("Degraded performance detected",       "Performance Degraded", "Latency or error rate elevated above baseline"),
            "critical": ("Critical latency or error threshold breached", "Active Incident", "Immediate attention recommended"),
        }
        _inc_desc, _inc_type, _inc_impact = incident_summary.get(
            h_status, ("Evaluation unavailable", "Unknown", "Backend may be unreachable")
        )

        # Trend label from anomaly score
        if h_anomaly > 2.0:
            _trend = "📈 Rapidly worsening"
        elif h_anomaly > 1.0:
            _trend = "↗️ Trending upward"
        elif h_anomaly > 0.2:
            _trend = "→ Stable"
        else:
            _trend = "↘️ Improving"

        alerts_list = health.get("alerts", [])
        _alert_lines = ""
        for al in alerts_list:
            sev = al.get("severity", "warning")
            sev_color = "#f85149" if sev == "critical" else "#e3b341"
            _alert_lines += (
                f'<div style="margin-top:0.4rem;padding:0.3rem 0.6rem;'
                f'border-left:3px solid {sev_color};background:#12181f;'
                f'border-radius:4px;font-size:0.82rem;color:#c9d1d9">'
                f'<strong style="color:{sev_color}">{sev.upper()}</strong> — {al.get("message","")}'
                f'</div>'
            )

        st.markdown(
            f'<div class="ae-incident-card {inc_cls}">'
            f'  <div class="ae-incident-title">{status_emoji(h_status)}&nbsp; {_inc_type}</div>'
            f'  <div class="ae-incident-service">service: {h_svc} / instance: {h_inst}</div>'
            f'  <div class="ae-incident-meta">'
            f'    {fmt_ts(h_ts)} &nbsp;·&nbsp; {h_n} samples &nbsp;·&nbsp; anomaly {h_anomaly:.2f}'
            f'    &nbsp;·&nbsp; {_trend}'
            f'  </div>'
            f'  <div class="ae-incident-body">'
            f'    <strong>Summary:</strong> {_inc_desc}<br>'
            f'    <strong>Impact:</strong> {_inc_impact}<br>'
            f'    <strong>P95 latency:</strong> {h_p95:.0f} ms &nbsp;·&nbsp; '
            f'    <strong>Error rate:</strong> {err_pct:.1f}%'
            f'    {_alert_lines}'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        with st.expander("📐 Alert thresholds", expanded=False):
            thresholds = health.get("thresholds", {})
            thr_p95_w = thresholds.get("p95_warning_ms", 1000)
            thr_p95_c = thresholds.get("p95_critical_ms", 3000)
            thr_err_w = thresholds.get("error_rate_warning", 0.1)
            thr_err_c = thresholds.get("error_rate_critical", 0.2)
            th1, th2 = st.columns(2)
            with th1:
                st.metric("P95 warning", f"{thr_p95_w:,} ms")
                st.metric("P95 critical", f"{thr_p95_c:,} ms")
            with th2:
                st.metric("Error rate warning", f"{thr_err_w*100:.0f}%")
                st.metric("Error rate critical", f"{thr_err_c*100:.0f}%")
    else:
        st.markdown(
            '<div class="ae-incident-card ae-incident-unknown">'
            '<div class="ae-incident-title">❓ Backend Unreachable</div>'
            '<div class="ae-incident-body">Alert state unavailable — verify backend connectivity.</div>'
            '</div>',
            unsafe_allow_html=True,
        )

# ── System Health strip (right column alongside incident) ─────────────────────
with inc_right:
    st.markdown('<div class="ae-section">Health Metrics</div>', unsafe_allow_html=True)
    p95_cls = "c-critical" if h_p95 > 3000 else "c-warning" if h_p95 > 1000 else "c-ok"
    err_cls  = "c-critical" if err_pct > 20 else "c-warning" if err_pct > 10 else "c-ok"
    score_cls = "c-critical" if h_score < 50 else "c-warning" if h_score < 80 else "c-ok"
    st.markdown(card("P95 Latency",   f"{h_p95:.0f} ms",   p95_cls),  unsafe_allow_html=True)
    st.markdown(card("Error Rate",    f"{err_pct:.1f}%",    err_cls),  unsafe_allow_html=True)
    st.markdown(card("Health Score",  f"{h_score} / 100",   score_cls), unsafe_allow_html=True)
    st.markdown(card("Req / Min",     str(rpm),              "c-blue"), unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2b · INCIDENT RESPONSE CONTROLS
# Only rendered when system is degraded — gives the SRE direct action options.
# ─────────────────────────────────────────────────────────────────────────────

if h_status in ("warning", "critical"):
    st.markdown('<div class="ae-section">Incident Response</div>', unsafe_allow_html=True)

    ctrl_c1, ctrl_c2, ctrl_c3 = st.columns(3)

    # ── Restart Service ───────────────────────────────────────────────────────
    with ctrl_c1:
        if st.button("🔁 Restart Service", use_container_width=True, key="btn_restart"):
            if actions_enabled:
                st.info(
                    f"To restart **{h_svc or service}**, call the actions endpoint with a "
                    f"signed token:\n\n"
                    f"```\nGET {BASE_URL}/action/restart?token=<signed-jwt>\n```\n\n"
                    f"Generate a token via your backend and visit "
                    f"`{BASE_URL}/action/confirm?token=<jwt>` to confirm."
                )
            else:
                st.warning(
                    "Action support is not enabled on this backend.\n\n"
                    "To activate, mount the actions router:\n"
                    "```python\n"
                    "from fastapi_alertengine import actions_router\n"
                    "app.include_router(actions_router)\n"
                    "```"
                )

    # ── Silence (session-scoped) ──────────────────────────────────────────────
    with ctrl_c2:
        _silenced = st.session_state.get("incident_silenced", False)
        _btn_label = "🔔 Unsilence" if _silenced else "🔕 Silence (this session)"
        if st.button(_btn_label, use_container_width=True, key="btn_silence"):
            st.session_state["incident_silenced"] = not _silenced
            if not _silenced:
                st.success("Incident alerts silenced for this browser session.")
            else:
                st.info("Incident alerts re-enabled.")

    # ── Copy Incident Summary ─────────────────────────────────────────────────
    with ctrl_c3:
        if st.button("📋 Copy Incident Summary", use_container_width=True, key="btn_copy"):
            _summary_lines = [
                f"Incident Summary — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"Service:      {h_svc or service}",
                f"Status:       {h_status.upper()}",
                f"P95 Latency:  {h_p95:.0f} ms",
                f"Error Rate:   {err_pct:.1f}%",
                f"Health Score: {h_score}/100",
                "",
                f"Root Cause:   {_root_cause['root_cause']}",
                f"Signal:       {_root_cause['signal']}",
                f"Confidence:   {_root_cause['confidence']}",
            ]
            if _timeline_events:
                _summary_lines.append("")
                _summary_lines.append("Timeline:")
                for _ev in reversed(_timeline_events):
                    _summary_lines.append(
                        f"  {__import__('datetime').datetime.fromtimestamp(float(_ev['timestamp'])).strftime('%H:%M:%S') if _ev.get('timestamp') else '—'}  [{_ev.get('event_type','')}]  "
                        f"{_ev.get('message','')}"
                    )
            st.code("\n".join(_summary_lines), language="text")

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 · TOP SIGNALS  +  WHAT CHANGED
# ─────────────────────────────────────────────────────────────────────────────

sig_col, delta_col = st.columns([3, 2])

with sig_col:
    st.markdown('<div class="ae-section">Top Signals</div>', unsafe_allow_html=True)
    if not ep_df.empty:
        for _, row in ep_df.head(3).iterrows():
            err_color = "#f85149" if row["error_rate_pct"] > 10 else "#e3b341" if row["error_rate_pct"] > 2 else "#3fb950"
            lat_color = "#f85149" if row["avg_latency_ms"] > 3000 else "#e3b341" if row["avg_latency_ms"] > 1000 else "#c9d1d9"
            st.markdown(
                f'<div class="ae-signal-item">'
                f'  <span class="ae-signal-path">{row["path"]}</span>'
                f'  <span style="color:#8b949e"> [{row["method"]}]</span>'
                f'  &nbsp;→&nbsp;'
                f'  <span style="color:{lat_color}">{row["avg_latency_ms"]:.0f} ms avg</span>'
                f'  &nbsp;·&nbsp;'
                f'  <span style="color:{err_color}">{row["error_rate_pct"]:.1f}% errors</span>'
                f'  &nbsp;·&nbsp;'
                f'  <span style="color:#8b949e">impact {row["impact_score"]}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
    else:
        st.info("No endpoint signals in the selected time window.")

with delta_col:
    st.markdown('<div class="ae-section">What Changed</div>', unsafe_allow_html=True)
    if not ts_df.empty and len(ts_df) > 1:
        latest = ts_df.iloc[-1]
        prev   = ts_df.iloc[-2]
        delta_lat = latest["avg_latency_ms"] - prev["avg_latency_ms"]
        delta_err = latest["error_rate_pct"] - prev["error_rate_pct"]
        delta_req = latest["total_requests"] - prev["total_requests"]

        def _delta_cls(v: float, invert: bool = False) -> str:
            if abs(v) < 0.01:
                return "ae-delta-flat"
            return ("ae-delta-up" if v > 0 else "ae-delta-down") if not invert else \
                   ("ae-delta-down" if v > 0 else "ae-delta-up")

        st.markdown(
            f'<div class="ae-delta-box">'
            f'  <div><span class="{_delta_cls(delta_lat, invert=True)}">Latency: {delta_lat:+.1f} ms</span></div>'
            f'  <div><span class="{_delta_cls(delta_err, invert=True)}">Error rate: {delta_err:+.2f}%</span></div>'
            f'  <div><span class="{_delta_cls(delta_req)}">Requests: {delta_req:+.0f} / min</span></div>'
            f'  <div style="margin-top:0.4rem;color:#8b949e;font-size:0.75rem">'
            f'    vs previous bucket ({prev["ts"].strftime("%H:%M") if hasattr(prev["ts"], "strftime") else "prev"})'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="ae-delta-box"><span class="ae-delta-flat">Not enough data points for delta analysis yet.</span></div>',
            unsafe_allow_html=True,
        )

st.markdown("<br>", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 · Time-Series Charts
# ─────────────────────────────────────────────────────────────────────────────

st.markdown(
    f'<div class="ae-section">Trends — {time_range_label}</div>',
    unsafe_allow_html=True,
)

ch1, ch2, ch3 = st.columns(3)
_cfg = {"displayModeBar": False}


def _line(x, y, color: str, fill: bool = True) -> go.Scatter:
    return go.Scatter(
        x=x, y=y,
        mode="lines",
        line=dict(color=color, width=2),
        fill="tozeroy" if fill else "none",
        fillcolor=color.replace(")", ",0.1)").replace("rgb", "rgba") if fill else None,
        hovertemplate="%{y:.1f}<extra></extra>",
    )


with ch1:
    if not ts_df.empty:
        fig = go.Figure(_line(ts_df["ts"], ts_df["total_requests"], "#58a6ff"))
        fig.update_layout(**_CHART_BASE, title=_chart_title("Requests / min"))
        st.plotly_chart(fig, use_container_width=True, config=_cfg)
    else:
        st.plotly_chart(empty_chart("Requests / min"), use_container_width=True, config=_cfg)

with ch2:
    if not ts_df.empty:
        fig = go.Figure(_line(ts_df["ts"], ts_df["error_rate_pct"], "#f85149"))
        fig.update_layout(**_CHART_BASE, title=_chart_title("Error Rate %"))
        # Threshold reference line at 10%
        fig.add_hline(y=10, line_dash="dot", line_color="#e3b341", line_width=1,
                      annotation_text="warning", annotation_font_color="#e3b341",
                      annotation_font_size=10)
        fig.add_hline(y=20, line_dash="dot", line_color="#f85149", line_width=1,
                      annotation_text="critical", annotation_font_color="#f85149",
                      annotation_font_size=10)
        st.plotly_chart(fig, use_container_width=True, config=_cfg)
    else:
        st.plotly_chart(empty_chart("Error Rate %"), use_container_width=True, config=_cfg)

with ch3:
    if not ts_df.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=ts_df["ts"], y=ts_df["avg_latency_ms"],
            mode="lines", name="avg",
            line=dict(color="#3fb950", width=2),
            fill="tozeroy", fillcolor="rgba(63,185,80,0.08)",
            hovertemplate="avg: %{y:.1f} ms<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=ts_df["ts"], y=ts_df["max_latency_ms"],
            mode="lines", name="max",
            line=dict(color="#e3b341", width=1, dash="dot"),
            hovertemplate="max: %{y:.1f} ms<extra></extra>",
        ))
        fig.update_layout(
            **{**_CHART_BASE, "showlegend": True},
            title=_chart_title("Latency ms"),
            legend=dict(
                orientation="h", y=1.12, x=1, xanchor="right",
                font=dict(size=10, color="#8b949e"),
                bgcolor="rgba(0,0,0,0)", borderwidth=0,
            ),
        )
        st.plotly_chart(fig, use_container_width=True, config=_cfg)
    else:
        st.plotly_chart(empty_chart("Latency ms"), use_container_width=True, config=_cfg)

st.markdown("<br>", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 · Endpoint Performance Table
# ─────────────────────────────────────────────────────────────────────────────

st.markdown(
    '<div class="ae-section">Endpoint Performance · Sorted by Impact Score ↓</div>',
    unsafe_allow_html=True,
)

if not ep_df.empty:
    max_impact = int(ep_df["impact_score"].max()) or 1
    st.dataframe(
        ep_df.rename(columns={
            "path":           "Endpoint",
            "method":         "Method",
            "request_count":  "Requests",
            "avg_latency_ms": "Avg Latency (ms)",
            "max_latency_ms": "Max Latency (ms)",
            "error_rate_pct": "Error Rate %",
            "impact_score":   "Impact Score ⚡",
        }),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Impact Score ⚡": st.column_config.ProgressColumn(
                "Impact Score ⚡",
                help="request_count × avg_latency — higher = higher business risk",
                min_value=0,
                max_value=max_impact,
                format="%d",
            ),
            "Error Rate %": st.column_config.NumberColumn(
                "Error Rate %", format="%.1f %%",
            ),
            "Avg Latency (ms)": st.column_config.NumberColumn(
                "Avg Latency (ms)", format="%.1f ms",
            ),
            "Max Latency (ms)": st.column_config.NumberColumn(
                "Max Latency (ms)", format="%.1f ms",
            ),
        },
    )
else:
    st.info("No endpoint data for the selected time range and service.")

st.markdown("<br>", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 · Ingestion Debug
# ─────────────────────────────────────────────────────────────────────────────

st.markdown('<div class="ae-section">Ingestion Health</div>', unsafe_allow_html=True)

with st.expander("🔧 Ingestion details", expanded=False):
    if ingestion:
        enqueued       = int(ingestion.get("enqueued", 0))
        dropped_q      = int(ingestion.get("dropped", 0))
        dropped_agg    = int(ingestion.get("dropped_agg_keys", 0))
        dropped_alerts = int(ingestion.get("dropped_alerts", 0))
        last_drain     = ingestion.get("last_drain_at")

        # Throughput estimate: total enqueued / window_seconds
        window_s = last_n_buckets * 60
        throughput = enqueued / window_s if window_s else 0.0

        # Queue saturation: we track cumulative enqueued so it's not a real
        # queue fill %, but dropped > 0 signals pressure.
        pressure = "🔴 Pressure!" if dropped_q > 0 else "🟢 Healthy"

        r1, r2 = st.columns(2)
        with r1:
            st.metric("Enqueued (total)", f"{enqueued:,}")
            st.metric("Dropped — queue", f"{dropped_q:,}",
                      delta=f"+{dropped_q}" if dropped_q else None,
                      delta_color="inverse")
            st.metric("Dropped — agg keys", f"{dropped_agg:,}",
                      delta=f"+{dropped_agg}" if dropped_agg else None,
                      delta_color="inverse")
        with r2:
            st.metric("Dropped — alerts", f"{dropped_alerts:,}",
                      delta=f"+{dropped_alerts}" if dropped_alerts else None,
                      delta_color="inverse")
            st.metric("Last drain", fmt_ts(last_drain))
            st.metric("Est. throughput", f"{throughput:.1f} req/s")

        st.markdown(f"**Queue pressure:** {pressure}")

        # Visual saturation bar
        fill_pct = min(1.0, dropped_q / max(enqueued, 1))
        st.progress(fill_pct, text=f"Drop ratio: {fill_pct*100:.1f}%")
    else:
        st.warning("Ingestion stats unavailable — backend may be unreachable.")

st.markdown("<br>", unsafe_allow_html=True)

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown(
    '<div style="color:#8b949e;font-size:0.75rem;text-align:center;padding-top:1rem;border-top:1px solid #21262d">'
    "fastapi-alertengine observability dashboard · "
    f'<a href="{BASE_URL}/docs" style="color:#58a6ff" target="_blank">API docs</a>'
    "</div>",
    unsafe_allow_html=True,
)

# ── Auto-refresh ──────────────────────────────────────────────────────────────
if auto_refresh:
    time.sleep(REFRESH_S)
    st.rerun()

