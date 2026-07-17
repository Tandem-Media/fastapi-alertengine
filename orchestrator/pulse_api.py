# orchestrator/pulse_api.py
"""
Tofamba Pulse — Orchestrator API
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

_redis_client = None

def _redis():
    global _redis_client
    if _redis_client is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _redis_client = _redis_module.Redis.from_url(url, decode_responses=True)
    return _redis_client

PULSE_EVENT_PREFIX  = "pulse:events:"
PULSE_ANSWER_PREFIX = "pulse:answer:"
PULSE_SESSION_PREFIX = "pulse:session:"
PULSE_SESSIONS_KEY  = "pulse:sessions:"
PULSE_TTL = 86400 * 7

def _verify_api_key(x_pulse_api_key):
    if not x_pulse_api_key:
        raise HTTPException(status_code=401, detail="x-pulse-api-key header required")
    return x_pulse_api_key

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
    source: str = "telegram"

@router.post("/event")
async def receive_event(event: PulseEvent, x_pulse_api_key: Optional[str] = Header(None)):
    api_key = _verify_api_key(x_pulse_api_key)
    if not event.timestamp:
        event.timestamp = time.time()
    r = _redis()
    event_key = f"{PULSE_EVENT_PREFIX}{event.session_id}"
    r.rpush(event_key, json.dumps(event.dict()))
    r.expire(event_key, PULSE_TTL)
    session_key = f"{PULSE_SESSION_PREFIX}{event.session_id}"
    existing = r.get(session_key)
    if existing:
        meta = json.loads(existing)
    else:
        meta = {"session_id": event.session_id, "agent_name": event.agent_name,
                "api_key": api_key, "started_at": event.timestamp,
                "event_count": 0, "cost_usd": 0.0, "status": "running"}
    meta["event_count"] = meta.get("event_count", 0) + 1
    meta["last_event"] = event.event_type
    meta["last_seen"] = event.timestamp
    if event.cost_usd:
        meta["cost_usd"] = event.cost_usd
    if event.event_type == "SESSION_END":
        meta["status"] = "completed"
    elif event.event_type == "SESSION_FAILED":
        meta["status"] = "failed"
    elif event.event_type == "INPUT_REQUIRED":
        meta["status"] = "waiting_for_human"
    elif event.event_type == "INPUT_RECEIVED":
        meta["status"] = "running"
    r.set(session_key, json.dumps(meta), ex=PULSE_TTL)
    sessions_key = f"{PULSE_SESSIONS_KEY}{api_key}"
    r.zadd(sessions_key, {event.session_id: event.timestamp})
    r.expire(sessions_key, PULSE_TTL)
    NOTIFY_EVENTS = {"INPUT_REQUIRED", "COST_LIMIT_HIT", "LOOP_DETECTED", "STALL_DETECTED", "SESSION_FAILED"}
    if event.event_type in NOTIFY_EVENTS:
        await _notify_human(event, api_key)
    logger.info("Pulse event: %s | session=%s", event.event_type, event.session_id)
    return {"received": True, "session_id": event.session_id, "event_type": event.event_type}

@router.get("/answer/{session_id}")
async def get_answer(session_id: str, x_pulse_api_key: Optional[str] = Header(None)):
    _verify_api_key(x_pulse_api_key)
    r = _redis()
    answer = r.get(f"{PULSE_ANSWER_PREFIX}{session_id}")
    if answer:
        data = json.loads(answer)
        return {"answer": data.get("answer"), "source": data.get("source", "unknown")}
    return {"answer": None}

@router.post("/answer/{session_id}")
async def submit_answer(session_id: str, payload: PulseAnswer, x_pulse_api_key: Optional[str] = Header(None)):
    _verify_api_key(x_pulse_api_key)
    r = _redis()
    r.set(f"{PULSE_ANSWER_PREFIX}{session_id}", json.dumps({
        "answer": payload.answer, "source": payload.source, "received_at": time.time()
    }), ex=PULSE_TTL)
    session_key = f"{PULSE_SESSION_PREFIX}{session_id}"
    existing = r.get(session_key)
    if existing:
        meta = json.loads(existing)
        meta["status"] = "running"
        r.set(session_key, json.dumps(meta), ex=PULSE_TTL)
    logger.info("Pulse answer: session=%s answer=%s", session_id, payload.answer[:50])
    return {"stored": True, "session_id": session_id}

@router.get("/session/{session_id}")
async def get_session(session_id: str, x_pulse_api_key: Optional[str] = Header(None)):
    _verify_api_key(x_pulse_api_key)
    r = _redis()
    meta = r.get(f"{PULSE_SESSION_PREFIX}{session_id}")
    if not meta:
        raise HTTPException(status_code=404, detail="Session not found")
    raw_events = r.lrange(f"{PULSE_EVENT_PREFIX}{session_id}", 0, -1)
    events = []
    for raw in raw_events:
        try:
            events.append(json.loads(raw))
        except Exception:
            continue
    return {"session": json.loads(meta), "events": events, "event_count": len(events)}

@router.get("/sessions")
async def list_sessions(x_pulse_api_key: Optional[str] = Header(None), limit: int = 20):
    api_key = _verify_api_key(x_pulse_api_key)
    r = _redis()
    session_ids = r.zrevrange(f"{PULSE_SESSIONS_KEY}{api_key}", 0, limit - 1)
    sessions = []
    for sid in session_ids:
        meta = r.get(f"{PULSE_SESSION_PREFIX}{sid}")
        if meta:
            sessions.append(json.loads(meta))
    return {"sessions": sessions, "count": len(sessions)}

async def _notify_human(event: PulseEvent, api_key: str) -> None:
    try:
        message = _format_notification(event)
        bot_token = os.getenv("PULSE_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id   = os.getenv("PULSE_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
        if not bot_token or not chat_id:
            logger.warning("No Telegram config for Pulse notifications")
            return
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"})
            if r.status_code == 200:
                logger.info("Pulse Telegram sent: session=%s", event.session_id)
            else:
                logger.warning("Pulse Telegram failed: %d", r.status_code)
    except Exception as e:
        logger.error("Pulse notification failed: %s", e)

def _format_notification(event: PulseEvent) -> str:
    icons = {"INPUT_REQUIRED": "🤔", "COST_LIMIT_HIT": "💰",
             "LOOP_DETECTED": "🔄", "STALL_DETECTED": "⏸️", "SESSION_FAILED": "❌"}
    icon = icons.get(event.event_type, "⚡")
    lines = [f"{icon} *Tofamba Pulse — {event.agent_name}*",
             f"Session: `{event.session_id[-12:]}`", ""]
    if event.message:
        lines += [event.message, ""]
    if event.options:
        for i, opt in enumerate(event.options):
            lines.append(f"{chr(65+i)}) {opt}")
        lines += ["", "_Reply with A/B/C or the full text to continue._"]
    if event.cost_usd:
        lines.append(f"_Current spend: ${event.cost_usd:.4f}_")
    lines.append(f"\nSession: `{event.session_id}`")
    return "\n".join(lines)
