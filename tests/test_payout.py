from datetime import datetime, timedelta

from app.models import Cycle, CycleState, Disbursement, DisbursementState, Slot
from app.engine.payout import resolve_async_disbursement


def _make_disbursing_cycle(db_session, pot, beneficiary_slot, monnify_ref):
    cycle = Cycle(
        pot_id=pot.id,
        round_no=1,
        beneficiary_slot_id=beneficiary_slot.id,
        opens_at=datetime.utcnow(),
        deadline=datetime.utcnow() + timedelta(days=7),
        state=CycleState.DISBURSING,
    )
    db_session.add(cycle)
    db_session.flush()

    disbursement = Disbursement(
        cycle_id=cycle.id,
        recipient_member_id=beneficiary_slot.member_id,
        monnify_ref=monnify_ref,
        amount=float(pot.amount) * (pot.size - 1),
        state=DisbursementState.PROCESSING,
    )
    db_session.add(disbursement)
    db_session.commit()
    return cycle, disbursement


def test_success_confirmation_marks_cycle_paid(db_session, make_member, make_pot):
    admin = make_member("2340000201", "Admin")
    pot = make_pot(admin.id, size=2)
    slot = db_session.query(Slot).filter_by(pot_id=pot.id, member_id=admin.id).first()
    cycle, disbursement = _make_disbursing_cycle(db_session, pot, slot, "padipot-payout-cycle-99")

    result = resolve_async_disbursement(db_session, monnify_ref="padipot-payout-cycle-99", success=True)

    assert result == "PAID"
    refreshed_cycle = db_session.get(Cycle, cycle.id)
    refreshed_disbursement = db_session.get(Disbursement, disbursement.id)
    assert refreshed_cycle.state == CycleState.PAID
    assert refreshed_disbursement.state == DisbursementState.SUCCESS
    assert refreshed_disbursement.completed_at is not None


def test_success_confirmation_marks_slot_collected(db_session, make_member, make_pot):
    admin = make_member("2340000202", "Admin")
    pot = make_pot(admin.id, size=2)
    slot = db_session.query(Slot).filter_by(pot_id=pot.id, member_id=admin.id).first()
    _make_disbursing_cycle(db_session, pot, slot, "padipot-payout-cycle-100")

    resolve_async_disbursement(db_session, monnify_ref="padipot-payout-cycle-100", success=True)

    refreshed_slot = db_session.get(Slot, slot.id)
    assert refreshed_slot.has_collected is True


def test_failure_confirmation_reverts_cycle_for_retry(db_session, make_member, make_pot):
    admin = make_member("2340000203", "Admin")
    pot = make_pot(admin.id, size=2)
    slot = db_session.query(Slot).filter_by(pot_id=pot.id, member_id=admin.id).first()
    cycle, disbursement = _make_disbursing_cycle(db_session, pot, slot, "padipot-payout-cycle-101")

    result = resolve_async_disbursement(db_session, monnify_ref="padipot-payout-cycle-101", success=False)

    assert result == "REVERTED_FOR_RETRY"
    refreshed_cycle = db_session.get(Cycle, cycle.id)
    refreshed_disbursement = db_session.get(Disbursement, disbursement.id)
    assert refreshed_cycle.state == CycleState.FUNDED  # retryable, not stranded
    assert refreshed_disbursement.state == DisbursementState.FAILED


def test_duplicate_confirmation_is_a_safe_no_op(db_session, make_member, make_pot):
    admin = make_member("2340000204", "Admin")
    pot = make_pot(admin.id, size=2)
    slot = db_session.query(Slot).filter_by(pot_id=pot.id, member_id=admin.id).first()
    _make_disbursing_cycle(db_session, pot, slot, "padipot-payout-cycle-102")

    first = resolve_async_disbursement(db_session, monnify_ref="padipot-payout-cycle-102", success=True)
    second = resolve_async_disbursement(db_session, monnify_ref="padipot-payout-cycle-102", success=True)

    assert first == "PAID"
    assert second == "ALREADY_RESOLVED"  # a retried webhook can't double-process


def test_unknown_reference_is_handled_gracefully(db_session):
    result = resolve_async_disbursement(db_session, monnify_ref="does-not-exist", success=True)
    assert result == "UNKNOWN_REFERENCE"