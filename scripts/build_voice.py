"""Prints what the bot will load as Meera's voice. The voice is read at runtime from voice/, so there
is nothing to build - edit voice/voice-skill.txt, voice/exemplars.md or voice/newsletters.md directly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
import _voice  # noqa: E402

for name in ("VOICE_GUIDE", "LINKEDIN_POSTS", "NEWSLETTERS"):
    print(f"{name}: {len(getattr(_voice, name)):,} characters")
