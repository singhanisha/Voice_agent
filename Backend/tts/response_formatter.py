"""Utilities for turning LLM text into stable, natural TTS input."""

import re

from tts.config import MAX_SENTENCES, MAX_TEXT_LENGTH


# Words that signal an incomplete sentence if they appear at the end
_TRAILING_CONNECTORS = {
    "and", "or", "but", "to", "for", "with", "the", "a", "an",
    "is", "are", "was", "were", "in", "on", "at", "of", "—", "-",
    "so", "then", "also", "like", "it's", "let", "me", "that",
}


def validate_completeness(text: str) -> str:
    """Ensure the response is a complete sentence. Fix or discard broken fragments.

    Catches:
    - Trailing connectors ("and", "or", "to", "for", "with")
    - Too-short fragments (< 3 words)
    - Missing terminal punctuation
    - Partial phrases ("it's a", "let me tell you")
    """
    if not text or not text.strip():
        return text

    cleaned = text.strip()

    # Strip trailing connectors and dangling words
    words = cleaned.split()
    while words and words[-1].lower().rstrip(".,;:-—") in _TRAILING_CONNECTORS:
        words.pop()

    if not words:
        return ""

    cleaned = " ".join(words)

    # Too-short fragment (less than 3 words) → likely broken
    if len(words) < 3 and "?" not in cleaned and "!" not in cleaned:
        return ""

    # Ensure terminal punctuation
    if cleaned and cleaned[-1] not in ".!?":
        # If it looks like a question, add ?
        question_starters = ("what", "which", "where", "when", "how", "who",
                             "are", "is", "do", "does", "can", "could", "would",
                             "kya", "kahan", "kaun", "kab", "kitna")
        first_word = words[0].lower()
        if first_word in question_starters:
            cleaned += "?"
        else:
            cleaned += "."

    return cleaned


def optimize_for_tts(text: str) -> str:
    """Keep TTS input short, clean, and rhythmically stable."""
    if not text:
        return ""

    try:
        from tts.speech_formatter import normalize_caps, normalize_fillers
        text = normalize_caps(text)
        text = normalize_fillers(text)
    except Exception:
        pass

    cleaned = text.strip()
    cleaned = cleaned.replace("\r", " ")
    cleaned = re.sub(r"[*_#`]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"([!?.,;:])\1+", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")

    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]
    if not sentences:
        sentences = [cleaned]

    deduped_sentences: list[str] = []
    seen: set[str] = set()
    for sentence in sentences:
        normalized = _normalize_sentence(sentence)
        if not normalized or normalized in seen:
            continue
        # Validate each sentence is complete before including
        validated = validate_completeness(sentence)
        if not validated:
            continue
        seen.add(normalized)
        deduped_sentences.append(validated)
        if len(deduped_sentences) >= MAX_SENTENCES:
            break

    final_text = " ".join(deduped_sentences).strip()
    if len(final_text) > MAX_TEXT_LENGTH:
        truncated = final_text[:MAX_TEXT_LENGTH]
        last_space = truncated.rfind(" ")
        if last_space > 0:
            truncated = truncated[:last_space]
        final_text = truncated.rstrip()

    if final_text and final_text[-1] not in ".!?":
        final_text += "."

    return final_text


def _normalize_sentence(text: str) -> str:
    normalized = text.casefold()
    normalized = re.sub(r"[^\w\s]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized

