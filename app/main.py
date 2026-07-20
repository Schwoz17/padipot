"""
PadiPot backend entrypoint.

Run locally:
    uvicorn app.main:app --reload --port 8000

Then point Monnify's sandbox webhook URL and WhatsApp's Cloud API webhook
URL at this server's public tunnel (ngrok during dev), e.g.:
    https://<your-ngrok-domain>/webhooks/monnify
    https://<your-ngrok-domain>/webhooks/whatsapp
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from app.config import settings
from app.db import init_db
from app.monnify.router import router as monnify_router
from app.channels.whatsapp.webhook import router as whatsapp_meta_router
from app.channels.whatsapp.twilio_webhook import router as whatsapp_twilio_router
from app.channels.ussd.handler import router as ussd_router
from app.scheduler import start_scheduler

logging.basicConfig(level=settings.log_level)

app = FastAPI(title="PadiPot Engine", version="4.0.0")

app.include_router(monnify_router)
app.include_router(whatsapp_meta_router)     # Meta Cloud API — original transport
app.include_router(whatsapp_twilio_router)   # Twilio — partner-owned transport
app.include_router(ussd_router)

_scheduler = None


@app.on_event("startup")
async def on_startup():
    global _scheduler
    init_db()
    _scheduler = start_scheduler()


@app.on_event("shutdown")
async def on_shutdown():
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "padipot-engine"}
