"""Minimal Telegram Bot API client (stdlib only)."""
import os

from _http import request


def _url(method):
    return f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{method}"


def call(method, **params):
    out = request("POST", _url(method), body={k: v for k, v in params.items() if v is not None}, timeout=30)
    return out.get("result") if isinstance(out, dict) else out


def send(chat_id, text, reply_markup=None):
    """Plain text (no parse mode, so nothing needs escaping), split at Telegram's 4096-char limit."""
    msg = None
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [""]
    for i, chunk in enumerate(chunks):
        msg = call("sendMessage", chat_id=chat_id, text=chunk,
                   reply_markup=reply_markup if i == len(chunks) - 1 else None,
                   link_preview_options={"is_disabled": True})
    return msg


def download_file(file_id):
    path = call("getFile", file_id=file_id)["file_path"]
    return request("GET", f"https://api.telegram.org/file/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{path}", raw=True)
