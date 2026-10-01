from __future__ import annotations
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional
from langchain_core.messages import AIMessage, HumanMessage

from intelligence.pipeline import ConversationState
from llm.llm import analyze_user_intent, generate_voice_response

logger = logging.getLogger("llm.state_manager")

CONFIG_PATH = Path(__file__).parent.parent / "Updated_Real_Estate_Agent.json"

def load_agent_config() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load agent config: {e}")
        return {}

def is_low_signal(text: str) -> bool:
    """Detects short, affirmative or conversational low-signal confirmation inputs."""
    clean = text.strip().lower().replace(".", "").replace(",", "")
    low_signal_phrases = {
        "yes", "yep", "yeah", "ok", "okay", "sure", "share it", "tell me",
        "go on", "continue", "झाल", "haan", "ho", "acha", "please"
    }
    return len(clean.split()) <= 2 or clean in low_signal_phrases

def is_hard_out(text: str) -> bool:
    """Detects strong refusal / hang up intents."""
    clean = text.lower()
    return any(term in clean for term in ["hang up", "stop calling", "not interested", "bye", "disconnect"])

def merge_slots(state_slots: Dict[str, Any], extracted: Any) -> Dict[str, Any]:
    """Merges newly extracted slots without overwriting existing ones."""
    res = dict(state_slots)
    for field in ["intent", "budget", "bhk", "timeline", "user_name"]:
        # Map ExtractedEntities field names to our slot keys
        entity_field_map = {
            "intent": "intent_value",
            "budget": "budget_range",
            "bhk": "preferred_bhk",
            "timeline": "timeline_weeks",
            "user_name": "user_name",
        }
        entity_field = entity_field_map.get(field, field)
        val = getattr(extracted, entity_field, None)
        if val is not None:
            res[field] = val
    return res

async def process_intent_and_slots(state: ConversationState) -> ConversationState:
    """Runs LLM extraction and intent mapping to update ConversationState."""
    history = []
    for msg in state.get("messages", []):
        role = "assistant" if isinstance(msg, AIMessage) else "user"
        history.append({"role": role, "content": msg.content})

    user_text = state.get("user_input", "")
    
    conversation_context = "\n".join(
        f"{item['role']}: {item['content']}" for item in history
    )

    analysis = await analyze_user_intent(user_text, conversation_context)
    
    # Merge slots
    state["extracted_slots"] = merge_slots(state.get("extracted_slots", {}), analysis.entities)
    
    # Store last intent confidence
    confidence = getattr(analysis, "confidence_score", 1.0)
    state["last_intent_confidence"] = confidence
    
    # Classify current node transition
    target_node = analysis.intent
    
    # Open-domain fallback track
    valid_nodes = {"GREETING", "DISCOVERY", "QUALIFICATION", "LIVE_SEARCH", "OBJECTION_HANDLING", "SCHEDULING", "CLOSING"}
    if (confidence < 0.6 or target_node not in valid_nodes) and not is_low_signal(user_text):
        logger.info(f"Open-domain detour triggered. Confidence: {confidence}, Intent: {target_node}")
        state["pending_return_node"] = state.get("current_node", "DISCOVERY")
        target_node = "OPEN_DOMAIN"
    
    # Deepening Strategy: Intercept low-signal responses (e.g. "Yes", "Okay")
    slots = state["extracted_slots"]
    missing_critical = []
    if not slots.get("intent"):
        missing_critical.append("intent")
    if not slots.get("bhk"):
        missing_critical.append("bhk")
    if not slots.get("budget"):
        missing_critical.append("budget")
        
    if target_node != "OPEN_DOMAIN" and is_low_signal(user_text) and missing_critical:
        logger.info(f"Deepening strategy triggered. User gave low-signal input: '{user_text}'. Missing slots: {missing_critical}.")
        # Keep user in discovery/qualification nodes to collect missing data
        target_node = "QUALIFICATION" if slots.get("intent") else "DISCOVERY"

    # Strict Exit Fencing: Protect CLOSING unless requirements are met
    if target_node == "CLOSING" and missing_critical and not is_hard_out(user_text):
        logger.info("Exit fence triggered: user trying to exit, but critical slots are missing. Routing to QUALIFICATION.")
        target_node = "QUALIFICATION"
        
    state["current_node"] = target_node
    return state

# Node Handlers

import re as _re
import random as _random_greet

def _build_opener_pool() -> list[str]:
    """Build a pool of natural greeting variants from the loaded agent config."""
    try:
        config = load_agent_config()
        global_prompt = config.get("conversationFlow", {}).get("global_prompt", "")
        persona_match = _re.search(r"You are (\w+)", global_prompt)
        persona_name = persona_match.group(1) if persona_match else "Priya"
        company_match = _re.search(
            r"from ([A-Z][A-Za-z\s]+(?:Apartments|Realty|Properties|Group|Homes|Projects|Real Estate))",
            global_prompt,
        )
        company_name = company_match.group(1).strip() if company_match else "Suncity Apartments"
        n, c = persona_name, company_name
        return [
            f"Hi, this is {n} from {c}. I'm calling about your recent property inquiry — just wanted to check if you're looking to buy or rent.",
            f"Hello, {n} here from {c}. I'm reaching out to see if you're currently in the market for a property.",
            f"Hi there, this is {n} from {c}. I'm following up on your property search to see what you're looking for.",
            f"Good day, {n} here from {c}. I wanted to quickly touch base about your property requirement.",
            f"Hi, this is {n} calling from {c}. Just checking in to see if you're looking to buy or rent at the moment.",
            f"Hello, I'm {n} from {c}. I'm calling to help with your property search — are you looking to buy or invest?",
        ]
    except Exception:
        return [
            "Hi, this is Priya calling from Suncity Apartments. I'm following up on your recent property inquiry — just wanted to check if you're looking to buy or rent.",
        ]

_OPENER_POOL: list[str] = _build_opener_pool()

def _get_cold_opener() -> str:
    """Return a random opener from the pool so each session starts differently."""
    return _random_greet.choice(_OPENER_POOL)

# Keep _COLD_OPENER as a module-level alias for backward compatibility;
# handle_greeting now calls _get_cold_opener() at runtime for variety.
_COLD_OPENER = _OPENER_POOL[0]


def _build_slot_context(slots: dict) -> str:
    """
    Build a human-readable summary of what has already been collected.
    This is injected into every LLM prompt so it knows what NOT to ask again.
    """
    known = []
    if slots.get("user_name"):
        known.append(f"name: {slots['user_name']}")
    if slots.get("intent") or slots.get("intent_value"):
        known.append(f"intent: {slots.get('intent') or slots.get('intent_value')}")
    if slots.get("budget") or slots.get("budget_range"):
        known.append(f"budget: {slots.get('budget') or slots.get('budget_range')}")
    if slots.get("bhk") or slots.get("preferred_bhk"):
        known.append(f"BHK preference: {slots.get('bhk') or slots.get('preferred_bhk')}")
    if slots.get("location"):
        known.append(f"location: {slots['location']}")
    if slots.get("timeline") or slots.get("timeline_weeks"):
        known.append(f"timeline: {slots.get('timeline') or slots.get('timeline_weeks')}")
    if not known:
        return ""
    return "Already known from this conversation: " + ", ".join(known) + ". DO NOT ask about any of these again."


def _needs_comment(user_input: str, slots: dict) -> tuple[bool, str]:
    """
    Decides whether the user's input warrants a comment before asking the next question.
    Returns (should_comment: bool, reason: str) where reason describes what to comment on.
    """
    text = (user_input or "").strip().lower()
    words = text.split()

    # Short confirmations / single-word factual answers → go straight to question
    straight_to_question = {
        "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "got it",
        "haan", "ho", "acha", "theek", "ji", "no", "nope", "nahi"
    }
    if text in straight_to_question or (len(words) == 1 and len(text) < 12):
        return False, ""

    # Name introduced → warm acknowledgement (broad check: any multi-word input where name was captured)
    if slots.get("user_name"):
        # Triggers if user just introduced themselves — covers "my name is X", "I'm X", "this is X"
        name_triggers = ["name", "i'm", "i am", "this is", "myself", "it's", "its", "calling"]
        if any(w in text for w in name_triggers) or len(words) >= 3:
            return True, f"acknowledge their name ({slots['user_name']}) warmly — something like 'Nice to talk to you, {slots['user_name']}'"

    # Budget given → comment on what it gets them
    budget = slots.get("budget") or slots.get("budget_range")
    if budget and any(w in text for w in ["lakh", "lacs", "crore", "lakhs", "budget", "cr", "k"]):
        return True, f"comment briefly on what {budget} can get them in the current market"

    # Intent with context → reflect on the decision
    intent = slots.get("intent") or slots.get("intent_value")
    if intent and any(w in text for w in ["buy", "invest", "rent", "own", "myself", "family", "use", "personal"]):
        return True, f"briefly reflect on the {intent} decision — what it means for someone in their situation"

    # BHK given with context → say something about the size
    bhk = slots.get("bhk") or slots.get("preferred_bhk")
    if bhk and len(words) > 1:
        return True, f"say something real about a {bhk} — why it makes sense"

    # Multi-word substantive answer (7+ words) → usually has context worth acknowledging
    if len(words) >= 7:
        return True, "briefly acknowledge what they shared before asking"

    return False, ""



async def handle_greeting(state: ConversationState) -> ConversationState:
    messages = state.get("messages", [])

    # Fire the cold opener if the agent has never spoken yet.
    # We check for AIMessage presence (not list emptiness) because
    # CALL_CONNECTED_TRIGGER is pre-appended as a HumanMessage before
    # LangGraph is invoked, making `messages` non-empty on the very first turn.
    agent_has_spoken = any(isinstance(m, AIMessage) for m in messages)

    if not agent_has_spoken:
        opener = _get_cold_opener()
        state["messages"].append(AIMessage(content=opener))
        state["current_node"] = "DISCOVERY"
        logger.info("[GREETING] Cold opener delivered: %s", opener)
        return state

    # Subsequent turns that route back here: treat as discovery with context
    logger.info("[GREETING] Re-routing to DISCOVERY — agent already greeted.")
    state["current_node"] = "DISCOVERY"
    return await handle_discovery(state)


async def handle_discovery(state: ConversationState) -> ConversationState:
    config = load_agent_config()
    prompt = config.get("conversationFlow", {}).get("global_prompt", "")
    slots = state.get("extracted_slots", {})
    slot_context = _build_slot_context(slots)
    user_input = state.get("user_input", "")
    should_comment, comment_topic = _needs_comment(user_input, slots)

    history = []
    for msg in state["messages"]:
        role = "assistant" if isinstance(msg, AIMessage) else "user"
        history.append({"role": role, "content": msg.content})

    # Determine what the single next question should be
    if not slots.get("intent") and not slots.get("intent_value"):
        next_question_goal = "Ask whether they're looking to buy, rent, or invest."
    elif not slots.get("location"):
        next_question_goal = "Ask which city or area they're considering."
    else:
        next_question_goal = "Ask one question to find out what they need most right now."

    if should_comment:
        style_instruction = (
            f"First, {comment_topic}. Keep it to one short sentence — genuine, not a compliment. "
            f"Then ask: {next_question_goal}"
        )
    else:
        style_instruction = f"Go straight to the question: {next_question_goal}"

    system_prompt = prompt

    discovery_instruction = (
        f"{slot_context}\n\n"
        f"{style_instruction}\n"
        "ONE question only. 10-22 words total. Plain spoken English. No scripted phrases."
    )

    response = await generate_voice_response(
        f"{system_prompt}\n{discovery_instruction}", history, language=state.get("language", "en")
    )
    state["messages"].append(AIMessage(content=response))
    return state

async def handle_qualification(state: ConversationState) -> ConversationState:
    config = load_agent_config()
    prompt = config.get("conversationFlow", {}).get("global_prompt", "")
    slots = state.get("extracted_slots", {})
    user_input = state.get("user_input", "")
    should_comment, comment_topic = _needs_comment(user_input, slots)

    # Determine the single next missing slot
    if not slots.get("intent") and not slots.get("intent_value"):
        next_question = "Ask if they want to buy, rent, or invest."
    elif not slots.get("bhk") and not slots.get("preferred_bhk"):
        next_question = "Ask what size apartment they need — 2 BHK, 3 BHK, or something else."
    elif not slots.get("budget") and not slots.get("budget_range"):
        next_question = "Ask their budget range."
    elif not slots.get("location"):
        next_question = "Ask which city or area they're looking in."
    else:
        next_question = "Ask when they're thinking of making the move — soon, or still exploring."

    if should_comment:
        style_instruction = (
            f"First, {comment_topic}. Keep it to one short, genuine sentence — not a compliment, something real. "
            f"Then ask: {next_question}"
        )
    else:
        style_instruction = f"Go straight to the question: {next_question}"

    system_prompt = prompt

    qual_instruction = (
        f"{_build_slot_context(slots)}\n\n"
        f"{style_instruction}\n"
        "CRITICAL: ONE question only. Do NOT repeat any already-answered question. 10-25 words total."
    )

    history = []
    for msg in state["messages"]:
        role = "assistant" if isinstance(msg, AIMessage) else "user"
        history.append({"role": role, "content": msg.content})

    response = await generate_voice_response(
        f"{prompt}\n{qual_instruction}", history, language=state.get("language", "en")
    )
    state["messages"].append(AIMessage(content=response))
    return state

async def handle_live_search(state: ConversationState, crawler: Any) -> ConversationState:
    """
    Step A: Stream verbal filler statement immediately if no rag_context exists.
    Step B: Fetch live website parsed chunks and generate final answer.
    """
    user_query = state.get("user_input", "")
    
    # Step A: Latency-Masking Filler trigger
    if not state.get("rag_context"):
        state["pending_filler_action"] = "Got it... let me pull up the live board for Suncity Apartments real quick..."
        state["rag_context"] = "PENDING"
        # Return state immediately to let core voice loop stream the filler audio
        return state

    # Step B: Background crawler parsing
    state["pending_filler_action"] = None
    logger.info(f"Executing async live web-scrape for: '{user_query}'")
    
    # Run the crawler with the user query to get semantic chunks
    scraped_context = await crawler.fetch_and_parse(user_query)
    state["rag_context"] = scraped_context
    
    config = load_agent_config()
    prompt = config.get("conversationFlow", {}).get("global_prompt", "")
    
    instruction = (
        "Answer the user's specific question about Suncity Apartments using only the provided context. "
        "Keep it conversational, natural, and under 20 words. No bullet points."
    )
    
    history = []
    for msg in state["messages"]:
        role = "assistant" if isinstance(msg, AIMessage) else "user"
        history.append({"role": role, "content": msg.content})
        
    response = await generate_voice_response(f"{prompt}\n{instruction}", history, context=scraped_context)
    state["messages"].append(AIMessage(content=response))
    
    # Re-route to closing or qualification depending on missing details
    state["current_node"] = "CLOSING" if state.get("extracted_slots", {}).get("timeline") else "QUALIFICATION"
    return state

async def handle_objection_handling(state: ConversationState) -> ConversationState:
    config = load_agent_config()
    prompt = config.get("conversationFlow", {}).get("global_prompt", "")

    language = state.get("language", "en")
    slots = state.get("extracted_slots", {})
    slot_context = _build_slot_context(slots)
    user_input = state.get("user_input", "").lower()

    # Detect specific objection types for targeted handling
    already_spoke_phrases = ["already spoke", "already called", "another agent", "already talked", "someone else called"]
    just_browsing_phrases = ["just browsing", "just looking", "not seriously", "not ready", "just exploring"]
    whatsapp_phrases = ["send on whatsapp", "whatsapp me", "drop a message", "send me details", "message me"]
    how_number_phrases = ["how did you get", "where did you get my number", "who gave you", "data leak", "where you got"]
    scam_phrases = ["scam", "fraud", "fake", "genuine company", "real company", "is this real", "is this legit"]

    if any(p in user_input for p in scam_phrases):
        instruction = (
            f"{slot_context}\n\n"
            "The caller is questioning whether this is a legitimate company or a scam. "
            "Respond like a calm, unbothered professional — briefly and transparently. "
            "Don't over-explain or sound defensive. Something like: 'We're Suncity Apartments — a registered developer. "
            "Happy to share our RERA details if you'd like.' Then smoothly return to the conversation. "
            "10-18 words only. No exclamation marks."
        )
    elif any(p in user_input for p in how_number_phrases):
        instruction = (
            f"{slot_context}\n\n"
            "The caller is asking how you got their number. Be honest and calm — we received their inquiry via a "
            "property portal or referral. Don't be defensive. One brief honest sentence, then pivot gently. "
            "15-20 words total."
        )
    elif any(p in user_input for p in whatsapp_phrases):
        instruction = (
            f"{slot_context}\n\n"
            "The caller wants you to send details over WhatsApp instead of calling. Agree naturally — "
            "'Sure, I can drop a quick note on WhatsApp. I'll need your number though — should I save the one "
            "you're calling from?' Then ask ONE clarifying question to keep the lead warm. 20-25 words."
        )
    elif any(p in user_input for p in already_spoke_phrases):
        instruction = (
            f"{slot_context}\n\n"
            "The caller mentions they already spoke to another agent about this. Acknowledge it without being dismissive — "
            "'Got it, I just wanted to check if there were any open questions on your end.' "
            "Then ask one genuinely useful follow-up. 18-25 words."
        )
    elif any(p in user_input for p in just_browsing_phrases):
        instruction = (
            f"{slot_context}\n\n"
            "The caller says they are just browsing and not seriously looking yet. That's fine — don't push. "
            "Acknowledge it warmly, offer something useful without pressure, like 'No rush at all — I can just flag "
            "good options when they come up. What kind of property were you loosely thinking about?' "
            "20-28 words."
        )
    else:
        # Generic objection handling — existing logic
        if not slots.get("intent") and not slots.get("intent_value"):
            pivot_question = "Ask what they're thinking of doing — buying, renting, or investing."
        elif not slots.get("bhk") and not slots.get("preferred_bhk"):
            pivot_question = "Ask what apartment size would work for them."
        elif not slots.get("budget") and not slots.get("budget_range"):
            pivot_question = "Ask for a rough budget range so you can pull up the right options."
        else:
            pivot_question = "Ask when they might be ready to take a next step."

        instruction = (
            f"{slot_context}\n\n"
            "The user raised a concern or hesitation. Respond like a real person who actually heard them.\n"
            "First, show you understood — one honest sentence that validates what they said, without being overly apologetic.\n"
            "Then ask one question to move forward gently.\n"
            f"Question to ask: {pivot_question}\n"
            "ONE question only. 15-28 words total. No sales pitch, no pressure."
        )

    history = []
    for msg in state["messages"]:
        role = "assistant" if isinstance(msg, AIMessage) else "user"
        history.append({"role": role, "content": msg.content})

    response = await generate_voice_response(
        f"{prompt}\n{instruction}", history, language=language
    )
    state["messages"].append(AIMessage(content=response))
    return state

async def handle_scheduling(state: ConversationState) -> ConversationState:
    config = load_agent_config()
    prompt = config.get("conversationFlow", {}).get("global_prompt", "")
    language = state.get("language", "en")

    instruction = (
        "The user is ready to schedule. Ask them lightly and confidently if they'd be free "
        "this weekend for a quick site visit. Mention Saturday or Sunday as options. "
        "Sound excited for them, not salesy. One question only."
    )

    history = []
    for msg in state["messages"]:
        role = "assistant" if isinstance(msg, AIMessage) else "user"
        history.append({"role": role, "content": msg.content})

    response = await generate_voice_response(
        f"{prompt}\n{instruction}", history, language=language
    )
    state["messages"].append(AIMessage(content=response))
    return state

