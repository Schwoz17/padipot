"""
WhatsApp via Twilio — inbound webhook receiver.

Twilio POSTs inbound WhatsApp messages as form-encoded fields (not JSON,
unlike Meta's Cloud API):
  From        "whatsapp:+2348012345678"
  To          "whatsapp:+14155238886"  (your Twilio sender)
  Body        the message text
  ProfileName the sender's WhatsApp display name (when available)

Every request carries an X-Twilio-Signature header, verified here using
Twilio's own RequestValidator against PUBLIC_BASE_URL + this route's path —
NOT the request's own reported URL, since that can be wrong or spoofed
behind a proxy/tunnel. Set PUBLIC_BASE_URL in .env to your ngrok/production
HTTPS origin for this to pass.

Reply strategy: like the Meta transport, this sends the reply via an
outbound REST call (twilio_client.send_text) rather than returning TwiML,
so both transports share one "process then send" shape in dispatcher.py.

Two separate failure points are guarded here, deliberately:
  1. dispatch_command() itself can raise (e.g. a Monnify API call inside a
     command handler fails) — without a guard here, the member gets total
     silence and Twilio gets a 500, which triggers a retry storm re-running
     the same command again. Caught below, with a plain-language reply so
     the member at least knows something went wrong rather than wondering
     if their message even arrived.
  2. Sending the reply can itself fail (rate limit, network blip) even
     when the command succeeded — guarded separately, since in that case
     the command's database changes already committed and must not be
     treated as a failure.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Form, Response
from twilio.request_validator import RequestValidator

from app.config import settings
from app.db import session_scope
from app.channels.whatsapp.dispatcher import get_or_create_member, dispatch_command
from app.channels.whatsapp.twilio_client import twilio_whatsapp_client

router = APIRouter(prefix="/webhooks/whatsapp-twilio", tags=["whatsapp-twilio"])
logger = logging.getLogger("padipot.twilio_webhook")


def _strip_whatsapp_prefix(twilio_number: str) -> str:
    return twilio_number.replace("whatsapp:", "", 1)


@router.post("")
async def receive(
    request: Request,
    From: str = Form(...),
    Body: str = Form(""),
    ProfileName: str = Form(""),
):
    signature = request.headers.get("X-Twilio-Signature", "")
    form = await request.form()
    params = dict(form)

    validator = RequestValidator(settings.twilio_auth_token)
    expected_url = f"{settings.public_base_url.rstrip('/')}/webhooks/whatsapp-twilio"
    if not validator.validate(expected_url, params, signature):
        return Response(status_code=401, content="invalid signature")

    phone = _strip_whatsapp_prefix(From)

    try:
        with session_scope() as db:
            member = get_or_create_member(db, phone, ProfileName)
            reply = await dispatch_command(db, member, Body)
    except Exception:  # noqa: BLE001 — a crashed command must still produce a reply, not silence
        logger.exception("dispatch_command crashed for %s — Body=%r", phone, Body)
        reply = (
            "Something went wrong processing that — our team has been notified. "
            "Please try again in a moment."
        )

    try:
        await twilio_whatsapp_client.send_text(phone, reply)
    except Exception:  # noqa: BLE001 — the command already committed; a reply failure must not look like the request failed
        logger.exception("Failed to send reply to %s — command was still processed successfully", phone)

    # Always 200: Twilio retries anything that isn't 2xx, and a retry here
    # would re-run dispatch_command for the same inbound message.
    return Response(status_code=200, content="", media_type="text/xml")
