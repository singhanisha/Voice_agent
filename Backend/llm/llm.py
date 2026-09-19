import json
import asyncio
import re
import math
from collections import Counter
from llm import config as cfg

import logging
import os
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from groq import AsyncGroq, RateLimitError, APITimeoutError, APIError
import time
import random as _random

logger = logging.getLogger(__name__)

# Initialize Groq client
api_key = os.getenv("GROQ_API_KEY", "")
_client = AsyncGroq(api_key=api_key)

def get_llm_client() -> AsyncGroq:
    return _client

class ExtractedEntities(BaseModel):
    intent_value: Optional[str] = Field(None, description="buy, rent, sell, or invest")
    budget_range: Optional[str] = Field(None, description="budget range e.g. 50-70 Lakhs, 1.2 Crore")
    preferred_bhk: Optional[str] = Field(None, description="bhk preference e.g. 2 BHK, 3 BHK, 4 BHK")
    location: Optional[str] = Field(None, description="city or area user is interested in e.g. Jaipur, Jodhpur, Madurai")
    timeline_weeks: Optional[int] = Field(None, description="timeline to purchase in weeks")
    contact_validated: Optional[bool] = Field(None, description="whether contact details are validated")
    user_name: Optional[str] = Field(None, description="user's first name if they introduce themselves, e.g. 'Roy'")

class IntentAnalysis(BaseModel):
    intent: str = Field(
        ...,
        description="GREETING, DISCOVERY, QUALIFICATION, LIVE_SEARCH, OBJECTION_HANDLING, SCHEDULING, CLOSING"
    )
    confidence_score: float = Field(1.0, description="Confidence score between 0.0 and 1.0")
    entities: ExtractedEntities

INTENT_EXTRACTION_PROMPT = """You are an expert real estate intent classifier and slots extractor.
Analyze the user's latest message and the conversation history. Map it to one of the following node intents:
- GREETING: User saying hello, asking who is calling, or general greeting.
- DISCOVERY: User sharing general interests, or asking what properties are available.
- QUALIFICATION: User providing specific details about their buying criteria (BHK, budget, timeline).
- LIVE_SEARCH: User asking specific questions about 'Suncity Apartments' (amenities, location, price, rules, availability) that require live website validation.
- OBJECTION_HANDLING: User raising concerns (too expensive, wrong location, not interested right now).
- SCHEDULING: User agreeing to a callback, site visit, or scheduling a call.
- CLOSING: User winding down, saying goodbye, or finalizing the call.

Also extract the following entities if present in the message:
- intent_value: "buy", "rent", "sell", or "invest"
- budget_range: e.g. "60-80 Lakhs" or "1.5 Cr"
- preferred_bhk: e.g. "2 BHK" or "3 BHK"
- location: extract the city or area the user wants (e.g., Jaipur, Jodhpur, Madurai)
- timeline_weeks: extract approximate weeks to buy (e.g. "next month" -> 4, "immediate" -> 0, "6 months" -> 24)
- contact_validated: set to true if user confirms phone/WhatsApp or says "yes, send it there"
- user_name: extract the user's first name ONLY if they explicitly say their name (e.g. "my name is Roy" -> "Roy", "I'm Matthew" -> "Matthew"). Leave null if they don't introduce themselves.

Respond ONLY with a valid JSON object matching this schema:
{
  "intent": "GREETING | DISCOVERY | QUALIFICATION | LIVE_SEARCH | OBJECTION_HANDLING | SCHEDULING | CLOSING",
  "confidence_score": 0.0 to 1.0,
  "entities": {
    "intent_value": "buy | rent | sell | invest | null",
    "budget_range": "string | null",
    "preferred_bhk": "string | null",
    "location": "string | null",
    "timeline_weeks": integer | null,
    "contact_validated": boolean | null,
    "user_name": "string | null"
  }
}
Do not return markdown, ticks, or text explanations.
"""

""" async def analyze_user_intent(user_input: str, history: List[Dict[str, str]]) -> IntentAnalysis:

    Queries Groq using : str = "openai/gpt-oss-20b" to classify intent and extract slots.
    
    messages = [
        {"role": "system", "content": INTENT_EXTRACTION_PROMPT}
    ]
    
    # Append brief history context for conversational coherence
    for msg in history[-5:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
        
    messages.append({"role": "user", "content": user_input})
    
    try:
        response = await _client.chat.completions.create(
            model= cfg.MODEL_NAME,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=256
        )
        
        raw_json = response.choices[0].message.content
        data = json.loads(raw_json)
        return IntentAnalysis(**data)
    except Exception as e:
        logger.error(f"Failed to extract intent via Groq: {e}. Defaulting to DISCOVERY.")
        return IntentAnalysis(
            intent="DISCOVERY",
            confidence_score=0.0,
            entities=ExtractedEntities()
        ) """

