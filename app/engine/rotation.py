"""
Earned Rotation.

Payout order (Slot.position, 0 = next to collect) is protected in two
different ways depending on the pot's phase:

  FORMING (no cycle has opened yet): members self-select any open turn as
  they join — see assign_chosen_slot(). This is safe precisely because
  everyone in a forming pot has equal (zero) history; there is no trust gap
  yet to protect. Self-selection here is a real product decision, not a
  loophole: people naturally want a say in which month they collect.

  ACTIVE (a cycle has opened / pot has started, see pot_service.py):
  membership locks — no new members, no turn changes. Reordering after
  this point only ever happens automatically via on_round_closed(), which
  moves members with more completed history earlier, never by request.

This is what keeps "collect round one and vanish" structurally impossible
even though members get real choice: a brand-new person can only ever pick
from turns that are still open in a pot that hasn't started yet, alongside
other members who are, at that moment, exactly as untested as they are.
Once a pot is running, no one — not even the admin — can hand someone an
early slot they haven't earned.

Scoring for reordering deliberately reuses PadiScore's completed-cycle count
and clean-record signal, so there is only one source of truth for "how much
history does this member have."
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Pot, Slot


def assign_new_member_slot(db: Session, *, pot_id: int, member_id: int) -> Slot:
    """
    Auto-append version: new member enters at the highest (latest) position.
    Used by scripts/demo_seed.py and anywhere a specific turn choice isn't
    relevant. The WhatsApp JOIN command uses assign_chosen_slot() instead,
    so real members get to pick.
    """
    existing_positions = [s.position for s in db.query(Slot).filter_by(pot_id=pot_id).all()]
    next_position = (max(existing_positions) + 1) if existing_positions else 0

    slot = Slot(pot_id=pot_id, member_id=member_id, position=next_position)
    db.add(slot)
    db.flush()
    return slot


def available_turns(db: Session, pot_id: int) -> list[int]:
    """1-indexed turn numbers still open to claim, up to the pot's target size."""
    pot = db.get(Pot, pot_id)
    taken_positions = {s.position for s in db.query(Slot).filter_by(pot_id=pot_id).all()}
    return [t for t in range(1, pot.size + 1) if (t - 1) not in taken_positions]


def assign_chosen_slot(db: Session, *, pot_id: int, member_id: int, requested_turn: int) -> Slot:
    """
    Member self-selects an open turn during pot formation. requested_turn
    is 1-indexed for a human-friendly WhatsApp UX ("turn 1" = collects
    first); stored internally as position (0-indexed), same field
    on_round_closed() and next_beneficiary_slot() already use.

    Only called pre-start — the caller (flows.handle_join_pot) is
    responsible for checking pot_service.pot_has_started() first.
    Raises ValueError if the turn is already taken or out of range.
    """
    pot = db.get(Pot, pot_id)
    if requested_turn < 1 or requested_turn > pot.size:
        raise ValueError(f"Turn must be between 1 and {pot.size}")
    position = requested_turn - 1
    already_taken = db.query(Slot).filter_by(pot_id=pot_id, position=position).first()
    if already_taken:
        raise ValueError(f"Turn {requested_turn} is already taken")

    slot = Slot(pot_id=pot_id, member_id=member_id, position=position)
    db.add(slot)
    db.flush()
    return slot


def _completed_cycle_count(db: Session, member_id: int) -> int:
    """
    Count of pots this member has fully completed a collection round in
    (across ALL pots, not just this one) — a member with a long clean
    record anywhere earns trust everywhere, same idea as a credit history.
    """
    return (
        db.query(Slot)
        .filter(Slot.member_id == member_id, Slot.has_collected.is_(True))
        .count()
    )


def on_round_closed(db: Session, *, pot_id: int, beneficiary_member_id: int) -> None:
    """
    Called by payout.py the instant a round pays out successfully.
    Reorders the REMAINING (not-yet-collected) slots in this pot so members
    with more completed history across the platform sit earlier — but never
    ahead of a slot's own earned_at ordering tiebreak, so two equally-scored
    members keep the order they joined in.
    """
    remaining = (
        db.query(Slot)
        .filter(Slot.pot_id == pot_id, Slot.has_collected.is_(False))
        .all()
    )
    if not remaining:
        return

    scored = [
        (slot, _completed_cycle_count(db, slot.member_id), slot.earned_at)
        for slot in remaining
    ]
    # Higher completed-cycle count moves earlier (toward position 0);
    # ties broken by who has been in this pot's queue longest.
    scored.sort(key=lambda t: (-t[1], t[2]))

    for new_position, (slot, _, _) in enumerate(scored):
        slot.position = new_position
    db.flush()


def next_beneficiary_slot(db: Session, pot_id: int) -> Slot | None:
    return (
        db.query(Slot)
        .filter(Slot.pot_id == pot_id, Slot.has_collected.is_(False))
        .order_by(Slot.position.asc())
        .first()
    )