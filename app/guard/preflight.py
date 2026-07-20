"""
Disbursement pre-flight.

Before any payout leaves the wallet, three cheap checks run. Any failure
defers the payout (member gets a "we're on it" notification) instead of a
silent error or, worse, a payout to the wrong account.
"""
from __future__ import annotations

from app.guard.ports import MonnifyQueryPort, PreflightResult

# Sandbox destination that Monnify's own docs say will simulate a failed
# disbursement — used in the demo to show pre-flight/failure handling live.
SANDBOX_FORCED_FAILURE_ACCOUNT = "0035785417"
SANDBOX_FORCED_FAILURE_BANK_CODE = "044"

# Banks flagged as currently unhealthy (populated by ops / a status feed in
# production; empty by default). Pre-flight defers payouts to these banks.
UNHEALTHY_BANK_CODES: set[str] = set()


async def run_preflight(
    *,
    wallet_balance: float,
    amount: float,
    destination_account_number: str,
    destination_bank_code: str,
    expected_account_name: str,
    monnify: MonnifyQueryPort,
) -> PreflightResult:
    reasons: list[str] = []

    # 1. Balance check
    if wallet_balance < amount:
        reasons.append(f"Insufficient wallet balance: have {wallet_balance}, need {amount}")

    # 2. Bank health check
    if destination_bank_code in UNHEALTHY_BANK_CODES:
        reasons.append(f"Destination bank {destination_bank_code} flagged unhealthy — deferring")

    # 3. Recipient account validation (name enquiry)
    if (
        destination_account_number == SANDBOX_FORCED_FAILURE_ACCOUNT
        and destination_bank_code == SANDBOX_FORCED_FAILURE_BANK_CODE
    ):
        reasons.append("Sandbox forced-failure destination — simulated validation failure")
    else:
        try:
            validation = await monnify.validate_bank_account(destination_account_number, destination_bank_code)
            returned_name = (validation.get("accountName") or "").strip().lower()
            if expected_account_name.strip().lower() not in returned_name and returned_name not in expected_account_name.strip().lower():
                reasons.append(
                    f"Account name mismatch: expected '{expected_account_name}', bank returned '{returned_name or 'unknown'}'"
                )
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"Name enquiry failed: {type(exc).__name__}: {exc}")

    return PreflightResult(passed=len(reasons) == 0, reasons=reasons)