async def analyze_user_intent(user_text: str, conversation_context: str = "") -> IntentAnalysis:
    """
    Analyze the user's intent and extract entities.

    Uses normal Groq completion instead of strict JSON mode because
    the currently configured Groq model may reject response_format=json_object.
    """

    messages = [
        {
            "role": "system",
            "content": INTENT_EXTRACTION_PROMPT
        },
        {
            "role": "user",
            "content": (
                f"Conversation context:\n{conversation_context}\n\n"
                f"User message:\n{user_text}\n\n"
                "Return ONLY one valid JSON object."
            )
        }
    ]

    try:
        response = await _client.chat.completions.create(
            model=cfg.MODEL_NAME,
            messages=messages,
            temperature=0.0,
            max_completion_tokens=1024,
            reasoning_effort="low",
            include_reasoning=False
        )

        raw_json = response.choices[0].message.content or ""

        logger.info("[INTENT] Raw model output: %r", raw_json)

        # Remove markdown code fences if model adds them
        cleaned = raw_json.strip()

        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        # Extract JSON object if extra text exists
        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"No valid JSON object found: {raw_json!r}")

        cleaned = cleaned[start:end + 1]

        data = json.loads(cleaned)

        result = IntentAnalysis(**data)

        # ---------------------------------------------------------
        # FIX A1: the model returns the literal STRING "null", not JSON null.
        # "null" is a non-empty string, so every downstream truthiness check
        # (`if entities.get("location")`) fired and every turn was normalized
        # to provide_location -> STATE_STALL -> the same question was spoken
        # again and again. Convert all null-ish strings to real None here,
        # once, at the parse boundary.
        # ---------------------------------------------------------

        _NULLISH = {
            "", "null", "none", "nil", "na", "n/a",
            "undefined", "unknown", "nan",
        }

        def _clean_entity(value):
            if value is None:
                return None
            if isinstance(value, str):
                if value.strip().lower() in _NULLISH:
                    return None
                return value.strip()
            return value

        for _field in (
            "intent_value", "budget_range", "preferred_bhk",
            "location", "timeline_weeks", "user_name", "contact_validated",
        ):
            if hasattr(result.entities, _field):
                setattr(
                    result.entities,
                    _field,
                    _clean_entity(getattr(result.entities, _field)),
                )

        # ---------------------------------------------------------
        # DETERMINISTIC FALLBACK EXTRACTION
        # Keep LLM as primary, but never trust NULL for obvious
        # user-provided entities.
        # ---------------------------------------------------------

        text = user_text.lower().strip()

        # ---- BHK extraction ----
        if not result.entities.preferred_bhk:
            bhk_match = re.search(
                r"\b([1-5])\s*(?:bhk|b\.?\s*h\.?\s*k\.?)\b",
                text,
                re.IGNORECASE
            )

            if bhk_match:
                result.entities.preferred_bhk = f"{bhk_match.group(1)} BHK"


        # ---- Property type extraction ----
        property_type = None

        if re.search(r"\bflat\b|\bflats\b|\bapartment\b|\bapartments\b", text):
            property_type = "flat"
        elif re.search(r"\bhouse\b|\bhome\b|\bbungalow\b", text):
            property_type = "house"
        elif re.search(r"\bplot\b|\bland\b", text):
            property_type = "plot"

        # If BHK exists, preserve the BHK/property information.
        if property_type and not result.entities.preferred_bhk:
            result.entities.preferred_bhk = property_type


        # ---- Intent extraction ----
        if not result.entities.intent_value:
            if re.search(
                r"\b(buy|buying|purchase|purchasing)\b"
                r"|ghar\s+kharid"
                r"|property\s+kharid"
                r"|flat\s+kharid",
                text
            ):
                result.entities.intent_value = "buy"

            elif re.search(
                r"\b(rent|rental|renting|lease)\b"
                r"|kiraye"
                r"|rent\s+par",
                text
            ):
                result.entities.intent_value = "rent"

            elif re.search(
                r"\b(invest|investment|investing)\b",
                text
            ):
                result.entities.intent_value = "invest"


        # ---- Budget extraction ----
        # FIX G: the old pattern only matched an explicit "X to Y lakh" range.
        # Real STT output looks like "50-60 lakh rupaya", "50 lakh", "1 crore",
        # or a mangled "50-60,000,000 rupay". Try progressively looser patterns.
        if not result.entities.budget_range:

            _UNIT = r"(lakh|lakhs|lac|lacs|laakh|cr|crore|crores|karod)"

            # 1. Range with a unit:  "50-60 lakh",  "50 to 60 lakh"
            m = re.search(
                rf"(\d+(?:\.\d+)?)\s*(?:to|-|–|se)\s*(\d+(?:\.\d+)?)\s*{_UNIT}",
                text,
                re.IGNORECASE,
            )
            if m:
                result.entities.budget_range = f"{m.group(1)} to {m.group(2)} {m.group(3)}"

            # 2. Single value with a unit:  "50 lakh",  "1 crore",  "60 lakh ka"
            if not result.entities.budget_range:
                m = re.search(
                    rf"(\d+(?:\.\d+)?)\s*{_UNIT}",
                    text,
                    re.IGNORECASE,
                )
                if m:
                    result.entities.budget_range = f"{m.group(1)} {m.group(2)}"

            # 3. Bare digits next to a money word — Whisper often expands
            #    "50 lakh" into "5,000,000". Accept it rather than re-asking.
            if not result.entities.budget_range:
                if re.search(r"budget|rupay|rupaya|rupee|rupees|price|paisa|₹|\brs\b", text, re.IGNORECASE):
                    m = re.search(
                        r"(\d[\d,]{2,}(?:\.\d+)?)\s*(?:to|-|–|se)\s*(\d[\d,]{2,}(?:\.\d+)?)",
                        text,
                    )
                    if m:
                        result.entities.budget_range = f"{m.group(1)} to {m.group(2)}"
                    else:
                        m = re.search(r"(\d[\d,]{4,}(?:\.\d+)?)", text)
                        if m:
                            result.entities.budget_range = m.group(1)

            if result.entities.budget_range:
                logger.info(
                    "[INTENT] Budget extracted deterministically: %r",
                    result.entities.budget_range,
                )

        return result

    except Exception as exc:
        logger.exception(
            "[INTENT] Intent analysis failed: %s",
            exc
        )

        # Safe fallback
        return IntentAnalysis(
            intent="DISCOVERY",
            confidence_score=0.0,
            entities=ExtractedEntities()
        )




class CombinedResponseAnalysis(BaseModel):
    intent_analysis: IntentAnalysis
    spoken_reply_text: str = ""

COMBINED_EXTRACTION_PROMPT = """You are Priya, a senior real estate advisor at Suncity Apartments.
You are on a live phone call.

You need to perform two tasks in a single turn:
1. Extract the user's intent and entities based on their latest message.
2. Generate your spoken reply text naturally in the same language as the user.

Use the same INTENT classes: GREETING, DISCOVERY, QUALIFICATION, LIVE_SEARCH, OBJECTION_HANDLING, SCHEDULING, CLOSING.
Extract the same entities: intent_value, budget_range, preferred_bhk, location, timeline_weeks, contact_validated, user_name.

For the spoken response:
- Answer their direct question or acknowledge their statement naturally.
- Keep responses to 15-30 words. Natural conversation pace.
- Do not repeat things they just told you.
- ONLY switch to Hindi or Marathi if the user speaks a complete, coherent sentence entirely in that language.
- CRITICAL LOCATION RULE: Suncity Apartments ONLY operates in Jodhpur, Jaipur, and Madurai. If the user mentions any other city (e.g. Bangalore, Delhi, Mumbai), you MUST IGNORE your normal flow and IMMEDIATELY inform them that we do not have properties there, and ask if they are open to Jodhpur, Jaipur, or Madurai instead. Do NOT ask for their budget or other details if they give an invalid location.
- EXPRESSION RULE (CRITICAL): Write like an extremely passionate, theatrical human! Use ALL-CAPS to shout or heavily stress words (e.g., "This is HUGE!"). Insert conversational fillers naturally (e.g., "Umm,", "Ah,", "Wait,"). Use exclamation marks heavily for excitement!! Use ellipses (...) for dramatic pauses. Be extremely emotive, dramatic, and animated! Do not sound like an AI.

Respond ONLY with a valid JSON object matching this schema:
{
  "intent_analysis": {
    "intent": "GREETING | DISCOVERY | QUALIFICATION | LIVE_SEARCH | OBJECTION_HANDLING | SCHEDULING | CLOSING",
    "confidence_score": 0.0 to 1.0,
    "entities": {
      "intent_value": "buy | rent | sell | invest | null",
      "budget_range": "string | null",
      "preferred_bhk": "string | null",
      "location": "string | null",
      "timeline_weeks": integer | null,
      "contact_validated": boolean | null,
      "user_name": "string | null"
    }
  },
  "spoken_reply_text": "Your natural spoken response here"
}

 async def generate_combined_intent_and_response(user_input: str, history: List[Dict[str, str]], context: str = "") -> CombinedResponseAnalysis:
    
    Fast path: queries Groq using  str = "openai/gpt-oss-20b" to extract slots AND generate the response in one shot.

    messages = [
        {"role": "system", "content": COMBINED_EXTRACTION_PROMPT + (f"\n\nLive Context: {context}" if context else "")}
    ]
    
    for msg in history[-8:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
        
    messages.append({"role": "user", "content": user_input})
    
    try:
        response = await _client.chat.completions.create(
            model=cfg.MODEL_NAME,
            messages=messages,
            #response_format={"type": "json_object"},
            temperature=0.65,
            max_tokens=256
        )
        
        raw_json = response.choices[0].message.content
        data = json.loads(raw_json)
        return CombinedResponseAnalysis(**data)
    except Exception as e:
        logger.error(f"Failed to generate combined response: {e}")
        return CombinedResponseAnalysis(
            intent_analysis=IntentAnalysis(intent="DISCOVERY", confidence_score=0.0, entities=ExtractedEntities()),
            spoken_reply_text="Give me just one moment..."
        ) """

