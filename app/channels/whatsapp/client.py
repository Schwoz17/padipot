"""
WhatsApp Cloud API client — outbound messages only. Inbound messages are
handled by webhook.py; this module is just "send text to this phone number."
"""
from __future__ import annotations

import httpx

from app.config import settings


class WhatsAppClient:
    def __init__(self):
        self._base_url = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_number_id}/messages"
        self._token = settings.whatsapp_token

    async def send_text(self, to_phone: str, body: str) -> dict:
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"body": body},
        }
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(self._base_url, json=payload, headers=headers)
        return resp.json()

    async def broadcast(self, phones: list[str], body: str) -> None:
        """Fire-and-collect-errors broadcast to a group of members (e.g. the whole pot)."""
        for phone in phones:
            try:
                await self.send_text(phone, body)
            except Exception:  # noqa: BLE001 — one member's delivery failure must not block the rest
                continue


whatsapp_client = WhatsAppClient()
