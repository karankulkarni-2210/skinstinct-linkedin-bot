"""GET /api/cron - daily run from Vercel Cron (Authorization: Bearer $CRON_SECRET)."""
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _web import Base, secret_ok

import _bot


class handler(Base):
    def do_GET(self):
        auth = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
        if not secret_ok(auth, "CRON_SECRET") and not secret_ok(self.query().get("key"), "CRON_SECRET"):
            return self.reply(401, {"ok": False})
        try:
            if self.query().get("draft_now") == "1":   # manual trigger: draft the best waiting note now
                return self.reply(200, {"ok": True, "draft": _bot.deliver_draft(note_id=int(self.query()["note"])
                                                                                  if self.query().get("note", "").isdigit() else None)})
            return self.reply(200, {"ok": True, **_bot.scheduled_run()})
        except Exception as e:
            return self.fail(e)
