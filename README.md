# ⚡ fastapi-alertengine

**Your API broke. A customer told you. That's the problem we solve.**

Human-authorized incident recovery for FastAPI — via WhatsApp or Telegram.

[![PyPI](https://img.shields.io/pypi/v/fastapi-alertengine)](https://pypi.org/project/fastapi-alertengine/)
[![Python](https://img.shields.io/pypi/pyversions/fastapi-alertengine)](https://pypi.org/project/fastapi-alertengine/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://github.com/Tandem-Media/fastapi-alertengine/actions/workflows/ci.yml/badge.svg)](https://github.com/Tandem-Media/fastapi-alertengine/actions)

---

## What Is This?

**fastapi-alertengine** is two things:

### 1. Free PyPI Package — `pip install fastapi-alertengine`
Drop-in P95 latency monitoring and error rate detection for FastAPI.
One line of code. No Prometheus. No Grafana. No dashboards.

### 2. Paid Orchestrator — Managed Incident Recovery
An AI-powered orchestration layer that watches your health endpoint,
diagnoses incidents in plain English, and sends you a WhatsApp or
Telegram message with a one-tap recovery link.

**AI detects. You authorize. Nothing runs without your approval.**

---

## Quick Start — Free Package (30 seconds)

```bash
pip install fastapi-alertengine
```

```python
from fastapi import FastAPI
from fastapi_alertengine import instrument

app = FastAPI()
instrument(app)  # that's it
```

Your app now exposes:

| Endpoint | Description |
|----------|-------------|
| `GET /health/alerts` | Current health status |
| `GET /metrics/history` | Per-minute aggregated metrics |
| `GET /metrics/ingestion` | Ingestion counters |
| `GET /__alertengine/status` | Full engine status |

---

## What You Get

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

---

## How It Works
FastAPI Request
↓
RequestMetricsMiddleware  ← measures latency + status
↓
Redis Streams             ← append-only event log
↓
Alert Engine              ← P95 + error rate + anomaly scoring
↓
/health/alerts            ← single status: ok | warning | critical

---

## Features — Free Package

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

---

## Orchestrator — Managed Incident Recovery

The orchestrator is the paid layer on top of the free package.
It polls your health endpoint, runs AI diagnosis via Claude,
and delivers human-authorized recovery via WhatsApp or Telegram.

### How an incident works

Your P95 spikes or error rate climbs
Orchestrator detects it within 5 seconds
Claude diagnoses root cause in plain English
You receive WhatsApp/Telegram: what broke, why, suggested fix
Secure recovery link included (JWT-signed, expires in 5 minutes)
You tap Approve
Fix executes (restart, scale, clear cache)
You receive confirmation when system recovers


Nothing executes without your explicit approval.

### Notification Channels

| Channel | Provider | Plan | Best for |
|---------|----------|------|----------|
| WhatsApp | Sent.dm | Solo (default) | Zero-friction, instant setup |
| WhatsApp | Twilio | All | Enterprise existing accounts |
| Telegram | Telegram Bot API | All | Developers, North America |
| Slack | Incoming Webhooks | Startup+ | Team transparency |
| Webhook | HTTP POST | All | Slack/Teams/PagerDuty fallback |

### Pricing

| Tier | Price | Services | Incidents/mo | Slack | Voice |
|------|-------|----------|--------------|-------|-------|
| Solo | $399 | 1 | 50 | ❌ | ❌ |
| Startup | $799 | 5 | 200 | ✅ | ✅ |
| Scale | $1,500 | 20 | 1,000 | ✅ | ✅ |
| Enterprise | Custom | Unlimited | Unlimited | ✅ | ✅ |

### Orchestrator API

| Method | Path | Description |
|--------|------|-------------|
| GET | /health | Service health + Redis status |
| GET | /status | Active tenants, degraded mode, DLQ, stage gates |
| POST | /onboard | Register a new tenant |
| POST | /verify | Verify WhatsApp number |
| GET | /tenant/{id} | Get tenant status |
| GET | /tenant/{id}/contacts | Get contact verification status |
| POST | /tenant/{id}/test | Trigger test incident |
| GET | /audit/{incident_id} | Incident audit log (requires ?tenant_id=) |
| GET | /delivery/{incident_id} | Delivery log (requires ?tenant_id=) |
| GET | /dlq | Dead letter queue (Startup+ plan required) |
| GET | /action/recover | Human-authorized recovery endpoint |
| POST | /onboarding/activate | Quick-start: activate after test alert |
| GET | /onboarding/status | Quick-start: onboarding status |
| POST | /onboarding/test-alert | Quick-start: send test alert |
| POST | /onboarding/test-connection | Quick-start: verify health URL |

> `/onboarding/*` endpoints are quick-start flows for dev/testing.
> Use `/onboard` + `/verify` for production phone-verified deployments.

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
| ALERTENGINE_BASE_URL | Yes | Public URL of this orchestrator |
| ANTHROPIC_API_KEY | Yes | Claude AI API key |
| ALERT_SECRET | Yes | JWT signing secret |
| TWILIO_ACCOUNT_SID | Twilio only | Twilio account SID |
| TWILIO_AUTH_TOKEN | Twilio only | Twilio auth token |
| TWILIO_WHATSAPP_FROM | Twilio only | Sender WhatsApp number |
| SENT_API_KEY | Sent.dm only | Sent.dm API key |
| SENT_PHONE_ID | Sent.dm only | Sent.dm phone ID |
| LOOP_INTERVAL_S | No | Polling interval seconds (default: 5) |
| POLICY_MIN_SCORE_TO_ALERT | No | Min score to open incident (default: 70) |

---

## Repository Structure
fastapi_alertengine/     ← Free PyPI package
middleware.py          ← RequestMetricsMiddleware
engine.py             ← Core alert engine
intelligence.py       ← Adaptive thresholds, health scoring
actions/              ← Recovery suggestions and JWT tokens
storage.py            ← Redis Streams persistence
orchestrator/           ← Paid managed service
loop.py              ← Multi-tenant polling
pipeline.py          ← Incident state machine
claude_engine.py     ← AI diagnosis
notifications.py     ← Multi-channel dispatch
providers/           ← WhatsApp, Telegram, Slack, Sent.dm, Webhook
audit.py             ← Immutable forensic log
plans.py             ← Billing tiers and feature gates
examples/               ← Demo scripts
docs/                   ← Agent integration guide
tests/                  ← 220+ tests, Python 3.10/3.11/3.12

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

**Free package:**
```bash
pip install fastapi-alertengine
```

**Managed orchestrator (Solo — $399):**
Contact: anchorflow@outlook.com

**Upwork:**
<a href="https://www.upwork.com/services/product/development-it-24-hour-stability-audit-and-an-active-recovery-system-for-instant-control-2042520713072042104">Human-authorized incident recovery for FastAPI</a>

---

## License

MIT — free package only.
The orchestrator is a commercial service.