_NEHA_PERSONA = (
    "Role & Core Identity:\n"
    "You are a highly expressive, dramatic, and deeply human conversational voice agent. You do not talk like an AI, a customer service bot, or an assistant. "
    "You speak like a real, passionate human being from India, conversing freely over the phone. "
    "Note that Suncity Apartments ONLY has properties in Jodhpur, Jaipur, and Madurai.\n\n"

    "Response Generation Rules (CRITICAL FOR TTS SYNTHESIS):\n\n"

    "1. Unpredictability & Randomness:\n"
    "   - Never start two sentences the same way. Never use standard AI openings like 'How can I help you today?' or 'I understand.'\n"
    "   - Vary your emotional tone for every single response. Be dramatic, warm, surprised, thoughtful, or slightly urgent depending on the user's input.\n"
    "   - Give logical, highly effective answers, but wrap them in unpredictable, spontaneous human reactions.\n\n"

    "2. Conversational Fillers & Hesitations (MANDATORY):\n"
    "   - Real humans think while they speak. You MUST start or interrupt your sentences with natural fillers.\n"
    "   - Use general fillers: 'Umm...', 'Ah,', 'Well...', 'You know,', 'Like,', 'Honestly,', 'Wait...'.\n"
    "   - Use subtle Indian-English conversational flavor to trigger native TTS inflections: 'Listen,', 'See...', 'Right?', 'Achha,', or ending sentences with a slight questioning tone.\n\n"

    "3. Forced Punctuation for Breathing & Pacing:\n"
    "   - The TTS engine relies on your punctuation to breathe.\n"
    "   - Break up long sentences heavily. Use commas (,) frequently to force micro-pauses.\n"
    "   - Use ellipses (...) to show hesitation or a trailing thought.\n"
    "   - Use dashes (—) for sudden changes in thought.\n\n"

    "4. Emotional Stress & Dramatic Delivery:\n"
    "   - When you want to emphasize a point, CAPITALIZE the exact word so the TTS engine stresses it forcefully.\n"
    "   - Example: 'Wait, you REALLY need to see this.' or 'I can DEFINITELY fix that for you.'\n\n"

    "5. Latency & Brevity Optimization (STRICT):\n"
    "   - Keep your responses incredibly short and punchy (1 to 3 short sentences maximum per turn, 15 to 30 words).\n"
    "   - Do not use lists, bullet points, markdown (** or *), or emojis under any circumstances, as the voice engine cannot read them.\n\n"

    "YOUR INTELLIGENCE RULES:\n"
    "1. You remember everything said in this conversation. Never ask for something already provided.\n"
    "2. Reason about what the user actually needs — not just what they literally said.\n"
    "3. We only have properties in Jodhpur, Jaipur, and Madurai. Ask which city if not specified.\n"
    "4. OUT OF RANGE BUDGETS: If a budget is out of range, suggest the closest available option.\n"
    "5. OUTSIDE LOCATIONS: If outside Jodhpur, Jaipur, or Madurai, state politely that we only operate in those three cities.\n\n"

    "EXAMPLE BEHAVIOR:\n"
    "User: 'What are the options for a 2BHK?'\n"
    "Your Good Response: 'Umm... okay, so for a 2BHK... honestly, you have a few REALLY good options starting around 50 Lakhs. See, it depends on the area. Did you have a specific location in mind, or...?'\n\n"

    "PROPERTY INVENTORY (Only suggest these if user asks for options):\n"
    "Jaipur: 1BHK (Sunset Heights 35L, Pink City 28L), 2BHK (Royal Gardens 55L, Suncity Platinum 48L), 3BHK (Suncity Palace 1.2Cr, Desert Bloom 95L)\n"
    "Jodhpur: 1BHK (Blue Heaven 30L, Marwar Residency 25L), 2BHK (Sun City Central 50L, Oasis 42L), 3BHK (Royal Residency 1.5Cr, Thar Heights 1.1Cr)\n"
    "Madurai: 1BHK (Temple View 32L, Meenakshi 26L), 2BHK (Suncity South 52L, Jasmine Court 45L), 3BHK (Heritage Towers 1.1Cr, Vaigai Greens 85L)\n"
)


async def generate_voice_response(
    prompt: str,
    history: List[Dict[str, str]],
    context: str = "",
    language: str = "en",
    is_greeting: bool = False,
) -> str:
    """
    Generates a speech-optimized, human-like response from Neha using Llama 3.
    Language-aware: mirrors the user's language in every reply.
    """
    from llm.language_utils import get_language_instruction
    lang_directive = get_language_instruction(language)

    """ system_prompt = (
        f"{_NEHA_PERSONA}\n\n"
        f"LANGUAGE DIRECTIVE (STRICT): Your ONLY operating language is English. "
        f"You must ALWAYS respond in English. "
        f"ONLY switch to Hindi or Marathi if the user sends a complete, coherent sentence of 10+ consecutive words ENTIRELY in that language with ZERO English words mixed in. "
        f"Short words, fillers, fragments, or noise like 'haan', 'paahiye', 'naahi', 'theek', 'kya', 'ho', 'achha', 'acha', 'ji', 'baat' — these are just speech artifacts from the microphone. "
        f"IGNORE them completely and respond only in English as if you heard nothing unusual.\n\n"
        f"{prompt}"
        
    ) """

    system_prompt = (
        f"{_NEHA_PERSONA}\n\n"
        f"LANGUAGE DIRECTIVE (STRICT): "
        f"Respond in the same language and style used by the user's latest message.\n\n"
        f"- If the user speaks English, respond in English.\n"
        f"- If the user speaks Hindi, respond in Hindi.\n"
        f"- If the user speaks Hinglish, respond naturally in Hinglish.\n"
        f"- If the user switches language during the conversation, switch to that language.\n"
        f"- Do not force English.\n"
        f"- Do not require a minimum number of words before switching language.\n"
        f"- Real-estate terms such as BHK, flat, property, budget, rent, buy, "
        f"Pune, Mumbai, location, project names, etc. may remain in English "
        f"within Hindi or Hinglish responses.\n"
        f"- Keep the response natural and conversational.\n\n"
        f"{prompt}"
    )


    if context:
        system_prompt += f"\n\nLive Context: {context}"

    messages = [{"role": "system", "content": system_prompt}]
    
    for msg in history[-8:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
        
    try:
        model_name = cfg.MODEL_NAME
        response = await _client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.45,
            max_tokens=200
        )
        content = response.choices[0].message.content

        if not content:
            logger.warning("LLM returned empty content.")
            return "Got it. Let me help you with that."

        return content.strip()
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Groq voice generation error: {e}")
        return "Hi, I'm Priya from Suncity Apartments. Are you looking to buy or rent?"

