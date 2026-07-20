"""
SMS notifier via Africa's Talking. Used for members whose preferred_channel
is SMS — same notification events as the WhatsApp broadcast (contribution
received, pot complete, payout sent), just shorter copy and no group thread,
since plain SMS has no concept of a group.
"""
from __future__ import annotations

import httpx

from app.config import settings

AT_SEND_URL = "https://api.sandbox.africastalking.com/version1/messaging"


class SmsNotifier:
    async def send(self, phone: str, message: str) -> dict:
        headers = {
            "apiKey": settings.at_api_key,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        data = {
            "username": settings.at_username,
            "to": phone,
            "message": message,
            "from": settings.at_sender_id,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(AT_SEND_URL, headers=headers, data=data)
        return resp.json()

    async def broadcast(self, phones: list[str], message: str) -> None:
        for phone in phones:
            try:
                await self.send(phone, message)
            except Exception:  # noqa: BLE001
                continue


sms_notifier = SmsNotifier()
