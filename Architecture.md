\# AlertEngine Orchestrator — Technical Architecture Document



\*\*Version:\*\* 1.0  

\*\*Date:\*\* 2026-06-01  

\*\*Classification:\*\* Source-available (audit)  



\---



\## 1. Executive Summary



FastAPI AlertEngine is a policy-first incident intelligence platform. The orchestrator does not use AI to decide whether an incident exists — it uses deterministic rules. AI is invoked only after policy gates have passed, and its role is strictly to explain the incident in natural language and suggest a recovery action. Every execution path requires explicit human authorization.



This document maps the Orchestrator Stack architecture to the actual codebase, explains the policy-first design philosophy, and provides audience-specific guidance for security auditors, developers, and enterprise buyers.



\---



\## 2. Architecture Overview



```

┌─────────────────────────────────────────────────────────────────┐

│  METRICS \& SIGNALS  (SDK — runs on customer servers)              │

│  middleware.py → engine.py → intelligence.py                      │

│  P95 · error\_rate · anomaly\_score · health\_score 0-100           │

└────────────────────────┬──────────────────────────────────────────┘

&#x20;                        │  /health/alerts  (poll every 5s)

&#x20;                        ▼

┌─────────────────────────────────────────────────────────────────┐

│  POLICY ENGINE  (Orchestrator — runs on Tandem Media)           │

│  policy.py · plans.py · degraded.py                              │

│  Hard rules: thresholds, quotas, service limits, degraded mode   │

└────────────────────────┬──────────────────────────────────────────┘

&#x20;                        │  Policy gates must ALL pass

&#x20;                        ▼

┌─────────────────────────────────────────────────────────────────┐

│  ORCHESTRATOR CORE                                              │

│  loop.py · pipeline.py · lock.py · idempotency.py               │

│  State machine: DETECTED → VALIDATED → RECOVERED                 │

│  Distributed locks · Baseline hygiene · Idempotency               │

└────────────────────────┬──────────────────────────────────────────┘

&#x20;                        │  Delegated call (override possible)

&#x20;                        ▼

┌─────────────────────────────────────────────────────────────────┐

│  AI REASONING                                                   │

│  claude\_engine.py · diagnostic\_council.py                        │

│  Diagnosis · Root cause · Confidence scoring · WhatsApp message  │

└────────────────────────┬──────────────────────────────────────────┘

&#x20;                        │  Recovery URL (JWT, 5-min TTL)

&#x20;                        ▼

┌─────────────────────────────────────────────────────────────────┐

│  HUMAN AUTHORIZATION                                            │

│  action\_generator.py (JWT) · Customer recovery webhook           │

│  Engineer taps approve → POST /action/recover/confirm            │

│  Orchestrator calls customer's webhook — never SSHs               │

└─────────────────────────────────────────────────────────────────┘

```



\---



\## 3. Layer-by-Layer Breakdown



\### 3.1 Metrics \& Signals (SDK)



\*\*Files:\*\* `fastapi\_alertengine/middleware.py`, `engine.py`, `intelligence.py`



\*\*What happens:\*\*

1\. `RequestMetricsMiddleware` intercepts every FastAPI request

2\. Measures latency and HTTP status code

3\. Writes to Redis Streams (append-only event log)

4\. `engine.py` computes rolling P95, error rate, and anomaly score

5\. `intelligence.py` computes composite health score (0–100) and trend direction



\*\*Key design decision:\*\* The SDK is pure measurement. Zero side effects. It never calls the orchestrator. The orchestrator polls the SDK.



\*\*Output format:\*\*

```json

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

```



\---



\### 3.2 Policy Engine



\*\*Files:\*\* `orchestrator/policy.py`, `plans.py`, `degraded.py`



\*\*Core principle:\*\* Policy decides incidents. Code executes policy.



\*\*Functions:\*\*



| Function | File | Purpose |

|----------|------|---------|

| `should\_alert(score, err)` | `policy.py` | Score < threshold AND error\_rate > threshold |

| `should\_escalate\_voice(duration, score)` | `policy.py` | Duration > 180s and score still critical |

| `should\_escalate\_secondary(duration, score)` | `policy.py` | Duration > 300s and primary unresponsive |

