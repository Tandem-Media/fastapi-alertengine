# FastAPI incident intelligence with human-approved recovery.

**Nothing runs on your servers except the free SDK middleware.**

One line instruments your FastAPI app. When something degrades, AI diagnoses the root cause and sends a WhatsApp or Telegram alert with a tap-to-approve recovery link. The orchestrator calls *your* webhook — it never SSH's into your machines.

Built where WhatsApp is the control plane. Designed for FastAPI teams everywhere.

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

**Try it locally — no orchestrator needed:**

```bash
# Clone the repo and run the demo
git clone https://github.com/Tandem-Media/fastapi-alertengine
cd fastapi-alertengine
pip install fastapi-alertengine uvicorn httpx
uvicorn examples.quickstart_example:app --reload

# In another terminal — simulate a spike
curl -X POST localhost:8000/simulate/spike
curl -s localhost:8000/health/alerts | python3 -m json.tool
```

Watch the health score drop in real time. No account required.

| Endpoint | Description |
|----------|-------------|
| `GET /health/alerts` | Current health status |
| `GET /metrics/history` | Per-minute aggregated metrics |
| `GET /metrics/ingestion` | Ingestion counters |
| `GET /__alertengine/status` | Full engine status |

---

## How It Works

**Free SDK (Steps 1–2) — runs on your servers:**

- **Step 1:** `instrument(app)` — P95 latency tracking, error rate detection, health scoring begins immediately
- **Step 2:** `GET /health/alerts` — returns P95, error rate, health score 0-100, trend direction

**Paid Orchestrator (Steps 3–4) — runs on Tandem Media's servers:**

- **Step 3:** Managed orchestrator polls your `/health/alerts` every 5 seconds. When score drops below threshold, Claude AI diagnoses root cause in plain English.
- **Step 4:** WhatsApp or Telegram alert arrives with diagnosis and a single-use recovery link. You tap approve. Nothing executes without you.


## Architecture

```
Your servers                          Tandem Media servers
─────────────────────────────────     ──────────────────────────────────────
FastAPI app                           Orchestrator (polls every 5s)
  instrument(app)                       ↓ detects degradation
  ↓                                   Claude AI diagnosis
Redis Streams ──→ /health/alerts ──→    ↓ confidence gated
  append-only        P95 · score        WhatsApp / Telegram alert
  event log          · trend              plain English · recovery link
                                          single-use JWT · 5 min TTL
                                          ↓ engineer taps approve
                                        POST /action/recover/confirm
                                          ↓ 3 retries · exponential backoff
                                        Your recovery webhook ←── you control this
                                          ↓
                                        Immutable audit log
                                          every stage · every decision · every approval
```

**Free SDK** (teal) runs on your servers. Zero side effects. Pure measurement.
**Paid orchestrator** (purple) runs on Tandem Media's servers. Never touches your machines directly.
**Human layer** (amber) — nothing executes without your explicit tap-to-approve.
**Execution + audit** (coral) — your webhook runs the fix. Everything is logged immutably.

---

## Compliance Features

AlertEngine applies financial-grade authorization discipline to API infrastructure.
Every design decision maps to a real compliance requirement.

| Compliance requirement | AlertEngine implementation |
|------------------------|---------------------------|
| Human authorization before execution | Engineer must tap approve — no autonomous remediation |
| Immutable audit trail | Append-only Redis log — every stage, decision, and approval recorded |
| Replay attack prevention | Single-use JWT tokens via atomic Redis SET NX |
| Cross-tenant data isolation | Tenant ID validated on every endpoint — 403 on mismatch |
| Separation of duties | Free SDK (data plane) and orchestrator (control plane) are fully isolated |
| Incident documentation | Full timeline reconstructable from audit log — DETECTED → AUTHORIZED → EXECUTED |
| Degraded mode handling | NORMAL / DEGRADED / EMERGENCY with automatic transitions — never crashes |
| Recovery action accountability | Who approved, when, what executed — all logged with timestamps |

**Why this matters:** In regulated industries (fintech, healthtech, logistics), every production action needs a paper trail. AlertEngine produces that trail automatically — no manual logging, no after-the-fact reconstruction.

**The accounting parallel:** I spent my career in finance before building AlertEngine. In accounting, no transaction executes without authorization and every action leaves an audit trail. AlertEngine applies that same discipline to production infrastructure.

---

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

The orchestrator runs as a managed service hosted by Tandem Media.
You never install it on your own infrastructure.

### How recovery works

During onboarding you provide a **recovery webhook URL** — an endpoint
on your own infrastructure that executes the recovery action (restart
a worker, clear a cache, scale a service). You control what the
webhook does. The orchestrator only calls it after you tap approve.

```
Your FastAPI app  ←  instrument(app) — runs on your servers
       ↓
/health/alerts (your servers, read-only, no auth required)
       ↓
AlertEngine Orchestrator  ←  polls every 5s — runs on Tandem Media servers
       ↓
Claude AI diagnosis
       ↓
WhatsApp/Telegram → you tap approve
       ↓
POST your-recovery-webhook.com/action  ←  you control this endpoint
       ↓
Confirmation sent
```

