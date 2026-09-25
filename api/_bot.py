"""Bot behaviour, independent of the web framework. Called from api/telegram.py (webhook) and
api/cron.py (schedule). Everything runs synchronously inside one serverless request."""
import datetime as dt
import logging
import os

import _brain as brain
import _store as store
import _tg as tg

log = logging.getLogger("skinstinct")

# Fixed offset instead of zoneinfo: serverless images may not ship tz data. IST = +330 minutes.
TZ = dt.timezone(dt.timedelta(minutes=int(os.getenv("TZ_OFFSET_MINUTES", "330"))))
DRAFT_DAYS = os.getenv("DRAFT_DAYS", "mon,wed,fri")
MAX_PENDING = int(os.getenv("MAX_PENDING_DRAFTS", "3"))
ACK_CAPTURES = os.getenv("ACK_CAPTURES", "true").lower() == "true"
# Draft straight away when a channel/DM note is judged worth a post (the schedule then only sweeps
# the backlog, e.g. bulk imports). Set AUTO_DRAFT=false to draft only on schedule or /draft.
AUTO_DRAFT = os.getenv("AUTO_DRAFT", "true").lower() == "true"
TRIAGE_BATCH = int(os.getenv("TRIAGE_BATCH", "8"))

LABEL = {"develop": "worth a post", "hold": "needs a fact from you", "reject": "not a post"}

HELP = """I turn your Telegram notes into LinkedIn drafts for you to review. I never post anything.

Drop notes in your notes channel as usual (or send them to me here). If a note is worth a post, I draft it straight away and send it here. On {days} mornings I also draft the best note still waiting in the backlog.

/draft - draft from the best note now
/draft 12 - draft from note #12
/backlog - notes worth a post, best first
/note 12 - show a note and why it was scored that way
/triage - score any notes still waiting
/stats - counts
/pause, /resume - stop or restart scheduled drafts
Send a .txt file (notes separated by lines of ---) to import a backlog."""


# ---------------------------------------------------------------- identity
def owner_id():
    v = os.getenv("OWNER_CHAT_ID") or store.kv_get("owner_chat_id")
    return int(v) if v else None


def channel_id():
    v = os.getenv("NOTES_CHANNEL_ID") or store.kv_get("notes_channel_id")
    return int(v) if v else None


def say(text, reply_markup=None):
    oid = owner_id()
    return tg.send(oid, text, reply_markup) if oid else None


# ---------------------------------------------------------------- capture + triage
def capture(text, source, msg_id=None):
    note_id = store.add_note(text, source, msg_id)
    if not note_id:
        return None
    t = brain.triage(text)
    store.set_triage(note_id, t)
    verdict, score = t.get("verdict"), t.get("score", 0)
    if verdict == "develop" and AUTO_DRAFT:
        say(f"Note #{note_id} is worth a post ({score}/10). Drafting it now - about 30 seconds.")
        deliver_draft(note_id=note_id)
    elif verdict == "hold":
        needs = "; ".join(t.get("needs_from_meera") or []) or "a fact only you have"
        say(f"Note #{note_id} has a post in it ({score}/10), but I need from you: {needs}. "
            f"Reply with it, or send /draft {note_id} and I'll mark the gaps as [VERIFY].")
    elif verdict == "reject":
        say(f"Note #{note_id} saved, but it isn't a post ({score}/10): {t.get('reason', '')}")
    elif ACK_CAPTURES:
        say(f"Note #{note_id} captured - {LABEL.get(verdict, verdict)} ({score}/10). {t.get('reason', '')}")
    return note_id


def triage_pending(limit=TRIAGE_BATCH):
    done = 0
    for n in store.untriaged_notes(limit):
        store.set_triage(n["id"], brain.triage(n["text"]))
        done += 1
    return done


# ---------------------------------------------------------------- drafting
def keyboard(draft_id):
    return {"inline_keyboard": [
        [{"text": "Approve", "callback_data": f"approve:{draft_id}"},
         {"text": "Redraft", "callback_data": f"redraft:{draft_id}"}],
        [{"text": "Later", "callback_data": f"later:{draft_id}"},
         {"text": "Skip", "callback_data": f"skip:{draft_id}"}],
    ]}


