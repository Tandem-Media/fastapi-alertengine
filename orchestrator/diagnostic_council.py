# orchestrator/diagnostic_council.py
"""
Diagnostic Council — the AI-driven incident court.

Instead of asking one model "what broke?", two models with different
diagnostic lenses independently analyze the same telemetry. If they
agree, one clean alert fires. If they diverge, a Dissent Alert is sent:

    ⚠️ Degraded State — Models Disagree
    Model A (Latency): Database lock contention
    Model B (Network): Upstream API timeout
    Confidence: Low — independent analyses diverge

    Check before approving:
    → DB slow query log
    → Upstream API response times

    👉 Approve A  👉 Approve B  👉 Investigate manually

Why this matters:
- Kills the "unanimous-but-wrong" failure mode
- Pre-loads the engineer with uncertainty before they tap approve
- Shifts role from "fix the system" to "arbitrate between theories"
- Audit trail records which model the engineer trusted → ground truth
- Over time: evidence-based model weighting by incident type

The disagreement is the most important information you can give
a human in a high-stakes production environment.
"""

import asyncio
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("orchestrator.diagnostic_council")

# ── Model configuration ────────────────────────────────────────────────────────

# Model A — fast, latency-focused (Haiku)
MODEL_A = os.getenv("COUNCIL_MODEL_A", "claude-haiku-4-5-20251001")
ROLE_A  = "latency and database performance specialist"
LENS_A  = "Focus on: P95 latency patterns, connection pool exhaustion, database lock contention, slow queries, resource saturation. Your lens is performance and throughput."

# Model B — reasoning-focused (Sonnet)
MODEL_B = os.getenv("COUNCIL_MODEL_B", "claude-sonnet-4-6")
ROLE_B  = "network and dependency specialist"
LENS_B  = "Focus on: upstream API failures, network timeouts, third-party service degradation, DNS issues, SSL/TLS problems, external dependency health. Your lens is connectivity and dependencies."

API_URL    = "https://api.anthropic.com/v1/messages"
MAX_TOKENS = 600
TIMEOUT    = 15

# Divergence threshold — below this similarity score, fire dissent alert
DIVERGENCE_THRESHOLD = float(os.getenv("COUNCIL_DIVERGENCE_THRESHOLD", "0.6"))

# Whether to run council (can be disabled to save cost)
COUNCIL_ENABLED = os.getenv("COUNCIL_ENABLED", "true").lower() == "true"


# ── Tool schema ────────────────────────────────────────────────────────────────

COUNCIL_TOOLS = [{
    "name": "council_diagnosis",
    "description": "Provide your independent diagnostic assessment of this incident",
    "input_schema": {
        "type": "object",
        "properties": {
            "root_cause": {
                "type": "string",
                "description": "Your assessment of the most likely root cause (one sentence)"
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Your confidence in this assessment (0.0 to 1.0)"
            },
            "category": {
                "type": "string",
                "enum": ["database", "network", "memory", "cpu", "external_api", "code_deploy", "unknown"],
                "description": "The category of failure"
            },
            "suggested_action": {
                "type": "string",
                "description": "The single most important thing to check or do first"
            },
            "log_to_check": {
                "type": "string",
                "description": "The specific log, metric, or endpoint to examine to confirm or deny this theory"
            }
        },
        "required": ["root_cause", "confidence", "category", "suggested_action", "log_to_check"]
    }
}]


# ── Council prompt builder ─────────────────────────────────────────────────────

def _build_council_prompt(health: dict, incident: Optional[dict], role: str, lens: str, tenant_id: str = "") -> str:
    hs  = health.get("health_score", {})
    m   = health.get("metrics", {})
    inc = incident or {}

    lines = [
        f"You are an independent {role}.",
        f"{lens}",
        "",
        "Analyze this incident with your specialized lens only.",
        "Do not try to cover all possibilities — give your single best theory.",
        "",
        f"Service: {inc.get('service_name', 'API')}",
        f"Health status: {hs.get('status', 'unknown')}",
        f"Score: {hs.get('score', 100):.0f}/100",
        f"Trend: {hs.get('trend', 'stable')}",
        f"P95 latency: {m.get('overall_p95_ms', 0):.0f}ms",
        f"Error rate: {m.get('error_rate', 0)*100:.1f}%",
        f"Anomaly score: {m.get('anomaly_score', 0):.2f}",
    ]

    # Inject baseline context
    if tenant_id:
        try:
            from baseline import baseline_context
            ctx = baseline_context(tenant_id, m.get("overall_p95_ms", 0), m.get("error_rate", 0))
            if ctx:
                lines.append(ctx)
        except Exception:
            pass

    # Inject commit context
    if inc and tenant_id:
        try:
            from commit_context import commit_context
            import time
            started_at = inc.get("started_at", time.time())
            ctx = commit_context(tenant_id, started_at)
            if ctx:
                lines.append(ctx)
        except Exception:
            pass

    lines += [
        "",
        "Provide your independent assessment using the council_diagnosis tool.",
        "Be specific. Your theory will be compared against another specialist's theory.",
        "If they diverge, the engineer will see both and decide.",
    ]

    return "\n".join(lines)


