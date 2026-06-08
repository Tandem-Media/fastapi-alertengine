FastAPI AlertEngine

Monitoring tools detect failures.

AlertEngine records how humans respond to them.

Human-authorized incident recovery for production APIs.

Metastability Defense: AlertEngine's human-in-the-loop authorization breaks the metastable feedback loops that automated remediation amplifies in agent-driven workloads. Peer-reviewed research (Demirbas et al., ACM CAIS 2026) shows AI agents create \~50x more rollbacks than human clients — their aggressive retry behavior turns automated recovery into a feedback amplifier. Human authorization is not a limitation. It is a resilience mechanism. Read the full analysis

Why AlertEngine Exists

Monitoring tools tell you something broke.

Runbooks tell you what to do. Automation platforms execute fixes. Neither tells you who authorized the fix, or leaves a record an auditor can replay.

AlertEngine sits between detection and execution — enforcing that every recovery action is authorized by a human, logged immutably, and replayable by an auditor.

The goal is not autonomous remediation. The goal is accountable remediation.

The Governance Model

Most monitoring tools detect incidents and alert you. AlertEngine detects, diagnoses, asks permission, executes, and proves it — in that order, every time.

plain

Detection    →  Deterministic policy rules. No AI involved.

Diagnosis    →  AI explains what broke and why. Confidence-gated.

Authorization →  Engineer taps approve. Nothing runs without this.

Execution    →  Your recovery webhook is called. 3 retries. DLQ on failure.

Audit        →  Append-only log. Every stage. Every actor. Replayable.

This hierarchy is enforced by the architecture, not by convention:

policy.py decides whether an incident exists — Claude does not

pipeline.py owns state transitions — Claude does not

action\_generator.py gates execution behind a signed JWT — Claude does not

audit.py records everything regardless of outcome

AI explains. Humans authorize. The system proves.

What an Incident Looks Like

plain

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



\[Approve fix]  Nothing will run without your approval.

(Requires GitHub webhook — POST /commits/webhook)

One message. Everything you need to make a decision. Nothing executes until you tap approve.

If the two AI models disagree, you receive a Dissent Alert instead — two competing theories, confidence scores, and specific logs to check before approving. See Diagnostic Council below.

Human-Authorized. Always.

Nothing executes without your explicit approval.

Every action is logged immutably.

The system fails safe — never fails open.

GET /action/recover — preview only, zero side effects

POST /action/recover/confirm — irreversible, requires valid JWT

JWT tokens: tenant-scoped, 5-minute TTL, single-use

Replay protection: atomic Redis SET NX

Immutable audit trail on every stage transition

Adversarial audit: 10/10 checks passed

Proof Strip

Production Proven

Live production tenant: fintech platform, Zimbabwe

Human-authorized recovery confirmed end-to-end

Security Verified

232 tests passing (Python 3.10, 3.11, 3.12)

Adversarial audit by autonomous AI agent: 10/10 passed

(replay attacks, cross-tenant isolation, concurrent token floods)

Code Transparency

17 orchestrator modules, \~3,500 lines of defensive Python

Every module includes graceful degradation and never-raises guarantees

Every README claim verified against source code — zero stubs, zero aspirational features

Complete actor attribution: policy · diagnosis · engineer · orchestrator

Source-available for independent security audit — see LICENSE-ORCHESTRATOR.md

Install + Quickstart

bash

pip install fastapi-alertengine

Python

from fastapi import FastAPI

from fastapi\_alertengine import instrument



app = FastAPI()

instrument(app)  # that's it

Your app now exposes /health/alerts.

Try it locally — no orchestrator needed:

bash

\# Clone the repo and run the demo

git clone https://github.com/Tandem-Media/fastapi-alertengine

cd fastapi-alertengine

pip install fastapi-alertengine uvicorn httpx

uvicorn examples.quickstart\_example:app --reload



\# In another terminal — simulate a spike

curl -X POST localhost:8000/simulate/spike

