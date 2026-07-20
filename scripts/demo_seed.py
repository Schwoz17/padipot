"""
Demo seed script — one command, one clean pot, ready to fund.

Wipes the local dev database and builds a fresh demo pot from scratch:
creates members, creates the pot, has every non-admin member self-select a
turn and join (which creates REAL Monnify sandbox reserved accounts),
starts the pot (locking membership and opening round 1), and prints
exactly what to fund in the Monnify simulator.

Usage:
    python scripts/demo_seed.py
    python scripts/demo_seed.py --size 4 --amount 5000 --force

Run from the repo root (not from inside scripts/), so relative imports
and the sqlite file path resolve correctly.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db, session_scope
from app.models import Member, Language, ReservedAccount
from app.engine.pot_service import create_pot
from app.engine import pot_service
from app.channels.whatsapp import flows

DEMO_MEMBERS = [
    ("2348010000001", "Amaka Eze"),
    ("2348010000002", "Tunde Bakare"),
    ("2348010000003", "Ngozi Chukwu"),
    ("2348010000004", "Ibrahim Musa"),
    ("2348010000005", "Yetunde Adeyemi"),
]


async def seed(size: int, amount: float, pot_name: str, language: Language) -> None:
    if size < 2 or size > len(DEMO_MEMBERS):
        raise SystemExit(f"--size must be between 2 and {len(DEMO_MEMBERS)}")

    init_db()

    with session_scope() as db:
        members = []
        for phone, name in DEMO_MEMBERS[:size]:
            m = Member(phone=phone, name=name)
            db.add(m)
            members.append(m)
        db.commit()

        admin = members[0]
        pot = create_pot(
            db, name=pot_name, admin_id=admin.id,
            size=size, amount=amount, language=language,
        )
        print(f"\nCreated pot #{pot.id}: '{pot.name}' — target {size} members, NGN{amount:,.0f}/cycle")
        print(f"Admin: {admin.name} — turn 1 (auto-assigned)\n")

        rows = []
        for turn, member in enumerate(members[1:], start=2):
            reply = await flows.handle_join_pot(db, member=member, pot=pot, requested_turn=turn)
            account = db.query(ReservedAccount).filter_by(pot_id=pot.id, member_id=member.id).first()
            rows.append((turn, member.name, account.account_number, account.bank_name))

        cycle = pot_service.start_pot(db, pot_id=pot.id, requesting_member_id=admin.id)
        print(f"Pot started — cycle {cycle.round_no} (id={cycle.id}) is OPEN\n")

        print("=" * 68)
        print(f"{'TURN':<6}{'MEMBER':<20}{'ACCOUNT NUMBER':<18}{'BANK'}")
        print("=" * 68)
        print(f"{'1':<6}{admin.name:<20}{'(no account needed — beneficiary)':<18}")
        for turn, name, acct_num, bank in rows:
            print(f"{turn:<6}{name:<20}{acct_num:<18}{bank}")
        print("=" * 68)

        total_needed = amount * (size - 1)
        print(f"\nFund EACH non-admin account above for exactly NGN{amount:,.0f} in the Monnify simulator.")
        print(f"Round completes once all {size - 1} accounts are funded (NGN{total_needed:,.0f} total).")
        print(f"\nBeneficiary this round: {admin.name}")
        print("(Beneficiary needs a payout account — have them send via WhatsApp/Twilio once live:")
        print("  SET PAYOUT <account number> <bank name>)")


def main():
    parser = argparse.ArgumentParser(description="Seed a fresh PadiPot demo pot")
    parser.add_argument("--size", type=int, default=3, help="number of members, including admin (default 3)")
    parser.add_argument("--amount", type=float, default=5000.0, help="contribution per member per cycle (default 5000)")
    parser.add_argument("--name", type=str, default="Demo Pot", help="pot name")
    parser.add_argument("--lang", type=str, default="en", choices=["en", "pcm"], help="pot language")
    parser.add_argument("--force", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    if not args.force:
        confirm = input("This will WIPE all existing demo data and start fresh. Continue? [y/N] ")
        if confirm.strip().lower() != "y":
            print("Cancelled.")
            return

    # Drop/recreate tables through the same engine the app itself uses,
    # rather than deleting the .db file directly — the file-delete approach
    # fails on Windows with a PermissionError while uvicorn has it open
    # (SQLite's own locking handles concurrent access fine; the OS-level
    # file handle is what Windows won't release).
    from app.db import engine, Base
    from app import models  # noqa: F401 — registers all tables on Base.metadata
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("Wiped and recreated all tables.\n")

    language = Language.PCM if args.lang == "pcm" else Language.EN
    asyncio.run(seed(args.size, args.amount, args.name, language))


if __name__ == "__main__":
    main()