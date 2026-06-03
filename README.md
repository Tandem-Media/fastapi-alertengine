# FastAPI AlertEngine

**A compliance-oriented incident command system for FastAPI.**

> **AI explains. Humans authorize. The system proves.**

Deterministic detection. AI-assisted diagnosis. Human authorization. Immutable audit trail.

Nothing executes without your explicit approval. Every action is logged and replayable.

**Nothing runs on your servers except the free SDK middleware.**

[![PyPI](https://img.shields.io/pypi/v/fastapi-alertengine)](https://pypi.org/project/fastapi-alertengine/)
[![Python](https://img.shields.io/pypi/pyversions/fastapi-alertengine)](https://pypi.org/project/fastapi-alertengine/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://github.com/Tandem-Media/fastapi-alertengine/actions/workflows/ci.yml/badge.svg)](https://github.com/Tandem-Media/fastapi-alertengine/actions)
[![RepoRanker](https://reporanker.com/badge/Tandem-Media/fastapi-alertengine)](https://reporanker.com/repos/Tandem-Media/fastapi-alertengine)

---

## Why AlertEngine Exists

Monitoring tools tell you something broke.

Runbooks tell you what to do.

Automation platforms execute actions for you.

AlertEngine sits between all three.

It combines detection, diagnosis, authorization, execution, and audit into a single workflow — with a human in the loop at the authorization step.

The goal is not autonomous remediation. The goal is **accountable remediation**.

---

## The Governance Model

Most monitoring tools detect incidents and alert you. AlertEngine detects, diagnoses, asks permission, executes, and proves it — in that order, every time.

```
Detection    →  Deterministic policy rules. No AI involved.
Diagnosis    →  AI explains what broke and why. Confidence-gated.
Authorization →  Engineer taps approve. Nothing runs without this.
Execution    →  Your recovery webhook is called. 3 retries. DLQ on failure.
Audit        →  Append-only log. Every stage. Every actor. Replayable.
```

This hierarchy is enforced by the architecture, not by convention:

- `policy.py` decides whether an incident exists — Claude does not
- `pipeline.py` owns state transitions — Claude does not
- `action_generator.py` gates execution behind a signed JWT — Claude does not
- `audit.py` records everything regardless of outcome

**AI explains. Humans authorize. The system proves.**

---

## What an Incident Looks Like

```
🚨 Checkout API degraded
Health score: 23/100 | P95: 2.8s | Errors: 19%

Both models agree.

Likely cause:
Database connection pool exhausted — connections
not being released after query timeout.

Recent deployment:
3 minutes ago — a1b2c3d
"Fix checkout query isolation level" (John, +12/-3)
⚠️ This commit touched database/query files

Suggested fix:
Restart checkout worker pool

Confidence: 87%

[Approve fix]  Nothing will run without your approval.
```

One message. Everything you need to make a decision. Nothing executes until you tap approve.

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

## Proof Strip

**Production Proven**
- Live production tenant: fintech platform, Zimbabwe
- Human-authorized recovery confirmed end-to-end

**Security Verified**
- 232 tests passing (Python 3.10, 3.11, 3.12)
- Adversarial audit by autonomous AI agent: 10/10 passed
  (replay attacks, cross-tenant isolation, concurrent token floods)

**Code Transparency**
- 17 orchestrator modules, ~3,500 lines of defensive Python
- Every module includes graceful degradation and never-raises guarantees
- Every README claim verified against source code — zero stubs, zero aspirational features
- Complete actor attribution: policy · claude · engineer · orchestrator
- Source-available for independent security audit — see `LICENSE-ORCHESTRATOR.md`

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

**Paid Orchestrator (Steps 3–6) — runs on Tandem Media's servers:**

- **Step 3:** Managed orchestrator polls `/health/alerts` every 5 seconds. Deterministic policy gates run first. If all gates pass, Claude AI diagnoses root cause in plain English.
- **Step 4:** WhatsApp or Telegram alert arrives with AI diagnosis and a single-use recovery link.
- **Step 5:** You tap approve. Nothing executes without you.
- **Step 6:** Your recovery webhook executes. Every stage is logged immutably.

---

## Architecture

```
Your servers                          Tandem Media servers
─────────────────────────────────     ──────────────────────────────────────
FastAPI app                           Orchestrator (polls every 5s)
  instrument(app)                       ↓ policy gates (deterministic)
  ↓                                   ↓ Claude AI diagnosis (advisory only)
Redis Streams ──→ /health/alerts ──→    ↓ confidence-gated
  append-only        P95 · score        WhatsApp / Telegram alert
  event log          · trend              AI diagnosis · recovery link
                                          single-use JWT · 5 min TTL
                                          ↓ engineer taps approve
                                        POST /action/recover/confirm
                                          ↓ 3 retries · exponential backoff
                                        Your recovery webhook ←── you control this
                                          ↓
                                        Immutable audit log
                                          every stage · every actor · replayable
```

**The real moat is not the AI.** It is the governance layer: `incident_policy.py`, `audit.py`, `delivery_ledger.py`, `idempotency.py`, and the human-approval workflow. Together they create a system that can explain, authorize, execute, and prove operational decisions afterward.

Every design principle is enforced by code and provable by audit:

| Principle | Enforcement |
|-----------|-------------|
| Policy decides incidents, not AI | `should_recover()` in `pipeline.py` sets `actor="policy"` |
| AI explains, humans authorize | Claude generates message; JWT gates execution |
| Nothing executes without approval | `POST /action/recover/confirm` requires valid JWT |
| Every action logged immutably | `append_event()` on every transition, every actor |
| Deterministic alert rules | `incident_policy.py` — single versioned POLICY dict |

---

## Local Incident Sensing — Free Forever

### Core Features

- **P95 latency tracking** — not averages, real percentiles
- **Error rate detection** — 4xx/5xx with configurable thresholds
- **Anomaly scoring** — detects spikes vs your baseline
- **Health score 0-100** — composite score with trend direction

### Advanced Features

- **Adaptive thresholds** — learns your normal traffic pattern
- **Rate-of-change detection** — catches sudden spikes below absolute thresholds
- **Action suggestions** — maps health score to notify, alert, restart
- **Incident replay** — reconstruct state from append-only audit log
- **Circuit breaker** — buffers events during Redis outages; never drops metrics
- **Memory mode** — SDK never crashes when Redis is unavailable
- **AI-agent friendly** — clean JSON API, works with Claude/Copilot/Cursor

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

**If your recovery webhook is unavailable when you tap Approve:**
The orchestrator retries 3 times with 2s/4s exponential backoff. On failure,
the incident is captured in the Dead Letter Queue for manual replay.

### How an incident works

1. Your P95 spikes or error rate climbs
2. Orchestrator detects it within 5 seconds
3. Policy gates run — quota, plan limits, degraded mode
4. Claude diagnoses root cause in plain English (confidence-gated)
5. You receive WhatsApp/Telegram: what broke, why, suggested fix
6. Secure recovery link included (JWT-signed, expires in 5 minutes)
7. You tap Approve
8. Your recovery webhook executes
9. Every stage logged immutably

### Diagnostic Council

Two AI models with different diagnostic lenses analyze each incident independently:

- **Model A** (Haiku) — latency and database specialist
- **Model B** (Sonnet) — network and dependency specialist

If they **agree** → one clean alert with "both models agree"

If they **diverge** → Dissent Alert:
```
⚠️ Degraded State — Models Disagree
Theory A (Database): Connection pool exhausted (82%)
Theory B (Network): Upstream API timeout (76%)

Check: DB slow query log vs upstream response times

👉 Trust Theory A  👉 Trust Theory B
Nothing will run without your approval.
```

### Diff-in-Pocket

Incidents are correlated with recent git commits:

```
Recent deployments before incident:
  3m ago — a1b2c3d: "Fix checkout query isolation level" (John, +12/-3)
  ⚠️ 1 commit touched database/query files
```

Set up via GitHub webhook → `POST /commits/webhook`.

### Notification Channels

| Channel | Provider | Plan | Best for |
|---------|----------|------|----------|
| WhatsApp | Sent.dm | Developer+ | Zero-friction, default provider |
| WhatsApp | Twilio | Developer+ | Enterprise existing accounts |
| Telegram | Telegram Bot API | All tiers | No business verification needed |
| Slack | Incoming Webhooks | Startup+ | Team-wide transparency |
| Webhook | HTTP POST | All tiers | Custom routing, PagerDuty fallback |

---

## Pricing

| Tier | Price | Services | Incidents/mo | Channels |
|------|-------|----------|--------------|----------|
| **Free** | $0 | — | — | SDK only |
| **Starter** | $19/mo | 1 | 5 | Telegram |
| **Growth** | $99/mo | 1 | 10 | WhatsApp + AI diagnosis |
| **Team** | $299/mo | 3 | 50 | WhatsApp + Telegram + Council |
| **Compliance** | $799/mo | 10 | 200 | + Slack + DLQ + Voice + Audit export |
| **Platform** | $1,500/mo | 20 | 1,000 | All channels + Custom policy thresholds |
| **Enterprise** | Custom | Unlimited | Unlimited | Dedicated deployment + Custom SLA |

### What each tier actually buys you

**Free — $0**
Detection SDK. MIT licensed. Runs on your servers. P95 tracking, health score, anomaly detection.
*The catch: You see the score drop. You don't know why. You don't get alerts. You don't get recovery links. That's the orchestrator.*

**Starter — $19/mo**
Your first production app. Telegram alerts. Basic detection.
*One hour of downtime costs more than a year of Starter.*
Best for: Pre-revenue founders, indie hackers, first production deployment.

**Growth — $99/mo**
AI diagnosis. WhatsApp. Actionable alerts. No noise.
Claude diagnoses root cause in plain English. Confidence-gated — suppresses noise below 60%. Diff-in-Pocket commit correlation included.
*One false-positive 3 AM alert costs more than a month of Growth.*
Best for: Seed-stage teams, solo developers with revenue, first on-call rotation.

**Team — $299/mo**
Multi-service. Full channels. Diagnostic Council.
3 services, 50 incidents, WhatsApp + Telegram. Dual-model AI — two models reason independently. Dissent alerts when models disagree.
*$6 per incident for AI diagnosis + human authorization + audit trail.*
Best for: Solo founders with revenue ($5K–$50K MRR), consultants managing multiple client apps.

**Compliance — $799/mo**
SOC 2 ready. DLQ. Voice escalation. Team transparency.
10 services, 200 incidents. Slack integration, Dead Letter Queue, voice escalation after 180s, full audit trail export, policy version tracking.
*SOC 2 Type II audit costs $15,000–$50,000. Compliance is $799/month — insurance against that delay.*
Best for: Series A fintech, healthtech approaching HIPAA, any team where auditors ask "who approved that?"

**Platform — $1,500/mo**
Custom policy thresholds. 20 services. Enterprise-grade.
Custom POLICY_RECOVER_SCORE, POLICY_VALIDATE_ERROR_RATE adapted to your baselines. Custom webhook routing. Priority support (24-hour response).
*Generic thresholds don't work at scale — your P95 normal might be 200ms, not 120ms.*
Best for: Multi-team platforms, African fintech with 100K+ users, teams with established operational baselines.

**Enterprise — Custom**
Dedicated deployment. Custom SLA. Procurement-ready.
Unlimited services and incidents. Dedicated managed instance. Data residency options. Annual contracts, POs, vendor security questionnaires. White-glove onboarding.
*Enterprise monitoring contracts run $50,000–$500,000/year. AlertEngine Enterprise is a fraction of that, with human authorization and audit trails they don't have.*
Best for: Banks, insurance companies, health systems, government agencies, African CBDC infrastructure.

---

## Built in Zimbabwe

Engineers here aren't always at laptops when things break.
WhatsApp is the operational control plane.

That constraint produced something better than a dashboard ever could:
alerts that find you, rather than dashboards you have to find.

I spent my career in accounting and finance before building AlertEngine.
In finance, no transaction executes without authorization and every
action leaves an audit trail. AlertEngine applies that same discipline
to production infrastructure.

---

## Compliance Features

| Requirement | Implementation |
|-------------|---------------|
| Human authorization before execution | Engineer must tap approve — no autonomous remediation |
| Immutable audit trail | Append-only Redis log — every stage, decision, and approval |
| Replay attack prevention | Single-use JWT tokens via atomic Redis SET NX |
| Cross-tenant data isolation | Tenant ID validated on every endpoint — 403 on mismatch |
| Separation of duties | Free SDK (data plane) and orchestrator (control plane) isolated |
| Incident documentation | Full timeline reconstructable from audit log |
| Degraded mode handling | NORMAL / DEGRADED / EMERGENCY with automatic transitions |
| Recovery accountability | Who approved, when, what executed — all timestamped |
| Deterministic alert rules | Single policy file; versionable; env-configurable |

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
- **Webhook retry** — 3 attempts with exponential backoff
- **Baseline hygiene** — updated only on healthy polls, never during incidents
- **Fail-safe AI** — Claude unavailable → suppress with 0% confidence

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| REDIS_URL | Yes | Redis connection URL |
| ALERTENGINE_BASE_URL | Yes | Orchestrator's public URL — e.g. `https://your-tenant.alertengine.io` |
| ANTHROPIC_API_KEY | Yes | Claude AI API key |
| ALERT_SECRET | Yes | JWT signing secret |
| TWILIO_ACCOUNT_SID | Twilio only | Twilio account SID |
| TWILIO_AUTH_TOKEN | Twilio only | Twilio auth token |
| TWILIO_WHATSAPP_FROM | Twilio only | Sender WhatsApp number |
| SENT_API_KEY | Sent.dm only | Sent.dm API key |
| SENT_PHONE_ID | Sent.dm only | Sent.dm phone ID |
| LOOP_INTERVAL_S | No | Polling interval seconds (default: 5) |
| POLICY_MIN_SCORE_TO_ALERT | No | Min score to open incident (default: 70) |
| COUNCIL_ENABLED | No | Dual-model diagnosis (default: true) |
| GITHUB_TOKEN | No | GitHub API for Diff-in-Pocket commit context |

> `ALERTENGINE_BASE_URL` is the orchestrator URL you receive after onboarding.
> Your app's `/health/alerts` URL is configured per-tenant during onboarding.

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
  pipeline.py          ← Incident state machine + IncidentStage enum
  incident_policy.py   ← Single source of truth for all thresholds
  claude_engine.py     ← AI diagnosis (tool use, few-shot, hardened)
  diagnostic_council.py ← Dual-model incident court
  commit_context.py    ← Diff-in-Pocket commit correlation
  baseline.py          ← Per-tenant EMA baseline memory
  diagnosis_memory.py  ← Multi-turn diagnosis history
  audit.py             ← Immutable forensic log
  notifications.py     ← Multi-channel dispatch
  action_generator.py  ← JWT recovery token creation
  safe_payload.py      ← Schema drift protection
  plans.py             ← Billing tiers and feature gates
  See LICENSE-ORCHESTRATOR.md

examples/               ← Demo scripts (try quickstart_example.py)
docs/                   ← Architecture docs + landing page
tests/                  ← 232 tests, Python 3.10/3.11/3.12
```

> The `orchestrator/` source is published for security audit and transparency.
> It is **not** designed for self-hosting. Runtime is operated by Tandem Media.
> See [LICENSE-ORCHESTRATOR.md](LICENSE-ORCHESTRATOR.md).

---

## Adversarial Audit

This system was audited by an autonomous AI agent acting as a hostile
tenant attempting to break isolation, bypass human authorization, and
overwhelm the system with concurrent requests.

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

**Managed orchestrator (Growth — $99/mo):**
Contact: anchorflowalertengine@outlook.com

Ready for accountable incident response? We'll configure your policy file, webhook, and first tenant.

**Full technical architecture:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

**Upwork:**
[Human-authorized incident recovery for FastAPI](https://www.upwork.com/services/product/development-it-24-hour-stability-audit-and-an-active-recovery-system-for-instant-control-2042520713072042104)

---

## FAQ

**Can I self-host the orchestrator?**
No. The orchestrator is source-available for audit, hosted and managed by Tandem Media. Enterprise gets a dedicated deployment under a custom SLA.

**What happens if Claude is unavailable?**
The system fails safe — falls back to deterministic policy rules. The audit log records `actor: "policy"`. No silent failures.

**What happens if my recovery webhook is down?**
The orchestrator retries 3 times with exponential backoff. On failure, the incident is captured in the Dead Letter Queue for manual replay. Available on Compliance tier and above.

**Can I start free and upgrade?**
Yes. `pip install fastapi-alertengine` is MIT licensed and never expires. The free SDK runs forever on your servers. Upgrade to a managed tier whenever you need alerts and diagnosis.

**Is the audit trail really immutable?**
Yes. `audit.py` uses Redis LIST with `rpush` — append only, never mutated. Every event includes actor, stage, confidence, reason, and policy version. Replay reconstructs state from events, not from stored state.

**How does pricing work if I exceed my incident quota?**
Growth and Starter: no overage — incidents are silently counted but not billed beyond quota (upgrade required for more). Team: $0.10/incident over 50. Compliance: $0.05/incident over 200. Platform: $0.02/incident over 1,000.

---

## License + Contact

**Free SDK** (`fastapi_alertengine/`): MIT — see [LICENSE](LICENSE)

**Orchestrator** (`orchestrator/`): Source-available for audit only — see [LICENSE-ORCHESTRATOR.md](LICENSE-ORCHESTRATOR.md)

Contact: anchorflowalertengine@outlook.com
