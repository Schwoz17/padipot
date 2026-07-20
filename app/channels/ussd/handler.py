"""
USSD handler — Africa's Talking sandbox simulator.

Deliberately thin: a five-option menu rendering the SAME underlying engine
data as the WhatsApp flows (status, account number, whose turn, ledger,
language). USSD is a renderer, not a fork — it calls the exact same
app.engine functions as flows.py; only the presentation differs (plain text
menus instead of WhatsApp formatting, because feature phone screens are tiny
and session length is limited by the telco).

Africa's Talking POSTs form-encoded fields per USSD session:
  sessionId, phoneNumber, serviceCode, text
`text` accumulates the user's choices so far, separated by "*"
(e.g. first request text="", user picks "1" -> next request text="1").
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Response

from app.db import session_scope
from app.models import Member, ReservedAccount, Slot, Pot
from app.engine import rotation, padirecord

router = APIRouter(prefix="/ussd", tags=["ussd"])

MAIN_MENU = (
    "CON Welcome to PadiPot\n"
    "1. My account number\n"
    "2. Round status\n"
    "3. My turn\n"
    "4. Ledger\n"
    "5. My Padi Record"
)


def _get_member_and_pot(db, phone: str) -> tuple[Member | None, Pot | None]:
    member = db.query(Member).filter_by(phone=phone).first()
    if member is None:
        return None, None
    slot = db.query(Slot).filter_by(member_id=member.id).order_by(Slot.id.desc()).first()
    pot = db.get(Pot, slot.pot_id) if slot else None
    return member, pot


@router.post("")
async def ussd_session(
    sessionId: str = Form(...),
    phoneNumber: str = Form(...),
    serviceCode: str = Form(...),
    text: str = Form(""),
):
    choices = text.split("*") if text else []

    with session_scope() as db:
        member, pot = _get_member_and_pot(db, phoneNumber)

        if member is None:
            return Response(content="END No PadiPot account found for this number. Join via WhatsApp first.", media_type="text/plain")

        if text == "":
            return Response(content=MAIN_MENU, media_type="text/plain")

        choice = choices[0]

        if choice == "1":
            if pot is None:
                body = "END You are not in an active pot."
            else:
                account = db.query(ReservedAccount).filter_by(pot_id=pot.id, member_id=member.id).first()
                body = f"END Account: {account.account_number} ({account.bank_name})" if account else "END No account on record."

        elif choice == "2":
            if pot is None:
                body = "END You are not in an active pot."
            else:
                from app.models import Cycle, Contribution
                cycle = db.query(Cycle).filter_by(pot_id=pot.id, state="OPEN").order_by(Cycle.round_no.desc()).first()
                if cycle is None:
                    body = "END No open round."
                else:
                    progress = db.query(Contribution).filter_by(cycle_id=cycle.id).count()
                    body = f"END Round {cycle.round_no}: {progress}/{pot.size - 1} contributed"

        elif choice == "3":
            if pot is None:
                body = "END You are not in an active pot."
            else:
                next_slot = rotation.next_beneficiary_slot(db, pot.id)
                if next_slot is None:
                    body = "END No pending beneficiary."
                else:
                    next_member = db.get(Member, next_slot.member_id)
                    body = f"END Next to collect: {next_member.name}"

        elif choice == "4":
            if pot is None:
                body = "END You are not in an active pot."
            else:
                from app.models import Contribution, Cycle
                rows = (
                    db.query(Contribution)
                    .join(Cycle, Contribution.cycle_id == Cycle.id)
                    .filter(Cycle.pot_id == pot.id)
                    .order_by(Contribution.funded_at.desc())
                    .limit(3)
                    .all()
                )
                if not rows:
                    body = "END No contributions yet."
                else:
                    lines = [f"{db.get(Member, r.member_id).name}: NGN{float(r.amount):,.0f}" for r in rows]
                    body = "END " + " | ".join(lines)

        elif choice == "5":
            record = padirecord.build_padi_record(db, member.id)
            body = "END " + padirecord.render_sms(record)

        else:
            body = "END Invalid option."

    return Response(content=body, media_type="text/plain")
