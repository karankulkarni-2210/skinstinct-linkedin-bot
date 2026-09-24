# Skinstinct: Notes to LinkedIn Drafts

A Telegram bot for Meera Pillai, founder of Skinstinct (MESA Case 1). It reads the notes she already
drops into her own Telegram channel, decides which ones are worth a post, drafts them in her voice
with Gemini, and sends them to her for review. **It never posts to LinkedIn.**

The deployed site's home page (`index.html`) holds the use case: automation brief, components map,
all parameters, the Cut, and live counts.

## How it works

```
Telegram channel post ──> /api/telegram (webhook) ──> Supabase: store + de-dupe
                                   │
                                   ├─ Gemini: triage 0-10 → develop / hold / reject
                                   │
Vercel Cron (daily, 08:30 IST) ─> /api/cron ─> Mon/Wed/Fri: best note → Gemini draft
                                   │                   → voice lint → one repair pass
                                   ▼
                     Meera's DM: draft + [VERIFY] list + Approve / Redraft / Later / Skip
```

| Path | Purpose |
|---|---|
| `api/telegram.py` | Webhook. Checks Telegram's secret header and ignores Telegram's retries of the same update |
| `api/cron.py` | Daily run. Scores leftover notes; drafts on `DRAFT_DAYS` |
| `api/setup.py` | `GET /api/setup?key=CRON_SECRET` registers the webhook and command menu |
| `api/health.py` | `GET /api/health?key=CRON_SECRET` checks Telegram, the channel, the database and Gemini |
| `api/stats.py` | Public counts only (no note text), used by the home page |
| `api/_brain.py` | Triage, draft and repair prompts; the voice lint |
| `api/_bot.py` | Commands, buttons, scheduling logic |
| `api/_store.py` | Supabase REST access |
| `voice/` | Voice guide and her 4 LinkedIn posts. Run `python scripts/build_voice.py` after editing |
| `notes/sample_notes.txt` | 10 **made-up** test notes (the case's real `notes/` folder wasn't provided) |

The code uses only the Python standard library, so there's nothing to install.

## Environment variables (set in Vercel, never committed)

See `.env.example`. Required: `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `OWNER_CHAT_ID`,
`NOTES_CHANNEL_ID`, `SUPABASE_URL`, `SUPABASE_KEY`, `DB_SECRET`, `WEBHOOK_SECRET`, `CRON_SECRET`.

## Database

Supabase tables: `notes`, `drafts`, `kv`, `processed_updates`. Every table has row-level security.
The publishable key can only read or write rows when the request carries an `x-bot-secret` header
that matches `private.bot_config.secret`. That schema isn't exposed through the API.
Schema: `supabase/schema.sql`.

## Tests

```bash
python -m pytest -q     # 16 offline tests; Telegram, Supabase and Gemini are faked
```