| `should\_open\_new\_incident(incident)` | `policy.py` | Idempotency check — no active incident |

| `can\_monitor\_more\_services(tenant)` | `plans.py` | Plan service limit gate |

| `incident\_quota\_remaining(tenant)` | `plans.py` | Monthly incident quota gate |

| `can\_mutate\_state()` | `degraded.py` | Emergency mode — no state changes allowed |

| `can\_escalate()` | `degraded.py` | Emergency mode — no escalations allowed |

| `can\_send\_notifications()` | `degraded.py` | Emergency mode — notifications suppressed |



\*\*Plan tiers (deterministic gates):\*\*



| Plan | Max Services | Incidents/mo | Claude | Voice | Slack | DLQ |

|------|-------------|--------------|--------|-------|-------|-----|

| hobby | 1 | 5 | ❌ | ❌ | ❌ | ❌ |

| developer | 1 | 10 | ✅ | ❌ | ❌ | ❌ |

| solo | 3 | 50 | ✅ | ❌ | ❌ | ✅ |

| startup | 5 | 200 | ✅ | ✅ | ✅ | ✅ |

| scale | 20 | 1,000 | ✅ | ✅ | ✅ | ✅ |

| teams | 20 | 1,000 | ✅ | ✅ | ✅ | ✅ |

| enterprise | ∞ | ∞ | ✅ | ✅ | ✅ | ✅ |



\*\*Key design decision:\*\* All policy checks run \*before\* any AI call. If a tenant has exhausted their incident quota, the orchestrator returns early with a warning log. Claude is never invoked. This keeps costs predictable and prevents AI hallucinations on non-actionable signals.



\---



\### 3.3 Orchestrator Core



\*\*Files:\*\* `orchestrator/loop.py`, `pipeline.py`, `lock.py`, `idempotency.py`, `baseline.py`



\*\*Evidence assembly (current: ad hoc in claude\_engine.py — target: evidence\_pack.py, Q3 2026):\*\* `evidence\_pack.py` will consolidate baseline context, diagnosis history, commit context, health metrics, and policy version into a single structured evidence package consumed by AI reasoning and audit layers. Currently assembled ad hoc inside `claude\_engine.py`.



\*\*State machine:\*\*

```

DETECTED → PROPOSED → VALIDATED → AUTHORIZED → EXECUTED → RESOLVED

&#x20;   ↓           ↓          ↓

&#x20;(Claude)   (Claude)   (Claude)



Shortcut from any active stage:

ANY ACTIVE STATE → RECOVERED  (system-detected health restoration)



Terminal states (no further transitions):

RECOVERED · RESOLVED · EXPIRED · FAILED · WEBHOOK\_FAILED



See: orchestrator/pipeline.py → IncidentStage enum

```



\*\*Per-tenant processing loop (`loop.py:\_process\_tenant`):\*\*



1\. \*\*Fetch health\*\* (`\_fetch\_health`) — 5s timeout via `httpx`

2\. \*\*Update baseline\*\* (`\_update\_baseline\_safe`) — \*\*only on healthy polls\*\* (`score >= 80, status == healthy`). This is critical: learning from P95=8000ms during an incident would poison the baseline, making future incidents appear as "only 1.2x baseline" when they are actually severe.

3\. \*\*Load active incident\*\* (`\_get\_tenant\_incident`) — from Redis

4\. \*\*New incident path\*\* (if `status == critical` and no active incident):

&#x20;  - Acquire distributed lock (`incident\_lock`)

&#x20;  - Double-check inside lock (race condition guard)

&#x20;  - Run policy gates: `should\_alert`, `can\_monitor\_more\_services`, `incident\_quota\_remaining`

&#x20;  - \*\*Check lease validity before expensive Claude call\*\*

&#x20;  - Call `claude\_decide()`

&#x20;  - Validate decision schema (`validate\_decision\_schema`)

&#x20;  - Open incident (`open\_incident`)

&#x20;  - Save to Redis (`save\_incident`)

&#x20;  - Execute actions (`\_execute\_actions`) — notifications, token generation

5\. \*\*Existing incident path:\*\*

&#x20;  - Acquire lock on incident\_id

