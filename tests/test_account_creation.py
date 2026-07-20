from unittest.mock import AsyncMock, patch

import pytest

from app.channels.whatsapp import flows
from app.models import ReservedAccount


FAKE_RESERVED_ACCOUNT = type(
    "FakeResult", (),
    {"account_reference": "padipot-1-1", "account_number": "1234567890", "bank_name": "Wema bank"},
)()


@pytest.mark.asyncio
async def test_create_pot_gives_admin_a_reserved_account(db_session, make_member):
    admin = make_member("2340000401", "Admin")

    with patch("app.channels.whatsapp.flows.monnify_client") as mock_client:
        mock_client.get_or_create_reserved_account = AsyncMock(return_value=FAKE_RESERVED_ACCOUNT)
        reply = await flows.handle_create_pot(
            db_session, member=admin, raw_args=" Test Pot | 2 | 5000"
        )

    assert "1234567890" in reply
    account = db_session.query(ReservedAccount).filter_by(member_id=admin.id).first()
    assert account is not None
    assert account.account_number == "1234567890"


@pytest.mark.asyncio
async def test_member_with_slot_but_no_account_can_self_heal_via_join(db_session, make_member, make_pot):
    """
    Regression test for the exact bug found in production: an admin (or
    anyone auto-seated without going through JOIN) has a Slot but no
    ReservedAccount. Sending JOIN again should create the missing account
    without touching their existing turn — not silently return nothing,
    and not error out because "you're already in this pot".
    """
    admin = make_member("2340000402", "Admin")
    pot = make_pot(admin.id, size=3)  # admin auto-seated at turn 1, no account yet

    existing_account = db_session.query(ReservedAccount).filter_by(member_id=admin.id).first()
    assert existing_account is None  # confirms the bug precondition

    with patch("app.channels.whatsapp.flows.monnify_client") as mock_client:
        mock_client.get_or_create_reserved_account = AsyncMock(return_value=FAKE_RESERVED_ACCOUNT)
        reply = await flows.handle_join_pot(db_session, member=admin, pot=pot, requested_turn=1)

    assert "1234567890" in reply
    account = db_session.query(ReservedAccount).filter_by(member_id=admin.id).first()
    assert account is not None

    from app.models import Slot
    slot = db_session.query(Slot).filter_by(pot_id=pot.id, member_id=admin.id).first()
    assert slot.position == 0  # turn untouched by the self-heal


@pytest.mark.asyncio
async def test_member_with_slot_and_account_gets_short_circuit_message(db_session, make_member, make_pot):
    admin = make_member("2340000403", "Admin")
    pot = make_pot(admin.id, size=2)

    db_session.add(ReservedAccount(
        pot_id=pot.id, member_id=admin.id, account_reference="ref-1",
        account_number="9999999999", bank_name="Access Bank",
    ))
    db_session.commit()

    with patch("app.channels.whatsapp.flows.monnify_client") as mock_client:
        mock_client.get_or_create_reserved_account = AsyncMock()
        reply = await flows.handle_join_pot(db_session, member=admin, pot=pot, requested_turn=1)

    assert "already in" in reply
    assert "9999999999" in reply
    mock_client.get_or_create_reserved_account.assert_not_called()  # no redundant Monnify callfrom unittest.mock import AsyncMock, patch

import pytest

from app.channels.whatsapp import flows
from app.models import ReservedAccount


FAKE_RESERVED_ACCOUNT = type(
    "FakeResult", (),
    {"account_reference": "padipot-1-1", "account_number": "1234567890", "bank_name": "Wema bank"},
)()


@pytest.mark.asyncio
async def test_create_pot_gives_admin_a_reserved_account(db_session, make_member):
    admin = make_member("2340000401", "Admin")

    with patch("app.channels.whatsapp.flows.monnify_client") as mock_client:
        mock_client.get_or_create_reserved_account = AsyncMock(return_value=FAKE_RESERVED_ACCOUNT)
        reply = await flows.handle_create_pot(
            db_session, member=admin, raw_args=" Test Pot | 2 | 5000"
        )

    assert "1234567890" in reply
    account = db_session.query(ReservedAccount).filter_by(member_id=admin.id).first()
    assert account is not None
    assert account.account_number == "1234567890"


@pytest.mark.asyncio
async def test_member_with_slot_but_no_account_can_self_heal_via_join(db_session, make_member, make_pot):
    """
    Regression test for the exact bug found in production: an admin (or
    anyone auto-seated without going through JOIN) has a Slot but no
    ReservedAccount. Sending JOIN again should create the missing account
    without touching their existing turn — not silently return nothing,
    and not error out because "you're already in this pot".
    """
    admin = make_member("2340000402", "Admin")
    pot = make_pot(admin.id, size=3)  # admin auto-seated at turn 1, no account yet

    existing_account = db_session.query(ReservedAccount).filter_by(member_id=admin.id).first()
    assert existing_account is None  # confirms the bug precondition

    with patch("app.channels.whatsapp.flows.monnify_client") as mock_client:
        mock_client.get_or_create_reserved_account = AsyncMock(return_value=FAKE_RESERVED_ACCOUNT)
        reply = await flows.handle_join_pot(db_session, member=admin, pot=pot, requested_turn=1)

    assert "1234567890" in reply
    account = db_session.query(ReservedAccount).filter_by(member_id=admin.id).first()
    assert account is not None

    from app.models import Slot
    slot = db_session.query(Slot).filter_by(pot_id=pot.id, member_id=admin.id).first()
    assert slot.position == 0  # turn untouched by the self-heal


@pytest.mark.asyncio
async def test_member_with_slot_and_account_gets_short_circuit_message(db_session, make_member, make_pot):
    admin = make_member("2340000403", "Admin")
    pot = make_pot(admin.id, size=2)

    db_session.add(ReservedAccount(
        pot_id=pot.id, member_id=admin.id, account_reference="ref-1",
        account_number="9999999999", bank_name="Access Bank",
    ))
    db_session.commit()

    with patch("app.channels.whatsapp.flows.monnify_client") as mock_client:
        mock_client.get_or_create_reserved_account = AsyncMock()
        reply = await flows.handle_join_pot(db_session, member=admin, pot=pot, requested_turn=1)

    assert "already in" in reply
    assert "9999999999" in reply
    mock_client.get_or_create_reserved_account.assert_not_called()  # no redundant Monnify call
