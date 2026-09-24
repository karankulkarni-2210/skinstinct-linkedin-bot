"""GET /api/setup?key=CRON_SECRET - registers the webhook and the command menu with Telegram."""
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import os

from _web import Base, secret_ok

import _tg

COMMANDS = [
    ("draft", "Draft a post from the best note"), ("backlog", "Notes worth a post"),
    ("note", "Show a note: /note 12"), ("triage", "Score notes still waiting"),
    ("stats", "Counts and status"), ("pause", "Pause scheduled drafts"),
    ("resume", "Resume scheduled drafts"), ("help", "What I can do"),
]


class handler(Base):
    def do_GET(self):
        if not secret_ok(self.query().get("key"), "CRON_SECRET"):
            return self.reply(401, {"ok": False})
        try:
            host = self.headers.get("x-forwarded-host") or self.headers.get("host")
            url = f"https://{host}/api/telegram"
            _tg.call("setWebhook", url=url, secret_token=os.environ["WEBHOOK_SECRET"],
                     allowed_updates=["message", "channel_post", "callback_query"], drop_pending_updates=True)
            _tg.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])
            me = _tg.call("getMe")
            return self.reply(200, {"ok": True, "webhook": url, "bot": "@" + me["username"],
                                    "webhook_info": _tg.call("getWebhookInfo")})
        except Exception as e:
            return self.fail(e)