curl -s localhost:8000/health/alerts | python3 -m json.tool

Table

Endpoint	Description

GET /health/alerts	Current health status

GET /metrics/history	Per-minute aggregated metrics

GET /metrics/ingestion	Ingestion counters

GET /\_\_alertengine/status	Full engine status

How It Works

Free SDK (Steps 1–2) — runs on your servers:

Step 1: instrument(app) — P95 latency tracking, error rate detection, health scoring begins immediately

Step 2: GET /health/alerts — returns P95, error rate, health score 0-100, trend direction

Paid Orchestrator (Steps 3–6) — runs on Tandem Media's servers:

Step 3: Managed orchestrator polls /health/alerts every 5 seconds. Deterministic policy gates run first. If all gates pass, Claude AI diagnoses root cause in plain English.

Step 4: WhatsApp or Telegram alert arrives with AI diagnosis and a single-use recovery link.

Step 5: You tap approve. Nothing executes without you.

Step 6: Your recovery webhook executes. Every stage is logged immutably.

Architecture

plain

Your servers                          Tandem Media servers

─────────────────────────────────     ──────────────────────────────────────

FastAPI app                           Orchestrator (polls every 5s)

&#x20; instrument(app)                       ↓ policy gates (deterministic)

&#x20; ↓                                   ↓ AI diagnosis (advisory only)

Redis Streams ──→ /health/alerts ──→    ↓ confidence-gated

&#x20; append-only        P95 · score        WhatsApp / Telegram alert

&#x20; event log          · trend              diagnosis · recovery link

&#x20;                                         single-use JWT · 5 min TTL

&#x20;                                         ↓ engineer taps approve

&#x20;                                       POST /action/recover/confirm

&#x20;                                         ↓ 3 retries · exponential backoff

&#x20;                                       Your recovery webhook ←── you control this

&#x20;                                         ↓

&#x20;                                       Immutable audit log

&#x20;                                         every stage · every actor · replayable

Architecture \& Auditability

AlertEngine treats every incident as a transaction — not a notification. Like a financial ledger, every stage is recorded with an immutable audit entry showing the actor, timestamp, and policy version.

plain

\[\*] ──→ DETECTED ──→ PROPOSED ──→ VALIDATED ──→ AUTHORIZED ──→ EXECUTED ──→ RESOLVED ──→ \[\*]

&#x20;           │              │             │                                    │

&#x20;           └──────────────┴─────────────┴── RECOVERED ──→ \[\*]  (policy override)

&#x20;                                        │

&#x20;                                        └── EXPIRED (JWT TTL)     WEBHOOK\_FAILED ──→ DLQ

Full state machine with transition guards: docs/ARCHITECTURE.md

Actor attribution on every transition:

Table

Actor	When	Example

policy	Hard thresholds override AI	should\_recover() → RECOVERED

claude	AI diagnosis and recommendation	"Database connection pool exhausted"

engineer	Human authorization	Taps "Approve" on WhatsApp

orchestrator	State machine execution	Webhook called, transition applied

Every transition is logged with actor, confidence, reason, and policy version.

State is derived from events — not stored as truth.

Redis loss → full replay from the audit ledger.

Why this matters for compliance: "The system fixed itself" is not an acceptable answer. AlertEngine produces: "Engineer X authorized action Y at time Z under policy version W."

The moat is the governance layer: incident\_policy.py, audit.py, delivery\_ledger.py, idempotency.py, and the human-approval workflow. Together they create a system that can explain, authorize, execute, and prove operational decisions afterward — with or without AI involvement.

Every design principle is enforced by code and provable by audit:

Table

Principle	Enforcement

Policy decides incidents, not AI	should\_recover() in pipeline.py sets actor="policy"

AI explains, humans authorize	Claude generates message; JWT gates execution

Nothing executes without approval	POST /action/recover/confirm requires valid JWT

Every action logged immutably	append\_event() on every transition, every actor

Deterministic alert rules	incident\_policy.py — single versioned POLICY dict

