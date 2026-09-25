"""GET /api/health?key=CRON_SECRET - checks Telegram, the channel, Supabase and Gemini end to end."""
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import os

from _web import Base, secret_ok

import _brain
import _voice
import _store
import _tg


def check(fn):
    try:
        return {"ok": True, "detail": fn()}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {str(e)[:300]}"}


class handler(Base):
    def do_GET(self):
        if not secret_ok(self.query().get("key"), "CRON_SECRET"):
            return self.reply(401, {"ok": False})
        cid = os.getenv("NOTES_CHANNEL_ID")
        out = {
            "telegram": check(lambda: "@" + _tg.call("getMe")["username"]),
            "channel": check(lambda: {k: v for k, v in _tg.call("getChat", chat_id=int(cid)).items()
                                      if k in ("id", "title", "type")} if cid else "not set"),
            "bot_is_channel_admin": check(lambda: _tg.call("getChatMember", chat_id=int(cid),
                                                           user_id=_tg.call("getMe")["id"])["status"] if cid else "not set"),
            "webhook": check(lambda: {k: v for k, v in _tg.call("getWebhookInfo").items()
                                      if k in ("url", "pending_update_count", "last_error_message")}),
            "database": check(lambda: _store.counts()),
            "voice_skill": check(lambda: {"voice-skill.txt": len(_voice.VOICE_GUIDE), "linkedin_posts": len(_voice.LINKEDIN_POSTS),
                                          "newsletters": len(_voice.NEWSLETTERS)}),
            "gemini": check(lambda: {"reply": _brain._call("Reply with exactly: ok", "ping", 50).strip()[:40],
                                     "model": _brain.active_model(), "endpoint": _brain._endpoint}),
        }
        out["all_ok"] = all(v["ok"] for v in out.values())
        return self.reply(200, out)