&#x20;  - Check lease before Claude call

&#x20;  - Call `claude\_decide()` with diagnosis memory

&#x20;  - If recovered (`status in healthy/degraded` and stage != RECOVERED):

&#x20;    - Apply transition (`apply\_transition`)

&#x20;    - Clear diagnosis memory (`clear\_history`)

&#x20;    - Resolve incident (`resolve\_incident`)

&#x20;  - Else: pipeline advance (`decide()` → `apply\_transition()`)

6\. \*\*Escalations:\*\* Voice after 180s, secondary after 300s



\*\*Distributed locking (`lock.py`):\*\*

\- Redis-based with Lua script atomic release

\- Lease validity checked before expensive operations (Claude API calls)

\- Lease validity checked again before state mutation

\- No race conditions on incident creation or transition



\*\*Idempotency (`idempotency.py`):\*\*

\- Atomic Redis SET NX (`claim\_action`)

\- Prevents duplicate incident creation under concurrent polls

\- Prevents duplicate notifications

\- Action IDs: `make\_action\_id(incident\_id, stage, action\_type)`



\*\*Baseline hygiene (`baseline.py`):\*\*

\- EMA (exponential moving average) over last 24 samples

\- Updated ONLY when `status == healthy` and `score >= 80`

\- Injected into Claude prompt as deviation context: "P95 is 43.6x baseline, errors 90.0x baseline"

\- This is what makes the AI diagnosis accurate — it knows what "normal" looks like



\---



\### 3.4 AI Reasoning



\*\*Files:\*\* `orchestrator/claude\_engine.py`, `diagnostic\_council.py`, `incident\_policy.py`



\*\*Council mode:\*\* Two models (Haiku — latency/database specialist; Sonnet — network/dependency specialist) analyze telemetry independently. If they agree, one clean alert fires. If they diverge, a Dissent Alert is sent showing both theories. Controlled by `COUNCIL\_ENABLED` env var.



\*\*Policy injection:\*\* `incident\_policy.py` thresholds are injected into the Claude prompt so the AI uses the same recovery/validation thresholds as the deterministic state machine.



\*\*Core principle:\*\* AI is not the decision-maker. It is the reasoning layer after detection.



\*\*Architecture:\*\*



```

Orchestrator Core

&#x20;      │

&#x20;      ├───► diagnostic\_council.py (dual-model, primary path)

&#x20;      │         ├───► Model A: Haiku — fast hypothesis

&#x20;      │         ├───► Model B: Sonnet — deep analysis

&#x20;      │         └───► Divergence detection + confidence merge

&#x20;      │

&#x20;      └───► claude\_engine.py (single-model fallback)

&#x20;                └───► Native tool use (schema-guaranteed output)

```



\*\*Claude prompt design:\*\*

\- System prompt includes few-shot examples

\- Injects current policy thresholds: "recover when score>70 and err<5%, validate when score<40 and err>20%"

\- Injects baseline context: "P95 is 43.6x baseline"

\- Injects commit context (Diff-in-Pocket): recent git commits correlated with incident start time

\- Injects diagnosis memory for active incidents (multi-turn continuity)



\*\*Native tool use:\*\*

\- Claude must call `incident\_decision` tool

\- Schema guarantees valid output — no JSON parsing failures

\- Returns: `action`, `reason`, `confidence`, `whatsapp\_message`



\*\*Confidence rules (hard-coded in prompt):\*\*

\- `confidence < 0.6` → `suppress` (do not alert)

\- `recover` only when score > 70 and error\_rate < 0.05

\- `validate` only when score < 40 and error\_rate > 0.20

\- Be conservative — false positives in fintech are costly



\*\*Fail-safe:\*\*

\- If Claude API fails after 2 retries, returns `suppress` with 0% confidence

\- If Claude returns invalid schema, falls back to suppress

\- Attribution: `actor = "claude"` in audit log



\*\*Diagnostic Council:\*\*

\- Two models reason independently

\- Divergence detection: if models disagree, confidence is lowered

\- Merged diagnosis includes both hypotheses

\- Recorded in audit metadata: `council\_mode`, `diverged`, `diagnosis\_a`, `diagnosis\_b`



\---