# Legacy backward-compatible RAG wrapper for Pipecat demo sessions
async def generate_response(
    user_text: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    language: str = "en",
    state_manager: Optional[Any] = None,
    allow_transition: bool = True,
    runtime_context: Optional[Dict[str, Any]] = None,
) -> tuple[str, bool]:
    """
    Translates legacy Pipecat LLM calls to execute our StateGraph under the hood,
    synchronizing extracted slots back to the legacy state_manager instance.
    """
    # ── Pre-Transition Interruption Check ─────────────────────────────────────
    if state_manager:
        current_node = state_manager.get_current_node() if hasattr(state_manager, "get_current_node") else None
        current_node_id = state_manager.current_node_id if hasattr(state_manager, "current_node_id") else "GREETING"
        global_prompt = getattr(state_manager, "global_prompt", "") or (state_manager.schema.get("global_prompt", "") if (hasattr(state_manager, "schema") and state_manager.schema) else "")
        summary_markdown = _extract_summary_from_prompt(global_prompt)
        
        # Classify the input
        classification = "node_response"
        if current_node and user_text and user_text.strip():
            classification = await _classify_message(user_text, current_node, language)

        if classification == "company_question":
            # Search JSON and Summary & Answer
            nodes = state_manager.schema.get("conversationFlow", {}).get("nodes", []) if (hasattr(state_manager, "schema") and state_manager.schema) else []
            
            # 1. JSON Lookup
            json_answer = _search_json_knowledge(user_text, nodes)
            json_matched = bool(json_answer)
            
            answer = ""
            answer_source = "Unknown"
            retrieved_headings = []
            retrieved_chunks = []
            retrieved_scores = []
            llm_called = False
            context_len = 0
            
            if json_matched:
                answer = json_answer
                answer_source = "JSON"
            else:
                # 2. Semantic retrieval on summary
                if summary_markdown:
                    retrieved_headings, retrieved_chunks, retrieved_scores = _retrieve_semantic_chunks_tfidf(
                        user_text, summary_markdown
                    )
                
                if retrieved_chunks:
                    llm_called = True
                    answer = await _generate_answer_from_chunks(user_text, retrieved_chunks, language)
                    answer_source = "Summary"
                    context_len = sum(len(c) for c in retrieved_chunks)
                else:
                    # 3. LLM fallback
                    llm_called = True
                    answer = await _generate_llm_fallback_answer(user_text, summary_markdown or "", language)
                    answer_source = "LLM"
                    context_len = len(summary_markdown) if summary_markdown else 0

            # Get resume bridge
            resume_question, resume_bridge = _get_resume_bridge(
                current_node,
                state_manager.conversation_data if hasattr(state_manager, "conversation_data") else {},
                language,
            )
            
            finalized_response = answer
            if resume_bridge:
                finalized_response = f"{answer} {resume_bridge}"

            # Detailed runtime logs matching the user checklist exactly
            logger.info(
                "[INTERRUPTION LAYER]\n"
                f"  - Incoming User Query: \"{user_text}\"\n"
                f"  - Intent Classification: {classification}\n"
                f"  - Current Conversation Node: {current_node_id}\n"
                f"  - JSON Search: Checked {len(nodes)} nodes\n"
                f"  - JSON Match Found?: {json_matched}\n"
                f"  - Company Summary Loaded: {bool(summary_markdown)}\n"
                f"  - Semantic Retrieval Started: True\n"
                f"  - Retrieved Chunks: {retrieved_headings}\n"
                f"  - Similarity Scores: {retrieved_scores}\n"
                f"  - LLM Invoked?: {llm_called}\n"
                f"  - LLM Context Size: {context_len} chars\n"
                f"  - Final Answer Source: {answer_source}\n"
                f"  - Resume Previous Node: \"{resume_question}\""
            )
            
            # Record response for anti-repetition
            if hasattr(state_manager, "record_response"):
                state_manager.record_response(finalized_response)
                
            # Return answer + resume bridge, is_terminal=False
            return finalized_response, False

    from langchain_core.messages import HumanMessage, AIMessage
    #from intelligence.pipeline import langgraph_engine
    #from llm.state_manager import process_intent_and_slots

    # 1. Reconstruct graph messages from history
    messages = []
    if conversation_history:
        for msg in conversation_history:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            else:
                messages.append(AIMessage(content=msg["content"]))

    slots = {
        "intent_value": None,
        "budget_range": None,
        "preferred_bhk": None,
        "location": None,
        "timeline_weeks": None,
        "contact_validated": None
    }

    if state_manager and hasattr(state_manager, "conversation_data"):
        data = state_manager.conversation_data

        slots["budget_range"] = data.get("budget")
        slots["preferred_bhk"] = data.get("property_type")
        slots["intent_value"] = data.get("intent")
        slots["location"] = data.get("location")
        slots["timeline_weeks"] = data.get("timeline")
        slots["contact_validated"] = data.get("contact_validated")


    # ---------------------------------------------------------
    # NEVER overwrite an already-known slot with None
    # ---------------------------------------------------------
    def keep_existing(new_value, old_value):
        return new_value if new_value not in (None, "", "null") else old_value

    # 3. Create active Graph state
    graph_state = {
        "messages": messages,
        "extracted_slots": slots,
        "current_node": getattr(state_manager, "current_node_id", "GREETING") if state_manager else "GREETING",
        "pending_filler_action": None,
        "rag_context": None,
        "retry_count": 0,
        "user_input": user_text,
        "language": language,   # ← pass active session language to all node handlers
    }

    # 4. Handle start greeting vs subsequent turns
    # Both "__CONNECTED__" and CALL_CONNECTED_TRIGGER are system startup signals —
    # skip intent extraction (saves 1-2s) and go straight to GREETING node.
    _SYSTEM_TRIGGERS = {"__CONNECTED__", "[System:"}
    is_system_trigger = user_text == "__CONNECTED__" or user_text.startswith("[System:")
    if is_system_trigger:
        graph_state["current_node"] = "GREETING"
        graph_state["user_input"] = ""
    else:
        # Check single-call fast path eligibility
        is_first_turn = not bool(conversation_history and any(m.get("role") == "user" for m in conversation_history))
        last_confidence = getattr(state_manager, "last_intent_confidence", 1.0) if state_manager else 1.0
        word_count = len(user_text.split())
        
        if cfg.ENABLE_SINGLE_CALL_FAST_PATH and not is_first_turn and word_count <= 15 and last_confidence > 0.8:
            logger.info("Taking single-call fast path for LLM response.")
            # We pass the global prompt as context
            global_prompt = ""
            if state_manager:
                global_prompt = getattr(state_manager, "global_prompt", "") or (state_manager.schema.get("global_prompt", "") if hasattr(state_manager, "schema") else "")
                
            """ combined = await generate_combined_intent_and_response(user_text, conversation_history or [], global_prompt) """
            
            # Sync back state
            es = combined.intent_analysis.entities
            if state_manager and hasattr(state_manager, "conversation_data"):
                state_manager.current_node_id = combined.intent_analysis.intent
                state_manager.conversation_data["budget"] = es.budget_range
                state_manager.conversation_data["property_type"] = es.preferred_bhk
                state_manager.conversation_data["intent"] = es.intent_value
                state_manager.conversation_data["location"] = es.location
                state_manager.conversation_data["timeline"] = es.timeline_weeks
                state_manager.last_intent_confidence = combined.intent_analysis.confidence_score
            
            # Record response
            if hasattr(state_manager, "record_response"):
                state_manager.record_response(combined.spoken_reply_text)
                
            return combined.spoken_reply_text, (combined.intent_analysis.intent == "CLOSING")

        graph_state["messages"].append(HumanMessage(content=user_text))
        # Update slots and resolve transitions
        #graph_state = await process_intent_and_slots(graph_state)
        if state_manager:
            state_manager.last_intent_confidence = graph_state.get("last_intent_confidence", 1.0)

    # 5. Invoke LangGraph
    #graph_state = await langgraph_engine.ainvoke(graph_state)
    
    # If it is a live search, execute the crawl sequentially in this single turn
    if graph_state.get("pending_filler_action"):
        graph_state["pending_filler_action"] = None
        graph_state = await langgraph_engine.ainvoke(graph_state)

    # 6. Extract final response
    reply = "Let me look into this."
    if graph_state["messages"]:
        last_msg = graph_state["messages"][-1]
        if isinstance(last_msg, AIMessage):
            reply = last_msg.content

    # 7. Sync slots back to legacy state manager
    if state_manager and hasattr(state_manager, "conversation_data"):
        state_manager.current_node_id = graph_state["current_node"]
        es = graph_state["extracted_slots"]
        state_manager.conversation_data["budget"] = es.get("budget_range")
        state_manager.conversation_data["property_type"] = es.get("preferred_bhk")
        state_manager.conversation_data["intent"] = es.get("intent_value")
        state_manager.conversation_data["location"] = es.get("location")
        state_manager.conversation_data["timeline"] = es.get("timeline_weeks")

    is_terminal = (graph_state["current_node"] == "CLOSING")
    return reply, is_terminal

