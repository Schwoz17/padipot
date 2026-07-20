"""
Monnify webhook signature verification.

Monnify signs every webhook with header `monnify-signature`, computed as
HMAC-SHA512(raw_request_body, client_secret_key) — hex digest, over the
EXACT raw bytes Monnify sent (not a re-serialized/re-ordered version of the
JSON), or the hash will never match.
"""
import hashlib
import hmac

from app.config import settings


def verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    if not signature_header:
        return False
    expected = hmac.new(
        settings.monnify_secret_key.encode(), raw_body, hashlib.sha512
    ).hexdigest()
    # constant-time compare to avoid timing attacks
    return hmac.compare_digest(expected, signature_header)


def extract_event_type(payload: dict) -> str:
    """
    Monnify webhook payloads carry the event under `eventType` at the top
    level (e.g. SUCCESSFUL_TRANSACTION, DISBURSEMENT_SUCCESSFUL,
    REVERSED_TRANSACTION). Falls back to "UNKNOWN" defensively.
    """
    return payload.get("eventType", "UNKNOWN")


def extract_transaction_reference(payload: dict) -> str | None:
    """Pulls the Monnify transaction reference out of a collection webhook's eventData."""
    return payload.get("eventData", {}).get("transactionReference")


def extract_account_reference(payload: dict) -> str | None:
    """
    For a SUCCESSFUL_TRANSACTION on a reserved account, Monnify nests the
    account reference under eventData.product.reference.
    """
    return payload.get("eventData", {}).get("product", {}).get("reference")


def extract_amount_paid(payload: dict) -> float | None:
    amount = payload.get("eventData", {}).get("amountPaid")
    return float(amount) if amount is not None else None


def extract_disbursement_reference(payload: dict) -> str | None:
    """For DISBURSEMENT_SUCCESSFUL / FAILED webhooks."""
    return payload.get("eventData", {}).get("reference")


def extract_disbursement_status(payload: dict) -> str | None:
    return payload.get("eventData", {}).get("status")
