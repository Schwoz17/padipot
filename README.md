# PadiPot Engine

Trustless rotating savings (ajo/esusu) on WhatsApp, USSD, and SMS — powered by Monnify.
Built for the APIConf Lagos 2026 × Monnify Developer Challenge.

This repo is the entire intelligence + integration layer: the Monnify client, the
round state machine, MonniGuard (the reliability layer), PadiScore, Earned Rotation,
the Defaulter Registry, Padi Record, and the WhatsApp/USSD/SMS channel layer.

**Status: proven live end-to-end against the real Monnify sandbox.** A pot has
been created, joined, started, funded, and fully paid out through this exact
codebase — not a simulation. 46/46 tests passing, CI green on every push
(see the Actions tab).

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # fill in real Monnify + Twilio keys
uvicorn app.main:app --reload --port 8000
```

Run the test suite (no external keys needed — pure logic, in-memory SQLite):

```bash
pytest tests/ -v
```

For local webhook testing, tunnel port 8000 (e.g. `ngrok http 8000`) and point:
- Monnify sandbox webhook URL → `https://<tunnel>/webhooks/monnify` (set both the
  Transaction Completion and Disbursement fields under Developer → Webhook URLs)
- Twilio WhatsApp sandbox webhook → `https://<tunnel>/webhooks/whatsapp-twilio`
- Africa's Talking USSD callback → `https://<tunnel>/ussd`

Seed a fresh demo pot in one command (see `scripts/demo_seed.py` for details):

```bash
python scripts/demo_seed.py --size 3 --amount 5000
```

## Architecture

```
WhatsApp (Twilio) ────┐                     ┌─ Monnify Reserved Accounts
USSD (Africa's Talk.)─┼─▶ FastAPI (app/main)─┼─ Monnify Webhooks (in)
SMS  (Africa's Talk.)─┘        │             ├─ Monnify Disbursements
                                │             ├─ Monnify Verification (name enquiry, bank list)
                                ▼             └─ Monnify Transactions (query)
                    ┌───────────────────────┐
                    │  engine/              │  state machine, reconciler, pot lifecycle,
                    │  (round + rotation)    │  payout orchestrator, rotation,
                    │                        │  PadiScore, registry, Padi Record
                    ├───────────────────────┤
                    │  guard/ (MonniGuard)   │  idempotency, reconciliation sweep,
                    │  zero domain imports   │  pre-flight checks
                    └───────────────────────┘
                                │
                         SQLite (dev) / PostgreSQL (prod)
                    ledger, pots, members, registry
```

## Module map

| Path | Responsibility |
|---|---|
| `app/monnify/client.py` | Auth, reserved accounts, disbursements, verification, bank list, transaction query |
| `app/monnify/webhooks.py` | HMAC-SHA512 signature verification, event parsing |
| `app/monnify/router.py` | Monnify webhook receiver → dispatches to reconciler/payout, resolves async disbursement confirmations |
| `app/engine/state_machine.py` | OPEN → FUNDED → DISBURSING → PAID, row-locked |
| `app/engine/reconciler.py` | Attributes contributions (webhook or sweep) to the right cycle |
| `app/engine/pot_service.py` | Pot lifecycle — create, formation, `start_pot` (locks membership), `leave_pot` |
| `app/engine/payout.py` | Pre-flight → disburse → confirm (sync or async) → trigger rotation update |
| `app/engine/rotation.py` | Earned Rotation — self-selected turns during formation, earned-only reordering once active |
| `app/engine/padiscore.py` | Rule-based 0–100 reliability score |
| `app/engine/registry.py` | Defaulter registry — logs defaults, gates future joins |
| `app/engine/padirecord.py` | The `/myrecord` shareable savings statement |
| `app/guard/*` | MonniGuard — idempotency, sweep, pre-flight (isolated via `ports.py`) |
| `app/channels/whatsapp/dispatcher.py` | Transport-agnostic command routing, shared by both WhatsApp transports |
| `app/channels/whatsapp/flows.py` | All command handlers — CREATE POT, JOIN, START POT, LEAVE, SET PAYOUT, STATUS, etc. |
| `app/channels/whatsapp/twilio_*.py` | Twilio WhatsApp transport (primary — see below) |
| `app/channels/whatsapp/webhook.py`, `client.py` | Meta Cloud API transport (alternative) |
| `app/channels/ussd/handler.py` | Africa's Talking USSD menu (feature-phone access) |
| `app/channels/sms/notifier.py` | SMS notifications for members without WhatsApp |
| `app/channels/i18n.py` | Message catalog — English + Nigerian Pidgin, extensible |
| `app/scheduler.py` | Periodic MonniGuard sweep + reminder jobs |
| `scripts/demo_seed.py` | One-command fresh demo pot, real Monnify accounts, ready to fund |

## WhatsApp commands

| Command | Who | What it does |
|---|---|---|
| `CREATE POT <name> \| <target size> \| <amount>` | Anyone | Creates a pot, creator gets turn 1 |
| `JOIN <pot id> <turn number>` | Anyone | Self-selects an open turn during formation — see Earned Rotation below |
| `ADD MEMBER <pot id> <turn> <phone> <name>` | Admin only | Adds someone who has never messaged the bot — see accessibility note below |
| `START POT <pot id>` | Admin only | Locks membership to whoever actually joined, opens round 1 |
| `LEAVE <pot id>` | Any member | Pre-start only — frees the turn for someone else |
| `MY POTS` | Anyone | Lists pots you administer, with status |
| `SET PAYOUT <account number> <bank name>` | Any member | Registers where your payout goes — validated live via Monnify name enquiry before saving |
| `STATUS` / `ORDER` / `LEDGER` / `MY ACCOUNT` | Pot member | Round progress, turn order, contribution history, your reserved account |
| `/myrecord` | Anyone | Shareable savings statement (PadiScore, streak, history) |

