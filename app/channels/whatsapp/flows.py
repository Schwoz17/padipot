"""
WhatsApp bot command router and conversation flows.

Deliberately simple command matching rather than free-text NLU — ajo members
need predictable, unambiguous commands more than they need a chatty AI.
Group creation is a short guided sequence; everything else is a one-shot
command.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Member, Pot, ReservedAccount, Slot, Cycle
from app.channels import i18n
from app.engine import rotation, padirecord, registry, pot_service
from app.monnify.client import monnify_client


def _lang(member: Member) -> str:
    return member.preferred_language.value if member.preferred_language else "en"


async def _create_reserved_account_for(db: Session, *, pot: Pot, member: Member) -> ReservedAccount:
    """
    Creates (or fetches, if it already exists) the Monnify reserved account
    for one member's contributions to one pot, and saves the local record.
    Used by both CREATE POT (for the admin, who never goes through JOIN)
    and JOIN (for everyone else) — one place, so neither path can drift
    out of sync with the other again.
    """
    account_ref = f"padipot-{pot.id}-{member.id}"
    result = await monnify_client.get_or_create_reserved_account(
        account_reference=account_ref,
        account_name=f"PadiPot - {member.name}",
        customer_email=f"{account_ref}@padipot.ng",
        customer_name=member.name,
    )
    account = ReservedAccount(
        pot_id=pot.id,
        member_id=member.id,
        account_reference=result.account_reference,
        account_number=result.account_number,
        bank_name=result.bank_name,
    )
    db.add(account)
    db.commit()
    return account


async def handle_join_pot(
    db: Session, *, member: Member, pot: Pot, requested_turn: int | None = None
) -> str:
    """
    If requested_turn is given (the real WhatsApp path — see dispatcher.py's
    "JOIN <pot id> <turn>"), the member self-selects that specific turn.
    If omitted (used by scripts/demo_seed.py), falls back to auto-appending
    at the back of the queue. Either way, joining a pot that has already
    started is refused — see rotation.py's module docstring for why that
    boundary matters.
    """
    allowed, reason = registry.can_join_pot(db, member.id)
    if not allowed:
        return i18n.t("join_blocked_default", _lang(member), reason=reason)

    if pot_service.pot_has_started(db, pot.id):
        return f"'{pot.name}' has already started — membership is closed. Ask the admin about the next pot."

    already_in_slot = db.query(Slot).filter_by(pot_id=pot.id, member_id=member.id).first()
    existing_account = db.query(ReservedAccount).filter_by(pot_id=pot.id, member_id=member.id).first()

    if already_in_slot and existing_account:
        return (
            f"You're already in '{pot.name}' — turn {already_in_slot.position + 1}.\n"
            f"Your account: {existing_account.account_number} ({existing_account.bank_name})"
        )

    if not already_in_slot:
        # A genuinely new member — assign a turn first.
        if requested_turn is not None:
            try:
                rotation.assign_chosen_slot(db, pot_id=pot.id, member_id=member.id, requested_turn=requested_turn)
            except ValueError as exc:
                open_turns = rotation.available_turns(db, pot.id)
                turns_text = ", ".join(map(str, open_turns)) if open_turns else "none — pot is full"
                return f"{exc} Available turns: {turns_text}"
        else:
            rotation.assign_new_member_slot(db, pot_id=pot.id, member_id=member.id)
    # else: already has a slot (e.g. the admin, auto-seated at creation) but
    # is missing their account — fall through and just create the account,
    # without touching their existing turn.

    account = await _create_reserved_account_for(db, pot=pot, member=member)

    return i18n.t(
        "account_created",
        _lang(member),
        pot_name=pot.name,
        account_number=account.account_number,
        bank_name=account.bank_name,
        deadline=f"every {pot.cadence_days} days",
    )

def handle_status(db: Session, *, member: Member, pot: Pot) -> str:
    cycle = db.query(Cycle).filter_by(pot_id=pot.id, state="OPEN").order_by(Cycle.round_no.desc()).first()
    if cycle is None:
        return "No open round right now."

    from app.models import Contribution
    progress = db.query(Contribution).filter_by(cycle_id=cycle.id).count()
    total = pot.size - 1
    amount_in_pot = progress * float(pot.amount)

    next_slot = rotation.next_beneficiary_slot(db, pot.id)
    next_member = db.get(Member, next_slot.member_id) if next_slot else None

    return i18n.t(
        "status_reply",
        _lang(member),
        round_no=cycle.round_no,
        progress=progress,
        total=total,
        amount_in_pot=amount_in_pot,
        next_beneficiary=next_member.name if next_member else "TBD",
    )


def handle_order(db: Session, *, pot: Pot) -> str:
    slots = db.query(Slot).filter_by(pot_id=pot.id).order_by(Slot.position.asc()).all()
    lines = []
    for i, slot in enumerate(slots, start=1):
        member = db.get(Member, slot.member_id)
        tag = "✅ collected" if slot.has_collected else ""
        lines.append(f"{i}. {member.name} {tag}".strip())
    return "\n".join(lines) if lines else "No members yet."


def handle_ledger(db: Session, *, pot: Pot) -> str:
    from app.models import Contribution
    rows = (
        db.query(Contribution)
        .join(Cycle, Contribution.cycle_id == Cycle.id)
        .filter(Cycle.pot_id == pot.id)
        .order_by(Contribution.funded_at.desc())
        .limit(20)
        .all()
    )
    if not rows:
        return "No contributions recorded yet."
    lines = []
    for c in rows:
        member = db.get(Member, c.member_id)
        lines.append(f"{c.funded_at:%d %b %H:%M} · {member.name} · NGN{float(c.amount):,.0f}")
    return "\n".join(lines)


def handle_my_account(db: Session, *, member: Member, pot: Pot) -> str:
    account = db.query(ReservedAccount).filter_by(pot_id=pot.id, member_id=member.id).first()
    if account is None:
        return "You don't have an account for this pot yet — reply JOIN first."
    return i18n.t("my_account_reply", _lang(member), account_number=account.account_number, bank_name=account.bank_name)


def handle_my_record(db: Session, *, member: Member) -> str:
    record = padirecord.build_padi_record(db, member.id)
    return padirecord.render_whatsapp(record, _lang(member))


async def handle_set_payout(db: Session, *, member: Member, raw_args: str) -> str:
    """
    'SET PAYOUT <accountNumber> <bank name...>' — e.g. 'SET PAYOUT 0123456789 Access Bank'

    This is the missing piece that used to require a hand-written script:
    a member registers which account should receive money when they're a
    round's beneficiary. It's validated for real against Monnify's Name
    Enquiry API before being saved — an unrecognized account number or a
    misspelled bank name fails here, at setup time, rather than silently
    breaking a payout weeks later.
    """
    parts = raw_args.strip().split(maxsplit=1)
    if len(parts) < 2:
        return (
            "To set your payout account, send:\n"
            "SET PAYOUT <account number> <bank name>\n"
            "Example: SET PAYOUT 0123456789 Access Bank"
        )

    account_number, bank_name_query = parts[0].strip(), parts[1].strip()
    if not account_number.isdigit() or len(account_number) not in (10, 11):
        return "That doesn't look like a valid account number. It should be 10-11 digits."

    try:
        banks = await monnify_client.get_banks()
    except Exception:  # noqa: BLE001
        return "Couldn't reach the bank directory right now — please try again shortly."

    query_lower = bank_name_query.lower()
    match = next((b for b in banks if b.get("name", "").lower() == query_lower), None)
    if match is None:
        match = next((b for b in banks if query_lower in b.get("name", "").lower()), None)
    if match is None:
        return f"Couldn't find a bank matching '{bank_name_query}'. Check the spelling and try again."

    bank_code = match["code"]
    bank_display_name = match["name"]

    try:
        validation = await monnify_client.validate_bank_account(account_number, bank_code)
    except Exception:  # noqa: BLE001
        return (
            f"Couldn't verify {account_number} at {bank_display_name} right now. "
            "Double-check the account number and try again."
        )

    confirmed_name = validation.get("accountName", "").strip()
    if not confirmed_name:
        return f"Couldn't verify that account at {bank_display_name}. Please check the details and try again."

    member.payout_account_number = account_number
    member.payout_bank_code = bank_code
    db.commit()

    return (
        f"✅ Payout account set: {account_number} ({bank_display_name})\n"
        f"Registered name on the account: {confirmed_name}\n"
        f"If that's not you, send SET PAYOUT again with the correct details."
    )


async def handle_create_pot(db: Session, *, member: Member, raw_args: str) -> str:
    """
    'CREATE POT <name> | <target size> | <amount per cycle>'
    e.g. 'CREATE POT Market Women Circle | 5 | 5000'

    Size is a TARGET for planning purposes, not a hard requirement — see
    pot_service.create_pot for why. The creator automatically gets turn 1;
    everyone else picks their own turn via JOIN.
    """
    parts = [p.strip() for p in raw_args.split("|")]
    if len(parts) != 3:
        return (
            "To create a pot, send:\n"
            "CREATE POT <name> | <target size> | <amount per cycle>\n"
            "Example: CREATE POT Market Women Circle | 5 | 5000"
        )

    name, size_str, amount_str = parts
    if not name:
        return "Pot name can't be empty."
    try:
        size = int(size_str)
        amount = float(amount_str)
    except ValueError:
        return "Size must be a whole number and amount must be a number, e.g. 5 and 5000."
    if size < 2:
        return "A pot needs a target size of at least 2."

    pot = pot_service.create_pot(
        db, name=name, admin_id=member.id, size=size, amount=amount, language=member.preferred_language
    )

    # The admin is auto-seated at turn 1 but never goes through JOIN — without
    # this, they'd have a turn but no reserved account to contribute from in
    # future rounds. Create it here so CREATE POT and JOIN never drift apart.
    account = await _create_reserved_account_for(db, pot=pot, member=member)

    return (
        f"✅ Pot created: '{pot.name}' (ID: {pot.id})\n"
        f"Target size: {size} · NGN{amount:,.0f}/cycle\n\n"
        f"You've got turn 1. Your account: {account.account_number} ({account.bank_name})\n\n"
        f"Share this so others can join:\n"
        f"JOIN {pot.id} <turn number>\n"
        f"Open turns: 2-{size}\n\n"
        f"When everyone's in, send START POT {pot.id} to begin."
    )

def handle_start_pot(db: Session, *, member: Member, pot_id: int) -> str:
    try:
        pot_service.start_pot(db, pot_id=pot_id, requesting_member_id=member.id)
    except ValueError as exc:
        return str(exc)

    pot = db.get(Pot, pot_id)
    slots = db.query(Slot).filter_by(pot_id=pot_id).order_by(Slot.position.asc()).all()
    order_lines = [f"{slot.position + 1}. {db.get(Member, slot.member_id).name}" for slot in slots]

    return (
        f"🎉 '{pot.name}' has started! {pot.size} members, NGN{float(pot.amount):,.0f}/cycle.\n\n"
        f"Turn order:\n" + "\n".join(order_lines) + "\n\n"
        f"Round 1 is open — fund your accounts!"
    )


def handle_leave_pot(db: Session, *, member: Member, pot_id: int) -> str:
    try:
        pot_service.leave_pot(db, pot_id=pot_id, member_id=member.id)
    except ValueError as exc:
        return str(exc)
    return "You've left the pot. Your turn is now free for someone else to claim."


def handle_my_pots(db: Session, *, member: Member) -> str:
    """
    Lists pots this member ADMINISTERS (created), not pots they've merely
    joined — the ID is the only handle a member has for JOIN/START POT/
    LEAVE, and there's otherwise no way to look it back up.
    """
    pots = db.query(Pot).filter_by(admin_id=member.id).order_by(Pot.id.desc()).all()
    if not pots:
        return "You haven't created any pots yet. Send:\nCREATE POT <name> | <target size> | <amount>"

    lines = []
    for pot in pots:
        member_count = db.query(Slot).filter_by(pot_id=pot.id).count()
        if pot_service.pot_has_started(db, pot.id):
            status = f"Active ({pot.size} members)"
        else:
            status = f"Forming ({member_count}/{pot.size} joined)"
        lines.append(f"#{pot.id} · {pot.name} · {status} · NGN{float(pot.amount):,.0f}/cycle")

    return "Your pots:\n" + "\n".join(lines)

async def handle_add_member(
    db: Session, *, member: Member, pot_id: int, phone: str, turn: int, name: str
) -> str:
    """
    'ADD MEMBER <pot id> <turn number> <phone number> <name>' — admin only.

    Answers the real accessibility gap: CREATE POT and JOIN both require
    someone to already be messaging the bot themselves. This lets an admin
    add a member who has never touched WhatsApp — the actual answer to
    "how does a feature-phone user with no WhatsApp get into a pot at all?"

    Important limitation, stated plainly rather than hidden: WhatsApp only
    allows free-form messages to someone within 24 hours of THEIR last
    message to the bot. A member added this way won't receive contribution
    or payout notifications until they send the bot one message themselves
    (even just "hi") to open that window. This is a WhatsApp platform rule,
    not a bug — USSD status checks and SMS notifications aren't affected.
    """
    pot = db.get(Pot, pot_id)
    if pot is None:
        return f"No pot found with ID {pot_id}."
    if pot.admin_id != member.id:
        return "Only the pot admin can add a member directly."
    if pot_service.pot_has_started(db, pot.id):
        return f"'{pot.name}' has already started — membership is closed."

    new_member = db.query(Member).filter_by(phone=phone).first()
    if new_member is None:
        new_member = Member(phone=phone, name=name)
        db.add(new_member)
        db.commit()

    already_in_slot = db.query(Slot).filter_by(pot_id=pot.id, member_id=new_member.id).first()
    if already_in_slot:
        return f"{new_member.name} is already in '{pot.name}' — turn {already_in_slot.position + 1}."

    try:
        rotation.assign_chosen_slot(db, pot_id=pot.id, member_id=new_member.id, requested_turn=turn)
    except ValueError as exc:
        open_turns = rotation.available_turns(db, pot.id)
        turns_text = ", ".join(map(str, open_turns)) if open_turns else "none — pot is full"
        return f"{exc} Available turns: {turns_text}"

    account = await _create_reserved_account_for(db, pot=pot, member=new_member)

    return (
        f"✅ Added {new_member.name} to '{pot.name}' — turn {turn}.\n"
        f"Their account: {account.account_number} ({account.bank_name})\n\n"
        f"Note: {new_member.name} won't get WhatsApp notifications until they "
        f"message the bot themselves (e.g. 'hi') — a WhatsApp platform rule, "
        f"not a bug. USSD/SMS aren't affected."
    )


COMMAND_TABLE = {
    "STATUS": handle_status,
    "ORDER": handle_order,
    "LEDGER": handle_ledger,
    "MY ACCOUNT": handle_my_account,
    "/MYRECORD": handle_my_record,
}


def unrecognized(member: Member) -> str:
    return i18n.t("unrecognized_command", _lang(member))
