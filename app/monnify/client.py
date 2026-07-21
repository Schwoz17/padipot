"""
Monnify API client.

Endpoints verified against current Monnify/Confluence developer docs
(sandbox base https://sandbox.monnify.com):

  POST /api/v1/auth/login                              -> access token (Basic apiKey:secretKey)
  POST /api/v2/bank-transfer/reserved-accounts          -> create reserved account
  GET  /api/v2/bank-transfer/reserved-accounts/{ref}    -> get reserved account details
  GET  /api/v1/disbursements/account/validate           -> name enquiry (account+bank -> name)
  POST /api/v2/disbursements/single                     -> initiate a payout
  GET  /api/v2/disbursements/single/summary?reference=  -> payout status
  GET  /api/v2/transactions/{transactionReference}      -> query a collection transaction
  GET  /api/v1/bank-transfer/reserved-accounts/transactions -> list payments to a reserved account

BVN match ("Identity Verification") is priced at NGN10/check on Monnify's own
pricing page but the exact current path varies by docs revision — confirm the
live path in the Monnify dashboard's API reference before going live; a
placeholder method is provided below (`verify_bvn`) that documents the
request/response shape so swapping in the confirmed path is a one-line change.

All amounts are Decimal-safe floats in NGN. All mutating requests carry a
caller-supplied unique reference; Monnify de-dupes on that reference, and our
own MonniGuard idempotency layer (app/guard/idempotency.py) de-dupes again on
our side — belt and suspenders.
"""
from __future__ import annotations

import logging
import base64
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
logger = logging.getLogger("padipot.monnify")

class MonnifyError(Exception):
    """Raised for any non-successful Monnify API response."""
    def __init__(self, message: str, response_body: dict | None = None):
        super().__init__(message)
        self.response_body = response_body or {}


@dataclass
class ReservedAccountResult:
    account_reference: str
    account_number: str
    bank_name: str
    account_name: str
    raw: dict


@dataclass
class DisbursementResult:
    reference: str
    status: str  # SUCCESS | PENDING | PENDING_AUTHORIZATION | FAILED
    raw: dict


