"""
Transport-agnostic WhatsApp command dispatch.

Both the Meta Cloud API webhook (webhook.py) and the Twilio webhook
(twilio_webhook.py) parse their own inbound payload shape, then call into
this module with just (db, phone, text, display_name). Neither transport
duplicates member lookup, pot resolution, or command routing — that logic
lives here exactly once.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Member, Pot, Slot
from app.channels.whatsapp import flows


def resolve_active_pot(db: Session, member: Member) -> Pot | None:
    # TODO(multi-pot): if a member has multiple active pots, prompt to choose.
    # Demo scope assumes one active pot per member.
    slot = db.query(Slot).filter_by(member_id=member.id).order_by(Slot.id.desc()).first()
    if slot is None:
        return None
    return db.get(Pot, slot.pot_id)


def get_or_create_member(db: Session, phone: str, display_name: str = "") -> Member:
    member = db.query(Member).filter_by(phone=phone).first()
    if member is None:
        member = Member(phone=phone, name=display_name or phone)
        db.add(member)
        db.commit()
    return member


async def dispatch_command(db: Session, member: Member, text: str) -> str:
    stripped = text.strip()
    command = stripped.upper()

    if command == "/MYRECORD":
        return flows.handle_my_record(db, member=member)

    if command == "MY POTS":
        return flows.handle_my_pots(db, member=member)

    if command.startswith("SET NAME"):
        raw_args = stripped[len("SET NAME"):]
        return flows.handle_set_name(db, member=member, raw_args=raw_args)

    if command.startswith("SET PAYOUT"):
        raw_args = stripped[len("SET PAYOUT"):]
        return await flows.handle_set_payout(db, member=member, raw_args=raw_args)

    if command.startswith("CREATE POT"):
        raw_args = stripped[len("CREATE POT"):]
        return await flows.handle_create_pot(db, member=member, raw_args=raw_args)

    if command.startswith("START POT"):
        tokens = stripped[len("START POT"):].split()
        if len(tokens) != 1 or not tokens[0].isdigit():
            return "To start a pot, send: START POT <pot id>"
        return flows.handle_start_pot(db, member=member, pot_id=int(tokens[0]))

    if command.startswith("ADD MEMBER"):
        tokens = stripped[len("ADD MEMBER"):].strip().split(maxsplit=3)
        if len(tokens) != 4 or not tokens[0].isdigit() or not tokens[1].isdigit():
            return "To add a member, send: ADD MEMBER <pot id> <turn number> <phone number> <name>"
        pot_id, turn, phone, name = int(tokens[0]), int(tokens[1]), tokens[2], tokens[3]
        return await flows.handle_add_member(db, member=member, pot_id=pot_id, phone=phone, turn=turn, name=name)

    if command.startswith("JOIN"):
        tokens = stripped[len("JOIN"):].split()
        if len(tokens) != 2 or not all(t.isdigit() for t in tokens):
            return "To join a pot, send: JOIN <pot id> <turn number>"
        pot_id, turn = int(tokens[0]), int(tokens[1])
        pot = db.get(Pot, pot_id)
        if pot is None:
            return f"No pot found with ID {pot_id}."
        return await flows.handle_join_pot(db, member=member, pot=pot, requested_turn=turn)

    if command.startswith("LEAVE"):
        tokens = stripped[len("LEAVE"):].split()
        if len(tokens) != 1 or not tokens[0].isdigit():
            return "To leave a pot, send: LEAVE <pot id>"
        return flows.handle_leave_pot(db, member=member, pot_id=int(tokens[0]))

    pot = resolve_active_pot(db, member)
    if pot is None:
        return flows.unrecognized(member)

    handler = flows.COMMAND_TABLE.get(command)
    if handler is None:
        return flows.unrecognized(member)

    if handler is flows.handle_status:
        return handler(db, member=member, pot=pot)
    if handler is flows.handle_order:
        return handler(db, pot=pot)
    if handler is flows.handle_ledger:
        return handler(db, pot=pot)
    if handler is flows.handle_my_account:
        return handler(db, member=member, pot=pot)
    return flows.unrecognized(member)
