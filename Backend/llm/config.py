""" 
LLM configuration constants.

All tunable parameters for the LLM (Groq) integration.
Change values here to adjust model, temperature, token limits, etc.
without touching any logic code.

Set GROQ_API_KEY via the environment variable:
  Windows:  $env:GROQ_API_KEY = "your_key_here"
  Linux:    export GROQ_API_KEY="your_key_here"


import os
from dotenv import load_dotenv

load_dotenv() # Load variables from .env if it exists

# ── API ────────────────────────────────────────────────────────────────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

# ── Model ──────────────────────────────────────────────────────────────────────
# Available Groq models (fast, free-tier):
#   "llama-3.1-8b-instant"   - fastest, ~40% lower latency, ideal for voice turns
#   "llama-3.3-70b-versatile" - most capable, used for complex/greeting turns
#   "mixtral-8x7b-32768" - 32k context
MODEL_NAME: str = "openai/gpt-oss-20b"

# ── Generation Parameters ──────────────────────────────────────────────────────
TEMPERATURE: float = 0.35      # balanced: not robotic (0.0) but not hallucinating (>0.7)
MAX_TOKENS: int = 90           # enough for complete 1-3 sentence voice turns, keeps latency low
TOP_P: float = 0.9             # slight nucleus cap for consistency

# ── Retry & Timeout ────────────────────────────────────────────────────────────
REQUEST_TIMEOUT_S: int = 6     # tighter: 8→6s to fail fast and not block the pipeline
MAX_RETRIES: int = 2           # Number of retries on transient failure

# Runtime response shaping
MAX_HISTORY_MESSAGES: int = 10  # trimmed from 12 for faster context processing
MAX_RESPONSE_SENTENCES: int = 2
MAX_RESPONSE_WORDS: int = 30

ENABLE_SINGLE_CALL_FAST_PATH: bool = True

# Phrase-constrained LLM response composition
PHRASE_RESPONSE_MAX_TOKENS: int = 120
PHRASE_RESPONSE_TEMPERATURE: float = 0.70

# ── Supported Languages ────────────────────────────────────────────────────────
# Matches the STT module's language support
SUPPORTED_LANGUAGES: list[str] = ["en", "hi", "mr", "hinglish"]
DEFAULT_LANGUAGE: str = "en"
 """

"""
LLM configuration for Groq Cloud API.

IMPORTANT:
This project uses Groq's hosted API only.
No local LLM/model is loaded or executed.

Set GROQ_API_KEY in Backend/.env
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Groq API ────────────────────────────────────────────────────────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "").strip()

# ── Model ───────────────────────────────────────────────────────────────────
# Groq Cloud hosted model.
# This is NOT a local model.
#MODEL_NAME: str = "llama-3.1-8b-instant"
MODEL_NAME: str = "openai/gpt-oss-20b"

# FIX B1: gpt-oss-20b is a REASONING model. Its reasoning tokens eat the
# max_tokens budget and message.content comes back EMPTY for short voice
# replies. Intent extraction survives because it passes reasoning_effort="low"
# and max_completion_tokens=1024; the spoken-response call did not.
# Use a plain (non-reasoning) model for spoken responses — also much faster.
RESPONSE_MODEL_NAME: str = "llama-3.3-70b-versatile"

# ── Generation ──────────────────────────────────────────────────────────────
TEMPERATURE: float = 0.35
MAX_TOKENS: int = 90
TOP_P: float = 0.9

# ── Request / Retry ─────────────────────────────────────────────────────────
REQUEST_TIMEOUT_S: int = 8
MAX_RETRIES: int = 2

# ── Conversation ────────────────────────────────────────────────────────────
MAX_HISTORY_MESSAGES: int = 10
MAX_RESPONSE_SENTENCES: int = 2
MAX_RESPONSE_WORDS: int = 30

ENABLE_SINGLE_CALL_FAST_PATH: bool = False

# ── Phrase response ─────────────────────────────────────────────────────────
PHRASE_RESPONSE_MAX_TOKENS: int = 120
PHRASE_RESPONSE_TEMPERATURE: float = 0.70

# ── Supported languages ─────────────────────────────────────────────────────
SUPPORTED_LANGUAGES: list[str] = [
    "en",
    "hi",
    "mr",
    "hinglish",
]

DEFAULT_LANGUAGE: str = "en"