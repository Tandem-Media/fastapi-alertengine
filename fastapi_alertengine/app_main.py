from fastapi import FastAPI, HTTPException, Form, Response
import httpx
import os
import redis
from fastapi_alertengine import RequestMetricsMiddleware, get_alert_engine, actions_router

app = FastAPI(title="fastapi_alertengine demo", version="1.1.4")

# --- CONFIG ---
ALERTENGINE_BASE = os.getenv("ALERTENGINE_BASE_URL", "http://localhost:8000")
ALLOWED_NUMBERS = ["+2637XXXXXXXX"]

# --- ENGINE ---
redis_client = redis.Redis.from_url("redis://localhost:6379/0")
engine = get_alert_engine(redis_client=redis_client)

@app.on_event("startup")
async def start_drain():
    import asyncio
    asyncio.create_task(engine.drain())

app.add_middleware(RequestMetricsMiddleware, alert_engine=engine)
app.include_router(actions_router)

# --- TWILIO WEBHOOK ---
@app.post("/twilio/webhook")
async def twilio_webhook(From: str = Form(...), Body: str = Form(...)):
    sender = From.replace("whatsapp:", "")
    message = Body.strip().lower()

    print(f"[TWILIO] From: {sender} | Message: {message}")

    # 🔒 Authorization
    if sender not in ALLOWED_NUMBERS:
        return Response(
            content="<Response><Message>Unauthorized</Message></Response>",
            media_type="application/xml"
        )

    reply = ""

    async with httpx.AsyncClient() as client:

        if message == "status":
            r = await client.get(f"{ALERTENGINE_BASE}/health/alerts")
            data = r.json()

            reply = (
                f"Health: {data['health_score']['score']}/100\n"
                f"Status: {data['status']}\n"
                f"Trend: {data['health_score']['trend']}"
            )

        elif message == "restart":
            s = await client.get(f"{ALERTENGINE_BASE}/actions/suggest")
            suggestions = s.json().get("suggestions", [])

            print("[ACTION] Suggestions:", suggestions)

            action = next(
                (a for a in suggestions if a["action"] == "restart"),
                None
            )

            if action and action.get("token"):
                try:
                    await client.get(
                        f"{ALERTENGINE_BASE}/action/restart",
                        params={"token": action["token"]}
                    )
                    reply = "✅ Restart executed via Signed Token."
                except Exception as e:
                    print("[ERROR]", e)
                    reply = "❌ Restart failed."
            else:
                reply = "⚠️ No authorized restart token available."

        else:
            reply = "Commands: status, restart"

    twiml = f"<Response><Message>{reply}</Message></Response>"
    return Response(content=twiml, media_type="application/xml")

# --- DEMO ROUTES ---
@app.get("/")
async def root():
    return {"status": "ok"}

@app.get("/error")
async def error():
    raise HTTPException(status_code=500, detail="Intentional error for demo")

@app.get("/health/alerts")
def alerts_health():
    return engine.evaluate(window_size=200)