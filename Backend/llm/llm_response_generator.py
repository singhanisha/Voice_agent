"""
LLM-powered spoken response generator — the SOLE response path.

Architecture:
    STT
      ↓
    Intent Extraction
      ↓
    StateManager (transitions ONLY)
      ↓
    LLMResponseGenerator (responses ONLY)
      ↓
    TTS

This module is the ONLY place that generates spoken responses.

Important design rules:
- StateManager decides the conversation state.
- This module decides how that state is spoken.
- JSON responses are guidance only.
- LLM constructs the final sentence.
- User's latest language has priority.
- English / Hindi / Hinglish are handled dynamically.
- A node transition must NOT force an English template.
- Template responses are fallback only.
"""

from __future__ import annotations

import asyncio
import logging
import re
import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


logger = logging.getLogger(__name__)


# =============================================================================
# TurnResult
# =============================================================================

@dataclass
class TurnResult:
    """Structured output from StateManager after executing a state transition."""

    node: dict[str, Any]
    node_id: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    user_input: str = ""
    language: str = "en"
    is_terminal: bool = False
    user_question: Optional[str] = None
    response_type: str = "normal"
    asked_flags: dict[str, bool] = field(default_factory=dict)
    recent_responses: list[str] = field(default_factory=list)
    node_changed: bool = False

    def __post_init__(self):
        if not self.node_id:
            self.node_id = str(self.node.get("id", ""))


# =============================================================================
# Load response prompt
# =============================================================================

def _load_response_prompt() -> str:
    prompt_path = Path(__file__).resolve().parent / "response_prompt.txt"

    try:
        return prompt_path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.warning(
            "[RESPONSE PROMPT] Could not load response_prompt.txt: %s",
            exc,
        )
        return ""


_RESPONSE_SYSTEM_PROMPT: str = _load_response_prompt()


# =============================================================================
# Node classification
# =============================================================================

_LLM_RESPONSE_NODES = {
    # Intent
    "node-1735264921453",
    "fallback_intent",

    # Buyer qualification
    "node-1735267546732",      # Ask Property Type
    "node-1767421000001",      # Ask Location
    "node-1767421000002",      # Ask Budget

    # Requirements complete
    "node-1736323961832",

    # Site visit
    "node-1735265015507",
    "fallback_visit_datetime",

    # Callback
    "node-1736492391269",
    "fallback_callback_time",

    # Objection
    "node-objection-not-looking",
    "node-objection-budget-high",

    # Seller
    "node-1736510533232",
    "node-1767420514711",

    # Other fallbacks
    "node_fallback",
    "node_fallback_budget",
    "node_fallback_location",
    "node_fallback_reschedule",
}


_TEMPLATE_ONLY_NODES = {
    "node-1767592854176",
    "node-1735264873079",
    "node-1735265209472",
    "node-1736567518748",
    "node-1736492485610",
    "node-1736492925252",
    "node-1735969972303",
    "node-1736492520068",
    "node-wrong-person-end",
}


def is_llm_node(node: dict[str, Any]) -> bool:
    """
    Decide whether this node can use LLM response generation.

    Any node with explicit LLM registration, collects fields, or fallback
    behavior is considered eligible.
    """

    node_id = str(node.get("id") or "")

    if node_id in _LLM_RESPONSE_NODES:
        return True

    if node.get("collects"):
        return True

    if node.get("type") in {"fallback", "fallback_guided"}:
        return True

    return False


# =============================================================================
# Language normalization
# =============================================================================

def _normalize_language(language: str | None) -> str:
    """
    Normalize all language labels used by the pipeline.

    Supported OUTPUT styles: en, hi.

    FIX K — Magpie TTS (the only TTS this project uses) has exactly two
    voice locales: EN-US and HI-IN. There is no "hinglish" voice. Before
    this fix, when the effective language resolved to "hinglish" the code
    picked the ROMANIZED text branch (e.g. "Aapka roughly kitna budget
    hai?") and sent it to the HI-IN voice, which is trained on Devanagari
    script — reading Roman-script Hindi through it produces broken,
    mispronounced audio. That is exactly the "TTS doesn't answer properly"
    symptom that was reported.
    
    Fix: "hinglish" is still a valid DETECTION label (analyze_user_text /
    LanguageTracker can still tell you a turn looked like code-switched
    Hinglish, for logging/debugging) — but as an OUTPUT language it is
    always collapsed to "hi" here, so every downstream consumer (template
    selection, LLM prompt instruction, TTS locale) picks the Devanagari
    Hindi branch, which the HI-IN voice actually reads correctly.
    """

    value = str(language or "").strip().lower()

    if value in {"hindi", "hin", "hi-in", "hi"}:
        return "hi"

    if value in {
        "hinglish",
        "hindi_english",
        "hindi-english",
        "roman_hindi",
        "roman-hindi",
    }:
        return "hi"

    if value in {"mr", "marathi", "mar"}:
        # Project requirement: do not speak Marathi.
        # Use Hindi as the closest supported response language.
        return "hi"

    return "en"


# =============================================================================
# Generic Roman Hindi / Hinglish language fallback
# =============================================================================

_ROMAN_HINDI_MARKERS = {
    "mujhe",
    "mujhko",
    "mera",
    "meri",
    "mere",
    "aap",
    "apka",
    "aapka",
    "aapki",
    "aapke",
    "kya",
    "kaise",
    "kaisa",
    "kaisi",
    "kahan",
    "kahaan",
    "kab",
    "kitna",
    "kitne",
    "kitni",
    "chahiye",
    "chahta",
    "chahti",
    "hai",
    "hain",
    "ho",
    "hoga",
    "hogi",
    "karna",
    "karni",
    "karne",
    "karunga",
    "karungi",
    "dekhna",
    "dekhni",
    "dikhana",
    "dikhaiye",
    "dikhao",
    "lena",
    "leni",
    "liye",
    "chahiye",
    "ghar",
    "flat",
    "makaan",
    "jagah",
    "area",
    "shehar",
    "shahar",
    "subah",
    "sabayh",
    "kal",
    "aaj",
    "parso",
    "lakh",
    "lakhs",
    "laakh",
    "crore",
    "rupaye",
    "rupees",
    "haan",
    "han",
    "nahi",
    "nahin",
    "ji",
    "acha",
    "achha",
    "theek",
    "thik",
    "bas",
    "toh",
    "to",
    "mein",
    "me",
    "se",
    "ko",
    "ka",
    "ki",
    "ke",
    "par",
    "liye",
    "lijiye",
    "dijiye",
    "bataiye",
    "bataye",
    "boliye",
    "suniyega",
    "appointment",
    "visit",
    "dekh",
    "property",
    "budget",
    # FIX H1: day names, time words, common spoken fillers were missing —
    # utterances like "Shaniwar subah 11 baje" had ZERO markers and were
    # misclassified as English.
    "shaniwar", "somwar", "mangalwar", "budhwar", "guruwar", "guruvar",
    "shukrawar", "ravivar", "raviwar", "itwaar",
    "baje", "sham", "shaam", "raat", "dopahar", "dopehar",
    "abhi", "turant", "jaldi", "der", "sabhi", "phir", "wapas",
    "chalega", "chaliye", "milte", "milenge", "batao", "sun",
    "haanji", "hanji", "arey", "are", "accha",
}


# FIX C: these words appear just as often in plain English real-estate talk
# as in Roman Hindi. Keeping them as Hindi markers made
# "My budget is 50-70 lakh" classify as Hindi.
_CROSS_LANGUAGE_WORDS = {
    "budget", "property", "flat", "area", "visit", "appointment",
    "rupees", "to", "me", "ki", "ka", "ke", "par", "se", "ho", "bas",
}

_ROMAN_HINDI_MARKERS = _ROMAN_HINDI_MARKERS - _CROSS_LANGUAGE_WORDS


