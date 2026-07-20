import pytest

from app.engine import pot_service, rotation
from app.models import Slot, CycleState


def test_new_pot_has_not_started(db_session, make_member, make_pot):
    admin = make_member("2340000101", "Admin")
    pot = make_pot(admin.id, size=3)
    assert pot_service.pot_has_started(db_session, pot.id) is False


def test_start_pot_fails_with_fewer_than_two_members(db_session, make_member, make_pot):
    admin = make_member("2340000102", "Admin")
    pot = make_pot(admin.id, size=5)  # only admin has joined so far

    with pytest.raises(ValueError, match="at least 2 members"):
        pot_service.start_pot(db_session, pot_id=pot.id, requesting_member_id=admin.id)


def test_start_pot_fails_for_non_admin(db_session, make_member, make_pot):
    admin = make_member("2340000103", "Admin")
    pot = make_pot(admin.id, size=3)
    other = make_member("2340000104", "Other")
    rotation.assign_chosen_slot(db_session, pot_id=pot.id, member_id=other.id, requested_turn=2)

    with pytest.raises(ValueError, match="Only the pot admin"):
        pot_service.start_pot(db_session, pot_id=pot.id, requesting_member_id=other.id)


def test_start_pot_locks_size_to_actual_member_count(db_session, make_member, make_pot):
    admin = make_member("2340000105", "Admin")
    pot = make_pot(admin.id, size=5)  # target of 5, but only 2 will actually join
    other = make_member("2340000106", "Other")
    rotation.assign_chosen_slot(db_session, pot_id=pot.id, member_id=other.id, requested_turn=2)

    pot_service.start_pot(db_session, pot_id=pot.id, requesting_member_id=admin.id)

    db_session.refresh(pot)
    assert pot.size == 2  # locked to actual joined count, not the original target of 5
    assert pot_service.pot_has_started(db_session, pot.id) is True


def test_start_pot_opens_the_first_cycle(db_session, make_member, make_pot):
    admin = make_member("2340000107", "Admin")
    pot = make_pot(admin.id, size=2)
    other = make_member("2340000108", "Other")
    rotation.assign_chosen_slot(db_session, pot_id=pot.id, member_id=other.id, requested_turn=2)

    cycle = pot_service.start_pot(db_session, pot_id=pot.id, requesting_member_id=admin.id)
    assert cycle.round_no == 1
    assert cycle.state == CycleState.OPEN


def test_cannot_start_an_already_started_pot(db_session, make_member, make_pot):
    admin = make_member("2340000109", "Admin")
    pot = make_pot(admin.id, size=2)
    other = make_member("2340000110", "Other")
    rotation.assign_chosen_slot(db_session, pot_id=pot.id, member_id=other.id, requested_turn=2)
    pot_service.start_pot(db_session, pot_id=pot.id, requesting_member_id=admin.id)

    with pytest.raises(ValueError, match="already started"):
        pot_service.start_pot(db_session, pot_id=pot.id, requesting_member_id=admin.id)


def test_leave_pot_frees_the_turn_before_start(db_session, make_member, make_pot):
    admin = make_member("2340000111", "Admin")
    pot = make_pot(admin.id, size=3)
    other = make_member("2340000112", "Other")
    rotation.assign_chosen_slot(db_session, pot_id=pot.id, member_id=other.id, requested_turn=2)

    pot_service.leave_pot(db_session, pot_id=pot.id, member_id=other.id)

    remaining = db_session.query(Slot).filter_by(pot_id=pot.id, member_id=other.id).first()
    assert remaining is None
    assert rotation.available_turns(db_session, pot.id) == [2, 3]


def test_cannot_leave_a_pot_that_has_already_started(db_session, make_member, make_pot):
    admin = make_member("2340000113", "Admin")
    pot = make_pot(admin.id, size=2)
    other = make_member("2340000114", "Other")
    rotation.assign_chosen_slot(db_session, pot_id=pot.id, member_id=other.id, requested_turn=2)
    pot_service.start_pot(db_session, pot_id=pot.id, requesting_member_id=admin.id)

    with pytest.raises(ValueError, match="already started"):
        pot_service.leave_pot(db_session, pot_id=pot.id, member_id=other.id)