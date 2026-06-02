# Contributing to FastAPI AlertEngine

Thank you for your interest in contributing. This document explains how to get involved.

\---

## What We Welcome

* Bug reports with reproducible examples
* Performance improvements to the middleware
* Additional test coverage
* Documentation improvements
* New provider integrations (notification channels)
* Redis fallback and memory backend improvements

## What We Don't Accept (Yet)

* Changes to the orchestrator (commercial layer) — this is not open source
* Breaking changes to the `/health/alerts` response schema without discussion
* Dependencies that aren't zero-dependency for the core SDK

\---

## Getting Started

```bash
git clone https://github.com/Tandem-Media/fastapi-alertengine
cd fastapi-alertengine
pip install -e ".\\\[dev]"
```

Run the test suite:

```bash
pytest tests/ -v
```

All 232 tests must pass before submitting a PR.

\---

## How to Contribute

**1. Open an issue first**

Before writing code, open an issue describing what you want to change and why. This avoids wasted effort if the change doesn't align with the project direction.

**2. Fork and branch**

```bash
git checkout -b fix/your-fix-name
# or
git checkout -b feature/your-feature-name
```

**3. Write tests**

Every change needs a test. We maintain 100% test coverage on the core middleware. PRs without tests will not be merged.

**4. Keep it focused**

One PR per change. Don't bundle unrelated fixes.

**5. Submit your PR**

Describe what changed, why, and how to verify it. Link to the issue it resolves.

\---

## Code Style

* Black for formatting
* Type hints required
* Docstrings for public functions
* No breaking changes to public API without a major version bump

\---

## Reporting Bugs

Open a GitHub issue with:

* Python version
* FastAPI version
* Redis version (if applicable)
* Minimal reproducible example
* Expected vs actual behaviour

\---

## Security Issues

Do not open a public issue for security vulnerabilities. See [SECURITY.md](SECURITY.md).

\---

## Questions

Email: anchorflowalertengine@outlook.com