def send_draft(draft_id, note, d):
    t = note.get("triage") or {}
    say(f"DRAFT #{draft_id} - from note #{note['id']} - {t.get('pillar') or 'Unclassified'} - "
        f"{len(d['post'])} chars\nWhy this note: {d.get('why') or t.get('reason', '')}")
    say(d["post"])
    checks = [f"- VERIFY: {v}" for v in d.get("verify", [])] + [f"- Style: {p}" for p in d.get("lint", [])]
    if d.get("hook_idea"):
        checks.append(f"- Optional current angle to look up yourself: {d['hook_idea']}")
    say("Check before posting:\n" + ("\n".join(checks) if checks else "- Nothing flagged."), keyboard(draft_id))


def deliver_draft(note_id=None, scheduled=False, feedback=None, previous=None):
    if not owner_id():
        return "no owner"
    if scheduled and store.pending_draft_count() >= MAX_PENDING:
        say(f"You have {store.pending_draft_count()} drafts waiting for review, so I'm not adding another "
            "today. Scroll up when you have ten minutes.")
        return "skipped: pending"
    note = store.get_note(note_id) if note_id else store.pick_next_note()
    if not note:
        say("No note in the backlog is strong enough for a post right now, so I haven't drafted anything. "
            "Drop a few more notes in the channel, or /backlog to see what's there.")
        return "skipped: empty"
    tg.call("sendChatAction", chat_id=owner_id(), action="typing")
    d = brain.make_draft(note["text"], note.get("triage"), feedback, previous["body"] if previous else None)
    draft_id = store.add_draft(note["id"], d["post"], d, (previous["version"] + 1) if previous else 1)
    send_draft(draft_id, note, d)
    return f"drafted #{draft_id}"


def scheduled_run(now=None):
    """Called by Vercel Cron once a day. Scores leftover notes; drafts on the configured days."""
    now = now or dt.datetime.now(TZ)
    report = {"triaged": triage_pending()}
    days = {d.strip().lower()[:3] for d in DRAFT_DAYS.split(",")}
    if now.strftime("%a").lower() not in days:
        report["draft"] = "not a drafting day"
    elif store.kv_get("paused") == "1":
        report["draft"] = "paused"
    else:
        report["draft"] = deliver_draft(scheduled=True)
    return report


# ---------------------------------------------------------------- update routing
def handle_update(u):
    if "channel_post" in u:
        return on_channel_post(u["channel_post"])
    if "callback_query" in u:
        return on_button(u["callback_query"])
    msg = u.get("message")
    if not msg or msg.get("chat", {}).get("type") != "private":
        return
    chat_id = msg["chat"]["id"]
    text = msg.get("text") or ""
    if text.startswith("/"):
        return on_command(chat_id, text)
    if chat_id != owner_id():
        tg.send(chat_id, "This is a private bot.")
        return
    if msg.get("document"):
        return on_document(msg["document"])
    if msg.get("voice") or msg.get("audio"):
        say("I only read text for now - paste the transcript and I'll take it from there.")
        return
    if text:
        return on_private_text(text, msg.get("message_id"))


def on_channel_post(p):
    cid = channel_id()
    if cid is None:
        store.kv_set("notes_channel_id", p["chat"]["id"])
        cid = p["chat"]["id"]
        say(f"Now listening to channel '{p['chat'].get('title')}'. Every note you drop there gets triaged.")
    if p["chat"]["id"] != cid:
        return
    if p.get("voice") or p.get("audio"):
        say("Voice note received in the channel. I only read text for now - paste the transcript into the "
            "channel or send it to me here.")
        return
    text = p.get("text") or p.get("caption")
    if text and not text.startswith("/"):
        capture(text, "channel", p.get("message_id"))


def on_private_text(text, msg_id):
    awaiting = store.kv_get("awaiting_feedback")
    if awaiting:
        store.kv_set("awaiting_feedback", None)
        prev = store.get_draft(int(awaiting))
        if prev:
            store.set_draft_status(prev["id"], "superseded")
            say("Redrafting with your note...")
            deliver_draft(note_id=prev["note_id"], feedback=text, previous=prev)
            return
    capture(text, "dm", msg_id)


def on_document(doc):
    if not (doc.get("file_name") or "").lower().endswith((".txt", ".md")):
        say("Send notes as a .txt file, separated by lines of ---")
        return
    raw = tg.download_file(doc["file_id"]).decode("utf-8", errors="ignore")
    new = [i for i in (store.add_note(t, "import") for t in store.split_notes(raw)) if i]
    done = triage_pending()
    left = max(0, len(new) - done)
    say(f"Imported {len(new)} new notes and scored {done}." +
        (f" {left} still waiting - send /triage to score the next batch (the daily run also picks them up)." if left else ""))
    say(backlog_text())


