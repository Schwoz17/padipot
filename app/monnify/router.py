"""
Monnify webhook receiver.

Verifies the monnify-signature HMAC before touching anything, then routes
by eventType:
  SUCCESSFUL_TRANSACTION   -> contribution reconciler (may flip a cycle to
                              FUNDED, which triggers the payout orchestrator)
  DISBURSEMENT_SUCCESSFUL  -> confirms/updates the Disbursement record
  DISBURSEMENT_FAILED      -> reverts the cycle so a retry can pick it up

Always returns 200 quickly (per Monnify's own best practice) even for
events we don't act on, so Monnify doesn't retry-storm us for events we
intentionally ignore.
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
from app.models import Cycle, CycleState, ReservedAccount

router = APIRouter(prefix="/webhooks/monnify", tags=["monnify"])


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
                contribution = record_webhook_contribution(
                    db, account_reference=account_reference, monnify_tx_ref=tx_reference, amount=amount
                )
                if contribution is not None:
                    account = db.query(ReservedAccount).filter_by(account_reference=account_reference).first()
                    cycle = (
                        db.query(Cycle)
                        .filter_by(pot_id=account.pot_id, state=CycleState.FUNDED)
                        .order_by(Cycle.round_no.desc())
                        .first()
                    )
                    if cycle is not None:
                        # Wallet balance would normally come from monnify_client's wallet-balance
                        # endpoint; pulled here as a placeholder call site for that wiring.
                        await run_payout_for_cycle(db, cycle.id, wallet_balance=float("inf"))

        return {"status": "acknowledged"}

    if event_type in ("DISBURSEMENT_SUCCESSFUL", "DISBURSEMENT_FAILED"):
        disb_ref = extract_disbursement_reference(payload)
        if disb_ref:
            with session_scope() as db:
                resolve_async_disbursement(
                    db, monnify_ref=disb_ref, success=(event_type == "DISBURSEMENT_SUCCESSFUL")
                )
        return {"status": "acknowledged"}

    return {"status": "ignored", "eventType": event_type}
