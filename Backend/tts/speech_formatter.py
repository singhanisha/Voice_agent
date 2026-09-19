"""
Speech Formatter
================
This module transforms raw LLM output into speech-friendly text before TTS inference.
By adjusting spacing, punctuation, and sentence length, these transformations reduce robotic 
qualities, add conversational rhythm, and provide natural breathing room, significantly 
improving overall voice naturalness.
"""

import re
from tts.config import MAX_TEXT_LENGTH

ALLOWED_ACRONYMS = {
    "BHK", "RERA", "GST", "PIN", "USD", "INR", "EMI", "EMIS", "OTP",
    "ID", "TV", "IVR", "VIP", "AC", "IT", "AI", "NRI", "NH", "SQFT"
}


def normalize_caps(text: str) -> str:
    """
    Normalize ALL-CAPS words so TTS engines pronounce them naturally as words
    (e.g., 'WOW' -> 'Wow', 'REALLY' -> 'really') rather than spelling them out
    letter-by-letter (e.g., 'W O W') or producing sudden pitch spikes.
    Legitimate technical/domain acronyms (e.g. BHK, RERA, GST) are preserved.
    """
    if not text:
        return ""

    # Explicitly normalize variations of W.O.W. or WOW
    text = re.sub(r'(?:^|\b)W\.O\.W\.(?:$|\b)', 'Wow', text, flags=re.IGNORECASE)

    def _replace_caps_word(match: re.Match) -> str:
        word = match.group(0)
        # Preserve genuine domain acronyms
        if word.upper() in ALLOWED_ACRONYMS:
            return word
        if len(word) <= 1:
            return word
        start_idx = match.start()
        # If at sentence start, use Capitalized titlecase, else lowercase
        if start_idx == 0 or re.search(r'[.!?]\s*$', text[:start_idx]):
            return word.capitalize()
        return word.lower()

    return re.sub(r'\b[A-Z]{2,}\b', _replace_caps_word, text)


def normalize_fillers(text: str) -> str:
    """
    Standardize conversational fillers (Um, Ah, Uh, Well, Achha, Honestly, Listen, See)
    so TTS engines across all 3 providers speak them as warm human sounds followed by a micro-pause.
    Removes heavy ellipses ('Umm...') or dash artifacts that cause audio drops or pitch bursts.
    """
    if not text:
        return ""

    # 1. Standardize elongated filler spellings with a clean trailing comma
    text = re.sub(r'\bUmm+\b[.,;:]*', 'Um,', text, flags=re.IGNORECASE)
    text = re.sub(r'\bUhh+\b[.,;:]*', 'Uh,', text, flags=re.IGNORECASE)
    text = re.sub(r'\bAhh+\b[.,;:]*', 'Ah,', text, flags=re.IGNORECASE)
    text = re.sub(r'\bErr+\b[.,;:]*', 'Er,', text, flags=re.IGNORECASE)

    # 2. Convert heavy '...' after fillers/hedges to a gentle comma micro-pause
    filler_words = [
        "well", "honestly", "actually", "basically", "listen", "see",
        "right", "achha", "theek hai", "samajh gaya", "samajh gayi", "got it", "okay", "sure"
    ]
    for fw in filler_words:
        pattern = r'(\b' + re.escape(fw) + r')\s*\.\.\.\s*'
        text = re.sub(pattern, r'\1, ', text, flags=re.IGNORECASE)

    # 3. Clean up double commas or leading dangling commas
    text = re.sub(r',\s*,+', ',', text)
    text = re.sub(r'^\s*,\s*', '', text)
    return text