Local Incident Sensing — Free Forever

Core Features

P95 latency tracking — not averages, real percentiles

Error rate detection — 4xx/5xx with configurable thresholds

Anomaly scoring — detects spikes vs your baseline

Health score 0-100 — composite score with trend direction

Advanced Features

Adaptive thresholds — learns your normal traffic pattern

Rate-of-change detection — catches sudden spikes below absolute thresholds

Action suggestions — maps health score to notify, alert, restart

Incident replay — reconstruct state from append-only audit log

Circuit breaker — buffers events during Redis outages; never drops metrics

Memory mode — SDK never crashes when Redis is unavailable

AI-agent friendly — clean JSON API, works with Claude/Copilot/Cursor

What You Get

JSON

{

&#x20; "status": "critical",

&#x20; "health\_score": {"score": 23, "status": "critical", "trend": "degrading"},

&#x20; "metrics": {

&#x20;   "overall\_p95\_ms": 2847.3,

&#x20;   "error\_rate": 0.19,

&#x20;   "anomaly\_score": 1.4,

&#x20;   "sample\_size": 187

&#x20; },

&#x20; "alerts": \[

&#x20;   {

&#x20;     "type": "latency\_spike",

&#x20;     "severity": "critical",

&#x20;     "reason\_for\_trigger": "P95 latency 2847ms exceeds threshold 3000ms",

&#x20;     "triggered\_by": "absolute\_threshold"

&#x20;   }

&#x20; ]

}

Pipeline

plain

FastAPI Request

↓

RequestMetricsMiddleware  ← measures latency + status

↓

Redis Streams             ← append-only event log

↓

Alert Engine              ← P95 + error rate + anomaly scoring

↓

/health/alerts            ← single status: ok | warning | critical

Managed Incident Command — Paid

The orchestrator runs as a managed service hosted by Tandem Media.

You never install it on your own infrastructure.

How recovery works

During onboarding you provide a recovery webhook URL — an endpoint

on your own infrastructure that executes the recovery action (restart

a worker, clear a cache, scale a service). You control what the

webhook does. The orchestrator only calls it after you tap approve.

If your recovery webhook is unavailable when you tap Approve:

The orchestrator retries 3 times with 2s/4s exponential backoff. On failure,

the incident is captured in the Dead Letter Queue for manual replay.

How an incident works

Your P95 spikes or error rate climbs

Orchestrator detects it within 5 seconds

Policy gates run — quota, plan limits, degraded mode

Claude diagnoses root cause in plain English (confidence-gated)

You receive WhatsApp/Telegram: what broke, why, suggested fix

Secure recovery link included (JWT-signed, expires in 5 minutes)

You tap Approve

Your recovery webhook executes

Every stage logged immutably

Diagnostic Council

Two AI models with different diagnostic lenses analyze each incident independently:

Model A (Haiku) — latency and database specialist

Model B (Sonnet) — network and dependency specialist

If they agree → one clean alert with "both models agree"

If they diverge → Dissent Alert:

plain

⚠️ Degraded State — Models Disagree

Theory A (Database): Connection pool exhausted (82%)

Theory B (Network): Upstream API timeout (76%)



Check: DB slow query log vs upstream response times



👉 Trust Theory A  👉 Trust Theory B

Nothing will run without your approval.

Diff-in-Pocket

Incidents are correlated with recent git commits:

plain

Recent deployments before incident:

&#x20; 3m ago — a1b2c3d: "Fix checkout query isolation level" (John, +12/-3)

&#x20; ⚠️ 1 commit touched database/query files

Set up via GitHub webhook → POST /commits/webhook.

Notification Channels

Table

Channel	Provider	Plan	Best for

WhatsApp	Sent.dm	Developer+	Zero-friction, default provider

WhatsApp	Twilio	Developer+	Enterprise existing accounts

Telegram	Telegram Bot API	All tiers	No business verification needed

Slack	Incoming Webhooks	Startup+	Team-wide transparency

