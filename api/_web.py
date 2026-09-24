"""Shared plumbing for the Vercel Python functions (BaseHTTPRequestHandler style, stdlib only)."""
import hmac
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(__file__))


def secret_ok(given, env_name):
    expected = os.getenv(env_name, "")
    return bool(expected) and hmac.compare_digest(given or "", expected)


class Base(BaseHTTPRequestHandler):
    def reply(self, status, obj):
        body = json.dumps(obj, indent=2, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def query(self):
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def fail(self, e):
        traceback.print_exc()
        self.reply(500, {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"})

    def log_message(self, *a):  # keep Vercel logs quiet
        pass