# ── Single model call ──────────────────────────────────────────────────────────

async def _ask_model(
    model: str,
    prompt: str,
    api_key: str,
    role_label: str,
) -> Optional[dict]:
    """Ask one council model for its diagnosis. Returns None on failure."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                API_URL,
                headers={
                    "x-api-key":         api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type":      "application/json",
                },
                json={
                    "model":       model,
                    "max_tokens":  MAX_TOKENS,
                    "messages":    [{"role": "user", "content": prompt}],
                    "tools":       COUNCIL_TOOLS,
                    "tool_choice": {"type": "tool", "name": "council_diagnosis"},
                },
            )

        if r.status_code != 200:
            logger.warning("Council %s returned %d", role_label, r.status_code)
            return None

        for block in r.json().get("content", []):
            if block.get("type") == "tool_use":
                diagnosis = block.get("input", {})
                logger.info(
                    "Council %s: %s (%.0f%%) — %s",
                    role_label,
                    diagnosis.get("category", "unknown"),
                    diagnosis.get("confidence", 0) * 100,
                    diagnosis.get("root_cause", "")[:80],
                )
                return diagnosis

        logger.warning("Council %s: no tool_use block", role_label)
        return None

    except Exception as e:
        logger.warning("Council %s failed: %s", role_label, e)
        return None


# ── Divergence detection ───────────────────────────────────────────────────────

def _models_diverge(diagnosis_a: dict, diagnosis_b: dict) -> bool:
    """
    Returns True if the two diagnoses are meaningfully different.
    
    Checks:
    1. Different root cause categories (database vs network = strong divergence)
    2. Both confident but pointing at different things
    3. One high confidence, one low (uncertainty signal)
    """
    cat_a = diagnosis_a.get("category", "unknown")
    cat_b = diagnosis_b.get("category", "unknown")
    conf_a = diagnosis_a.get("confidence", 0)
    conf_b = diagnosis_b.get("confidence", 0)

    # Same category = agreement
    if cat_a == cat_b:
        return False

    # Both low confidence = uncertain, not divergent
    if conf_a < 0.5 and conf_b < 0.5:
        return False

    # One confident in a different direction = genuine divergence
    if cat_a != cat_b and (conf_a > 0.6 or conf_b > 0.6):
        return True

    return False


# ── Dissent alert formatter ────────────────────────────────────────────────────

def _format_dissent_alert(
    diagnosis_a: dict,
    diagnosis_b: dict,
    incident_id: str,
    score: float,
    p95: float,
    approve_a_url: str = "",
    approve_b_url: str = "",
) -> str:
    """Format the dissent WhatsApp message."""
    conf_a = diagnosis_a.get("confidence", 0)
    conf_b = diagnosis_b.get("confidence", 0)

    lines = [
        f"⚠️ Degraded State — Models Disagree",
        f"Score: {score:.0f}/100 | P95: {p95:.0f}ms",
        "",
        f"Theory A ({diagnosis_a.get('category', 'unknown').replace('_', ' ').title()}):",
        f"  {diagnosis_a.get('root_cause', 'Unknown')}",
        f"  Confidence: {conf_a*100:.0f}%",
        f"  Check: {diagnosis_a.get('log_to_check', 'N/A')}",
        "",
        f"Theory B ({diagnosis_b.get('category', 'unknown').replace('_', ' ').title()}):",
        f"  {diagnosis_b.get('root_cause', 'Unknown')}",
        f"  Confidence: {conf_b*100:.0f}%",
        f"  Check: {diagnosis_b.get('log_to_check', 'N/A')}",
        "",
        "The models disagree. Investigate before approving.",
        "",
    ]

    if approve_a_url:
        lines.append(f"👉 Trust Theory A: {approve_a_url}")
    if approve_b_url:
        lines.append(f"👉 Trust Theory B: {approve_b_url}")

    lines.append("Nothing will run without your approval.")

    return "\n".join(lines)


def _format_consensus_alert(
    diagnosis: dict,
    incident_id: str,
    score: float,
    p95: float,
    recovery_url: str = "",
) -> str:
    """Format the consensus WhatsApp message (both models agreed)."""
    lines = [
        f"⚠️ Action Recommended",
        f"Score: {score:.0f}/100 | P95: {p95:.0f}ms",
        "",
        f"Issue: {diagnosis.get('root_cause', 'Unknown')}",
        f"Suggested fix: {diagnosis.get('suggested_action', 'Investigate')}",
        "",
        f"Confidence: {diagnosis.get('confidence', 0)*100:.0f}% (both models agree)",
        f"👉 Approve fix: {recovery_url}",
        "",
        "Nothing will run without your approval.",
    ]
    return "\n".join(lines)


# ── Main council entry point ───────────────────────────────────────────────────

async def council_diagnose(
    health: dict,
    incident: Optional[dict] = None,
    tenant_id: str = "",
    recovery_url: str = "",
) -> dict:
    """
    Run the diagnostic council: two models, one verdict or one dissent.

    Returns a dict with:
        "mode":         "consensus" | "dissent" | "fallback"
        "action":       "validate" | "escalate" | "suppress"
        "whatsapp_message": str
        "confidence":   float
        "reason":       str
        "diagnosis_a":  dict | None
        "diagnosis_b":  dict | None
        "diverged":     bool
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or not COUNCIL_ENABLED:
        # Fall back to single model
        try:
            from claude_engine import get_decision
            decision = await get_decision(health, incident, tenant_id, recovery_url)
            return {**decision, "mode": "fallback", "diverged": False,
                    "diagnosis_a": None, "diagnosis_b": None}
        except Exception as e:
            logger.error("Council fallback failed: %s", e)
            return _safe_default()

    hs    = health.get("health_score", {})
    m     = health.get("metrics", {})
    score = hs.get("score", 100)
    p95   = m.get("overall_p95_ms", 0)

    # Build prompts for each specialist
    prompt_a = _build_council_prompt(health, incident, ROLE_A, LENS_A, tenant_id)
    prompt_b = _build_council_prompt(health, incident, ROLE_B, LENS_B, tenant_id)

    # Run both models in parallel
    diagnosis_a, diagnosis_b = await asyncio.gather(
        _ask_model(MODEL_A, prompt_a, api_key, "Model-A"),
        _ask_model(MODEL_B, prompt_b, api_key, "Model-B"),
        return_exceptions=False,
    )

    incident_id = (incident or {}).get("incident_id", "unknown")

    # Handle model failures
    if not diagnosis_a and not diagnosis_b:
        logger.error("Both council models failed — falling back")
        return _safe_default()

    if not diagnosis_a:
        diagnosis_a = diagnosis_b
    if not diagnosis_b:
        diagnosis_b = diagnosis_a

    # Check for divergence
    diverged = _models_diverge(diagnosis_a, diagnosis_b)

    if diverged:
        logger.warning(
            "Council divergence: A=%s vs B=%s",
            diagnosis_a.get("category"), diagnosis_b.get("category"),
        )
        message = _format_dissent_alert(
            diagnosis_a, diagnosis_b,
            incident_id, score, p95,
            approve_a_url=recovery_url,
            approve_b_url=recovery_url,
        )
        # Confidence is the lower of the two when diverged
        confidence = min(
            diagnosis_a.get("confidence", 0),
            diagnosis_b.get("confidence", 0),
        )
        return {
            "mode":              "dissent",
            "action":            "validate",
            "whatsapp_message":  message,
            "confidence":        confidence,
            "reason":            f"Models diverge: {diagnosis_a.get('category')} vs {diagnosis_b.get('category')}",
            "diagnosis_a":       diagnosis_a,
            "diagnosis_b":       diagnosis_b,
            "diverged":          True,
        }

    # Consensus — use the higher-confidence diagnosis
    primary = diagnosis_a if diagnosis_a.get("confidence", 0) >= diagnosis_b.get("confidence", 0) else diagnosis_b
    confidence = max(diagnosis_a.get("confidence", 0), diagnosis_b.get("confidence", 0))

    if confidence < 0.6:
        return {
            "mode":              "consensus",
            "action":            "suppress",
            "whatsapp_message":  "",
            "confidence":        confidence,
            "reason":            "Both models uncertain — suppressing to avoid false alert",
            "diagnosis_a":       diagnosis_a,
            "diagnosis_b":       diagnosis_b,
            "diverged":          False,
        }

    message = _format_consensus_alert(primary, incident_id, score, p95, recovery_url)

    return {
        "mode":              "consensus",
        "action":            "validate",
        "whatsapp_message":  message,
        "confidence":        confidence,
        "reason":            primary.get("root_cause", ""),
        "diagnosis_a":       diagnosis_a,
        "diagnosis_b":       diagnosis_b,
        "diverged":          False,
    }


def _safe_default() -> dict:
    return {
        "mode":             "fallback",
        "action":           "suppress",
        "whatsapp_message": "",
        "confidence":       0.0,
        "reason":           "Council unavailable — suppressing to avoid false alert",
        "diagnosis_a":      None,
        "diagnosis_b":      None,
        "diverged":         False,
    }
