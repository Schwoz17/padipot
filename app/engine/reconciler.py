"""
Contribution reconciler.

Two entry points feed this module:
  - record_webhook_contribution(): called from the WhatsApp/webhook receiver
    the instant Monnify confirms a transfer.
  - record_sweep_contribution(): called from the scheduler after
    guard/sweep.py finds a payment the webhook path missed.

Both converge on the same `_record` function so a webhook-caught and a
sweep-caught contribution are indistinguishable in the ledger afterwards —
the only difference is the `source` field, kept for audit/demo purposes.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Contribution, Cycle, Pot, ReservedAccount, Slot
from app.guard.idempotency import guard_duplicate_contribution
from app.engine.state_machine import try_mark_funded
from app.engine import padiscore


class _SqlLedgerAdapter:
    """Adapts a DB session to the guard.ports.LedgerPort protocol."""

    def __init__(self, db: Session):
        self.db = db

    def has_contribution(self, monnify_tx_ref: str) -> bool:
        return self.db.query(Contribution).filter_by(monnify_tx_ref=monnify_tx_ref).first() is not None

    def has_disbursement(self, monnify_ref: str) -> bool:
        from app.models import Disbursement
        return self.db.query(Disbursement).filter_by(monnify_ref=monnify_ref).first() is not None

    def log_guard_event(self, event_type: str, reference: str, detail: str) -> None:
        from app.models import GuardLog
        self.db.add(GuardLog(event_type=event_type, reference=reference, detail=detail))
        self.db.flush()


def _cycle_is_full(db: Session, cycle: Cycle) -> bool:
    pot = db.get(Pot, cycle.pot_id)
    total_contributed = sum(
        Decimal(str(c.amount)) for c in db.query(Contribution).filter_by(cycle_id=cycle.id).all()
    )
    required = Decimal(str(pot.amount)) * Decimal(pot.size - 1)  # everyone except the beneficiary
    return total_contributed >= required


async def _record(
    db: Session,
    *,
    account_reference: str,
    monnify_tx_ref: str,
    amount: float,
    source: str,
) -> Contribution | None:
    ledger = _SqlLedgerAdapter(db)
    if guard_duplicate_contribution(ledger, monnify_tx_ref):
        return None

    account = db.query(ReservedAccount).filter_by(account_reference=account_reference).first()
    if account is None:
        ledger.log_guard_event("UNKNOWN_ACCOUNT", account_reference, "No reserved account matches this reference")
        return None

    cycle = (
        db.query(Cycle)
        .filter(Cycle.pot_id == account.pot_id)
        .filter(Cycle.state == "OPEN")
        .order_by(Cycle.round_no.desc())
        .first()
    )
    if cycle is None:
        ledger.log_guard_event("NO_OPEN_CYCLE", account_reference, "Payment received but no open cycle for this pot")
        return None

    contribution = Contribution(
        cycle_id=cycle.id,
        member_id=account.member_id,
        monnify_tx_ref=monnify_tx_ref,
        amount=amount,
        source=source,
    )
    db.add(contribution)
    db.flush()

    # Update PadiScore for on-time / late funding behaviour
    padiscore.record_funding_event(db, member_id=account.member_id, cycle=cycle, funded_at=contribution.funded_at)

    if _cycle_is_full(db, cycle):
        try_mark_funded(db, cycle.id)  # safe no-op if another path already flipped it

    db.commit()
    return contribution


async def record_webhook_contribution(db: Session, *, account_reference: str, monnify_tx_ref: str, amount: float) -> Contribution | None:
    return await _record(db, account_reference=account_reference, monnify_tx_ref=monnify_tx_ref, amount=amount, source="WEBHOOK")


async def record_sweep_contribution(db: Session, *, account_reference: str, monnify_tx_ref: str, amount: float) -> Contribution | None:
    return await _record(db, account_reference=account_reference, monnify_tx_ref=monnify_tx_ref, amount=amount, source="SWEEP")