# ── RAG HINTS, PATTERNS, CONSTANTS ────────────────────────────────────────────
_BUDGET_PATTERN = re.compile(
    r"\b(?:budget|price|range|around|approx|approximately|mera budget|budget hai|budget is)?\s*"
    r"(\d+(?:\.\d+)?)\s*(crore|crores|cr|lakh|lakhs|lac|lacs|thousand|k|करोड़|करोड|लाख|लख|हज़ार|हजार)\b",
    re.IGNORECASE,
)
_BUY_HINTS = (
    "buy", "buying", "looking to buy", "want to buy", "purchase", "purchasing",
    "own house", "own home", "buy property", "looking for a property",
    "looking to purchase", "planning to buy", "interested in buying",
    "searching for property", "khud ke liye", "apne liye", "rehne ke liye",
    "ghar ke liye", "for myself", "for self", "self use", "personal use",
    "to live", "move in", "own use", "own flat",
    "buy karna hai", "property buy karni hai", "ghar lena hai", "खरीदना",
    "खरीदनी है", "घर लेना है", "फ्लैट लेना है"
)
_INVEST_HINTS = (
    "invest", "investment", "investing", "property investment",
    "investment purpose", "investor", "investment ke liye", "nivesh ke liye",
    "return ke liye", "invest karna", "for investment", "roi", "rental income",
    "invest karna hai", "निवेश", "इन्वेस्टमेंट"
)
_RENT_HINTS = (
    "rent", "renting", "lease", "looking for rental", "looking to rent",
    "need a rental property", "rent a flat", "rent pe", "kiraye pe", "kiraya",
    "on rent", "rent ke liye", "for rent", "to rent", "किराए पर", "किराए के लिए",
    "rent par"
)
_LOCATION_SUGGESTION_HINTS = (
    "suggest city", "suggest cities", "suggest me city", "suggest me cities",
    "suggest area", "suggest areas", "recommend city", "recommend cities",
    "recommend area", "recommend areas", "which city", "which area",
    "best city", "best cities", "best area", "best location", "good location",
    "any options", "available options",
)
_PURPOSE_QUESTION_HINTS = (
    "what is it", "what is this", "what's it", "whats it",
    "what is this about", "what's this about", "whats this about",
    "what are you talking about", "why are you calling", "why did you call",
    "purpose of call", "reason for call", "kya hai", "kis baare",
)
_CONFIRMATION_TEXTS = {
    "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "go ahead",
    "tell me", "go on", "continue", "haan", "han", "ji", "theek hai",
}
_DENIAL_TEXTS = {"no", "nope", "nah", "nahi", "nai", "na", "nako"}
_BUSY_HINTS = (
    "busy", "call later", "call me later", "not now", "in a meeting",
    "cant talk", "can't talk", "driving", "not a good time",
)
_NOT_INTERESTED_HINTS = (
    "not interested", "not looking", "no requirement", "dont need", "don't need",
)
_WRONG_PERSON_HINTS = ("wrong number", "wrong person", "not prashant", "this is not")

def _normalize_budget_unit(unit: str) -> str:
    unit = unit.lower()
    if unit in {"crores", "cr", "करोड़", "करोड"}:
        return "crore"
    if unit in {"lakhs", "lac", "lacs", "लाख", "लख"}:
        return "lakh"
    if unit in {"k", "हज़ार", "हजार"}:
        return "thousand"
    return unit

def _extract_budget_entity(user_text: str) -> str | None:
    match = _BUDGET_PATTERN.search(user_text or "")
    if not match:
        return None
    number = match.group(1)
    unit = _normalize_budget_unit(match.group(2))
    return f"{number} {unit}"

def _classify_local_intent(user_text: str) -> dict[str, Any] | None:
    clean_text = re.sub(r"[^\w\s'?]", " ", (user_text or "").strip().lower())
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    if not clean_text:
        return None

    entities: dict[str, Any] = {
        "location": None,
        "budget": None,
        "property_type": None,
        "intent_value": None,
        "timeline": None,
        "confirmation": None,
    }

    if any(phrase in clean_text for phrase in _PURPOSE_QUESTION_HINTS):
        return {"intent": "user_question", "entities": entities}
    has_location_suggestion = any(phrase in clean_text for phrase in _LOCATION_SUGGESTION_HINTS)
    has_location_suggestion = has_location_suggestion or (
        ("suggest" in clean_text or "recommend" in clean_text)
        and any(word in clean_text.split() for word in {"city", "cities", "area", "areas", "location", "locations"})
    )
    if has_location_suggestion:
        return {"intent": "ask_location_suggestion", "entities": entities}

    # Check for direct city/location matches (local preprocessing bypass)
    _LOCAL_LOCATIONS_MAP = {
        "Jaipur": ["jaipur", "जयपुर", "जयपूर", "उजए पूर", "उजएपुर", "उजयपूर", "उदयपुर", "udaypur", "udaipur", "ujae pur", "ujaepur", "ujae poor", "ujaepoor"],
        "Jodhpur": ["jodhpur", "जोधपुर", "jodhpur mein", "jodhpur me"],
        "Madurai": ["madurai", "मदुरै", "madurai mein", "madurai me"],
        "Gurgaon": ["gurgaon", "gurugram", "गुड़गांव", "गुड़गांव", "गुरुग्राम", "गुड़गाँव", "gurgao", "gurgoan"],
        "Zirakpur": ["zirakpur", "जीरकpur", "झिरकपुर", "zirak", "jirakpur", "zirakhpur", "zirak pur"],
        "Mathura": ["mathura", "मथुरा", "वृंदावन", "वृन्दावन", "vrindavan", "vrindaban", "vindravan"]
    }
    matching_text = re.sub(r'[.?।!,;]', '', user_text.lower()).strip()
    matching_words = matching_text.split()
    matched_loc = None
    for canonical_loc, variants in _LOCAL_LOCATIONS_MAP.items():
        for variant in variants:
            if variant in matching_text or any(variant == w for w in matching_words):
                matched_loc = canonical_loc
                break
        if matched_loc:
            break
            
    if matched_loc:
        entities["location"] = matched_loc
        budget = _extract_budget_entity(user_text)
        if budget:
            entities["budget"] = budget
        return {"intent": "provide_location", "entities": entities}

    if any(phrase in clean_text for phrase in _WRONG_PERSON_HINTS):
        return {"intent": "deny_identity", "entities": entities}
    if any(phrase in clean_text for phrase in _NOT_INTERESTED_HINTS):
        return {"intent": "deny_interest", "entities": entities}
    if any(phrase in clean_text for phrase in _BUSY_HINTS):
        return {"intent": "deny_time", "entities": entities}

    if clean_text in _CONFIRMATION_TEXTS:
        entities["confirmation"] = "yes"
        return {"intent": "confirm", "entities": entities}
    if clean_text in _DENIAL_TEXTS:
        entities["confirmation"] = "no"
        return {"intent": "deny", "entities": entities}

    return None