Webhook	HTTP POST	All tiers	Custom routing, PagerDuty fallback

Pricing

Table

Tier	Price	Services	Incidents/mo	Channels

Free	$0	—	—	SDK only

Starter	$19/mo	1	5	Telegram

Growth	$99/mo	1	10	WhatsApp + AI diagnosis

Team	$299/mo	3	50	WhatsApp + Telegram + Council

Compliance	$799/mo	10	200	+ Slack + DLQ + Voice + Audit export

Platform	$1,500/mo	20	1,000	All channels + Custom policy thresholds

Enterprise	Custom	Unlimited	Unlimited	Dedicated deployment + Custom SLA

What each tier actually buys you

Free — $0

Detection SDK. MIT licensed. Runs on your servers. P95 tracking, health score, anomaly detection.

The catch: You see the score drop. You don't know why. You don't get alerts. You don't get recovery links. That's the orchestrator.

Starter — $19/mo

Your first production app. Telegram alerts. Basic detection.

One hour of downtime costs more than a year of Starter.

Best for: Pre-revenue founders, indie hackers, first production deployment.

Growth — $99/mo

AI diagnosis. WhatsApp. Actionable alerts. No noise.

Claude diagnoses root cause in plain English. Confidence-gated — suppresses noise below 60%. Diff-in-Pocket commit correlation included.

One false-positive 3 AM alert costs more than a month of Growth.

Best for: Seed-stage teams, solo developers with revenue, first on-call rotation.

Team — $299/mo

Multi-service. Full channels. Diagnostic Council.

3 services, 50 incidents, WhatsApp + Telegram. Dual-model AI — two models reason independently. Dissent alerts when models disagree.

$6 per incident for AI diagnosis + human authorization + audit trail.

Best for: Solo founders with revenue ($5K–$50K MRR), consultants managing multiple client apps.

Compliance — $799/mo

SOC 2 ready. DLQ. Voice escalation. Team transparency.

10 services, 200 incidents. Slack integration, Dead Letter Queue, voice escalation after 180s, full audit trail export, policy version tracking.

SOC 2 Type II audit costs $15,000–$50,000. Compliance is $799/month — insurance against that delay.

Best for: Series A fintech, healthtech approaching HIPAA, any team where auditors ask "who approved that?"

Platform — $1,500/mo

Custom policy thresholds. 20 services. Enterprise-grade.

Custom POLICY\_RECOVER\_SCORE, POLICY\_VALIDATE\_ERROR\_RATE adapted to your baselines. Custom webhook routing. Priority support (24-hour response).

Generic thresholds don't work at scale — your P95 normal might be 200ms, not 120ms.

Best for: Multi-team platforms, African fintech with 100K+ users, teams with established operational baselines.

Enterprise — Custom

Dedicated deployment. Custom SLA. Procurement-ready.

Unlimited services and incidents. Dedicated managed instance. Data residency options. Annual contracts, POs, vendor security questionnaires. White-glove onboarding.

Enterprise monitoring contracts run $50,000–$500,000/year. AlertEngine Enterprise is a fraction of that, with human authorization and audit trails they don't have.

Best for: Banks, insurance companies, health systems, government agencies, African CBDC infrastructure.

Built in Zimbabwe

Engineers here aren't always at laptops when things break.

WhatsApp is the operational control plane.

That constraint produced something better than a dashboard ever could:

alerts that find you, rather than dashboards you have to find.

I spent my career in accounting and finance before building AlertEngine.

In finance, no transaction executes without authorization and every

action leaves an audit trail. AlertEngine applies that same discipline

to production infrastructure.

Compliance Features

Table

Requirement	Implementation

Human authorization before execution	Engineer must tap approve — no autonomous remediation

Immutable audit trail	Append-only Redis log — every stage, decision, and approval

Replay attack prevention	Single-use JWT tokens via atomic Redis SET NX

Cross-tenant data isolation	Tenant ID validated on every endpoint — 403 on mismatch

