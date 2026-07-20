"""
Core data model. Every module (engine, guard, channels) reads and writes
through these tables — this file is the shared contract of the whole codebase.
"""
import enum
from datetime import datetime

from sqlalchemy import (
    String, Integer, Numeric, DateTime, ForeignKey, Boolean, Enum, UniqueConstraint, Text
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Channel(str, enum.Enum):
    WHATSAPP = "WHATSAPP"
    USSD = "USSD"
    SMS = "SMS"


class Language(str, enum.Enum):
    EN = "en"
    PCM = "pcm"  # Nigerian Pidgin


class CycleState(str, enum.Enum):
    OPEN = "OPEN"
    FUNDED = "FUNDED"
    DISBURSING = "DISBURSING"
    PAID = "PAID"
    FAILED = "FAILED"


class DisbursementState(str, enum.Enum):
    PENDING = "PENDING"
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class PotStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    preferred_channel: Mapped[Channel] = mapped_column(Enum(Channel), default=Channel.WHATSAPP)
    preferred_language: Mapped[Language] = mapped_column(Enum(Language), default=Language.EN)

    bvn_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    bvn_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Destination account for payouts when this member is a round's beneficiary.
    # Collected once at onboarding (or slot-claim time) via a WhatsApp/USSD flow.
    payout_account_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payout_bank_code: Mapped[str | None] = mapped_column(String(10), nullable=True)

    padiscore: Mapped[float] = mapped_column(Numeric(5, 2), default=100.00)
    registry_flag: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    slots: Mapped[list["Slot"]] = relationship(back_populates="member")
    contributions: Mapped[list["Contribution"]] = relationship(back_populates="member")
    registry_entries: Mapped[list["RegistryEntry"]] = relationship(back_populates="member")


class Pot(Base):
    __tablename__ = "pots"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    admin_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    size: Mapped[int] = mapped_column(Integer)  # number of members/slots
    amount: Mapped[float] = mapped_column(Numeric(12, 2))  # contribution per member per cycle
    cadence_days: Mapped[int] = mapped_column(Integer, default=7)
    language: Mapped[Language] = mapped_column(Enum(Language), default=Language.EN)
    status: Mapped[PotStatus] = mapped_column(Enum(PotStatus), default=PotStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    slots: Mapped[list["Slot"]] = relationship(back_populates="pot", order_by="Slot.position")
    cycles: Mapped[list["Cycle"]] = relationship(back_populates="pot")


class Slot(Base):
    """
    A member's seat in a pot's rotation. `position` is mutable — this is where
    Earned Rotation lives: new members start high (late), and the rotation
    engine reorders positions as members build history.
    """
    __tablename__ = "slots"
    __table_args__ = (UniqueConstraint("pot_id", "member_id", name="uq_slot_pot_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pot_id: Mapped[int] = mapped_column(ForeignKey("pots.id"))
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    position: Mapped[int] = mapped_column(Integer)  # 0 = next to collect
    earned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    has_collected: Mapped[bool] = mapped_column(Boolean, default=False)

    pot: Mapped["Pot"] = relationship(back_populates="slots")
    member: Mapped["Member"] = relationship(back_populates="slots")


class Cycle(Base):
    __tablename__ = "cycles"

    id: Mapped[int] = mapped_column(primary_key=True)
    pot_id: Mapped[int] = mapped_column(ForeignKey("pots.id"))
    round_no: Mapped[int] = mapped_column(Integer)
    beneficiary_slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id"))

    opens_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deadline: Mapped[datetime] = mapped_column(DateTime)
    state: Mapped[CycleState] = mapped_column(Enum(CycleState), default=CycleState.OPEN)

    # version column enables optimistic locking on top of the row lock in state_machine.py
    version: Mapped[int] = mapped_column(Integer, default=0)

    pot: Mapped["Pot"] = relationship(back_populates="cycles")
    contributions: Mapped[list["Contribution"]] = relationship(back_populates="cycle")
    disbursement: Mapped["Disbursement"] = relationship(back_populates="cycle", uselist=False)


class ReservedAccount(Base):
    """One Monnify reserved account per member per pot (funds are pot-scoped)."""
    __tablename__ = "reserved_accounts"
    __table_args__ = (UniqueConstraint("pot_id", "member_id", name="uq_account_pot_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pot_id: Mapped[int] = mapped_column(ForeignKey("pots.id"))
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    account_reference: Mapped[str] = mapped_column(String(80), unique=True)
    account_number: Mapped[str] = mapped_column(String(20))
    bank_name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Contribution(Base):
    __tablename__ = "contributions"

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("cycles.id"))
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    monnify_tx_ref: Mapped[str] = mapped_column(String(120), unique=True)  # idempotency key
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    funded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    source: Mapped[str] = mapped_column(String(20), default="WEBHOOK")  # WEBHOOK | SWEEP

    cycle: Mapped["Cycle"] = relationship(back_populates="contributions")
    member: Mapped["Member"] = relationship(back_populates="contributions")


class Disbursement(Base):
    __tablename__ = "disbursements"

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("cycles.id"), unique=True)
    recipient_member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    monnify_ref: Mapped[str] = mapped_column(String(120), unique=True)  # idempotency key
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    preflight_result: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[DisbursementState] = mapped_column(Enum(DisbursementState), default=DisbursementState.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    cycle: Mapped["Cycle"] = relationship(back_populates="disbursement")


class RegistryEntry(Base):
    """A logged default. Presence of an uncleared entry gates future pot joins."""
    __tablename__ = "registry_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    pot_id: Mapped[int] = mapped_column(ForeignKey("pots.id"))
    amount_outstanding: Mapped[float] = mapped_column(Numeric(12, 2))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    member: Mapped["Member"] = relationship(back_populates="registry_entries")


class GuardLog(Base):
    """Append-only audit trail for MonniGuard actions — sweep catches, pre-flight blocks, etc."""
    __tablename__ = "guard_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(40))  # SWEEP_CATCH | PREFLIGHT_BLOCK | DUPLICATE_IGNORED
    reference: Mapped[str] = mapped_column(String(120))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
