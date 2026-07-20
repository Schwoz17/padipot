from app.engine import rotation
from app.models import Slot


def test_new_member_always_joins_at_the_back(db_session, make_member, make_pot):
    admin = make_member("2340000001", "Admin")
    pot = make_pot(admin.id)

    m2 = make_member("2340000002", "Bola")
    m3 = make_member("2340000003", "Chidi")

    slot2 = rotation.assign_new_member_slot(db_session, pot_id=pot.id, member_id=m2.id)
    slot3 = rotation.assign_new_member_slot(db_session, pot_id=pot.id, member_id=m3.id)

    positions = [s.position for s in db_session.query(Slot).filter_by(pot_id=pot.id).all()]
    assert slot2.position == 1  # admin already holds position 0
    assert slot3.position == 2
    assert positions == sorted(positions)


def test_member_can_self_select_any_open_turn_during_formation(db_session, make_member, make_pot):
    admin = make_member("2340000001", "Admin")
    pot = make_pot(admin.id, size=4)  # admin holds turn 1 automatically
    newcomer = make_member("2340000004", "Newcomer")

    # A brand-new member picking turn 2 is fine — everyone in a forming
    # pot has equal (zero) history, so there's no trust gap to protect yet.
    slot = rotation.assign_chosen_slot(db_session, pot_id=pot.id, member_id=newcomer.id, requested_turn=2)
    assert slot.position == 1  # turn 2 -> position 1 (0-indexed)


def test_cannot_claim_an_already_taken_turn(db_session, make_member, make_pot):
    admin = make_member("2340000001", "Admin")
    pot = make_pot(admin.id, size=3)
    challenger = make_member("2340000008", "Challenger")

    import pytest
    with pytest.raises(ValueError, match="already taken"):
        rotation.assign_chosen_slot(db_session, pot_id=pot.id, member_id=challenger.id, requested_turn=1)


def test_cannot_claim_a_turn_outside_the_target_size(db_session, make_member, make_pot):
    admin = make_member("2340000001", "Admin")
    pot = make_pot(admin.id, size=3)
    newcomer = make_member("2340000009", "Newcomer")

    import pytest
    with pytest.raises(ValueError, match="between 1 and 3"):
        rotation.assign_chosen_slot(db_session, pot_id=pot.id, member_id=newcomer.id, requested_turn=5)


def test_available_turns_excludes_taken_positions(db_session, make_member, make_pot):
    admin = make_member("2340000001", "Admin")
    pot = make_pot(admin.id, size=4)  # admin takes turn 1
    m2 = make_member("2340000010", "Second")
    rotation.assign_chosen_slot(db_session, pot_id=pot.id, member_id=m2.id, requested_turn=3)

    assert rotation.available_turns(db_session, pot.id) == [2, 4]


def test_member_with_more_history_moves_earlier_after_round_closes(db_session, make_member, make_pot):
    admin = make_member("2340000001", "Admin")
    pot = make_pot(admin.id, size=3)

    veteran = make_member("2340000005", "Veteran")
    newcomer = make_member("2340000006", "Newcomer")

    # Give the veteran prior completed-cycle history via a slot in a different pot
    other_pot = make_pot(admin.id, name="Other Pot", size=2)
    veteran_prior_slot = rotation.assign_new_member_slot(db_session, pot_id=other_pot.id, member_id=veteran.id)
    veteran_prior_slot.has_collected = True
    db_session.flush()

    rotation.assign_new_member_slot(db_session, pot_id=pot.id, member_id=veteran.id)
    rotation.assign_new_member_slot(db_session, pot_id=pot.id, member_id=newcomer.id)

    # Simulate the admin's own round closing — should trigger a reorder of
    # the remaining slots, moving the veteran ahead of the equally-new newcomer.
    rotation.on_round_closed(db_session, pot_id=pot.id, beneficiary_member_id=admin.id)

    veteran_slot = db_session.query(Slot).filter_by(pot_id=pot.id, member_id=veteran.id).first()
    newcomer_slot = db_session.query(Slot).filter_by(pot_id=pot.id, member_id=newcomer.id).first()
    assert veteran_slot.position < newcomer_slot.position


def test_next_beneficiary_skips_members_who_already_collected(db_session, make_member, make_pot):
    admin = make_member("2340000001", "Admin")
    pot = make_pot(admin.id, size=2)

    admin_slot = db_session.query(Slot).filter_by(pot_id=pot.id, member_id=admin.id).first()
    admin_slot.has_collected = True
    db_session.flush()

    other = make_member("2340000007", "Other")
    other_slot = rotation.assign_new_member_slot(db_session, pot_id=pot.id, member_id=other.id)

    next_slot = rotation.next_beneficiary_slot(db_session, pot.id)
    assert next_slot.id == other_slot.id