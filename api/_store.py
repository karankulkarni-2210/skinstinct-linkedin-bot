"""Persistence on Supabase (Postgres) through its REST API.

Access rule: every table has row-level security. The bot's requests carry an `x-bot-secret`
header that must match a value stored in a private, non-API schema - without it the publishable
key sees nothing and can write nothing.
"""
import os
import urllib.parse

from _http import HttpError, request

URL = os.getenv("SUPABASE_URL", "").rstrip("/") + "/rest/v1"


def _h(extra=None):
    key = os.getenv("SUPABASE_KEY", "")
    h = {"apikey": key, "Authorization": f"Bearer {key}" if key.startswith("eyJ") else "",
         "x-bot-secret": os.getenv("DB_SECRET", "")}
    h = {k: v for k, v in h.items() if v}
    h.update(extra or {})
    return h


def _get(table, query):
    return request("GET", f"{URL}/{table}?{query}", headers=_h()) or []


def _post(table, row, prefer="return=representation", query=""):
    return request("POST", f"{URL}/{table}{query}", body=row, headers=_h({"Prefer": prefer})) or []


def _patch(table, match, fields):
    return request("PATCH", f"{URL}/{table}?{match}", body=fields, headers=_h({"Prefer": "return=minimal"}))


def q(v):
    return urllib.parse.quote(str(v), safe="")


# ---------- kv ----------
def kv_get(k, default=None):
    rows = _get("kv", f"k=eq.{q(k)}&select=v")
    return rows[0]["v"] if rows else default


def kv_set(k, v):
    if v is None:
        request("DELETE", f"{URL}/kv?k=eq.{q(k)}", headers=_h())
    else:
        _post("kv", {"k": k, "v": str(v)}, prefer="resolution=merge-duplicates,return=minimal")


# ---------- dedupe Telegram retries ----------
def first_time_seeing(update_id):
    """True the first time an update_id is seen. Telegram retries slow webhooks; this stops double work."""
    try:
        _post("processed_updates", {"update_id": update_id}, prefer="return=minimal")
        return True
    except HttpError as e:
        if e.status == 409:
            return False
        raise


# ---------- notes ----------
def add_note(text, source, tg_message_id=None):
    """Insert a note. Returns new id, or None if the exact text already exists."""
    text = (text or "").strip()
    if not text:
        return None
    rows = _post("notes", {"text": text, "source": source, "tg_message_id": tg_message_id},
                 prefer="resolution=ignore-duplicates,return=representation", query="?on_conflict=text_hash")
    return rows[0]["id"] if rows else None


def set_triage(note_id, triage):
    verdict = triage.get("verdict") if triage.get("verdict") in ("develop", "hold", "reject") else "hold"
    _patch("notes", f"id=eq.{note_id}", {"status": verdict, "score": int(triage.get("score") or 0),
                                         "pillar": triage.get("pillar"), "triage": triage})


def set_note_status(note_id, status):
    _patch("notes", f"id=eq.{note_id}", {"status": status})


def get_note(note_id):
    rows = _get("notes", f"id=eq.{note_id}&select=*")
    return _note(rows[0]) if rows else None


def _note(r):
    r["triage"] = r.get("triage") or {}
    return r


def untriaged_notes(limit=8):
    return [_note(r) for r in _get("notes", f"status=eq.new&order=id.asc&limit={limit}&select=*")]


def backlog(limit=10):
    return [_note(r) for r in _get("notes", f"status=eq.develop&order=score.desc,created_at.desc&limit={limit}&select=*")]


def pick_next_note():
    """Highest-scoring 'develop' note, preferring a different pillar from the last draft
    when a candidate within 1 point of the top exists."""
    candidates = backlog(limit=5)
    if not candidates:
        return None
    last = last_drafted_pillar()
    top = candidates[0]
    if last and top.get("pillar") == last:
        for n in candidates[1:]:
            if n.get("pillar") != last and (n["score"] or 0) >= (top["score"] or 0) - 1:
                return n
    return top


def last_drafted_pillar():
    rows = _get("drafts", "order=id.desc&limit=1&select=note_id")
    if not rows:
        return None
    n = get_note(rows[0]["note_id"])
    return n.get("pillar") if n else None


def counts():
    notes, drafts = {}, {}
    for r in _get("notes", "select=status"):
        notes[r["status"]] = notes.get(r["status"], 0) + 1
    for r in _get("drafts", "select=status"):
        drafts[r["status"]] = drafts.get(r["status"], 0) + 1
    return notes, drafts


# ---------- drafts ----------
def add_draft(note_id, body, meta, version=1):
    rows = _post("drafts", {"note_id": note_id, "version": version, "body": body, "meta": meta})
    set_note_status(note_id, "drafted")
    return rows[0]["id"]


def get_draft(draft_id):
    rows = _get("drafts", f"id=eq.{draft_id}&select=*")
    if not rows:
        return None
    rows[0]["meta"] = rows[0].get("meta") or {}
    return rows[0]


def set_draft_status(draft_id, status):
    _patch("drafts", f"id=eq.{draft_id}", {"status": status})


def pending_draft_count():
    return len(_get("drafts", "status=eq.pending&select=id"))


def split_notes(raw):
    """Split a bulk-import text file into notes: on lines of '---' if present, else on blank lines."""
    import re
    if re.search(r"^\s*---\s*$", raw, re.M):
        parts = re.split(r"^\s*---\s*$", raw, flags=re.M)
    else:
        parts = raw.split("\n\n")
    return [p.strip() for p in parts if len(p.strip()) > 15]
