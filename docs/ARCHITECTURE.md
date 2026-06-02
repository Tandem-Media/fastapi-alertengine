# AlertEngine Orchestrator — Technical Architecture Document

**Version:** 1.0  
**Date:** 2026-06-01  
**Classification:** Source-available (audit)  

---

## 1. Executive Summary

FastAPI AlertEngine is a policy-first incident intelligence platform. The orchestrator does not use AI to decide whether an incident exists — it uses deterministic rules. AI is invoked only after policy gates have passed, and its role is strictly to explain the incident in natural language and suggest a recovery action. Every execution path requires explicit human authorization.

This document maps the Orchestrator Stack architecture to the actual codebase, explains the policy-first design philosophy, and provides audience-specific guidance for security auditors, developers, and enterprise buyers.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  METRICS & SIGNALS  (SDK — runs on customer servers)              │
│  middleware.py → engine.py → intelligence.py                      │
│  P95 · error_rate · anomaly_score · health_score 0-100           │
└────────────────────────┬──────────────────────────────────────────┘
                         │  /health/alerts  (poll every 5s)
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  POLICY ENGINE  (Orchestrator — runs on Tandem Media)           │
│  policy.py · plans.py · degraded.py                              │
│  Hard rules: thresholds, quotas, service limits, degraded mode   │
└────────────────────────┬──────────────────────────────────────────┘
                         │  Policy gates must ALL pass
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR CORE                                              │
│  loop.py · pipeline.py · lock.py · idempotency.py               │
│  State machine: DETECTED → VALIDATED → RECOVERED                 │
│  Distributed locks · Baseline hygiene · Idempotency               │
└────────────────────────┬──────────────────────────────────────────┘
                         │  Delegated call (override possible)
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  AI REASONING                                                   │
│  claude_engine.py · diagnostic_council.py                        │
│  Diagnosis · Root cause · Confidence scoring · WhatsApp message  │
└────────────────────────┬──────────────────────────────────────────┘
                         │  Recovery URL (JWT, 5-min TTL)
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  HUMAN AUTHORIZATION                                            │
│  action_generator.py (JWT) · Customer recovery webhook           │
│  Engineer taps approve → POST /action/recover/confirm            │
│  Orchestrator calls customer's webhook — never SSHs               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Layer-by-Layer Breakdown

### 3.1 Metrics & Signals (SDK)

**Files:** `fastapi_alertengine/middleware.py`, `engine.py`, `intelligence.py`

**What happens:**
1. `RequestMetricsMiddleware` intercepts every FastAPI request
2. Measures latency and HTTP status code
3. Writes to Redis Streams (append-only event log)
4. `engine.py` computes rolling P95, error rate, and anomaly score
5. `intelligence.py` computes composite health score (0–100) and trend direction

**Key design decision:** The SDK is pure measurement. Zero side effects. It never calls the orchestrator. The orchestrator polls the SDK.

**Output format:**
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

### 3.2 Policy Engine

**Files:** `orchestrator/policy.py`, `plans.py`, `degraded.py`

**Core principle:** Policy decides incidents. Code executes policy.

**Functions:**

| Function | File | Purpose |
|----------|------|---------|
| `should_alert(score, err)` | `policy.py` | Score < threshold AND error_rate > threshold |
| `should_escalate_voice(duration, score)` | `policy.py` | Duration > 180s and score still critical |
| `should_escalate_secondary(duration, score)` | `policy.py` | Duration > 300s and primary unresponsive |
| `should_open_new_incident(incident)` | `policy.py` | Idempotency check — no active incident |
| `can_monitor_more_services(tenant)` | `plans.py` | Plan service limit gate |
| `incident_quota_remaining(tenant)` | `plans.py` | Monthly incident quota gate |
| `can_mutate_state()` | `degraded.py` | Emergency mode — no state changes allowed |
| `can_escalate()` | `degraded.py` | Emergency mode — no escalations allowed |
| `can_send_notifications()` | `degraded.py` | Emergency mode — notifications suppressed |

**Plan tiers (deterministic gates):**

