from app.guard import idempotency


class FakeLedger:
    """In-memory stand-in for the LedgerPort protocol — no DB needed for these tests."""

    def __init__(self):
        self.contributions: set[str] = set()
        self.disbursements: set[str] = set()
        self.events: list[tuple[str, str, str]] = []

    def has_contribution(self, monnify_tx_ref: str) -> bool:
        return monnify_tx_ref in self.contributions

    def has_disbursement(self, monnify_ref: str) -> bool:
        return monnify_ref in self.disbursements

    def log_guard_event(self, event_type: str, reference: str, detail: str) -> None:
        self.events.append((event_type, reference, detail))


def test_first_contribution_is_not_a_duplicate():
    ledger = FakeLedger()
    assert idempotency.guard_duplicate_contribution(ledger, "MNFY-TX-001") is False
    assert ledger.events == []


def test_repeated_webhook_is_flagged_and_logged():
    ledger = FakeLedger()
    ledger.contributions.add("MNFY-TX-001")

    is_dup = idempotency.guard_duplicate_contribution(ledger, "MNFY-TX-001")

    assert is_dup is True
    assert len(ledger.events) == 1
    assert ledger.events[0][0] == "DUPLICATE_IGNORED"
    assert ledger.events[0][1] == "MNFY-TX-001"


def test_disbursement_reference_is_deterministic_per_cycle():
    ref_a = idempotency.deterministic_disbursement_reference(cycle_id=42)
    ref_b = idempotency.deterministic_disbursement_reference(cycle_id=42)
    ref_other = idempotency.deterministic_disbursement_reference(cycle_id=43)

    assert ref_a == ref_b  # same cycle -> same reference, even across separate calls/retries
    assert ref_a != ref_other


def test_duplicate_disbursement_attempt_is_blocked():
    ledger = FakeLedger()
    reference = idempotency.deterministic_disbursement_reference(cycle_id=7)
    ledger.disbursements.add(reference)

    assert idempotency.guard_duplicate_disbursement(ledger, reference) is True
    assert ledger.events[-1][0] == "DUPLICATE_IGNORED"


def test_fresh_disbursement_reference_is_not_blocked():
    ledger = FakeLedger()
    reference = idempotency.deterministic_disbursement_reference(cycle_id=99)
    assert idempotency.guard_duplicate_disbursement(ledger, reference) is False