**If your recovery webhook is unavailable when you tap Approve:**
The orchestrator retries 3 times with exponential backoff. On failure,
the incident is captured in the Dead Letter Queue for manual replay.
Developer-tier DLQ entries are visible via `GET /dlq` for 24 hours.
Startup+ plans get full DLQ management and replay tooling.

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
| WhatsApp | Sent.dm | Developer+ | Zero-friction, default WhatsApp provider |
| WhatsApp | Twilio | Developer+ | Enterprise existing Twilio accounts |
| Telegram | Telegram Bot API | All tiers | Developers, no business verification needed |
| Slack | Incoming Webhooks | Startup+ | Team-wide transparency |
| Webhook | HTTP POST | All tiers | Custom routing, PagerDuty fallback |

> Sent.dm is the default WhatsApp provider across all tiers that include WhatsApp.
> Hobby tier is Telegram only — no WhatsApp business account required to get started.
> Enterprise "dedicated deployment" means a separate managed instance hosted by Tandem Media
> under a dedicated SLA — not self-hosted. Contact us to discuss.

---

## Pricing

| Tier | Price | Services | Incidents/mo | Channels |
|------|-------|----------|--------------|----------|
| Hobby | $19/mo | 1 | 5 | Telegram only |
| Developer | $99/mo | 1 | 10 | WhatsApp (via Sent.dm or Twilio) |
| Solo | $299/mo | 3 | 50 | WhatsApp + Telegram |
| Startup | $799/mo | 10 | 200 | WhatsApp + Telegram + Slack |
| Scale | $1,500/mo | 20 | 1,000 | All channels + Voice escalation |
| Enterprise | Custom | Custom | Custom | Custom + dedicated deployment |

---

## Built in Zimbabwe

Engineers here aren't always at laptops when things break.
WhatsApp is the operational control plane.

That constraint produced something better than a dashboard ever could:
alerts that find you, rather than dashboards you have to find.

FastAPI AlertEngine is built for mobile-first operational reality —
and designed for FastAPI teams everywhere who want incident intelligence
without telemetry sprawl.

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
- **Webhook retry** — 3 attempts with exponential backoff on recovery webhook failure

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| REDIS_URL | Yes | Redis connection URL |
| ALERTENGINE_BASE_URL | Yes | **Orchestrator's** public URL — provided after onboarding. Example: `https://your-tenant.alertengine.io` |
| ANTHROPIC_API_KEY | Yes | Claude AI API key |
| ALERT_SECRET | Yes | JWT signing secret |
| TWILIO_ACCOUNT_SID | Twilio only | Twilio account SID |
| TWILIO_AUTH_TOKEN | Twilio only | Twilio auth token |
| TWILIO_WHATSAPP_FROM | Twilio only | Sender WhatsApp number |
| SENT_API_KEY | Sent.dm only | Sent.dm API key |
| SENT_PHONE_ID | Sent.dm only | Sent.dm phone ID |
| LOOP_INTERVAL_S | No | Polling interval seconds (default: 5) |
| POLICY_MIN_SCORE_TO_ALERT | No | Min score to open incident (default: 70) |

> `ALERTENGINE_BASE_URL` is the orchestrator URL you receive after onboarding,
> e.g. `https://your-tenant.alertengine.io`. Your app's `/health/alerts` URL
> is configured separately per-tenant during onboarding.

---

## Repository Structure

```text
fastapi_alertengine/     ← Free SDK — MIT licensed — install this
  middleware.py          ← RequestMetricsMiddleware
  engine.py             ← Core alert engine
  intelligence.py       ← Adaptive thresholds, health scoring
  actions/              ← Recovery suggestions and JWT tokens
  storage.py            ← Redis Streams persistence

orchestrator/           ← Source-available for security audit only
  loop.py              ← Published here for transparency — NOT for self-hosting
  claude_engine.py     ← Runtime is hosted by Tandem Media
  notifications.py     ← See LICENSE-ORCHESTRATOR.md
  audit.py
  plans.py

examples/               ← Demo scripts (try quickstart_example.py)
docs/                   ← Landing page (GitHub Pages)
tests/                  ← 232 tests, Python 3.10/3.11/3.12
```

> The `orchestrator/` source is published here for security audit and transparency.
> It is **not** designed for self-hosting. The production runtime is operated by
> Tandem Media. See [LICENSE-ORCHESTRATOR.md](LICENSE-ORCHESTRATOR.md).

---

## Adversarial Audit

This system was audited by an autonomous AI agent acting
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

**Try locally (clone the repo first):**
```bash
git clone https://github.com/Tandem-Media/fastapi-alertengine
cd fastapi-alertengine
pip install fastapi-alertengine uvicorn httpx
uvicorn examples.quickstart_example:app --reload

# In another terminal
curl -X POST localhost:8000/simulate/spike
curl -s localhost:8000/health/alerts | python3 -m json.tool
```

**Managed orchestrator (Developer — $99/mo):**
Contact: anchorflowalertengine@outlook.com

---

## License + Contact

**Free SDK** (`fastapi_alertengine/`): MIT — see [LICENSE](LICENSE)

**Orchestrator** (`orchestrator/`): Source-available for audit only — see [LICENSE-ORCHESTRATOR.md](LICENSE-ORCHESTRATOR.md)

Contact: anchorflowalertengine@outlook.com
