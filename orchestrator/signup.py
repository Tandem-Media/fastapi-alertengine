# orchestrator/signup.py
"""
Self-serve signup endpoint for AlertEngine.

POST /signup — captures customer details, stores in Redis,
sends auto-response email to customer, and notifies Lenard.

Flow:
  1. Customer fills form on landing page
  2. POST /signup with their details
  3. System stores lead in Redis with unique lead_id
  4. Auto-response sent to customer: "You'll be live within 2 hours"
  5. Notification sent to anchorflowalertengine@outlook.com
  6. Lenard runs create_tenant.py with the lead details
  7. Customer receives welcome email with tenant_id

No payment taken here — invoice sent manually after tenant is created.
"""

import json
import logging
import os
import time
import uuid
from typing import Optional

logger = logging.getLogger("orchestrator.signup")

SIGNUP_PREFIX = "alertengine:signups:"
SIGNUP_TTL    = 86400 * 30  # 30 days


# ── Redis storage ──────────────────────────────────────────────────────────────

_signup_redis = None

def _redis():
    global _signup_redis
    if _signup_redis is None:
        import redis
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _signup_redis = redis.Redis.from_url(url, decode_responses=True)
    return _signup_redis


def store_lead(lead: dict) -> str:
    """Store signup lead in Redis. Returns lead_id."""
    lead_id = str(uuid.uuid4())[:8].upper()
    lead["lead_id"]   = lead_id
    lead["created_at"] = time.time()
    lead["status"]    = "pending"

    key = f"{SIGNUP_PREFIX}{lead_id}"
    try:
        r = _redis()
        r.set(key, json.dumps(lead))
        r.expire(key, SIGNUP_TTL)

        # Also push to a list for easy listing
        r.lpush("alertengine:signup_queue", lead_id)
        r.ltrim("alertengine:signup_queue", 0, 999)
        logger.info("Lead stored: %s — %s (%s)", lead_id, lead.get("name"), lead.get("plan"))
    except Exception as e:
        logger.error("Failed to store lead: %s", e)

    return lead_id


def get_lead(lead_id: str) -> Optional[dict]:
    """Retrieve a lead by ID."""
    try:
        key  = f"{SIGNUP_PREFIX}{lead_id}"
        data = _redis().get(key)
        return json.loads(data) if data else None
    except Exception:
        return None


def list_leads(limit: int = 20) -> list:
    """List recent signup leads."""
    try:
        r       = _redis()
        ids     = r.lrange("alertengine:signup_queue", 0, limit - 1)
        leads   = []
        for lid in ids:
            lead = get_lead(lid)
            if lead:
                leads.append(lead)
        return leads
    except Exception:
        return []


def mark_lead_onboarded(lead_id: str) -> bool:
    """Mark a lead as onboarded after tenant creation."""
    lead = get_lead(lead_id)
    if not lead:
        return False
    lead["status"]      = "onboarded"
    lead["onboarded_at"] = time.time()
    try:
        key = f"{SIGNUP_PREFIX}{lead_id}"
        _redis().set(key, json.dumps(lead))
        return True
    except Exception:
        return False


# ── Email notifications ────────────────────────────────────────────────────────

def send_auto_response(lead: dict) -> bool:
    """
    Send auto-response to customer confirming signup.
    Uses SMTP if configured, otherwise logs the message.
    """
    customer_email = lead.get("email")
    if not customer_email:
        return False

    plan       = lead.get("plan", "growth").title()
    name       = lead.get("name", "there")
    lead_id    = lead.get("lead_id", "")

    subject = "AlertEngine — You'll be live within 2 hours"
    body = f"""Hi {name},

Thanks for signing up for AlertEngine ({plan} plan).

We've received your details and will have your tenant configured within 2 hours.

Here's what happens next:

1. We create your tenant and send you your tenant_id
2. You receive a test alert on your {lead.get('channel', 'WhatsApp')} 
   ({lead.get('phone', '')})
3. You confirm receipt and we send your invoice
4. Once paid, all features are permanently active

Your signup reference: {lead_id}

If you need anything in the meantime, reply to this email or 
WhatsApp us at +263785023897.

Lenard Francis
AlertEngine / AnchorFlow
anchorflowalertengine@outlook.com
https://tandem-media.github.io/fastapi-alertengine/
"""

    # Try SMTP if configured
    smtp_host = os.getenv("SMTP_HOST")
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")

    if smtp_host and smtp_user and smtp_pass:
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart()
            msg["From"]    = smtp_user
            msg["To"]      = customer_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            port = int(os.getenv("SMTP_PORT", "587"))
            with smtplib.SMTP(smtp_host, port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)

            logger.info("Auto-response sent to %s", customer_email)
            return True
        except Exception as e:
            logger.error("SMTP send failed: %s", e)

    # Fallback — log the message
    logger.info(
        "AUTO-RESPONSE (SMTP not configured):\nTo: %s\nSubject: %s\n\n%s",
        customer_email, subject, body
    )
    return False


def notify_lenard(lead: dict) -> bool:
    """Notify anchorflowalertengine@outlook.com of new signup."""
    smtp_host = os.getenv("SMTP_HOST")
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")

    plan    = lead.get("plan", "growth").title()
    name    = lead.get("name", "Unknown")
    lead_id = lead.get("lead_id", "")

    subject = f"🚨 New AlertEngine signup: {name} ({plan})"
    body = f"""New signup received.

Lead ID:      {lead_id}
Name:         {name}
Email:        {lead.get('email', '—')}
Phone:        {lead.get('phone', '—')}
Plan:         {plan}
Channel:      {lead.get('channel', '—')}
Health URL:   {lead.get('health_url', '—')}
Webhook URL:  {lead.get('recovery_webhook_url', '—')}
GitHub repo:  {lead.get('github_repo', '—')}

Run create_tenant.py with these details.

Time: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}
"""

    if smtp_host and smtp_user and smtp_pass:
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart()
            msg["From"]    = smtp_user
            msg["To"]      = "anchorflowalertengine@outlook.com"
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            port = int(os.getenv("SMTP_PORT", "587"))
            with smtplib.SMTP(smtp_host, port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)

            logger.info("Lenard notified of new signup: %s", lead_id)
            return True
        except Exception as e:
            logger.error("Notification send failed: %s", e)

    # Fallback — always log
    logger.warning(
        "NEW SIGNUP (SMTP not configured):\n%s", body
    )
    return False