# FIX H2 — confidence check before abandoning an active Hindi/Hinglish
# conversation. A turn like "Shaniwar subah 11 baje" has no strong Hindi
# marker AND no strong English marker either — it's a bare day/time phrase.
# Without this check, `_infer_effective_language` treated every such
# ambiguous turn as "detected en" and switched languages mid-flow, which is
# exactly the "sticks for one or two turns then reverts to English" bug.
# Only switch OUT of Hindi/Hinglish when the turn has real English sentence
# structure — not just because it lacks Hindi vocabulary.
_ENGLISH_CONFIDENCE_WORDS = {
    "yes", "no", "is", "are", "am", "can", "could", "would", "should",
    "will", "the", "a", "an", "for", "and", "but", "if", "of", "in", "on",
    "at", "i", "you", "he", "she", "we", "they", "it", "this", "that",
    "my", "your", "want", "wants", "need", "needs", "have", "has", "do",
    "does", "did", "please", "thanks", "thank", "what", "when", "where",
    "how", "why", "who", "which", "looking", "see", "show", "tell",
    "right", "now", "today", "tomorrow", "speak", "speaking", "yeah",
    "sure", "okay", "not", "interested", "buy", "rent", "sell",
}


def _looks_like_confident_english(text: str) -> bool:
    words = re.findall(r"[a-zA-Z]+", (text or "").lower())
    if not words:
        return False
    matches = sum(1 for w in words if w in _ENGLISH_CONFIDENCE_WORDS)
    return matches >= 2 or (matches >= 1 and len(words) <= 3)


def _looks_like_roman_hindi(text: str) -> bool:
    """
    Generic fallback detector for Roman Hindi/Hinglish.

    This is NOT tied to any one user sentence.
    It looks at vocabulary patterns and Devanagari characters.

    FIX C: a single marker word is no longer enough. One shared word such as
    "budget" or "to" used to flip an entirely English sentence to Hindi.
    """

    text = (text or "").strip()

    if not text:
        return False

    # Devanagari = definitely Hindi
    if re.search(r"[\u0900-\u097F]", text):
        return True

    words = re.findall(r"[a-zA-Z]+", text.lower())

    if not words:
        return False

    marker_count = sum(
        1 for word in words
        if word in _ROMAN_HINDI_MARKERS
    )

    ratio = marker_count / len(words)

    # Two independent markers, or a short utterance that is mostly markers.
    return marker_count >= 2 or ratio >= 0.35


def _infer_effective_language(
    language: str,
    user_input: str,
) -> str:
    """
    Determine the effective response language.

    Priority:
        1. Explicit Hindi/Devanagari signal in latest user input.
        2. Explicit Hinglish signal in latest user input.
        3. Pipeline language.
        4. English fallback.

    This prevents a stale 'en' state from forcing English after the
    caller switches to Hindi/Hinglish.
    """

    from .language_utils import analyze_user_text

    normalized = _normalize_language(language)
    text = (user_input or "").strip()

    if not text:
        return normalized

    # Devanagari = Hindi, always.
    if re.search(r"[\u0900-\u097F]", text):
        return "hi"

    # FIX C2: use the SAME detector as the pipeline's LanguageTracker instead
    # of a second, differently-tuned marker list. Previously the two disagreed
    # (log: "turn.language=en | effective_language=hi") and the reply language
    # depended on which one happened to win.
    analysis = analyze_user_text(text, fallback=normalized)

    if not analysis.actionable:
        # Noise / "haan" / "yes" — keep whatever we were already speaking.
        return normalized

    detected = _normalize_language(analysis.detected_language)

    # Cross-check with the local Roman-Hindi heuristic so a transliterated
    # turn is never answered in English.
    if detected == "en" and _looks_like_roman_hindi(text):
        return "hi"

    # FIX H3 — stickiness. If we're already speaking Hindi/Hinglish and this
    # turn was only classified "en" for lack of Hindi vocabulary (day names,
    # numbers, short acks like "haan"/STT garble), do NOT bounce to English
    # unless the turn actually reads like an English sentence. This is what
    # was causing the agent to hold Hindi for a turn or two and then flip
    # back to English mid-conversation.
    if (
        detected == "en"
        and normalized in {"hi", "hinglish"}
        and not _looks_like_confident_english(text)
    ):
        return normalized

    # FIX C3: an explicitly English turn now returns English. The old code
    # returned `normalized`, which carried a stale "hi" over an English turn.
    return detected


# =============================================================================
# Node goals
# =============================================================================

def _classify_node_goal(
    node: dict[str, Any],
    state_manager: Optional[Any] = None,
) -> str:
    """
    Return a clean semantic goal for the current node.

    IMPORTANT:
    Do not infer old combined location/budget behavior from
    node-1735267546732 anymore.

    That node is now Ask Property Type.
    """

    node_id = str(node.get("id") or "")

    # Explicit goal mapping for the new sequential buyer flow.
    explicit_goals = {
        "node-1735264921453":
            "Understand whether the user wants to buy, rent, invest, or sell a property.",

        "fallback_intent":
            "Clarify whether the user wants to buy, rent, invest, or sell a property.",

        "node-1735267546732":
            "Ask for the type of property the buyer or renter wants, such as flat, house, plot, villa, or BHK.",

        "node-1767421000001":
            "Ask for the city or area where the user wants the property.",

        "node-1767421000002":
            "Ask for the user's approximate property budget.",

        "node-1736323961832":
            "Confirm the collected property requirements and ask whether the user wants to explore a suitable property and plan a visit.",

        "node-1735265015507":
            "Ask for the preferred site visit date and time.",

        "fallback_visit_datetime":
            "Clarify the preferred site visit date and time.",

        "node-1736492391269":
            "Ask for a convenient callback date or time.",

        "fallback_callback_time":
            "Clarify the convenient callback date or time.",

        "node-objection-not-looking":
            "Respect the user's decision and handle the objection naturally without pressure.",

        "node-objection-budget-high":
            "Acknowledge the budget concern and offer to consider options closer to the user's comfort range.",

        "node-1736510533232":
            "For a seller, collect the property's location and expected selling price.",

        "node-1767420514711":
            "For a seller, collect the property type or one useful basic property detail.",

        "node_fallback":
            "Clarify the current real estate requirement without changing the current conversation stage.",

        "node_fallback_budget":
            "Clarify the user's property budget.",

        "node_fallback_location":
            "Clarify the user's preferred property location.",

        "node_fallback_reschedule":
            "Ask whether another site visit day would work better.",
    }

    if node_id in explicit_goals:
        return explicit_goals[node_id]

    # Try project NODE_GOALS if available.
    if state_manager:
        try:
            from .state_manager import NODE_GOALS

            goal = NODE_GOALS.get(node_id)

            if goal:
                return goal

        except Exception as exc:
            logger.debug(
                "[NODE GOAL] Could not load NODE_GOALS: %s",
                exc,
            )

    # Dynamic classification
    instruction = node.get("instruction", {})

    instruction_text = ""

    if isinstance(instruction, dict):
        instruction_text = instruction.get("text", "")

    elif isinstance(instruction, str):
        instruction_text = instruction

    if instruction_text:
        return instruction_text

    collects = node.get("collects") or []

    if isinstance(collects, str):
        collects = [collects]

    if collects:
        return (
            "Collect the following information from the user: "
            + ", ".join(str(x) for x in collects)
            + ". Ask only for information that is still missing."
        )

    if node.get("type") == "end":
        return "End the call politely and naturally."

    if node.get("type") == "fallback":
        return "Clarify the information required by the current conversation stage."

    return "Continue the conversation naturally according to the current real estate stage."


# =============================================================================
# Extract JSON phrases
# =============================================================================

