"""
Defaulter registry.

A member who exits a pot with obligations unmet gets a RegistryEntry. This
gates future participation (join_gate below) but does not itself compensate
anyone — that's the point: PadiPot doesn't need an escrow vault to absorb
losses, because Earned Rotation already prevents early collection, so the
"damage" a default can do is inherently capped to whatever that member had
already legitimately funded elsewhere. Registry + Earned Rotation together
are what stand in for PadiPay's Guarantee Vault.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Member, RegistryEntry


def record_default(db: Session, *, member_id: int, pot_id: int, amount_outstanding: float) -> RegistryEntry:
    entry = RegistryEntry(member_id=member_id, pot_id=pot_id, amount_outstanding=amount_outstanding)
    db.add(entry)

    member = db.get(Member, member_id)
    member.registry_flag = True

    db.flush()
    return entry


def clear_default(db: Session, *, registry_entry_id: int) -> RegistryEntry:
    entry = db.get(RegistryEntry, registry_entry_id)
    entry.cleared_at = datetime.utcnow()
    db.flush()

    member = db.get(Member, entry.member_id)
    has_other_unresolved = (
        db.query(RegistryEntry)
        .filter(RegistryEntry.member_id == member.id, RegistryEntry.cleared_at.is_(None))
        .count()
        > 0
    )
    member.registry_flag = has_other_unresolved
    db.flush()
    return entry


def can_join_pot(db: Session, member_id: int) -> tuple[bool, str]:
    """
    The join-gate. Returns (allowed, reason). Called by the WhatsApp/USSD
    pot-join flow before creating a Slot/ReservedAccount for a member.
    """
    unresolved = (
        db.query(RegistryEntry)
        .filter(RegistryEntry.member_id == member_id, RegistryEntry.cleared_at.is_(None))
        .all()
    )
    if unresolved:
        total = sum(float(e.amount_outstanding) for e in unresolved)
        return False, f"Outstanding default of NGN{total:,.2f} across {len(unresolved)} pot(s) must be cleared first"
    return True, "OK"


def unresolved_entries(db: Session, member_id: int) -> list[RegistryEntry]:
    return (
        db.query(RegistryEntry)
        .filter(RegistryEntry.member_id == member_id, RegistryEntry.cleared_at.is_(None))
        .all()
    )