def on_button(q):
    tg.call("answerCallbackQuery", callback_query_id=q["id"])
    if q["from"]["id"] != owner_id():
        return
    action, _, did = (q.get("data") or "").partition(":")
    dr = store.get_draft(int(did)) if did.isdigit() else None
    if not dr:
        say("That draft no longer exists.")
        return
    m = q.get("message") or {}
    if m:
        tg.call("editMessageReplyMarkup", chat_id=m["chat"]["id"], message_id=m["message_id"],
                reply_markup={"inline_keyboard": []})
    if action == "approve":
        store.set_draft_status(dr["id"], "approved")
        store.set_note_status(dr["note_id"], "used")
        markers = dr["meta"].get("verify_markers") or []
        warn = f" It still has {len(markers)} [VERIFY] marker(s) - fill those in first." if markers else ""
        say(f"Approved draft #{dr['id']}. Clean copy below to paste into LinkedIn.{warn}")
        say(dr["body"])
    elif action == "redraft":
        store.kv_set("awaiting_feedback", dr["id"])
        say("What should change? Reply in one message - e.g. 'too long', 'open on the factory scene', "
            "'drop the second example'.")
    elif action == "later":
        store.set_draft_status(dr["id"], "later")
        store.set_note_status(dr["note_id"], "hold")
        say(f"Parked. Note #{dr['note_id']} is on hold - /draft {dr['note_id']} to bring it back.")
    elif action == "skip":
        store.set_draft_status(dr["id"], "skipped")
        store.set_note_status(dr["note_id"], "skipped")
        say("Skipped. I won't draft from that note again.")


def backlog_text():
    rows = store.backlog(10)
    if not rows:
        return "Backlog is empty - nothing currently rated worth a post."
    lines = ["Worth a post, best first:"]
    for n in rows:
        lines.append(f"#{n['id']} ({n['score']}/10, {n.get('pillar') or '-'}): "
                     f"{((n['triage'] or {}).get('angle') or n['text'])[:110]}")
    return "\n".join(lines)


def on_command(chat_id, text):
    cmd, *args = text.split()
    cmd = cmd.split("@")[0].lower()
    if cmd == "/start":
        if owner_id() is None:
            store.kv_set("owner_chat_id", chat_id)
            tg.send(chat_id, "You're set as the owner. Only you can use this bot.")
    if chat_id != owner_id():
        tg.send(chat_id, "This is a private bot.")
        return
    if cmd in ("/start", "/help"):
        say(HELP.format(days=DRAFT_DAYS))
        if cmd == "/start" and channel_id() is None:
            say("Next step: add me as an admin of your notes channel. The first post I see there sets it as "
                "your notes channel.")
    elif cmd == "/draft":
        nid = int(args[0]) if args and args[0].isdigit() else None
        if nid and not store.get_note(nid):
            say(f"No note #{nid}.")
            return
        say("Drafting... (about 30 seconds)")
        deliver_draft(note_id=nid)
    elif cmd == "/backlog":
        say(backlog_text())
    elif cmd == "/note" and args and args[0].isdigit():
        n = store.get_note(int(args[0]))
        if not n:
            say("No such note.")
            return
        t = n["triage"] or {}
        say(f"Note #{n['id']} - {n['status']} - {n.get('score')}/10 - {n.get('pillar') or '-'}\n\n{n['text']}\n\n"
            f"Angle: {t.get('angle', '')}\nWhy: {t.get('reason', '')}\n"
            f"Needs from you: {'; '.join(t.get('needs_from_meera') or []) or 'none'}")
    elif cmd == "/triage":
        done = triage_pending()
        say(f"Scored {done} note(s)." if done else "Nothing waiting to be scored.")
    elif cmd == "/stats":
        notes, drafts = store.counts()
        say("Notes: " + (", ".join(f"{k} {v}" for k, v in notes.items()) or "none") +
            "\nDrafts: " + (", ".join(f"{k} {v}" for k, v in drafts.items()) or "none") +
            f"\nScheduled drafting: {'paused' if store.kv_get('paused') == '1' else 'on'} ({DRAFT_DAYS})"
            f"\nModel: {brain.active_model()}")
    elif cmd == "/pause":
        store.kv_set("paused", "1")
        say("Scheduled drafts paused. /resume to restart.")
    elif cmd == "/resume":
        store.kv_set("paused", None)
        say(f"Scheduled drafts on: {DRAFT_DAYS}.")
    else:
        say("I don't know that command. /help lists what I can do.")
