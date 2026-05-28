# FastAPI incident intelligence with human-approved recovery.

Add one line to your FastAPI app. Detect latency spikes, error surges, and degraded health. Get WhatsApp or Telegram recovery approvals that require your explicit authorization.

[![PyPI](https://img.shields.io/pypi/v/fastapi-alertengine)](https://pypi.org/project/fastapi-alertengine/)
[![Python](https://img.shields.io/pypi/pyversions/fastapi-alertengine)](https://pypi.org/project/fastapi-alertengine/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://github.com/Tandem-Media/fastapi-alertengine/actions/workflows/ci.yml/badge.svg)](https://github.com/Tandem-Media/fastapi-alertengine/actions)
[![RepoRanker](https://reporanker.com/badge/Tandem-Media/fastapi-alertengine)](https://reporanker.com/repos/Tandem-Media/fastapi-alertengine)

---

## Human-Authorized. Always.

> Nothing executes without your explicit approval.  
> Every action is logged immutably.  
> The system fails safe — never fails open.

- `GET /action/recover` — preview only, zero side effects
- `POST /action/recover/confirm` — irreversible, requires valid JWT
- JWT tokens: tenant-scoped, 5-minute TTL, single-use
- Replay protection: atomic Redis SET NX
- Immutable audit trail on every stage transition
- Adversarial audit: 10/10 checks passed

---

## Install + Quickstart

```bash
pip install fastapi-alertengine
```

```python
from fastapi import FastAPI
from fastapi_alertengine import instrument

app = FastAPI()
instrument(app)  # that's it
```

Your app now exposes `/health/alerts`.

| Endpoint | Description |
|----------|-------------|
| `GET /health/alerts` | Current health status |
| `GET /metrics/history` | Per-minute aggregated metrics |
| `GET /metrics/ingestion` | Ingestion counters |
| `GET /__alertengine/status` | Full engine status |

---

## How It Works

**Free SDK (Steps 1–2):**

- **Step 1:** `instrument(app)` — P95 latency tracking, error rate detection, health scoring begins immediately
- **Step 2:** `GET /health/alerts` — returns P95, error rate, health score 0-100, trend direction

**Paid Orchestrator (Steps 3–4):**

- **Step 3:** Managed orchestrator polls `/health/alerts` every 5 seconds. When score drops below threshold, Claude AI diagnoses root cause in plain English.
- **Step 4:** WhatsApp or Telegram alert arrives with diagnosis and a single-use recovery link. You tap approve. Nothing executes without you.

---

## Proof Strip

**Production Proven**
- Live production tenant: fintech platform, Zimbabwe
- Human-authorized recovery confirmed end-to-end

**Security Verified**
- 232 tests passing (Python 3.10, 3.11, 3.12)
- Adversarial audit by autonomous AI agent: 10/10 passed
  (replay attacks, cross-tenant isolation, concurrent token floods)

---

## Local Incident Sensing — Free Forever

- **Category:** FastAPI incident intelligence
- **Free SDK =** Local Incident Sensing
- **Core promise:** Nothing executes without your approval

### Features

- **P95 latency tracking** — not averages, real percentiles
- **Error rate detection** — 4xx/5xx with configurable thresholds
- **Anomaly scoring** — detects spikes vs your baseline
- **Adaptive thresholds** — learns your normal traffic pattern
- **Rate-of-change detection** — catches sudden spikes below thresholds
- **Health score 0-100** — composite score with trend analysis
- **Action suggestions** — maps health to notify, alert, restart
- **Incident replay** — reconstruct state from append-only audit log
- **Circuit breaker** — buffers events during Redis outages
- **Memory mode** — never crashes your app when Redis is unavailable
- **AI-agent friendly** — clean API, works with Claude/Copilot/Cursor

### What You Get

```json
{
  "status": "critical",
  "health_score": {"score": 23, "status": "critical", "trend": "degrading"},
  "metrics": {
    "overall_p95_ms": 2847.3,
    "error_rate": 0.19,
    "anomaly_score": 1.4,
    "sample_size": 187
  },
  "alerts": [
    {
      "type": "latency_spike",
      "severity": "critical",
      "reason_for_trigger": "P95 latency 2847ms exceeds threshold 3000ms",
      "triggered_by": "absolute_threshold"
    }
  ]
}
```

### Pipeline

```
FastAPI Request
↓
RequestMetricsMiddleware  ← measures latency + status
↓
Redis Streams             ← append-only event log
↓
Alert Engine              ← P95 + error rate + anomaly scoring
↓
/health/alerts            ← single status: ok | warning | critical
```

---

## Managed Incident Command — Paid

The orchestrator is the paid layer on top of the free SDK.
It runs as a managed service on your behalf — you never install
it on your own infrastructure.

### How recovery works

The orchestrator calls a **recovery webhook URL** that you provide
during onboarding. This is a URL on your own infrastructure that
executes the recovery action (restart a worker, clear a cache,
scale a service). You control what the webhook does. The orchestrator
only calls it after you tap approve.

```
Your FastAPI app ← instrument(app)
        ↓
/health/alerts endpoint (public, read-only)
        ↓
AlertEngine Orchestrator (polls every 5s)
        ↓
Claude AI diagnosis
        ↓
WhatsApp/Telegram alert → you tap approve
        ↓
POST your-recovery-webhook.com/restart  ← you control this
        ↓
Confirmation message sent
```

Nothing runs on your servers except the free SDK middleware.
The orchestrator never SSH's into your machines.

### How an incident works

1. Your P95 spikes or error rate climbs
2. Orchestrator detects it within 5 seconds
3. Claude diagnoses root cause in plain English
4. You receive WhatsApp/Telegram: what broke, why, suggested fix
5. Secure recovery link included (JWT-signed, expires in 5 minutes)
6. You tap Approve
7. Your recovery webhook executes
8. You receive confirmation when system recovers

### Notification Channels

| Channel | Provider | Plan | Best for |
|---------|----------|------|----------|
| WhatsApp | Sent.dm | Solo (default) | Zero-friction, instant setup |
| WhatsApp | Twilio | All | Enterprise existing accounts |
| Telegram | Telegram Bot API | All | Developers, North America |
| Slack | Incoming Webhooks | Startup+ | Team transparency |
| Webhook | HTTP POST | All | Slack/Teams/PagerDuty fallback |

---

## Pricing

| Tier | Price | Services | Incidents/mo | Channels |
|------|-------|----------|--------------|----------|
| Hobby | $19/mo | 1 | 5 | Telegram only |
| Developer | $99/mo | 1 | 10 | WhatsApp |
| Solo | $299/mo | 3 | 50 | WhatsApp + Telegram |
| Startup | $799/mo | 10 | 200 | WhatsApp + Telegram + Slack |
| Scale | $1,500/mo | 20 | 1,000 | All channels + Voice |
| Enterprise | Custom | Custom | Custom | Custom |

---

## Reliability Guarantees

- **Duplicate incident prevention** — tenant-scoped lock + idempotency
- **Replay protection** — JWT tokens single-use, atomic Redis SET NX
- **Distributed locking** — Lua script atomic release, no race conditions
- **Tenant isolation** — cross-tenant data access returns 403
- **Audit trail** — every stage transition and recovery authorization logged
- **Degraded mode** — NORMAL / DEGRADED / EMERGENCY with auto-recovery
- **Dead letter queue** — unrecoverable failures captured for replay
- **Circuit breaker** — per-provider per-tenant, Redis-backed

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| REDIS_URL | Yes | Redis connection URL |
| ALERTENGINE_BASE_URL | Yes | **Orchestrator's** public URL (used to generate recovery links) |
| ANTHROPIC_API_KEY | Yes | Claude AI API key |
| ALERT_SECRET | Yes | JWT signing secret |
| TWILIO_ACCOUNT_SID | Twilio only | Twilio account SID |
| TWILIO_AUTH_TOKEN | Twilio only | Twilio auth token |
| TWILIO_WHATSAPP_FROM | Twilio only | Sender WhatsApp number |
| SENT_API_KEY | Sent.dm only | Sent.dm API key |
| SENT_PHONE_ID | Sent.dm only | Sent.dm phone ID |
| LOOP_INTERVAL_S | No | Polling interval seconds (default: 5) |
| POLICY_MIN_SCORE_TO_ALERT | No | Min score to open incident (default: 70) |

> `ALERTENGINE_BASE_URL` is the orchestrator's URL, not your app's URL.
> Your app's URL is set per-tenant during onboarding via the `health_url` field.

---

## Repository Structure

```text
fastapi_alertengine/     ← Free SDK — MIT licensed
  middleware.py          ← RequestMetricsMiddleware
  engine.py             ← Core alert engine
  intelligence.py       ← Adaptive thresholds, health scoring
  actions/              ← Recovery suggestions and JWT tokens
  storage.py            ← Redis Streams persistence

orchestrator/           ← Managed service — source-available, not MIT
  loop.py              ← Multi-tenant polling         See LICENSE-ORCHESTRATOR.md
  claude_engine.py     ← AI diagnosis
  notifications.py     ← Multi-channel dispatch
  audit.py             ← Immutable forensic log
  plans.py             ← Billing tiers and feature gates

examples/               ← Demo scripts
docs/                   ← Landing page (GitHub Pages)
tests/                  ← 232 tests, Python 3.10/3.11/3.12
```

---

## Adversarial Audit

This system was audited by an autonomous AI agent (Manus AI) acting
as a hostile tenant attempting to break isolation, bypass human
authorization, and overwhelm the system with concurrent requests.

**Result: 10/10 live checklist checks passed.**

- Cross-tenant isolation: blocked (403 returned)
- Replay attack (20 concurrent): exactly 1 succeeded, 19 rejected
- Natural incident detection: confirmed working
- Recovery authorization audit trail: confirmed
- DLQ plan enforcement: confirmed

---

## Get Started

**Free SDK:**
```bash
pip install fastapi-alertengine
```

**Managed orchestrator (Developer — $99/mo):**
Contact: anchorflowalertengine@outlook.com

**Upwork:**
[Human-authorized incident recovery for FastAPI](https://www.upwork.com/services/product/development-it-24-hour-stability-audit-and-an-active-recovery-system-for-instant-control-2042520713072042104)

---

## Built for mobile-first operational reality

Built in Zimbabwe where WhatsApp is the operational
control plane and engineering teams are mobile-first.
Designed for FastAPI teams everywhere who want incident
intelligence without telemetry sprawl.

---

## License + Contact

**Free SDK** (`fastapi_alertengine/`): MIT — see [LICENSE](LICENSE)

**Orchestrator** (`orchestrator/`): Source-available for audit purposes only — see [LICENSE-ORCHESTRATOR.md](LICENSE-ORCHESTRATOR.md)

Contact: anchorflowalertengine@outlook.com
