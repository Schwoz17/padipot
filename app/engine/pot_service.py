"""
Pot lifecycle: creation, formation, starting, leaving, and round-opening.
Kept separate from rotation.py (which only cares about slot ordering) and
payout.py (which only cares about closing a round out) so each file
answers one question.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Pot, Cycle, CycleState, Language, PotStatus, Slot
from app.engine import rotation


def create_pot(
    db: Session,
    *,
    name: str,
    admin_id: int,
    size: int,
    amount: float,
    cadence_days: int = 7,
    language: Language = Language.EN,
) -> Pot:
    """
    `size` is a TARGET, not a hard requirement — the pot stays in formation
    (open to new members self-selecting a turn) until the admin calls
    start_pot(), at which point it locks to however many people actually
    joined. This means an admin doesn't need an exact headcount up front.
    """
    pot = Pot(
        name=name,
        admin_id=admin_id,
        size=size,
        amount=amount,
        cadence_days=cadence_days,
        language=language,
        status=PotStatus.ACTIVE,
    )
    db.add(pot)
    db.flush()

    # Admin gets turn 1 automatically — a reasonable default for the
    # person creating the pot. Everyone who joins after this picks their
    # own turn via assign_chosen_slot (see rotation.py and flows.handle_join_pot).
    rotation.assign_new_member_slot(db, pot_id=pot.id, member_id=admin_id)

    db.commit()
    return pot


def pot_has_started(db: Session, pot_id: int) -> bool:
    """A pot has 'started' the instant its first cycle opens — after that, membership locks."""
    return db.query(Cycle).filter_by(pot_id=pot_id).first() is not None


def start_pot(db: Session, *, pot_id: int, requesting_member_id: int) -> Cycle:
    """
    Admin-only. Locks membership at whatever has actually joined (minimum
    2 — a pot of 1 isn't a rotation), sets pot.size to that real count
    (overriding the original target size), and opens round 1.

    Raises ValueError with a message safe to show directly to the member
    on WhatsApp for any failure case.
    """
    pot = db.get(Pot, pot_id)
    if pot is None:
        raise ValueError(f"No pot found with ID {pot_id}.")
    if pot.admin_id != requesting_member_id:
        raise ValueError("Only the pot admin can start it.")
    if pot_has_started(db, pot_id):
        raise ValueError(f"'{pot.name}' has already started.")

    slots = db.query(Slot).filter_by(pot_id=pot_id).all()
    if len(slots) < 2:
        raise ValueError(f"Need at least 2 members before starting — '{pot.name}' currently has {len(slots)}.")

    pot.size = len(slots)  # lock to actual joined count, not the original target
    db.flush()

    cycle = open_next_cycle(db, pot_id)
    if cycle is None:
        raise ValueError("Couldn't open the first round — please check the pot's members and try again.")
    return cycle


def leave_pot(db: Session, *, pot_id: int, member_id: int) -> None:
    """
    Pre-start only. A member who leaves before the pot starts simply frees
    their turn for someone else to claim — no consequence, since no money
    has moved yet. Once a pot has started, walking away isn't a "leave,"
    it's a default — that routes through app.engine.registry instead.
    """
    pot = db.get(Pot, pot_id)
    if pot is None:
        raise ValueError(f"No pot found with ID {pot_id}.")
    if pot_has_started(db, pot_id):
        raise ValueError(f"Can't leave — '{pot.name}' has already started. Contact the admin.")

    slot = db.query(Slot).filter_by(pot_id=pot_id, member_id=member_id).first()
    if slot is None:
        raise ValueError(f"You're not a member of '{pot.name}'.")

    db.delete(slot)
    db.commit()


def open_next_cycle(db: Session, pot_id: int) -> Cycle | None:
    """
    Opens round N+1 once the pot has enough members to fill its size and the
    previous round (if any) is fully PAID. Beneficiary is whoever currently
    sits at position 0 per Earned Rotation.
    """
    pot = db.get(Pot, pot_id)

    last_cycle = db.query(Cycle).filter_by(pot_id=pot_id).order_by(Cycle.round_no.desc()).first()
    if last_cycle is not None and last_cycle.state != CycleState.PAID:
        return None  # previous round still in flight

    next_slot = rotation.next_beneficiary_slot(db, pot_id)
    if next_slot is None:
        return None  # everyone has collected — pot is complete

    round_no = (last_cycle.round_no + 1) if last_cycle else 1
    cycle = Cycle(
        pot_id=pot_id,
        round_no=round_no,
        beneficiary_slot_id=next_slot.id,
        opens_at=datetime.utcnow(),
        deadline=datetime.utcnow() + timedelta(days=pot.cadence_days),
        state=CycleState.OPEN,
    )
    db.add(cycle)
    db.flush()
    db.commit()
    return cycle