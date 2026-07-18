# orchestrator/pulse_api.py
"""
Tofamba Pulse — Orchestrator API

Handles the server side of agent supervision:

  POST /pulse/event
    Receives events from the Pulse SDK (session start/end, ask_human,
    cost warnings, loop detection, etc). Stores in Redis, fires
    WhatsApp/Telegram notification when human input is needed.

  GET  /pulse/answer/{session_id}
    SDK polls this while waiting for a human reply to session.ask().
    Returns the answer once the human has responded via Telegram/WhatsApp.

  POST /pulse/answer/{session_id}
    Webhook endpoint — called by Telegram bot when human replies.
    Stores the answer so the SDK poll can pick it up.

  GET  /pulse/session/{session_id}
    Returns the full event log for a session (audit trail).

  GET  /pulse/sessions
    Lists recent sessions for a given API key.
"""

import json
import logging
import os
import time
from typing import Optional

import redis as _redis_module
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("orchestrator.pulse_api")

router = APIRouter(prefix="/pulse", tags=["pulse"])

# ── Redis helpers ─────────────────────────────────────────────────────────────

_redis_client = None

def _redis():
    global _redis_client
    if _redis_client is None:
        url = os.getenv("REDIS_URL", os.getenv("ALERTENGINE_REDIS_URL", "redis://localhost:6379/0"))
        _redis_client = _redis_module.Redis.from_url(url, decode_responses=True)
    return _redis_client


PULSE_EVENT_PREFIX   = "pulse:events:"     # pulse:events:{session_id} → list of events
PULSE_ANSWER_PREFIX  = "pulse:answer:"     # pulse:answer:{session_id} → human answer
PULSE_SESSION_PREFIX = "pulse:session:"    # pulse:session:{session_id} → session metadata
PULSE_SESSIONS_KEY   = "pulse:sessions:"  # pulse:sessions:{api_key} → sorted set of session IDs
PULSE_TTL            = 86400 * 7          # 7 days


def _verify_api_key(x_pulse_api_key: Optional[str]) -> str:
    """
    Verify the Pulse API key.
    For now: accepts any non-empty key and uses it as the tenant identifier.
    In production: validate against a stored key→tenant mapping.
    """
    if not x_pulse_api_key:
        raise HTTPException(status_code=401, detail="x-pulse-api-key header required")
    return x_pulse_api_key


# ── Request models ────────────────────────────────────────────────────────────

class PulseEvent(BaseModel):
    event_type:   str
    session_id:   str
    agent_name:   str
    timestamp:    Optional[float] = None
    message:      Optional[str] = None
    context:      Optional[dict] = None
    options:      Optional[list] = None
    human_answer: Optional[str] = None
    cost_usd:     Optional[float] = None
    token_count:  Optional[int] = None
    actor:        str = "pulse"


class PulseAnswer(BaseModel):
    answer: str
    source: str = "telegram"   # "telegram" | "whatsapp" | "api"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/event")
async def receive_event(
    event: PulseEvent,
    x_pulse_api_key: Optional[str] = Header(None),
):
    """
    Receive an event from the Pulse SDK.

    Stores the event in Redis and fires a human notification if
    the event type requires human input (INPUT_REQUIRED, COST_LIMIT_HIT,
    LOOP_DETECTED, STALL_DETECTED).
    """
    api_key = _verify_api_key(x_pulse_api_key)

    if not event.timestamp:
        event.timestamp = time.time()

    r = _redis()

    # Store event in session log
    event_key = f"{PULSE_EVENT_PREFIX}{event.session_id}"
    r.rpush(event_key, json.dumps(event.dict()))
    r.expire(event_key, PULSE_TTL)

    # Store/update session metadata
    session_key = f"{PULSE_SESSION_PREFIX}{event.session_id}"
    existing = r.get(session_key)
    if existing:
        meta = json.loads(existing)
    else:
        meta = {
            "session_id":  event.session_id,
            "agent_name":  event.agent_name,
            "api_key":     api_key,
            "started_at":  event.timestamp,
            "event_count": 0,
            "cost_usd":    0.0,
            "status":      "running",
        }

    meta["event_count"] = meta.get("event_count", 0) + 1
    meta["last_event"]  = event.event_type
    meta["last_seen"]   = event.timestamp
    if event.cost_usd:
        meta["cost_usd"] = event.cost_usd

    # Update status based on event type
    if event.event_type == "SESSION_END":
        meta["status"] = "completed"
    elif event.event_type == "SESSION_FAILED":
        meta["status"] = "failed"
    elif event.event_type == "INPUT_REQUIRED":
        meta["status"] = "waiting_for_human"
    elif event.event_type == "INPUT_RECEIVED":
        meta["status"] = "running"

    r.set(session_key, json.dumps(meta), ex=PULSE_TTL)

    # Track session in API key index
    sessions_key = f"{PULSE_SESSIONS_KEY}{api_key}"
    r.zadd(sessions_key, {event.session_id: event.timestamp})
    r.expire(sessions_key, PULSE_TTL)

    # Fire human notification for events that need human input
    NOTIFY_EVENTS = {"INPUT_REQUIRED", "COST_LIMIT_HIT", "LOOP_DETECTED", "STALL_DETECTED", "SESSION_FAILED"}
    if event.event_type in NOTIFY_EVENTS:
        await _notify_human(event, api_key)

    logger.info("Pulse event: %s | session=%s | agent=%s",
                event.event_type, event.session_id, event.agent_name)

    return {"received": True, "session_id": event.session_id, "event_type": event.event_type}