| Plan | Max Services | Incidents/mo | Claude | Voice | Slack | DLQ |
|------|-------------|--------------|--------|-------|-------|-----|
| hobby | 1 | 5 | ❌ | ❌ | ❌ | ❌ |
| developer | 1 | 10 | ✅ | ❌ | ❌ | ❌ |
| solo | 3 | 50 | ✅ | ❌ | ❌ | ✅ |
| startup | 5 | 200 | ✅ | ✅ | ✅ | ✅ |
| scale | 20 | 1,000 | ✅ | ✅ | ✅ | ✅ |
| teams | 20 | 1,000 | ✅ | ✅ | ✅ | ✅ |
| enterprise | ∞ | ∞ | ✅ | ✅ | ✅ | ✅ |

**Key design decision:** All policy checks run *before* any AI call. If a tenant has exhausted their incident quota, the orchestrator returns early with a warning log. Claude is never invoked. This keeps costs predictable and prevents AI hallucinations on non-actionable signals.

---

### 3.3 Orchestrator Core

**Files:** `orchestrator/loop.py`, `pipeline.py`, `lock.py`, `idempotency.py`, `baseline.py`

**Evidence assembly (in progress):** `evidence_pack.py` will consolidate baseline context, diagnosis history, commit context, health metrics, and policy version into a single structured evidence package consumed by AI reasoning and audit layers. Currently assembled ad hoc inside `claude_engine.py`.

**State machine:**
```
DETECTED → PROPOSED → VALIDATED → AUTHORIZED → EXECUTED → RESOLVED
    ↓           ↓          ↓
 (Claude)   (Claude)   (Claude)

Shortcut from any active stage:
ANY ACTIVE STATE → RECOVERED  (system-detected health restoration)

Terminal states (no further transitions):
RECOVERED · RESOLVED · EXPIRED · FAILED · WEBHOOK_FAILED

See: orchestrator/pipeline.py → IncidentStage enum
```

**Per-tenant processing loop (`loop.py:_process_tenant`):**

1. **Fetch health** (`_fetch_health`) — 5s timeout via `httpx`
2. **Update baseline** (`_update_baseline_safe`) — **only on healthy polls** (`score >= 80, status == healthy`). This is critical: learning from P95=8000ms during an incident would poison the baseline, making future incidents appear as "only 1.2x baseline" when they are actually severe.
3. **Load active incident** (`_get_tenant_incident`) — from Redis
4. **New incident path** (if `status == critical` and no active incident):
   - Acquire distributed lock (`incident_lock`)
   - Double-check inside lock (race condition guard)
   - Run policy gates: `should_alert`, `can_monitor_more_services`, `incident_quota_remaining`
   - **Check lease validity before expensive Claude call**
   - Call `claude_decide()`
   - Validate decision schema (`validate_decision_schema`)
   - Open incident (`open_incident`)
   - Save to Redis (`save_incident`)
   - Execute actions (`_execute_actions`) — notifications, token generation
5. **Existing incident path:**
   - Acquire lock on incident_id
   - Check lease before Claude call
   - Call `claude_decide()` with diagnosis memory
   - If recovered (`status in healthy/degraded` and stage != RECOVERED):
     - Apply transition (`apply_transition`)
     - Clear diagnosis memory (`clear_history`)
     - Resolve incident (`resolve_incident`)
   - Else: pipeline advance (`decide()` → `apply_transition()`)
6. **Escalations:** Voice after 180s, secondary after 300s

**Distributed locking (`lock.py`):**
- Redis-based with Lua script atomic release
- Lease validity checked before expensive operations (Claude API calls)
- Lease validity checked again before state mutation
- No race conditions on incident creation or transition

**Idempotency (`idempotency.py`):**
- Atomic Redis SET NX (`claim_action`)
- Prevents duplicate incident creation under concurrent polls
- Prevents duplicate notifications
- Action IDs: `make_action_id(incident_id, stage, action_type)`

