from unittest.mock import AsyncMock, patch

import pytest

from app.channels.whatsapp import flows
from app.models import Member, ReservedAccount, Slot


FAKE_RESERVED_ACCOUNT = type(
    "FakeResult", (),
    {"account_reference": "padipot-1-2", "account_number": "5551234567", "bank_name": "Access Bank"},
)()


@pytest.mark.asyncio
async def test_admin_can_add_a_member_who_never_messaged_the_bot(db_session, make_member, make_pot):
    admin = make_member("2340000501", "Admin")
    pot = make_pot(admin.id, size=3)

    with patch("app.channels.whatsapp.flows.monnify_client") as mock_client:
        mock_client.get_or_create_reserved_account = AsyncMock(return_value=FAKE_RESERVED_ACCOUNT)
        reply = await flows.handle_add_member(
            db_session, member=admin, pot_id=pot.id, phone="2348099998888", turn=2, name="Ngozi Okafor"
        )

    assert "Added Ngozi Okafor" in reply
    assert "5551234567" in reply
    assert "won't get WhatsApp notifications" in reply

    new_member = db_session.query(Member).filter_by(phone="2348099998888").first()
    assert new_member is not None
    assert new_member.name == "Ngozi Okafor"

    slot = db_session.query(Slot).filter_by(pot_id=pot.id, member_id=new_member.id).first()
    assert slot.position == 1  # turn 2 -> position 1

    account = db_session.query(ReservedAccount).filter_by(member_id=new_member.id).first()
    assert account is not None


@pytest.mark.asyncio
async def test_only_admin_can_add_a_member(db_session, make_member, make_pot):
    admin = make_member("2340000502", "Admin")
    pot = make_pot(admin.id, size=3)
    other = make_member("2340000503", "Other")

    reply = await flows.handle_add_member(
        db_session, member=other, pot_id=pot.id, phone="2348099998888", turn=2, name="Ngozi"
    )

    assert "Only the pot admin" in reply
    assert db_session.query(Member).filter_by(phone="2348099998888").first() is None


@pytest.mark.asyncio
async def test_cannot_add_member_to_a_started_pot(db_session, make_member, make_pot):
    from app.engine import pot_service, rotation

    admin = make_member("2340000504", "Admin")
    pot = make_pot(admin.id, size=2)
    other = make_member("2340000505", "Other")
    rotation.assign_chosen_slot(db_session, pot_id=pot.id, member_id=other.id, requested_turn=2)
    pot_service.start_pot(db_session, pot_id=pot.id, requesting_member_id=admin.id)

    reply = await flows.handle_add_member(
        db_session, member=admin, pot_id=pot.id, phone="2348099998888", turn=1, name="Late Joiner"
    )

    assert "already started" in reply


@pytest.mark.asyncio
async def test_adding_already_added_member_shows_their_existing_turn(db_session, make_member, make_pot):
    admin = make_member("2340000506", "Admin")
    pot = make_pot(admin.id, size=3)

    with patch("app.channels.whatsapp.flows.monnify_client") as mock_client:
        mock_client.get_or_create_reserved_account = AsyncMock(return_value=FAKE_RESERVED_ACCOUNT)
        await flows.handle_add_member(
            db_session, member=admin, pot_id=pot.id, phone="2348099998888", turn=2, name="Ngozi"
        )
        second_reply = await flows.handle_add_member(
            db_session, member=admin, pot_id=pot.id, phone="2348099998888", turn=3, name="Ngozi"
        )

    assert "already in" in second_reply
    assert "turn 2" in second_reply