def optimize_for_tts(text: str) -> str:
    """
    Format raw LLM output for TTS by handling spacing, repeated punctuation,
    converting ALL-CAPS words (e.g. WOW -> Wow), standardizing human fillers,
    adding soft micro-pauses, and smoothing sentence prosody.
    """
    if not text:
        return ""

    # 1. NORMALIZE ALL-CAPS WORDS (Fix "WOW" spelled out as "W O W" & pitch spikes)
    text = normalize_caps(text)

    # 2. NORMALIZE HUMAN FILLERS (Fix fillers returning as speech for all 3 voices)
    text = normalize_fillers(text)

    # 3. EXPAND ABBREVIATIONS
    # Ensure technical terms are pronounced fully and correctly by the neural engine.
    # ── Numeric-prefix BHK expansion (must run BEFORE the generic BHK rule) ──
    _bhk_digits = {
        "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
    }
    def _expand_bhk(m):
        digit = m.group(1)
        return f"{_bhk_digits.get(digit, digit)} B H K"
    text = re.sub(r'\b([1-5])\s*BHK\b', _expand_bhk, text, flags=re.IGNORECASE)

    expansions = {
        r'\bsq\.?ft\.?\b': 'square feet',
        r'\bsq\.?\s?feet\b': 'square feet',
        r"\bBHK\b": "B H K",       # Spacing forces letter-by-letter pronunciation for BHK
        r"\bCr\b": "crore",
        r"\blakhs?\b": "lakh",
    }
    for pattern, replacement in expansions.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # 4. NORMALIZE WHITESPACE & STACKED PUNCTUATION
    # Remove excessive gaps and flatten repeated punctuation to prevent TTS pitch spikes and erratic tempo.
    text = text.strip()
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # Flatten multiple exclamation/question marks (which cause pitch spikes)
    text = re.sub(r'!{2,}', '!', text)
    text = re.sub(r'\?{2,}', '?', text)
    text = re.sub(r'\.{4,}', '...', text)
    text = re.sub(r',{2,}', ',', text)

    # 5. CONVERSATIONAL MICRO-PAUSE MARKERS (Soft commas, avoiding heavy ellipsis drops)
    ack_patterns = [
        r'\b(got it)[.,;]*\s+', r'\b(i see)[.,;]*\s+', r'\b(okay)[.,;]*\s+', r'\b(sure)[.,;]*\s+',
        r'\b(theek hai)[.,;]*\s+', r'\b(samajh gayi)[.,;]*\s+', r'\b(samjha)[.,;]*\s+',
        r'\b(bilkul)[.,;]*\s+', r'\b(barobar)[.,;]*\s+', r'\b(achha)[.,;]*\s+'
    ]
    for pattern in ack_patterns:
        # Replace heavy ellipsis pause with a gentle comma for smooth prosody
        text = re.sub(pattern, r'\1, ', text, flags=re.IGNORECASE)

    # Re-split on standard sentence boundaries
    sentences_raw = re.split(r'([.!?]+|\n+)', text)
    sentences = []
    
    for i in range(0, len(sentences_raw), 2):
        chunk = sentences_raw[i].strip()
        punct = sentences_raw[i+1].strip() if i+1 < len(sentences_raw) else ""
        
        if not chunk and not punct:
            continue
            
        combined = chunk + (punct if punct else "")
        if combined.strip():
            sentences.append(combined.strip())

    # 6. TRUNCATE TO MAX LENGTH
    final_text = " ".join(sentences)
    if len(final_text) > MAX_TEXT_LENGTH:
        truncated = final_text[:MAX_TEXT_LENGTH]
        last_space = truncated.rfind(" ")
        if last_space > 0:
            truncated = truncated[:last_space]
        if not re.search(r'[.!?]$', truncated):
            truncated += "."
        final_text = truncated

    # 7. ENFORCE CLEAN SENTENCE STRUCTURE
    final_sentences = re.split(r'(?<=[.!?])\s+', final_text)
    final_lines = []
    for i in range(0, len(final_sentences), 2):
        pair = " ".join(f for f in final_sentences[i:i+2] if f)
        if pair.strip():
            final_lines.append(pair.strip())

    return "\n".join(final_lines)