**On `ADD MEMBER` and WhatsApp's 24-hour rule:** WhatsApp only allows
free-form messages to someone within 24 hours of *their* last message to
the bot. A member added via `ADD MEMBER` (rather than joining themselves)
won't receive contribution/payout notifications until they message the bot
once (even just "hi"). This is a platform rule, not a bug — USSD status
checks and SMS notifications aren't affected. `ADD MEMBER` is the real
answer to "how does a feature-phone user with no WhatsApp get into a pot
at all?" — an admin who does have WhatsApp adds them directly.

## Design principles carried through the code

- **Trust by architecture, not escrow.** Earned Rotation makes collect-and-vanish
  structurally impossible without sacrificing member choice: during a pot's
  *formation* phase, members self-select any open turn (everyone has equal —
  zero — history at that point, so there's no trust gap to protect yet). Once
  a pot *starts*, membership and turn order lock; any later reordering only
  ever happens automatically, based on earned history, never by request.
- **MonniGuard is isolated.** Nothing under `app/guard` imports from
  `app/engine`, `app/channels`, or `app/models` — only the small Protocols in
  `app/guard/ports.py`. This is what makes it extractable later as a
  standalone SDK.
- **Every disbursement and contribution is idempotent.** Deterministic
  references (`padipot-payout-cycle-{id}`) mean a retry, a race, or a
  duplicate webhook is always a safe no-op, never a double-payment.
- **Acceptance is not confirmation.** Monnify's async disbursements can be
  *accepted* and still later *fail*. A cycle only becomes `PAID` on Monnify's
  actual `DISBURSEMENT_SUCCESSFUL` webhook (`resolve_async_disbursement` in
  `app/engine/payout.py`) — never optimistically on acceptance alone.
- **PadiScore is explainable, not a black box.** Every input is a plain
  count/duration from the ledger with a named, fixed weight — defensible
  under technical questioning today; the module boundary is where an ML
  swap would happen once there's enough data.

## WhatsApp transport

**Twilio is the primary transport** — no Meta Business verification wait, which
matters close to a submission deadline. Sandbox setup:
1. Twilio console → Messaging → Try it out → Send a WhatsApp message
2. From your phone, WhatsApp "join `<sandbox-code>`" to the number shown
3. Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`, and
   `PUBLIC_BASE_URL` (your ngrok HTTPS URL) in `.env`
4. Point the Twilio sandbox webhook at `https://<your-tunnel>/webhooks/whatsapp-twilio`
5. Every demo phone must send the join code once — a sandbox limit, lifted with
   an approved sender later

The Meta Cloud API transport (`app/channels/whatsapp/webhook.py`, `client.py`)
is also fully wired in as an alternative — both share all command logic through
`app/channels/whatsapp/dispatcher.py`, so switching transports never touches
`flows.py`, the engine, or MonniGuard.

## Demo script

1. `python scripts/demo_seed.py --size 3` — creates a real pot with real Monnify accounts
2. `ADD MEMBER <pot id> <turn> <phone> <name>` — show a member being added who has
   never touched WhatsApp themselves; call out the accessibility story explicitly
3. Fund each printed account for the exact amount, via the Monnify sandbox simulator
   (Developer → Simulators) or a real bank transfer
4. Watch the webhook fire in real time — contribution recorded, cycle flips to `FUNDED`
5. **The self-heal moment** — break the webhook on Monnify's side, not your server:
   - Monnify dashboard → Developer → Webhook URLs → temporarily set Transaction
     Completion to an invalid URL → fund an account → nothing happens (webhook
     has nowhere to land)
   - Restore the correct webhook URL
   - The scheduler's guard sweep catches the missed contribution on its next tick
     (`GUARD_SWEEP_INTERVAL_SECONDS`, default 120s — consider lowering to ~20s in
     Render's environment variables just for judging day, then setting it back
     afterward, so the self-heal doesn't require dead air on stage)
6. Beneficiary sends `SET PAYOUT <account> <bank>` — validated live before saving
7. Pot completes → pre-flight passes → disbursement fires → cycle closes `PAID`
8. `/myrecord` renders the Padi Record; USSD shows the same data on a feature phone

This exact sequence has been run successfully against the live Monnify sandbox.

## Known integration gaps to confirm before going live

- `MonnifyClient.verify_bvn()` — the exact live path for the BVN match
  endpoint should be confirmed against the current Monnify API reference in
  your dashboard; the method is isolated so this is a one-line change.
- `run_payout_for_cycle(..., wallet_balance=...)` — replace the placeholder
  balance value in `app/monnify/router.py` with a real call to Monnify's
  wallet-balance endpoint before going live.
- Three sandbox-side blockers (OTP/MFA on disbursements, Disbursements not
  enabled by default, and a transient `503` on name enquiry) were all
  resolved via Monnify support during development — worth confirming they're
  also cleared on any new sandbox/live contract.

## Partner handoff — Twilio WhatsApp owner

Files that are yours to own end-to-end:
- `app/channels/whatsapp/twilio_client.py` — outbound sending
- `app/channels/whatsapp/twilio_webhook.py` — inbound receiver + signature check

You do **not** need to touch `flows.py`, `dispatcher.py`, or anything under
`app/engine` / `app/guard` / `app/monnify` — those are shared and already
tested. Richer UX (interactive lists, quick-reply buttons, template messages
for the 24-hour-window rule) is fully your call to add on top of
`twilio_client.py` — it won't affect the rest of the system as long as
`send_text()` keeps working for the plain notifications the engine sends.