**Baseline hygiene (`baseline.py`):**
- EMA (exponential moving average) over last 24 samples
- Updated ONLY when `status == healthy` and `score >= 80`
- Injected into Claude prompt as deviation context: "P95 is 43.6x baseline, errors 90.0x baseline"
- This is what makes the AI diagnosis accurate — it knows what "normal" looks like

---

### 3.4 AI Reasoning

**Files:** `orchestrator/claude_engine.py`, `diagnostic_council.py`, `incident_policy.py`

**Council mode:** Two models (Haiku — latency/database specialist; Sonnet — network/dependency specialist) analyze telemetry independently. If they agree, one clean alert fires. If they diverge, a Dissent Alert is sent showing both theories. Controlled by `COUNCIL_ENABLED` env var.

**Policy injection:** `incident_policy.py` thresholds are injected into the Claude prompt so the AI uses the same recovery/validation thresholds as the deterministic state machine.

**Core principle:** AI is not the decision-maker. It is the reasoning layer after detection.

**Architecture:**

```
Orchestrator Core
       │
       ├───► diagnostic_council.py (dual-model, when available)
       │         ├───► Model A: Haiku — fast hypothesis
       │         ├───► Model B: Sonnet — deep analysis
       │         └───► Divergence detection + confidence merge
       │
       └───► claude_engine.py (single-model fallback)
                 └───► Native tool use (schema-guaranteed output)
```

**Claude prompt design:**
- System prompt includes few-shot examples
- Injects current policy thresholds: "recover when score>70 and err<5%, validate when score<40 and err>20%"
- Injects baseline context: "P95 is 43.6x baseline"
- Injects commit context (Diff-in-Pocket): recent git commits correlated with incident start time
- Injects diagnosis memory for active incidents (multi-turn continuity)

**Native tool use:**
- Claude must call `incident_decision` tool
- Schema guarantees valid output — no JSON parsing failures
- Returns: `action`, `reason`, `confidence`, `whatsapp_message`

**Confidence rules (hard-coded in prompt):**
- `confidence < 0.6` → `suppress` (do not alert)
- `recover` only when score > 70 and error_rate < 0.05
- `validate` only when score < 40 and error_rate > 0.20
- Be conservative — false positives in fintech are costly

**Fail-safe:**
- If Claude API fails after 2 retries, returns `suppress` with 0% confidence
- If Claude returns invalid schema, falls back to suppress
- Attribution: `actor = "claude"` in audit log

**Diagnostic Council (when available):**
- Two models reason independently
- Divergence detection: if models disagree, confidence is lowered
- Merged diagnosis includes both hypotheses
- Recorded in audit metadata: `council_mode`, `diverged`, `diagnosis_a`, `diagnosis_b`

---

### 3.5 Human Authorization

**Files:** `orchestrator/action_generator.py`, recovery webhook (customer-owned)

**Flow:**
1. Orchestrator generates single-use JWT token (`generate_recovery_token`)
2. Token is tenant-scoped, 5-minute TTL, single-use
3. Recovery URL sent via WhatsApp/Telegram: `https://tenant.alertengine.io/action/recover?token=...`
4. Engineer taps the link — this hits `GET /action/recover` (preview only, zero side effects)
5. Engineer taps "Approve" — this hits `POST /action/recover/confirm` (irreversible, requires valid JWT)
6. Orchestrator validates JWT via atomic Redis SET NX (replay protection)
7. Orchestrator calls customer's recovery webhook URL (configured during onboarding)
8. Customer's webhook executes the fix (restart worker, clear cache, scale service)
9. Orchestrator confirms success/failure to engineer

**Security guarantees:**
- Nothing executes without explicit approval
- Preview endpoint is read-only
- JWT: tenant-scoped, 5-min TTL, single-use, atomic Redis SET NX
- Replay protection: exactly 1 succeeds, 19 rejected in concurrent flood test
- Orchestrator never SSHs into customer machines
- 3 retries with exponential backoff on webhook failure
- Dead Letter Queue (DLQ) for unrecoverable failures (Startup+ tier)

---

## 4. Data Flow: Exact Execution Sequence

### 4.1 New Incident Detection