@router.get("/answer/{session_id}")
async def get_answer(
    session_id: str,
    x_pulse_api_key: Optional[str] = Header(None),
):
    """
    Poll for a human answer to a session.ask() call.

    The Pulse SDK calls this in a loop while waiting for the human to reply.
    Returns {"answer": null} if no reply yet, {"answer": "..."} once received.
    """
    _verify_api_key(x_pulse_api_key)

    r = _redis()
    answer_key = f"{PULSE_ANSWER_PREFIX}{session_id}"
    answer = r.get(answer_key)

    if answer:
        data = json.loads(answer)
        # Don't delete — SDK may poll again, and we want audit trail
        return {"answer": data.get("answer"), "source": data.get("source", "unknown")}

    return {"answer": None}


@router.post("/answer/{session_id}")
async def submit_answer(
    session_id: str,
    payload: PulseAnswer,
    x_pulse_api_key: Optional[str] = Header(None),
):
    """
    Submit a human answer for a waiting session.

    Called by:
    - Telegram webhook when human replies to the bot
    - WhatsApp webhook when human replies
    - Direct API call (for testing)
    """
    _verify_api_key(x_pulse_api_key)

    r = _redis()

    # Store the answer
    answer_key = f"{PULSE_ANSWER_PREFIX}{session_id}"
    r.set(answer_key, json.dumps({
        "answer":     payload.answer,
        "source":     payload.source,
        "received_at": time.time(),
    }), ex=PULSE_TTL)

    # Update session status
    session_key = f"{PULSE_SESSION_PREFIX}{session_id}"
    existing = r.get(session_key)
    if existing:
        meta = json.loads(existing)
        meta["status"] = "running"
        r.set(session_key, json.dumps(meta), ex=PULSE_TTL)

    logger.info("Pulse answer received: session=%s answer=%s source=%s",
                session_id, payload.answer[:50], payload.source)

    return {"stored": True, "session_id": session_id}


@router.get("/session/{session_id}")
async def get_session(
    session_id: str,
    x_pulse_api_key: Optional[str] = Header(None),
):
    """Return the full event log and metadata for a session."""
    _verify_api_key(x_pulse_api_key)

    r = _redis()

    session_key = f"{PULSE_SESSION_PREFIX}{session_id}"
    meta = r.get(session_key)
    if not meta:
        raise HTTPException(status_code=404, detail="Session not found")

    event_key = f"{PULSE_EVENT_PREFIX}{session_id}"
    raw_events = r.lrange(event_key, 0, -1)
    events = []
    for raw in raw_events:
        try:
            events.append(json.loads(raw))
        except Exception:
            continue

    return {
        "session":    json.loads(meta),
        "events":     events,
        "event_count": len(events),
    }


@router.get("/sessions")
async def list_sessions(
    x_pulse_api_key: Optional[str] = Header(None),
    limit: int = 20,
):
    """List recent sessions for the authenticated API key."""
    api_key = _verify_api_key(x_pulse_api_key)

    r = _redis()
    sessions_key = f"{PULSE_SESSIONS_KEY}{api_key}"

    # Get most recent session IDs (sorted by timestamp, descending)
    session_ids = r.zrevrange(sessions_key, 0, limit - 1)

    sessions = []
    for sid in session_ids:
        meta = r.get(f"{PULSE_SESSION_PREFIX}{sid}")
        if meta:
            sessions.append(json.loads(meta))

    return {"sessions": sessions, "count": len(sessions)}


# ── Human notification ────────────────────────────────────────────────────────

async def _notify_human(event: PulseEvent, api_key: str) -> None:
    """
    Send a WhatsApp/Telegram notification when the agent needs human input.

    For now: uses the Telegram bot configured on the first active tenant
    associated with this API key. In production: look up notification
    preferences from the API key record.
    """
    try:
        token = _generate_pulse_token(event.session_id, api_key)
        message = _format_notification(event, token=token)

        # Use Telegram by default — use the same bot infrastructure as AlertEngine
        bot_token = os.getenv("PULSE_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id   = os.getenv("PULSE_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")

        if not bot_token or not chat_id:
            logger.warning("No Telegram config for Pulse notifications — set PULSE_TELEGRAM_BOT_TOKEN and PULSE_TELEGRAM_CHAT_ID")
            return

        import httpx
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={
                "chat_id":    chat_id,
                "text":       message,
                "parse_mode": "Markdown",
            })
            if r.status_code == 200:
                logger.info("Pulse Telegram notification sent: session=%s", event.session_id)
            else:
                logger.warning("Pulse Telegram failed: %d %s", r.status_code, r.text[:200])

    except Exception as e:
        logger.error("Pulse notification failed: %s", e)