def _extract_node_phrases(node: dict[str, Any]) -> list[str]:
    """
    Extract JSON phrases as semantic guidance.

    IMPORTANT:
    These phrases are NOT authoritative final output.
    """

    phrases: list[str] = []

    response = node.get("response")

    if isinstance(response, str) and response.strip():
        phrases.append(response.strip())

    missing_slot_responses = node.get("missing_slot_responses")

    if isinstance(missing_slot_responses, dict):
        for value in missing_slot_responses.values():
            if isinstance(value, str) and value.strip():
                phrases.append(value.strip())

    instruction = node.get("instruction", {})

    if isinstance(instruction, dict):
        text = instruction.get("text", "")

        if isinstance(text, str) and text.strip():
            phrases.append(text.strip())

    return phrases


# =============================================================================
# Context formatting
# =============================================================================

def _safe_context_value(value: Any) -> str:
    """
    Convert context values safely.

    Never expose Python None or literal 'null'.
    """

    if value is None:
        return "None"

    text = str(value).strip()

    if not text:
        return "None"

    if text.lower() in {"null", "none"}:
        return "None"

    return text


def _build_context_block(
    context_data: dict[str, Any],
) -> str:

    context_lines: list[str] = []

    ignored = {
        "memory",
        "asked_flags",
        "recent_responses",
        "active_language",
    }

    for key, value in context_data.items():

        if key in ignored:
            continue

        context_lines.append(
            f"  {key}: {_safe_context_value(value)}"
        )

    if not context_lines:
        return "  None"

    return "\n".join(context_lines)


# =============================================================================
# Build LLM user message
# =============================================================================

def _build_llm_user_message(
    *,
    node_id: str,
    node_goal: str,
    json_phrases: list[str],
    context_data: dict[str, Any],
    user_input: str,
    language: str,
    asked_flags: dict[str, bool] | None = None,
    recent_responses: list[str] | None = None,
) -> str:

    phrases_block = (
        "\n".join(f"- {phrase}" for phrase in json_phrases)
        if json_phrases
        else "(none)"
    )

    context_block = _build_context_block(context_data)

    recent_block = (
        "\n".join(
            f"  - {response}"
            for response in (recent_responses or [])[-3:]
        )
        if recent_responses
        else "  None"
    )

    return f"""
CURRENT CONVERSATION STATE

current_node: {node_id}

node_goal:
{node_goal}

ACTIVE RESPONSE LANGUAGE:
{language}

JSON PHRASES — GUIDANCE ONLY:
{phrases_block}

CURRENT KNOWN CONTEXT:
{context_block}

ALREADY ASKED / RECENT AGENT RESPONSES:
{recent_block}

LATEST USER MESSAGE:
"{user_input}"

IMPORTANT:
The latest user message may contain natural English, Hindi, Roman Hindi,
Hinglish, numbers, abbreviations, pronunciation errors, or speech-to-text
errors.

Understand the meaning rather than requiring an exact sentence match.

Generate the next spoken response according to the current node goal.

Do not copy the JSON phrase word-for-word unless that is genuinely the
natural response in the active language.

The active language is determined from the latest user message and the
conversation context.

If the active language is Hindi, speak natural conversational Hindi.

If the active language is Hinglish, speak natural conversational Hinglish.

If the active language is English, speak natural conversational English.

Ask only one question.

Do not ask for information already present in CURRENT KNOWN CONTEXT.

Do not skip the current node goal.
""".strip()


# =============================================================================
# User meta-question
# =============================================================================

def _answer_user_question(
    question_type: str,
    language: str,
    state_manager: Optional[Any] = None,
) -> str:

    language = _normalize_language(language)

    agent_name = "Agent"
    agent_type = "real_estate_sales"
    global_prompt = ""

    if state_manager:

        agent_name = state_manager.schema.get(
            "agent_name",
            "Agent",
        )

        agent_type = (
            state_manager.schema
            .get("agent_metadata", {})
            .get("agent_type")
            or state_manager.schema.get(
                "agent_type",
                "real_estate_sales",
            )
        )

        if hasattr(state_manager, "global_prompt"):
            global_prompt = (
                state_manager.global_prompt
                or state_manager.schema.get("global_prompt", "")
            )
        else:
            global_prompt = state_manager.schema.get(
                "global_prompt",
                "",
            )

    type_label = {
        "real_estate_sales": "Real Estate team",
        "finance": "Finance advisory team",
        "insurance": "Insurance advisory team",
        "education": "Education counselling team",
        "recruitment": "Recruitment team",
        "healthcare": "Healthcare team",
    }.get(agent_type)

    if not type_label:

        if "sales" in str(agent_type).lower():
            type_label = "Sales team"

        elif (
            "advisory" in str(agent_type).lower()
            or "advisor" in str(agent_type).lower()
        ):
            type_label = "Advisory team"

        else:
            type_label = "Customer support team"

    purpose_desc = "your property interest"

    if (
        agent_type == "healthcare"
        or "clinic" in global_prompt.lower()
        or "doctor" in global_prompt.lower()
    ):
        purpose_desc = "your doctor's appointment"

    elif (
        agent_type == "recruitment"
        or "job application" in global_prompt.lower()
    ):
        purpose_desc = "your job application"

    elif (
        agent_type == "education"
        or "course interest" in global_prompt.lower()
        or "admission" in global_prompt.lower()
    ):
        purpose_desc = "your course inquiry"

    if language == "hi":

        if question_type == "identity":
            return f"{agent_name} बोल रही हूँ, {type_label} से।"

        if question_type == "purpose":
            return f"आपकी property requirement के बारे में call किया है।"

        return "थोड़ा स्पष्ट बताएँगे?"

    if language == "hinglish":

        if question_type == "identity":
            return f"{agent_name} bol rahi hoon, {type_label} se."

        if question_type == "purpose":
            return "Aapki property requirement ke baare mein call kiya hai."

        return "Thoda clear batayenge?"

    if language == "mr":

        if question_type == "identity":
            return f"{agent_name} बोलत आहे, {type_label} मधून."

        if question_type == "purpose":
            return "तुमच्या property requirement बद्दल call केला आहे."

        return "थोडं स्पष्ट सांगाल का?"

    if question_type == "identity":
        return f"This is {agent_name} calling from the {type_label}."

    if question_type == "purpose":
        return f"I am calling regarding {purpose_desc}."

    return "Could you say that differently?"


# =============================================================================
# Template localization
# =============================================================================

def _resolve_template_response(
    node: dict[str, Any],
    context: dict[str, Any],
    language: str,
) -> str:

    from .language_utils import localize_template

    language = _normalize_language(language)

    collects = node.get("collects")

    if not collects:
        collects = []

    elif isinstance(collects, str):
        collects = [collects]

    elif not isinstance(collects, list):
        collects = list(collects)

    # Find missing slots
    missing_slots = [
        slot
        for slot in collects
        if not context.get(slot)
    ]

    template = ""

    # Partially filled node
    if 0 < len(missing_slots) < len(collects):

        missing_slot_responses = node.get(
            "missing_slot_responses",
            {},
        )

        target_slot = missing_slots[0]

        template = missing_slot_responses.get(
            target_slot,
            "",
        )

        # Add acknowledgement only if there is a real value.
        if template:

            filled_slots = [
                slot
                for slot in collects
                if context.get(slot)
            ]

            if filled_slots:

                last_filled = filled_slots[-1]

                value = context.get(last_filled)

                value_text = _safe_context_value(value)

                if value_text != "None":

                    if language == "hi":
                        ack = f"ठीक है, {value_text}।"

                    elif language == "hinglish":
                        ack = f"Theek hai, {value_text}."

                    elif language == "mr":
                        ack = f"ठीक आहे, {value_text}."

                    else:
                        ack = f"Got it — {value_text}."

                    template = f"{ack} {template}"

    if not template:
        template = node.get("response", "")

    if not template:

        instruction = node.get("instruction") or {}

        if isinstance(instruction, dict):
            template = instruction.get("text", "")

    if not template:
        return ""

    def fill(match: re.Match[str]) -> str:

        key = match.group(1)

        if key == "name":

            value = (
                context.get("name")
                or context.get("lead_name")
                or context.get("lead")
                or ""
            )

        else:
            value = context.get(key)

        return _safe_context_value(value) if value else ""

    # Use project's localization helper.
    try:

        localized = localize_template(
            template,
            language,
        )

    except Exception as exc:

        logger.warning(
            "[TEMPLATE LOCALIZATION] Failed: %s",
            exc,
        )

        localized = template

    resolved = re.sub(
        r"\{\{(\w+)\}\}",
        fill,
        localized,
    ).strip()

    # Remove null/None artifacts.
    resolved = re.sub(
        r"\b(?:null|None)\b",
        "",
        resolved,
        flags=re.IGNORECASE,
    )

    resolved = re.sub(
        r"\s+",
        " ",
        resolved,
    ).strip()

    # Clean punctuation spacing.
    resolved = re.sub(
        r"\s+([,.!?])",
        r"\1",
        resolved,
    )

    return resolved