\### 3.5 Human Authorization



\*\*Files:\*\* `orchestrator/action\_generator.py`, recovery webhook (customer-owned)



\*\*Flow:\*\*

1\. Orchestrator generates single-use JWT token (`generate\_recovery\_token`)

2\. Token is tenant-scoped, 5-minute TTL, single-use

3\. Recovery URL sent via WhatsApp/Telegram: `https://tenant.alertengine.io/action/recover?token=...`

4\. Engineer taps the link — this hits `GET /action/recover` (preview only, zero side effects)

5\. Engineer taps "Approve" — this hits `POST /action/recover/confirm` (irreversible, requires valid JWT)

6\. Orchestrator validates JWT via atomic Redis SET NX (replay protection)

7\. Orchestrator calls customer's recovery webhook URL (configured during onboarding)

8\. Customer's webhook executes the fix (restart worker, clear cache, scale service)

9\. Orchestrator confirms success/failure to engineer



\*\*Security guarantees:\*\*

\- Nothing executes without explicit approval

\- Preview endpoint is read-only

\- JWT: tenant-scoped, 5-min TTL, single-use, atomic Redis SET NX

\- Replay protection: exactly 1 succeeds, 19 rejected in concurrent flood test

\- Orchestrator never SSHs into customer machines

\- 3 retries with exponential backoff on webhook failure

\- Dead Letter Queue (DLQ) for unrecoverable failures (Startup+ tier)



\---



\## 4. Data Flow: Exact Execution Sequence



\### 4.1 New Incident Detection



```

1\. loop.py:\_run\_once()

&#x20;  └── list\_active\_tenants()



2\. loop.py:\_process\_tenant(tenant)

&#x20;  ├── \_fetch\_health(health\_url) → health dict

&#x20;  ├── if status == healthy and score >= 80:

&#x20;  │   └── \_update\_baseline\_safe(tenant\_id, health)

&#x20;  ├── \_get\_tenant\_incident(tenant\_id) → None (no active incident)

&#x20;  ├── if status == critical:

&#x20;  │   ├── incident\_lock("creating-{tenant\_id}", ttl=10)

&#x20;  │   ├── should\_open\_new\_incident() → True

&#x20;  │   ├── claim\_action(creation\_key) → True (atomic SET NX)

&#x20;  │   ├── should\_alert(score, err) → True

&#x20;  │   ├── can\_monitor\_more\_services(tenant) → True

&#x20;  │   ├── incident\_quota\_remaining(tenant) > 0

&#x20;  │   ├── get\_tenant\_plan(tenant).has\_claude\_decision → True

&#x20;  │   ├── lease.valid check

&#x20;  │   ├── claude\_decide(health, incident=None, tenant\_id=tenant\_id)

&#x20;  │   │   ├── \_build\_prompt() → injects baseline, policy, commits

&#x20;  │   │   ├── Anthropic API call with tool\_use

&#x20;  │   │   └── returns {action, reason, confidence, whatsapp\_message}

&#x20;  │   ├── decide\_new\_incident() → decision dict

&#x20;  │   ├── validate\_decision\_schema() → (True, "")

&#x20;  │   ├── open\_incident() → incident\_record

&#x20;  │   ├── save\_incident() → Redis

&#x20;  │   ├── \_save\_tenant\_active() → Redis

&#x20;  │   ├── increment\_incident\_count() → tenant dict

&#x20;  │   ├── save\_tenant() → Redis

&#x20;  │   ├── append\_event() → audit log (actor="claude", metadata={council\_mode, diverged})

&#x20;  │   └── \_execute\_actions()

&#x20;  │       ├── GENERATE\_TOKEN → recovery\_url

&#x20;  │       └── SEND\_NOTIFICATION → WhatsApp/Telegram

&#x20;  └── return

```



\### 4.2 Incident Recovery