Separation of duties	Free SDK (data plane) and orchestrator (control plane) isolated

Incident documentation	Full timeline reconstructable from audit log

Degraded mode handling	NORMAL / DEGRADED / EMERGENCY with automatic transitions

Recovery accountability	Who approved, when, what executed — all timestamped

Deterministic alert rules	Single policy file; versionable; env-configurable

Reliability Guarantees

Duplicate incident prevention — tenant-scoped lock + idempotency

Replay protection — JWT tokens single-use, atomic Redis SET NX

Distributed locking — Lua script atomic release, no race conditions

Tenant isolation — cross-tenant data access returns 403

Audit trail — every stage transition and recovery authorization logged

Degraded mode — NORMAL / DEGRADED / EMERGENCY with auto-recovery

Dead letter queue — unrecoverable failures captured for replay

Circuit breaker — per-provider per-tenant, Redis-backed

Webhook retry — 3 attempts with exponential backoff

Baseline hygiene — updated only on healthy polls, never during incidents

Fail-safe AI — Claude unavailable → suppress with 0% confidence

Environment Variables

Table

Variable	Required	Description

REDIS\_URL	Yes	Redis connection URL

ALERTENGINE\_BASE\_URL	Yes	Orchestrator's public URL — e.g. https://your-tenant.alertengine.io

ANTHROPIC\_API\_KEY	Yes	Claude AI API key

ALERT\_SECRET	Yes	JWT signing secret

TWILIO\_ACCOUNT\_SID	Twilio only	Twilio account SID

TWILIO\_AUTH\_TOKEN	Twilio only	Twilio auth token

TWILIO\_WHATSAPP\_FROM	Twilio only	Sender WhatsApp number

SENT\_API\_KEY	Sent.dm only	Sent.dm API key

SENT\_PHONE\_ID	Sent.dm only	Sent.dm phone ID

LOOP\_INTERVAL\_S	No	Polling interval seconds (default: 5)

POLICY\_MIN\_SCORE\_TO\_ALERT	No	Min score to open incident (default: 70)

COUNCIL\_ENABLED	No	Dual-model diagnosis (default: true)

GITHUB\_TOKEN	No	GitHub API for Diff-in-Pocket commit context

ALERTENGINE\_BASE\_URL is the orchestrator URL you receive after onboarding.

Your app's /health/alerts URL is configured per-tenant during onboarding.

Repository Structure

Text

fastapi\_alertengine/     ← Free SDK — MIT licensed — install this

&#x20; middleware.py          ← RequestMetricsMiddleware

&#x20; engine.py             ← Core alert engine

&#x20; intelligence.py       ← Adaptive thresholds, health scoring

&#x20; actions/              ← Recovery suggestions and JWT tokens

&#x20; storage.py            ← Redis Streams persistence



orchestrator/           ← Source-available for security audit only

&#x20; loop.py              ← Published here for transparency — NOT for self-hosting

&#x20; pipeline.py          ← Incident state machine + IncidentStage enum

&#x20; incident\_policy.py   ← Single source of truth for all thresholds

&#x20; claude\_engine.py     ← AI diagnosis (tool use, few-shot, hardened)

&#x20; diagnostic\_council.py ← Dual-model incident court

&#x20; commit\_context.py    ← Diff-in-Pocket commit correlation

&#x20; baseline.py          ← Per-tenant EMA baseline memory

&#x20; diagnosis\_memory.py  ← Multi-turn diagnosis history

&#x20; audit.py             ← Immutable forensic log

&#x20; notifications.py     ← Multi-channel dispatch

&#x20; action\_generator.py  ← JWT recovery token creation

&#x20; safe\_payload.py      ← Schema drift protection

&#x20; plans.py             ← Billing tiers and feature gates

&#x20; See LICENSE-ORCHESTRATOR.md



examples/               ← Demo scripts (try quickstart\_example.py)

docs/                   ← Architecture docs + landing page

