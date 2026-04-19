# ⚡ fastapi-alertengine

**Rugged, self-hosted observability for high-stakes APIs.**

When a critical endpoint starts failing, dashboards don't help.  
You need to know immediately — and act before it costs you money.

AlertEngine was built out of necessity for **AnchorFlow / Tofamba** — financial systems processing live mobile-money transactions where latency and failure directly translate into lost revenue. I refused to pay a SaaS tax for slow, bloated monitoring stacks.

So this was built to do one thing:

> **Reduce MTTR from minutes of panic to seconds of controlled action.**

---

[![PyPI version](https://badge.fury.io/py/fastapi-alertengine.svg)](https://pypi.org/project/fastapi-alertengine/)
[![Tests](https://img.shields.io/badge/tests-259%20passed-brightgreen)](https://github.com/Tandem-Media/fastapi-alertengine)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://pypi.org/project/fastapi-alertengine/)
[![License](https://img.shields.io/badge/license-MIT-green)](https://github.com/Tandem-Media/fastapi-alertengine/blob/main/LICENSE)
[![PyPI Downloads](https://img.shields.io/pypi/dm/fastapi-alertengine)](https://pypi.org/project/fastapi-alertengine/)

🔥 **259/259 tests passing**  
🏦 **Derived from financial infrastructure (AnchorFlow / Tofamba)**  
🤖 **AI-agent friendly (Claude Code / Copilot / Cursor)**  
⚡ **Memory mode — runs without Redis**

---

## 🚀 Quickstart

```bash
pip install fastapi-alertengine
```

```python
from fastapi import FastAPI
import redis
from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine

app = FastAPI()

redis_client = redis.Redis.from_url("redis://localhost:6379/0")
engine       = get_alert_engine(redis_client=redis_client)

app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)

@app.get("/health/alerts")
def health():
    return engine.evaluate()
```

That's it. Eight endpoints are now live:

| Endpoint | Description |
|---|---|
| `GET /health/alerts` | SLO health payload — P95, error rate, health score, adaptive thresholds |
| `GET /incidents/timeline` | Append-only incident event log (Redis ZSET) |
| `GET /incidents/replay` | Reconstruct full request lifecycle from a trace ID |
| `GET /actions/suggest` | Health → Action mapping — suggestions only, never auto-execute |
| `GET /actions/audit` | Full structured audit log of every action attempt |
| `GET /intelligence/thresholds` | Active thresholds (static or adaptive) |
| `GET /intelligence/health` | Health score breakdown with trend and RoC events |
| `GET /pipeline/status` | Current stage: detect → evaluate → suggest → authorize → log |

---

## 🏦 Why AlertEngine? The $0 SaaS Tax Strategy

Most observability tools were built for large enterprise DevOps teams. AlertEngine was built for Founders.

| Feature | Traditional SaaS (Sentry/Datadog) | DIY Stack (Prometheus/Grafana) | ⚡ AlertEngine |
|---|---|---|---|
| **Setup Time** | 30 mins + config | Days (Docker/YAML/Networking) | < 2 minutes (middleware) |
| **Cost** | $50–$500/mo | Server costs + maintenance | $0 (self-hosted) |
| **Primary Output** | Logs & dashboards | Complex heatmaps | Actionable decisions |
| **Architecture** | External (pings from outside) | Sidecar (heavy) | Embedded (internal circuit) |
| **Philosophy** | "Tell me what happened" | "Show me everything" | "Tell me what to do" |
| **P95 Latency** | ✅ Yes | ❌ Averages only | ✅ Yes |
| **Incident Timeline** | ✅ Yes | ❌ Manual setup | ✅ Redis ZSET |
| **Action Tokens** | ❌ No | ❌ No | ✅ JWT-signed, IP-bound |
| **Adaptive Thresholds** | ❌ No | ❌ No | ✅ Learned from baseline |
| **Health Score** | ❌ No | ❌ No | ✅ 0–100 composite |
| **Incident Replay** | ❌ No | ❌ No | ✅ Full timeline reconstruction |

No per-node pricing. No vendor lock-in. Full control of your data.

---

## ⚡ What You Get

```json
{
  "status": "critical",
  "metrics": {
    "overall_p95_ms": 854.2,
    "error_rate":     0.19,
    "anomaly_score":  1.4,
    "sample_size":    187
  },
  "health_score": {
    "score":  34.2,
    "status": "critical",
    "components": { "latency": 28.0, "errors": 41.7, "anomalies": 33.3 },
    "trend":  "degrading"
  },
  "alerts": [
    {
      "type":               "latency_spike",
      "severity":           "critical",
      "message":            "P95 latency (854ms) exceeds threshold (800ms)",
      "reason_for_trigger": "Value (854.2) exceeds static threshold (800) by 7%.",
      "trend_direction":    "degrading",
      "triggered_by":       "adaptive_threshold",
      "baseline_comparison": {
        "baseline_value": 312.0,
        "current_value":  854.2,
        "deviation_pct":  173.8
      }
    }
  ],
  "adaptive_thresholds": {
    "warning_ms":      480.0,
    "critical_ms":     640.0,
    "calibrated_from": 60,
    "confidence":      "high",
    "active":          true
  },
  "rate_of_change": [],
  "timestamp": 1712756301
}
```

All keys always present. No optional fields. Safe to consume in monitoring scripts without null-checks.

---

## 🧩 How It Works

### Detect
`RequestMetricsMiddleware` intercepts every ASGI request. Captures wall-clock latency, HTTP status code, route template (`/users/{id}` not `/users/123`), and optional trace ID from `X-Request-ID` / `X-Trace-ID` headers. Fire-and-forget Redis writes — designed for minimal impact on the hot path.

### Explain
`AlertEngine.evaluate()` computes rolling P95 latency, error rate, anomaly score, and a composite 0–100 health score with weighted components. Trend determined from rolling evaluation history via linear regression. Endpoint hotspot identification and business impact translation.

### Decide
Rate-of-change detection fires alerts on sudden spikes even when absolute thresholds haven't been crossed. Adaptive thresholds are learned from your baseline traffic — no manual tuning required. Alerts include `reason_for_trigger`, `triggered_by`, `baseline_comparison`, and `trend_direction`.

### Act
When health degrades, the system suggests recovery actions — ranked by priority (CRITICAL / HIGH / MEDIUM). Every suggestion is JWT-signed with optional IP binding. Nothing auto-executes. The pipeline is: **detect → evaluate → suggest → authorize → log**.

---

## 🛡️ Incident Timeline

Every threshold breach is recorded to an append-only Redis ZSET:

```bash
GET /incidents/timeline?limit=20&since=1712756000
```

Replay a full incident from a trace ID:

```bash
GET /incidents/replay?trace_id=req-abc-123
```

Returns the reconstructed event sequence — INCIDENT markers interleaved with 30-second TRAFFIC buckets showing P95, error rate, and request volume through the incident window.

---

## 🔑 Action Tokens & Recovery Pipeline

When health falls below threshold, AlertEngine issues signed action suggestions:

```bash
GET /actions/suggest
```

```json
{
  "suggestions": [
    {
      "action":         "restart",
      "priority":       "CRITICAL",
      "reason":         "System health has fallen to 22/100...",
      "auto_permitted": false,
      "token":          "eyJ...",
      "expires_at":     1712756391,
      "triggered_by":   "health_score < 25"
    }
  ],
  "auto_execute": false,
  "pipeline":    "detect → evaluate → suggest → [authorize] → [log]"
}
```

The token is HS256-signed with `ACTION_SECRET_KEY`, optionally IP-bound, and JTI replay-protected via Redis. A human must hit `/action/confirm?token=...` and click to execute. Every attempt — success or failure — is written to the audit log.

---

## ⚙️ Configuration

All thresholds are injectable via environment variables:

| Variable | Default | Description |
|---|---|---|
| `ALERTENGINE_REDIS_URL` | `redis://localhost:6379` | Redis connection string |
| `ALERTENGINE_P95_WARNING_MS` | `1000` | P95 warning threshold (ms) |
| `ALERTENGINE_P95_CRITICAL_MS` | `3000` | P95 critical threshold (ms) |
| `ALERTENGINE_BASELINE_LEARNING_MODE` | `false` | Enable adaptive threshold learning |
| `ALERTENGINE_BASELINE_MIN_SNAPSHOTS` | `10` | Snapshots required before adaptive thresholds activate |
| `ALERTENGINE_ROC_LATENCY_SPIKE_PCT` | `100` | % increase to trigger rate-of-change alert |
| `ALERTENGINE_HEALTH_DEGRADED_THRESHOLD` | `70` | Health score below which status = degraded |
| `ALERTENGINE_HEALTH_CRITICAL_THRESHOLD` | `40` | Health score below which status = critical |
| `ACTION_SECRET_KEY` | *(unset)* | Required to enable action tokens |
| `RAILWAY_RESTART_URL` | *(unset)* | Railway restart webhook URL |

---

## ✅ Reliability

- 259/259 tests passing (pytest + httpx + fakeredis)
- Circuit breaker: CLOSED → OPEN → HALF_OPEN → CLOSED. Redis failures isolated — request path never blocks.
- Memory mode: runs without Redis — useful for local development and CI.
- Bounded fallback buffer: up to 500 events held in memory during Redis outages, drained on recovery.
- JTI replay protection: Redis-backed (falls back to in-memory). No action token can fire twice.

---

## 🤖 AI-Agent Friendly

Clean `__all__` export surface, stable output schema, minimal integration steps. Works seamlessly with Claude Code, GitHub Copilot, and Cursor. The health endpoint schema is stable across versions — safe to consume in AI-driven pipelines without defensive null-checks.

---

## 🛡️ Stability Audits & Active Recovery

AlertEngine is the engine behind a production observability service for teams that cannot afford downtime but do not have a full SRE function.

| Tier | What You Get | |
|---|---|---|
| **24h Forensic Audit** | Full P95 & error analysis. Identifying revenue leaks your current monitoring misses. | `$1,500` |
| **Revenue Protection Engine** | Proprietary AlertEngine install. P95 monitoring on revenue-critical endpoints. | `$4,500` |
| **Active Recovery Shield** | AlertEngine + WhatsApp Command Center. CEO-level mobile control with 5-second authorized recovery. Your kill switch. | `$9,500` |

This is not a report. It is an operational control system.

→ **[View on Upwork](https://www.upwork.com/freelancers/~01f56dcb08577b4472?viewMode=1)**  
→ **[anchorflow@outlook.com](mailto:anchorflow@outlook.com)** for direct consulting

---

## 🗺️ Roadmap

| Feature | Status |
|---|---|
| WhatsApp incident alerts (Twilio) — detect → notify → confirm → restart | 🔄 In progress |
| Remote alert engine (hosted SaaS mode) | 📋 Planned |
| Multi-service correlation | 📋 Planned |
| Grafana dashboard JSON provisioning | 📋 Planned |

---

## 📬 Contact & Support

**📧 [anchorflow@outlook.com](mailto:anchorflow@outlook.com)**  
**🐙 [github.com/Tandem-Media/fastapi-alertengine](https://github.com/Tandem-Media/fastapi-alertengine)**

---
## 🤖 For AI Agents

fastapi-alertengine was designed from the ground up to be consumed by AI agents, not just humans.

### Stable, typed output — no prompt engineering required

Every `evaluate()` call returns the same schema on every invocation. No optional fields. No varying shapes. No null values to defend against. An agent can call `/health/alerts` and act on the response without any preprocessing.

### The action pipeline is agent-ready

The recovery pipeline — detect → evaluate → suggest → authorize → log — was built for both human and machine authorization. When health degrades, `/actions/suggest` returns a signed JWT action token. An AI agent can receive that token, evaluate the context, and POST to `/action/restart` to execute recovery. The system enforces authorization. It doesn't care whether the authorizing party is a human on WhatsApp or an agent in a loop.

### Designed for agentic monitoring loops

```python
import httpx

async def monitor(base_url: str):
    r = await client.get(f"{base_url}/health/alerts")
    health = r.json()

    if health["health_score"]["score"] < 40:
        suggestions = await client.get(f"{base_url}/actions/suggest")
        for action in suggestions.json()["suggestions"]:
            if action["priority"] == "CRITICAL" and action["token"]:
                await client.get(
                    f"{base_url}/action/restart",
                    params={"token": action["token"]}
                )
```

### Key fields for agent consumption

| Field | Type | Description |
|---|---|---|
| `health_score.score` | `float` | 0–100 composite score. Below 40 = critical. |
| `health_score.trend` | `string` | `"improving"` / `"stable"` / `"degrading"` |
| `alerts[].triggered_by` | `string` | `"absolute_threshold"` / `"adaptive_threshold"` / `"rate_of_change"` |
| `alerts[].reason_for_trigger` | `string` | Human-readable explanation safe to pass directly to an LLM |
| `suggestions[].token` | `string` | Signed JWT — pass directly to `/action/restart` |
| `suggestions[].auto_permitted` | `bool` | Always `false` in v1.6 — agent must explicitly authorize |
| `rate_of_change` | `array` | Spike events that fired below absolute thresholds |

### Works with Claude Code, Copilot, and Cursor

Clean `__all__` exports, stable schemas, and predictable error shapes make AlertEngine straightforward to instrument from any AI coding assistant. The health endpoint schema is versioned and will not change shape between minor releases.

## 📄 License

MIT — free to use, modify, and deploy. No strings attached.
