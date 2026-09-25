"""Loads Meera's voice files at runtime from voice/ (bundled via vercel.json includeFiles).

voice/voice-skill.txt  - the voice skill: rules, signature moves, safe published facts
voice/exemplars.md     - her 4 LinkedIn posts, verbatim
voice/newsletters.md   - her 11 newsletters, verbatim
Edit those files; nothing to regenerate.
"""
from pathlib import Path

_CANDIDATES = [Path(__file__).resolve().parent.parent / "voice", Path.cwd() / "voice", Path("/var/task/voice")]


def _read(name):
    for base in _CANDIDATES:
        p = base / name
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError(f"voice/{name} not found in {[str(c) for c in _CANDIDATES]}")


VOICE_GUIDE = _read("voice-skill.txt")
LINKEDIN_POSTS = _read("exemplars.md")
NEWSLETTERS = _read("newsletters.md")
EXEMPLARS = LINKEDIN_POSTS + "\n\n" + NEWSLETTERS
