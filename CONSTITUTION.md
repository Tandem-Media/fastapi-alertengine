═══════════════════════════════════════════════════════════
ALERTENGINE — PROJECT CONSTITUTION
Project: FastAPI AlertEngine + AnchorFlow/Tofamba
Version: 1.0
Maintainer: Lenard Francis, Tandem Media
Contact: anchorflowalertengine@outlook.com
═══════════════════════════════════════════════════════════

CORE PHILOSOPHY

Nothing executes without explicit human authorization.
Every action is logged immutably.
The system fails safe — never fails open.
AI proposes. Human authorizes. Audit trail proves.
Deterministic by design. AI is enrichment, not infrastructure.

SOURCE OF TRUTH FILES

  CONSTITUTION.md  — The rules that never change
  requirements.md  — What the system must do
  design.md        — How the system is built
  tasks.md         — What to build next, in order
  SECURITY.md      — What the system must never do

MANDATORY BEFORE ANY ACTION

  1. Read CONSTITUTION.md in full
  2. Read requirements.md in full
  3. Read design.md in full
  4. Read SECURITY.md — understand the threat model
  5. Read tasks.md — identify the next incomplete [ ] task
  6. Run the test suite — all 232+ tests must pass before starting

HARD CONSTRAINTS

  ✗ Never add autonomous execution — human approval required always
  ✗ Never store secrets in code — environment variables only
  ✗ Never break the /health/alerts response schema without a major version bump
  ✗ Never merge without 232+ tests passing
  ✗ Never cross tenant boundaries — 403 on mismatch, always
  ✗ Never fail open on Redis errors — fail closed
  ✗ Never skip the audit trail — every stage transition logged
  ✗ Never suppress Claude diagnosis without surfacing raw metrics
  ✗ Never execute a recovery action without a valid JWT token
  ✗ Never allow a JWT token to be used more than once
  ✗ Never add a dependency to the free SDK without explicit approval
  ✗ Never remove the memory fallback — Redis is optional, not required
  ✗ Never guess when a requirement is ambiguous — ask instead

DIVERGENCE PROTOCOL

  If implementation must deviate from design.md:
    → Stop immediately
    → Describe the conflict clearly
    → Wait for explicit human approval
    → Update design.md BEFORE writing code
    → Update tests BEFORE merging
    → Document the deviation in the commit message

ARCHITECTURAL INVARIANTS

  The free SDK (fastapi_alertengine/) never depends on the orchestrator
  The orchestrator never crashes the host FastAPI application
  JWT tokens are always: single-use, tenant-scoped, time-limited (5 min)
  Audit logs are always: append-only, never mutable, 7-day retention
  Recovery actions always: GET = preview only, POST = execute (irreversible)
  AI diagnosis is always: optional enrichment, never on the critical path
  Health scoring is always: deterministic, P95-based, not AI-dependent
  Circuit breakers are always: distributed (Redis-backed), not in-memory

THE ZIMBABWE CONSTRAINT

  Engineers are mobile-first.
  WhatsApp is the operational control plane.
  Every alert must be actionable from a phone.
  Every recovery must be approvable with one tap.
  If it requires a laptop at 3am, it is not good enough.
  If it requires a dashboard to interpret, it is not good enough.
  The product must work where the engineer already is.

FINANCIAL-GRADE AUTHORIZATION DISCIPLINE

  This system applies accounting principles to software operations:
  - No transaction executes without authorization (like finance)
  - Every action leaves an audit trail (like accounting)
  - Auditors can reconstruct what happened and who approved it
  - Exceptions are documented, not ignored
  - The system is designed to pass a compliance review

WHAT SUCCESS LOOKS LIKE

  A production incident is detected within 5 seconds.
  The engineer receives a plain-English WhatsApp message.
  The engineer taps approve from their phone.
  The fix executes.
  The audit trail is complete.
  The engineer goes back to sleep.

═══════════════════════════════════════════════════════════