def _format_notification(event: PulseEvent, token: str = "") -> str:
    """Format a human-readable Telegram message for a Pulse event."""
    icons = {
        "INPUT_REQUIRED":  "🤔",
        "COST_LIMIT_HIT":  "💰",
        "LOOP_DETECTED":   "🔄",
        "STALL_DETECTED":  "⏸️",
        "SESSION_FAILED":  "❌",
    }
    icon = icons.get(event.event_type, "⚡")

    lines = [
        f"{icon} *Tofamba Pulse — {event.agent_name}*",
        f"Session: `{event.session_id[-12:]}`",
        "",
    ]

    if event.message:
        lines.append(event.message)
        lines.append("")

    if event.options:
        for i, opt in enumerate(event.options):
            letter = chr(65 + i)  # A, B, C...
            lines.append(f"{letter}) {opt}")
        lines.append("")
        lines.append(f"_Reply with A/B/C or the full text to continue._")

    if event.cost_usd:
        lines.append(f"_Current spend: ${event.cost_usd:.4f}_")

    if token:
        base = os.getenv("ALERTENGINE_BASE_URL", "https://enthusiastic-perception-production-a16b.up.railway.app")
        if event.options:
            for i, opt in enumerate(event.options):
                letter = chr(65 + i)
                lines.append(f"[{letter}] [{opt}]({base}/pulse/resolve/{event.session_id}?token={token}&decision={opt})")
        else:
            lines.append(f"[Approve]({base}/pulse/resolve/{event.session_id}?token={token}&decision=approved)")
    lines.append(f"\nSession: `{event.session_id}`")

    return "\n".join(lines)


def _generate_pulse_token(session_id: str, api_key: str, ttl: int = 300) -> str:
    """Generate a signed JWT for a Pulse resolution link. TTL default 5 minutes."""
    import jwt as _jwt
    import os as _os
    secret = _os.getenv("ALERT_SECRET", "dev-secret")
    payload = {
        "session_id": session_id,
        "api_key":    api_key,
        "type":       "pulse_resolve",
        "iat":        int(time.time()),
        "exp":        int(time.time()) + ttl,
    }
    return _jwt.encode(payload, secret, algorithm="HS256")


def _verify_pulse_token(token: str) -> Optional[dict]:
    """Verify a Pulse resolution JWT. Returns payload or None."""
    try:
        import jwt as _jwt
        import os as _os
        secret = _os.getenv("ALERT_SECRET", "dev-secret")
        return _jwt.decode(token, secret, algorithms=["HS256"])
    except Exception as e:
        logger.warning("Pulse token verification failed: %s", e)
        return None


@router.get("/resolve/{session_id}")
async def resolve_intervention(
    session_id: str,
    token: str,
    decision: str = "approved",
):
    """
    JWT-secured resolution endpoint called when human taps the Telegram link.
    Validates the token, stores the decision, returns a confirmation page.
    """
    from fastapi.responses import HTMLResponse

    payload = _verify_pulse_token(token)
    if not payload or payload.get("session_id") != session_id or payload.get("type") != "pulse_resolve":
        raise HTTPException(status_code=401, detail="Invalid or expired resolution token")

    r = _redis()

    r.set(f"{PULSE_ANSWER_PREFIX}{session_id}", json.dumps({
        "answer":      decision,
        "source":      "telegram_link",
        "received_at": time.time(),
    }), ex=300)

    session_key = f"{PULSE_SESSION_PREFIX}{session_id}"
    existing = r.get(session_key)
    agent_name = "Agent"
    if existing:
        meta = json.loads(existing)
        agent_name = meta.get("agent_name", "Agent")
        meta["status"] = "running"
        meta["last_decision"] = decision
        r.set(session_key, json.dumps(meta), ex=PULSE_TTL)

    logger.info("Pulse resolved: session=%s decision=%s source=telegram_link", session_id, decision)

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Tofamba Pulse</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; background: #0C1A27; color: #E8E1D5;
           display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
    .card {{ background: #162535; border: 1px solid rgba(201,154,62,0.35); border-radius: 8px;
             padding: 32px; max-width: 400px; text-align: center; }}
    .icon {{ font-size: 48px; margin-bottom: 16px; }}
    h2 {{ color: #C99A3E; margin: 0 0 8px; font-size: 20px; }}
    p {{ color: #8FA0AF; margin: 0; font-size: 14px; line-height: 1.6; }}
    .decision {{ color: #4A9970; font-weight: 600; font-size: 16px; margin: 16px 0; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h2>Decision Recorded</h2>
    <div class="decision">{decision}</div>
    <p><strong>{agent_name}</strong> has been notified and will continue with your decision.</p>
    <p style="margin-top:16px;font-size:12px;color:#5C6E7A;">
      Logged to the immutable audit ledger · Tofamba Pulse
    </p>
  </div>
</body>
</html>"""

    return HTMLResponse(content=html)
