"""
MonniGuard's ports.

Design rule: nothing under app/guard imports from app/engine, app/channels,
or app/models. The guard package only knows about these small Protocols.
Callers (engine/reconciler.py, engine/payout.py) implement/adapt them.

This is what makes MonniGuard extractable later as a standalone package or
SDK (see the technical blueprint, Section 5) — swapping PadiPot's SQLAlchemy
models for another system's storage is a new adapter, not a guard rewrite.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class OpenCycleRef:
    """The minimal shape the sweep needs to know about a cycle awaiting funds."""
    cycle_id: int
    account_reference: str
    member_id: int
    expected_amount: float


@dataclass
class SweepFound:
    """A contribution the sweep found on Monnify's side that our ledger is missing."""
    cycle_id: int
    member_id: int
    monnify_tx_ref: str
    amount: float


class LedgerPort(Protocol):
    """What the guard needs to know about — and record into — PadiPot's ledger."""

    def has_contribution(self, monnify_tx_ref: str) -> bool: ...
    def has_disbursement(self, monnify_ref: str) -> bool: ...
    def log_guard_event(self, event_type: str, reference: str, detail: str) -> None: ...


class MonnifyQueryPort(Protocol):
    """What the guard needs from Monnify — transaction lookups and account validation."""

    async def get_reserved_account_transactions(self, account_reference: str) -> list[dict]: ...
    async def validate_bank_account(self, account_number: str, bank_code: str) -> dict: ...


@dataclass
class PreflightResult:
    passed: bool
    reasons: list[str]

    def summary(self) -> str:
        return "OK" if self.passed else "; ".join(self.reasons)