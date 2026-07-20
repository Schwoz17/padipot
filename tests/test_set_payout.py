from unittest.mock import AsyncMock, patch

import pytest

from app.channels.whatsapp import flows


FAKE_BANKS = [
    {"name": "Access Bank", "code": "044"},
    {"name": "Sterling bank", "code": "232"},
]


@pytest.mark.asyncio
async def test_missing_args_returns_help_text(db_session, make_member):
    member = make_member("2348000000301", "Payout Tester")
    reply = await flows.handle_set_payout(db_session, member=member, raw_args="  ")
    assert "SET PAYOUT" in reply
    assert member.payout_account_number is None


@pytest.mark.asyncio
async def test_invalid_account_number_format_is_rejected(db_session, make_member):
    member = make_member("2348000000302", "Payout Tester")
    reply = await flows.handle_set_payout(db_session, member=member, raw_args="12AB Access Bank")
    assert "valid account number" in reply
    assert member.payout_account_number is None


@pytest.mark.asyncio
async def test_unknown_bank_name_is_rejected(db_session, make_member):
    member = make_member("2348000000303", "Payout Tester")
    with patch("app.channels.whatsapp.flows.monnify_client") as mock_client:
        mock_client.get_banks = AsyncMock(return_value=FAKE_BANKS)
        reply = await flows.handle_set_payout(db_session, member=member, raw_args="0123456789 Not A Real Bank")
    assert "Couldn't find a bank" in reply
    assert member.payout_account_number is None


@pytest.mark.asyncio
async def test_successful_validation_saves_payout_details(db_session, make_member):
    member = make_member("2348000000304", "Chidinma Okafor")
    with patch("app.channels.whatsapp.flows.monnify_client") as mock_client:
        mock_client.get_banks = AsyncMock(return_value=FAKE_BANKS)
        mock_client.validate_bank_account = AsyncMock(return_value={"accountName": "CHIDINMA OKAFOR"})
        reply = await flows.handle_set_payout(db_session, member=member, raw_args="0123456789 Access Bank")

    assert member.payout_account_number == "0123456789"
    assert member.payout_bank_code == "044"
    assert "CHIDINMA OKAFOR" in reply
    assert "✅" in reply


@pytest.mark.asyncio
async def test_failed_name_enquiry_does_not_save(db_session, make_member):
    member = make_member("2348000000305", "Payout Tester")
    with patch("app.channels.whatsapp.flows.monnify_client") as mock_client:
        mock_client.get_banks = AsyncMock(return_value=FAKE_BANKS)
        mock_client.validate_bank_account = AsyncMock(side_effect=Exception("503"))
        reply = await flows.handle_set_payout(db_session, member=member, raw_args="0123456789 Access Bank")

    assert "Couldn't verify" in reply
    assert member.payout_account_number is None