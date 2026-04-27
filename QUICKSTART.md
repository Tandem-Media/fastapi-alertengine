# ⚡ AlertEngine Lite — Quickstart

Fix your API from WhatsApp in under 5 seconds.

## Setup (10 minutes)

**1. Install**
```bash
pip install -r requirements.txt
```

**2. Configure**
```bash
cp .env.example .env
# Fill in Twilio credentials and BASE_URL
```

**3. Start Redis**
```bash
redis-server
# or: "C:\Program Files\Redis\redis-server.exe" on Windows
```

**4. Run**
```bash
uvicorn demo_app:app --no-access-log
```

**5. Generate traffic**
```bash
python load.py
```

**6. Open**
- Control panel: http://localhost:8000/demo
- WhatsApp sim: http://localhost:8000/sim/whatsapp_sim.html
- Health: http://localhost:8000/health/alerts

---

## Demo Flow

1. Wait 60s for health score to reach 100
2. Click **🔴 Full Failure**
3. Watch score drop → alert fires on WhatsApp sim
4. Click **Confirm Recovery**
5. Score rises → system recovered

---

## Escalation Timeline

| Time | Action |
|---|---|
| T+0 | Initial WhatsApp alert with recovery link |
| T+2m | Reminder alert (new link) |
| T+3m | Voice call to primary engineer |
| T+5m | WhatsApp to secondary engineer |

---

## Security

Every recovery link is:
- ✅ Signed (HS256 JWT)
- ✅ Expires in 90 seconds
- ✅ Single-use (replay protected)
- ✅ Tied to incident ID

---

## For real WhatsApp on your phone

```bash
pip install ngrok
ngrok http 8000
# Set BASE_URL=https://xxxx.ngrok-free.app in .env
```

Join Twilio sandbox: send `join <code>` to +14155238886 on WhatsApp.