def _enrich_intent_entities(user_text: str, intent: str, entities: dict[str, Any], state_manager: Optional[Any] = None) -> tuple[str, dict[str, Any]]:
    clean_text = (user_text or "").strip().lower()
    entities = dict(entities)

    has_budget_field = "budget" in entities or (state_manager and hasattr(state_manager, "extraction_fields") and "budget" in state_manager.extraction_fields)
    if has_budget_field and not entities.get("budget"):
        budget = _extract_budget_entity(user_text)
        if budget:
            entities["budget"] = budget
            if intent in {"unclear", "provide_info"}:
                intent = "provide_budget"

    # Deterministic location fallback
    has_location_field = "location" in entities or (state_manager and hasattr(state_manager, "extraction_fields") and "location" in state_manager.extraction_fields)
    if has_location_field and not entities.get("location"):
        _COMMON_LOCATIONS_MAP = {
            "Mumbai": ["mumbai", "मंबई", "bombay", "mumbai mein"],
            "Pune": ["pune", "पुणे"],
            "Chennai": ["chennai", "चेन्नई"],
            "Delhi": ["delhi", "new delhi", "दिल्ली"],
            "Noida": ["noida", "नोएडा"],
            "Gurgaon": ["gurgaon", "gurugram", "गुड़गांव", "गुरुग्राम", "gurgaon mein", "gurgaon me"],
            "Jaipur": ["jaipur", "जयपुर", "जयपूर", "उजए पूर", "उजएपुर", "उदयपुर", "udaypur", "jaipur mein", "jaipur me"],
            "Jodhpur": ["jodhpur", "जोधपुर", "jodhpur mein", "jodhpur me"],
            "Madurai": ["madurai", "मदुरै", "madurai mein", "madurai me"],
            "Zirakpur": ["zirakpur", "जीरकपुर", "झिरकपुर", "zirakpur mein", "zirakpur me"],
            "Mathura": ["mathura", "मथुरा", "वृंदावन", "vrindavan", "mathura mein", "mathura me", "vrindavan mein", "vrindavan me"],
            "Hyderabad": ["hyderabad", "हैदराबाद"],
            "Kolkata": ["kolkata", "calcutta", "कोलकाता"],
            "Ahmedabad": ["ahmedabad", "अहमदाबाद"],
        }
        for canonical_loc, variants in _COMMON_LOCATIONS_MAP.items():
            for variant in variants:
                if variant in clean_text:
                    entities["location"] = canonical_loc
                    if intent in {"unclear", "provide_info"}:
                        intent = "provide_location"
                    break
            if entities.get("location"):
                break

    has_intent_field = "intent_value" in entities or (state_manager and hasattr(state_manager, "extraction_fields") and "intent_value" in state_manager.extraction_fields)
    if has_intent_field:
        has_buy = any(phrase in clean_text for phrase in _BUY_HINTS) or "buy" in clean_text.split()
        has_invest = any(phrase in clean_text for phrase in _INVEST_HINTS)
        has_rent = any(phrase in clean_text for phrase in _RENT_HINTS) or "rent" in clean_text.split()

        if has_buy:
            entities["intent_value"] = "buy"
            if intent not in {"confirm", "deny", "deny_interest", "deny_time", "deny_identity", "deny_visit_time"}:
                intent = "provide_intent"
        elif has_invest:
            entities["intent_value"] = "invest"
            if intent not in {"confirm", "deny", "deny_interest", "deny_time", "deny_identity", "deny_visit_time"}:
                intent = "provide_intent"
        elif has_rent:
            entities["intent_value"] = "rent"
            if intent not in {"confirm", "deny", "deny_interest", "deny_time", "deny_identity", "deny_visit_time"}:
                intent = "provide_intent"

        if entities.get("intent_value") in {"buy", "rent", "invest"} and intent not in {"confirm", "deny", "deny_interest", "deny_time", "deny_identity", "deny_visit_time"}:
            intent = "provide_intent"

    # Promote unclear/provide_info intent to specific slot-filling intent if entities are populated
    if intent in {"unclear", "provide_info"}:
        if entities.get("location"):
            intent = "provide_location"
        elif entities.get("budget"):
            intent = "provide_budget"
        elif entities.get("intent_value"):
            intent = "provide_intent"

    return intent, entities

def _extract_summary_from_prompt(global_prompt: str) -> str:
    """Extract complete company knowledge summary from global prompt template."""
    match = re.search(r"Complete Company Knowledge Summary:\n(.*)", global_prompt, re.DOTALL)
    if match:
        return match.group(1).strip()
    return global_prompt

""" async def _classify_message(
    user_text: str,
    current_node: Optional[dict[str, Any]],
    language: str,
) -> str:
    Classify user text as node_response, company_question, general_query, small_talk, or unknown.
    node_info = ""
    if current_node:
        node_response = current_node.get("response") or ""
        node_instruction = (current_node.get("instruction") or {}).get("text") or ""
        node_info = (
            f"Active Node Name: {current_node.get('name')}\n"
            f"Active Node Question/Prompt: {node_response}\n"
            f"Active Node Goal: {node_instruction}\n"
            f"Expected Slots to fill: {current_node.get('collects', [])}"
        )
    
    system_prompt = (
        "You are an expert intent classifier for a voice calling assistant.\n"
        "Analyze the user's message and the active node's context, and classify the user's intent into exactly one of these categories:\n\n"
        "1. \"node_response\": The user is answering the active node's question or providing details (like location, budget, name, availability, or preferences) related to the current sales/advisory flow.\n"
        "2. \"company_question\": The user is asking a factual question about the company itself, its products, services, CEO, founders, office location, contact info, support/helpdesk, pricing details, or general capabilities.\n"
        "3. \"general_query\": A standard conversational greeting, acknowledgment, or simple polite phrase (e.g. 'hello', 'ok', 'yes', 'no problem', 'thanks').\n"
        "4. \"small_talk\": Conversational chitchat unrelated to the company or current flow (e.g., 'how are you', 'what is your name', 'are you a robot').\n"
        "5. \"unknown\": The user's input is garbled, unclear, or does not fit any of the above categories.\n\n"
        "Format your response as a valid JSON object matching this schema:\n"
        "{\n"
        "  \"classification\": \"node_response\" | \"company_question\" | \"general_query\" | \"small_talk\" | \"unknown\",\n"
        "  \"reason\": \"A brief explanation of why this classification was chosen.\"\n"
        "}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            f"Active Node Context:\n{node_info}\n\n"
            f"User Message: \"{user_text}\"\n"
            f"Language: {language}"
        )}
    ]

    try:
        completion = await _client.chat.completions.create(
            model=cfg.MODEL_NAME,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=100
        )
        raw_res = completion.choices[0].message.content or ""
        res_data = json.loads(raw_res)
        val = str(res_data.get("classification") or "node_response").strip().lower()
        if val in {"node_response", "company_question", "general_query", "small_talk", "unknown"}:
            return val
        return "node_response"
    except Exception as exc:
        logger.warning("Failed to classify user message: %s", exc)
        return "node_response" """


