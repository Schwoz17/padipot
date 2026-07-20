"""
Monnify webhook receiver.

Verifies the monnify-signature HMAC before touching anything, then routes
by eventType:
  SUCCESSFUL_TRANSACTION   -> contribution reconciler (may flip a cycle to
                              FUNDED, which triggers the payout orchestrator)
                              -> notifies the group a contribution landed
  DISBURSEMENT_SUCCESSFUL  -> confirms/updates the Disbursement record
                              -> notifies the group + beneficiary
  DISBURSEMENT_FAILED      -> reverts the cycle so a retry can pick it up

Always returns 200 quickly (per Monnify's own best practice) even for
events we don't act on, so Monnify doesn't retry-storm us for events we
intentionally ignore.

Notifications are sent here, in the channel layer — not inside
app.engine.payout — because engine code must never import from channels/
(same layering rule MonniGuard follows: dependencies point one way).
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Header, Response

from app.db import session_scope
from app.monnify.webhooks import (
    verify_signature,
    extract_event_type,
    extract_transaction_reference,
    extract_account_reference,
    extract_amount_paid,
    extract_disbursement_reference,
)
from app.engine.reconciler import record_webhook_contribution
from app.engine.payout import run_payout_for_cycle, resolve_async_disbursement
from app.models import Cycle, CycleState, ReservedAccount, Slot, Member, Pot, Contribution
from app.channels.i18n import t
from app.channels.whatsapp.twilio_client import twilio_whatsapp_client

router = APIRouter(prefix="/webhooks/monnify", tags=["monnify"])


def _pot_lang(pot: Pot) -> str:
    return pot.language.value if pot.language else "en"


def _pot_member_phones(db, pot_id: int) -> list[str]:
    slots = db.query(Slot).filter_by(pot_id=pot_id).all()
    phones = []
    for s in slots:
        m = db.get(Member, s.member_id)
        if m:
            phones.append(m.phone)
    return phones


async def _notify_contribution(db, *, pot: Pot, cycle: Cycle, contributor: Member) -> None:
    progress = db.query(Contribution).filter_by(cycle_id=cycle.id).count()
    total = pot.size - 1
    amount_in_pot = progress * float(pot.amount)
    msg = t(
        "contribution_received", _pot_lang(pot),
        member_name=contributor.name, progress=progress, total=total, amount_in_pot=amount_in_pot,
    )
    await twilio_whatsapp_client.broadcast(_pot_member_phones(db, pot.id), msg)


async def _notify_payout_success(db, cycle_id: int) -> None:
    cycle = db.get(Cycle, cycle_id)
    pot = db.get(Pot, cycle.pot_id)
    slot = db.get(Slot, cycle.beneficiary_slot_id)
    beneficiary = db.get(Member, slot.member_id)
    amount = float(pot.amount) * (pot.size - 1)
    lang = _pot_lang(pot)

    group_msg = t("pot_complete", lang, beneficiary_name=beneficiary.name)
    await twilio_whatsapp_client.broadcast(_pot_member_phones(db, pot.id), group_msg)

    payout_msg = t("payout_sent", lang, amount=amount, beneficiary_name=beneficiary.name, round_no=cycle.round_no)
    await twilio_whatsapp_client.send_text(beneficiary.phone, payout_msg)


@router.post("")
async def receive(request: Request, monnify_signature: str | None = Header(default=None, alias="monnify-signature")):
    raw_body = await request.body()

    if not verify_signature(raw_body, monnify_signature):
        return Response(status_code=401, content="invalid signature")

    payload = await request.json()
    event_type = extract_event_type(payload)

    if event_type == "SUCCESSFUL_TRANSACTION":
        account_reference = extract_account_reference(payload)
        tx_reference = extract_transaction_reference(payload)
        amount = extract_amount_paid(payload)

        if account_reference and tx_reference and amount is not None:
            with session_scope() as db:
                contribution = await record_webhook_contribution(
                    db, account_reference=account_reference, monnify_tx_ref=tx_reference, amount=amount
                )
                if contribution is not None:
                    account = db.query(ReservedAccount).filter_by(account_reference=account_reference).first()
                    pot = db.get(Pot, account.pot_id)
                    contributor = db.get(Member, contribution.member_id)
                    contributed_cycle = db.get(Cycle, contribution.cycle_id)

                    await _notify_contribution(db, pot=pot, cycle=contributed_cycle, contributor=contributor)

                    funded_cycle = (
                        db.query(Cycle)
                        .filter_by(pot_id=account.pot_id, state=CycleState.FUNDED)
                        .order_by(Cycle.round_no.desc())
                        .first()
                    )
                    if funded_cycle is not None:
                        # Wallet balance would normally come from monnify_client's wallet-balance
                        # endpoint; pulled here as a placeholder call site for that wiring.
                        result = await run_payout_for_cycle(db, funded_cycle.id, wallet_balance=float("inf"))
                        if result == "PAID":
                            await _notify_payout_success(db, funded_cycle.id)

        return {"status": "acknowledged"}

    if event_type in ("DISBURSEMENT_SUCCESSFUL", "DISBURSEMENT_FAILED"):
        disb_ref = extract_disbursement_reference(payload)
        if disb_ref:
            with session_scope() as db:
                result = resolve_async_disbursement(
                    db, monnify_ref=disb_ref, success=(event_type == "DISBURSEMENT_SUCCESSFUL")
                )
                if result == "PAID":
                    # The cycle_id isn't returned by resolve_async_disbursement directly;
                    # look it up via the Disbursement record for the notification.
                    from app.models import Disbursement
                    disbursement = db.query(Disbursement).filter_by(monnify_ref=disb_ref).first()
                    if disbursement is not None:
                        await _notify_payout_success(db, disbursement.cycle_id)
        return {"status": "acknowledged"}

    return {"status": "ignored", "eventType": event_type}