async def handle_closing(state: ConversationState) -> ConversationState:
    config = load_agent_config()
    prompt = config.get("conversationFlow", {}).get("global_prompt", "")
    language = state.get("language", "en")

    instruction = (
        "Wrap up the call as Priya from Suncity Apartments. Thank them warmly and genuinely — sound like you actually enjoyed chatting. "
        "Wish them well and sign off naturally. Keep it under 15 words."
    )

    history = []
    for msg in state["messages"]:
        role = "assistant" if isinstance(msg, AIMessage) else "user"
        history.append({"role": role, "content": msg.content})

    response = await generate_voice_response(
        f"{prompt}\n{instruction}", history, language=language
    )
    state["messages"].append(AIMessage(content=response))
    return state


async def handle_open_domain_query(state: ConversationState) -> ConversationState:
    """
    Handles any utterance that doesn't cleanly map to a known node —
    off-script tangents, meta-questions about the call, small talk, etc.
    Answers naturally in-character, then re-anchors to the conversation.
    """
    from llm.pipeline_logger import pipeline_logger
    config = load_agent_config()
    prompt = config.get("conversationFlow", {}).get("global_prompt", "")
    language = state.get("language", "en")
    slots = state.get("extracted_slots", {})
    slot_context = _build_slot_context(slots)
    user_input = state.get("user_input", "")
    return_node = state.get("pending_return_node", "DISCOVERY")

    # Log for ongoing knowledge base expansion
    pipeline_logger.log_event("OPEN_DOMAIN_DETOUR", {
        "user_input": user_input,
        "language": language,
        "return_node": return_node,
        "slots": slots,
    })
    logger.info("[OPEN_DOMAIN] Detour for: '%s'. Will return to: %s", user_input, return_node)

    # Determine what to re-anchor to after answering
    if return_node == "QUALIFICATION" or slots.get("intent"):
        if not slots.get("bhk") and not slots.get("preferred_bhk"):
            re_anchor = "After answering, gently ask: what size apartment are they looking for?"
        elif not slots.get("budget") and not slots.get("budget_range"):
            re_anchor = "After answering, gently ask: what budget range should you work with?"
        else:
            re_anchor = "After answering, suggest taking a next step like a site visit."
    else:
        re_anchor = "After answering, naturally ask whether they are looking to buy, rent, or invest."

    instruction = (
        f"{slot_context}\n\n"
        "The caller has asked something outside the property script — it could be small talk, a question about "
        "the call itself, a meta-question about the company, timing, or anything unrelated to buying/renting.\n"
        "Answer briefly and naturally in character as Priya — warm, calm, never robotic.\n"
        "Do not ignore the question to push your own agenda.\n"
        "Do not invent specific facts you don't have (RERA numbers, exact prices, possession dates).\n"
        "After you've answered, return smoothly to the conversation.\n"
        f"{re_anchor}\n"
        "Total response: 20-35 words. No exclamation marks."
    )

    history = []
    for msg in state["messages"]:
        role = "assistant" if isinstance(msg, AIMessage) else "user"
        history.append({"role": role, "content": msg.content})

    response = await generate_voice_response(
        f"{prompt}\n{instruction}", history, language=language
    )
    state["messages"].append(AIMessage(content=response))
    # Restore the return node so the next routing pass picks it up
    state["current_node"] = return_node
    state["pending_return_node"] = None
    return state



import copy

import json
import logging
import uuid
import os
import re
import random
from pathlib import Path
from typing import Any, Dict, Optional

from . import config as cfg
from .llm_response_generator import TurnResult
from .language_utils import localize_template

logger = logging.getLogger(__name__)

SHORT_NOISE = {".", ",", "uh", "ah", "hmm", "hm", "um", "oh", "ohh", "this", "that"}
NON_SKIPPABLE_NAMES = {
    "Smart Greeting",
    "Confirm and End",
    "Confirm Callback",
    "Polite Goodbye",
    "End Conversation",
    "Immediate End Call",
}
ENTITY_KEYS = ("location", "budget", "property_type", "intent_value", "timeline", "callback_date", "callback_time")
DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "Updated_Real_Estate_Agent.json"
INVALID_LOCATION_VALUES = {"location", "place", "area", "there", "nek", "city", "property", "this"}
INVALID_BUDGET_VALUES = {"budget", "price", "amount"}
INVALID_PROPERTY_TYPE_VALUES = {"property", "home", "n property", "bhk"}
KNOWN_LOCATION_WHITELIST = {
    "wakad", "baner", "hinjewadi", "kharadi", "pune", "mumbai",
    "kothrud", "viman nagar", "hadapsar", "aundh", "pimpri",
    "chinchwad", "bavdhan", "pashan", "sus", "lavale",
    "magarpatta", "kondhwa", "undri", "katraj", "sinhagad road",
    "deccan", "shivajinagar",
}
INVALID_TIMELINE_VALUES = {
    "yesterday", "last week", "last month", "last year",
    "ago", "previous", "past",
}
LOCATION_NORMALIZATION = {
    # Baner variants
    "banner": "Baner",
    "banar": "Baner",
    "baner": "Baner",
    "banr": "Baner",
    # Wakad variants
    "wakud": "Wakad",
    "wakad": "Wakad",
    "waked": "Wakad",
    "vakad": "Wakad",
    # Hinjewadi variants
    "hinjewdi": "Hinjewadi",
    "hinjwadi": "Hinjewadi",
    "hinjewadi": "Hinjewadi",
    # Kharadi variants
    "kharady": "Kharadi",
    "karate": "Kharadi",
    "kharadi": "Kharadi",
    "karadi": "Kharadi",
    "kharad": "Kharadi",
    # Gurgaon variants
    "gurugram": "Gurgaon",
    "gurgaon": "Gurgaon",
    # Jaipur variants
    "jaipur": "Jaipur",
    "uj-ae pur": "Jaipur",
    "ujae pur": "Jaipur",
    "ujaepur": "Jaipur",
    "udaypur": "Jaipur",
    "udaipur": "Jaipur",
    "ujae poor": "Jaipur",
    # Zirakpur variants
    "zirakpur": "Zirakpur",
    # Mathura variants
    "mathura": "Mathura",
    "vrindavan": "Mathura",
}
HINDI_LOCATION_TRANSLITERATION = {
    "वाकड़": "Wakad",
    "वाकड": "Wakad",
    "बानेर": "Baner",
    "बानर": "Baner",
    "बनर": "Baner",
    "बॅनर": "Baner",
    "हिंजवडी": "Hinjewadi",
    "हिंजेवाडी": "Hinjewadi",
    "हिंजवाडी": "Hinjewadi",
    "खराडी": "Kharadi",
    "खरादी": "Kharadi",
    "खारडी": "Kharadi",
    # Gurgaon
    "गुड़गांव": "Gurgaon",
    "गुड़गांव": "Gurgaon",
    "गुरुग्राम": "Gurgaon",
    "गुड़गाँव": "Gurgaon",
    # Jaipur
    "जयपुर": "Jaipur",
    "जयपूर": "Jaipur",
    "उजए पूर": "Jaipur",
    "उजएपुर": "Jaipur",
    "उदयपुर": "Jaipur",
    # Zirakpur
    "जीरकपुर": "Zirakpur",
    "झिरकपुर": "Zirakpur",
    # Mathura
    "मथुरा": "Mathura",
    # Vrindavan
    "वृंदावन": "Mathura",
    "वृन्दावन": "Mathura",
}
VISIT_SCHEDULING_NODES = {"node-1736323961832", "node-1735265015507", "node-visit"}
CALLBACK_SCHEDULING_NODE_ID = "node-1736492391269"

# ── Context-aware deny routing ────────────────────────────────────────────────
WRONG_PERSON_END_NODE_ID = "node-wrong-person-end"
POLITE_END_NODE_ID = "node-1735969972303"       # End Conversation
RESCHEDULE_VISIT_NODE_ID = "node_fallback_reschedule"

# Node sets where each deny sub-type applies
DENY_IDENTITY_NODES = {"node-1767592854176", "node-greeting"}                        # Smart Greeting
DENY_TIME_NODES = {"node-1735264873079", "node-1735970090937", "node-availability"}      # Availability Check, Re-engage
DENY_INTEREST_NODES = {"node-1735264921453", "node-explain"}                        # Ask Intent
DENY_VISIT_NODES = {"node-1736323961832", "node-1735265015507", "node-visit"}     # Share Property, Site Visit

ALL_DENY_INTENTS = {"deny", "deny_identity", "deny_interest", "deny_time", "deny_visit_time"}
# Edges with these condition keywords auto-advance without user input
SKIP_EDGE_MARKERS = {"skip", "skip response"}

# ── Node Classification Map (Fix #6: Avoid fragile string matching) ──────────
NODE_GOALS = {
    "node-1767592854176": "greet_and_confirm_identity",
    "node-greeting": "greet_and_confirm_identity",
    "node-1735264873079": "ask_availability",
    "node-1735970090937": "ask_availability",
    "node-availability": "ask_availability",
    "node-1735264921453": "ask_intent",
    "node-explain": "ask_intent",
    "fallback_intent": "ask_intent",
    "fallback-intent": "ask_intent",
    "node-1735267546732": "ask_location", # Initial combined node
    "node-ask-city": "ask_location",
    "fallback_location": "ask_location",
    "fallback-city": "ask_location",
    "fallback_budget": "ask_budget",
    "fallback-budget": "ask_budget",
    "node-1736323961832": "share_property",
    "node-1735265015507": "ask_visit_time",
    "node-visit": "ask_visit_time",
    "fallback_visit_datetime": "ask_visit_time",
    "fallback-visit": "ask_visit_time",
    "node-1736492391269": "ask_callback_time",
    "node-callback": "ask_callback_time",
    "fallback_callback_time": "ask_callback_time",
    "node-1735265209472": "confirm_and_close",
    "node-1736567518748": "confirm_and_close",
    "node-confirm-callback": "confirm_and_close",
    "node-confirm-visit": "confirm_and_close",
}

# Nodes that should auto-advance through skip edges after delivering response
AUTO_ADVANCE_NODES = {
    "node-1736492925252",  # Confirm and End
    "node-1736567518748",  # Confirm Callback
    "node-1736492485610",  # Polite Goodbye
}
FALLBACK_ESCALATION = {
    "fallback_location": "You can choose Wakad, Baner, Hinjewadi or Kharadi.",
    "fallback_budget": "Typical budgets range from 20 lakh to 1.5 crore. What range works for you?",
    "fallback_visit_datetime": "Would Saturday or Sunday this week work for you?",
    "fallback_callback_time": "Would morning or evening be more convenient?",
}
MAX_FALLBACK_ATTEMPTS = 2
LOCATION_SUGGESTION_PHRASES = (
    "suggest",
    "recommend",
    "city",
    "cities",
    "area",
    "areas",
    "options",
    "available",
    "availability",
    "offer me",
    "can you offer",
    "what can you offer",
    "which area",
    "best location",
    "good location",
    "any options",
)
UNCERTAIN_PHRASES = (
    "i don't know",
    "dont know",
    "don't know",
    "not sure",
    "maybe",
    "not certain",
    "unsure",
)

# ── Behavioral refinement configuration ──────────────────────────────────────
BRIDGE_ENABLED           = False
VAGUE_DETECTION_ENABLED  = False
HOSTILE_DETECTION_ENABLED = False

# Bridge words are ONLY used for unclear / fallback / noise situations.
# Normal flow responses are returned verbatim from the JSON schema.
FALLBACK_BRIDGE_PHRASES = {
    "unclear":                "Sorry, I didn't catch that.",
    "unclear_intent":         "Got it, just to clarify —",
    "unclear_location":       "Got it, just to clarify —",
    "unclear_budget":         "Got it, just to clarify —",
    "unclear_property_type":  "Got it, just to clarify —",
    "unclear_visit_datetime": "Sorry, I didn't catch that.",
    "unclear_callback_time":  "Sorry, I didn't catch that.",
}

CLARIFICATION_TEMPLATES = {
    "provide_location": "Did you mean you're looking in a specific city?",
    "provide_budget":   "Did you mean a particular budget range?",
    "provide_intent":   "Did you mean you're looking to buy or rent?",
    "unclear":          "Could you say that again in a different way?",
}

GUIDANCE_RESPONSES = {
    "budget":   "No problem — are you thinking more budget-friendly, mid-range, or premium?",
    "location": "Sure — are you open to areas near IT hubs, or do you prefer quieter residential zones?",
}

DEESCALATION_RESPONSES = [
    "Understood. Let me focus on what's most useful for you.",
    "Fair enough. I'll keep this brief and practical.",
    "Noted. What would be most helpful right now?",
]

FILLER_STARTERS = {"uh", "um", "ah", "like", "so", "er", "hmm"}

VAGUE_TOKENS = {
    "budget":   ["flexible", "not sure", "reasonable", "affordable", "depends",
                 "whatever", "not too much", "moderate", "medium"],
    "location": ["anywhere", "not sure", "somewhere", "any area", "near",
                 "doesn't matter", "flexible", "good area"],
}

HOSTILE_TOKENS = [
    "stupid", "idiot", "useless", "waste", "shut up", "stop",
    "terrible", "worst", "hate", "awful", "rubbish", "garbage",
    "don't want", "leave me", "go away", "not helpful",
    "fuck", "shit", "bitch", "ass", "damn", "screw you",
    "die", "kill", "bloody", "bastard", "crap",
]
GOODBYE_PHRASES = (
    "bye",
    "bye bye",
    "goodbye",
    "good bye",
    "good night",
    "have a good night",
    "have good night",
    "see you",
    "talk later",
)

OPENING_NODE_RESPONSES = {
    # Step 1: identity confirmation
    "node-1767592854176": "Hey, this is Neha — am I speaking with {{name}}?",
    # Step 2: availability check
    "node-1735264873079": "Is this a good time to speak?",
    # Step 3 + Step 4: context + intent discovery
    "node-1735264921453": "I actually came across your interest in property. Are you exploring for yourself or as an investment?",
    "node-1736567518748": "Thank you, we'll call you around {{timeline}}. Have a great day!",
}

BUSY_TIME_HINTS = (
    # Core
    "busy", "meeting", "call later", "not now", "later", "driving", "occupied",
    # Extended busy signals
    "in a meeting", "on a call", "at work", "at office", "working",
    "cant talk", "can't talk", "cant speak", "can't speak",
    "not a good time", "bad time", "wrong time", "bad moment",
    "call back", "call me back", "call me later", "ring me later",
    "travelling", "traveling", "in traffic", "on the way",
    "eating", "having lunch", "having dinner", "having breakfast",
    "sleeping", "resting", "tired", "not free", "not available",
    "hospital", "doctor", "emergency", "out of station",
    "thoda time de", "baad mein", "abhi nahi", "baad mein call karo",
    "give me some time", "give me a minute", "two minutes", "five minutes",
    "little busy", "bit busy", "slightly busy", "kinda busy",
    "weekend", "evening", "tonight", "tomorrow",
    "in a rush", "rushing", "hurrying", "very busy", "super busy",
)

NOT_INTERESTED_HINTS = (
    "not interested",
    "not looking",
    "no requirement",
    "dont need",
    "don't need",
    "not now",
)

INTERESTED_HINTS = (
    # Core
    "yes", "yeah", "sure", "go ahead", "tell me", "interested", "ok", "okay",
    # Extended available/interested signals
    "yep", "yup", "of course", "absolutely", "definitely", "certainly",
    "please", "please tell me", "i'm free", "i am free", "free now",
    "available", "available now", "speak now", "go on", "continue",
    "what is it", "what did you want", "what's it about", "what is it about",
    "haan", "haan bolo", "bolo", "batao", "theek hai", "bilkul",
    "i have time", "have two minutes", "have a minute", "have some time",
    "few minutes", "two minutes", "couple minutes", "quick call is fine",
    "good time", "perfect time", "right time",
    "hi yes", "yes hi", "speaking", "yes speaking", "yes this is",
    "it's me", "its me", "that's me", "thats me", "yes it is", "yes i am",
    "fine go ahead", "alright go ahead", "sure go ahead",
    "not busy", "not in a meeting", "i'm available", "i'm listening",
)

CALLBACK_PART_OF_DAY_WINDOWS = {
    "morning": (10 * 60 + 15, 11 * 60 + 45),      # 10:15 AM - 11:45 AM
    "afternoon": (14 * 60 + 15, 16 * 60 + 45),    # 2:15 PM - 4:45 PM
    "evening": (18 * 60 + 15, 20 * 60 + 45),      # 6:15 PM - 8:45 PM
    "night": (20 * 60 + 15, 21 * 60 + 45),        # 8:15 PM - 9:45 PM
}

PURPOSE_CLARIFICATION_PHRASES = (
    "who is this",
    "who are you",
    "what is this about",
    "what is it about",
    "whats this about",
    "what's this about",
    "why are you calling",
    "why did you call",
    "purpose of call",
    "kya hai",
    "kis baare",
)


def _log(tag: str, message: str) -> None:
    logger.info("[%s] %s", tag, message)


# ── Behavioral refinement helpers ────────────────────────────────────────────

def _normalise_stt(text: str) -> str:
    """
    Clean common STT artefacts before intent extraction.
    Operates on words only — no regex for performance.
    Steps (in order):
      1. Collapse immediate word repetitions: "I I want" → "I want"
      2. Strip leading filler words: "uh", "um", "ah", "like", "so", "you know"
      3. Collapse multiple spaces
    Never removes content words. Never translates or corrects spelling.
    """
    words = text.strip().split()

    # Step 1 — deduplicate adjacent identical words (case-insensitive)
    deduped = []
    for word in words:
        if not deduped or word.lower() != deduped[-1].lower():
            deduped.append(word)

    # Step 2 — strip leading fillers (one pass only — preserve content)
    while deduped and deduped[0].lower().strip(".,") in FILLER_STARTERS:
        deduped.pop(0)

    return " ".join(deduped).strip()


def _is_vague_answer(text: str, field: str) -> bool:
    """
    Return True if user gave a vague non-answer for a specific field.
    Used to offer guided defaults instead of repeating the same question.
    """
    t = text.lower()
    return any(v in t for v in VAGUE_TOKENS.get(field, []))


def _get_guidance_response(field: str) -> str:
    """Return a static guidance response for a vague slot answer."""
    return GUIDANCE_RESPONSES.get(field, "Could you give me a rough idea to help narrow it down?")


def _get_bridge(intent: str) -> str:
    """Return a short bridge phrase for unclear/fallback intents only. Empty string otherwise."""
    return FALLBACK_BRIDGE_PHRASES.get(intent, "")


def _is_hostile(text: str) -> bool:
    """
    Detect clearly hostile or dismissive input.
    Lightweight keyword check — no ML, no API call.
    """
    t = text.lower()
    return any(token in t for token in HOSTILE_TOKENS)


def _load_default_flow() -> dict[str, Any]:
    try:
        with DEFAULT_SCHEMA_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        logger.error("Failed to load state schema from %s: %s", DEFAULT_SCHEMA_PATH, exc)
        return {}
    return data.get("conversationFlow", {})


_FLOW = _load_default_flow()
_NODE_MAP: dict[str, dict[str, Any]] = {node["id"]: node for node in _FLOW.get("nodes", []) if "id" in node}


def _build_intent_index(nodes: list[dict[str, Any]]) -> dict[str, str]:
    """
    Map intent_trigger -> node_id for all nodes.
    If two nodes share a trigger, last one wins.
    """
    intent_index: dict[str, str] = {}
    for node in nodes:
        for trigger in node.get("intent_triggers") or []:
            previous = intent_index.get(trigger)
            if previous and previous != node["id"]:
                _log("WARN", f"intent trigger '{trigger}' remapped from {previous} to {node['id']}")
            intent_index[trigger] = node["id"]
    return intent_index


_INTENT_INDEX: dict[str, str] = _build_intent_index(_FLOW.get("nodes", []))


# ── Phrase Bank ───────────────────────────────────────────────────────────────