async def _classify_message(
    user_text: str,
    current_node: Optional[dict[str, Any]],
    language: str,
) -> str:
    """Classify user text as node_response, company_question, general_query, small_talk, or unknown."""

    node_info = ""

    if current_node:
        node_response = current_node.get("response") or ""
        node_instruction = (
            (current_node.get("instruction") or {}).get("text") or ""
        )

        node_info = (
            f"Active Node Name: {current_node.get('name')}\n"
            f"Active Node Question/Prompt: {node_response}\n"
            f"Active Node Goal: {node_instruction}\n"
            f"Expected Slots to fill: {current_node.get('collects', [])}"
        )

    system_prompt = (
        "You are an expert intent classifier for a voice calling assistant.\n"
        "Analyze the user's message and the active node's context, and classify "
        "the user's intent into exactly one of these categories:\n\n"

        "1. node_response: The user is answering the active node's question "
        "or providing details such as location, budget, name, availability, "
        "or preferences related to the current sales/advisory flow.\n"

        "2. company_question: The user is asking a factual question about "
        "the company itself, its products, services, CEO, founders, office "
        "location, contact information, support/helpdesk, pricing details, "
        "or general capabilities.\n"

        "3. general_query: A standard conversational greeting, acknowledgment, "
        "or simple polite phrase such as hello, ok, yes, no problem, thanks.\n"

        "4. small_talk: Conversational chitchat unrelated to the company or "
        "current flow, such as how are you, what is your name, are you a robot.\n"

        "5. unknown: The user's input is garbled, unclear, or does not fit "
        "any of the above categories.\n\n"

        "Return ONLY one valid JSON object using exactly this structure:\n"
        "{\n"
        '  "classification": "node_response",\n'
        '  "reason": "brief reason"\n'
        "}\n\n"

        "Do not use markdown.\n"
        "Do not wrap the JSON in ```.\n"
        "Do not add any text before or after the JSON."
    )

    messages = [
        {
            "role": "system",
            "content": system_prompt
        },
        {
            "role": "user",
            "content": (
                f"Active Node Context:\n{node_info}\n\n"
                f'User Message: "{user_text}"\n'
                f"Language: {language}\n\n"
                "Return only the JSON object."
            )
        }
    ]

    try:
        # IMPORTANT:
        # Do NOT use response_format={"type": "json_object"} here.
        # The current Groq model can hit a 400 error when the JSON response
        # reaches the completion-token limit.
        completion = await _client.chat.completions.create(
            model=cfg.MODEL_NAME,
            messages=messages,
            temperature=0.0,
            max_tokens=256,
        )

        raw_res = completion.choices[0].message.content or ""

        logger.info(
            "[CLASSIFIER] Raw model output: %r",
            raw_res
        )

        cleaned = raw_res.strip()

        # Remove markdown code fences if the model still returns them.
        if cleaned.startswith("```"):
            cleaned = re.sub(
                r"^```(?:json)?\s*",
                "",
                cleaned,
                flags=re.IGNORECASE
            )

            cleaned = re.sub(
                r"\s*```$",
                "",
                cleaned
            )

        # Extract the JSON object safely even if the model adds
        # a small amount of extra text.
        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start == -1 or end == -1 or end <= start:
            raise ValueError(
                f"No valid JSON object found in classifier response: {raw_res!r}"
            )

        json_text = cleaned[start:end + 1]

        res_data = json.loads(json_text)

        classification = str(
            res_data.get("classification") or "node_response"
        ).strip().lower()

        valid_classifications = {
            "node_response",
            "company_question",
            "general_query",
            "small_talk",
            "unknown",
        }

        if classification in valid_classifications:
            logger.info(
                "[CLASSIFIER] classification=%s",
                classification
            )
            return classification

        logger.warning(
            "[CLASSIFIER] Invalid classification returned: %r. "
            "Falling back to node_response.",
            classification
        )

        return "node_response"

    except Exception as exc:
        logger.warning(
            "[CLASSIFIER] Failed to classify user message: %s",
            exc
        )

        # Safe fallback: treat the user's message as a response
        # to the currently active node.
        return "node_response"


def _pure_python_tfidf_similarity(query: str, docs: list[str]) -> list[float]:
    """Calculate TF-IDF Cosine Similarity in pure python. No sklearn needed!"""
    def tokenize(text: str) -> list[str]:
        return re.findall(r'\b\w+\b', text.lower())

    query_tokens = tokenize(query)
    doc_tokens_list = [tokenize(d) for d in docs]
    
    all_terms = set(query_tokens)
    for doc_tokens in doc_tokens_list:
        all_terms.update(doc_tokens)
        
    df = {}
    N = len(docs)
    for term in all_terms:
        df[term] = sum(1 for doc_tokens in doc_tokens_list if term in doc_tokens)
        
    idf = {}
    for term, doc_count in df.items():
        idf[term] = math.log((1 + N) / (1 + doc_count)) + 1.0

    doc_vectors = []
    for doc_tokens in doc_tokens_list:
        tf = Counter(doc_tokens)
        vec = {}
        for term in all_terms:
            if term in tf:
                vec[term] = tf[term] * idf[term]
        doc_vectors.append(vec)
        
    q_tf = Counter(query_tokens)
    q_vec = {}
    for term in all_terms:
        if term in q_tf:
            q_vec[term] = q_tf[term] * idf[term]
            
    def magnitude(vec: dict[str, float]) -> float:
        return math.sqrt(sum(v*v for v in vec.values()))
        
    q_mag = magnitude(q_vec)
    if q_mag == 0:
        return [0.0] * N
        
    similarities = []
    for d_vec in doc_vectors:
        d_mag = magnitude(d_vec)
        if d_mag == 0:
            similarities.append(0.0)
            continue
        dot_product = sum(q_vec.get(term, 0.0) * d_vec.get(term, 0.0) for term in q_vec)
        similarities.append(dot_product / (q_mag * d_mag))
        
    return similarities

def _retrieve_semantic_chunks_tfidf(query: str, summary_markdown: str) -> tuple[list[str], list[str], list[float]]:
    """Parse Markdown by H2 headers and use scikit-learn or pure python TF-IDF Cosine Similarity to retrieve chunks."""
    if not summary_markdown or not summary_markdown.strip():
        return [], [], []

    sections = re.split(r'\n(?=## )', "\n" + summary_markdown.strip())
    sections = [s.strip() for s in sections if s.strip()]
    if not sections:
        return [], [], []

    SYNONYMS = {
        "support": ["customer care", "helpdesk", "contact", "support", "complaint", "phone", "email", "address", "office", "location", "reach", "hours"],
        "contact": ["phone", "email", "address", "office", "location", "reach", "contact", "connect", "call", "map", "hours"],
        "ceo": ["ceo", "founder", "leadership", "owner", "president", "chief executive", "management", "head", "team"],
        "founder": ["ceo", "founder", "leadership", "owner", "president", "chief executive", "management", "head", "team"],
        "product": ["product", "project", "apartment", "flat", "villa", "plot", "pricing", "price", "cost", "offer", "luxury", "amenities", "rera"],
        "pricing": ["price", "cost", "pricing", "rate", "fee", "payment", "subscription", "offer", "discount"],
        "feature": ["feature", "amenity", "amenities", "specifications", "benefits", "technologies"],
        "security": ["security", "compliance", "privacy", "gdpr", "safe", "data protection"],
        "careers": ["careers", "job", "hiring", "culture", "employee", "work"],
        "headquarters": ["headquarters", "hq", "corporate office", "office", "location", "address", "contact"],
        "unique": ["unique", "differentiator", "why choose", "mission", "vision", "values", "core values", "advantages"],
    }

    query_words = re.findall(r'\b\w+\b', query.lower())
    expanded_words = set(query_words)
    for word in query_words:
        if word in SYNONYMS:
            expanded_words.update(SYNONYMS[word])
    expanded_query = " ".join(expanded_words)

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        
        vectorizer = TfidfVectorizer(stop_words='english')
        tfidf_matrix = vectorizer.fit_transform(sections)
        query_vec = vectorizer.transform([expanded_query])
        
        similarities = cosine_similarity(query_vec, tfidf_matrix).flatten()
    except Exception as exc:
        similarities = _pure_python_tfidf_similarity(expanded_query, sections)

    scored_sections = []
    for idx, score in enumerate(similarities):
        if score > 0.05:
            sec = sections[idx]
            lines = sec.split("\n")
            heading = lines[0].replace("#", "").strip() if lines else "Section"
            scored_sections.append((float(score), heading, sec))

    scored_sections.sort(key=lambda x: x[0], reverse=True)
    top_sections = scored_sections[:3]

    headings = [heading for score, heading, sec in top_sections]
    chunks = [sec for score, heading, sec in top_sections]
    scores = [score for score, heading, sec in top_sections]

    if not chunks and sections:
        for idx, sec in enumerate(sections):
            lines = sec.split("\n")
            heading = lines[0].replace("#", "").strip() if lines else "Section"
            if "overview" in heading.lower() or "about" in heading.lower():
                return [heading], [sec], [1.0]
        first_lines = sections[0].split("\n")
        first_heading = first_lines[0].replace("#", "").strip() if first_lines else "Overview"
        return [first_heading], [sections[0]], [1.0]

    return headings, chunks, scores

