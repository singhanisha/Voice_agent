# ---------------------------------------------------------------------------
# Text-to-Speech (TTS) module
# This file exposes the public interface for speech synthesis.
#
# To swap TTS engines, change the import below to point at the new
# implementation file.  The rest of the pipeline depends only on the
# generate_speech(text: str) -> bytes signature.
# ---------------------------------------------------------------------------

from tts.provider import generate_speech_stream

def check_voice_assets():
    # Edge TTS requires no local assets!
    pass

__all__ = [
    "check_voice_assets",
    "generate_speech_stream"
]
