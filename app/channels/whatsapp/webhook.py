"""
WhatsApp Cloud API (Meta, direct) webhook receiver.

Two jobs:
  1. GET  — Meta's subscription verification handshake (hub.challenge echo)
  2. POST — inbound message parsing + command dispatch

Command routing itself lives in dispatcher.py, shared with the Twilio
transport (twilio_webhook.py) — this file only knows Meta's payload shape.
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response, Query

from app.config import settings
from app.db import session_scope
from app.channels.whatsapp.dispatcher import get_or_create_member, dispatch_command
from app.channels.whatsapp.client import whatsapp_client

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp-meta"])


@router.get("")
async def verify(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(status_code=403)


@router.post("")
async def receive(request: Request):
    payload = await request.json()

    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]["value"]
        messages = change.get("messages", [])
    except (KeyError, IndexError):
        return {"status": "ignored"}

    for message in messages:
        phone = message.get("from")
        text = message.get("text", {}).get("body", "")
        contact_name = ""
        try:
            contact_name = change["contacts"][0]["profile"]["name"]
        except (KeyError, IndexError):
            pass

        with session_scope() as db:
            member = get_or_create_member(db, phone, contact_name)
            reply = await dispatch_command(db, member, text)

        await whatsapp_client.send_text(phone, reply)

    return {"status": "ok"}
