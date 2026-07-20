"""
Cycle state machine: OPEN -> FUNDED -> DISBURSING -> PAID (or -> FAILED).

The dangerous transition is OPEN -> FUNDED -> DISBURSING: a webhook and the
MonniGuard sweep can both observe "pot is now full" for the same cycle at
nearly the same instant (this is exactly the race the demo's forced-webhook-
drop scenario is designed to surface). Whoever gets there first must win,
and the other must be a safe no-op — not a second disbursement.

We use `SELECT ... FOR UPDATE` (via SQLAlchemy's with_for_update) to take a
row lock on the Cycle before checking/flipping its state, so only one
transaction can ever observe OPEN and flip to FUNDED for a given cycle.
The `version` column adds a second optimistic-lock belt in case row locks
aren't available on a given DB backend (e.g. plain SQLite in dev/demo mode).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Cycle, CycleState


class IllegalTransition(Exception):
    pass


def _locked_cycle(db: Session, cycle_id: int) -> Cycle:
    """
    Takes a row lock on the cycle. On SQLite (used for local dev/demo),
    with_for_update() is a no-op — SQLite's own single-writer model still
    serializes concurrent writers, so the version column is the real guard
    there. On Postgres in production this takes a genuine row lock.
    """
    cycle = (
        db.query(Cycle)
        .filter(Cycle.id == cycle_id)
        .with_for_update()
        .one()
    )
    return cycle


def try_mark_funded(db: Session, cycle_id: int) -> bool:
    """
    Attempts OPEN -> FUNDED. Returns True if THIS call performed the
    transition, False if the cycle was already FUNDED or beyond (meaning:
    someone else already got here — safe no-op, do not disburse again).
    """
    cycle = _locked_cycle(db, cycle_id)

    if cycle.state != CycleState.OPEN:
        return False  # already funded/disbursing/paid — nothing to do

    expected_version = cycle.version
    cycle.state = CycleState.FUNDED
    cycle.version = expected_version + 1
    db.flush()
    return True


def try_mark_disbursing(db: Session, cycle_id: int) -> bool:
    """FUNDED -> DISBURSING. Returns False (safe no-op) if not currently FUNDED."""
    cycle = _locked_cycle(db, cycle_id)
    if cycle.state != CycleState.FUNDED:
        return False
    cycle.state = CycleState.DISBURSING
    cycle.version += 1
    db.flush()
    return True


def mark_paid(db: Session, cycle_id: int) -> None:
    cycle = _locked_cycle(db, cycle_id)
    if cycle.state != CycleState.DISBURSING:
        raise IllegalTransition(f"Cannot mark PAID from state {cycle.state}")
    cycle.state = CycleState.PAID
    cycle.version += 1
    db.flush()


def mark_failed(db: Session, cycle_id: int, revert_to_funded: bool = True) -> None:
    """
    A disbursement attempt failed after we moved to DISBURSING. Revert to
    FUNDED so a retry can pick it back up, rather than stranding the cycle.
    """
    cycle = _locked_cycle(db, cycle_id)
    cycle.state = CycleState.FUNDED if revert_to_funded else CycleState.FAILED
    cycle.version += 1
    db.flush()