# =============================================================================
# Response finalization
# =============================================================================

def _finalize_response(text: str) -> str:
    """
    Production response constraints.

    - Max 2 sentences
    - Max 30 words
    - One question
    - No accidental JSON/labels
    - Complete sentence
    """

    if not text or not text.strip():
        return ""

    cleaned = text.strip()

    # Remove code fences
    cleaned = cleaned.replace("```text", "")
    cleaned = cleaned.replace("```", "")

    # Remove surrounding quotes
    if (
        cleaned.startswith('"')
        and cleaned.endswith('"')
    ) or (
        cleaned.startswith("'")
        and cleaned.endswith("'")
    ):
        cleaned = cleaned[1:-1].strip()

    # Remove accidental labels
    cleaned = re.sub(
        r"^(?:response|output|answer|reply)\s*[:—-]\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()

    # Remove JSON-like wrapping
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return ""

    cleaned = re.sub(
        r"\s+",
        " ",
        cleaned,
    ).strip()

    # Maximum two sentences
    parts = [
        part.strip()
        for part in re.split(
            r"(?<=[.!?])\s+",
            cleaned,
        )
        if part.strip()
    ]

    if len(parts) > 2:
        parts = parts[:2]

    cleaned = " ".join(parts)

    # Maximum one question mark
    if cleaned.count("?") > 1:

        first_question = cleaned.find("?")

        cleaned = (
            cleaned[:first_question + 1]
            + cleaned[first_question + 1:].replace(
                "?",
                ".",
            )
        )

    words = cleaned.split()

    if len(words) > 30:

        truncated = " ".join(words[:30])

        last_boundary = max(
            truncated.rfind("."),
            truncated.rfind("?"),
            truncated.rfind("!"),
        )

        if last_boundary > 10:
            cleaned = truncated[:last_boundary + 1]

        else:
            cleaned = (
                " ".join(words[:25])
                .rstrip(" ,;:-")
                + "."
            )

        words = cleaned.split()

    # Remove trailing incomplete connectors
    trailing = {
        "and",
        "or",
        "but",
        "to",
        "for",
        "with",
        "the",
        "a",
        "an",
        "is",
        "are",
        "in",
        "on",
        "at",
        "of",
        "so",
        "—",
        "-",
        "it's",
        "let",
        "me",
        "that",
        "like",
    }

    while words:

        last_word = words[-1].lower().rstrip(
            ".,;:-—"
        )

        if last_word in trailing:
            words.pop()
        else:
            break

    if not words:
        return ""

    cleaned = " ".join(words)

    # Terminal punctuation
    if cleaned[-1] not in ".!?":

        first_word = words[0].lower()

        question_starters = {
            "what",
            "which",
            "where",
            "when",
            "how",
            "who",
            "are",
            "is",
            "do",
            "does",
            "can",
            "could",
            "would",
            "kya",
            "kahan",
            "kahaan",
            "kaun",
            "kab",
            "kitna",
            "kitne",
            "kitni",
        }

        if first_word in question_starters:
            cleaned += "?"
        else:
            cleaned += "."

    return cleaned


# =============================================================================
# Language validation
# =============================================================================

def _contains_devanagari(text: str) -> bool:
    return bool(
        re.search(
            r"[\u0900-\u097F]",
            text or "",
        )
    )


def _english_marker_count(text: str) -> int:
    words = set(
        re.findall(
            r"[a-zA-Z]+",
            (text or "").lower(),
        )
    )

    markers = {
        "the",
        "what",
        "which",
        "where",
        "when",
        "would",
        "could",
        "should",
        "your",
        "you",
        "are",
        "is",
        "do",
        "does",
        "can",
        "looking",
        "property",
        "budget",
        "area",
        "city",
        "visit",
        "time",
        "day",
        "please",
        "would",
        "like",
    }

    return len(words & markers)


def _validate_language(
    response: str,
    language: str,
) -> bool:
    """
    Lightweight language safety check.

    This is intentionally conservative.

    Hindi:
        Devanagari is strong evidence.
        Roman Hindi is allowed because Hinglish-style responses may use
        Roman script.

    Hinglish:
        English words are naturally allowed.

    English:
        Strong Hindi markers should not appear.
    """

    if not response:
        return False

    language = _normalize_language(language)

    if language == "hi":

        # Pure Hindi response should normally contain Devanagari.
        # Allow very short real-estate terms like BHK / WhatsApp.
        if _contains_devanagari(response):
            return True

        # If no Devanagari, it is probably Roman Hindi/Hinglish.
        # Reject only if the response is clearly full English.
        english_count = _english_marker_count(response)

        word_count = len(
            re.findall(
                r"[a-zA-Z]+",
                response,
            )
        )

        if word_count >= 6 and english_count >= 3:
            return False

        return True

    if language == "hinglish":
        return True

    # English
    if _contains_devanagari(response):
        return False

    return True


# =============================================================================
# Strong language instructions
# =============================================================================

def _language_generation_rules(language: str) -> str:

    language = _normalize_language(language)

    if language == "hi":

        return """
LANGUAGE = HINDI

HARD REQUIREMENT:
The final spoken response MUST be natural conversational Hindi.

Important:
- Do NOT copy English JSON phrases.
- Translate the meaning into natural Hindi.
- Use Devanagari for the Hindi response whenever possible.
- Common real-estate terms may remain natural: BHK, flat, property, budget,
  WhatsApp, site visit.
- Do not write a complete English sentence.
- Do not append an English question after a Hindi sentence.
- Do not mix an English template with Hindi acknowledgement.

Example:
WRONG:
"ठीक है, 50 लाख। What type of property are you looking for?"

RIGHT:
"ठीक है। आप किस तरह की प्रॉपर्टी देख रहे हैं?"

The current node goal is more important than the exact JSON response wording.
""".strip()

    if language == "hinglish":

        return """
LANGUAGE = HINGLISH

HARD REQUIREMENT:
The final spoken response MUST be natural conversational Hinglish.

Important:
- Use natural Hindi + English mixing.
- Roman script is acceptable.
- Do not simply copy the English JSON response.
- Reconstruct the sentence naturally.
- Real-estate terms such as flat, property, budget, BHK, location,
  WhatsApp and site visit may remain in English.
- Do not output a full formal English sentence.

Example:
WRONG:
"What type of property are you looking for?"

BETTER:
"Aap kis type ki property dekh rahe ho?"

The current node goal is more important than the exact JSON response wording.
""".strip()

    return """
LANGUAGE = ENGLISH

HARD REQUIREMENT:
The final spoken response MUST be natural conversational English.

Important:
- Do not insert Hindi or Hinglish clauses.
- Use the current node goal.
- Do not copy JSON phrases mechanically.
- Keep the response short and natural.
""".strip()


# =============================================================================
# Build response system prompt
# =============================================================================

def build_response_system_prompt(
    state_manager: Optional[Any],
    language: str,
) -> str:

    language = _normalize_language(language)

    if not state_manager:
        return (
            _RESPONSE_SYSTEM_PROMPT
            + "\n\n"
            + _language_generation_rules(language)
        )

    schema = getattr(
        state_manager,
        "schema",
        {},
    ) or {}

    agent_name = schema.get(
        "agent_name",
        "Agent",
    )

    agent_type = schema.get(
        "agent_type",
        "real_estate_sales",
    )

    global_prompt = (
        getattr(
            state_manager,
            "global_prompt",
            None,
        )
        or schema.get(
            "global_prompt",
            "",
        )
        or "You are a helpful AI assistant."
    )

    prompt_builder: list[str] = []

    prompt_builder.append(
        "## IDENTITY & AUTHORITATIVE SCRIPT"
    )

    prompt_builder.append(
        f"Agent Name: {agent_name}"
    )

    prompt_builder.append(
        f"Agent Type/Industry: {agent_type}"
    )

    prompt_builder.append(
        "Primary Agent Script/Behavior definition:"
    )

    prompt_builder.append(
        global_prompt
    )

    prompt_builder.append("")

    prompt_builder.append(
        "## CURRENT DATA"
    )

    prompt_builder.append(
        "If a field has a value, NEVER ask for that field again."
    )

    extraction_fields = getattr(
        state_manager,
        "extraction_fields",
        [],
    ) or []

    conversation_data = getattr(
        state_manager,
        "conversation_data",
        {},
    ) or {}

    for field_name in extraction_fields:

        value = conversation_data.get(
            field_name
        )

        if value:
            prompt_builder.append(
                f"- {field_name}: COLLECTED ({value})"
            )
        else:
            prompt_builder.append(
                f"- {field_name}: MISSING"
            )

    prompt_builder.append("")

    prompt_builder.append(
        "## LANGUAGE RULES"
    )

    prompt_builder.append(
        f"Active Language: {language}"
    )

    prompt_builder.append(
        _language_generation_rules(language)
    )

    prompt_builder.append("")

    prompt_builder.append(
        "## RESPONSE GOVERNANCE"
    )

    prompt_builder.append(
        "- Generate spoken response text only."
    )

    prompt_builder.append(
        "- Maximum 2 sentences."
    )

    prompt_builder.append(
        "- Maximum approximately 30 words."
    )

    prompt_builder.append(
        "- Ask exactly ONE question when the current node requires a question."
    )

    prompt_builder.append(
        "- Never ask again for information already collected."
    )

    prompt_builder.append(
        "- Never skip the current node's goal."
    )

    prompt_builder.append(
        "- Do not jump to site visit before property requirements are complete."
    )

    prompt_builder.append(
        "- Do not invent property information."
    )

    prompt_builder.append(
        "- Do not invent pricing, amenities, availability, project names, "
        "locations, dates, or appointment confirmations."
    )

    prompt_builder.append(
        "- JSON phrases are guidance only. Do not copy them mechanically."
    )

    prompt_builder.append(
        "- Understand the meaning of Roman Hindi, Hinglish, speech-to-text "
        "errors, abbreviations, numbers, and natural conversational wording."
    )

    prompt_builder.append(
        "- The latest user message controls the response language when it "
        "contains a clear language signal."
    )

    prompt_builder.append(
        "- Do not return JSON, markdown, labels, or explanations."
    )

    return "\n".join(prompt_builder)


# =============================================================================
# LLM call
# =============================================================================

async def _call_llm_for_response(
    node: dict[str, Any],
    *,
    context_data: dict[str, Any],
    user_input: str,
    language: str,
    asked_flags: dict[str, bool] | None = None,
    recent_responses: list[str] | None = None,
    state_manager: Optional[Any] = None,
) -> str:

    from . import config as cfg
    from .llm import _async_call_groq_api

    language = _normalize_language(language)

    system_prompt = build_response_system_prompt(
        state_manager,
        language,
    )

    json_phrases = _extract_node_phrases(
        node
    )

    node_goal = _classify_node_goal(
        node,
        state_manager=state_manager,
    )

    user_message = _build_llm_user_message(
        node_id=str(
            node.get("id", "")
        ),
        node_goal=node_goal,
        json_phrases=json_phrases,
        context_data=context_data,
        user_input=user_input,
        language=language,
        asked_flags=asked_flags,
        recent_responses=recent_responses,
    )

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]

    logger.info(
        "[LLM RESPONSE] node=%s goal=%s language=%s user=\"%s\"",
        node.get("id", "?"),
        node_goal,
        language,
        (user_input or "")[:80],
    )

    max_attempts = 3

    for attempt in range(max_attempts):

        try:

            temperature = (
                cfg.PHRASE_RESPONSE_TEMPERATURE
                if attempt == 0
                else 0.4
            )

            raw = await _async_call_groq_api(
                messages,
                max_tokens=cfg.PHRASE_RESPONSE_MAX_TOKENS,
                temperature=temperature,
            )

        except Exception as exc:

            logger.error(
                "[LLM RESPONSE] API error attempt=%d: %s",
                attempt + 1,
                exc,
            )

            return ""

        if not raw or not raw.strip():

            logger.warning(
                "[LLM RESPONSE] Empty response attempt=%d",
                attempt + 1,
            )

            continue

        raw = raw.strip()

        # Remove accidental code fence
        raw = raw.replace("```text", "")
        raw = raw.replace("```", "")
        raw = raw.strip()

        finalized = _finalize_response(
            raw
        )

        if not finalized:

            logger.warning(
                "[LLM RESPONSE] Invalid finalized output: %s",
                raw,
            )

            continue

        # Language validation
        if not _validate_language(
            finalized,
            language,
        ):

            logger.warning(
                "[LANGUAGE VALIDATOR] Rejected output "
                "for language=%s: \"%s\"",
                language,
                finalized,
            )

            messages.append(
                {
                    "role": "assistant",
                    "content": finalized,
                }
            )

            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your previous response violated the required "
                        f"{language} language. Rewrite it completely in "
                        f"natural {language}. Do not use an English template. "
                        f"Keep the same semantic purpose and ask only one "
                        f"question if required."
                    ),
                }
            )

            continue

        # Anti-repetition
        if recent_responses:

            new_opening = " ".join(
                finalized.split()[:6]
            ).lower()

            repetitive = False

            for recent in recent_responses[-3:]:

                recent_opening = " ".join(
                    recent.split()[:6]
                ).lower()

                similarity = difflib.SequenceMatcher(
                    None,
                    new_opening,
                    recent_opening,
                ).ratio()

                if similarity > 0.82:
                    repetitive = True
                    break

            if repetitive:

                logger.warning(
                    "[LLM RESPONSE] Repetition detected attempt=%d",
                    attempt + 1,
                )

                messages.append(
                    {
                        "role": "assistant",
                        "content": finalized,
                    }
                )

                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Rephrase naturally. Do not repeat the previous "
                            "opening or sentence structure. Keep the same "
                            "current node goal and language."
                        ),
                    }
                )

                continue

        # Minimum length
        words = finalized.split()

        if len(words) < 2:

            logger.warning(
                "[RESPONSE VALIDATOR] Too short: \"%s\"",
                finalized,
            )

            continue

        # Cut-off detection
        if (
            finalized[-1] not in ".!?"
            and len(words) > 10
        ):

            logger.warning(
                "[RESPONSE VALIDATOR] Possible cut-off: \"%s\"",
                finalized,
            )

            continue

        return finalized

    logger.warning(
        "[LLM RESPONSE] All attempts failed for node=%s",
        node.get("id", "?"),
    )

    return ""