tests/                  ← 232 tests, Python 3.10/3.11/3.12

The orchestrator/ source is published for security audit and transparency.

It is not designed for self-hosting. Runtime is operated by Tandem Media.

See LICENSE-ORCHESTRATOR.md.

Adversarial Audit

This system was audited by an autonomous AI agent acting as a hostile

tenant attempting to break isolation, bypass human authorization, and

overwhelm the system with concurrent requests.

Result: 10/10 live checklist checks passed.

Cross-tenant isolation: blocked (403 returned)

Replay attack (20 concurrent): exactly 1 succeeded, 19 rejected

Natural incident detection: confirmed working

Recovery authorization audit trail: confirmed

DLQ plan enforcement: confirmed

Get Started

Free SDK:

bash

pip install fastapi-alertengine

Managed orchestrator (Growth — $99/mo):

Contact: anchorflowalertengine@outlook.com

Ready for accountable incident response? We'll configure your policy file, webhook, and first tenant.

Full technical architecture: docs/ARCHITECTURE.md

Need a custom integration or white-glove onboarding?

Available on Upwork

Roadmap

AlertEngine is evolving through four phases:

Phase 1 — Alert Detection ✅ Complete

P95 latency tracking, error rate detection, health scoring, anomaly detection. Free SDK, MIT licensed.

Phase 2 — Incident Orchestration ✅ Complete

Deterministic policy gates, AI-assisted diagnosis, human authorization, webhook execution, immutable audit trail. Managed orchestrator, live in production.

Phase 3 — Decision Governance ✅ In progress

Diagnostic Council (dual-model adversarial deliberation, live — COUNCIL\_ENABLED=true by default), Diff-in-Pocket commit correlation, policy versioning, actor attribution, Auditor's One-Pager PDF. The audit trail as a compliance asset. Human authorization as metastability defense (Demirbas et al., ACM CAIS 2026).

Phase 4 — Governance Simulation 🔭 Future direction

Before trusting a process during an emergency, test the process itself.

AlertEngine is already built around explicit policies, deterministic state transitions, and an immutable event history. These are the exact ingredients needed for simulation. A future Policy Simulator could answer:

"If our database error rate jumps to 20% and reviewers are unavailable for an hour, what happens to our incident governance process?"

Most incident tools cannot answer that question. AlertEngine's architecture is designed to eventually be able to.

Inspired by: Demirbas, Charapko, Vig — "A Case for Simulation-Driven Resilience in Agentic Data Systems" (ACM CAIS 2026). docs/ARCHITECTURE.md

FAQ

Can I self-host the orchestrator?

No. The orchestrator is source-available for audit, hosted and managed by Tandem Media. Enterprise gets a dedicated deployment under a custom SLA.

What happens if Claude is unavailable?

The system fails safe — falls back to deterministic policy rules. The audit log records actor: "policy". No silent failures.

What happens if my recovery webhook is down?

The orchestrator retries 3 times with exponential backoff. On failure, the incident is captured in the Dead Letter Queue for manual replay. Available on Compliance tier and above.

Can I start free and upgrade?

Yes. pip install fastapi-alertengine is MIT licensed and never expires. The free SDK runs forever on your servers. Upgrade to a managed tier whenever you need alerts and diagnosis.

Is the audit trail really immutable?

Yes. audit.py uses Redis LIST with rpush — append only, never mutated. Every event includes actor, stage, confidence, reason, and policy version. Replay reconstructs state from events, not from stored state.

How does pricing work if I exceed my incident quota?

Growth and Starter: no overage — incidents are silently counted but not billed beyond quota (upgrade required for more). Team: $0.10/incident over 50. Compliance: $0.05/incident over 200. Platform: $0.02/incident over 1,000.

License + Contact

Free SDK (fastapi\_alertengine/): MIT — see LICENSE

Orchestrator (orchestrator/): Source-available for audit only — see LICENSE-ORCHESTRATOR.md

Contact: anchorflowalertengine@outlook.com



