# ---------------------------------------------------------------------------
# TTS Configuration
# All tunable constants live here. No logic, no imports.
# ---------------------------------------------------------------------------

# ── Audio output ───────────────────────────────────────────────────────────
SAMPLE_RATE = 24000          # Hz — LiveKit and VoIP compatible
CHANNELS = 1                 # mono required for telephony

# Percentage offset from neutral pace. "+8%" ensures snappy, energetic speaking pace.
EDGE_SPEECH_RATE = "+8%"

# ── Text preprocessing ────────────────────────────────────────────────────
MAX_TEXT_LENGTH = 500         # characters — truncate beyond this

# ── Feature flags ─────────────────────────────────────────────────────────
ENABLE_LANGUAGE_AUTO_DETECT = True
MAX_SENTENCES = 2
SENTENCE_PAUSE_MS = 80