```
1. loop.py:_run_once()
   └── list_active_tenants()

2. loop.py:_process_tenant(tenant)
   ├── _fetch_health(health_url) → health dict
   ├── if status == healthy and score >= 80:
   │   └── _update_baseline_safe(tenant_id, health)
   ├── _get_tenant_incident(tenant_id) → None (no active incident)
   ├── if status == critical:
   │   ├── incident_lock("creating-{tenant_id}", ttl=10)
   │   ├── should_open_new_incident() → True
   │   ├── claim_action(creation_key) → True (atomic SET NX)
   │   ├── should_alert(score, err) → True
   │   ├── can_monitor_more_services(tenant) → True
   │   ├── incident_quota_remaining(tenant) > 0
   │   ├── get_tenant_plan(tenant).has_claude_decision → True
   │   ├── lease.valid check
   │   ├── claude_decide(health, incident=None, tenant_id=tenant_id)
   │   │   ├── _build_prompt() → injects baseline, policy, commits
   │   │   ├── Anthropic API call with tool_use
   │   │   └── returns {action, reason, confidence, whatsapp_message}
   │   ├── decide_new_incident() → decision dict
   │   ├── validate_decision_schema() → (True, "")
   │   ├── open_incident() → incident_record
   │   ├── save_incident() → Redis
   │   ├── _save_tenant_active() → Redis
   │   ├── increment_incident_count() → tenant dict
   │   ├── save_tenant() → Redis
   │   ├── append_event() → audit log (actor="claude", metadata={council_mode, diverged})
   │   └── _execute_actions()
   │       ├── GENERATE_TOKEN → recovery_url
   │       └── SEND_NOTIFICATION → WhatsApp/Telegram
   └── return
```

### 4.2 Incident Recovery

```
1. loop.py:_process_tenant(tenant)
   ├── _fetch_health(health_url) → health dict
   ├── _get_tenant_incident(tenant_id) → incident dict
   ├── incident_lock(incident_id)
   ├── lease.valid check
   ├── if status in (healthy, degraded) and stage != RECOVERED:
   │   ├── claude_decide(health, incident=incident, tenant_id=tenant_id)
   │   │   ├── _build_history_messages() → multi-turn diagnosis memory
   │   │   ├── _build_prompt() → includes prior hypotheses
   │   │   └── returns {action: "recover", ...}
   │   ├── decide(incident, health, claude_output) → decision
   │   ├── validate_decision_schema() → True
   │   ├── if next_stage == "RECOVERED":
   │   │   ├── can_mutate_state() → True
   │   │   ├── apply_transition(incident, "RECOVERED") → updated
   │   │   ├── save_incident(updated) → Redis
   │   │   ├── resolve_incident(incident_id) → Redis
   │   │   ├── _clear_tenant_active(tenant_id) → Redis
   │   │   ├── clear_history(incident_id) → diagnosis memory
   │   │   ├── append_event() → audit log (stage="RECOVERED")
   │   │   └── _execute_actions() → SEND_RECOVERY notification
   └── return
```

---

## 5. Policy-First Design Philosophy

### From "code decides incidents" → "policy decides incidents"

**Before (anti-pattern):**
```python
# Logic scattered across files
if score < 30 and err > 0.15:
    alert()
# Thresholds hard-coded in 7 places
# Tuning requires code changes and redeploys
```

**After (AlertEngine pattern):**
```python
# Single policy file controls all thresholds
POLICY = {
    "recover_score": 70,
    "recover_error_rate": 0.05,
    "validate_score": 40,
    "validate_error_rate": 0.20,
    "suppress_confidence": 0.60,
}

# Policy version is auditable
# A/B testing is a config change, not a deploy
# Rollbacks are deterministic
```

**Why this matters for compliance:**
- Regulated industries require documented, versionable decision rules
- Auditors can read `policy.py` and `plans.py` to understand exactly when alerts fire
- AI is not a black box that "decides" — it is a bounded reasoning tool that operates within a policy envelope

