"""
Background jobs:
  - guard sweep: every GUARD_SWEEP_INTERVAL_SECONDS, checks all OPEN cycles
    against Monnify directly, in case a webhook never arrived. This is the
    job behind the demo's "kill a webhook, watch it self-heal" moment.
  - reminders: every REMINDER_CHECK_INTERVAL_SECONDS, nudges members who
    haven't funded their account as a cycle's deadline approaches.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.db import session_scope
from app.models import Cycle, CycleState, ReservedAccount, Contribution, Pot
from app.guard.sweep import sweep_open_cycles
from app.guard.ports import OpenCycleRef
from app.engine.reconciler import record_sweep_contribution, _SqlLedgerAdapter  # noqa: F401 (adapter reused)
from app.engine.payout import run_payout_for_cycle
from app.monnify.client import monnify_client

logger = logging.getLogger("padipot.scheduler")


class _MonnifyQueryAdapter:
    async def get_reserved_account_transactions(self, account_reference: str) -> list[dict]:
        return await monnify_client.get_reserved_account_transactions(account_reference)

    async def validate_bank_account(self, account_number: str, bank_code: str) -> dict:
        return await monnify_client.validate_bank_account(account_number, bank_code)


async def run_guard_sweep() -> None:
    with session_scope() as db:
        open_cycles = db.query(Cycle).filter_by(state=CycleState.OPEN).all()

        refs: list[OpenCycleRef] = []
        for cycle in open_cycles:
            accounts = db.query(ReservedAccount).filter_by(pot_id=cycle.pot_id).all()
            pot = db.get(Pot, cycle.pot_id)
            for account in accounts:
                already_paid = (
                    db.query(Contribution)
                    .filter_by(cycle_id=cycle.id, member_id=account.member_id)
                    .first()
                )
                if already_paid:
                    continue
                refs.append(
                    OpenCycleRef(
                        cycle_id=cycle.id,
                        account_reference=account.account_reference,
                        member_id=account.member_id,
                        expected_amount=float(pot.amount),
                    )
                )

        from app.engine.reconciler import _SqlLedgerAdapter as LedgerAdapter
        ledger = LedgerAdapter(db)
        found = await sweep_open_cycles(refs, ledger, _MonnifyQueryAdapter())

        newly_funded_cycle_ids: set[int] = set()
        for item in found:
            account = db.query(ReservedAccount).filter_by(
                member_id=item.member_id
            ).join(Cycle, Cycle.pot_id == ReservedAccount.pot_id).filter(Cycle.id == item.cycle_id).first()
            if account:
                contribution = await record_sweep_contribution(
                    db,
                    account_reference=account.account_reference,
                    monnify_tx_ref=item.monnify_tx_ref,
                    amount=item.amount,
                )
                if contribution is not None:
                    newly_funded_cycle_ids.add(item.cycle_id)

        if found:
            logger.info("Guard sweep recovered %d missing contribution(s)", len(found))

        # Same behavior as the webhook path (app/monnify/router.py): a
        # contribution that completes a round should trigger a payout
        # attempt immediately, regardless of whether the webhook or the
        # sweep was what caught it. Wallet-balance placeholder matches the
        # webhook path's current TODO (wire up the real balance endpoint).
        for cycle_id in newly_funded_cycle_ids:
            cycle = db.get(Cycle, cycle_id)
            if cycle is not None and cycle.state == CycleState.FUNDED:
                result = await run_payout_for_cycle(db, cycle_id, wallet_balance=float("inf"))
                logger.info("Sweep-triggered payout for cycle %d: %s", cycle_id, result)


async def run_reminder_check() -> None:
    """Placeholder hook for deadline-approaching nudges — wire into WhatsApp/SMS senders."""
    logger.debug("Reminder check tick — extend with actual due-soon queries as needed")


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_guard_sweep,
        "interval",
        seconds=settings.guard_sweep_interval_seconds,
        id="guard_sweep",
    )
    scheduler.add_job(
        run_reminder_check,
        "interval",
        seconds=settings.reminder_check_interval_seconds,
        id="reminder_check",
    )
    scheduler.start()
    return scheduler