def _build_phrase_bank(nodes: list[dict[str, Any]]) -> list[str]:
    """
    Extract all approved phrases from the JSON conversation file and
    hardcoded constants.  Returns a deduplicated, ordered list of strings
    that the LLM is allowed to draw from when composing responses.
    """
    phrases: list[str] = []

    # 1. Node responses and missing-slot overrides
    for node in nodes:
        resp = node.get("response")
        if isinstance(resp, str) and resp.strip():
            phrases.append(resp.strip())
        msr = node.get("missing_slot_responses")
        if isinstance(msr, dict):
            for v in msr.values():
                if isinstance(v, str) and v.strip():
                    phrases.append(v.strip())
        instruction = node.get("instruction", {})
        if isinstance(instruction, dict):
            itext = instruction.get("text", "")
            if isinstance(itext, str) and itext.strip():
                phrases.append(itext.strip())

    # 2. Hardcoded behavioural phrases
    for v in FALLBACK_BRIDGE_PHRASES.values():
        phrases.append(v)
    for v in CLARIFICATION_TEMPLATES.values():
        phrases.append(v)
    for v in GUIDANCE_RESPONSES.values():
        phrases.append(v)
    for v in DEESCALATION_RESPONSES:
        phrases.append(v)
    for v in FALLBACK_ESCALATION.values():
        phrases.append(v)

    # 3. Standard acknowledgements
    phrases.extend(["Got it.", "Understood.", "Okay.", "Sure.", "No problem.", "Makes sense."])

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for p in phrases:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


_PHRASE_BANK: list[str] = _build_phrase_bank(_FLOW.get("nodes", []))


def get_phrase_bank() -> list[str]:
    """Return the list of approved phrases from the JSON conversation file."""
    return list(_PHRASE_BANK)


def _match_phrases_used(response: str, bank: list[str]) -> list[str]:
    """
    Return which phrase bank entries appear (fully or partially) in the response.
    Used for [JSON PHRASES USED] logging.
    """
    resp_lower = response.lower()
    matched: list[str] = []
    for phrase in bank:
        # Check if a meaningful fragment (4+ words) of the phrase is in the response
        words = phrase.split()
        if len(words) <= 3:
            if phrase.lower().rstrip(".!?,") in resp_lower:
                matched.append(phrase)
        else:
            # Check sliding windows of 4 words from the phrase
            for i in range(len(words) - 3):
                fragment = " ".join(words[i:i + 4]).lower()
                if fragment in resp_lower:
                    matched.append(phrase)
                    break
    return matched


def find_node_by_intent(intent: str) -> dict[str, Any] | None:
    """Return the node mapped to the given intent, if any."""
    node_id = _INTENT_INDEX.get(intent)
    return _NODE_MAP.get(node_id) if node_id else None


def _is_actionable(text: str) -> bool:
    """
    Return False for input too weak to extract intent from.
    Allow short confirmations through. Block empty, punctuation-only, or noise.
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    if t in {"yes", "yeah", "yep", "ok", "okay", "sure", "no", "nope", "nah"}:
        return True
    if len(t) < 2:
        return False
    if t in SHORT_NOISE:
        return False
    if re.fullmatch(r"[\W_]+", t):
        return False
    if not any(c.isalpha() for c in t):
        return False
    return True


def _has_availability_confirmation(text: str) -> bool:
    cleaned = re.sub(r"[^\w\s]", " ", (text or "").lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return False
        
    # M6 FIX: Add negation awareness so "No, I am not free" or "No time" doesn't falsely match
    negations = {"no", "nahi", "not", "busy", "later"}
    words = cleaned.split()
    if any(neg in words[:3] for neg in negations):
        return False
        
    words_set = set(words)
    exact_confirmations = {
        "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "fine",
        "haan", "han", "ji", "theek", "bilkul",
    }
    if words_set & exact_confirmations:
        return True
    confirmation_phrases = (
        "go ahead", "tell me", "go on", "continue", "i have time",
        "have time", "i am free", "i'm free", "free now", "i am listening",
        "i'm listening", "have two minutes", "two minutes",
    )
    return any(phrase in cleaned for phrase in confirmation_phrases)


def should_answer_user_question(user_input: str) -> bool:
    """Return True when the user needs a brief answer before flow continues."""
    return _detect_user_question(user_input) is not None

def _detect_user_question(user_text: str) -> str | None:
    """Return question type if user is asking a meta-question, else None."""
    text = re.sub(r"[^\w\s'?]", " ", (user_text or "").lower())
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None

    identity_markers = (
        "who are you", "who is this", "who's this", "your name",
        "kaun ho", "kaun bol", "aap kaun", "kon bol",
    )
    purpose_markers = (
        "why are you calling", "why did you call", "what is this about",
        "what is it about", "what's it about", "whats this about",
        "what are you talking about", "what is this", "what is it",
        "purpose of call", "reason for call", "kis baare", "kyu call",
        "kyun call", "kya baat", "kya hai",
    )
    confusion_markers = (
        "i don't understand", "i dont understand", "not clear",
        "what do you mean", "what are you saying", "confused",
        "samajh nahi", "samjha nahi", "clear nahi",
    )
    off_topic_markers = (
        "weather", "prime minister", "pm", "stock market",
        "cricket", "score", "joke", "song", "sing a song",
        "mausam", "barish", "modi", "khana khaya", "lunch kiya",
        "how are you", "kaise ho", "kasa ahes",
    )

    if any(marker in text for marker in identity_markers):
        return "identity"
    if any(marker in text for marker in purpose_markers):
        return "purpose"
    if any(marker in text for marker in confusion_markers):
        return "confusion"
    if any(marker in text for marker in off_topic_markers):
        return "off_topic"
    return None


def _resolve_template(
    node: dict[str, Any],
    data: dict[str, Any],
    language: str = "en",
) -> str:
    """Fill {{placeholders}} in a node's template response. No LLM. No generation logic."""
    template = OPENING_NODE_RESPONSES.get(str(node.get("id") or ""), node.get("response"))
    missing_slot_responses = node.get("missing_slot_responses")
    if isinstance(missing_slot_responses, dict):
        collects = node.get("collects")
        missing_slots: list[str] = []
        if isinstance(collects, list):
            missing_slots = [slot for slot in collects if not data.get(slot)]
        if len(missing_slots) == 1:
            override = missing_slot_responses.get(missing_slots[0])
            if isinstance(override, str) and override.strip():
                template = override
    if template is None:
        template = node.get("instruction", {}).get("text", "")

    def fill(match: re.Match[str]) -> str:
        key = match.group(1)
        if key == "name":
            val = data.get("name") or data.get("lead_name") or data.get("lead") or "there"
        else:
            val = data.get(key)
        return str(val) if val else ""

    localized_template = localize_template(template, language)
    resolved = re.sub(r"\{\{(\w+)\}\}", fill, localized_template).strip()
    resolved = re.sub(r" +", " ", resolved)
    return resolved


_FILLER_OPENERS = (
    "sure,",
    "sure -",
    "sure —",
    "great,",
    "great -",
    "great —",
    "absolutely,",
    "absolutely -",
    "absolutely —",
)


def _enforce_single_question(text: str) -> str:
    question_count = text.count("?")
    if question_count <= 1:
        return text
    first_seen = False
    chars: list[str] = []
    for ch in text:
        if ch == "?":
            if first_seen:
                chars.append(".")
            else:
                chars.append("?")
                first_seen = True
        else:
            chars.append(ch)
    return "".join(chars)


def _remove_filler_openers(text: str) -> str:
    cleaned = text.strip()
    lowered = cleaned.lower()
    for filler in _FILLER_OPENERS:
        if lowered.startswith(filler):
            cleaned = cleaned[len(filler):].lstrip(" ,.-—")
            break
    return cleaned


