"""Regenerate api/_voice.py from voice/*.md (Vercel bundles .py files reliably, so the voice lives in Python)."""
from pathlib import Path
root = Path(__file__).resolve().parent.parent
g = (root / "voice/voice_guide.md").read_text(encoding="utf-8")
e = (root / "voice/exemplars.md").read_text(encoding="utf-8")
(root / "api/_voice.py").write_text(
    '"""Meera\'s voice reference. Generated from voice/*.md - edit those, then run: python scripts/build_voice.py"""\n'
    f"VOICE_GUIDE = {g!r}\n\nEXEMPLARS = {e!r}\n", encoding="utf-8")
print("api/_voice.py rebuilt")