**Policy override pattern:**
If `pipeline.py` ever needs to override Claude's recommendation based on hard policy rules, that override must be auditable:

```python
# Pseudocode for the pattern
if health["score"] > POLICY["recover_score"] and claude_output["action"] != "recover":
    audit.append_event(
        incident_id=incident_id,
        stage="RECOVERED",
        decision="recover",
        actor="policy_override",  # ← explicit
        reason="score above recover threshold, overriding Claude recommendation",
        confidence=1.0,
        metadata={"claude_recommended": claude_output["action"]}
    )
    return {"action": "recover", ...}
```

This makes the architecture self-documenting: **policy is the floor, AI is the ceiling.**

---

## 6. Compliance Mapping

Every design decision maps to a real compliance requirement:

| Requirement | Implementation | File |
|-------------|---------------|------|
| Human authorization before execution | Engineer must tap approve; no autonomous remediation | `action_generator.py`, recovery webhook |
| Immutable audit trail | Append-only Redis LIST; every stage, decision, approval recorded | `audit.py` |
| Replay attack prevention | Single-use JWT tokens; atomic Redis SET NX | `idempotency.py`, `action_generator.py` |
| Cross-tenant data isolation | Tenant ID validated on every endpoint; 403 on mismatch | `loop.py`, `tenants.py` |
| Separation of duties | Free SDK (data plane) and orchestrator (control plane) fully isolated | Architecture boundary |
| Incident documentation | Full timeline reconstructable from audit log: DETECTED → AUTHORIZED → EXECUTED | `audit.py` |
| Degraded mode handling | NORMAL / DEGRADED / EMERGENCY with automatic transitions | `degraded.py` |
| Recovery action accountability | Who approved, when, what executed — all logged with timestamps | `audit.py` |
| Deterministic alert rules | Single policy file controls thresholds; versionable | `policy.py`, `incident_policy.py` |

**The accounting parallel:** In accounting, no transaction executes without authorization and every action leaves an audit trail. AlertEngine applies that same discipline to production infrastructure.

---

## 7. Audience-Specific Guidance

### 7.1 For Security Auditors

**What to review:**
1. `orchestrator/loop.py` — verify policy gates run before AI calls
2. `orchestrator/policy.py` — verify thresholds are deterministic and versionable
3. `orchestrator/audit.py` — verify append-only, no updates, 7-day TTL
4. `orchestrator/idempotency.py` — verify atomic SET NX for replay protection
5. `orchestrator/lock.py` — verify Lua script atomic release, no race conditions
6. `orchestrator/claude_engine.py` — verify tool_use schema guarantees, fail-safe defaults
7. `orchestrator/action_generator.py` — verify JWT tenant-scoping, TTL, single-use

**Questions to ask:**
- What happens if Redis is unavailable? → `degraded.py` enters EMERGENCY mode; no state mutations, no escalations
- What happens if Claude returns a recommendation that violates policy? → Currently, `pipeline.py` should validate and override. Verify this in your audit.
- Can a tenant access another tenant's incident data? → No. Every Redis key is prefixed with `orchestrator:active_incident:{tenant_id}` and `orchestrator:incident:{incident_id}`. Cross-tenant access returns 403.
- What happens in a concurrent token flood? → Adversarial audit passed: 20 concurrent requests → exactly 1 succeeded, 19 rejected.

**Audit artifacts:**
- 232 tests passing (Python 3.10, 3.11, 3.12)
- Adversarial audit by autonomous AI agent: 10/10 checks passed
- Source-available orchestrator for inspection (not for self-hosting)

### 7.2 For Developers

**Getting started:**
```bash
pip install fastapi-alertengine
```

```python
from fastapi import FastAPI
from fastapi_alertengine import instrument

app = FastAPI()
instrument(app)  # that's it
```

Your app now exposes `/health/alerts`. The orchestrator polls this endpoint every 5 seconds.

**Local development (no orchestrator needed):**
```bash
git clone https://github.com/Tandem-Media/fastapi-alertengine
cd fastapi-alertengine
pip install fastapi-alertengine uvicorn httpx
uvicorn examples.quickstart_example:app --reload

# In another terminal — simulate a spike
curl -X POST localhost:8000/simulate/spike
curl -s localhost:8000/health/alerts | python3 -m json.tool
```