```

1\. loop.py:\_process\_tenant(tenant)

&#x20;  ├── \_fetch\_health(health\_url) → health dict

&#x20;  ├── \_get\_tenant\_incident(tenant\_id) → incident dict

&#x20;  ├── incident\_lock(incident\_id)

&#x20;  ├── lease.valid check

&#x20;  ├── if status in (healthy, degraded) and stage != RECOVERED:

&#x20;  │   ├── claude\_decide(health, incident=incident, tenant\_id=tenant\_id)

&#x20;  │   │   ├── \_build\_history\_messages() → multi-turn diagnosis memory

&#x20;  │   │   ├── \_build\_prompt() → includes prior hypotheses

&#x20;  │   │   └── returns {action: "recover", ...}

&#x20;  │   ├── decide(incident, health, claude\_output) → decision

&#x20;  │   ├── validate\_decision\_schema() → True

&#x20;  │   ├── if next\_stage == "RECOVERED":

&#x20;  │   │   ├── can\_mutate\_state() → True

&#x20;  │   │   ├── apply\_transition(incident, "RECOVERED") → updated

&#x20;  │   │   ├── save\_incident(updated) → Redis

&#x20;  │   │   ├── resolve\_incident(incident\_id) → Redis

&#x20;  │   │   ├── \_clear\_tenant\_active(tenant\_id) → Redis

&#x20;  │   │   ├── clear\_history(incident\_id) → diagnosis memory

&#x20;  │   │   ├── append\_event() → audit log (stage="RECOVERED")

&#x20;  │   │   └── \_execute\_actions() → SEND\_RECOVERY notification

&#x20;  └── return

```



\---



\## 5. Policy-First Design Philosophy



\### From "code decides incidents" → "policy decides incidents"



\*\*Before (anti-pattern):\*\*

```python

\# Logic scattered across files

if score < 30 and err > 0.15:

&#x20;   alert()

\# Thresholds hard-coded in 7 places

\# Tuning requires code changes and redeploys

```



\*\*After (AlertEngine pattern):\*\*

```python

\# Single policy file controls all thresholds

POLICY = {

&#x20;   "recover\_score": 70,

&#x20;   "recover\_error\_rate": 0.05,

&#x20;   "validate\_score": 40,

&#x20;   "validate\_error\_rate": 0.20,

&#x20;   "suppress\_confidence": 0.60,

}



\# Policy version is auditable

\# A/B testing is a config change, not a deploy

\# Rollbacks are deterministic

```



\*\*Why this matters for compliance:\*\*

\- Regulated industries require documented, versionable decision rules

\- Auditors can read `policy.py` and `plans.py` to understand exactly when alerts fire

\- AI is not a black box that "decides" — it is a bounded reasoning tool that operates within a policy envelope



\*\*Policy override pattern:\*\*

If `pipeline.py` ever needs to override Claude's recommendation based on hard policy rules, that override must be auditable:



```python

\# Pseudocode for the pattern

if health\["score"] > POLICY\["recover\_score"] and claude\_output\["action"] != "recover":

&#x20;   audit.append\_event(

&#x20;       incident\_id=incident\_id,

&#x20;       stage="RECOVERED",

&#x20;       decision="recover",

&#x20;       actor="policy\_override",  # ← explicit

&#x20;       reason="score above recover threshold, overriding Claude recommendation",

&#x20;       confidence=1.0,

&#x20;       metadata={"claude\_recommended": claude\_output\["action"]}

&#x20;   )

&#x20;   return {"action": "recover", ...}

```



This makes the architecture self-documenting: \*\*policy is the floor, AI is the ceiling.\*\*



\---



\## 6. Compliance Mapping



Every design decision maps to a real compliance requirement:



| Requirement | Implementation | File |

|-------------|---------------|------|

| Human authorization before execution | Engineer must tap approve; no autonomous remediation | `action\_generator.py`, recovery webhook |

| Immutable audit trail | Append-only Redis LIST; every stage, decision, approval recorded | `audit.py` |

| Replay attack prevention | Single-use JWT tokens; atomic Redis SET NX | `idempotency.py`, `action\_generator.py` |

| Cross-tenant data isolation | Tenant ID validated on every endpoint; 403 on mismatch | `loop.py`, `tenants.py` |

| Separation of duties | Free SDK (data plane) and orchestrator (control plane) fully isolated | Architecture boundary |

| Incident documentation | Full timeline reconstructable from audit log: DETECTED → AUTHORIZED → EXECUTED | `audit.py` |

