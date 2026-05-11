# orchestrator/telegram_notifier.py
"""
Telegram notification channel.
Uses Telegram Bot API directly via httpx — no Twilio required.
Free to use. Ideal for North American users who don't use WhatsApp.

Setup per tenant:
    1. User creates a bot via @BotFather on Telegram → gets BOT_TOKEN
    2. User starts a chat with the bot → gets CHAT_ID
    3. Both are stored in the tenant record at onboarding
"""

import logging
import os
import httpx

logger = logging.getLogger("orchestrator.telegram")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def send_telegram(
    bot_token: str,
    chat_id: str,
    message: str,
) -> bool:
    """
    Send a message via Telegram Bot API.
    Returns True on success. Never raises.
    """
    if not bot_token or not chat_id:
        logger.warning("Telegram credentials not configured")
        return False
    try:
        url = TELEGRAM_API.format(token=bot_token)
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={
                "chat_id":    chat_id,
                "text":       message,
                "parse_mode": "Markdown",
            })
        if r.status_code == 200:
            logger.info("Telegram message sent to chat_id=%s", chat_id)
            return True
        logger.warning("Telegram API returned %d: %s", r.status_code, r.text)
        return False
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
        return False


async def send_telegram_detection(
    bot_token: str,
    chat_id: str,
    incident_id: str,
    score: float,
    p95: float,
    err: float,
) -> bool:
    message = (
        f"🚨 *API Critical — Analysing...*\n\n"
        f"Score: {score:.0f}/100\n"
        f"P95: {p95:.0f}ms\n"
        f"Errors: {err*100:.0f}%\n\n"
        f"Incident: `{incident_id}`"
    )
    return await send_telegram(bot_token, chat_id, message)


async def send_telegram_validation(
    bot_token: str,
    chat_id: str,
    incident_id: str,
    score: float,
    p95: float,
    recovery_url: str,
) -> bool:
    message = (
        f"⚡ *Action Recommended*\n\n"
        f"Score: {score:.0f}/100\n"
        f"P95: {p95:.0f}ms\n\n"
        f"Tap to authorise:\n{recovery_url}\n\n"
        f"_Nothing runs without your approval._"
    )
    return await send_telegram(bot_token, chat_id, message)


async def send_telegram_recovery(
    bot_token: str,
    chat_id: str,
    incident_id: str,
    score: float,
    duration_s: float,
) -> bool:
    minutes = int(duration_s // 60)
    seconds = int(duration_s % 60)
    duration_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    message = (
        f"✅ *Recovered*\n\n"
        f"Score: {score:.0f}/100\n"
        f"Duration: {duration_str}"
    )
    return await send_telegram(bot_token, chat_id, message)
