"""Offline tests: no network, no keys. Telegram, Supabase and Gemini are faked in memory.
Run: python -m pytest -q"""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
os.environ.update({
    "TELEGRAM_BOT_TOKEN": "123:TEST", "GEMINI_API_KEY": "g", "SUPABASE_URL": "https://db.test",
    "SUPABASE_KEY": "sb_publishable_x", "DB_SECRET": "s", "OWNER_CHAT_ID": "42",
    "NOTES_CHANNEL_ID": "-1001", "ACK_CAPTURES": "true",
})

import pytest  # noqa: E402

import _brain as brain  # noqa: E402
import _bot as bot  # noqa: E402
import _http  # noqa: E402
import _store as store  # noqa: E402
import _tg as tg  # noqa: E402

GOOD = ("Batch fourteen came back with its pH down by about 0.4 and it's enough to shift the texture. "
        "I think you'd notice it, and that's why we're holding it back this week. " * 12).strip()


RSS = b"""<?xml version="1.0"?><rss><channel><title>t</title>
<item><title>Regulator tightens cosmetic preservative rules - Example Times</title>
<link>https://news.google.com/rss/articles/abc</link><pubDate>Mon, 21 Sep 2026 08:00:00 GMT</pubDate>
<description>&lt;a href="x"&gt;New labelling rules for preservatives take effect in 2027&lt;/a&gt;</description>
<source url="https://example.com">Example Times</source></item></channel></rss>"""
RSS_EMPTY = b"""<?xml version="1.0"?><rss><channel><title>t</title></channel></rss>"""


class Fake:
    """In-memory stand-in for PostgREST, the Telegram Bot API and Gemini."""

    def __init__(self):
        self.tables = {"notes": [], "drafts": [], "kv": [], "processed_updates": []}
        self.ids = {"notes": 0, "drafts": 0}
        self.sent = []
        self.gemini_calls = []
        self.blocked = set()
        self.no_news = False
        self.last_user = ""
        self.news_queries = []
        self.use_news = True

    # ---- routing
    def request(self, method, url, body=None, headers=None, timeout=60, raw=False):
        if "api.telegram.org" in url:
            return self.telegram(url, body or {}, raw)
        if "googleapis.com" in url:
            return self.gemini(url, body)
        if "news.google.com" in url:
            self.news_queries.append(url)
            return RSS_EMPTY if self.no_news else RSS
        return self.db(method, url, body, headers or {})

    # ---- telegram
    def telegram(self, url, body, raw):
        if body.get("chat_id") in self.blocked:
            raise _http.HttpError(400, '{"description":"Bad Request: PEER_ID_INVALID"}', url)
        if raw:
            return "note one about pH drift in batch fourteen\n---\nnote two about fragrance in sensitive skin products".encode()
        m = url.rsplit("/", 1)[1]
        if m == "sendMessage":
            self.sent.append(body)
        if m == "getFile":
            return {"ok": True, "result": {"file_path": "x.txt"}}
        return {"ok": True, "result": {"message_id": len(self.sent)}}

    # ---- gemini
    def gemini(self, url, body):
        system = body["systemInstruction"]["parts"][0]["text"]
        self.gemini_calls.append(system[:40])
        user = body["contents"][0]["parts"][0]["text"]
        if user.startswith("NOTE FROM MEERA"):
            self.last_user = user                 # what the drafter was given
        if body["generationConfig"].get("responseMimeType") == "application/json":
            text = json.dumps({"score": 8, "reason": "Specific batch incident with a clear lesson.",
                               "pillar": "Formulation Science", "angle": "mid-batch sampling catches drift",
                               "hook": "batch 14", "needs_from_meera": [],
                               "keywords": ["pH drift", "preservative blend", "certificate of analysis"],
                               "search_phrase": "cosmetic preservative change pH", "hard_reject": None})
        else:
            used = "yes" if ("NEWS ITEM" in user and self.use_news) else "no"
            text = (f"<post>{GOOD}</post><verify>none</verify><hook_idea>none</hook_idea>"
                    f"<news_used>{used}</news_used><why>strong</why>")
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}

    # ---- postgrest (just the subset _store uses)
    def db(self, method, url, body, headers):
        assert headers.get("x-bot-secret") == "s", "DB secret header missing"
        u = urlparse(url)
        table = u.path.rsplit("/", 1)[1]
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        rows = self.tables[table]
        filters = {k: v for k, v in qs.items() if k not in ("select", "order", "limit", "on_conflict")}

        def match(r):
            for k, v in filters.items():
                op, val = v.split(".", 1)
                if op == "eq" and str(r.get(k)) != val:
                    return False
            return True

        if method == "GET":
            out = [dict(r) for r in rows if match(r)]
            if "order" in qs:
                for part in reversed(qs["order"].split(",")):
                    col, _, d = part.partition(".")
                    out.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=(d == "desc"))
            return out[: int(qs["limit"])] if "limit" in qs else out
        if method == "POST":
            row = dict(body)
            if table == "processed_updates":
                if any(r["update_id"] == row["update_id"] for r in rows):
                    raise _http.HttpError(409, "dup", url)
            if table == "kv":
                rows[:] = [r for r in rows if r["k"] != row["k"]]
            if table == "notes":
                if any(r["text"] == row["text"] for r in rows):
                    return []
                row.update(status="new", score=None, pillar=None, triage=None, created_at=len(rows))
            if table == "drafts":
                row.setdefault("status", "pending")
            if table in self.ids:
                self.ids[table] += 1
                row["id"] = self.ids[table]
            rows.append(row)
            return [dict(row)]
        if method == "PATCH":
            for r in rows:
                if match(r):
                    r.update(body)
            return None
        if method == "DELETE":
            rows[:] = [r for r in rows if not match(r)]
            return None


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    for mod in (_http, store, tg, brain):
        monkeypatch.setattr(mod, "request", f.request, raising=False)
    brain._endpoint = brain._model = None
    return f


