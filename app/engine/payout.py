"""
Payout orchestrator.

Runs whenever a cycle is FUNDED (called right after reconciler.py flips a
cycle, and also safe to call again from a retry job — every check here is
idempotent). Sequence:

  1. Try FUNDED -> DISBURSING (state machine; no-op if already past this point)
  2. Run MonniGuard pre-flight (balance, bank health, name enquiry)
  3. If pre-flight fails: revert to FUNDED, log, notify — do NOT call Monnify
  4. If pre-flight passes: call Monnify disburse() with a deterministic
     reference, so even a duplicate orchestrator run is a safe no-op on
     Monnify's side too
  5. On success: mark cycle PAID, record Disbursement, bump PadiScore/registry
     bookkeeping for the round closing out
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Cycle, Disbursement, DisbursementState, Member, Pot, Slot
from app.monnify.client import monnify_client, MonnifyError
from app.guard.preflight import run_preflight
from app.guard.idempotency import guard_duplicate_disbursement, deterministic_disbursement_reference
from app.engine.state_machine import try_mark_disbursing, mark_paid, mark_failed
from app.engine import rotation


class _SqlLedgerAdapter:
    def __init__(self, db: Session):
        self.db = db

    def has_contribution(self, monnify_tx_ref: str) -> bool:
        from app.models import Contribution
        return self.db.query(Contribution).filter_by(monnify_tx_ref=monnify_tx_ref).first() is not None

    def has_disbursement(self, monnify_ref: str) -> bool:
        return self.db.query(Disbursement).filter_by(monnify_ref=monnify_ref).first() is not None

    def log_guard_event(self, event_type: str, reference: str, detail: str) -> None:
        from app.models import GuardLog
        self.db.add(GuardLog(event_type=event_type, reference=reference, detail=detail))
        self.db.flush()


class _MonnifyQueryAdapter:
    """Adapts the real Monnify client to guard.ports.MonnifyQueryPort for pre-flight."""

    async def get_reserved_account_transactions(self, account_reference: str) -> list[dict]:
        return await monnify_client.get_reserved_account_transactions(account_reference)

    async def validate_bank_account(self, account_number: str, bank_code: str) -> dict:
        return await monnify_client.validate_bank_account(account_number, bank_code)


async def run_payout_for_cycle(db: Session, cycle_id: int, *, wallet_balance: float) -> str:
    """
    Returns a short status string for logging/demo narration:
    "PAID" | "DEFERRED: <reasons>" | "ALREADY_DONE" | "FAILED: <error>"
    """
    ledger = _SqlLedgerAdapter(db)
    reference = deterministic_disbursement_reference(cycle_id)

    if guard_duplicate_disbursement(ledger, reference):
        db.commit()
        return "ALREADY_DONE"

    if not try_mark_disbursing(db, cycle_id):
        db.commit()
        return "ALREADY_DONE"  # someone else already progressed this cycle

    cycle = db.get(Cycle, cycle_id)
    slot = db.get(Slot, cycle.beneficiary_slot_id)
    beneficiary = db.get(Member, slot.member_id)
    pot = db.get(Pot, cycle.pot_id)
    payout_amount = float(pot.amount) * (pot.size - 1)

    # NOTE: destination bank details would come from the member's registered
    # payout account, collected at slot-claim time. Placeholder fields below
    # (bank_code/account_number on Member) are assumed present via onboarding;
    # wire up to your actual payout-details capture flow.
    destination_account_number = getattr(beneficiary, "payout_account_number", "") or ""
    destination_bank_code = getattr(beneficiary, "payout_bank_code", "") or ""

    preflight = await run_preflight(
        wallet_balance=wallet_balance,
        amount=payout_amount,
        destination_account_number=destination_account_number,
        destination_bank_code=destination_bank_code,
        expected_account_name=beneficiary.name,
        monnify=_MonnifyQueryAdapter(),
    )

    if not preflight.passed:
        ledger.log_guard_event("PREFLIGHT_BLOCK", reference, preflight.summary())
        mark_failed(db, cycle_id, revert_to_funded=True)
        db.commit()
        return f"DEFERRED: {preflight.summary()}"

    try:
        result = await monnify_client.disburse(
            reference=reference,
            amount=payout_amount,
            destination_account_number=destination_account_number,
            destination_bank_code=destination_bank_code,
            destination_account_name=beneficiary.name,
            narration=f"PadiPot payout - {pot.name} round {cycle.round_no}",
        )
    except MonnifyError as exc:
        ledger.log_guard_event("DISBURSEMENT_ERROR", reference, str(exc))
        mark_failed(db, cycle_id, revert_to_funded=True)
        db.commit()
        return f"FAILED: {exc}"

    disbursement = Disbursement(
        cycle_id=cycle_id,
        recipient_member_id=beneficiary.id,
        monnify_ref=reference,
        amount=payout_amount,
        preflight_result=preflight.summary(),
        state=DisbursementState.SUCCESS if result.status == "SUCCESS" else DisbursementState.PROCESSING,
    )
    db.add(disbursement)

    if result.status == "SUCCESS":
        # Synchronous confirmation — safe to close the round out immediately.
        mark_paid(db, cycle_id)
        slot.has_collected = True
        rotation.on_round_closed(db, pot_id=pot.id, beneficiary_member_id=beneficiary.id)
        db.commit()
        return "PAID"

    # Async disbursement ACCEPTED but not yet CONFIRMED — cycle stays in
    # DISBURSING. Acceptance is not the same as success: Monnify can still
    # report DISBURSEMENT_FAILED later for a request it initially accepted.
    # resolve_async_disbursement() below, called from the webhook router
    # when that confirmation arrives, is what actually closes this out.
    db.commit()
    return f"PROCESSING: awaiting Monnify confirmation (status={result.status})"


def resolve_async_disbursement(db: Session, *, monnify_ref: str, success: bool) -> str:
    """
    Called by the Monnify webhook router (DISBURSEMENT_SUCCESSFUL /
    DISBURSEMENT_FAILED) once an async disbursement actually resolves.
    This — not the initial accept in run_payout_for_cycle — is what's
    allowed to mark a cycle PAID, so a disbursement that's accepted and
    then later fails can never leave the system showing money that never
    actually moved.
    """
    disbursement = db.query(Disbursement).filter_by(monnify_ref=monnify_ref).first()
    if disbursement is None:
        return "UNKNOWN_REFERENCE"
    if disbursement.state in (DisbursementState.SUCCESS, DisbursementState.FAILED):
        return "ALREADY_RESOLVED"  # idempotent — a duplicate webhook is a safe no-op

    cycle = db.get(Cycle, disbursement.cycle_id)

    if success:
        disbursement.state = DisbursementState.SUCCESS
        disbursement.completed_at = datetime.utcnow()
        mark_paid(db, cycle.id)
        slot = db.get(Slot, cycle.beneficiary_slot_id)
        slot.has_collected = True
        pot = db.get(Pot, cycle.pot_id)
        rotation.on_round_closed(db, pot_id=pot.id, beneficiary_member_id=disbursement.recipient_member_id)
        db.commit()
        return "PAID"

    disbursement.state = DisbursementState.FAILED
    mark_failed(db, cycle.id, revert_to_funded=True)
    db.commit()
    return "REVERTED_FOR_RETRY"