# PadiPot Engine

Trustless rotating savings (ajo/esusu) on WhatsApp, USSD, and SMS — powered by Monnify.
Built for the APIConf Lagos 2026 × Monnify Developer Challenge.

This repo is the entire intelligence + integration layer: the Monnify client, the
round state machine, MonniGuard (the reliability layer), PadiScore, Earned Rotation,
the Defaulter Registry, Padi Record, and the WhatsApp/USSD/SMS channel layer.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # fill in real Monnify + WhatsApp + Africa's Talking keys
uvicorn app.main:app --reload --port 8000
```

Run the test suite (no external keys needed — pure logic, in-memory SQLite):

```bash
pytest tests/ -v
```

For local webhook testing, tunnel port 8000 (e.g. `ngrok http 8000`) and point:
- Monnify sandbox webhook URL → `https://<tunnel>/webhooks/monnify`
- WhatsApp Cloud API webhook → `https://<tunnel>/webhooks/whatsapp`
- Africa's Talking USSD callback → `https://<tunnel>/ussd`

## Architecture

```
WhatsApp Cloud API ──┐                      ┌─ Monnify Reserved Accounts
USSD (Africa's Talk.)─┼─▶ FastAPI (app/main)─┼─ Monnify Webhooks (in)
SMS  (Africa's Talk.)─┘        │             ├─ Monnify Disbursements
                                │             ├─ Monnify BVN Verification
                                ▼             └─ Monnify Transactions (query)
                    ┌───────────────────────┐
                    │  engine/              │  state machine, reconciler,
                    │  (round + rotation)    │  payout orchestrator, rotation,
                    │                        │  PadiScore, registry, Padi Record
                    ├───────────────────────┤
                    │  guard/ (MonniGuard)   │  idempotency, reconciliation sweep,
                    │  zero domain imports   │  pre-flight checks
                    └───────────────────────┘
                                │
                    PostgreSQL/SQLite (ledger, pots, members, registry)
```

## Module map

| Path | Responsibility |
|---|---|
| `app/monnify/client.py` | Auth, reserved accounts, disbursements, verification, transaction query |
| `app/monnify/webhooks.py` | HMAC-SHA512 signature verification, event parsing |
| `app/monnify/router.py` | Monnify webhook receiver → dispatches to reconciler/payout |
| `app/engine/state_machine.py` | OPEN → FUNDED → DISBURSING → PAID, row-locked |
| `app/engine/reconciler.py` | Attributes contributions (webhook or sweep) to the right cycle |
| `app/engine/payout.py` | Pre-flight → disburse → mark paid → trigger rotation update |
| `app/engine/rotation.py` | Earned Rotation — payout order is earned, not assigned |
| `app/engine/padiscore.py` | Rule-based 0–100 reliability score |
| `app/engine/registry.py` | Defaulter registry — logs defaults, gates future joins |
| `app/engine/padirecord.py` | The `/myrecord` shareable savings statement |
| `app/guard/*` | MonniGuard — idempotency, sweep, pre-flight (isolated via `ports.py`) |
| `app/channels/whatsapp/*` | WhatsApp Cloud API client, webhook, conversation flows |
| `app/channels/ussd/handler.py` | Africa's Talking USSD menu (feature-phone access) |
| `app/channels/sms/notifier.py` | SMS notifications for members without WhatsApp |
| `app/channels/i18n.py` | Message catalog — English + Nigerian Pidgin, extensible |
| `app/scheduler.py` | Periodic MonniGuard sweep + reminder jobs |

## Design principles carried through the code

- **Trust by architecture, not escrow.** Earned Rotation (new members start
  in late payout slots and earn early ones through history) makes
  collect-and-vanish structurally impossible — no vault, no compensation
  logic needed.
- **MonniGuard is isolated.** Nothing under `app/guard` imports from
  `app/engine`, `app/channels`, or `app/models` — only the small Protocols in
  `app/guard/ports.py`. This is what makes it extractable later as a
  standalone SDK.
- **Every disbursement and contribution is idempotent.** Deterministic
  references (`padipot-payout-cycle-{id}`) mean a retry, a race, or a
  duplicate webhook is always a safe no-op, never a double-payment.
- **PadiScore is explainable, not a black box.** Every input is a plain
  count/duration from the ledger with a named, fixed weight — defensible
  under technical questioning today; the module boundary is where an ML
  swap would happen once there's enough data.

## WhatsApp transport: two options, pick one for the demo

Both are wired in and live side by side — `app/channels/whatsapp/webhook.py`
(Meta Cloud API, direct) and `app/channels/whatsapp/twilio_webhook.py`
(Twilio). They share all business logic through
`app/channels/whatsapp/dispatcher.py`, so switching transports never touches
`flows.py`, the engine, or MonniGuard.

**Twilio (recommended for the hackathon demo)** — no Meta Business
verification wait. Sandbox setup:
1. Twilio console → Messaging → Try it out → Send a WhatsApp message
2. From your phone, WhatsApp "join `<sandbox-code>`" to the number shown
3. Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`,
   and `PUBLIC_BASE_URL` (your ngrok HTTPS URL) in `.env`
4. Point the Twilio sandbox webhook at `https://<your-tunnel>/webhooks/whatsapp-twilio`
5. Every demo phone (yours, your partner's, any test members) must send the
   join code once — a sandbox limit, lifted with an approved sender later

**Meta Cloud API (direct)** — already built and functional, kept as the
production path since Twilio adds a small per-message cost on top of
Meta's. Requires Meta Business Manager verification, which can take days —
not recommended to depend on this close to submission.

## Partner handoff — Twilio WhatsApp owner

Files that are yours to own end-to-end:
- `app/channels/whatsapp/twilio_client.py` — outbound sending
- `app/channels/whatsapp/twilio_webhook.py` — inbound receiver + signature check

You do **not** need to touch `flows.py`, `dispatcher.py`, or anything under
`app/engine` / `app/guard` / `app/monnify` — those are shared and already
tested. If you want richer UX (interactive list messages, quick-reply
buttons, template messages for the 24-hour-window rule), that's fully your
call to add on top of `twilio_client.py` — it won't affect the rest of the
system as long as `send_text()` keeps working for the plain notifications
the engine already sends.

## Demo script

1. Create a pot, join 2–3 more members — each gets a live Monnify sandbox reserved account.
2. Fund accounts by sandbox transfer — webhooks update the group in real time.
3. Kill one webhook deliberately — the scheduler's guard sweep self-heals the missing
   contribution on the next tick (`GUARD_SWEEP_INTERVAL_SECONDS`, default 120s).
4. Pot hits 100% → pre-flight passes → Disbursement API pays the beneficiary automatically.
5. `/myrecord` on WhatsApp renders the Padi Record.
6. Dial the USSD shortcode to show the same data on a feature phone.

## Known integration gaps to confirm before going live

- `MonnifyClient.verify_bvn()` — the exact live path for the BVN match
  endpoint should be confirmed against the current Monnify API reference in
  your dashboard; the method is isolated so this is a one-line change.
- `Member.payout_account_number` / `payout_bank_code` — wire up the
  WhatsApp/USSD flow step that actually collects a beneficiary's payout
  bank details at join time (currently a schema field with no capture flow).
- `run_payout_for_cycle(..., wallet_balance=...)` — replace the placeholder
  balance value in `app/monnify/router.py` with a real call to Monnify's
  wallet-balance endpoint before going live.
