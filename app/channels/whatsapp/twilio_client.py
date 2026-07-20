"""
WhatsApp via Twilio — outbound messages.

Twilio wraps the underlying WhatsApp Business Platform, so from PadiPot's
side this is just REST calls to Twilio's Messages API with 'whatsapp:'
prefixed numbers. No Meta Business Manager verification required to get a
working sandbox number — that's the main reason this transport exists
alongside the direct Meta client (app/channels/whatsapp/client.py).

Sandbox setup (fastest path to a working demo number):
  1. In the Twilio console: Messaging -> Try it out -> Send a WhatsApp message
  2. From your own WhatsApp, send "join <your-sandbox-code>" to the shown number
  3. Put that number in TWILIO_WHATSAPP_FROM as "whatsapp:+14155238886" (or
     whatever Twilio assigns you) and your Account SID/Auth Token in .env
  4. Every phone that needs to receive/send PadiPot messages in the demo
     must also send "join <code>" once — this is a sandbox limit, lifted
     once you request an approved production sender.
"""
from __future__ import annotations

from twilio.rest import Client

from app.config import settings


class TwilioWhatsAppClient:
    def __init__(self):
        self._client: Client | None = None
        self._from = settings.twilio_whatsapp_from

    def _get_client(self) -> Client:
        """
        Lazy construction: the app must be able to boot (and run tests)
        even before real Twilio credentials are in .env. The client is only
        actually built the first time a message is sent.
        """
        if self._client is None:
            self._client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        return self._client

    async def send_text(self, to_phone: str, body: str) -> dict:
        """
        to_phone should be a bare E.164 number (e.g. '+2348012345678') —
        this method adds the 'whatsapp:' prefix Twilio requires.
        The Twilio SDK is synchronous under the hood; wrapped here so the
        rest of the app (which is async throughout) has one consistent
        interface with the Meta client in client.py.
        """
        message = self._get_client().messages.create(
            from_=self._from,
            to=f"whatsapp:{to_phone}",
            body=body,
        )
        return {"sid": message.sid, "status": message.status}

    async def broadcast(self, phones: list[str], body: str) -> None:
        for phone in phones:
            try:
                await self.send_text(phone, body)
            except Exception:  # noqa: BLE001 — one member's delivery failure must not block the rest
                continue


twilio_whatsapp_client = TwilioWhatsAppClient()
