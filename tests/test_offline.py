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

GOOD = ("The niacinamide serum you're using probably has the ingredient listed at 5% on the label. "
        "That number is almost certainly meaningless without knowing the pH. " * 12).strip()


class Fake:
    """In-memory stand-in for PostgREST, the Telegram Bot API and Gemini."""

    def __init__(self):
        self.tables = {"notes": [], "drafts": [], "kv": [], "processed_updates": []}
        self.ids = {"notes": 0, "drafts": 0}
        self.sent = []
        self.gemini_calls = []

    # ---- routing
    def request(self, method, url, body=None, headers=None, timeout=60, raw=False):
        if "api.telegram.org" in url:
            return self.telegram(url, body or {}, raw)
        if "googleapis.com" in url:
            return self.gemini(url, body)
        return self.db(method, url, body, headers or {})

    # ---- telegram
    def telegram(self, url, body, raw):
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
        if body["generationConfig"].get("responseMimeType") == "application/json":
            text = json.dumps({"verdict": "develop", "score": 8, "pillar": "Formulation Science",
                               "angle": "mid-batch sampling catches drift", "hook": "batch 14", "reason": "specific",
                               "needs_from_meera": [], "hard_reject": None})
        else:
            text = f"<post>{GOOD}</post><verify>none</verify><hook_idea>none</hook_idea><why>strong</why>"
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
    assert brain.parse_json("garbage")["verdict"] == "hold"
    assert brain.parse_json('x {"verdict":"develop","score":8} y')["score"] == 8


def test_hard_reject_overrides(monkeypatch):
    monkeypatch.setattr(brain, "_call", lambda *a, **k: '{"verdict":"develop","score":9,"hard_reject":"names a customer"}')
    assert brain.triage("x")["verdict"] == "reject"


def test_repair_pass(monkeypatch):
    calls = []

    def fake_call(system, user, max_tokens=0, json_mode=False):
        calls.append(system)
        if system is brain.REPAIR_SYSTEM:
            return f"<post>{GOOD}</post>"
        return f"<post>{GOOD} Amazing!</post><verify>none</verify><hook_idea>none</hook_idea><why>w</why>"

    monkeypatch.setattr(brain, "_call", fake_call)
    d = brain.make_draft("note", {})
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
    assert fake.tables["notes"][0]["status"] == "develop"
    assert "worth a post" in texts(fake)[-1]

    bot.handle_update({"update_id": 2, "message": {"chat": {"id": 42, "type": "private"}, "text": "/draft"}})
    assert fake.tables["drafts"][0]["status"] == "pending"
    assert texts(fake)[-2] == GOOD                       # the post itself, alone, easy to copy
    assert "inline_keyboard" in fake.sent[-1]["reply_markup"]

    bot.handle_update({"update_id": 3, "callback_query": {"id": "c", "from": {"id": 42}, "data": "approve:1",
                                                          "message": {"chat": {"id": 42}, "message_id": 9}}})
    assert fake.tables["drafts"][0]["status"] == "approved"
    assert fake.tables["notes"][0]["status"] == "used"
    assert texts(fake)[-1] == GOOD


def test_redraft_uses_feedback(fake):
    bot.capture("A note about airless pumps and stability data", "dm")
    bot.deliver_draft()
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


def test_scheduled_run_respects_days_and_pending_limit(fake):
    import datetime as dt
    tue = dt.datetime(2026, 9, 29, 9, tzinfo=bot.TZ)
    mon = dt.datetime(2026, 9, 28, 9, tzinfo=bot.TZ)
    bot.capture("Note one: pH drift across batch fourteen", "dm")
    assert bot.scheduled_run(tue)["draft"] == "not a drafting day"
    assert bot.scheduled_run(mon)["draft"].startswith("drafted")
    for i in range(3):
        fake.tables["drafts"].append({"id": 100 + i, "note_id": 1, "status": "pending", "body": "x", "meta": {}})
    assert bot.scheduled_run(mon)["draft"] == "skipped: pending"


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