def texts(f):
    return [m["text"] for m in f.sent]


# ------------------------------------------------------------------ lint / parsing
def test_lint_clean_post():
    assert brain.lint(GOOD) == []


def test_lint_catches_off_voice():
    bad = GOOD + "\n\nThis serum is a game-changer for your glow! 🌟 #skincare — agree?"
    probs = " ".join(brain.lint(bad))
    for needle in ["emoji", "exclamation", "hashtags", "dash", "game-changer", "glow", "question"]:
        assert needle in probs, needle


def test_lint_us_spelling_and_length():
    probs = " ".join(brain.lint("Our moisturizer will oxidize. " * 5))
    assert "moisturiser" in probs and "oxidise" in probs and "Too short" in probs
    assert "Too long" in " ".join(brain.lint("a" * 3100))


def test_parse_draft_and_json():
    d = brain.parse_draft("<post>\nHi.\n</post><verify>\n- confirm batch pH\n</verify><hook_idea>none</hook_idea><why>w</why>")
    assert d["post"] == "Hi." and d["verify"] == ["confirm batch pH"] and d["hook_idea"] == ""
    assert brain.parse_json("garbage")["score"] == 0
    assert brain.parse_json('x {"verdict":"develop","score":8} y')["score"] == 8


def test_hard_reject_overrides(monkeypatch):
    monkeypatch.setattr(brain, "_call", lambda *a, **k: '{"score":9,"hard_reject":"names a customer"}')
    t = brain.triage("x")
    assert t["verdict"] == "reject" and t["score"] <= 3


def test_repair_pass(monkeypatch):
    calls = []

    def fake_call(system, user, max_tokens=0, json_mode=False):
        calls.append(system)
        if system is brain.REPAIR_SYSTEM:
            return f"<post>{GOOD}</post>"
        return f"<post>{GOOD} Amazing!</post><verify>none</verify><hook_idea>none</hook_idea><why>w</why>"

    monkeypatch.setattr(brain, "_call", fake_call)
    d = brain.make_draft("note", {}, find_news=False)
    assert len(calls) == 2 and d["lint"] == []


def test_split_notes():
    assert len(store.split_notes((ROOT / "notes/sample_notes.txt").read_text())) == 10


# ------------------------------------------------------------------ gemini transport
def test_gemini_falls_back_on_unknown_model(monkeypatch, fake):
    seen = []

    def req(method, url, body=None, headers=None, timeout=60, raw=False):
        seen.append(url)
        if brain.MODEL in url:
            raise _http.HttpError(404, "not found", url)
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    monkeypatch.setattr(brain, "request", req)
    assert brain._call("s", "u") == "ok" and brain.active_model() != brain.MODEL


def test_gemini_switches_to_vertex_for_express_keys(monkeypatch, fake):
    def req(method, url, body=None, headers=None, timeout=60, raw=False):
        if "generativelanguage" in url:
            raise _http.HttpError(401, "API keys are not supported", url)
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    monkeypatch.setattr(brain, "request", req)
    assert brain._call("s", "u") == "ok" and "aiplatform" in brain._endpoint