class MonnifyClient:
    """
    Thin async wrapper. One instance per process; token is cached and
    refreshed automatically (Monnify tokens last ~1 hour).
    """

    def __init__(self):
        self._base_url = settings.monnify_base_url.rstrip("/")
        self._api_key = settings.monnify_api_key
        self._secret_key = settings.monnify_secret_key
        self._contract_code = settings.monnify_contract_code
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # ---------------------------------------------------------------- auth
    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        basic = base64.b64encode(f"{self._api_key}:{self._secret_key}".encode()).decode()
        async with httpx.AsyncClient(base_url=self._base_url, timeout=15) as client:
            resp = await client.post("/api/v1/auth/login", headers={"Authorization": f"Basic {basic}"})
        body = resp.json()
        if not body.get("requestSuccessful"):
            raise MonnifyError("Monnify authentication failed", body)

        self._token = body["responseBody"]["accessToken"]
        # expiresIn is in seconds; Monnify tokens are typically valid ~1 hour
        self._token_expires_at = time.time() + body["responseBody"].get("expiresIn", 3600)
        return self._token

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        token = await self._get_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(base_url=self._base_url, timeout=30) as client:
            resp = await client.request(method, path, headers=headers, **kwargs)
        body = resp.json()
        if not body.get("requestSuccessful"):
            logger.error(
                "Monnify request failed: %s %s | responseCode=%s | responseMessage=%s | full body=%s",
                method, path, body.get("responseCode"), body.get("responseMessage"), body,
            )
            raise MonnifyError(f"Monnify request failed: {method} {path}", body)
        return body["responseBody"]
      
    # ---------------------------------------------------------- reserved accounts
    def _parse_reserved_account_response(self, body: dict, fallback_account_name: str) -> ReservedAccountResult:
        accounts = body.get("accounts", [])
        primary = accounts[0] if accounts else {}
        return ReservedAccountResult(
            account_reference=body["accountReference"],
            account_number=primary.get("accountNumber", ""),
            bank_name=primary.get("bankName", ""),
            account_name=body.get("accountName", fallback_account_name),
            raw=body,
        )

    async def create_reserved_account(
        self,
        *,
        account_reference: str,
        account_name: str,
        customer_email: str,
        customer_name: str,
        bvn: str | None = None,
        get_all_available_banks: bool = True,
    ) -> ReservedAccountResult:
        """
        Creates a dedicated virtual account for one member's contributions to
        one pot. account_reference must be globally unique — we use
        f"padipot-{pot_id}-{member_id}".
        """
        payload: dict[str, Any] = {
            "accountReference": account_reference,
            "accountName": account_name,
            "currencyCode": "NGN",
            "contractCode": self._contract_code,
            "customerEmail": customer_email,
            "customerName": customer_name,
            "getAllAvailableBanks": get_all_available_banks,
        }
        if bvn:
            payload["bvn"] = bvn

        body = await self._request("POST", "/api/v2/bank-transfer/reserved-accounts", json=payload)
        return self._parse_reserved_account_response(body, account_name)

    async def get_or_create_reserved_account(
        self,
        *,
        account_reference: str,
        account_name: str,
        customer_email: str,
        customer_name: str,
        bvn: str | None = None,
        get_all_available_banks: bool = True,
    ) -> ReservedAccountResult:
        """
        Idempotent version of create_reserved_account: if Monnify reports
        that this reference already exists (responseCode '99', "cannot
        reserve an account with the same reference"), fetches and returns
        the existing account instead of raising.

        This matters in practice whenever local state and Monnify's state
        can drift apart — most commonly during demo rehearsals, where a
        local dev database gets wiped and reseeded (see scripts/demo_seed.py)
        but Monnify's server-side account references persist forever. Same
        idempotency principle as the rest of MonniGuard, applied here too.
        """
        try:
            return await self.create_reserved_account(
                account_reference=account_reference,
                account_name=account_name,
                customer_email=customer_email,
                customer_name=customer_name,
                bvn=bvn,
                get_all_available_banks=get_all_available_banks,
            )
        except MonnifyError as exc:
            is_duplicate = exc.response_body.get("responseCode") == "99" or "same reference" in str(exc).lower() or "same reference" in exc.response_body.get("responseMessage", "").lower()
            if not is_duplicate:
                raise
            existing = await self.get_reserved_account(account_reference)
            return self._parse_reserved_account_response(existing, account_name)

    async def get_reserved_account(self, account_reference: str) -> dict:
        return await self._request("GET", f"/api/v2/bank-transfer/reserved-accounts/{account_reference}")

    # ---------------------------------------------------------------- verification
    async def validate_bank_account(self, account_number: str, bank_code: str) -> dict:
        """Name enquiry: confirms the account number/bank code pair and returns the account name."""
        return await self._request(
            "GET",
            "/api/v1/disbursements/account/validate",
            params={"accountNumber": account_number, "bankCode": bank_code},
        )

    async def verify_bvn(self, bvn: str, name: str, date_of_birth: str, mobile_no: str) -> dict:
        """
        BVN identity match, priced ~NGN10/check on Monnify (free on Sandbox test BVNs).
        Confirm the exact path against the current Monnify API reference before
        go-live — this call is isolated behind this one method for that reason.
        """
        payload = {"bvn": bvn, "name": name, "dateOfBirth": date_of_birth, "mobileNo": mobile_no}
        return await self._request("POST", "/api/v1/vas/bvn-details-match", json=payload)

    # ---------------------------------------------------------------- disbursements
    async def disburse(
        self,
        *,
        reference: str,
        amount: float,
        destination_account_number: str,
        destination_bank_code: str,
        destination_account_name: str,
        narration: str,
    ) -> DisbursementResult:
        """
        Initiates a payout. `reference` is our idempotency key — Monnify
        rejects/echoes duplicate references rather than double-paying, and we
        also check our own Disbursement table before ever calling this
        (see app/guard/idempotency.py).
        """
        payload = {
            "amount": amount,
            "reference": reference,
            "narration": narration,
            "destinationBankCode": destination_bank_code,
            "destinationAccountNumber": destination_account_number,
            "destinationAccountName": destination_account_name,
            "currency": "NGN",
            "sourceAccountNumber": settings.monnify_source_account_number,
            "async": True,
        }
        body = await self._request("POST", "/api/v2/disbursements/single", json=payload)
        return DisbursementResult(reference=body.get("reference", reference), status=body.get("status", "PENDING"), raw=body)

    async def get_disbursement_status(self, reference: str) -> dict:
        return await self._request("GET", "/api/v2/disbursements/single/summary", params={"reference": reference})

    # ---------------------------------------------------------------- transactions (reconciliation)
    async def get_transaction(self, transaction_reference: str) -> dict:
        """Looks up a single, already-known transaction by its Monnify transactionReference."""
        return await self._request("GET", f"/api/v2/transactions/{transaction_reference}")

    async def get_reserved_account_transactions(
        self, account_reference: str, page: int = 0, size: int = 10
    ) -> list[dict]:
        """
        Lists payments received on a reserved account — this is what the
        MonniGuard sweep actually needs (it doesn't know a transaction
        reference in advance; it's asking "has ANYTHING landed on this
        account?"). Distinct from get_transaction(), which looks up one
        already-known transaction reference.
        """
        body = await self._request(
            "GET",
            "/api/v1/bank-transfer/reserved-accounts/transactions",
            params={"accountReference": account_reference, "page": page, "size": size},
        )
        return body.get("content", [])

    # ---------------------------------------------------------------- reference data
    async def get_banks(self) -> list[dict]:
        """
        Returns Monnify's authoritative list of supported banks and their
        codes — use this instead of hardcoding bank codes from third-party
        sources, which frequently disagree with each other.
        """
        return await self._request("GET", "/api/v1/banks")


# Module-level singleton — import this everywhere rather than constructing MonnifyClient() repeatedly,
# so the cached auth token is actually shared.
monnify_client = MonnifyClient()
