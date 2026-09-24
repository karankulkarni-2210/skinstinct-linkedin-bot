"""Tiny stdlib HTTP helper - no third-party packages, so nothing to install on Vercel."""
import json
import urllib.error
import urllib.request


class HttpError(Exception):
    def __init__(self, status, body, url):
        super().__init__(f"HTTP {status} from {url.split('?')[0]}: {body[:300]}")
        self.status, self.body = status, body


def request(method, url, body=None, headers=None, timeout=60, raw=False):
    data = None
    h = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode(errors="ignore"), url) from None
    if raw:
        return payload
    text = payload.decode(errors="ignore")
    return json.loads(text) if text.strip() else None
