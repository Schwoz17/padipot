"""
Message catalog. Every user-facing string across WhatsApp/USSD/SMS lives
here, keyed by a message id and language code, so:
  (a) no copy is duplicated across three channel modules, and
  (b) adding Yoruba/Hausa/Igbo later is a translation task — add a key to
      each dict below — not an engineering one.

Deliberately shipping only en + pcm for the hackathon: machine-translated
Yoruba/Hausa/Igbo in front of a Lagos judging panel is a bigger risk than
not having it, and the catalog architecture proves the roadmap without
needing to ship it today.
"""
from __future__ import annotations

CATALOG: dict[str, dict[str, str]] = {
    "welcome": {
        "en": "Welcome to PadiPot! Reply JOIN to create or join a savings pot.",
        "pcm": "Welcome to PadiPot! Reply JOIN make you create or join one pot.",
    },
    "account_created": {
        "en": "Your account for '{pot_name}' is ready.\nAccount number: {account_number}\nBank: {bank_name}\nFund it before {deadline} each cycle.",
        "pcm": "Your account for '{pot_name}' don ready.\nAccount number: {account_number}\nBank: {bank_name}\nMake you fund am before {deadline} every cycle.",
    },
    "contribution_received": {
        "en": "✅ {member_name} has contributed · {progress}/{total} · NGN{amount_in_pot:,.0f} in the pot",
        "pcm": "✅ {member_name} don pay in own · {progress}/{total} · NGN{amount_in_pot:,.0f} dey inside pot",
    },
    "pot_complete": {
        "en": "🎉 POT COMPLETE! Sending the full pot to {beneficiary_name} now.",
        "pcm": "🎉 POT DON PAY! We dey send everything go {beneficiary_name} now now.",
    },
    "payout_sent": {
        "en": "💰 NGN{amount:,.0f} has been sent to {beneficiary_name}. Round {round_no} complete.",
        "pcm": "💰 NGN{amount:,.0f} don land for {beneficiary_name} account. Round {round_no} don finish.",
    },
    "payout_deferred": {
        "en": "⏳ Payout for round {round_no} is delayed — we're resolving a payment check and will notify you shortly.",
        "pcm": "⏳ Payout for round {round_no} dey delay small — we dey sort am out, we go update una sharp sharp.",
    },
    "score_dropped": {
        "en": "⚠️ {member_name}'s PadiScore dropped to {score:.0f} after a late payment.",
        "pcm": "⚠️ {member_name} PadiScore don drop reach {score:.0f} because dem pay late.",
    },
    "default_recorded": {
        "en": "{member_name} has an outstanding default of NGN{amount:,.0f} and cannot join new pots until it's cleared.",
        "pcm": "{member_name} get outstanding wey never clear — NGN{amount:,.0f}. Dem no fit join new pot until e clear.",
    },
    "join_blocked_default": {
        "en": "You can't join a new pot yet: {reason}",
        "pcm": "You no fit join new pot yet: {reason}",
    },
    "status_reply": {
        "en": "Round {round_no}: {progress}/{total} contributed · NGN{amount_in_pot:,.0f} in the pot · Next to collect: {next_beneficiary}",
        "pcm": "Round {round_no}: {progress}/{total} don pay · NGN{amount_in_pot:,.0f} dey pot · Who dey next: {next_beneficiary}",
    },
    "my_account_reply": {
        "en": "Your account number is {account_number} ({bank_name}). Fund it to contribute.",
        "pcm": "Your account number na {account_number} ({bank_name}). Fund am make you contribute.",
    },
    "unrecognized_command": {
        "en": "Sorry, I didn't understand that. Reply STATUS, ORDER, LEDGER, MY ACCOUNT, or /myrecord.",
        "pcm": "Sorry, I no understand. Reply STATUS, ORDER, LEDGER, MY ACCOUNT, or /myrecord.",
    },
}


def t(message_id: str, lang: str = "en", **kwargs) -> str:
    """Look up and format a message. Falls back to English if the language is missing a key."""
    entry = CATALOG.get(message_id)
    if entry is None:
        return f"[missing message: {message_id}]"
    template = entry.get(lang, entry.get("en", ""))
    try:
        return template.format(**kwargs)
    except KeyError as exc:
        return f"[template error in {message_id}: missing {exc}]"