# ------------------------------------------------------------------ end-to-end flows
def test_channel_note_to_approved_post(fake):
    bot.handle_update({"update_id": 1, "channel_post": {"message_id": 5, "chat": {"id": -1001, "title": "notes"},
                                                        "text": "Factory today: batch 14 pH drift mid-run"}})
    # a note worth a post is drafted straight away - no /draft needed
    assert "Drafting it now" in texts(fake)[0]
    assert len(fake.tables["drafts"]) == 1 and fake.tables["drafts"][0]["status"] == "pending"
    assert texts(fake)[-2].startswith(GOOD)              # the post itself, alone, easy to copy
    assert "inline_keyboard" in fake.sent[-1]["reply_markup"]

    bot.handle_update({"update_id": 3, "callback_query": {"id": "c", "from": {"id": 42}, "data": "approve:1",
                                                          "message": {"chat": {"id": 42}, "message_id": 9}}})
    assert fake.tables["drafts"][0]["status"] == "approved"
    assert fake.tables["notes"][0]["status"] == "used"
    assert texts(fake)[-1].startswith(GOOD) and "[VERIFY NEWS:" in texts(fake)[-1]


def test_channel_note_answered_in_channel_even_if_owner_never_pressed_start(fake):
    fake.blocked.add(42)                     # owner never pressed Start: DMs are refused
    bot.handle_update({"update_id": 50, "channel_post": {"message_id": 5, "chat": {"id": -1001, "title": "notes"},
                                                         "text": "Batch fourteen pH drift at mid-run sampling"}})
    assert len(fake.tables["drafts"]) == 1
    assert all(m["chat_id"] == -1001 for m in fake.sent) and any(t.startswith(GOOD) for t in texts(fake))
    # Redraft pressed in the channel, feedback posted in the channel
    bot.handle_update({"update_id": 51, "callback_query": {"id": "c", "from": {"id": 42}, "data": "redraft:1",
                                                           "message": {"chat": {"id": -1001, "type": "channel"}, "message_id": 9}}})
    bot.handle_update({"update_id": 52, "channel_post": {"message_id": 6, "chat": {"id": -1001}, "text": "make it shorter please"}})
    assert [d["status"] for d in fake.tables["drafts"]] == ["superseded", "pending"]
    assert len(fake.tables["notes"]) == 1    # the feedback was not stored as a new note


def test_dm_falls_back_to_channel(fake):
    fake.blocked.add(42)
    bot._reply_to = None
    bot.say("hello")
    assert fake.sent[-1]["chat_id"] == -1001


def test_news_item_used_gets_verify_flag_and_is_shown(fake):
    bot.capture("Batch fourteen pH drift after the supplier changed the preservative blend", "channel")
    assert "%22pH%20drift%22%20cosmetics" in fake.news_queries[0]             # exact phrase from the note
    assert "NEWS ITEM" in fake.last_user and "Example Times" in fake.last_user    # given to the drafter
    assert "genuinely relevant" in fake.last_user and "ignore it" in fake.last_user
    d = fake.tables["drafts"][0]
    assert d["body"].rstrip().endswith("https://news.google.com/rss/articles/abc]")   # verify flag at the end
    assert "[VERIFY NEWS:" in d["body"] and d["meta"]["news"]["source"] == "Example Times"
    checks = texts(fake)[-1]
    assert "Keywords from your note: pH drift, preservative blend, certificate of analysis" in checks
    assert "News used (Google News" in checks


def test_two_repair_passes_for_length(monkeypatch):
    calls = []

    def fake_call(system, user, max_tokens=0, json_mode=False):
        calls.append(system)
        if system is brain.REPAIR_SYSTEM:
            return f"<post>{'x' * 3100 if len(calls) == 2 else GOOD}</post>"
        return f"<post>{'y' * 3200}</post><verify>none</verify>"

    monkeypatch.setattr(brain, "_call", fake_call)
    d = brain.make_draft("note", {}, find_news=False)
    assert len(calls) == 3 and d["post"] == GOOD and d["lint"] == []


def test_news_ignored_when_it_doesnt_fit(fake):
    fake.use_news = False
    bot.capture("Batch fourteen pH drift after the supplier changed the preservative blend", "channel")
    d = fake.tables["drafts"][0]
    assert "[VERIFY NEWS:" not in d["body"] and "News found but not used" in texts(fake)[-1]


def test_no_news_when_google_news_is_empty(fake):
    fake.no_news = True
    bot.capture("Batch fourteen pH drift after the supplier changed the preservative blend", "channel")
    assert fake.tables["drafts"][0]["meta"]["news"] is None
    assert len(fake.news_queries) >= 2                          # retried with fewer keywords
    assert "no Google News result" in texts(fake)[-1] and "NEWS ITEM" not in fake.last_user


