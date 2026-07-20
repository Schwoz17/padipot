"""
Padi Record — the portable savings statement behind the /myrecord command.

This is intentionally plain data (a dataclass) with a render_* function per
channel, rather than one hardcoded WhatsApp-shaped string — USSD/SMS need a
shorter render than WhatsApp, and the future "shareable to a lender" version
needs structured JSON, not prose. One data source, three renderers.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Member, Pot, Slot
from app.engine import padiscore, registry


@dataclass
class PadiRecord:
    member_name: str
    padiscore: float
    pots_joined: int
    cycles_completed: int
    current_streak: int
    has_unresolved_default: bool
    unresolved_default_amount: float


def build_padi_record(db: Session, member_id: int) -> PadiRecord:
    member = db.get(Member, member_id)

    pots_joined = db.query(Slot).filter_by(member_id=member_id).count()
    cycles_completed = db.query(Slot).filter_by(member_id=member_id, has_collected=True).count()

    stats = padiscore.gather_member_stats(db, member_id)
    unresolved = registry.unresolved_entries(db, member_id)
    unresolved_amount = sum(float(e.amount_outstanding) for e in unresolved)

    return PadiRecord(
        member_name=member.name,
        padiscore=float(member.padiscore),
        pots_joined=pots_joined,
        cycles_completed=cycles_completed,
        current_streak=stats.on_time_streak,
        has_unresolved_default=len(unresolved) > 0,
        unresolved_default_amount=unresolved_amount,
    )


def render_whatsapp(record: PadiRecord, lang: str = "en") -> str:
    if lang == "pcm":
        lines = [
            f"*Padi Record for {record.member_name}*",
            f"PadiScore: {record.padiscore:.0f}/100",
            f"Pots wey you don join: {record.pots_joined}",
            f"Rounds wey you don complete: {record.cycles_completed}",
            f"Current streak: {record.current_streak} cycle(s) wey you pay on time",
        ]
        if record.has_unresolved_default:
            lines.append(f"⚠️ Outstanding wahala: NGN{record.unresolved_default_amount:,.2f} you still owe")
        else:
            lines.append("✅ No outstanding default. Clean record.")
        return "\n".join(lines)

    lines = [
        f"*Padi Record — {record.member_name}*",
        f"PadiScore: {record.padiscore:.0f}/100",
        f"Pots joined: {record.pots_joined}",
        f"Cycles completed: {record.cycles_completed}",
        f"Current on-time streak: {record.current_streak} cycle(s)",
    ]
    if record.has_unresolved_default:
        lines.append(f"⚠️ Outstanding default: NGN{record.unresolved_default_amount:,.2f}")
    else:
        lines.append("✅ No outstanding defaults. Clean record.")
    return "\n".join(lines)


def render_sms(record: PadiRecord) -> str:
    """Short form — SMS has practical length limits."""
    flag = "CLEAN" if not record.has_unresolved_default else f"DEFAULT NGN{record.unresolved_default_amount:,.0f}"
    return (
        f"PadiPot record: {record.member_name} | Score {record.padiscore:.0f}/100 "
        f"| {record.cycles_completed} cycles done | Streak {record.current_streak} | {flag}"
    )


def to_dict(record: PadiRecord) -> dict:
    """The future lender-facing shape — same source of truth as the chat renderers."""
    return {
        "member_name": record.member_name,
        "padiscore": record.padiscore,
        "pots_joined": record.pots_joined,
        "cycles_completed": record.cycles_completed,
        "current_streak": record.current_streak,
        "has_unresolved_default": record.has_unresolved_default,
        "unresolved_default_amount": record.unresolved_default_amount,
    }