| Degraded mode handling | NORMAL / DEGRADED / EMERGENCY with automatic transitions | `degraded.py` |

| Recovery action accountability | Who approved, when, what executed — all logged with timestamps | `audit.py` |

| Deterministic alert rules | Single policy file controls thresholds; versionable | `policy.py`, `incident\_policy.py` |



\*\*The accounting parallel:\*\* In accounting, no transaction executes without authorization and every action leaves an audit trail. AlertEngine applies that same discipline to production infrastructure.



\---



\## 7. Audience-Specific Guidance



\### 7.1 For Security Auditors



\*\*What to review:\*\*

1\. `orchestrator/loop.py` — verify policy gates run before AI calls

2\. `orchestrator/policy.py` — verify thresholds are deterministic and versionable

3\. `orchestrator/audit.py` — verify append-only, no updates, 7-day TTL

4\. `orchestrator/idempotency.py` — verify atomic SET NX for replay protection

5\. `orchestrator/lock.py` — verify Lua script atomic release, no race conditions

6\. `orchestrator/claude\_engine.py` — verify tool\_use schema guarantees, fail-safe defaults

7\. `orchestrator/action\_generator.py` — verify JWT tenant-scoping, TTL, single-use



\*\*Questions to ask:\*\*

\- What happens if Redis is unavailable? → `degraded.py` enters EMERGENCY mode; no state mutations, no escalations

\- What happens if Claude returns a recommendation that violates policy? → Currently, `pipeline.py` should validate and override. Verify this in your audit.

\- Can a tenant access another tenant's incident data? → No. Every Redis key is prefixed with `orchestrator:active\_incident:{tenant\_id}` and `orchestrator:incident:{incident\_id}`. Cross-tenant access returns 403.

\- What happens in a concurrent token flood? → Adversarial audit passed: 20 concurrent requests → exactly 1 succeeded, 19 rejected.



\*\*Audit artifacts:\*\*

\- 232 tests passing (Python 3.10, 3.11, 3.12)

\- Adversarial audit by autonomous AI agent: 10/10 checks passed

\- Source-available orchestrator for inspection (not for self-hosting)



\### 7.2 For Developers



\*\*Getting started:\*\*

```bash

pip install fastapi-alertengine

```



```python

from fastapi import FastAPI

from fastapi\_alertengine import instrument



app = FastAPI()

instrument(app)  # that's it

```



Your app now exposes `/health/alerts`. The orchestrator polls this endpoint every 5 seconds.



\*\*Local development (no orchestrator needed):\*\*

```bash

git clone https://github.com/Tandem-Media/fastapi-alertengine

cd fastapi-alertengine

pip install fastapi-alertengine uvicorn httpx

uvicorn examples.quickstart\_example:app --reload



\# In another terminal — simulate a spike

curl -X POST localhost:8000/simulate/spike

curl -s localhost:8000/health/alerts | python3 -m json.tool

```



\*\*Key integration points:\*\*

1\. \*\*Recovery webhook\*\* — During onboarding, you provide a URL like `https://your-api.com/webhooks/recovery`. The orchestrator calls this after human approval. You control what the webhook does.

2\. \*\*Environment variables\*\* — `REDIS\_URL`, `ALERTENGINE\_BASE\_URL`, `ANTHROPIC\_API\_KEY`, `ALERT\_SECRET`

3\. \*\*Plan selection\*\* — Start with Hobby ($19/mo, Telegram only) or Developer ($99/mo, WhatsApp + AI decisions)



\*\*SDK behavior:\*\*

\- Memory mode: works without Redis (metrics stored in-process, lost on restart)

\- Circuit breaker: buffers events during Redis outages

\- Never crashes your app — all exceptions are caught and logged



\### 7.3 For Enterprise Buyers



\*\*Value proposition:\*\*

\- \*\*Financial-grade authorization discipline\*\* applied to API infrastructure

\- \*\*Human-in-the-loop\*\* — nothing executes without explicit engineer approval

\- \*\*Immutable audit trail\*\* — every decision, every approval, every execution is logged

\- \*\*Deterministic policy engine\*\* — versionable thresholds, A/B testable, rollback-safe

\- \*\*AI as explanation, not decision\*\* — reduces alert fatigue without removing human judgment

