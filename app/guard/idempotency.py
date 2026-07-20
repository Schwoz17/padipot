"""
Idempotency layer.

Every inbound webhook and every outbound disbursement carries a reference.
Before acting on either, callers check here first. This is what prevents:
  - a retried/duplicate webhook double-crediting a contribution
  - a retried disbursement call double-paying a beneficiary
  - our own crash-and-retry logic from repeating a payout

Deterministic reference generation also lives here so the same logical
event always produces the same reference, which is what makes de-dup
possible in the first place — random UUIDs per attempt would defeat this.
"""
from app.guard.ports import LedgerPort


def is_duplicate_contribution(ledger: LedgerPort, monnify_tx_ref: str) -> bool:
    return ledger.has_contribution(monnify_tx_ref)


def is_duplicate_disbursement(ledger: LedgerPort, monnify_ref: str) -> bool:
    return ledger.has_disbursement(monnify_ref)


def guard_duplicate_contribution(ledger: LedgerPort, monnify_tx_ref: str) -> bool:
    """Returns True if this was a duplicate (and logs it). False means: safe to record."""
    if is_duplicate_contribution(ledger, monnify_tx_ref):
        ledger.log_guard_event(
            "DUPLICATE_IGNORED", monnify_tx_ref, "Contribution webhook replay ignored"
        )
        return True
    return False


def guard_duplicate_disbursement(ledger: LedgerPort, monnify_ref: str) -> bool:
    if is_duplicate_disbursement(ledger, monnify_ref):
        ledger.log_guard_event(
            "DUPLICATE_IGNORED", monnify_ref, "Disbursement retry ignored — already recorded"
        )
        return True
    return False


def deterministic_disbursement_reference(cycle_id: int) -> str:
    """
    One cycle can only ever produce one payout reference. Even if the payout
    orchestrator is accidentally invoked twice for the same cycle (crash +
    retry, double webhook, race), Monnify sees the same reference both times
    and will not process it twice.
    """
    return f"padipot-payout-cycle-{cycle_id}"


def deterministic_contribution_dedupe_key(monnify_tx_ref: str) -> str:
    """Monnify's own transactionReference is already globally unique — pass through,
    named here so callers have one obvious place to look for the dedupe key."""
    return monnify_tx_ref