**Key integration points:**
1. **Recovery webhook** — During onboarding, you provide a URL like `https://your-api.com/webhooks/recovery`. The orchestrator calls this after human approval. You control what the webhook does.
2. **Environment variables** — `REDIS_URL`, `ALERTENGINE_BASE_URL`, `ANTHROPIC_API_KEY`, `ALERT_SECRET`
3. **Plan selection** — Start with Hobby ($19/mo, Telegram only) or Developer ($99/mo, WhatsApp + AI decisions)

**SDK behavior:**
- Memory mode: works without Redis (metrics stored in-process, lost on restart)
- Circuit breaker: buffers events during Redis outages
- Never crashes your app — all exceptions are caught and logged

### 7.3 For Enterprise Buyers

**Value proposition:**
- **Financial-grade authorization discipline** applied to API infrastructure
- **Human-in-the-loop** — nothing executes without explicit engineer approval
- **Immutable audit trail** — every decision, every approval, every execution is logged
- **Deterministic policy engine** — versionable thresholds, A/B testable, rollback-safe
- **AI as explanation, not decision** — reduces alert fatigue without removing human judgment
- **Mobile-first operations** — WhatsApp/Telegram alerts find engineers, not dashboards

**Deployment model:**
- **Free SDK** runs on your servers (MIT license). Zero side effects. Pure measurement.
- **Orchestrator** runs on Tandem Media's managed infrastructure. Source-available for audit. Not self-hosted.
- **Enterprise tier** includes dedicated deployment (separate managed instance under SLA) and custom features.

**Security model:**
- Orchestrator never SSHs into your machines
- Orchestrator only calls your webhook after human approval
- All communication is over HTTPS
- JWT tokens are tenant-scoped, time-bound, and single-use
- Redis is used for state and audit — not for sensitive customer data

**Compliance readiness:**
- SOC 2 Type II roadmap (contact for timeline)
- GDPR: no PII stored in orchestrator; tenant data is ephemeral (7-day TTL on audit logs)
- PCI DSS: orchestrator does not handle payment data; SDK is pure measurement

**Pricing for scale:**
- Scale: $1,500/mo — 20 services, 1,000 incidents/mo, all channels + voice escalation
- Enterprise: Custom — unlimited services and incidents, dedicated deployment, custom SLA

---

## 8. Reliability Guarantees

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

---

## 9. Glossary

| Term | Definition |
|------|-----------|
| **SDK** | The `fastapi_alertengine` Python package that runs on customer servers. MIT licensed. |
| **Orchestrator** | The managed service that polls health endpoints, runs AI diagnosis, and sends alerts. Source-available. |
| **Policy Engine** | The deterministic rules layer (`policy.py`, `plans.py`) that gates all incident decisions. |
| **AI Reasoning** | The Claude/council layer that explains incidents in natural language after policy gates pass. |
| **Human Authorization** | The tap-to-approve flow via JWT recovery links. Nothing executes without explicit approval. |
| **Baseline** | EMA of normal P95 and error rate. Updated only on healthy polls. |
| **Diagnostic Council** | Dual-model AI reasoning (Haiku + Sonnet) with divergence detection. |
| **Diff-in-Pocket** | Commit context injection — correlates incidents with recent deployments. |
| **DLQ** | Dead Letter Queue — captures unrecoverable webhook failures for manual replay. |
| **Circuit Breaker** | Per-provider per-tenant failure isolation. 3 failures → 60s cooldown. |

---

## 10. Contact & License

**Free SDK** (`fastapi_alertengine/`): MIT — see `LICENSE`

**Orchestrator** (`orchestrator/`): Source-available for audit only — see `LICENSE-ORCHESTRATOR.md`

**Contact:** anchorflowalertengine@outlook.com

**Built in Zimbabwe.** Engineers here aren't always at laptops when things break. WhatsApp is the operational control plane.
