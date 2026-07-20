"""
PadiScore — rule-based reliability index, 0-100.

Deliberately NOT machine learning. Every input is a plain count or duration
pulled straight from the contribution ledger, and every weight is a named
constant below, so the score is defensible under technical questioning
("why is my score 74?" has a one-paragraph answer). At scale, this ledger
becomes training data for a real ML model — the module boundary
(compute_padiscore takes a MemberStats in, returns a float out) is exactly
where that swap would happen.

Weights (must sum to 1.0):
  on-time streak        30%
  average funding delay 25%
  missed payment count  20%
  tenure                15%
  recovery record        10%
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Contribution, Cycle, Member, RegistryEntry, Slot

WEIGHT_STREAK = 0.30
WEIGHT_DELAY = 0.25
WEIGHT_MISSED = 0.20
WEIGHT_TENURE = 0.15
WEIGHT_RECOVERY = 0.10

# Diminishing-returns caps so no single factor needs an arbitrary "max value"
# guess — scores naturally approach (but never quite reach) the cap.
STREAK_SATURATION = 12       # cycles of perfect streak to hit ~full marks
TENURE_SATURATION = 20       # completed cycles across all pots
MAX_REASONABLE_DELAY_HOURS = 48.0  # delay at/beyond this scores ~0 on this factor


@dataclass
class MemberStats:
    on_time_streak: int
    average_delay_hours: float
    missed_payment_count: int
    completed_cycles_all_pots: int
    unresolved_defaults: int
    resolved_defaults: int


def _saturating(value: float, saturation_point: float) -> float:
    """Smooth 0..1 curve that approaches 1 as value approaches saturation_point, never exceeding it."""
    if saturation_point <= 0:
        return 0.0
    return min(1.0, value / saturation_point)


def compute_padiscore(stats: MemberStats) -> float:
    streak_component = _saturating(stats.on_time_streak, STREAK_SATURATION)

    delay_component = max(0.0, 1.0 - (stats.average_delay_hours / MAX_REASONABLE_DELAY_HOURS))

    # Missed payments hurt hard and fast: 3 missed payments effectively zeroes this factor.
    missed_component = max(0.0, 1.0 - (stats.missed_payment_count / 3.0))

    tenure_component = _saturating(stats.completed_cycles_all_pots, TENURE_SATURATION)

    total_defaults = stats.unresolved_defaults + stats.resolved_defaults
    if total_defaults == 0:
        recovery_component = 1.0  # no default history at all — full marks, nothing to recover from
    else:
        recovery_component = stats.resolved_defaults / total_defaults

    score = 100.0 * (
        WEIGHT_STREAK * streak_component
        + WEIGHT_DELAY * delay_component
        + WEIGHT_MISSED * missed_component
        + WEIGHT_TENURE * tenure_component
        + WEIGHT_RECOVERY * recovery_component
    )
    return round(max(0.0, min(100.0, score)), 2)


def gather_member_stats(db: Session, member_id: int) -> MemberStats:
    """Pulls the raw ledger facts needed by compute_padiscore for one member."""
    contributions = (
        db.query(Contribution)
        .filter(Contribution.member_id == member_id)
        .join(Cycle, Contribution.cycle_id == Cycle.id)
        .order_by(Cycle.round_no.asc())
        .all()
    )

    on_time_streak = 0
    delays = []
    missed = 0
    for c in contributions:
        cycle = db.get(Cycle, c.cycle_id)
        delay_hours = max(0.0, (c.funded_at - cycle.opens_at).total_seconds() / 3600.0)
        delays.append(delay_hours)
        if c.funded_at <= cycle.deadline:
            on_time_streak += 1
        else:
            on_time_streak = 0  # a late payment resets the streak

    average_delay = sum(delays) / len(delays) if delays else 0.0

    all_cycles_for_member = (
        db.query(Cycle)
        .join(Slot, Slot.pot_id == Cycle.pot_id)
        .filter(Slot.member_id == member_id, Cycle.deadline < datetime.utcnow())
        .all()
    )
    contributed_cycle_ids = {c.cycle_id for c in contributions}
    missed = sum(1 for cyc in all_cycles_for_member if cyc.id not in contributed_cycle_ids)

    completed_cycles_all_pots = (
        db.query(Slot).filter(Slot.member_id == member_id, Slot.has_collected.is_(True)).count()
    )

    unresolved = db.query(RegistryEntry).filter_by(member_id=member_id, cleared_at=None).count()
    resolved = db.query(RegistryEntry).filter(RegistryEntry.member_id == member_id, RegistryEntry.cleared_at.isnot(None)).count()

    return MemberStats(
        on_time_streak=on_time_streak,
        average_delay_hours=average_delay,
        missed_payment_count=missed,
        completed_cycles_all_pots=completed_cycles_all_pots,
        unresolved_defaults=unresolved,
        resolved_defaults=resolved,
    )


def record_funding_event(db: Session, *, member_id: int, cycle: Cycle, funded_at: datetime) -> float:
    """Called by the reconciler right after recording a contribution. Recomputes and stores PadiScore."""
    stats = gather_member_stats(db, member_id)
    new_score = compute_padiscore(stats)
    member = db.get(Member, member_id)
    member.padiscore = new_score
    db.flush()
    return new_score


def refresh_padiscore(db: Session, member_id: int) -> float:
    """Public entry point to recompute a member's score on demand (e.g. for /myrecord)."""
    stats = gather_member_stats(db, member_id)
    score = compute_padiscore(stats)
    member = db.get(Member, member_id)
    member.padiscore = score
    db.flush()
    return score
