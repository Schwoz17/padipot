"""
Reconciliation sweep.

Webhooks get lost — a server restart at the wrong second, a network blip
between Monnify and our endpoint, a 500 we returned by mistake. The sweep
is the safety net: it periodically asks Monnify directly "did this account
actually get paid?" for every cycle still OPEN, and returns anything our
webhook-driven ledger is missing.

This module does not touch the database. It takes the open cycles as plain
data (OpenCycleRef), queries Monnify through the injected port, and returns
what it found (SweepFound) for the caller (engine/reconciler.py) to record.
That separation is what keeps this file honest scaffolding for the demo's
"kill a webhook, watch it self-heal" moment — the sweep logic itself never
changes based on how PadiPot chooses to store the result.
"""
from __future__ import annotations

from app.guard.ports import LedgerPort, MonnifyQueryPort, OpenCycleRef, SweepFound


async def sweep_open_cycles(
    open_cycles: list[OpenCycleRef],
    ledger: LedgerPort,
    monnify: MonnifyQueryPort,
) -> list[SweepFound]:
    """
    For each open cycle, list Monnify's payment history for that member's
    reserved account. If Monnify shows a payment our ledger has no matching
    Contribution for, surface it as a SweepFound and log the catch.
    """
    found: list[SweepFound] = []

    for cycle_ref in open_cycles:
        try:
            transactions = await monnify.get_reserved_account_transactions(cycle_ref.account_reference)
        except Exception as exc:  # noqa: BLE001 — sweep must never crash the scheduler
            ledger.log_guard_event(
                "SWEEP_QUERY_ERROR", cycle_ref.account_reference, f"{type(exc).__name__}: {exc}"
            )
            continue

        for tx in transactions:
            monnify_tx_ref = tx.get("transactionReference")
            if not monnify_tx_ref or ledger.has_contribution(monnify_tx_ref):
                continue  # already recorded (normal path) or malformed entry — nothing to heal

            amount_paid = float(tx.get("amountPaid", tx.get("amount", cycle_ref.expected_amount)))
            found.append(
                SweepFound(
                    cycle_id=cycle_ref.cycle_id,
                    member_id=cycle_ref.member_id,
                    monnify_tx_ref=monnify_tx_ref,
                    amount=amount_paid,
                )
            )
            ledger.log_guard_event(
                "SWEEP_CATCH",
                monnify_tx_ref,
                f"Sweep recovered missing webhook for cycle {cycle_ref.cycle_id}, "
                f"member {cycle_ref.member_id}, amount {amount_paid}",
            )

    return found