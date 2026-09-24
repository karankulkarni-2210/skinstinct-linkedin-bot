---
name: meera-linkedin-drafter
description: Maintain, debug or extend the Skinstinct Telegram bot (Vercel + Gemini + Supabase) that turns Meera Pillai's Telegram notes into LinkedIn drafts in her voice. Use for any change to its voice, triage rules, schedule, deployment or data (MESA Case 1).
---

# Meera LinkedIn Drafter: working on this repo

The bot runs as Python serverless functions on Vercel (`api/`), uses Gemini as its brain,
stores data in Supabase, and talks to Telegram through a webhook. Read `README.md` first.

## Rules that must not change
- **Never post to LinkedIn.** Meera publishes herself. She turned down two tools that ran end to end.
- **No automatic news hooks or outside statistics (the Cut).** Any fact that isn't in the note or
  in "Facts she has already published" (`voice/voice_guide.md`) must appear as `[VERIFY: ...]`.
  Never loosen `DRAFT_SYSTEM` rule 2 in `api/_brain.py`.
- **Never commit secrets.** `tests/test_offline.py::test_secrets_never_in_repo` enforces this.
- Voice comes only from `voice/voice_guide.md` and `voice/exemplars.md` (her 4 real posts).

## Everyday changes
| Change | Where | Then |
|---|---|---|
| Voice rule or new published fact | `voice/*.md` | `python scripts/build_voice.py` |
| New off-voice phrase | `BANNED` in `api/_brain.py` | add a test |
| Triage rubric or thresholds | `TRIAGE_SYSTEM` in `api/_brain.py` | also update `index.html` Parameters |
| Draft days, pending cap, model | Vercel env: `DRAFT_DAYS`, `MAX_PENDING_DRAFTS`, `GEMINI_MODEL` | redeploy |
| Cron time | `vercel.json` (UTC; 03:00 UTC = 08:30 IST) | push |

After every change: `python -m pytest -q` (all tests must pass), commit, push. Vercel redeploys
from `main` automatically.

## Checks after deploying
- `GET https://<domain>/api/health?key=<CRON_SECRET>` should return `"all_ok": true`. It checks
  Telegram, the channel, whether the bot is a channel admin, the webhook, the database and Gemini.
- If the domain or `WEBHOOK_SECRET` changes, open `GET /api/setup?key=<CRON_SECRET>` once.
- Logs: Vercel dashboard > Project > Logs.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Channel posts ignored | Bot isn't a channel admin, or `NOTES_CHANNEL_ID` is wrong (it must start with `-100`) |
| Gemini 400/401/403 | Key invalid for both AI Studio and Vertex express. Get a new key at aistudio.google.com |
| Gemini 404 | `GEMINI_MODEL` unknown. The code already falls back to a current Flash model |
| Database 401/empty results | `DB_SECRET` in Vercel doesn't match `private.bot_config.secret` |
| Duplicate replies | Webhook retries. `processed_updates` should de-dupe; check that table exists |
| Draft invents facts | Tighten `DRAFT_SYSTEM` rule 2 and add the case to the tests |