\- \*\*Mobile-first operations\*\* — WhatsApp/Telegram alerts find engineers, not dashboards



\*\*Deployment model:\*\*

\- \*\*Free SDK\*\* runs on your servers (MIT license). Zero side effects. Pure measurement.

\- \*\*Orchestrator\*\* runs on Tandem Media's managed infrastructure. Source-available for audit. Not self-hosted.

\- \*\*Enterprise tier\*\* includes dedicated deployment (separate managed instance under SLA) and custom features.



\*\*Security model:\*\*

\- Orchestrator never SSHs into your machines

\- Orchestrator only calls your webhook after human approval

\- All communication is over HTTPS

\- JWT tokens are tenant-scoped, time-bound, and single-use

\- Redis is used for state and audit — not for sensitive customer data



\*\*Compliance readiness:\*\*

\- SOC 2 Type II roadmap (contact for timeline)

\- GDPR: no PII stored in orchestrator; tenant data is ephemeral (7-day TTL on audit logs)

\- PCI DSS: orchestrator does not handle payment data; SDK is pure measurement



\*\*Pricing for scale:\*\*

\- Scale: $1,500/mo — 20 services, 1,000 incidents/mo, all channels + voice escalation

\- Enterprise: Custom — unlimited services and incidents, dedicated deployment, custom SLA



\---



\## 8. Reliability Guarantees



| Guarantee | Implementation |

|-----------|---------------|

| Duplicate incident prevention | Tenant-scoped lock + idempotency |

| Replay protection | Single-use JWT tokens; atomic Redis SET NX |

| Distributed locking | Lua script atomic release; no race conditions |

| Tenant isolation | Cross-tenant data access returns 403 |

| Audit trail | Append-only Redis LIST; every stage transition logged |

| Degraded mode | NORMAL / DEGRADED / EMERGENCY with auto-recovery |

| Dead letter queue | Unrecoverable failures captured for replay (Startup+) |

| Circuit breaker | Per-provider per-tenant; Redis-backed |

| Webhook retry | 3 attempts with exponential backoff |

| Baseline hygiene | Updated only on healthy polls; never during incidents |

| Fail-safe AI | Claude unavailable → suppress with 0% confidence |

| Memory mode | SDK never crashes when Redis is unavailable |



\---



\## 9. Glossary



| Term | Definition |

|------|-----------|

| \*\*SDK\*\* | The `fastapi\_alertengine` Python package that runs on customer servers. MIT licensed. |

| \*\*Orchestrator\*\* | The managed service that polls health endpoints, runs AI diagnosis, and sends alerts. Source-available. |

| \*\*Policy Engine\*\* | The deterministic rules layer (`policy.py`, `plans.py`) that gates all incident decisions. |

| \*\*AI Reasoning\*\* | The Claude/council layer that explains incidents in natural language after policy gates pass. |

| \*\*Human Authorization\*\* | The tap-to-approve flow via JWT recovery links. Nothing executes without explicit approval. |

| \*\*Baseline\*\* | EMA of normal P95 and error rate. Updated only on healthy polls. |

| \*\*Diagnostic Council\*\* | Dual-model AI reasoning (Haiku + Sonnet) with divergence detection. |

| \*\*Diff-in-Pocket\*\* | Commit context injection — correlates incidents with recent deployments. |

| \*\*DLQ\*\* | Dead Letter Queue — captures unrecoverable webhook failures for manual replay. |

| \*\*Circuit Breaker\*\* | Per-provider per-tenant failure isolation. 3 failures → 60s cooldown. |



\---



\## 10. Metastability Defense



AlertEngine's human-in-the-loop authorization is designed to break the metastable feedback loops described in Demirbas et al. (2026), \*"A Case for Simulation-Driven Resilience in Agentic Data Systems"\* (ACM CAIS / SAO Workshop, San Jose, May 2026).



\*\*The core finding:\*\* AI agents create \~20x more branches and \~50x more rollbacks than human clients. Their aggressive retry behavior violates assumptions baked into every layer of modern data systems. Automated remediation in this environment amplifies failures — the recovery mechanism becomes part of the feedback loop.



\*\*The metastable trap in incident response:\*\*