def test_fetch_news_parses_top_result(fake):
    n = brain.fetch_news("cosmetic preservative change")
    assert n["headline"] == "Regulator tightens cosmetic preservative rules" and n["source"] == "Example Times"
    assert n["summary"].startswith("New labelling rules") and n["url"].startswith("https://news.google.com")


def test_lint_flags_formal_and_copied_text():
    formal = ("It is true that it is not simple. I am sure we are not done and it does not help. " * 12)
    assert any("Too formal" in p for p in brain.lint(formal))
    copied = GOOD + " Most serums don't list their pH on the label. This is legal. It is also not helpful."
    assert any("Copies a phrase" in p for p in brain.lint(copied))


def test_generic_keywords_are_dropped():
    kws = brain.clean_keywords(["ingredients", "Regulations", "preservative blend change", "finished product pH",
                                "skincare", "a very long keyword phrase that goes on", "Finished product pH", "CDSCO"])
    assert kws == ["preservative blend change", "finished product pH", "CDSCO"]


def test_voice_files_load():
    import _voice
    assert "Contractions, always" in _voice.VOICE_GUIDE
    assert "newsletter_011" in _voice.NEWSLETTERS and "linkedin_post_004" in _voice.LINKEDIN_POSTS


def test_redraft_uses_feedback(fake):
    bot.capture("A note about airless pumps and stability data", "dm")   # auto-drafts draft #1
    bot.handle_update({"update_id": 9, "callback_query": {"id": "c", "from": {"id": 42}, "data": "redraft:1",
                                                          "message": {"chat": {"id": 42}, "message_id": 9}}})
    bot.handle_update({"update_id": 10, "message": {"chat": {"id": 42, "type": "private"}, "text": "shorter"}})
    assert [d["status"] for d in fake.tables["drafts"]] == ["superseded", "pending"]
    assert fake.tables["drafts"][1]["version"] == 2


def test_strangers_and_other_channels_ignored(fake):
    bot.handle_update({"message": {"chat": {"id": 7, "type": "private"}, "text": "hello"}})
    assert texts(fake) == ["This is a private bot."]
    bot.handle_update({"channel_post": {"chat": {"id": -999}, "text": "someone else's channel post"}})
    assert fake.tables["notes"] == []


def test_duplicate_update_detected(fake):
    assert store.first_time_seeing(77) is True
    assert store.first_time_seeing(77) is False


def test_scheduled_run_respects_days_and_pending_limit(fake, monkeypatch):
    import datetime as dt
    monkeypatch.setattr(bot, "AUTO_DRAFT", False)
    tue = dt.datetime(2026, 9, 29, 9, tzinfo=bot.TZ)
    mon = dt.datetime(2026, 9, 28, 9, tzinfo=bot.TZ)
    bot.capture("Note one: pH drift across batch fourteen", "dm")
    assert bot.scheduled_run(tue)["draft"] == "not a drafting day"
    assert bot.scheduled_run(mon)["draft"].startswith("drafted")
    for i in range(3):
        fake.tables["drafts"].append({"id": 100 + i, "note_id": 1, "status": "pending", "body": "x", "meta": {}})
    assert bot.scheduled_run(mon)["draft"] == "skipped: pending"


def test_score_threshold_decides(monkeypatch):
    for score, verdict in ((6, "develop"), (5, "reject"), (9, "develop"), (0, "reject")):
        monkeypatch.setattr(brain, "_call", lambda *a, _s=score, **k: json.dumps({"score": _s, "reason": "r"}))
        assert brain.triage("x")["verdict"] == verdict, score


def test_low_score_note_gets_reason_and_no_draft(fake, monkeypatch):
    monkeypatch.setattr(brain, "triage", lambda text: {"verdict": "reject", "score": 1,
                                                         "reason": "A task reminder, not an idea."})
    bot.capture("call supplier re: invoice tmrw", "channel")
    assert fake.tables["drafts"] == []
    assert "No draft for note #1 - it scored 1/10. A task reminder, not an idea." in texts(fake)[-1]


def test_import_file_batches_triage(fake):
    bot.handle_update({"message": {"chat": {"id": 42, "type": "private"},
                                   "document": {"file_name": "notes.txt", "file_id": "f"}}})
    assert len(fake.tables["notes"]) == 2 and all(n["status"] == "develop" for n in fake.tables["notes"])
    assert "Imported 2 new notes" in " ".join(texts(fake))


def test_secrets_never_in_repo():
    key_like = re.compile(r"\d{9,10}:AA[\w-]{30,}|AQ\.[\w-]{30,}|AIza[\w-]{30,}|sb_secret_[a-zA-Z0-9]{10,}")
    for p in ROOT.rglob("*"):
        if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts and p.suffix != ".pyc":
            assert not key_like.search(p.read_text(errors="ignore")), p
