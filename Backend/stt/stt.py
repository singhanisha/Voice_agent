"""Compatibility wrapper for the active STT provider.

`flows.runtime` imports this module directly, so it remains the stable public
entrypoint while provider selection lives behind `stt.provider`.
"""

from .provider import transcribe_audio

__all__ = ["transcribe_audio"]