```

Pool exhausted → AI diagnoses → auto-restart fires

→ agents retry → new connection burst

→ pool exhausts again → restart fires again

→ system never stabilizes

```



\*\*AlertEngine's structural defenses:\*\*



| Defense | Mechanism | Effect |

|---------|-----------|--------|

| Human authorization | Engineer approves before webhook executes | Breaks agent feedback loop |

| Policy gates before AI | `should\_recover()` runs before Claude | Hard thresholds prevent AI-driven recovery in unstable states |

| Immutable audit trail | Append-only log with actor/timestamp/policy version | Enables post-incident simulation replay |

| Single policy file | `incident\_policy.py` — one versioned source of truth | Prevents two reasonable policies composing into a metastable loop |



\*\*Human authorization is not a UX choice. It is a metastability defense.\*\*



\*\*Reference:\*\* Murat Demirbas, Aleksey Charapko, Akshat Vig. \*"A Case for Simulation-Driven Resilience in Agentic Data Systems."\* ACM CAIS SAO Workshop, 2026. \[PDF](https://bauplanlabs.github.io/SAO-workshop/papers/9.pdf)



\---



\## 11. Threat Model



AlertEngine operates under the following trust assumptions. The 10/10 adversarial audit results address the threats within this model.



| Component | Trust Level | Rationale |

|-----------|-------------|-----------|

| Orchestrator infrastructure | \*\*Trusted\*\* | Tandem Media operates it; physical and logical access controls apply |

| Customer's recovery webhook | \*\*Trusted\*\* | Customer owns and controls the endpoint; orchestrator only POSTs to it after human approval |

| Redis instance | \*\*Trusted\*\* | Tenant-isolated; credentials never exposed in API responses |

| Claude / Anthropic API | \*\*Untrusted\*\* | Fail-safe defaults apply; Claude cannot trigger state transitions; unavailability → suppress with 0% confidence |

| Customer's `/health/alerts` endpoint | \*\*Untrusted\*\* | Poll-only; read-only; no authentication required by design; malformed responses handled by `safe\_payload.py` |

| JWT recovery tokens | \*\*Verified\*\* | HMAC-signed, tenant-scoped, 5-minute TTL, single-use via atomic Redis SET NX |

| Incoming webhook payloads | \*\*Untrusted\*\* | Validated via Pydantic; `safe\_payload.py` strips unexpected fields |



\*\*Out of scope:\*\* Physical infrastructure attacks, supply chain attacks on Python dependencies, Anthropic API compromise.



\---



\## 12. Data Retention



| Data type | Retention | Mechanism |

|-----------|-----------|-----------|

| Audit log events | 7 days | Redis LIST with `EXPIRE` — automatic expiration |

| Incident records | Duration + 7 days post-resolution | Redis key with TTL |

| Tenant configuration | Persistent until deletion request | Redis HASH — no TTL |

| Health metrics | Not stored by orchestrator | SDK generates; orchestrator polls and discards |

| Commit context (Diff-in-Pocket) | 7 days, max 50 commits | Redis sorted set with TTL |

| Diagnosis memory | 24 hours | Redis LIST with TTL |

| Signup leads | 30 days | Redis key with TTL |



\*\*No PII is stored\*\* in the orchestrator. Health metrics contain only latency/error/status data. Tenant records contain contact phone numbers for WhatsApp delivery — these are not shared with third parties and are used solely for incident notification.



\*\*Long-term audit retention:\*\* For compliance requirements beyond 7 days, export audit logs via `GET /audit/{incident\_id}/report` (PDF) or `GET /audit/{incident\_id}` (JSON) and store in your own durable storage. S3/Postgres archival is on the roadmap for Compliance tier.



\---



\## 13. Contact \& License



\*\*Free SDK\*\* (`fastapi\_alertengine/`): MIT — see `LICENSE`



\*\*Orchestrator\*\* (`orchestrator/`): Source-available for audit only — see `LICENSE-ORCHESTRATOR.md`



\*\*Contact:\*\* anchorflowalertengine@outlook.com



\*\*Built in Zimbabwe.\*\* Engineers here aren't always at laptops when things break. WhatsApp is the operational control plane.