# =============================================================================
# Location suggestion
# =============================================================================

def _is_location_suggestion_request(
    user_input: str,
) -> bool:

    text = re.sub(
        r"[^\w\s]",
        " ",
        (user_input or "").lower(),
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    hints = (
        "suggest",
        "recommend",
        "which city",
        "which area",
        "best location",
        "good location",
        "cities",
        "areas",
        "options",
        "offer me",
        "can you offer",
        "what can you offer",
    )

    return any(
        hint in text
        for hint in hints
    )


def _get_suggested_locations(
    state_manager: Optional[Any],
) -> list[str]:

    if not state_manager:
        return [
            "Wakad",
            "Baner",
            "Hinjewadi",
            "Kharadi",
        ]

    schema = getattr(
        state_manager,
        "schema",
        {},
    ) or {}

    by_city = (
        schema
        .get("project_quick_reference", {})
        .get("by_city")
    )

    if isinstance(by_city, dict) and by_city:
        return list(by_city.keys())

    projects = schema.get(
        "all_projects",
        [],
    )

    if isinstance(projects, list) and projects:

        cities = set()

        for project in projects:

            if not isinstance(project, dict):
                continue

            location = project.get(
                "location",
                {},
            )

            if not isinstance(location, dict):
                continue

            city = location.get(
                "city"
            )

            if city:
                cities.add(
                    str(city)
                )

        if cities:
            return sorted(cities)

    return [
        "Wakad",
        "Baner",
        "Hinjewadi",
        "Kharadi",
    ]


def _location_suggestion_response(
    language: str,
    state_manager: Optional[Any] = None,
) -> str:

    language = _normalize_language(language)

    locations = _get_suggested_locations(
        state_manager
    )

    if not locations:
        locations = [
            "Wakad",
            "Baner",
            "Hinjewadi",
            "Kharadi",
        ]

    if language == "hi":

        if len(locations) == 1:
            return (
                f"{locations[0]} एक option है। "
                "क्या आप इसी area में देखना चाहेंगे?"
            )

        return (
            f"आप {', '.join(locations[:-1])} "
            f"या {locations[-1]} देख सकते हैं। "
            "इनमें से कौन-सा area आपको पसंद है?"
        )

    if language == "hinglish":

        if len(locations) == 1:
            return (
                f"{locations[0]} ek option hai. "
                "Kya aap isi area mein dekhna chahenge?"
            )

        return (
            f"Aap {', '.join(locations[:-1])} "
            f"ya {locations[-1]} consider kar sakte ho. "
            "Inmein se kaunsa area prefer karoge?"
        )

    if len(locations) == 1:
        return (
            f"{locations[0]} is one option. "
            "Would you like to consider this area?"
        )

    return (
        f"You can consider {', '.join(locations[:-1])}, "
        f"or {locations[-1]}. Which area would you prefer?"
    )


# =============================================================================
# Anti-repeat nudge
# =============================================================================

def _get_anti_repeat_nudge(
    node: dict[str, Any],
    language: str,
    context: Optional[dict] = None,
) -> str:

    language = _normalize_language(language)

    node_id = str(
        node.get("id", "")
    )

    nudges = {

        "node-1735264921453": {
            "en": "Are you looking to buy, rent, invest, or sell a property?",
            "hi": "आप property buy, rent, investment या sell करने के लिए देख रहे हैं?",
            "hinglish": "Aap property buy, rent, invest ya sell karne ke liye dekh rahe ho?",
        },

        "node-1735267546732": {
            "en": "What type of property are you looking for?",
            "hi": "आप किस तरह की property देख रहे हैं?",
            "hinglish": "Aap kis type ki property dekh rahe ho?",
        },

        "node-1767421000001": {
            "en": "Which city or area are you considering?",
            "hi": "आप किस शहर या area में property देख रहे हैं?",
            "hinglish": "Aap kis city ya area mein property dekh rahe ho?",
        },

        "node-1767421000002": {
            "en": "What budget range should I keep in mind?",
            "hi": "आपका लगभग कितना budget है?",
            "hinglish": "Aapka roughly kitna budget hai?",
        },

        "node-1736323961832": {
            "en": "Would you like to explore a suitable property and plan a visit?",
            "hi": "क्या आप कोई suitable property देखना और visit plan करना चाहेंगे?",
            "hinglish": "Kya aap suitable property dekhna aur visit plan karna chahoge?",
        },

        "node-1735265015507": {
            "en": "What day and time would work best for the site visit?",
            "hi": "Site visit के लिए कौन-सा दिन और समय आपके लिए convenient रहेगा?",
            "hinglish": "Site visit ke liye kaunsa din aur time aapke liye convenient rahega?",
        },

        "node-1736492391269": {
            "en": "When would be a convenient time for a callback?",
            "hi": "Callback के लिए कौन-सा दिन और समय convenient रहेगा?",
            "hinglish": "Callback ke liye kaunsa din aur time convenient rahega?",
        },

        # FIX B2: greeting + availability nodes were missing here, so the
        # language guard had no Hindi text to fall back to.
        # FIX I: the "en" entry here used to be IDENTICAL to the node's
        # default response. The anti-repeat guard in runtime.py only swaps
        # in a nudge when it differs from the last thing said, so on a
        # stall it found "no different nudge" and repeated the exact same
        # sentence verbatim (this is why the greeting said the same line
        # 2-3 times in a row). Give it an actual rephrase.
        "node-1767592854176": {
            "en": "Hi there — am I speaking with you directly?",
            "hi": "नमस्ते, मैं नेहा बोल रही हूँ Real Estate team से। क्या मैं आपसे बात कर सकती हूँ?",
            "hinglish": "Hi, main Neha bol rahi hoon Real Estate team se. Kya main aapse baat kar sakti hoon?",
        },

        "node-1735264873079": {
            "en": "Sorry, just checking — do you have a couple of minutes to talk?",
            "hi": "क्या अभी आपके पास दो मिनट हैं?",
            "hinglish": "Abhi aapke paas 2 minute hain?",
        },

        # FIX O: these two terminal nodes had no entry, so the language guard
        # fell through to the generic "would you like to know more?" default
        # — losing the actual visit-time confirmation and repeating the same
        # wrong line on both the confirmation AND the goodbye node.
        # {timeline} is substituted below from context if present.
        "node-1735265209472": {
            "en": "Perfect, I'll note that down and share the visit details on WhatsApp before {timeline}. Looking forward to it.",
            "hi": "बढ़िया, मैं ये नोट कर लेती हूँ और {timeline} से पहले visit की details WhatsApp पर भेज दूँगी। मिलते हैं!",
            "hinglish": "Perfect, main ye note kar leti hoon aur {timeline} se pehle visit ki details WhatsApp par bhej doongi. Milte hain!",
        },
        "node-1736492925252": {
            "en": "All set, see you then. Have a great day!",
            "hi": "सब तय हो गया, तब मिलते हैं। आपका दिन शुभ हो!",
            "hinglish": "Sab set ho gaya, tab milte hain. Aapka din accha ho!",
        },

        # FIX O2: remaining commonly-reached nodes from real_estate.json.
        "node-1736567518748": {
            "en": "Got it, I'll get back to you around {timeline}. Take care!",
            "hi": "ठीक है, मैं आपसे {timeline} के आसपास संपर्क करूँगी। ध्यान रखिए!",
            "hinglish": "Theek hai, main aapse {timeline} ke aaspaas contact karungi. Take care!",
        },
        "node-1736492485610": {
            "en": "Alright, take care. Talk soon!",
            "hi": "ठीक है, ध्यान रखिए। जल्दी बात करते हैं!",
            "hinglish": "Theek hai, take care. Jaldi baat karte hain!",
        },
        "node-1735970090937": {
            "en": "No worries, I'll keep it brief. Would you like to continue now?",
            "hi": "कोई बात नहीं, मैं छोटी बात करूँगी। क्या अभी बात जारी रखना चाहेंगे?",
            "hinglish": "Koi baat nahi, main short mein baat karungi. Kya abhi continue karna chahenge?",
        },
        "node-1735969972303": {
            "en": "Thanks for chatting with me. Have a great rest of your day!",
            "hi": "बात करने के लिए धन्यवाद। आपका दिन शुभ हो!",
            "hinglish": "Baat karne ke liye dhanyavaad. Aapka din accha ho!",
        },
        "node-1736492520068": {
            "en": "No problem at all. Take care!",
            "hi": "कोई बात नहीं। ध्यान रखिए!",
            "hinglish": "Koi baat nahi. Take care!",
        },
        "node-wrong-person-end": {
            "en": "No problem. Thanks for your time. Goodbye!",
            "hi": "कोई बात नहीं। आपके समय के लिए धन्यवाद। अलविदा!",
            "hinglish": "Koi baat nahi. Aapke time ke liye dhanyavaad. Bye!",
        },
        "node-objection-budget-high": {
            "en": "I understand, budget is important. Would you be open to looking at similar options?",
            "hi": "समझ सकती हूँ, budget ज़रूरी है। क्या आप similar options भी देखना चाहेंगे?",
            "hinglish": "Samajh sakti hoon, budget important hai. Kya aap similar options bhi dekhna chahenge?",
        },
        "node-objection-not-looking": {
            "en": "No worries, I understand. Would it be okay if I sent you some details anyway?",
            "hi": "कोई बात नहीं, समझ सकती हूँ। क्या मैं फिर भी आपको कुछ details भेज दूँ?",
            "hinglish": "Koi baat nahi, samajh sakti hoon. Kya main phir bhi aapko details bhej doon?",
        },
        "node-1736510533232": {
            "en": "I can help with that. Which area is the property in, and what price are you expecting?",
            "hi": "मैं मदद कर सकती हूँ। property किस area में है, और आप क्या price expect कर रहे हैं?",
            "hinglish": "Main madad kar sakti hoon. Property kis area mein hai, aur aap kya price expect kar rahe hain?",
        },
        "node-1767420514711": {
            "en": "What type of property is it?",
            "hi": "ये किस तरह की property है?",
            "hinglish": "Ye kis type ki property hai?",
        },

        # FIX Q: the entire "fallback_*" node category (type: "fallback")
        # was missing entirely. These fire whenever the LLM can't cleanly
        # parse the user's answer — exactly the moment a correct, on-topic
        # Hindi re-ask matters most — and were falling through to the wrong
        # generic "would you like to know more?" line instead.
        "fallback_intent": {
            "en": "Just to clarify, are you looking to buy, rent, invest, or sell a property?",
            "hi": "बस साफ़ करने के लिए — क्या आप property खरीदना, किराए पर लेना, invest करना, या बेचना चाहते हैं?",
            "hinglish": "Bas clarify karne ke liye — kya aap property buy, rent, invest, ya sell karna chahte hain?",
        },
        "fallback_location": {
            "en": "Which city or area are you considering?",
            "hi": "आप किस शहर या area में देख रहे हैं?",
            "hinglish": "Aap kis city ya area mein dekh rahe hain?",
        },
        "fallback_budget": {
            "en": "What budget range should I keep in mind?",
            "hi": "मैं budget किस range का रखूँ?",
            "hinglish": "Main budget kis range ka rakhoon?",
        },
        "fallback_property_type": {
            "en": "What type of property are you looking for, like a flat, house, or BHK?",
            "hi": "आप किस तरह की property देख रहे हैं — जैसे फ्लैट, घर, या कितने BHK?",
            "hinglish": "Aap kis type ki property dekh rahe hain — jaise flat, ghar, ya kitne BHK?",
        },
        "fallback_visit_datetime": {
            "en": "Sorry, I didn't quite catch that — which day and time would work best for the visit?",
            "hi": "माफ़ कीजिए, मैं समझ नहीं पाई — visit के लिए कौन-सा दिन और समय आपके लिए सही रहेगा?",
            "hinglish": "Sorry, main samajh nahi paayi — visit ke liye kaunsa din aur time aapke liye sahi rahega?",
        },
        "fallback_callback_time": {
            "en": "When would be a convenient time for a callback?",
            "hi": "Callback के लिए कौन-सा समय आपके लिए convenient रहेगा?",
            "hinglish": "Callback ke liye kaunsa time aapke liye convenient rahega?",
        },
        "node_fallback": {
            "en": "Could you tell me a little more about what you're looking for in a property?",
            "hi": "क्या आप मुझे थोड़ा और बता सकते हैं कि आप property में क्या ढूंढ रहे हैं?",
            "hinglish": "Kya aap thoda aur bata sakte hain ki aap property mein kya dhoondh rahe hain?",
        },
    }

    stage = nudges.get(node_id)

    if stage:
        _text = stage.get(
            language,
            stage.get("en", ""),
        )
        if "{timeline}" in _text:
            _timeline = (context or {}).get("timeline") or "the scheduled time"
            _text = _text.replace("{timeline}", str(_timeline))
        return _text

    collects = node.get(
        "collects"
    ) or []

    if isinstance(collects, str):
        collects = [collects]

    missing = [
        slot
        for slot in collects
        if not context or not context.get(slot)
    ]

    if missing:

        slot = missing[0]

        if language == "hi":
            return f"कृपया अपना {slot} बता दें?"

        if language == "hinglish":
            return f"Aap apna {slot} bata denge?"

        return f"Could you share your {slot}?"

    if language == "hi":
        return "क्या आप इसके बारे में थोड़ा और जानना चाहेंगे?"

    if language == "hinglish":
        return "Kya aap iske baare mein thoda aur jaana chahoge?"

    return "Would you like to know more?"


# =============================================================================
# Repetition
# =============================================================================

def _is_repetition(
    new_response: str,
    last_response: str,
) -> bool:

    if not new_response or not last_response:
        return False

    a = (
        new_response
        .lower()
        .strip()
        .rstrip("?.!,")
    )

    b = (
        last_response
        .lower()
        .strip()
        .rstrip("?.!,")
    )

    if not a or not b:
        return False

    if a == b:
        return True

    words_a = set(
        a.split()
    )

    words_b = set(
        b.split()
    )

    if not words_a or not words_b:
        return False

    overlap = (
        len(words_a & words_b)
        / max(
            len(words_a),
            len(words_b),
        )
    )

    return overlap > 0.8


# =============================================================================
# Phrase usage logging
# =============================================================================

def _log_phrase_usage(
    response: str,
) -> None:

    try:

        from .state_manager import (
            _match_phrases_used,
            _PHRASE_BANK,
        )

        matched = _match_phrases_used(
            response,
            _PHRASE_BANK,
        )

        if matched:

            logger.info(
                "[JSON PHRASES USED] %s",
                "; ".join(
                    f'"{phrase}"'
                    for phrase in matched[:5]
                ),
            )

    except Exception:
        pass


# =============================================================================
# Main response generation
# =============================================================================

async def generate_response_for_turn(
    turn: TurnResult,
    state_manager: Optional[Any] = None,
) -> str:
    """
    Generate the final spoken response.

    IMPORTANT:
    The LLM is now allowed to generate a response even when the node changed.

    Why?

    Example:

        User: KAL SUBAYH 8 BAJE.
        StateManager:
            transition -> Save Lead & Confirm

    The new node contains an English template, but the user was speaking
    Hinglish/Hindi.

    Therefore:
        node_changed = True
        MUST NOT mean "use English template directly".

    The language of the response must remain consistent with the user's
    latest language.
    """

    node = turn.node
    node_id = turn.node_id
    context = turn.context
    user_input = turn.user_input

    # -------------------------------------------------------------------------
    # IMPORTANT:
    # Use latest user input as an additional language signal.
    # -------------------------------------------------------------------------

    language = _infer_effective_language(
        turn.language,
        user_input,
    )

    logger.info(
        "[LANGUAGE] turn.language=%s | effective_language=%s | input=\"%s\"",
        turn.language,
        language,
        (user_input or "")[:80],
    )

    asked_flags = turn.asked_flags

    # =========================================================================
    # PATH 1 — META QUESTION
    # =========================================================================

    if turn.user_question:

        answer = _answer_user_question(
            turn.user_question,
            language,
            state_manager=state_manager,
        )

        if is_llm_node(node):

            follow_up = await _call_llm_for_response(
                node,
                context_data=context,
                user_input=(
                    f"[User asked: {turn.user_question}] "
                    f"{user_input}"
                ),
                language=language,
                asked_flags=asked_flags,
                recent_responses=turn.recent_responses,
                state_manager=state_manager,
            )

            if follow_up:

                combined = (
                    f"{answer} "
                    f"{follow_up}"
                )

                finalized = _finalize_response(
                    combined
                )

                logger.info(
                    "[LLM RESPONSE] Question + Continue: \"%s\"",
                    finalized,
                )

                return finalized

        template = _resolve_template_response(
            node,
            context,
            language,
        )

        if template:

            return _finalize_response(
                f"{answer} {template}"
            )

        return _finalize_response(
            answer
        )

    # =========================================================================
    # PATH 2 — NOISE
    # =========================================================================

    if turn.response_type == "noise_repeat":

        response = _resolve_template_response(
            node,
            context,
            language,
        )

        logger.info(
            "[TEMPLATE] Noise repeat node=%s language=%s",
            node_id,
            language,
        )

        return (
            _finalize_response(response)
            if response
            else ""
        )

    # =========================================================================
    # PATH 3 — DEESCALATION
    # =========================================================================

    if turn.response_type == "deescalation":

        try:

            from .state_manager import (
                DEESCALATION_RESPONSES
            )

            if DEESCALATION_RESPONSES:

                idx = (
                    hash(user_input)
                    % len(DEESCALATION_RESPONSES)
                )

                response = DEESCALATION_RESPONSES[
                    idx
                ]

                # If deescalation response is English but
                # user is Hindi/Hinglish, ask LLM to localize it.
                if language in {
                    "hi",
                    "hinglish",
                }:

                    localized = await _call_llm_for_response(
                        node,
                        context_data=context,
                        user_input=user_input,
                        language=language,
                        asked_flags=asked_flags,
                        recent_responses=turn.recent_responses,
                        state_manager=state_manager,
                    )

                    if localized:
                        return localized

                return _finalize_response(
                    response
                )

        except Exception as exc:

            logger.warning(
                "[DEESCALATION] Failed: %s",
                exc,
            )

        return ""

    # =========================================================================
    # PATH 4 — LOCATION SUGGESTION
    # =========================================================================

    if (
        node_id in {
            "fallback_location",
            "fallback-city",
            "node_fallback_location",
        }
        and _is_location_suggestion_request(
            user_input
        )
    ):

        response = _location_suggestion_response(
            language,
            state_manager,
        )

        return _finalize_response(
            response
        )

    # =========================================================================
    # PATH 5 — LLM RESPONSE
    # =========================================================================

    # IMPORTANT CHANGE:
    #
    # OLD:
    #   if is_llm_node(node) and not turn.node_changed
    #
    # NEW:
    #   if is_llm_node(node)
    #
    # A node change must NOT suppress LLM response generation because
    # the new node's JSON response may be English while the user speaks
    # Hindi/Hinglish.

    if is_llm_node(node):

        logger.info(
            "[LLM RESPONSE] Generating after node transition=%s "
            "node_changed=%s language=%s",
            node_id,
            turn.node_changed,
            language,
        )

        llm_response = await _call_llm_for_response(
            node,
            context_data=context,
            user_input=user_input,
            language=language,
            asked_flags=asked_flags,
            recent_responses=turn.recent_responses,
            state_manager=state_manager,
        )

        if llm_response:

            finalized = _finalize_response(
                llm_response
            )

            if finalized:

                _log_phrase_usage(
                    finalized
                )

                logger.info(
                    "[LLM RESPONSE FINAL] node=%s language=%s response=\"%s\"",
                    node_id,
                    language,
                    finalized,
                )

                return finalized

        logger.warning(
            "[LLM RESPONSE] Failed. "
            "Using localized template fallback for node=%s",
            node_id,
        )

    # =========================================================================
    # PATH 6 — TEMPLATE FALLBACK
    # =========================================================================

    response = _resolve_template_response(
        node,
        context,
        language,
    )

    finalized = (
        _finalize_response(response)
        if response
        else ""
    )

    # -------------------------------------------------------------------------
    # FIX B2 — LANGUAGE GUARD
    #
    # _resolve_template_response() ultimately falls back to node["response"],
    # which is raw ENGLISH text from real_estate.json. When the LLM failed
    # (see FIX B1) that English string was spoken to a Hindi caller verbatim.
    # Never let that happen: swap in the localized node text instead.
    # -------------------------------------------------------------------------

    if (
        finalized
        and language in {"hi", "hinglish"}
        and not _contains_devanagari(finalized)
        and _english_marker_count(finalized) >= 2
    ):

        localized = _get_anti_repeat_nudge(
            node,
            language,
            context=context,
        )

        if localized:

            logger.warning(
                "[LANGUAGE GUARD] English template blocked for language=%s "
                "on node=%s; using localized node text",
                language,
                node_id,
            )

            finalized = _finalize_response(localized)

    # -------------------------------------------------------------------------
    # Anti-repeat
    # -------------------------------------------------------------------------

    if (
        finalized
        and turn.recent_responses
        and _is_repetition(
            finalized,
            turn.recent_responses[-1],
        )
    ):

        logger.warning(
            "[ANTI-REPEAT] Template repeated last response. "
            "Using language-aware nudge."
        )

        nudge = _get_anti_repeat_nudge(
            node,
            language,
            context=context,
        )

        if nudge:
            return _finalize_response(
                nudge
            )

    # =========================================================================
    # PATH 7 — TERMINAL FALLBACK
    # =========================================================================

    if turn.is_terminal:

        if finalized:

            logger.info(
                "[TERMINAL RESPONSE] node=%s language=%s response=\"%s\"",
                node_id,
                language,
                finalized,
            )

            return finalized

    # =========================================================================
    # PATH 8 — GUARANTEED FALLBACK
    # =========================================================================

    if not finalized:

        logger.warning(
            "[FALLBACK] Response generation completely failed."
        )

        if language == "hi":

            finalized = (
                "माफ़ कीजिए, आवाज़ थोड़ी कट रही थी। "
                "क्या आप दोबारा बताएँगे?"
            )

        elif language == "hinglish":

            finalized = (
                "Maaf karna, aawaz thodi cut rahi thi. "
                "Kya aap dobara batayenge?"
            )

        else:

            finalized = (
                "I didn't quite catch that. "
                "Could you repeat?"
            )

    logger.info(
        "[RESPONSE FINAL] node=%s language=%s response=\"%s\"",
        node_id,
        language,
        finalized,
    )

    return finalized


# =============================================================================
# Sync wrapper
# =============================================================================

def generate_response_for_turn_sync(
    turn: TurnResult,
    state_manager: Optional[Any] = None,
) -> str:
    """
    Synchronous wrapper for backward compatibility.
    """

    try:
        loop = asyncio.get_running_loop()

    except RuntimeError:
        loop = None

    if loop and loop.is_running():

        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=1
        ) as pool:

            future = pool.submit(
                asyncio.run,
                generate_response_for_turn(
                    turn,
                    state_manager=state_manager,
                ),
            )

            return future.result(
                timeout=15
            )

    return asyncio.run(
        generate_response_for_turn(
            turn,
            state_manager=state_manager,
        )
    )