def _finalize_response_text(text: str) -> str:
    """Apply production response constraints before TTS."""
    if not text or not text.strip():
        return text
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = _remove_filler_openers(cleaned)
    cleaned = _enforce_single_question(cleaned)
    cleaned = _truncate_response(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# ---------------------------------------------------------------------------
# Informational-query gate — decides if LLM fallback is permitted
# ---------------------------------------------------------------------------

_QUESTION_STARTERS = (
    "what", "which", "where", "how", "why",
    "is", "are", "does", "do", "can", "should",
)


def _is_informational_query(text: str, intent: str) -> bool:
    """
    Return True only if the user is asking an informational question
    that is not a structured slot-filling response.

    Conditions (ALL must be true):
    1. intent is "ask_off_topic" or "unclear"
    2. text contains a question indicator:
       - ends with "?"  OR
       - starts with a question word
    3. text is at least 4 words long (avoids noise like "what?")
    """
    if intent not in ("ask_off_topic", "unclear"):
        return False
    t = text.strip().lower()
    words = t.split()
    if len(words) < 4:
        return False
    has_question_mark = t.endswith("?")
    has_question_word = any(t.startswith(w) for w in _QUESTION_STARTERS)
    return has_question_mark or has_question_word



import copy

import json
import logging
import uuid
import os
import re
import random
from pathlib import Path
from typing import Any, Dict, Optional

from . import config as cfg
from .llm_response_generator import TurnResult
from .language_utils import localize_template

logger = logging.getLogger(__name__)

SHORT_NOISE = {".", ",", "uh", "ah", "hmm", "hm", "um", "oh", "ohh", "this", "that"}
NON_SKIPPABLE_NAMES = {
    "Smart Greeting",
    "Confirm and End",
    "Confirm Callback",
    "Polite Goodbye",
    "End Conversation",
    "Immediate End Call",
}
ENTITY_KEYS = ("location", "budget", "property_type", "intent_value", "timeline", "callback_date", "callback_time")
DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "Updated_Real_Estate_Agent.json"
INVALID_LOCATION_VALUES = {"location", "place", "area", "there", "nek", "city", "property", "this"}
INVALID_BUDGET_VALUES = {"budget", "price", "amount"}
INVALID_PROPERTY_TYPE_VALUES = {"property", "home", "n property", "bhk"}
KNOWN_LOCATION_WHITELIST = {
    "wakad", "baner", "hinjewadi", "kharadi", "pune", "mumbai",
    "kothrud", "viman nagar", "hadapsar", "aundh", "pimpri",
    "chinchwad", "bavdhan", "pashan", "sus", "lavale",
    "magarpatta", "kondhwa", "undri", "katraj", "sinhagad road",
    "deccan", "shivajinagar",
}
INVALID_TIMELINE_VALUES = {
    "yesterday", "last week", "last month", "last year",
    "ago", "previous", "past",
}
LOCATION_NORMALIZATION = {
    # Baner variants
    "banner": "Baner",
    "banar": "Baner",
    "baner": "Baner",
    "banr": "Baner",
    # Wakad variants
    "wakud": "Wakad",
    "wakad": "Wakad",
    "waked": "Wakad",
    "vakad": "Wakad",
    # Hinjewadi variants
    "hinjewdi": "Hinjewadi",
    "hinjwadi": "Hinjewadi",
    "hinjewadi": "Hinjewadi",
    # Kharadi variants
    "kharady": "Kharadi",
    "karate": "Kharadi",
    "kharadi": "Kharadi",
    "karadi": "Kharadi",
    "kharad": "Kharadi",
    # Gurgaon variants
    "gurugram": "Gurgaon",
    "gurgaon": "Gurgaon",
    # Jaipur variants
    "jaipur": "Jaipur",
    "uj-ae pur": "Jaipur",
    "ujae pur": "Jaipur",
    "ujaepur": "Jaipur",
    "udaypur": "Jaipur",
    "udaipur": "Jaipur",
    "ujae poor": "Jaipur",
    # Zirakpur variants
    "zirakpur": "Zirakpur",
    # Mathura variants
    "mathura": "Mathura",
    "vrindavan": "Mathura",
}
HINDI_LOCATION_TRANSLITERATION = {
    "वाकड़": "Wakad",
    "वाकड": "Wakad",
    "बानेर": "Baner",
    "बानर": "Baner",
    "बनर": "Baner",
    "बॅनर": "Baner",
    "हिंजवडी": "Hinjewadi",
    "हिंजेवाडी": "Hinjewadi",
    "हिंजवाडी": "Hinjewadi",
    "खराडी": "Kharadi",
    "खरादी": "Kharadi",
    "खारडी": "Kharadi",
    # Gurgaon
    "गुड़गांव": "Gurgaon",
    "गुड़गांव": "Gurgaon",
    "गुरुग्राम": "Gurgaon",
    "गुड़गाँव": "Gurgaon",
    # Jaipur
    "जयपुर": "Jaipur",
    "जयपूर": "Jaipur",
    "उजए पूर": "Jaipur",
    "उजएपुर": "Jaipur",
    "उदयपुर": "Jaipur",
    # Zirakpur
    "जीरकपुर": "Zirakpur",
    "झिरकपुर": "Zirakpur",
    # Mathura
    "मथुरा": "Mathura",
    # Vrindavan
    "वृंदावन": "Mathura",
    "वृन्दावन": "Mathura",
}
VISIT_SCHEDULING_NODES = {"node-1736323961832", "node-1735265015507", "node-visit"}
CALLBACK_SCHEDULING_NODE_ID = "node-1736492391269"

# ── Context-aware deny routing ────────────────────────────────────────────────
WRONG_PERSON_END_NODE_ID = "node-wrong-person-end"
POLITE_END_NODE_ID = "node-1735969972303"       # End Conversation
RESCHEDULE_VISIT_NODE_ID = "node_fallback_reschedule"

# Node sets where each deny sub-type applies
DENY_IDENTITY_NODES = {"node-1767592854176", "node-greeting"}                        # Smart Greeting
DENY_TIME_NODES = {"node-1735264873079", "node-1735970090937", "node-availability"}      # Availability Check, Re-engage
DENY_INTEREST_NODES = {"node-1735264921453", "node-explain"}                        # Ask Intent
DENY_VISIT_NODES = {"node-1736323961832", "node-1735265015507", "node-visit"}     # Share Property, Site Visit

ALL_DENY_INTENTS = {"deny", "deny_identity", "deny_interest", "deny_time", "deny_visit_time"}
# Edges with these condition keywords auto-advance without user input
SKIP_EDGE_MARKERS = {"skip", "skip response"}

# ── Node Classification Map (Fix #6: Avoid fragile string matching) ──────────
NODE_GOALS = {
    "node-1767592854176": "greet_and_confirm_identity",
    "node-greeting": "greet_and_confirm_identity",
    "node-1735264873079": "ask_availability",
    "node-1735970090937": "ask_availability",
    "node-availability": "ask_availability",
    "node-1735264921453": "ask_intent",
    "node-explain": "ask_intent",
    "fallback_intent": "ask_intent",
    "fallback-intent": "ask_intent",
    "node-1735267546732": "ask_location", # Initial combined node
    "node-ask-city": "ask_location",
    "fallback_location": "ask_location",
    "fallback-city": "ask_location",
    "fallback_budget": "ask_budget",
    "fallback-budget": "ask_budget",
    "node-1736323961832": "share_property",
    "node-1735265015507": "ask_visit_time",
    "node-visit": "ask_visit_time",
    "fallback_visit_datetime": "ask_visit_time",
    "fallback-visit": "ask_visit_time",
    "node-1736492391269": "ask_callback_time",
    "node-callback": "ask_callback_time",
    "fallback_callback_time": "ask_callback_time",
    "node-1735265209472": "confirm_and_close",
    "node-1736567518748": "confirm_and_close",
    "node-confirm-callback": "confirm_and_close",
    "node-confirm-visit": "confirm_and_close",
}

# Nodes that should auto-advance through skip edges after delivering response
AUTO_ADVANCE_NODES = {
    "node-1736492925252",  # Confirm and End
    "node-1736567518748",  # Confirm Callback
    "node-1736492485610",  # Polite Goodbye
}
FALLBACK_ESCALATION = {
    "fallback_location": "You can choose Wakad, Baner, Hinjewadi or Kharadi.",
    "fallback_budget": "Typical budgets range from 20 lakh to 1.5 crore. What range works for you?",
    "fallback_visit_datetime": "Would Saturday or Sunday this week work for you?",
    "fallback_callback_time": "Would morning or evening be more convenient?",
}
MAX_FALLBACK_ATTEMPTS = 2
LOCATION_SUGGESTION_PHRASES = (
    "suggest",
    "recommend",
    "city",
    "cities",
    "area",
    "areas",
    "options",
    "available",
    "availability",
    "offer me",
    "can you offer",
    "what can you offer",
    "which area",
    "best location",
    "good location",
    "any options",
)
UNCERTAIN_PHRASES = (
    "i don't know",
    "dont know",
    "don't know",
    "not sure",
    "maybe",
    "not certain",
    "unsure",
)

# ── Behavioral refinement configuration ──────────────────────────────────────
BRIDGE_ENABLED           = False
VAGUE_DETECTION_ENABLED  = False
HOSTILE_DETECTION_ENABLED = False

# Bridge words are ONLY used for unclear / fallback / noise situations.
# Normal flow responses are returned verbatim from the JSON schema.
FALLBACK_BRIDGE_PHRASES = {
    "unclear":                "Sorry, I didn't catch that.",
    "unclear_intent":         "Got it, just to clarify —",
    "unclear_location":       "Got it, just to clarify —",
    "unclear_budget":         "Got it, just to clarify —",
    "unclear_property_type":  "Got it, just to clarify —",
    "unclear_visit_datetime": "Sorry, I didn't catch that.",
    "unclear_callback_time":  "Sorry, I didn't catch that.",
}

CLARIFICATION_TEMPLATES = {
    "provide_location": "Did you mean you're looking in a specific city?",
    "provide_budget":   "Did you mean a particular budget range?",
    "provide_intent":   "Did you mean you're looking to buy or rent?",
    "unclear":          "Could you say that again in a different way?",
}

GUIDANCE_RESPONSES = {
    "budget":   "No problem — are you thinking more budget-friendly, mid-range, or premium?",
    "location": "Sure — are you open to areas near IT hubs, or do you prefer quieter residential zones?",
}

DEESCALATION_RESPONSES = [
    "Understood. Let me focus on what's most useful for you.",
    "Fair enough. I'll keep this brief and practical.",
    "Noted. What would be most helpful right now?",
]

FILLER_STARTERS = {"uh", "um", "ah", "like", "so", "er", "hmm"}

VAGUE_TOKENS = {
    "budget":   ["flexible", "not sure", "reasonable", "affordable", "depends",
                 "whatever", "not too much", "moderate", "medium"],
    "location": ["anywhere", "not sure", "somewhere", "any area", "near",
                 "doesn't matter", "flexible", "good area"],
}

HOSTILE_TOKENS = [
    "stupid", "idiot", "useless", "waste", "shut up", "stop",
    "terrible", "worst", "hate", "awful", "rubbish", "garbage",
    "don't want", "leave me", "go away", "not helpful",
    "fuck", "shit", "bitch", "ass", "damn", "screw you",
    "die", "kill", "bloody", "bastard", "crap",
]
GOODBYE_PHRASES = (
    "bye",
    "bye bye",
    "goodbye",
    "good bye",
    "good night",
    "have a good night",
    "have good night",
    "see you",
    "talk later",
)

OPENING_NODE_RESPONSES = {
    # Step 1: identity confirmation
    "node-1767592854176": "Hey, this is Neha — am I speaking with {{name}}?",
    # Step 2: availability check
    "node-1735264873079": "Is this a good time to speak?",
    # Step 3 + Step 4: context + intent discovery
    "node-1735264921453": "I actually came across your interest in property. Are you exploring for yourself or as an investment?",
    "node-1736567518748": "Thank you, we'll call you around {{timeline}}. Have a great day!",
}

BUSY_TIME_HINTS = (
    # Core
    "busy", "meeting", "call later", "not now", "later", "driving", "occupied",
    # Extended busy signals
    "in a meeting", "on a call", "at work", "at office", "working",
    "cant talk", "can't talk", "cant speak", "can't speak",
    "not a good time", "bad time", "wrong time", "bad moment",
    "call back", "call me back", "call me later", "ring me later",
    "travelling", "traveling", "in traffic", "on the way",
    "eating", "having lunch", "having dinner", "having breakfast",
    "sleeping", "resting", "tired", "not free", "not available",
    "hospital", "doctor", "emergency", "out of station",
    "thoda time de", "baad mein", "abhi nahi", "baad mein call karo",
    "give me some time", "give me a minute", "two minutes", "five minutes",
    "little busy", "bit busy", "slightly busy", "kinda busy",
    "weekend", "evening", "tonight", "tomorrow",
    "in a rush", "rushing", "hurrying", "very busy", "super busy",
)

NOT_INTERESTED_HINTS = (
    "not interested",
    "not looking",
    "no requirement",
    "dont need",
    "don't need",
    "not now",
)

INTERESTED_HINTS = (
    # Core
    "yes", "yeah", "sure", "go ahead", "tell me", "interested", "ok", "okay",
    # Extended available/interested signals
    "yep", "yup", "of course", "absolutely", "definitely", "certainly",
    "please", "please tell me", "i'm free", "i am free", "free now",
    "available", "available now", "speak now", "go on", "continue",
    "what is it", "what did you want", "what's it about", "what is it about",
    "haan", "haan bolo", "bolo", "batao", "theek hai", "bilkul",
    "i have time", "have two minutes", "have a minute", "have some time",
    "few minutes", "two minutes", "couple minutes", "quick call is fine",
    "good time", "perfect time", "right time",
    "hi yes", "yes hi", "speaking", "yes speaking", "yes this is",
    "it's me", "its me", "that's me", "thats me", "yes it is", "yes i am",
    "fine go ahead", "alright go ahead", "sure go ahead",
    "not busy", "not in a meeting", "i'm available", "i'm listening",
)

CALLBACK_PART_OF_DAY_WINDOWS = {
    "morning": (10 * 60 + 15, 11 * 60 + 45),      # 10:15 AM - 11:45 AM
    "afternoon": (14 * 60 + 15, 16 * 60 + 45),    # 2:15 PM - 4:45 PM
    "evening": (18 * 60 + 15, 20 * 60 + 45),      # 6:15 PM - 8:45 PM
    "night": (20 * 60 + 15, 21 * 60 + 45),        # 8:15 PM - 9:45 PM
}

PURPOSE_CLARIFICATION_PHRASES = (
    "who is this",
    "who are you",
    "what is this about",
    "what is it about",
    "whats this about",
    "what's this about",
    "why are you calling",
    "why did you call",
    "purpose of call",
    "kya hai",
    "kis baare",
)


def _log(tag: str, message: str) -> None:
    logger.info("[%s] %s", tag, message)


# ── Behavioral refinement helpers ────────────────────────────────────────────

def _normalise_stt(text: str) -> str:
    """
    Clean common STT artefacts before intent extraction.
    Operates on words only — no regex for performance.
    Steps (in order):
      1. Collapse immediate word repetitions: "I I want" → "I want"
      2. Strip leading filler words: "uh", "um", "ah", "like", "so", "you know"
      3. Collapse multiple spaces
    Never removes content words. Never translates or corrects spelling.
    """
    words = text.strip().split()

    # Step 1 — deduplicate adjacent identical words (case-insensitive)
    deduped = []
    for word in words:
        if not deduped or word.lower() != deduped[-1].lower():
            deduped.append(word)

    # Step 2 — strip leading fillers (one pass only — preserve content)
    while deduped and deduped[0].lower().strip(".,") in FILLER_STARTERS:
        deduped.pop(0)

    return " ".join(deduped).strip()


def _is_vague_answer(text: str, field: str) -> bool:
    """
    Return True if user gave a vague non-answer for a specific field.
    Used to offer guided defaults instead of repeating the same question.
    """
    t = text.lower()
    return any(v in t for v in VAGUE_TOKENS.get(field, []))


def _get_guidance_response(field: str) -> str:
    """Return a static guidance response for a vague slot answer."""
    return GUIDANCE_RESPONSES.get(field, "Could you give me a rough idea to help narrow it down?")


def _get_bridge(intent: str) -> str:
    """Return a short bridge phrase for unclear/fallback intents only. Empty string otherwise."""
    return FALLBACK_BRIDGE_PHRASES.get(intent, "")


def _is_hostile(text: str) -> bool:
    """
    Detect clearly hostile or dismissive input.
    Lightweight keyword check — no ML, no API call.
    """
    t = text.lower()
    return any(token in t for token in HOSTILE_TOKENS)


def _load_default_flow() -> dict[str, Any]:
    try:
        with DEFAULT_SCHEMA_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        logger.error("Failed to load state schema from %s: %s", DEFAULT_SCHEMA_PATH, exc)
        return {}
    return data.get("conversationFlow", {})


_FLOW = _load_default_flow()
_NODE_MAP: dict[str, dict[str, Any]] = {node["id"]: node for node in _FLOW.get("nodes", []) if "id" in node}


def _build_intent_index(nodes: list[dict[str, Any]]) -> dict[str, str]:
    """
    Map intent_trigger -> node_id for all nodes.
    If two nodes share a trigger, last one wins.
    """
    intent_index: dict[str, str] = {}
    for node in nodes:
        for trigger in node.get("intent_triggers") or []:
            previous = intent_index.get(trigger)
            if previous and previous != node["id"]:
                _log("WARN", f"intent trigger '{trigger}' remapped from {previous} to {node['id']}")
            intent_index[trigger] = node["id"]
    return intent_index


_INTENT_INDEX: dict[str, str] = _build_intent_index(_FLOW.get("nodes", []))


# ── Phrase Bank ───────────────────────────────────────────────────────────────

def _build_phrase_bank(nodes: list[dict[str, Any]]) -> list[str]:
    """
    Extract all approved phrases from the JSON conversation file and
    hardcoded constants.  Returns a deduplicated, ordered list of strings
    that the LLM is allowed to draw from when composing responses.
    """
    phrases: list[str] = []

    # 1. Node responses and missing-slot overrides
    for node in nodes:
        resp = node.get("response")
        if isinstance(resp, str) and resp.strip():
            phrases.append(resp.strip())
        msr = node.get("missing_slot_responses")
        if isinstance(msr, dict):
            for v in msr.values():
                if isinstance(v, str) and v.strip():
                    phrases.append(v.strip())
        instruction = node.get("instruction", {})
        if isinstance(instruction, dict):
            itext = instruction.get("text", "")
            if isinstance(itext, str) and itext.strip():
                phrases.append(itext.strip())

    # 2. Hardcoded behavioural phrases
    for v in FALLBACK_BRIDGE_PHRASES.values():
        phrases.append(v)
    for v in CLARIFICATION_TEMPLATES.values():
        phrases.append(v)
    for v in GUIDANCE_RESPONSES.values():
        phrases.append(v)
    for v in DEESCALATION_RESPONSES:
        phrases.append(v)
    for v in FALLBACK_ESCALATION.values():
        phrases.append(v)

    # 3. Standard acknowledgements
    phrases.extend(["Got it.", "Understood.", "Okay.", "Sure.", "No problem.", "Makes sense."])

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for p in phrases:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


_PHRASE_BANK: list[str] = _build_phrase_bank(_FLOW.get("nodes", []))


def get_phrase_bank() -> list[str]:
    """Return the list of approved phrases from the JSON conversation file."""
    return list(_PHRASE_BANK)


def _match_phrases_used(response: str, bank: list[str]) -> list[str]:
    """
    Return which phrase bank entries appear (fully or partially) in the response.
    Used for [JSON PHRASES USED] logging.
    """
    resp_lower = response.lower()
    matched: list[str] = []
    for phrase in bank:
        # Check if a meaningful fragment (4+ words) of the phrase is in the response
        words = phrase.split()
        if len(words) <= 3:
            if phrase.lower().rstrip(".!?,") in resp_lower:
                matched.append(phrase)
        else:
            # Check sliding windows of 4 words from the phrase
            for i in range(len(words) - 3):
                fragment = " ".join(words[i:i + 4]).lower()
                if fragment in resp_lower:
                    matched.append(phrase)
                    break
    return matched


def find_node_by_intent(intent: str) -> dict[str, Any] | None:
    """Return the node mapped to the given intent, if any."""
    node_id = _INTENT_INDEX.get(intent)
    return _NODE_MAP.get(node_id) if node_id else None


def _is_actionable(text: str) -> bool:
    """
    Return False for input too weak to extract intent from.
    Allow short confirmations through. Block empty, punctuation-only, or noise.
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    if t in {"yes", "yeah", "yep", "ok", "okay", "sure", "no", "nope", "nah"}:
        return True
    if len(t) < 2:
        return False
    if t in SHORT_NOISE:
        return False
    if re.fullmatch(r"[\W_]+", t):
        return False
    if not any(c.isalpha() for c in t):
        return False
    return True


def _has_availability_confirmation(text: str) -> bool:
    cleaned = re.sub(r"[^\w\s]", " ", (text or "").lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return False
        
    # M6 FIX: Add negation awareness so "No, I am not free" or "No time" doesn't falsely match
    negations = {"no", "nahi", "not", "busy", "later"}
    words = cleaned.split()
    if any(neg in words[:3] for neg in negations):
        return False
        
    words_set = set(words)
    exact_confirmations = {
        "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "fine",
        "haan", "han", "ji", "theek", "bilkul",
    }
    if words_set & exact_confirmations:
        return True
    confirmation_phrases = (
        "go ahead", "tell me", "go on", "continue", "i have time",
        "have time", "i am free", "i'm free", "free now", "i am listening",
        "i'm listening", "have two minutes", "two minutes",
    )
    return any(phrase in cleaned for phrase in confirmation_phrases)


def should_answer_user_question(user_input: str) -> bool:
    """Return True when the user needs a brief answer before flow continues."""
    return _detect_user_question(user_input) is not None

def _detect_user_question(user_text: str) -> str | None:
    """Return question type if user is asking a meta-question, else None."""
    text = re.sub(r"[^\w\s'?]", " ", (user_text or "").lower())
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None

    identity_markers = (
        "who are you", "who is this", "who's this", "your name",
        "kaun ho", "kaun bol", "aap kaun", "kon bol",
    )
    purpose_markers = (
        "why are you calling", "why did you call", "what is this about",
        "what is it about", "what's it about", "whats this about",
        "what are you talking about", "what is this", "what is it",
        "purpose of call", "reason for call", "kis baare", "kyu call",
        "kyun call", "kya baat", "kya hai",
    )
    confusion_markers = (
        "i don't understand", "i dont understand", "not clear",
        "what do you mean", "what are you saying", "confused",
        "samajh nahi", "samjha nahi", "clear nahi",
    )
    off_topic_markers = (
        "weather", "prime minister", "pm", "stock market",
        "cricket", "score", "joke", "song", "sing a song",
        "mausam", "barish", "modi", "khana khaya", "lunch kiya",
        "how are you", "kaise ho", "kasa ahes",
    )

    if any(marker in text for marker in identity_markers):
        return "identity"
    if any(marker in text for marker in purpose_markers):
        return "purpose"
    if any(marker in text for marker in confusion_markers):
        return "confusion"
    if any(marker in text for marker in off_topic_markers):
        return "off_topic"
    return None


def _resolve_template(
    node: dict[str, Any],
    data: dict[str, Any],
    language: str = "en",
) -> str:
    """Fill {{placeholders}} in a node's template response. No LLM. No generation logic."""
    template = OPENING_NODE_RESPONSES.get(str(node.get("id") or ""), node.get("response"))
    missing_slot_responses = node.get("missing_slot_responses")
    if isinstance(missing_slot_responses, dict):
        collects = node.get("collects")
        missing_slots: list[str] = []
        if isinstance(collects, list):
            missing_slots = [slot for slot in collects if not data.get(slot)]
        if len(missing_slots) == 1:
            override = missing_slot_responses.get(missing_slots[0])
            if isinstance(override, str) and override.strip():
                template = override
    if template is None:
        template = node.get("instruction", {}).get("text", "")

    def fill(match: re.Match[str]) -> str:
        key = match.group(1)
        if key == "name":
            val = data.get("name") or data.get("lead_name") or data.get("lead") or "there"
        else:
            val = data.get(key)
        return str(val) if val else ""

    localized_template = localize_template(template, language)
    resolved = re.sub(r"\{\{(\w+)\}\}", fill, localized_template).strip()
    resolved = re.sub(r" +", " ", resolved)
    return resolved


_FILLER_OPENERS = (
    "sure,",
    "sure -",
    "sure —",
    "great,",
    "great -",
    "great —",
    "absolutely,",
    "absolutely -",
    "absolutely —",
)


def _enforce_single_question(text: str) -> str:
    question_count = text.count("?")
    if question_count <= 1:
        return text
    first_seen = False
    chars: list[str] = []
    for ch in text:
        if ch == "?":
            if first_seen:
                chars.append(".")
            else:
                chars.append("?")
                first_seen = True
        else:
            chars.append(ch)
    return "".join(chars)


def _remove_filler_openers(text: str) -> str:
    cleaned = text.strip()
    lowered = cleaned.lower()
    for filler in _FILLER_OPENERS:
        if lowered.startswith(filler):
            cleaned = cleaned[len(filler):].lstrip(" ,.-—")
            break
    return cleaned


def _finalize_response_text(text: str) -> str:
    """Apply production response constraints before TTS."""
    if not text or not text.strip():
        return text
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = _remove_filler_openers(cleaned)
    cleaned = _enforce_single_question(cleaned)
    cleaned = _truncate_response(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# ---------------------------------------------------------------------------
# Informational-query gate — decides if LLM fallback is permitted
# ---------------------------------------------------------------------------

_QUESTION_STARTERS = (
    "what", "which", "where", "how", "why",
    "is", "are", "does", "do", "can", "should",
)


def _is_informational_query(text: str, intent: str) -> bool:
    """
    Return True only if the user is asking an informational question
    that is not a structured slot-filling response.

    Conditions (ALL must be true):
    1. intent is "ask_off_topic" or "unclear"
    2. text contains a question indicator:
       - ends with "?"  OR
       - starts with a question word
    3. text is at least 4 words long (avoids noise like "what?")
    """
    if intent not in ("ask_off_topic", "unclear"):
        return False
    t = text.strip().lower()
    words = t.split()
    if len(words) < 4:
        return False
    has_question_mark = t.endswith("?")
    has_question_word = any(t.startswith(w) for w in _QUESTION_STARTERS)
    return has_question_mark or has_question_word


class StateManager:
    """Conversation state tracker backed by Updated_Real_Estate_Agent.json."""

    def __init__(self, json_path: str):
        self.json_path = json_path
        self.schema: Dict[str, Any] = {}
        self.nodes: Dict[str, dict[str, Any]] = {}
        self.tools: Dict[str, dict[str, Any]] = {}
        self.global_prompt = ""
        self.start_node_id = ""
        self.current_node_id = ""
        self.conversation_data: Dict[str, Any] = {}
        self.visited_nodes: set[str] = set()
        self._last_user_text = ""
        # Behavioral refinement state
        self._last_node_id: Optional[str] = None
        self._deescalation_index: int = 0
        self._has_apologised: bool = False
        # Acknowledgement repetition tracking
        self._last_ack: str = ""
        # Session termination flag (Issue 5)
        self._session_ended: bool = False
        # Fallback escalation counters (Issue 6)
        self._fallback_counts: dict[str, int] = {}
        self.active_language: str = "en"
        # Dynamic asked flags will be set in reset_state
        self._asked_flags: dict[str, bool] = {}
        self._recent_responses: list[str] = []
        self.extraction_fields: set[str] = set()
        self.load_schema()

    def load_schema(self) -> None:
        try:
            if os.path.isdir(self.json_path):
                from flows.loader import load_agent_directory
                self.schema = load_agent_directory(self.json_path)
            else:
                with open(self.json_path, "r", encoding="utf-8") as handle:
                    self.schema = json.load(handle)
        except Exception as exc:
            logger.error("Failed to load StateManager schema from %s: %s", self.json_path, exc)
            return

        flow = self.schema.get("conversationFlow", {})
        self._repair_generated_greeting(flow)
        self.global_prompt = flow.get("global_prompt", "")
        self.start_node_id = flow.get("start_node_id", "")
        self.nodes = {node["id"]: node for node in flow.get("nodes", []) if "id" in node}
        self.tools = {}
        for tool in flow.get("tools", []):
            tool_id = tool.get("tool_id")
            if tool_id:
                self.tools[tool_id] = tool

        # Dynamically determine extraction fields
        self.extraction_fields = set()
        for node in self.nodes.values():
            collects = node.get("collects") or []
            if isinstance(collects, str):
                collects = [collects]
            for f in collects:
                if f:
                    self.extraction_fields.add(f)
        
        for f in self.schema.get("data_fields") or []:
            if f:
                self.extraction_fields.add(f)

        self.reset_state()
        logger.info("Loaded %d nodes. Start node: %s", len(self.nodes), self.start_node_id)

    def _repair_generated_greeting(self, flow: dict[str, Any]) -> None:
        """Repair old generated schemas that hardcoded Neha and omitted {{name}}."""
        nodes = flow.get("nodes") or []
        agent_name = str(self.schema.get("agent_name") or "Agent").strip() or "Agent"
        bad_responses = {
            "Hello, this is Neha from the Real Estate AI team. Am I speaking with you?",
            "Hello, this is Neha from the Real Estate AI team. Am I speaking with you",
        }
        for node in nodes:
            if node.get("id") != "root_greeting":
                continue
            if node.get("response") in bad_responses:
                node["response"] = f"Hello, this is {agent_name}. Am I speaking with {{{{name}}}}?"
            instruction = node.get("instruction")
            if isinstance(instruction, dict) and instruction.get("text") == "Greet the user warmly as Neha and confirm identity.":
                instruction["text"] = f"Greet the user warmly as {agent_name} and confirm identity using the lead name."

    @classmethod
    def template_new_agent(
        cls,
        name: str,
        script: str,
        voice_id: str,
        data_fields: list[str],
        agent_type: str = "real_estate_sales",
    ) -> dict[str, Any]:
        """Creates a new agent JSON schema based on a generic template and admin inputs."""
        agent_name = (name or "Real Estate Specialist").strip()
        type_label = {
            "real_estate_sales": "Real Estate team",
            "finance": "Finance advisory team",
            "insurance": "Insurance advisory team",
            "education": "Education counselling team",
        }.get(agent_type, "Customer advisory team")
        # Use a simplified version of the standard flow with Neha as default persona if name is matching
        template = {
            "agent_name": agent_name,
            "voice_id": voice_id or "en-IN-NeerjaNeural",
            "conversation_flow_id": f"flow_{uuid.uuid4().hex[:8]}",
            "global_prompt": script,
            "conversationFlow": {
                "global_prompt": script,
                "start_node_id": "root_greeting",
                "nodes": [
                    {
                        "id": "root_greeting",
                        "name": "Initial Greeting",
                        "type": "conversation",
                        "instruction": {"type": "prompt", "text": f"Greet the user warmly as {agent_name} and confirm identity using the lead name."},
                        "response": f"Hello, this is {agent_name} from the {type_label}. Am I speaking with {{{{name}}}}?",
                        "intent_triggers": ["call_connected"],
                        "edges": [
                            {"id": "to_discovery", "condition": "user responds", "destination_node_id": "discovery"}
                        ]
                    },
                    {
                        "id": "discovery",
                        "name": "Information Discovery",
                        "type": "conversation",
                        "instruction": {"type": "prompt", "text": "Qualify lead interest and collect data fields."},
                        "response": "I'm calling about some premium property options. Would you have a moment?",
                        "collects": data_fields,
                        "edges": [
                            {"id": "to_end", "condition": "conversation finished", "destination_node_id": "end_node"}
                        ]
                    },
                    {
                        "id": "end_node",
                        "name": "Conclusion",
                        "type": "end",
                        "instruction": {"type": "prompt", "text": "End the call politely."},
                        "response": "Thank you for your time. Have a great day!"
                    }
                ]
            }
        }
        return template

    def reset_state(self) -> None:
        self.current_node_id = self.start_node_id
        
        # Dynamic slot memory initialization
        self.conversation_data = {}
        if hasattr(self, "extraction_fields"):
            for f in self.extraction_fields:
                self.conversation_data[f] = None
        
        # Ensure standard keys are present for backward compatibility
        for k in ["location", "budget", "property_type", "timeline", "callback_date", "callback_time", "confirmation"]:
            if k not in self.conversation_data:
                self.conversation_data[k] = None

        self.visited_nodes = {self.start_node_id} if self.start_node_id else set()
        self._last_user_text = ""
        # Behavioral refinement state reset
        self._last_node_id = None
        self._deescalation_index = 0
        self._has_apologised = False
        self._last_ack = ""
        self._session_ended = False
        self._fallback_counts = {}
        self._whatsapp_sent = False
        self.active_language = "en"
        
        # Dynamic asked flags
        self._asked_flags = {}
        if hasattr(self, "extraction_fields"):
            for f in self.extraction_fields:
                self._asked_flags[f] = False
        for k in ["availability", "intent", "location", "budget", "visit_time", "callback_time", "property_type"]:
            if k not in self._asked_flags:
                self._asked_flags[k] = False

        self._recent_responses = []

    def _record_asked(self, node_id: str) -> None:
        """Mark the question type or slot as asked."""
        node = self.nodes.get(node_id)
        if not node:
            return
        
        # Mark collects fields as asked
        collects = node.get("collects") or []
        if isinstance(collects, str):
            collects = [collects]
        for slot in collects:
            if slot in self._asked_flags:
                self._asked_flags[slot] = True
                _log("ASKED FLAG", f"{slot} = True")
        
        # Check expected_input_type for fallbacks
        expected = node.get("expected_input_type")
        if expected and expected in self._asked_flags:
            self._asked_flags[expected] = True
            _log("ASKED FLAG", f"{expected} = True")

        # Keep backward compatibility mapping
        _FLAG_MAP = {
            "node-1735264873079": "availability",
            "node-1735970090937": "availability",
            "node-1735264921453": "intent",
            "fallback_intent": "intent",
            "node-1735267546732": "location",  # also budget but tracked via context
            "fallback_location": "location",
            "fallback_budget": "budget",
            "node-1735265015507": "visit_time",
            "fallback_visit_datetime": "visit_time",
            "node-1736492391269": "callback_time",
            "fallback_callback_time": "callback_time",
            "node-1767420514711": "property_type",
            "fallback_property_type": "property_type",
        }
        flag = _FLAG_MAP.get(node_id)
        if flag and flag in self._asked_flags:
            self._asked_flags[flag] = True
            _log("ASKED FLAG", f"{flag} = True")

    def _build_turn_result(self, node: dict[str, Any], **kwargs: Any) -> TurnResult:
        """Build a TurnResult with asked_flags and recent_responses always included."""
        return TurnResult(
            node=node,
            context=dict(self.conversation_data),
            language=self.active_language,
            asked_flags=dict(self._asked_flags),
            recent_responses=list(self._recent_responses),
            **kwargs,
        )

    def record_response(self, response: str) -> None:
        """Called after response generation to update recent_responses for anti-repetition."""
        if response and response.strip():
            self._recent_responses.append(response.strip())
            # Keep only the last 5 responses
            if len(self._recent_responses) > 5:
                self._recent_responses = self._recent_responses[-5:]

    def set_active_language(self, language: str) -> None:
        self.active_language = language or "en"

    def _trigger_whatsapp_if_needed(self, intent: str, next_node: dict[str, Any]) -> None:
        """Asynchronously triggers WhatsApp property details based on intent or node."""
        node_id = next_node.get("id", "")
        
        # Trigger conditions
        trigger_nodes = {"confirm_interest", "schedule_site_visit", "share_details", "send_property_details"}
        trigger_intents = {"interested", "site_visit_requested"}
        
        is_trigger_node = node_id in trigger_nodes
        is_trigger_name = next_node.get("name", "").replace(" ", "_").lower() in trigger_nodes
        
        if not (is_trigger_node or is_trigger_name or intent in trigger_intents):
            return
            
        phone = self.conversation_data.get("phone")
        if not phone:
            _log("WHATSAPP", "Cannot send message: no phone number found in conversation data.")
            return
            
        # Prevent duplicate sends in the same session
        if getattr(self, "_whatsapp_sent", False):
            return
            
        try:
            import sys
            import threading
            import asyncio
            from pathlib import Path
            
            # Ensure integrations can be imported
            backend_dir = str(Path(__file__).resolve().parent.parent)
            if backend_dir not in sys.path:
                sys.path.append(backend_dir)
                
            from integrations.whatsapp import send_whatsapp_message, format_property_message, MOCK_PROPERTIES
            
            message = format_property_message(self.conversation_data, MOCK_PROPERTIES)
            
            _log("WHATSAPP", f"Triggering background task to send details to {phone}")
            
            # M1 FIX: Avoid asyncio.create_task() which fails if called from a sync thread
            # without an active running event loop. Use a fire-and-forget thread instead.
            def _send_in_bg():
                try:
                    asyncio.run(send_whatsapp_message(phone, message))
                except Exception as ex:
                    _log("WHATSAPP", f"Background send failed: {ex}")

            threading.Thread(target=_send_in_bg, daemon=True).start()
            self._whatsapp_sent = True
        except Exception as e:
            _log("WHATSAPP", f"Error triggering WhatsApp integration: {e}")

    def get_current_node(self) -> Optional[dict[str, Any]]:
        return self.nodes.get(self.current_node_id)

    def is_terminal_node(self, node_id: Optional[str] = None) -> bool:
        node = self.nodes.get(node_id or self.current_node_id)
        return bool(node and node.get("type") == "end")

    def transition_to(self, edge_id: str) -> bool:
        current_node = self.get_current_node()
        if not current_node:
            return False

        for edge in current_node.get("edges", []):
            if edge.get("id") != edge_id:
                continue
            destination_id = edge.get("destination_node_id")
            destination = self.nodes.get(destination_id)
            if not destination:
                return False
            next_node = self._apply_forward_guard(destination)
            self.current_node_id = next_node["id"]
            if next_node.get("type") != "fallback":
                self.visited_nodes.add(next_node["id"])
            _log("STATE", f"-> {next_node['id']}")
            return True

        logger.warning("Invalid edge_id %s requested from node %s", edge_id, self.current_node_id)
        return False

    def get_system_prompt(self, language: Optional[str] = None, allow_transition: bool = True) -> str:
        del language, allow_transition
        return self.global_prompt

    def is_actionable(self, text: str) -> bool:
        return _is_actionable(text)

    # C1 FIX: _reactivate_if_needed REMOVED.
    # Once _session_ended=True, the session is permanently closed.
    # A new WebSocket connection must be opened for a new call.

    def execute_noise_transition(self, user_text: str) -> TurnResult:
        """Handle noise/non-actionable input. Returns TurnResult (no response)."""
        if self._session_ended:
            _log("SESSION ENDED", "Ignoring further input")
            return self._build_turn_result({}, is_terminal=True, user_input=user_text)
        self._last_user_text = user_text or ""

        """ # Update active language from the CURRENT user turn
        if user_text:
            detected_language = detect_language(user_text)
            self.set_active_language(detected_language) """

        current_node = self.get_current_node()
        if not current_node:
            return self._build_turn_result({}, is_terminal=True, user_input=user_text)

        # Recover callback scheduling even when intent extraction is unavailable.
        node_id = current_node.get("id")
        if node_id in {"node-1736492391269", "node-callback", "fallback_callback_time"}:
            clean_text = re.sub(r"[^\w:\s]", " ", (user_text or "").lower())
            callback_time_keywords = (
                "morning", "afternoon", "evening", "night",
                "am", "pm", "after", "post", "around",
                "later", "tomorrow", "today",
            )
            has_callback_time_hint = any(re.search(rf"\b{kw}\b", clean_text) for kw in callback_time_keywords)
            has_callback_number = any(char.isdigit() for char in clean_text)
            if has_callback_time_hint or has_callback_number:
                timeline = self._synthesize_callback_timeline((user_text or "").strip())
                _log("NOISE RECOVERY", f'Callback time detected -> "{timeline}"')
                return self.execute_transition(
                    user_text,
                    {"intent": "provide_timeline", "entities": {"timeline": timeline}},
                )

        _log("NOISE FILTERED", f"\"{user_text}\"")
        return self._build_turn_result(
            current_node, user_input=user_text, response_type="noise_repeat",
        )

    def execute_greeting_transition(self, user_text: str = "") -> TurnResult:
        """Return TurnResult for the greeting/current node without transitioning."""
        if self._session_ended:
            _log("SESSION ENDED", "Ignoring greeting transition — session closed")
            return self._build_turn_result({}, is_terminal=True, user_input=user_text)
        node = self.get_current_node()
        if not node:
            return self._build_turn_result({}, is_terminal=True, user_input=user_text)
        self._record_asked(node.get("id", ""))
        return self._build_turn_result(
            node, user_input=user_text, response_type="greeting",
        )

    def execute_transition(self, user_text: str, intent_data: Optional[dict[str, Any]]) -> TurnResult:
        """Execute a state transition and return TurnResult.

        This is the CORE method. It handles:
        - STT normalization
        - User question detection
        - Intent normalization
        - Entity extraction and merging
        - Node transition
        - Auto-advance through skip edges

        It does NOT generate any response text. That's LLMResponseGenerator's job.
        """
        if self._session_ended:
            _log("SESSION ENDED", "Ignoring further input")
            return self._build_turn_result(
                self.get_current_node() or {}, is_terminal=True, user_input=user_text,
            )

        self._last_user_text = user_text or ""
        _log("STT", f"\"{user_text}\"")

        original_text = user_text or ""
        user_text = _normalise_stt(original_text)
        if user_text != original_text:
            _log("STT CLEAN", f'"{original_text}" -> "{user_text}"')

    
        current_node = self.get_current_node()
        if not current_node:
            return self._build_turn_result({}, is_terminal=True, user_input=user_text)

        # ── User is asking a meta-question → stay on current node ────────
        user_question = _detect_user_question(user_text)
        if user_question:
            if (
                current_node.get("id") in {"node-1735264873079", "node-availability"}
                and user_question in {"purpose", "identity", "confusion"}
                and _has_availability_confirmation(user_text)
            ):
                next_node = self.nodes.get("node-1735264921453") or self.nodes.get("node-explain")
                if next_node:
                    _log("STATE", f"{current_node['id']} -> {next_node['id']} (availability confirmed with question)")
                    self.current_node_id = next_node["id"]
                    self.visited_nodes.add(next_node["id"])
                    self._last_node_id = self.current_node_id
                    self._record_asked(next_node["id"])
                    return self._build_turn_result(
                        next_node,
                        user_input=user_text,
                        user_question=user_question,
                        node_changed=True,
                    )
            _log("STATE", f"{current_node['id']} unchanged (user question: {user_question})")
            self._last_node_id = self.current_node_id
            return self._build_turn_result(
                current_node, user_input=user_text, user_question=user_question,
            )

        # ── End node → terminal ──────────────────────────────────────────
        if current_node.get("type") == "end":
            _log("END NODE REACHED", "Conversation terminated gracefully.")
            self._session_ended = True
            self._last_node_id = self.current_node_id
            return self._build_turn_result(
                current_node, user_input=self._last_user_text, is_terminal=True,
            )

        # ── Hostile input → deescalation ─────────────────────────────────
        if HOSTILE_DETECTION_ENABLED and _is_hostile(user_text):
            _log("TONE", "Hostile input detected")
            self._deescalation_index = (self._deescalation_index + 1) % len(DEESCALATION_RESPONSES)
            self._last_node_id = self.current_node_id
            return self._build_turn_result(
                current_node, user_input=user_text, response_type="deescalation",
            )

        # ── No intent data → noise ──────────────────────────────────────
        if intent_data is None:
            return self.execute_noise_transition(user_text)

        # ── Intent processing ────────────────────────────────────────────
        intent = str(intent_data.get("intent") or "unclear").strip() or "unclear"
        entities = intent_data.get("entities") or {}
        if not isinstance(entities, dict):
            entities = {}

        # Questions -> answer + stay in node
        if intent == "user_question":
            _log("STATE", f"{current_node['id']} unchanged (user question from LLM)")
            self._last_node_id = self.current_node_id
            return self._build_turn_result(
                current_node, user_input=user_text, user_question="purpose"
            )

        # Intent mapping for custom intents
        if intent == "suggest_time":
            intent = "provide_timeline"

        # State correction: If user corrects previous misunderstanding by confirming availability
        if intent == "confirm_availability" and current_node.get("id") in {"node-1736492391269", "fallback_callback_time"}:
            _log("STATE CORRECTION", "User confirmed availability; returning to Availability Check.")
            corrected_node = self.nodes.get("node-1735264873079")
            if corrected_node:
                current_node = corrected_node
                
        # Confirming a time suggestion: if user says "Yeah" after agent suggests a time
        if intent == "confirm" and current_node.get("id") in {"node-1736492391269", "fallback_callback_time"}:
            _log("STATE CORRECTION", "User confirmed time suggestion; mapping to provide_timeline.")
            intent = "provide_timeline"

        raw_intent = intent
        intent = self._normalize_intent_for_context(current_node, intent, entities, user_text)
        if intent != raw_intent:
            _log("INTENT NORMALIZED", f"{raw_intent} -> {intent}")
            if intent == "ask_location_suggestion":
                raw_intent = intent

        if intent in {"confirm", "deny"}:
            entities = {"confirmation": entities.get("confirmation")}

        # User-requested Language telemetry log
        if hasattr(self, "active_language") and self.active_language:
            _log("LANGUAGE", f"{self.active_language.capitalize()}")
        else:
            _log("LANGUAGE", "English (Default)")

        # Add debug log for INTENT DETECTED
        extracted_intent_value = entities.get("intent_value")
        if extracted_intent_value:
            _log("INTENT DETECTED", f"{extracted_intent_value}")
        else:
            _log("INTENT DETECTED", f"{intent}")

        _log("INTENT", self._format_intent_log(intent, entities))
        # C2 FIX: Merge entities BEFORE applying forward guards or checking slots
        # This prevents skipping checks from running against stale slot data.
        self._merge_entities(entities, intent=intent)

        # C3 FIX: Re-run forward guard on current node now that entities are merged
        # to auto-skip if a required slot was just provided.
        if self._should_skip_node(current_node):
            _log("SKIP", f"Node {current_node.get('id')} skipping because slots are now fulfilled")
            skip_dest = self._resolve_skip_destination(current_node)
            if skip_dest:
                next_node = self.nodes.get(skip_dest) or current_node
                current_node = next_node
                node_id = current_node.get("id")
                # Add logging so we can track this fast-forward jump
                _log("FAST-FORWARD", f"Jumped to {node_id} directly")

        # 5. SLOT COMPLETION DERIVATION & ANTI-REPETITION (Fix #5)
        collects = self._collect_slots(current_node)
        if collects:
            # Emit individual state completion logs
            for slot in collects:
                if self.conversation_data.get(slot):
                    _log("STATE", f"{slot} slot completed")
            
            # Emit aggregated SLOTS log
            slots_log = ", ".join(f"{k}={self.conversation_data.get(k)}" for k in collects if self.conversation_data.get(k))
            if slots_log:
                _log("SLOTS", slots_log)

            if all(self.conversation_data.get(slot) for slot in collects):
                _log("STATE COMPLETE", f"{current_node.get('name', current_node['id'])}")
                
                # Exceptions for special edges that override the standard forward path
                override_intents = {
                    "seller_interest", "not_looking_now", "budget_high", 
                    "deny", "deny_interest", "deny_time", "deny_identity", "deny_visit_time"
                }
                
                if intent not in override_intents:
                    forward_id = self._resolve_skip_destination(current_node)
                    if forward_id:
                        next_node = self.nodes.get(forward_id)
                        if next_node:
                            _log("TRANSITION", f"{current_node.get('name', current_node['id'])} -> {next_node.get('name', next_node['id'])}")
                            self.current_node_id = next_node["id"]
                            if next_node.get("type") != "fallback":
                                self.visited_nodes.add(next_node["id"])
                            self._trigger_whatsapp_if_needed(intent, next_node)
                            self._last_node_id = self.current_node_id
                            self._record_asked(next_node["id"])
                            _log("STATE", f"Transition complete -> {next_node['id']}")
                            return self._build_turn_result(
                                next_node, user_input=self._last_user_text, is_terminal=False,
                                node_changed=True,
                            )
            elif any(self.conversation_data.get(slot) for slot in collects) and intent == "unclear":
                 # If user provided SOMETHING valid even if intent extraction was shaky
                 intent = "partial_info"

        # Callback time recovery from fallback
        if current_node.get("id") == "fallback_callback_time" and intent == "provide_timeline":
            resume_node_id = self._resolve_skip_destination(current_node)
            resume_node = self.nodes.get(resume_node_id) if resume_node_id else None
            if resume_node:
                _log("STATE", f"{current_node['id']} -> {resume_node['id']} (resume callback)")
                current_node = resume_node

        # Vague detection
        if VAGUE_DETECTION_ENABLED:
            collect_slots = self._collect_slots(current_node)
            for slot in collect_slots:
                if self.conversation_data.get(slot):
                    continue
                if _is_vague_answer(user_text, slot):
                    _log("VAGUE", f"Vague answer for '{slot}'")
                    self._last_node_id = self.current_node_id
                    return self._build_turn_result(
                        current_node, user_input=user_text,
                    )

        # ---------------------------------------------------------------------
        # FIX M — greeting node has no rescue path for "unclear" intent.
        #
        # The Availability Check node (node-1735264873079) and the generic
        # "forward to a fallback-type node" rescue above only fire when the
        # forward destination's type is "fallback". Smart Greeting's forward
        # destination is a normal "conversation" node, so when STT garbles the
        # caller's "haan, boliye" into "hain, bu liet." and the LLM returns an
        # unclear/greeting intent, nothing ever rescues it — the greeting
        # replayed 2-3 times in testing.
        #
        # This is a low-stakes consent gate: almost any non-empty reply that
        # isn't a clear rejection means "yes, continue". Don't wait on a
        # possibly-noisy LLM classification for it — decide deterministically.
        # ---------------------------------------------------------------------
        # FIX M2 — generalized low-stakes consent-gate rescue.
        #
        # FIX M (previous round) only covered the greeting node. Testing
        # showed the EXACT same stall pattern on other yes/no gates —
        # Availability Check ("do you have 2 minutes") stalled 3x on "Huh?",
        # and Share Property Details ("would you like to explore a visit")
        # stalled on "Jee hain, mein visit plan karna chahun gi." — because
        # STT garbling makes the LLM return an unclear intent, and only the
        # greeting node had a deterministic rescue.
        #
        # Generalize: ANY node whose edges include a real "confirm"-style
        # edge (per _select_confirmation_edge's own marker list) is a
        # yes/no gate. For those, a non-empty reply with no clear rejection
        # word means "yes, continue" — decide it deterministically instead
        # of stalling on a noisy LLM classification.
        # ---------------------------------------------------------------------
        if (
            intent not in ALL_DENY_INTENTS
            and intent != "confirm"
            and self._select_confirmation_edge(current_node, "confirm") is not None
        ):
            _NEGATIVE_GREETING_WORDS = {
                "no", "not", "busy", "wrong", "nahi", "nahin", "nhi",
                "kaun", "who", "later", "baad", "reject", "stop",
            }
            _text_words = set(re.findall(r"[a-zA-Z\u0900-\u097F]+", (user_text or "").lower()))
            if _text_words and not (_text_words & _NEGATIVE_GREETING_WORDS):
                _log(
                    "STATE",
                    f"Consent gate {current_node.get('id')}: non-negative reply "
                    f"'{user_text}' (intent was '{intent}') -> forcing confirm",
                )
                intent = "confirm"

        # ── Resolve next node ────────────────────────────────────────────
        if intent in {"confirm"} or intent in ALL_DENY_INTENTS:
            _log("STATE", "Confirmation handled via edge")
            next_node, bypass_guard = self._handle_confirmation(current_node, intent)
        else:
            next_node = self._resolve_by_intent(current_node, intent, raw_intent=raw_intent)
            bypass_guard = False

        if not bypass_guard:
            next_node = self._apply_forward_guard(next_node or current_node)

        # Track whether a state transition occurred (node changed)
        node_changed = next_node.get("id") != current_node.get("id")

        # ── Prevent Loops ────────────────────────────────────────────────
        if not node_changed:
            missing = self._missing_slots(current_node)
            is_collecting = len(missing) > 0 and current_node.get("type") != "fallback"
            
            if is_collecting:
                _log("LOOP PREVENTION", f"Holding at {current_node['id']} because entities are missing: {missing}")
                self._same_node_count = 0
            elif current_node.get("id") == "fallback_location" and intent == "ask_location_suggestion":
                _log("LOOP PREVENTION", "Holding on location suggestion fallback")
                self._same_node_count = 0
            else:
                self._same_node_count = getattr(self, "_same_node_count", 0) + 1
                # C4 FIX: Allow up to 3 clarification attempts before forcing transition
                # (was >= 1, causing premature jumps after a single unclear response)
                if self._same_node_count >= 3:
                    # C4 GUARD: Never force-forward from nodes that have unfilled required slots
                    unfilled = self._missing_slots(current_node)
                    if unfilled:
                        _log("LOOP PREVENTION", f"NOT forcing — required slots still missing: {unfilled}")
                        self._same_node_count = 0
                    else:
                        _log("LOOP PREVENTION", f"Forcing transition from {current_node['id']}")
                        forward_id = self._resolve_skip_destination(current_node)
                        if forward_id:
                            next_node = self.nodes.get(forward_id) or next_node
                        else:
                            end_node = self.nodes.get("node-1736492520068")
                            if end_node:
                                next_node = end_node
                        node_changed = next_node.get("id") != current_node.get("id")
                        self._same_node_count = 0
        else:
            self._same_node_count = 0

        self.current_node_id = next_node["id"]
        if next_node.get("type") != "fallback":
            self.visited_nodes.add(next_node["id"])

        self._trigger_whatsapp_if_needed(intent, next_node)

        if next_node.get("type") == "fallback":
            node_id = next_node["id"]
            self._fallback_counts[node_id] = self._fallback_counts.get(node_id, 0) + 1
            _log("FALLBACK COUNT", f"{node_id} = {self._fallback_counts[node_id]}")

        is_terminal = False
        if next_node.get("type") == "end":
            _log("END NODE REACHED", "Conversation terminated gracefully.")
            self._session_ended = True
            is_terminal = True

        # Auto-advance through skip edges
        # FIX R: previously this block only updated self.current_node_id to
        # the auto-advanced destination, but kept building the TurnResult
        # from the STALE `next_node` (the node BEFORE auto-advancing). Every
        # downstream consumer — the response generator, the language guard,
        # the anti-repeat nudge lookup — then operated on the wrong node's
        # data, and the log's "Transition complete -> X" line printed the
        # stale id too, making this very hard to spot from logs alone.
        if not is_terminal and next_node["id"] in AUTO_ADVANCE_NODES:
            terminal = self._auto_advance_skip_edges(next_node)
            if terminal and terminal["id"] != next_node["id"]:
                self.current_node_id = terminal["id"]
                self.visited_nodes.add(terminal["id"])
                next_node = terminal  # <-- use the real final node from here on
                if terminal.get("type") == "end":
                    _log("END NODE REACHED", f"Auto-advanced -> {terminal['id']}")
                    self._session_ended = True
                    is_terminal = True

        self._last_node_id = self.current_node_id
        self._record_asked(next_node["id"])
        _log("STATE", f"Transition complete -> {next_node['id']}")

        return self._build_turn_result(
            next_node, user_input=self._last_user_text, is_terminal=is_terminal,
            node_changed=node_changed,
        )

    # ── Backward-compatible wrappers (call execute_transition + generate response) ──

    def process_noise_turn(self, user_text: str) -> str:
        """DEPRECATED: Use execute_noise_transition() + LLMResponseGenerator instead."""
        turn = self.execute_noise_transition(user_text)
        if not turn.node:
            return ""
        from .llm_response_generator import generate_response_for_turn_sync
        response = generate_response_for_turn_sync(turn)
        self.record_response(response)
        self._log_response(turn.node, response)
        return _finalize_response_text(response)

    def next_step(self, user_text: str = "", allow_transition: bool = True) -> str:
        """DEPRECATED: Use execute_greeting_transition() + LLMResponseGenerator instead."""
        if self._session_ended:
            return ""
        if allow_transition:
            return self.process_turn(user_text, None)
        turn = self.execute_greeting_transition(user_text)
        from .llm_response_generator import generate_response_for_turn_sync
        response = generate_response_for_turn_sync(turn)
        self.record_response(response)
        self._log_response(turn.node, response)
        return _finalize_response_text(response)

    def process_turn(self, user_text: str, intent_data: Optional[dict[str, Any]]) -> str:
        """DEPRECATED: Use execute_transition() + LLMResponseGenerator instead."""
        turn = self.execute_transition(user_text, intent_data)
        if not turn.node:
            return ""
        from .llm_response_generator import generate_response_for_turn_sync
        response = generate_response_for_turn_sync(turn)
        self.record_response(response)
        _log("FINAL RESPONSE", f'"{response}"')
        self._last_node_id = self.current_node_id
        return _finalize_response_text(response).strip()

    def _resolve_by_intent(self, current_node: dict[str, Any], intent: str, raw_intent: str = "") -> dict[str, Any]:
        """Resolve next node strictly from current node edges."""
        node_id = current_node.get("id", "")

        # ── Global Interrupts / Shortcuts ───────────────────────────────────────────
        # 1. Callback scheduling shortcut on busy
        if intent in {"deny_time", "busy", "call_later", "not_now"}:
            if node_id not in {"node-1736492391269", "node-callback"}:
                target = self.nodes.get("node-1736492391269") or self.nodes.get("node-callback")  # Callback Scheduling
                if target:
                    _log("STATE", f"{node_id} -> {target['id']} (forced callback scheduling)")
                    return target

        # 2. Not interested shortcut globally
        if intent in {"deny_interest", "not_looking_now"}:
            if node_id != "node-objection-not-looking":
                target = self.nodes.get("node-objection-not-looking")
                if not target:
                    # Fallback to any end node
                    target = next((n for n in self.nodes.values() if n.get("type") == "end"), None)
                if target and node_id != target.get("id"):
                    _log("STATE", f"{node_id} -> {target['id']} (forced objection not looking)")
                    return target

        # 3. Wrong person / wrong number globally
        if intent in {"deny_identity", "wrong_person", "wrong_number"}:
            if node_id not in {"node-1736492520068", "node-wrong-person"}:
                target = self.nodes.get("node-1736492520068") or self.nodes.get("node-wrong-person")  # Immediate End Call / Wrong Person
                if not target:
                    # Fallback to any end node in dynamic flow
                    target = next((n for n in self.nodes.values() if n.get("type") == "end"), None)
                if target and node_id != target.get("id"):
                    _log("STATE", f"{node_id} -> {target['id']} (forced immediate end call - wrong person)")
                    return target

        # 2. Resuming flow from callback
        if node_id in {"node-1736492391269", "node-callback", "fallback_callback_time"}:
            if intent in {"confirm_availability", "provide_intent", "confirm"}:
                target = self.nodes.get("node-1735264921453") or self.nodes.get("node-explain")  # Ask Intent
                if target:
                    _log("STATE", f"{node_id} -> {target['id']} (resumed flow from callback)")
                    return target
            if intent in {"deny", "deny_interest", "deny_time"}:
                target = self.nodes.get("node-1736492520068") or self.nodes.get("node-wrong-person")  # Immediate End Call
                if not target:
                    target = next((n for n in self.nodes.values() if n.get("type") == "end"), None)
                if target:
                    _log("STATE", f"{node_id} -> {target['id']} (user refused callback)")
                    return target

        # ── Intent shortcut from Ask Intent or fallback_intent ──────────────────
        # When user gives a valid intent (buy/invest/rent/sell) on either the Ask
        # Intent node OR its fallback, skip intermediate steps and jump directly
        # to the correct destination.
        if node_id in {"node-1735264921453", "node-explain", "fallback_intent", "fallback-intent"}:
            if intent in {"provide_intent", "provide_property_type", "provide_location", "provide_budget"}:
                target = self.nodes.get("node-1735267546732") or self.nodes.get("node-ask-city")  # Ask Location & Budget / City
                if target:
                    _log("STATE", f"{node_id} -> {target['id']} ({intent} shortcut)")
                    return target
            if intent == "seller_interest":
                target = self.nodes.get("node-1736510533232") or self.nodes.get("node-seller-flow")  # Seller Flow Start
                if target:
                    _log("STATE", f"{node_id} -> {target['id']} (seller shortcut)")
                    return target


        edges = current_node.get("edges", []) or []
        if not edges:
            _log("STATE", f"No outgoing edges from {current_node['id']} - staying on current node")
            return current_node

        intents_to_match = [intent]
        if raw_intent and raw_intent not in intents_to_match:
            intents_to_match.append(raw_intent)

        # 1. Structured Edge Matching (Semantic Intent Mapping)
        for edge in edges:
            destination_id = edge.get("destination_node_id")
            if not destination_id:
                continue
            destination = self.nodes.get(destination_id)
            if not destination:
                continue

            destination_triggers = destination.get("intent_triggers") or []
            condition = (edge.get("condition", "") or "").lower().strip()
            
            # Match by explicit intent triggers OR semantic condition aliases
            if any(i in destination_triggers for i in intents_to_match) or self._edge_condition_matches_intent(condition, intents_to_match):
                if destination.get("type") == "fallback":
                    expected = destination.get("expected_input_type")
                    if expected and self.conversation_data.get(expected):
                        _log("SKIP FALLBACK", f"{destination['id']} ignored because '{expected}' is already collected")
                        continue
                _log(
                    "STATE",
                    f"{current_node['id']} -> {destination['id']} (intent={intent}, condition='{condition}')",
                )
                return destination

        _log(
            "STATE_STALL",
            f"No matching edge from {current_node['id']} for intent '{intent}'",
        )

        # Callback scheduling should still progress once timeline exists.
        if current_node.get("id") in {"node-1736492391269", "fallback_callback_time"} and self.conversation_data.get("timeline"):
            for edge in edges:
                destination_id = edge.get("destination_node_id")
                destination = self.nodes.get(destination_id) if destination_id else None
                if destination and "provide_timeline" in (destination.get("intent_triggers") or []):
                    _log("STATE", f"{current_node['id']} -> {destination['id']} (timeline recovery)")
                    return destination

        # Never stall on unclear intents: prefer a connected fallback edge from current node.
        if intent.startswith("unclear") or raw_intent.startswith("unclear") or intent == "ask_off_topic":
            for edge in edges:
                destination_id = edge.get("destination_node_id")
                destination = self.nodes.get(destination_id) if destination_id else None
                if destination and destination.get("type") == "fallback":
                    _log("STATE", f"{current_node['id']} -> {destination['id']} (unclear fallback edge)")
                    return destination

            # Availability node has no fallback edge; move to re-engage path instead of stalling.
            if current_node.get("id") == "node-1735264873079":
                for edge in edges:
                    condition = (edge.get("condition", "") or "").lower()
                    if "busy" in condition or "reject" in condition:
                        destination_id = edge.get("destination_node_id")
                        destination = self.nodes.get(destination_id) if destination_id else None
                        if destination:
                            _log("STATE", f"{current_node['id']} -> {destination['id']} (unclear -> re-engage)")
                            return destination

        # 2.4 Repetition Root Fix: If we still haven't moved and intent was unclear, 
        # force a transition to the node's forward destination to prevent infinite re-prompts.
        if intent.startswith("unclear") or raw_intent.startswith("unclear"):
            forward_id = self._first_destination(current_node)
            forward_node = self.nodes.get(forward_id)
            if forward_node and forward_node.get("type") == "fallback":
                 _log("STATE_FORCE", f"Stalled on unclear -> forcing forward to fallback {forward_id}")
                 return forward_node

        return current_node

    def _handle_confirmation(self, current_node: dict[str, Any], intent: str) -> tuple[dict[str, Any], bool]:
        """Returns (next_node, bypass_forward_guard)."""
        if intent in ALL_DENY_INTENTS:
            target = self._route_deny_subtype(current_node, intent)
            if target:
                return target, False

        edge = self._select_confirmation_edge(current_node, intent)

        if not edge:
            # Fallback for dynamic flows: if user denies identity/interest and no matching edge, route to end node
            if intent in {"deny", "deny_identity", "deny_interest"}:
                end_node = next((n for n in self.nodes.values() if n.get("type") == "end"), None)
                if end_node:
                    _log("CONFIRMATION FALLBACK", f"Routing to end_node {end_node['id']} on deny")
                    return end_node, False
            return current_node, False
        destination_id = edge.get("destination_node_id")
        destination = self.nodes.get(destination_id)
        if not destination:
            return current_node, False
        _log("STATE", f"{current_node['id']} -> {destination['id']} (confirm edge)")
        return destination, False

    def _route_deny_subtype(
        self, current_node: dict[str, Any], intent: str
    ) -> Optional[dict[str, Any]]:
        """
        Route deny intent based on conversation context (current node).
        Returns target node if a contextual override applies, None otherwise.

        Deny sub-type is determined by:
        1. The LLM-classified sub-type (deny_identity, deny_interest, etc.)
        2. OR the current node context (which question was asked)
        """
        node_id = current_node["id"]

        # Determine effective deny sub-type from LLM intent or node context
        if intent == "deny":
            # Generic deny → resolve from current node context
            if node_id in DENY_IDENTITY_NODES:
                intent = "deny_identity"
            elif node_id in DENY_TIME_NODES:
                intent = "deny_time"
            elif node_id in DENY_INTEREST_NODES:
                intent = "deny_interest"
            elif node_id in DENY_VISIT_NODES:
                intent = "deny_visit_time"
            else:
                return None  # no contextual override — fall through to edge matching

        _log("DENY TYPE", f"{intent} at {node_id} ({current_node.get('name', '')})")

        # deny_identity → wrong person end
        if intent == "deny_identity":
            target = self.nodes.get(WRONG_PERSON_END_NODE_ID)
            if not target:
                target = next((n for n in self.nodes.values() if n.get("type") == "end"), None)
            if target:
                _log("DENY ROUTE", f"deny_identity -> {target['id']} (wrong person)")
                return target

        # deny_time → callback scheduling
        if intent == "deny_time":
            target = self.nodes.get(CALLBACK_SCHEDULING_NODE_ID)
            if not target:
                target = next((n for n in self.nodes.values() if n.get("type") == "end"), None)
            if target:
                _log("DENY ROUTE", f"deny_time -> {target['id']} (busy/not available)")
                return target

        # deny_interest → polite end
        if intent == "deny_interest":
            target = self.nodes.get(POLITE_END_NODE_ID)
            if not target:
                target = next((n for n in self.nodes.values() if n.get("type") == "end"), None)
            if target:
                _log("DENY ROUTE", f"deny_interest -> {target['id']} (not interested)")
                return target

        # deny_visit_time → offer alternate date
        if intent == "deny_visit_time":
            target = self.nodes.get(RESCHEDULE_VISIT_NODE_ID)
            if not target:
                target = self.nodes.get(CALLBACK_SCHEDULING_NODE_ID)
            if not target:
                target = next((n for n in self.nodes.values() if n.get("type") == "end"), None)
            if target:
                _log("DENY ROUTE", f"deny_visit_time -> {target['id']} (offering alternate)")
                return target

        return None  # no contextual override

    def _select_confirmation_edge(self, node: dict[str, Any], intent: str) -> Optional[dict[str, Any]]:
        edges = node.get("edges", [])
        if not edges:
            return None

        # Check semantic edge matching first
        for edge in edges:
            condition = " ".join(
                filter(
                    None,
                    [
                        edge.get("condition", ""),
                        edge.get("transition_condition", {}).get("prompt", ""),
                    ],
                )
            ).lower()
            if self._edge_condition_matches_intent(condition, [intent]):
                return edge

        positive_markers = (
            "correct person", "correct", "user is free", "user is",
            "agrees", "agree", "hear more", "wants to visit",
            "finished confirmation", "done", "speak", "confirms identity",
            "confirms", "interested in visit", "wants visit",
            # Generic additions — kisi bhi LLM-generated flow (LIC, finance,
            # jiotv, future agents) ke greeting/consent nodes ke liye zaroori,
            # jahan condition text bilkul alag likha ho sakta hai
            "ready", "available", "good time", "time to talk", "willing",
            "consents", "proceed", "continue", "yes", "confirm",
        )
        negative_markers = (
            "wrong person", "busy", "reject", "not looking", "not interested",
            "still rejects", "uncertain", "tell later", "later", "refuses",
            "busy or rejects now",
            # Generic additions
            "not available", "not ready", "no time", "can't talk", "cant talk",
            "not now", "not a good time", "unwilling",
        )

        markers = positive_markers if intent == "confirm" else negative_markers
        for edge in edges:
            condition = " ".join(
                filter(
                    None,
                    [
                        edge.get("condition", ""),
                        edge.get("transition_condition", {}).get("prompt", ""),
                    ],
                )
            ).lower()
            # FIX M2 safety: "interested in visit" is a substring of
            # "not interested in visit" — don't let a positive marker match
            # inside a negated condition.
            if intent == "confirm" and "not interested" in condition:
                continue
            if any(marker in condition for marker in markers):
                return edge

        # Dynamic fallback: if there is exactly 1 edge and intent is confirm, follow it
        if len(edges) == 2:
            _NEGATION_WORDS = (
                "not", "no", "wrong", "busy", "reject", "later",
                "refuse", "never", "cant", "can't", "unavailable", "nahi",
            )
            for edge in edges:
                condition = " ".join(
                    filter(None, [
                        edge.get("condition", ""),
                        edge.get("transition_condition", {}).get("prompt", ""),
                    ])
                ).lower()
                has_negation = any(w in condition for w in _NEGATION_WORDS)
                if intent == "confirm" and not has_negation:
                    return edge
                if intent != "confirm" and has_negation:
                    return edge

        return None

    def _advance_from_node(self, node: dict[str, Any]) -> dict[str, Any]:
        current = node
        seen: set[str] = set()
        while self._should_skip_node(current):
            _log("SKIP", f"{current['id']} - {self._skip_reason(current)}")
            next_id = self._first_destination(current)
            if not next_id or next_id in seen:
                return current
            seen.add(next_id)
            next_node = self.nodes.get(next_id)
            if not next_node:
                return current
            current = next_node
        return current

    def _find_path(self, start_id: str, target_id: str) -> list[str]:
        if start_id == target_id:
            return [start_id]

        queue: list[tuple[str, list[str]]] = [(start_id, [start_id])]
        seen = {start_id}
        while queue:
            node_id, path = queue.pop(0)
            node = self.nodes.get(node_id)
            if not node:
                continue
            for edge in node.get("edges", []):
                next_id = edge.get("destination_node_id")
                if not next_id or next_id in seen or next_id not in self.nodes:
                    continue
                next_path = path + [next_id]
                if next_id == target_id:
                    return next_path
                seen.add(next_id)
                queue.append((next_id, next_path))
        return []

    def _apply_forward_guard(self, target_node: dict[str, Any]) -> dict[str, Any]:
        """
        Prevents transitioning to a node that has already collected its data.
        If target node is fulfilled, fast forwards to its first destination.
        """
        current = target_node
        visited = set()
        while self._should_skip_node(current):
            if current["id"] in visited:
                break
            visited.add(current["id"])
            forward_id = self._first_destination(current)
            if not forward_id:
                break
            forward_node = self.nodes.get(forward_id)
            if not forward_node:
                break
            _log("STATE FAST-FORWARD", f"Skipping {current['id']} -> {forward_node['id']} ({self._skip_reason(current)})")
            current = forward_node
        
        return current

    def _edge_condition_matches_intent(self, condition: str, intents_to_match: list[str]) -> bool:
        """
        Semantic intent mapping layer for edge conditions.
        This protects config-generated flows where transitions are defined by
        edge condition text instead of destination intent_triggers.
        """
        if not condition:
            return False
        normalized = condition.strip().lower().replace("_", " ")
        
        # 2.1 Intent Alias Mapping (Expanded as per instruction)
        intent_aliases = {
            "confirm": {
                "confirm", "affirm", "yes", "acknowledge", "open_yes", "perm_yes",
                "conv_yes", "okay", "ok", "sure", "haan", "acha",
                "conversation finished", "finished",
            },
            "deny": {"deny", "no", "reject", "decline", "conv_no", "not_interested"},
            "deny_time": {"deny_time", "busy", "not_now", "call_later", "perm_busy", "later"},
            "provide_intent": {"provide_intent", "intent_active", "intent_info", "ask_info"},
            "provide_info": {
                "provide_info", "provide info", "info", "particulars", "details",
                "conversation finished", "finished",
            },
            # 2.2 Fallback Routing - retry is a synonym for unclear
            "unclear": {
                "unclear", "fallback", "retry", "hmm", "noise",
                "conversation finished", "finished",
            },
            "unclear_intent": {"unclear_intent", "intent_fallback"},
            "slots_collected": {"slots_collected", "qual_done", "completed", "details provided"},
            # FIX F: real_estate.json writes edge conditions as
            # "budget provided" / "location provided" / "property type provided",
            # i.e. noun-first. The literal substring test below looks for
            # "provide budget" and never matched. Alias both directions.
            "provide_budget": {
                "provide_budget", "budget provided", "budget given",
                "budget", "budget complete",
            },
            "provide_location": {
                "provide_location", "location provided", "city provided",
                "location", "area provided",
            },
            "provide_property_type": {
                "provide_property_type", "property type provided",
                "property type", "property provided",
            },
            "buyer_requirements_ready": {
                "buyer_requirements_ready", "requirements ready",
                "requirements collected", "budget provided", "details provided",
            },
            "confirm_site_visit": {
                "confirm_site_visit", "interested in visit", "wants visit",
                "site visit",
            },
        }
        
        for intent in intents_to_match:
            key = intent.strip().lower().replace("_", " ")
            if key and key in normalized:
                return True
            aliases = intent_aliases.get(intent.strip().lower(), set())
            if any(alias.replace("_", " ") in normalized for alias in aliases):
                return True
        return False

    @property
    def entity_keys(self) -> set[str]:
        standard_keys = {"intent_value", "callback_date", "callback_time", "confirmation", "timeline"}
        keys = set(standard_keys)
        if hasattr(self, "extraction_fields"):
            for field in self.extraction_fields:
                keys.add(field)
                keys.add(field.lower())
                keys.add(field.lower().replace(" ", "_"))
        return keys

    def _merge_entities(self, entities: dict[str, Any], intent: str = "") -> None: 
        entities = dict(entities)

        # ---------------------------------------------------------------------
        # FIX D — ENTITY FIELD ALIASES
        #
        # The intent model emits ExtractedEntities field names
        # (budget_range, preferred_bhk, timeline_weeks) but the flow JSON
        # collects slot names (budget, property_type, timeline).
        # The matching loop below compares names exactly, so "budget_range"
        # never matched the "budget" slot and the Ask Budget node asked
        # forever. Alias them here, once.
        # ---------------------------------------------------------------------
        # FIX Q: timeline_weeks REMOVED from this alias map.
        #
        # timeline_weeks is the LLM's estimate of purchase urgency in weeks
        # ("immediate" -> 0, "next month" -> 4) — a completely different
        # concept from the flow's "timeline" slot, which needs the literal
        # visit date/time text ("Shaniwaar subhe 11 baje"). Aliasing them
        # together meant that whenever intent was misclassified (so the
        # dedicated visit-datetime text capture never ran), a purchase-
        # urgency default of 0 silently satisfied the visit-date slot,
        # skipped the whole "what day and time" step, and fast-forwarded
        # straight past the real confirmation.
        _ENTITY_ALIASES = {
            "budget_range": "budget",
            "preferred_bhk": "property_type",
        }

        for _src, _dst in _ENTITY_ALIASES.items():
            _val = entities.get(_src)
            if _val in (None, "") or entities.get(_dst) not in (None, ""):
                continue
            entities[_dst] = _val
            _log("ENTITY ALIAS", f"{_src} -> {_dst} = {_val}")

            # Derive property_type when the LLM omits it.
        # This keeps state progression reliable for common real-estate
        # expressions such as "1 BHK", "2 BHK flat", or "apartment".
        if not entities.get("property_type"):
            source_parts = []

            if entities.get("preferred_bhk"):
                source_parts.append(str(entities["preferred_bhk"]))

            source_parts.append(str(self._last_user_text or ""))

            property_source = " ".join(source_parts).strip().lower()

            property_patterns = (
                (r"\b([1-5])\s*bhk\b", r"\1 BHK"),
                (r"\bflat\b", "flat"),
                (r"\bapartment\b", "flat"),
                (r"\bvilla\b", "villa"),
                (r"\bplot\b", "plot"),
                (r"\bstudio\b", "studio"),
            )

            for pattern, replacement in property_patterns:
                match = re.search(pattern, property_source, re.IGNORECASE)
                if match:
                    if callable(replacement):
                        derived_type = replacement(match)
                    else:
                        derived_type = match.expand(replacement)

                    entities["property_type"] = derived_type
                    _log(
                        "ENTITY DERIVED",
                        f"property_type={derived_type}"
                    )
                    break

        for key in self.entity_keys:
            # Match keys case-insensitively or with space/underscore normalization
            value = None
            for e_key, e_val in entities.items():
                if e_key.lower().replace(" ", "_") == key.lower().replace(" ", "_"):
                    value = e_val
                    break
            if value in (None, ""):
                continue

            # Map normalized key to canonical field name from extraction_fields if applicable
            canonical_key = key
            if hasattr(self, "extraction_fields"):
                for field in self.extraction_fields:
                    if field.lower().replace(" ", "_") == key.lower().replace(" ", "_"):
                        canonical_key = field
                        break

            existing = self.conversation_data.get(canonical_key)
            if existing:
                # Allow overwrite when user explicitly provides via provide_* intent
                is_explicit_provide = intent.startswith("provide_")
                # Allow overwrite when existing value is a known-invalid timeline
                is_stale_timeline = (
                    canonical_key == "timeline"
                    and any(inv in str(existing).lower() for inv in INVALID_TIMELINE_VALUES)
                )
                if not is_explicit_provide and not is_stale_timeline:
                    continue
                _log("ENTITY OVERWRITE", f"{canonical_key}: \"{existing}\" -> \"{value}\"")
            cleaned = self._clean_entity_value(canonical_key, value)
            if cleaned is None:
                # H4 FIX: User-facing warning for rejected entities
                _log("ENTITY SKIPPED/REJECTED", f'Key "{canonical_key}" dropped due to validation failure on value "{value}"')
                continue
            self.conversation_data[canonical_key] = cleaned
            _log("ENTITY", f"{canonical_key}={cleaned}")
            
            # Explicit debug log as requested
            _log("SLOT UPDATED", f"intent={cleaned}" if canonical_key == "intent_value" else f"{canonical_key}={cleaned}")

    def _should_skip_node(self, node: dict[str, Any]) -> bool:
        if node.get("name") in NON_SKIPPABLE_NAMES or node.get("type") == "end":
            return False
        collects = self._collect_slots(node)
        if not collects:
            return False
        return all(self.conversation_data.get(slot) for slot in collects)

    def _skip_reason(self, node: dict[str, Any]) -> str:
        slots = self._collect_slots(node)
        if not slots:
            return "already collected"
        if len(slots) == 1:
            return f"{slots[0]} already collected"
        return f"{', '.join(slots)} already collected"

    def _collect_slots(self, node: dict[str, Any]) -> list[str]:
        collects = node.get("collects")
        if isinstance(collects, str) and collects:
            return [collects]
        if isinstance(collects, list):
            return [slot for slot in collects if isinstance(slot, str) and slot]
        return []

    def _missing_slots(self, node: dict[str, Any]) -> list[str]:
        return [slot for slot in self._collect_slots(node) if not self.conversation_data.get(slot)]

    def _first_destination(self, node: dict[str, Any]) -> str:
        edge = next(iter(node.get("edges", [])), None)
        return edge.get("destination_node_id", "") if edge else ""

    def _resolve_skip_destination(self, node: dict[str, Any]) -> str:
        edges = node.get("edges", [])
        if not edges:
            return ""
            
        # Try to match slot values against edge conditions
        for slot in self._collect_slots(node):
            val = self.conversation_data.get(slot)
            if val:
                val_list = [str(val).lower().strip()]
                if isinstance(val, list):
                    val_list = [str(v).lower().strip() for v in val]
                for edge in edges:
                    condition = (edge.get("condition") or "").lower().strip()
                    if any(condition == v or v in condition for v in val_list):
                        destination_id = edge.get("destination_node_id")
                        if destination_id:
                            return destination_id
                            
        # Fallback to first destination
        return self._first_destination(node)

    def _normalize_intent_for_context(
        self,
        current_node: dict[str, Any],
        intent: str,
        entities: dict[str, Any],
        user_text: str,
    ) -> str:
        text = (user_text or "").strip().lower()
        # Remove punctuation for signal matching
        clean_text = re.sub(r'[^\w\s]', ' ', text).strip()
        clean_text = re.sub(r'\s+', ' ', clean_text)
        clean_words = clean_text.split()
        asks_call_purpose = any(phrase in clean_text for phrase in PURPOSE_CLARIFICATION_PHRASES)
        asks_call_purpose = asks_call_purpose or (
            ("what" in clean_words or "why" in clean_words or "who" in clean_words)
            and ("about" in clean_words or "call" in clean_words or "calling" in clean_words)
        )

        node_id = current_node.get("id")
        if asks_call_purpose:
            return intent

        if node_id in {"node-1735267546732", "node-ask-city", "fallback_location", "fallback-city", "fallback_budget", "fallback-budget"}:
            if self._is_location_suggestion(clean_text, current_node):
                _log("INTENT NORMALIZED", "Location suggestion requested -> ask_location_suggestion")
                return "ask_location_suggestion"

        # ── buyer_requirements_ready: auto-transition when both location+budget collected ──
        # FIX E: the Ask Budget node (node-1767421000002) was NOT in this list.
        # Its only outgoing edge points at node-1736323961832, whose single
        # intent_trigger is "buyer_requirements_ready" — and the edge condition
        # text "budget provided" does not alias to provide_budget either.
        # Result: budget got collected and the node still had no way out, so it
        # re-asked the budget question forever. Include the budget/location
        # nodes and their fallbacks here.
        if node_id in {
            "node-1735267546732", "node-ask-city",
            "node-1767421000001", "node-1767421000002",
            "fallback_budget", "fallback-budget", "node_fallback_budget",
            "fallback_location", "fallback-city",
        }:
            def is_valid_val(v):
                if not v or not isinstance(v, str): return False
                s = v.lower().strip()
                if s in ("", "null", "none", "preference", "no preference", "anywhere", "flexible", "open"): return False
                return True

            loc_ready = self.conversation_data.get("location") or entities.get("location")
            bud_ready = (
                self.conversation_data.get("budget")
                or entities.get("budget")
                or entities.get("budget_range")
            )

            if is_valid_val(loc_ready) and is_valid_val(bud_ready):
                _log("INTENT NORMALIZED", "Both location+budget collected -> buyer_requirements_ready")
                return "buyer_requirements_ready"

            # On the budget node itself, a fresh budget alone is enough to move on.
            if node_id in {"node-1767421000002", "fallback_budget", "fallback-budget", "node_fallback_budget"}:
                if is_valid_val(bud_ready):
                    _log("INTENT NORMALIZED", "Budget collected on budget node -> buyer_requirements_ready")
                    return "buyer_requirements_ready"

        # ── Ask Intent node: map purchase/investment answers to provide_intent ──────
        # The LLM sometimes returns confirm_identity/unclear for "for myself",
        # "investment", "personal use" etc. We intercept here to keep the flow moving.
        if node_id in {"node-1735264921453", "node-explain", "fallback_intent", "fallback-intent"}:
            _BUY_SIGNALS = (
                # Personal use
                "for myself", "myself", "personal use", "own use", "personal",
                "self use", "for self", "for me", "my own", "own home",
                "end use", "end-use", "residential", "to live", "to stay",
                "to reside", "living", "my family", "my wife", "my husband",
                "for us", "for our family", "for staying", "to settle",
                "primary residence", "primary home", "first home",
                "to move in", "we want to buy", "i want to buy",
                "buying for myself", "buying for us", "purchase",
                "apne liye", "khud ke liye", "ghar chahiye", "rehne ke liye",
                "own house", "want a house", "need a house", "need a home",
                "flat for myself", "flat for us", "apartment for myself",
                "house for myself", "villa for myself", "2bhk for myself",
                "3bhk for myself", "buying it", "buy it", "want to buy",
                "looking to buy", "planning to buy", "planning to purchase",
                "self occupied", "self-occupied", "owner occupied",
                "not investment", "not for rent", "not for renting",
            )
            _INVEST_SIGNALS = (
                # Investment / rental
                "investment", "invest", "as an investment", "for investment",
                "rental income", "rental", "renting out", "to rent",
                "for rent", "as rental", "rental property", "yield",
                "returns", "return on investment", "roi", "passive income",
                "for tenants", "for renting", "tenant", "lease out",
                "nikivesh", "nivesh", "kiraya", "rent ke liye",
                "buy to let", "buy to rent", "rental yield", "commercial use",
                "not for myself", "not to stay", "to let out", "to give out",
                "portfolio", "real estate portfolio", "property investment",
                "second property", "additional property",
            )
            _SELL_SIGNALS = (
                # Seller signals
                "sell", "selling", "want to sell", "seller", "i am selling",
                "i'm selling", "my property", "selling my flat",
                "selling my house", "selling my property", "want to sell my",
                "looking to sell", "planning to sell", "list my property",
                "bechna hai", "bechna chahta", "apna ghar bechna",
                "property for sale", "sale my flat", "sell my apartment",
            )
            _RENT_SIGNALS = (
                "for rent", "to rent", "looking to rent", "renting",
                "need on rent", "want to rent", "rental home",
                "rent a flat", "rent an apartment", "on lease",
                "lease", "rented accommodation", "rented flat",
                "kiraaye pe", "rent pe lena", "kiraya par lena",
            )
            if any(sig in clean_text for sig in _BUY_SIGNALS):
                if not entities.get("intent_value"):
                    entities["intent_value"] = "buy"
                _log("INTENT NORMALIZED", "Ask Intent: buy/personal detected -> provide_intent")
                return "provide_intent"
            if any(sig in clean_text for sig in _INVEST_SIGNALS):
                if not entities.get("intent_value"):
                    entities["intent_value"] = "invest"
                _log("INTENT NORMALIZED", "Ask Intent: investment detected -> provide_intent")
                return "provide_intent"
            if any(sig in clean_text for sig in _RENT_SIGNALS):
                if not entities.get("intent_value"):
                    entities["intent_value"] = "rent"
                _log("INTENT NORMALIZED", "Ask Intent: rent detected -> provide_intent")
                return "provide_intent"
            if any(sig in clean_text for sig in _SELL_SIGNALS):
                _log("INTENT NORMALIZED", "Ask Intent: seller detected -> seller_interest")
                return "seller_interest"

        # FIX A2: guard against null-ish placeholder values ("null", "none", ...)
        # arriving from the intent model. Without this, every turn was forced to
        # provide_location and the flow stalled on the current node.
        def _has_value(v) -> bool:
            if v is None:
                return False
            return str(v).strip().lower() not in {
                "", "null", "none", "nil", "na", "n/a",
                "undefined", "unknown", "nan",
            }

        if _has_value(entities.get("location")) and not intent.startswith("provide"):
            return "provide_location"
        if _has_value(entities.get("budget")) and not intent.startswith("provide"):
            return "provide_budget"

        if current_node.get("id") in VISIT_SCHEDULING_NODES:
            is_visit_rejection = any(rej in clean_text for rej in [
                "not interested", "don't want", "no visit"
            ])
            if not is_visit_rejection:
                datetime_keywords = [
                    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                    "today", "tomorrow", "tonight",
                    "next week", "this week", "weekend",
                    "morning", "afternoon", "evening", "night",
                    "am", "pm",
                    # FIX S: Hindi/Hinglish day names and time words — the
                    # English-only list above meant a Hindi answer like
                    # "Shaniwaar subhe 11 baje" was never recognized as a
                    # date/time reply on this node.
                    "somvar", "mangalvar", "budhvar", "guruvar", "shukravar",
                    "shanivar", "shaniwar", "raviwar", "ravivar",
                    "aaj", "kal", "parso",
                    "subah", "subh", "subhe", "shaam", "sham", "dopahar", "raat",
                    "baje", "bje",
                ]
                has_datetime_keyword = any(re.search(rf"\b{kw}\b", clean_text) for kw in datetime_keywords)
                has_date_number = any(char.isdigit() for char in clean_text)
                
                if entities.get("timeline") or has_datetime_keyword or has_date_number:
                    if not entities.get("timeline") and not entities.get("visit_time"):
                        entities["timeline"] = user_text.strip()
                    return "provide_visit_datetime"

        # Callback scheduling accepts broad natural time expressions and normalizes
        # them into a specific callback time so we don't loop on fallback prompts.
        if node_id in {"node-1736492391269", "node-callback", "fallback_callback_time"}:
            callback_time_keywords = [
                "morning", "afternoon", "evening", "night",
                "am", "pm", "after", "post", "around",
                "later", "tomorrow", "today",
            ]
            has_callback_time_hint = any(re.search(rf"\b{kw}\b", clean_text) for kw in callback_time_keywords)
            has_callback_number = any(char.isdigit() for char in clean_text)
            if entities.get("timeline") or has_callback_time_hint or has_callback_number:
                timeline_raw = str(entities.get("timeline") or user_text).strip()
                entities["timeline"] = self._synthesize_callback_timeline(timeline_raw)
                return "provide_timeline"

        if clean_text in {"ok", "okay", "alright", "fine", "cool", "great", "sure", "thanks", "thank you", "done"}:
            if intent.startswith("unclear"):
                return "confirm"

        uncertain = {
            "i don't know", "dont know", "don't know", "not sure", "maybe",
            "not certain", "unsure", "i'm not sure", "im not sure",
            "hard to say", "hard to tell", "difficult to say", "can't say",
            "cant say", "no idea", "have no idea", "not really sure",
            "haven't decided", "havent decided", "still thinking",
            "still deciding", "not decided yet", "yet to decide",
            "pata nahi", "abhi pata nahi", "soch raha hoon", "socha nahi",
            "not fixed", "not finalized", "not finalised", "open",
            "anywhere", "anything", "whatever", "don't mind",
            "dont mind", "no specific preference", "not particular",
        }
        if any(phrase in text for phrase in uncertain):
            if current_node["id"] in {"node-1735264921453", "node-explain"}:
                return "unclear_intent"
            if current_node["id"] in {"node-1735267546732", "node-ask-city"}:
                if self.conversation_data.get("location") or entities.get("location"):
                    return "unclear_budget"
                if self.conversation_data.get("budget") or entities.get("budget"):
                    return "unclear_location"
                if "budget" in text or "price" in text:
                    return "unclear_budget"
                return "unclear_location"
            if current_node["id"] == "node-1767420514711":
                return "unclear_property_type"
            if current_node["id"] in {"node-1735265015507", "node-visit"}:
                return "unclear_visit_datetime"
            if current_node["id"] in {"node-1736492391269", "node-callback"}:
                return "unclear_callback_time"

        # ── Catch-all: signals ───────────────────────────────────────────
        _AFFIRMATIVE_SIGNALS = {
            # English
            "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "alright",
            "correct", "right", "go ahead", "fine", "sounds good", "of course",
            "absolutely", "definitely", "certainly", "exactly", "precisely",
            "that is correct", "thats right", "thats correct", "yes indeed",
            "indeed", "affirmative", "agreed", "i agree", "i do", "of course yes",
            "please", "please do", "yes please", "sure please",
            "speaking", "yes speaking", "yes this is", "yes i am", "yes it is",
            "it is", "it's me", "its me", "thats me", "thats right",
            # Hindi / Indian English
            "haan", "han", "ha", "ji", "theek", "bilkul", "zaroor",
            "theek hai", "haan ji", "bilkul theek", "haan bilkul",
            "sahi hai", "sahi baat", "zaroor karo", "haan karo",
        }
        _NEGATIVE_SIGNALS = {
            # English
            "no", "nope", "nah", "never", "not", "sorry no", "no sorry",
            "no thanks", "no thank you", "i dont", "i don't", "i do not",
            "not really", "not at all", "absolutely not", "definitely not",
            "certainly not", "no way", "nah thanks",
            # Hindi / Indian English
            "nahi", "na", "nako", "nai", "nahi ji", "bilkul nahi",
            "nahi chahiye", "nahi kar sakta", "nahi hoga", "nahi karunga",
            "mat karo", "band karo",
        }
        
        has_affirmative = any(
            signal in clean_words or clean_text == signal
            for signal in _AFFIRMATIVE_SIGNALS
        )
        has_negative = any(
            signal in clean_words or clean_text == signal
            for signal in _NEGATIVE_SIGNALS
        )
        
        # Immediate hang-up routing for real world scenarios
        HANGUP_PHRASES = (
            "cut the call", "hang up", "hanging up", "phone rakh", "call cut",
            "disconnect", "do not call", "don't call me", "stop calling", "stop talking",
            "i am driving", "driving right now", "call later", "call me back later",
            "busy right now", "i am busy", "dont have time", "don't have time"
        )
        if any(phrase in clean_text for phrase in HANGUP_PHRASES):
            target = self.nodes.get("node-universal-disconnect")
            if target:
                _log("INTENT NORMALIZED", "Universal disconnect triggered by user")
                # H8 FIX: Return string intent, not node dict. Routing handled in _resolve_by_intent.
                return "hangup"
        has_goodbye = any(
            phrase == clean_text or phrase in clean_text
            for phrase in GOODBYE_PHRASES
        )

        # ── No-preference detection for location/budget (must come before generic deny) ──
        _NO_LOCATION_PREFERENCE_PHRASES = (
            # Core no-preference
            "no preference", "no preference in city", "no preference for city",
            "don't have any preference", "dont have any preference",
            "no preference in area", "no preference for area",
            "any area", "any location", "any city", "any place",
            "doesn't matter", "does not matter", "doesn't matter where",
            "open to any", "flexible on location", "no specific area",
            "not considering any area", "not particular about area",
            "no area preference",
            # Extended no-preference
            "anywhere", "anywhere is fine", "anywhere works", "any location works",
            "no fixed location", "no specific location", "no particular area",
            "not specific about", "not particular about", "not fixed on",
            "open to all", "open to any area", "open to all areas",
            "you can suggest", "you suggest", "suggest me", "whatever you suggest",
            "as per your suggestion", "whatever is available", "whatever suits",
            "not bothered about location", "not fussy about area",
            "no location preference", "no city preference",
            "koi bhi area", "koi bhi jagah", "kahi bhi",
            "not tied to any area", "not fixed on area", "not restricted to area",
            "flexible", "flexible location", "flexible about location",
            "any locality", "any suburb", "any neighbourhood", "any neighborhood",
            "not particular", "no particular place", "no particular city",
            "doesn't matter to me", "does not matter to me",
            "not considering", "no consideration for area",
            "i'll consider anywhere", "will consider anywhere",
        )
        _NO_BUDGET_PREFERENCE_PHRASES = (
            # Core no-budget
            "no budget preference", "no preference for budget", "flexible budget",
            "any budget", "no fixed budget", "doesn't matter budget",
            "not sure about budget",
            # Extended
            "budget flexible", "budget is flexible", "i am flexible on budget",
            "flexible on budget", "no strict budget", "no hard budget",
            "open on budget", "open to budget", "depends on property",
            "depends on the property", "depends what's available",
            "can discuss", "can talk about it", "let's discuss",
            "will adjust", "can adjust budget", "adjustable budget",
            "no limit", "no particular budget", "no specific budget",
            "budget nahi pata", "budget fix nahi hai", "abhi pata nahi",
            "not sure yet", "haven't decided on budget", "still deciding",
            "budget toh discuss kar lete", "baat karte hai",
            "price toh dekh lete", "price dekh lete", "cost dekh lenge",
        )
        if node_id in {"node-1735267546732", "node-ask-city", "fallback_location", "fallback-city", "fallback_budget", "fallback-budget"}:
            has_no_loc_pref = any(phrase in clean_text for phrase in _NO_LOCATION_PREFERENCE_PHRASES)
            has_no_budget_pref = any(phrase in clean_text for phrase in _NO_BUDGET_PREFERENCE_PHRASES)
            if has_no_loc_pref and not self.conversation_data.get("location"):
                _log("INTENT NORMALIZED", "No location preference detected -> unclear_location")
                return "unclear_location"
            if has_no_budget_pref and not self.conversation_data.get("budget"):
                _log("INTENT NORMALIZED", "No budget preference detected -> unclear_budget")
                return "unclear_budget"

        # Prevent unclear stalls at opening/availability:
        # busy -> callback path, interested -> continue, neutral -> clarify/re-engage.
        if node_id == "node-1735264873079":
            if has_negative:
                return "deny_time"
            if has_goodbye:
                return "deny_time"
            if has_affirmative or intent == "confirm_availability":
                return "confirm"
            if any(hint in clean_text for hint in INTERESTED_HINTS):
                return "confirm"
            if any(hint in clean_text for hint in NOT_INTERESTED_HINTS):
                return "deny_time"
            if any(hint in clean_text for hint in BUSY_TIME_HINTS):
                return "deny_time"
            if intent.startswith("unclear") or intent == "ask_off_topic":
                return "unclear"

        if has_negative:
            return "deny"
        if has_goodbye:
            return "deny_interest"
        if has_affirmative:
            return "confirm"

        if intent.startswith("unclear") or intent == "ask_off_topic":
            if current_node["id"] in ("node-1735265209472", "node-1736567518748", "node-1736492485610"):
                return "confirm"
            if current_node["id"] in ("node-1735264921453", "fallback_intent"):
                return "unclear_intent"
            if current_node["id"] in ("node-1735267546732", "fallback_location", "fallback_budget"):
                if self.conversation_data.get("location") or entities.get("location"):
                    return "unclear_budget"
                if self.conversation_data.get("budget") or entities.get("budget"):
                    return "unclear_location"
                return "unclear_location"
            if current_node["id"] in ("node-1767420514711", "fallback_property_type"):
                return "unclear_property_type"
            if current_node["id"] in ("node-1735265015507", "fallback_visit_datetime"):
                return "unclear_visit_datetime"
            if current_node["id"] in ("node-1736492391269", "fallback_callback_time"):
                return "unclear_callback_time"
            # Removed the dangerous default return "confirm" here

        if intent in {"provide_timeline", "provide_visit_datetime"}:
            if current_node["id"] in {"node-1735265015507", "node-1736323961832"}:
                return "provide_visit_datetime"
            if current_node["id"] in {"node-1736492391269", "fallback_callback_time"}:
                return "provide_timeline"

        return intent

    def _contextual_unclear_intent(self, current_node: dict[str, Any], entities: dict[str, Any], text: str) -> str:
        node_id = current_node.get("id")
        if node_id in {"node-1735264921453", "node-explain", "fallback_intent", "fallback-intent"}:
            return "unclear_intent"
        if node_id in {"node-1735267546732", "node-ask-city", "fallback_location", "fallback-city", "fallback_budget", "fallback-budget"}:
            if self.conversation_data.get("location") or entities.get("location"):
                return "unclear_budget"
            if self.conversation_data.get("budget") or entities.get("budget"):
                return "unclear_location"
            if "budget" in text or "price" in text or "amount" in text:
                return "unclear_budget"
            return "unclear_location"
        if node_id in {"node-1767420514711", "fallback_property_type"}:
            return "unclear_property_type"
        if node_id in {"node-1735265015507", "fallback_visit_datetime"}:
            return "unclear_visit_datetime"
        if node_id in {"node-1736492391269", "node-callback", "fallback_callback_time"}:
            return "unclear_callback_time"
        return "unclear"

    def _is_location_suggestion(self, text: str, current_node: dict[str, Any]) -> bool:
        if current_node.get("id") not in {"node-1735267546732", "fallback_location"}:
            return False
        return any(phrase in text for phrase in LOCATION_SUGGESTION_PHRASES)

    def _clean_entity_value(self, key: str, value: Any) -> Optional[str]:
        text = re.sub(r"\s+", " ", str(value).strip())
        # Strip common punctuation (like Hindi full stop '।', commas, periods)
        text = re.sub(r"[.?।!,;]", "", text).strip()
        if not text:
            return None

        agent_type = self.schema.get("agent_type", "real_estate_sales")
        if agent_type == "real_estate_sales":
            if key == "location":
                # ── Issue 2: Hindi script transliteration ──
                if text in HINDI_LOCATION_TRANSLITERATION:
                    transliterated = HINDI_LOCATION_TRANSLITERATION[text]
                    _log("TRANSLITERATED LOCATION", f"{text} -> {transliterated}")
                    text = transliterated
                # ── Issue 3: phonetic STT normalization ──
                lowered = text.lower()
                if lowered in LOCATION_NORMALIZATION:
                    normalized = LOCATION_NORMALIZATION[lowered]
                    _log("NORMALIZED LOCATION", f"{text} -> {normalized}")
                    text = normalized
                return text if self._is_valid_location(text) else None
            if key == "budget":
                return text if self._is_valid_budget(text) else None
            if key == "property_type":
                normalized = self._normalize_property_type(text)
                return normalized if normalized and self._is_valid_property_type(normalized) else None
            if key == "timeline":
                normalized = self._normalize_timeline(text)
                if normalized != text:
                    _log("NORMALIZED TIMELINE", f"{text} -> {normalized}")
                return normalized if self._is_valid_timeline(normalized) else None
        else:
            # Generic validation: skip Pune specific whitelists
            if len(text) <= 1:
                return None
            return text
        return text

    def get_allowed_languages(self) -> list[str]:
        lang_str = str(self.schema.get("language") or "").lower()
        if not lang_str:
            lang_str = str(self.schema.get("languages") or "").lower()
            
        if not lang_str:
            return ["en", "hi", "mr", "hinglish"]

        allowed = []
        if "english" in lang_str or "en" in lang_str.split():
            allowed.append("en")
        if "hinglish" in lang_str:
            allowed.append("hinglish")
        if "hindi" in lang_str or "hi" in lang_str.split("+") or "hi" in lang_str.split():
            allowed.append("hi")
        if "marathi" in lang_str or "mr" in lang_str.split():
            allowed.append("mr")

        if not allowed:
            return ["en"]
        return allowed

    def _is_valid_location(self, value: str) -> bool:
        lowered = value.strip().lower()
        if len(lowered) <= 2 or not any(char.isalpha() for char in lowered):
            return False
        if lowered in INVALID_LOCATION_VALUES:
            return False
        # Accept if it matches the known location whitelist or normalization maps
        if lowered in KNOWN_LOCATION_WHITELIST:
            return True
        if lowered in LOCATION_NORMALIZATION:
            return True
        # Accept anything else that passed the LLM and basic checks
        return True

    def _is_valid_budget(self, value: str) -> bool:
        lowered = value.strip().lower()
        if lowered in INVALID_BUDGET_VALUES:
            return False
            
        # H6 FIX: Expand budget validation to accept word-form numbers
        number_words = {
            "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", 
            "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", 
            "hundred", "thousand", "lakh", "lakhs", "crore", "crores",
            "ek", "do", "teen", "char", "paanch", "chhe", "saat", "aath", "nau", "das",
            "bees", "tees", "chalis", "pachas", "saath", "sattar", "assi", "nabbe", "sau"
        }
        
        has_digit = any(char.isdigit() for char in lowered)
        has_number_word = any(word in lowered.split() for word in number_words)
        
        return has_digit or has_number_word

    def _normalize_property_type(self, value: str) -> str:
        normalized = re.sub(r"\b([123])\s*bhk\b", r"\1 BHK", value, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if normalized.lower() == "apartment":
            return "flat"
        return normalized

    def _normalize_timeline(self, value: str) -> str:
        lowered = value.strip().lower()
        
        if lowered == "yesterday":
            return "tomorrow"
            
        if "last" in lowered:
            return re.sub(r"\blast\b", "next", value, flags=re.IGNORECASE)
            
        if "previous" in lowered:
            return re.sub(r"\bprevious\b", "next", value, flags=re.IGNORECASE)
            
        if "ago" in lowered:
            return re.sub(r"\bago\b", "from now", value, flags=re.IGNORECASE)
            
        if "past" in lowered:
            return re.sub(r"\bpast\b", "upcoming", value, flags=re.IGNORECASE)
            
        return value

    def _synthesize_callback_timeline(self, value: str) -> str:
        """
        Convert loose callback time phrases into a concrete, friendly time.
        Examples:
          - "post 6 PM" -> "6:45 PM" (randomized after 6 PM)
          - "evening"   -> random time in evening window
        """
        text = value.strip()
        lowered = text.lower()
        normalized_lowered = re.sub(r"\b([ap])\s*\.?\s*m\.?\b", r"\1m", lowered)

        # Pattern: after/post <hour>[:minute] <am/pm>
        m = re.search(r"\b(?:after|post)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", normalized_lowered)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            meridiem = (m.group(3) or "").lower()
            base_minutes = self._to_24h_minutes(hour, minute, meridiem, normalized_lowered)
            # Pick a random slot after mentioned time.
            delta = random.choice([15, 30, 45, 60, 75, 90])
            return self._format_minutes_12h(base_minutes + delta)

        # If user gave part-of-day only, pick a random concrete time in that window.
        for part, (start_min, end_min) in CALLBACK_PART_OF_DAY_WINDOWS.items():
            if re.search(rf"\b{part}\b", normalized_lowered):
                return self._format_minutes_12h(random.randint(start_min, end_min))

        # Extract explicit times from a longer sentence (e.g. "6 p.m. would work").
        explicit_time = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", normalized_lowered)
        if explicit_time:
            hour = int(explicit_time.group(1))
            minute = int(explicit_time.group(2) or 0)
            meridiem = explicit_time.group(3)
            return self._format_minutes_12h(self._to_24h_minutes(hour, minute, meridiem, normalized_lowered))

        return text

    def _to_24h_minutes(self, hour: int, minute: int, meridiem: str, context_text: str) -> int:
        hour = max(0, min(hour, 23))
        minute = max(0, min(minute, 59))
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        elif not meridiem:
            # Infer PM for common callback evening contexts.
            if ("evening" in context_text or "night" in context_text or "post" in context_text or "after" in context_text) and hour <= 11:
                hour += 12
        return (hour * 60) + minute

    def _format_minutes_12h(self, total_minutes: int) -> str:
        total_minutes %= (24 * 60)
        hour_24 = total_minutes // 60
        minute = total_minutes % 60
        meridiem = "AM" if hour_24 < 12 else "PM"
        hour_12 = hour_24 % 12
        if hour_12 == 0:
            hour_12 = 12
        return f"{hour_12}:{minute:02d} {meridiem}"

    def _is_valid_timeline(self, value: str) -> bool:
        lowered = value.strip().lower()
        if any(inv in lowered for inv in INVALID_TIMELINE_VALUES):
            _log("TIMELINE REJECTED", f"Past or invalid timeline: \"{value}\"")
            return False
        # FIX Q2: a real visit-date/time answer always has non-numeric
        # content (a day name, "subah"/"baje"/"am"/"pm", a month, etc).
        # A bare number here (e.g. "0") is the purchase-urgency
        # timeline_weeks value leaking in through a different path, not an
        # actual date/time — reject it so the flow keeps asking instead of
        # silently skipping the whole visit-confirmation step.
        if re.fullmatch(r"[\d\s.,:-]+", lowered):
            _log("TIMELINE REJECTED", f"Bare numeric value, not a real date/time: \"{value}\"")
            return False
        return True

    def _is_valid_property_type(self, value: str) -> bool:
        lowered = value.strip().lower()
        allowed_patterns = (
            r"\b1\s*bhk\b",
            r"\b2\s*bhk\b",
            r"\b3\s*bhk\b",
            r"\bstudio\b",
            r"\bvilla\b",
            r"\bplot\b",
            r"\bflat\b",
        )
        return lowered not in INVALID_PROPERTY_TYPE_VALUES and any(
            re.search(pattern, lowered) for pattern in allowed_patterns
        )

    def _auto_advance_skip_edges(self, node: dict[str, Any]) -> dict[str, Any]:
        """Walk through skip edges to reach terminal nodes after response delivery."""
        current = node
        seen: set[str] = {current["id"]}
        while True:
            edges = current.get("edges", [])
            if not edges:
                return current
            # Check if the only edge is a skip edge
            if len(edges) == 1:
                condition = (edges[0].get("condition", "") or "").lower().strip()
                if condition in SKIP_EDGE_MARKERS:
                    dest_id = edges[0].get("destination_node_id")
                    dest = self.nodes.get(dest_id) if dest_id else None
                    if not dest or dest["id"] in seen:
                        return current
                    _log("SKIP EDGE", f"{current['id']} -> {dest['id']}")
                    seen.add(dest["id"])
                    self.visited_nodes.add(dest["id"])
                    current = dest
                    continue
            return current

    def _format_intent_log(self, intent: str, entities: dict[str, Any]) -> str:
        pairs = [f"{key}: {value}" for key, value in entities.items() if value not in (None, "")]
        if pairs:
            return f"intent={intent}  entities={{" + ", ".join(pairs) + "}"
        return f"intent={intent}"

    def _log_response(self, node: dict[str, Any], response: str) -> None:
        """Used only by non-process_turn callers (noise, greeting, next_step)."""
        _log("RESPONSE", f'[JSON] "{response}"')


# ---------------------------------------------------------------------------
# Issue 4 — Response truncation for TTS latency
# ---------------------------------------------------------------------------

def _truncate_response(
    text: str,
    max_words: int = cfg.MAX_RESPONSE_WORDS,
    max_sentences: int = cfg.MAX_RESPONSE_SENTENCES,
) -> str:
    """
    Enforce word and sentence limits on the final response text
    to keep TTS output short and reduce TTFB.

    Rules:
      1. Split on sentence-ending punctuation (. ! ?).
      2. Keep at most `max_sentences`.
      3. If total word count exceeds `max_words`, truncate to that limit.
      4. Rejoin sentences with newline for natural TTS pause.
    """
    if not text or not text.strip():
        return text

    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    # Limit sentence count
    sentences = sentences[:max_sentences]

    # Rejoin and check word count
    joined = "\n".join(sentences)
    words = joined.split()
    if len(words) > max_words:
        truncated = " ".join(words[:max_words])
        # Ensure it ends with punctuation
        if not truncated.rstrip().endswith((".", "!", "?")):
            truncated = truncated.rstrip().rstrip(".,;:") + "."
        _log("TRUNCATE", f"Response trimmed from {len(words)} to {max_words} words")
        return truncated

    return joined