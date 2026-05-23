# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| Latest (PyPI) | ✅ |
| Older versions | ❌ |

Always use the latest version from PyPI:

```bash
pip install --upgrade fastapi-alertengine
```

---

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Email: anchorflowalertengine@outlook.com

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will acknowledge within 48 hours and aim to resolve critical issues within 7 days.

---

## Adversarial Audit Results

FastAPI AlertEngine was subjected to a full adversarial audit by an autonomous AI agent acting as a hostile tenant. The agent attempted to break cross-tenant isolation, replay authorization tokens, and overwhelm the system with concurrent requests.

**Result: 10/10 checks passed.**

| Check | Result | Detail |
|---|---|---|
| Cross-tenant audit access | ✅ Blocked | 403 returned |
| Cross-tenant delivery access | ✅ Blocked | 403 returned |
| Recovery token replay (20 concurrent) | ✅ Protected | 1 succeeded, 19 rejected |
| Duplicate incident creation (race) | ✅ Protected | Exactly 1 created |
| Concurrent token flood | ✅ Handled | Atomic Redis SET NX |
| Natural incident detection | ✅ Confirmed | End-to-end verified |
| WhatsApp delivery | ✅ Confirmed | Live production delivery |
| Recovery authorization audit trail | ✅ Written | Immutable append-only log |
| Degraded mode handling | ✅ Confirmed | NORMAL/DEGRADED/EMERGENCY |
| Lease renewal under load | ✅ Atomic | Lua compare-and-delete |

---

## Security Design Principles

**1. Human authorization required — always**

No recovery action executes without explicit human approval. The system is designed to fail safe — if authorization is unclear or unavailable, nothing runs.

**2. Single-use JWT tokens**

Every recovery link contains a tenant-scoped JWT with a 5-minute TTL. Tokens are validated atomically in Redis using `SET NX` — the first request burns the token, all subsequent requests return 403.

**3. Cross-tenant isolation**

Every endpoint enforces tenant ownership. Tenant ID is validated against the JWT payload on every request. No cross-tenant data access is possible.

**4. Immutable audit trail**

Every stage transition, delivery attempt, and authorization event is written to an append-only log. Nothing happens silently. The full incident timeline is always reconstructable.

**5. Circuit breaker and degraded mode**

Redis outages trigger memory fallback in the SDK. The orchestrator enters degraded mode — notifications continue, mutations are gated. The system never crashes the host application.

**6. Lua atomic scripts**

Circuit breaker resets and token validations use Redis Lua scripts for atomicity. No race conditions on concurrent worker access.

---

## Dependency Security

The free SDK (`fastapi-alertengine`) has zero required dependencies beyond FastAPI itself. Optional Redis integration uses the standard `redis` package.

Keep dependencies updated:

```bash
pip install --upgrade fastapi-alertengine
```

---

## Contact

Security issues: anchorflowalertengine@outlook.com  
General: anchorflowalertengine@outlook.com  
GitHub: https://github.com/Tandem-Media/fastapi-alertengine
