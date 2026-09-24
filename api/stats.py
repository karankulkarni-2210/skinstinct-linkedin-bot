"""GET /api/stats - public, aggregate counts only (no note text), for the project page."""
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _web import Base

import _store


class handler(Base):
    def do_GET(self):
        try:
            notes, drafts = _store.counts()
            return self.reply(200, {"ok": True, "notes": notes, "drafts": drafts})
        except Exception as e:
            return self.reply(200, {"ok": False, "error": type(e).__name__})