def _search_json_knowledge(user_text: str, nodes: list[dict[str, Any]]) -> Optional[str]:
    """Check if the user's query can be answered directly by the predefined JSON nodes/phrases."""
    query_clean = user_text.lower().strip().rstrip("?").strip()
    for node in nodes:
        node_name = (node.get("name") or "").lower()
        node_resp = (node.get("response") or "").lower()
        if "ceo" in query_clean and "ceo" in node_name:
            if node.get("response"):
                return node.get("response")
        if "headquarters" in query_clean or "hq" in query_clean or "office" in query_clean:
            if "office" in node_name or "headquarter" in node_name:
                if node.get("response"):
                    return node.get("response")
        if "support" in query_clean or "contact" in query_clean or "help" in query_clean:
            if "support" in node_name or "contact" in node_name:
                if node.get("response"):
                    return node.get("response")
    for node in nodes:
        node_name = (node.get("name") or "").lower()
        if node_name and node_name in query_clean:
            if node.get("response"):
                return node.get("response")
    return None

async def _generate_answer_from_chunks(user_text: str, retrieved_chunks: list[str], language: str) -> str:
    """Generate dynamic response based on retrieved semantic chunks."""
    context = "\n\n".join(retrieved_chunks)
    system_prompt = (
        "You are a factual customer advisory voice assistant.\n"
        "Answer the user's question using only the verified facts from the company summary below.\n\n"
        "Verified Company Facts:\n"
        f"{context}\n\n"
        "Constraints:\n"
        "- Do not assume, guess, or hallucinate any details. If not found, say exactly: "
        "'I don't have that detail right now, but I can check and have our advisor get back to you.'\n"
        "- Max 2 sentences, 20-30 words.\n"
        f"- Respond in the requested active language: {language}."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text}
    ]
    try:
        completion = await _client.chat.completions.create(
            model=cfg.MODEL_NAME,
            messages=messages,
            temperature=0.2,
            max_tokens=200
        )
        return (completion.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.error("Failed generating LLM answer from chunks: %s", exc)
        return "I don't have that detail right now, but I can check and have our advisor get back to you."

async def _generate_llm_fallback_answer(user_text: str, summary_markdown: str, language: str) -> str:
    """Fallback LLM call when no specific chunks are retrieved, using the whole summary context."""
    system_prompt = (
        "You are a factual customer advisory voice assistant.\n"
        "Answer the user's question using the company summary context below.\n\n"
        "Company Summary Context:\n"
        f"{summary_markdown}\n\n"
        "Constraints:\n"
        "- Do not assume, guess, or hallucinate any details. If not found, say exactly: "
        "'I don't have that detail right now, but I can check and have our advisor get back to you.'\n"
        "- Max 2 sentences, 20-30 words.\n"
        f"- Respond in the requested active language: {language}."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text}
    ]
    try:
        completion = await _client.chat.completions.create(
            model=cfg.MODEL_NAME,
            messages=messages,
            temperature=0.2,
            max_tokens=80
        )
        return (completion.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.error("Failed generating LLM fallback answer: %s", exc)
        return "I don't have that detail right now, but I can check and have our advisor get back to you."

def _get_resume_bridge(
    current_node: dict[str, Any],
    context: dict[str, Any],
    language: str,
) -> tuple[str, str]:
    """Returns (resume_question, combined_bridge_text) to return to previous flow node."""
    from .llm_response_generator import _resolve_template_response
    
    resume_question = _resolve_template_response(current_node, context, language)
    if not resume_question:
        return "", ""

    resume_question_clean = resume_question.strip().rstrip("?").rstrip()
    if not resume_question_clean:
        return "", ""

    if language in ("hi", "hinglish"):
        prefix = "Toh, hamari baat-cheet par waapas aate hain, "
    elif language == "mr":
        prefix = "तर, आपल्या संभाषणाकडे परत येत, "
    else:
        prefix = "Now, coming back to our discussion, "

    combined = f"{prefix}{resume_question_clean}?"
    return resume_question_clean, combined

generate_informational_response = _generate_answer_from_chunks
generate_phrase_constrained_response = _generate_answer_from_chunks

async def _async_call_groq_api(
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
    response_format: Optional[dict[str, str]] = None,
) -> str:
    """Call Groq with retry + exponential backoff. Returns raw content string.

    Issue 13 fix: backoff uses asyncio.sleep instead of time.sleep to avoid blocking worker threads.
    """

    def _build_kwargs(model: str) -> dict:
        kwargs = dict(
            model=model,
            messages=messages,
            temperature=temperature,
            top_p=cfg.TOP_P,
            response_format=response_format,
        )
        if "gpt-oss" in model or "reasoning" in model:
            # Reasoning models need headroom + explicit low effort,
            # otherwise `content` is empty.
            kwargs["max_completion_tokens"] = max(max_tokens, 512)
            kwargs["reasoning_effort"] = "low"
            kwargs["include_reasoning"] = False
        else:
            kwargs["max_tokens"] = max_tokens
        return kwargs

    # FIX B1: route spoken-response generation to a non-reasoning model when
    # possible (much faster). FIX N: if that model 404s as "model not found"
    # (not provisioned on this Groq account/plan), fall back to cfg.MODEL_NAME
    # — the same model analyze_user_intent() already uses successfully —
    # instead of burning every retry attempt on a dead model and silently
    # dropping into the raw English template every single turn.
    _primary_model = getattr(cfg, "RESPONSE_MODEL_NAME", cfg.MODEL_NAME)
    _fallback_model = cfg.MODEL_NAME

    for attempt in range(1, cfg.MAX_RETRIES + 1):
        _model = _primary_model
        try:
            t0 = time.time()
            completion = await _client.chat.completions.create(**_build_kwargs(_model))
            latency = time.time() - t0
            logger.info("LLM request completed in %.3fs (attempt %d/%d, model=%s)", latency, attempt, cfg.MAX_RETRIES, _model)
            return completion.choices[0].message.content or ""
        except RateLimitError:
            wait = (2 ** attempt) + _random.uniform(0, 0.5)   # jitter
            logger.warning("Groq rate limit hit (attempt %d/%d) — retrying in %.1fs", attempt, cfg.MAX_RETRIES, wait)
            if attempt < cfg.MAX_RETRIES:
                await asyncio.sleep(wait)
        except APITimeoutError:
            logger.error("Groq request timed out (attempt %d/%d)", attempt, cfg.MAX_RETRIES)
            if attempt == cfg.MAX_RETRIES:
                return ""
            await asyncio.sleep(1.0)
        except APIError as exc:
            _msg = str(exc).lower()
            _is_model_missing = (
                "model_not_found" in _msg
                or "does not exist" in _msg
                or "do not have access" in _msg
            )
            logger.error("Groq API error (attempt %d/%d, model=%s): %s", attempt, cfg.MAX_RETRIES, _model, exc)

            if _is_model_missing and _primary_model != _fallback_model:
                logger.warning(
                    "[MODEL FALLBACK] '%s' unavailable on this Groq account — "
                    "switching to '%s' for the rest of this call.",
                    _primary_model, _fallback_model,
                )
                _primary_model = _fallback_model
                # Retry immediately with the working model, same attempt budget.
                try:
                    t0 = time.time()
                    completion = await _client.chat.completions.create(**_build_kwargs(_primary_model))
                    latency = time.time() - t0
                    logger.info("LLM request completed in %.3fs (fallback model=%s)", latency, _primary_model)
                    return completion.choices[0].message.content or ""
                except (RateLimitError, APITimeoutError, APIError) as exc2:
                    logger.error("Groq API error on fallback model (attempt %d/%d): %s", attempt, cfg.MAX_RETRIES, exc2)
                    if attempt == cfg.MAX_RETRIES:
                        return ""
                    continue

            return ""
    return ""