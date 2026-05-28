# Orchestrator License

## Source-Available, Not Open Source

The files contained in the `orchestrator/` directory of this repository
are **source-available for review and audit purposes only**.

They are **not** licensed under the MIT License.

## What You Can Do

✅ Read the source code for security auditing and evaluation  
✅ Study the architecture and implementation patterns  
✅ Reference the code when integrating with the managed service  
✅ Submit bug reports and security disclosures  

## What You Cannot Do

❌ Run the orchestrator as a managed service for third parties  
❌ Use the orchestrator code to build a competing incident management service  
❌ Redistribute the orchestrator code under any license  
❌ Sub-license the orchestrator code  

## Why We Publish It

We publish the orchestrator source for two reasons:

1. **Security transparency** — Our adversarial audit claims are verifiable.
   Anyone can read the JWT validation, Redis SET NX replay protection,
   cross-tenant isolation, and circuit breaker logic in full.

2. **Integration confidence** — Teams evaluating AlertEngine can audit
   exactly how their health data is processed, how recovery tokens are
   generated, and how the audit trail is maintained before committing
   to a subscription.

## The Free SDK

The `fastapi_alertengine/` package remains fully MIT licensed.

```
pip install fastapi-alertengine
```

You can use, modify, redistribute, and build on the free SDK without
restriction. See [LICENSE](LICENSE) for the full MIT license text.

## Commercial Use

The managed orchestrator is available as a hosted service starting
at $19/mo. See [pricing](https://tandem-media.github.io/fastapi-alertengine/#pricing).

For on-premise enterprise deployments or licensing inquiries:

**Contact:** anchorflowalertengine@outlook.com

---

*FastAPI AlertEngine — Built in Zimbabwe. Shipped globally.*
