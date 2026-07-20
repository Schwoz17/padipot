from datetime import datetime, timedelta

import pytest

from app.models import Cycle, CycleState, Slot
from app.engine import state_machine


def _make_cycle(db_session, pot, beneficiary_slot):
    cycle = Cycle(
        pot_id=pot.id,
        round_no=1,
        beneficiary_slot_id=beneficiary_slot.id,
        opens_at=datetime.utcnow(),
        deadline=datetime.utcnow() + timedelta(days=7),
        state=CycleState.OPEN,
    )
    db_session.add(cycle)
    db_session.commit()
    return cycle


def test_open_to_funded_happens_once(db_session, make_member, make_pot):
    admin = make_member("2340000001", "Admin")
    pot = make_pot(admin.id)
    slot = db_session.query(Slot).filter_by(pot_id=pot.id, member_id=admin.id).first()
    cycle = _make_cycle(db_session, pot, slot)

    first_call = state_machine.try_mark_funded(db_session, cycle.id)
    db_session.commit()
    second_call = state_machine.try_mark_funded(db_session, cycle.id)  # simulates webhook+sweep race
    db_session.commit()

    assert first_call is True
    assert second_call is False  # the race loser gets a safe no-op, not a double transition

    refreshed = db_session.get(Cycle, cycle.id)
    assert refreshed.state == CycleState.FUNDED


def test_full_happy_path_transitions(db_session, make_member, make_pot):
    admin = make_member("2340000001", "Admin")
    pot = make_pot(admin.id)
    slot = db_session.query(Slot).filter_by(pot_id=pot.id, member_id=admin.id).first()
    cycle = _make_cycle(db_session, pot, slot)

    assert state_machine.try_mark_funded(db_session, cycle.id) is True
    assert state_machine.try_mark_disbursing(db_session, cycle.id) is True
    state_machine.mark_paid(db_session, cycle.id)
    db_session.commit()

    refreshed = db_session.get(Cycle, cycle.id)
    assert refreshed.state == CycleState.PAID


def test_cannot_mark_paid_before_disbursing(db_session, make_member, make_pot):
    admin = make_member("2340000001", "Admin")
    pot = make_pot(admin.id)
    slot = db_session.query(Slot).filter_by(pot_id=pot.id, member_id=admin.id).first()
    cycle = _make_cycle(db_session, pot, slot)

    with pytest.raises(state_machine.IllegalTransition):
        state_machine.mark_paid(db_session, cycle.id)


def test_failed_disbursement_reverts_to_funded_for_retry(db_session, make_member, make_pot):
    admin = make_member("2340000001", "Admin")
    pot = make_pot(admin.id)
    slot = db_session.query(Slot).filter_by(pot_id=pot.id, member_id=admin.id).first()
    cycle = _make_cycle(db_session, pot, slot)

    state_machine.try_mark_funded(db_session, cycle.id)
    state_machine.try_mark_disbursing(db_session, cycle.id)
    state_machine.mark_failed(db_session, cycle.id, revert_to_funded=True)
    db_session.commit()

    refreshed = db_session.get(Cycle, cycle.id)
    assert refreshed.state == CycleState.FUNDED  # retryable, not stranded
