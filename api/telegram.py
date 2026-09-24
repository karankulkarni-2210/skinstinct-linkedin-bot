"""POST /api/telegram - Telegram webhook."""
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _web import Base, secret_ok

import _bot
import _store


class handler(Base):
    def do_POST(self):
        if not secret_ok(self.headers.get("X-Telegram-Bot-Api-Secret-Token"), "WEBHOOK_SECRET"):
            return self.reply(401, {"ok": False})
        update = self.body_json()
        try:
            if update.get("update_id") is not None and not _store.first_time_seeing(update["update_id"]):
                return self.reply(200, {"ok": True, "duplicate": True})
            _bot.handle_update(update)
        except Exception as e:  # always 200 so Telegram doesn't retry forever; tell the owner instead
            import traceback
            traceback.print_exc()
            try:
                _bot.say(f"Something went wrong on my side ({type(e).__name__}). Your note is saved if it "
                         "reached me - try /draft again in a minute.")
            except Exception:
                pass
        return self.reply(200, {"ok": True})

    def do_GET(self):
        return self.reply(200, {"ok": True, "hint": "Telegram posts updates here."})
