"""Shared language detection and routing helpers for voice responses."""

from __future__ import annotations

import logging
from dataclasses import dataclass
import re

logger = logging.getLogger(__name__)


SUPPORTED_LANGUAGES = {"en", "hi", "mr", "hinglish"}
DEVANAGARI_RANGE = ("\u0900", "\u097F")
HINDI_MARKERS = {
    "hindi", "hindi mein", "bol", "boliye", "bataiye", "samajh", "nahi", "haan", "acha", "theek",
    "kya", "kyun", "kaise", "kab", "kaun", "kahan", "hai", "hain", "tha", "thi", "the",
    "kar", "karo", "kar rahe", "baat", "call", "milenge", "do minute", "investment",
    "ghar", "flat", "property", "zaroorat", "chaiye", "chahiye", "mangta",
    "है", "नहीं", "मुझे", "आप", "क्या", "जी", "चाहिए", "करना", "बोलिए", "हाँ", "अच्छा", "ठीक",
}
MARATHI_MARKERS = {
    "marathi", "marathi madhe", "bol", "bola", "sanga", "sangal", "pahije", "ho", "nahi",
    "kai", "kasa", "kiva", "ani", "pan", "tar", "aahe", "aahet", "hote", "hoti", "hota",
    "karaycha", "kartoy", "boltoy", "boltey", "ghetlay", "pahata", "pahat",
    "mala", "tula", "aamhi", "amhi", "majha", "majhi", "majhe", "tujha", "tujhi", "tujhe",
    "kuthe", "kadhi", "kiti", "baghaychay", "ghyaychay", "vikaychay", "bhadane", 
    "zaga", "jaaga", "guntha", "kimmat", "navin", "juna", "changle", "changla",
    "आहे", "नाही", "माझ", "तुम्ह", "काय", "होय", "पाहिजे", "करू", "बोला", "हो", "सांगा",
    "मला", "तुला", "आम्ही", "माझा", "माझी", "माझे", "तुझा", "तुझी", "तुझे", "कुठे", "कधी", "किती",
    "बघायचंय", "घ्यायचंय", "विकायचंय", "भाड्याने", "जागा", "गुंठा", "किंमत", "नवीन", "जुना", "चांगले", "चांगला",
}
HINGLISH_MARKERS = (
    "aap", "apka", "apki", "haan", "han", "ji", "nahi", "nahin",
    "achha", "acha", "kya", "kaise", "karna", "chahiye", "chahiye",
    "thik", "theek", "boliye", "bataiye", "mera", "meri", "mujhe",
    # FIX I: common verb conjugations and fillers that were missing —
    # a Hinglish turn using only these words scored zero Hindi signal
    # and was misclassified as confident English.
    "chahta", "chahti", "chahte", "hoon", "hu", "hun", "toh",
    "wala", "wali", "wale", "bhai", "yaar", "matlab", "sirf",
    "hain", "chahungi", "chahunga", "chahoongi", "chahoonga",
    "chahenge", "dehkna", "dekhungi", "dekhunga",
    "jiha", "jihaan", "jee",
)
ROMAN_HINDI_MARKERS = (
    "main", "mai", "mera", "meri", "mere", "mujhe", "mje", "mujhko",
    "aap", "ap", "aapka", "aapki", "aapke", "tum", "tumhara", "tumhari",
    "haan", "han", "nahi", "nahin", "nhi", "kya", "kaunsa", "kaunsi",
    "kaise", "kitna", "kitni", "kitne", "chahiye", "dekh", "dekh raha",
    "dekh rahi", "raha", "rahi", "liye", "ke liye", "ke", "hai", "hain", "hoon",
    "khud", "apne liye",
    "rehne", "rehna", "batao", "bolo", "samjha",
    "samajh", "thoda", "bolenge", "pata", "abhi", "baad",
    # FIX I: same missing-conjugation gap as HINGLISH_MARKERS above.
    # These are exclusively Hindi/Hinglish words (never plain English),
    # so adding them does not risk misclassifying English sentences.
    "chahta", "chahti", "chahte", "hu", "hun", "toh",
    "wala", "wali", "wale", "dikhao", "dikhado", "dikhaiye",
    "batado", "bata do", "karo", "kro", "milega", "milegi",
    # FIX K: STT frequently mangles these — accept common variants directly
    # instead of only the "correct" spelling.
    "chahungi", "chahunga", "chahoongi", "chahoonga", "chahenge",
    "dehkna", "dekhungi", "dekhunga", "dehkungi",
    "jiha", "jihaan", "ji haan", "jee", "jee haan",
    # FIX K: budget-turn vocabulary was entirely missing. A sentence like
    # "Pachas, say, 5 lakh rupay." had zero Hindi markers and was scored as
    # confident English (4 latin words -> confidence 0.87), which is exactly
    # why the agent flipped to English right after the budget was given.
    # "lakh"/"crore"/"rupay(e)" are Hindi money units even in Roman script;
    # they are safe here (unlike "budget"/"property") because they never
    # appear in a plain English real-estate sentence.
    "lakh", "lakhs", "laakh", "crore", "crores", "rupay", "rupaye",
    "rupaiya", "paisa", "paise",
    # Hindi number words (spoken budget amounts: "pachas lakh", "ek crore").
    # "do", "teen", "char" deliberately excluded — real English words
    # ("Do you...", "a teen", "char siu") would false-positive on those.
    "ek", "paanch", "panch", "chhe", "che",
    "saat", "aath", "nau", "das", "gyarah", "barah", "terah", "chaudah",
    "pandrah", "solah", "satrah", "atharah", "unnees", "tees",
    "chalis", "pachas", "sattar", "assi", "nabbe", "sau",
    "dedh", "dhai", "sava", "paune",
)

# Words that appear naturally in both English real-estate conversations AND
# Hindi transliteration. They must NOT count toward Hindi hit scores — otherwise
# a single "flat" or "property" from an English speaker will trigger a language switch.
_CROSS_LANGUAGE_WORDS = frozenset({
    "ghar", "flat", "plot", "property", "budget", "investment", "area",
    "location", "rent", "clear", "call", "investment", "milenge",
})
ENGLISH_STYLE_MARKERS = (
    "budget", "investment", "invest", "self use", "location", "property",
    "project", "site visit", "callback", "schedule", "timeline", "area",
    "price", "range", "premium", "options", "rent", "rental", "apartment",
)
LOCALIZED_TEMPLATE_MAP = {
    "Hey, this is Neha — I'm with the Real Estate team.Can I talk to you. ": {
        "hi": "Hi, Neha bol rahi hoon. Kya main aapse baat kar sakti hoon",
        "hinglish": "Hi, Neha bol rahi hoon. Kya main aapse baat kar sakti hoon",
    },
    "Is this a good time to speak?": {
        "hi": "Abhi baat karne ke liye do minute milenge?",
        "hinglish": "Abhi baat karne ke liye 2 minute milenge?",
    },
    "I actually came across your interest in property. Are you exploring for yourself or as an investment?": {
        "hi": "Aap property khud ke liye dekh rahe ho ya investment ke liye?",
        "hinglish": "Aap property khud ke liye dekh rahe ho ya investment ke liye?",
    },
    "Hello, this is Neha from the Real Estate AI team. Am I speaking with you?": {
        "hi": "Hi, Neha bol rahi hoon Real Estate team se. Kya main aapse baat kar rahi hoon?",
        "hinglish": "Hi, Neha bol rahi hoon Real Estate team se. Kya main aapse baat kar rahi hoon?",
    },
    "I'm calling about some premium property options. Would you have a moment?": {
        "hi": "Premium property options ke baare mein call kiya hai. Ek minute mil jayega?",
        "hinglish": "Premium property options ke baare mein call kiya hai. Ek minute milega?",
    },
    "Thank you for your time. Have a great day!": {
        "hi": "Time dene ke liye thanks. Aapka din achha rahe.",
        "hinglish": "Time dene ke liye thanks. Have a great day.",
    },
    "Do you have two minutes right now? I came across something that might actually be relevant for you.": {
        "hi": "Bas do minute milenge? Aapke liye ek relevant option tha.",
        "hinglish": "Bas 2 minute milenge? Aapke liye ek relevant option tha.",
    },
    "Are you currently looking to buy, rent, or invest in a property?": {
        "hi": "Aap abhi property kharidne, rent par lene, ya invest karne ke liye dekh rahe hain?",
        "hinglish": "Aap currently property buy, rent, ya invest karne ke liye dekh rahe hain?",
        "mr": "तुम्ही सध्या प्रॉपर्टी विकत घेण्यासाठी, भाड्याने घेण्यासाठी, किंवा इन्व्हेस्ट करण्यासाठी पाहत आहात का?",
    },
    "Got it — are you mainly looking for something to move into, or more of an investment angle?": {
        "hi": "Samajh gayi. Aap khud rehne ke liye dekh rahe ho ya investment angle se?",
        "hinglish": "Got it. Aap khud ke liye dekh rahe ho ya investment angle se?",
    },
    "Yeah, totally fair — budget matters. We do have some options in Hinjewadi and Mamurdi that are more accessible, with flexible payment plans. Worth a look?": {
        "hi": "Bilkul, budget matter karta hai. Hinjewadi aur Mamurdi mein kuch options hain jahan payment plan bhi flexible hai, dekhna chahoge?",
        "hinglish": "Bilkul, budget matter karta hai. Hinjewadi aur Mamurdi mein kuch options hain with flexible payment plans, dekhna chahoge?",
    },
    "Got it, no worries. Just so I'm not wasting your time — would it be okay if I sent you something on WhatsApp? You can look at it whenever it suits you.": {
        "hi": "Theek hai, koi issue nahi. Main WhatsApp par details bhej doon? Aap jab time mile tab dekh lena.",
        "hinglish": "No worries. Main WhatsApp par details bhej doon? Aap jab convenient ho tab dekh lena.",
    },
    "Which part of the city are you looking at — and roughly what budget should I work with?": {
        "hi": "Aap kis area mein dekh rahe hain — aur budget roughly kitna rahega?",
        "hinglish": "Aap kis area mein dekh rahe ho — aur budget roughly kitna rahega?",
    },
    "Which city or area are you considering?": {
        "hi": "Kaunsa city ya area dekh rahe ho?",
        "hinglish": "Kaunsa city ya area dekh rahe ho?",
    },
    "What budget range should I keep in mind?": {
        "hi": "Budget range roughly kitni rakhun?",
        "hinglish": "Budget range roughly kitni rakhun?",
    },
    "Actually, I have a property in {{location}} that fits your budget well. Would you like to visit it in person?": {
        "hi": "Mere paas {{location}} mein ek property hai jo aapke budget mein fit baithti hai. Kya aap use dekhne jaana chahenge?",
        "hinglish": "Mere paas {{location}} mein ek property hai jo aapke budget mein fit baithti hai. Kya aap visit karna chahenge?",
    },
    "What works better for you — weekend or a weekday? And any time preference?": {
        "hi": "Aapke liye weekend better rahega ya weekday? Aur koi time preference?",
        "hinglish": "Aapke liye weekend better rahega ya weekday? Aur koi time preference?",
    },
    "Done — I'll send you the location and directions on WhatsApp before {{timeline}}. Looking forward to it.": {
        "hi": "Done — main aapko {{timeline}} se pehle WhatsApp par location bhej dungi. Milte hain fir.",
        "hinglish": "Done — main aapko {{timeline}} se pehle WhatsApp par location bhej dungi. See you then.",
    },
    "All set — see you then. Have a good one!": {
        "hi": "Sab set hai — fir milte hain. Have a good one!",
        "hinglish": "Sab set hai — see you then. Have a good one!",
    },
    "No worries. When's a better time to catch you — morning, afternoon, or evening?": {
        "hi": "Koi baat nahi. Callback ke liye morning, afternoon ya evening mein kya better rahega?",
        "hinglish": "No worries. Callback ke liye morning, afternoon ya evening mein kya better rahega?",
    },
    "I'll ping you around {{timeline}} then. Take care!": {
        "hi": "Theek hai, main aapko {{timeline}} ke around call kar lungi. Take care.",
        "hinglish": "Theek hai, main aapko {{timeline}} ke around call kar lungi. Take care.",
    },
    "Alright, take care. Talk soon!": {
        "hi": "Theek hai, take care. Jaldi baat karte hain.",
        "hinglish": "Alright, take care. Jaldi baat karte hain.",
    },
    "Sure, I can help with that. Which area is the property in, and what kind of price are you targeting?": {
        "hi": "Bilkul, usmein help kar sakti hoon. Property kis area mein hai, aur aap kya price expect kar rahe ho?",
        "hinglish": "Sure, usmein help kar sakti hoon. Property kis area mein hai, aur aap kya price target kar rahe ho?",
    },
    "Got it — what type of property is it, roughly how old, and any standout features? I'll have someone from our team reach out with a proper eval.": {
        "hi": "Samajh gayi. Property ka type kya hai, roughly kitni purani hai, aur koi key feature? Hamari team ka koi person proper eval ke saath connect karega.",
        "hinglish": "Got it. Property type kya hai, roughly kitni purani hai, aur koi standout feature? Hamari team proper eval ke saath connect karegi.",
    },
    "No worries, I'll be quick — it's about a property inquiry from earlier. Do you have two minutes, or should I call back later?": {
        "hi": "Koi baat nahi, main jaldi bolti hoon. Ye pehle wali property inquiry ke baare mein hai, do minute hain ya main baad mein call karun?",
        "hinglish": "No worries, main quick rahoongi. Ye earlier property inquiry ke baare mein hai, 2 minute hain ya baad mein call karun?",
    },
    "Thanks for chatting — have a great rest of your day!": {
        "hi": "Baat karne ke liye thanks. Aapka din achha rahe.",
        "hinglish": "Baat karne ke liye thanks. Have a great day ahead.",
    },
    "No problem at all — take care!": {
        "hi": "Bilkul theek hai. Take care.",
        "hinglish": "No problem at all. Take care.",
    },
    "Sorry, I missed that. Just to clarify, are you looking to buy, rent, or invest in a property?": {
        "hi": "माफ़ कीजिये, मैं समझ नहीं पाई। क्या आप प्रॉपर्टी खरीदना, किराए पर लेना, या इन्वेस्ट करना चाहते हैं?",
        "hinglish": "Sorry, main samjh nahi paayi. Kya aap property buy karna, rent karna, ya invest karna chahte hain?",
        "mr": "माफ करा, मला समजलं नाही. तुम्ही प्रॉपर्टी खरेदी करायला, भाड्याने घ्यायला की इन्वेस्ट करायला बघताय?"
    },
    "Sorry, which area was that? Somewhere like Wakad or Baner, or maybe Hinjewadi?": {
        "hi": "Thoda clear bolenge? Agar open ho to Wakad, Baner, Hinjewadi ya Kharadi achhe options hain. Aap kis side dekh rahe ho?",
        "hinglish": "Thoda clear bolenge? Agar open ho to Wakad, Baner, Hinjewadi ya Kharadi achhe options hain. Aap kis side dekh rahe ho?",
    },
    "I didn't get the budget clearly. Are we looking around 50 lakhs, or perhaps closer to 2 crores?": {
        "hi": "Thoda clear bolenge? 50 lakh se 2 crore tak options hain. Aapka budget roughly kitna hai?",
        "hinglish": "Thoda clear bolenge? 50 lakh se 2 crore tak options hain. Aapka budget roughly kitna hai?",
    },
    "Could you repeat the property type? Is it a 2 BHK you're looking for, or something else?": {
        "hi": "Thoda clear bolenge? Aap 1 BHK, 2 BHK ya kuch aur dekh rahe ho?",
        "hinglish": "Thoda clear bolenge? Aap 1 BHK, 2 BHK ya kuch aur dekh rahe ho?",
    },
    "What works better for you — weekend or a weekday? And any time preference?": {
        "hi": "Thoda clear bolenge? Weekend better rahega ya weekday? Time ka bhi koi preference hai?",
        "hinglish": "Thoda clear bolenge? Weekend better rahega ya weekday? Time ka bhi koi preference hai?",
    },
    "That's alright. When would be a convenient time for a callback?": {
        "hi": "Koi baat nahi. Callback ke liye kaunsa time convenient rahega?",
        "hinglish": "Koi baat nahi. Callback ke liye kaunsa time convenient rahega?",
    },
    "No problem — are you thinking more budget-friendly, mid-range, or premium?": {
        "hi": "Koi issue nahi. Aap budget-friendly, mid-range ya premium mein kya prefer karoge?",
        "hinglish": "No problem. Aap budget-friendly, mid-range ya premium mein kya prefer karoge?",
    },
    "Popular areas include Wakad, Baner, Hinjewadi, and Kharadi. Which location interests you?": {
        "hi": "Popular options Wakad, Baner, Hinjewadi aur Kharadi mein hain. Aapko kaunsi location better lag rahi hai?",
        "hinglish": "Popular options Wakad, Baner, Hinjewadi aur Kharadi mein hain. Aapko kaunsi location better lag rahi hai?",
    },
    "Would next week work better for the visit?": {
        "hi": "Agar aapko theek lage to next week visit rakh lete hain?",
        "hinglish": "Agar theek lage to next week visit rakh lete hain?",
    },
    "Sorry about that. Thank you for your time. Goodbye.": {
        "hi": "Theek hai, time dene ke liye thanks. Namaste.",
        "hinglish": "Theek hai, time dene ke liye thanks. Bye.",
    },
    "Could you help me understand what you're looking for?": {
        "hi": "Aap exactly kis type ka option dekh rahe ho?",
        "hinglish": "Aap exactly kis type ka option dekh rahe ho?",
    },
    "Sorry, I didn't catch that clearly. Could you repeat that once?": {
        "hi": "Thoda clear bolenge? Main wahi se continue karti hoon.",
        "hinglish": "Thoda clear bolenge? Main wahi se continue karti hoon.",
    },
    "I'm sorry, I missed that. Could you say it again?": {
        "hi": "Maaf kijiyega, main sun nahi paayi. Ek baar aur bolenge?",
        "hinglish": "Maaf kijiyega, main sun nahi paayi. Ek baar aur bolenge?",
    },
    "Apologies, my line dropped for a second. What was that?": {
        "hi": "Shayad aawaz cut gayi thi. Kya kaha aapne?",
        "hinglish": "Shayad aawaz cut gayi thi. Kya kaha aapne?",
    },
    "Give me just one moment...": {
        "hi": "Ek second dijiye...",
        "hinglish": "Ek second dijiye...",
    },
}


@dataclass(frozen=True)
class UserTextAnalysis:
    original_text: str
    cleaned_text: str
    detected_language: str
    confidence: float
    actionable: bool
    reason: str
    latin_letters: int
    devanagari_letters: int
    unsupported_letters: int


def _detect_explicit_language_request(text: str) -> str | None:
    t = (text or "").lower()
    # Check English requests
    if any(phrase in t for phrase in ["speak english", "talk in english", "continue in english", "english mein baat", "english me baat"]):
        return "en"
    # Check Hindi requests
    if any(phrase in t for phrase in ["speak hindi", "talk in hindi", "continue in hindi", "hindi mein baat", "hindi me baat", "hindi bol", "hindi me baat karo", "hindi mein baat karo"]):
        return "hi"
    # Check Marathi requests
    if any(phrase in t for phrase in ["speak marathi", "talk in marathi", "continue in marathi", "marathi mein baat", "marathi me baat", "marathi bol"]):
        return "mr"
    return None


class LanguageTracker:
    """Keep language switching stable across noisy turns."""

    def __init__(self, initial_language: str = "en"):
        self.current_language = initial_language if initial_language in SUPPORTED_LANGUAGES else "en"
        self._determined = False
        self._history: list[str] = []
        self._cooldown_turns_left: int = 0

    """ def observe(self, text: str, allowed_languages: list[str] = None) -> tuple[str, UserTextAnalysis]:
        if allowed_languages is None:
            allowed_languages = ["en", "hi", "mr", "hinglish"]

        analysis = analyze_user_text(text, fallback=self.current_language)
        
        # Log the required information
        logger.info(
            "\nRAW TRANSCRIPT: \"%s\"\n"
            "DETECTED LANGUAGE: %s\n"
            "CONFIDENCE: %.2f\n"
            "SESSION LANGUAGE: %s",
            text,
            analysis.detected_language,
            analysis.confidence,
            self.current_language
        )

        if not analysis.actionable:
            return self.current_language, analysis

        # STEP 1: Classify current turn language
        label = analysis.detected_language
        if label not in {"en", "hi", "hinglish", "mr"}:
            label = "en"
            
        cleaned_text = re.sub(r"[^\w\s]", "", text.lower()).strip()
        words = cleaned_text.split()
        word_count = len(words)
        
        noise_fillers = {
            "haan", "okay", "hmm", "yes", "ha", "theek", "uh", "achha", "right", "sure", "no", "yeah",
            "yep", "nope", "ok", "hm", "mhm", "cool", "fine", "alright", "acha", "han", "kya", "why"
        }
        is_noise = (word_count > 0 and all(w in noise_fillers for w in words))
        # Require higher confidence and longer utterances to avoid language-flipping on noise
        is_uncertain = (analysis.confidence < 0.70 or word_count < 4)

        if is_noise or is_uncertain:
            logger.info("Label classified as Noise/Uncertain. Skipping state updates.")
            return self.current_language, analysis
            
        if not self._determined:
            # Require at least 10 words to determine session language initially.
            # This prevents "haan okay sure" from permanently switching to Hindi/Hinglish.
             if word_count < 10:
                logger.info("Utterance too short (%d words) to set initial session language. Keeping default: %s", word_count, self.current_language)
                return self.current_language, analysis 

            if not self._determined:
                if word_count < 2:
                    logger.info(
                        "Utterance too short (%d words) to determine language.",
                        word_count
                    )
                    return self.current_language, analysis

                if label in allowed_languages:
                    self.current_language = label
                    self._determined = True
                    self._history.clear()
                    self._cooldown_turns_left = 0

                    logger.info(
                        "Session language determined from first meaningful utterance: %s",
                        self.current_language
                    )

                return self.current_language, analysis
                
            if label in allowed_languages:
                self.current_language = label
                self._determined = True
                self._history.clear()
                self._cooldown_turns_left = 0
                logger.info("Session language determined from first meaningful utterance: %s", self.current_language)
            return self.current_language, analysis

        # STEP 2: Switch Gate
        self._history.append(label)
        if len(self._history) > 3:
            self._history.pop(0)
            
        switch_candidate = None
        
        # C. Explicit language change request bypasses everything
        explicit_req = _detect_explicit_language_request(text)
        if explicit_req in {"en", "hi", "hinglish", "mr"}:
            switch_candidate = explicit_req
            logger.info("Switch candidate via explicit request: %s", switch_candidate)
        else:
            # A. Full sentence — lower threshold for mid-call switch to 4 words
            if word_count >= 4 and label not in {"en", self.current_language}:
                switch_candidate = label
                logger.info("Switch candidate via mid-call short sentence: %s", switch_candidate)
            # B. Three consecutive turns — raised from 2 to prevent noise-based flipping
            elif (len(self._history) >= 3
                  and self._history[-1] == self._history[-2] == self._history[-3]
                  and self._history[-1] != self.current_language):
                switch_candidate = self._history[-1]
                logger.info("Switch candidate via 3 consecutive turns: %s", switch_candidate)

        # Ensure switch candidate is allowed!
        if switch_candidate and switch_candidate not in allowed_languages:
            logger.info("Switch candidate %s blocked because it is not in allowed_languages: %s", switch_candidate, allowed_languages)
            switch_candidate = None

        # STEP 3: Anti Ping-Pong Cooldown
        if explicit_req:
            pass # bypass cooldown
        elif switch_candidate:
            if self._cooldown_turns_left > 0:
                self._cooldown_turns_left -= 1
                logger.info("Cooldown active (%d left). Switch candidate %s blocked.", self._cooldown_turns_left, switch_candidate)
                switch_candidate = None
        
        if not switch_candidate and not explicit_req:
            if self._cooldown_turns_left > 0:
                self._cooldown_turns_left -= 1
                logger.info("Cooldown decremented to %d", self._cooldown_turns_left)

        # STEP 4: Apply Switch
        if switch_candidate:
            self.current_language = switch_candidate
            self._cooldown_turns_left = 3
            self._history.clear()
            logger.info("Language switched to %s, cooldown reset to 3", self.current_language)

        return self.current_language, analysis """

    
    def observe(
        self,
        text: str,
        allowed_languages: list[str] = None
    ) -> tuple[str, UserTextAnalysis]:
        """
        Detect the language of the CURRENT user turn.

        Important behavior:
        - The current user message determines the response language.
        - Conversation context is NOT lost when language changes.
        - English -> Hindi -> English -> Hindi switching is supported.
        - Very short/noisy utterances keep the previous language.
        """

        if allowed_languages is None:
            allowed_languages = ["en", "hi", "mr", "hinglish"]

        analysis = analyze_user_text(
            text,
            fallback=self.current_language
        )

        logger.info(
            "\nRAW TRANSCRIPT: \"%s\"\n"
            "DETECTED LANGUAGE: %s\n"
            "CONFIDENCE: %.2f\n"
            "PREVIOUS SESSION LANGUAGE: %s",
            text,
            analysis.detected_language,
            analysis.confidence,
            self.current_language
        )

        # ---------------------------------------------------------
        # 1. Ignore empty / non-actionable transcripts
        # ---------------------------------------------------------
        if not analysis.actionable:
            return self.current_language, analysis

        label = analysis.detected_language

        if label not in {"en", "hi", "mr", "hinglish"}:
            label = self.current_language

        # FIX K2: Magpie TTS has only EN-US / HI-IN voices — there is no
        # Hinglish voice, and Roman-script Hinglish text read through the
        # HI-IN voice comes out mispronounced. Collapse "hinglish" to "hi"
        # here at the source, so every downstream consumer of
        # self.current_language (template selection, TTS locale, fallback
        # replies) always gets a value that actually has a matching
        # Devanagari-script response and a matching voice.
        if label == "hinglish":
            label = "hi"

        # ---------------------------------------------------------
        # 2. Explicit language request has highest priority
        # ---------------------------------------------------------
        explicit_req = _detect_explicit_language_request(text)

        if explicit_req and explicit_req in allowed_languages:
            self.current_language = explicit_req
            self._determined = True
            self._history.clear()
            self._cooldown_turns_left = 0

            logger.info(
                "Explicit language request detected. "
                "Switched response language to: %s",
                self.current_language
            )

            return self.current_language, analysis

        # ---------------------------------------------------------
        # 3. Detect very short/noisy responses
        # ---------------------------------------------------------
        cleaned_text = re.sub(
            r"[^\w\s]",
            "",
            (text or "").lower()
        ).strip()

        words = cleaned_text.split()
        word_count = len(words)

        noise_fillers = {
            "haan",
            "han",
            "okay",
            "ok",
            "hmm",
            "hm",
            "mhm",
            "yes",
            "ha",
            "theek",
            "acha",
            "achha",
            "uh",
            "right",
            "sure",
            "no",
            "yeah",
            "yep",
            "nope",
            "cool",
            "fine",
            "alright",
            "kya",
            "why",
        }

        is_noise = (
            word_count > 0
            and all(word in noise_fillers for word in words)
        )

        # Very short responses such as:
        # "yes", "haan", "okay", "right"
        # should NOT switch the language.
        if is_noise:
            logger.info(
                "Noise/filler detected. Keeping current language: %s",
                self.current_language
            )
            return self.current_language, analysis

        # ---------------------------------------------------------
        # 4. CURRENT TURN LANGUAGE WINS
        # ---------------------------------------------------------
        #
        # This is the important change.
        #
        # Example:
        #
        # Turn 1:
        #   "Tell me about Suncity Apartments."
        #   -> English
        #
        # Turn 2:
        #   "Mujhe iska price batao."
        #   -> Hindi
        #
        # Turn 3:
        #   "What amenities are available?"
        #   -> English
        #
        # Turn 4:
        #   "Isme parking available hai?"
        #   -> Hinglish
        #
        # We don't wait for 3 consecutive turns.
        # ---------------------------------------------------------

        if label in allowed_languages:

            # If this is the first meaningful user message
            if not self._determined:
                self.current_language = label
                self._determined = True
                self._history.clear()
                self._cooldown_turns_left = 0

                logger.info(
                    "Initial language determined from current turn: %s",
                    self.current_language
                )

                return self.current_language, analysis

            # If current turn language is different,
            # immediately switch.
            if label != self.current_language:

                logger.info(
                    "LANGUAGE SWITCH: %s -> %s "
                    "(based on CURRENT user turn)",
                    self.current_language,
                    label
                )

                self.current_language = label
                self._history.clear()
                self._cooldown_turns_left = 0

            else:
                logger.info(
                    "Current turn language matches session language: %s",
                    self.current_language
                )

        return self.current_language, analysis


def analyze_user_text(text: str, fallback: str = "en") -> UserTextAnalysis:
    """Classify user text for language and whether it is safe to act on."""
    normalized_fallback = fallback if fallback in SUPPORTED_LANGUAGES else "en"
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return UserTextAnalysis(text, "", normalized_fallback, 0.0, False, "empty", 0, 0, 0)

    latin_letters = 0
    devanagari_letters = 0
    unsupported_letters = 0
    for ch in cleaned:
        if not ch.isalpha():
            continue
        if DEVANAGARI_RANGE[0] <= ch <= DEVANAGARI_RANGE[1]:
            devanagari_letters += 1
        elif ch.isascii():
            latin_letters += 1
        else:
            unsupported_letters += 1

    if latin_letters == 0 and devanagari_letters == 0 and unsupported_letters == 0:
        return UserTextAnalysis(
            text, cleaned, normalized_fallback, 0.0, False, "punctuation_only", 0, 0, 0
        )

    # Whitelist common Unicode punctuation Whisper sometimes inserts
    # (curly apostrophes/quotes, em-dashes, ellipsis) — these are NOT
    # foreign scripts and should not cause a transcript to be rejected.
    _PUNCT_WHITELIST = {
        '\u2019', '\u2018',  # curly apostrophes
        '\u201c', '\u201d',  # curly double quotes
        '\u2013', '\u2014',  # en-dash / em-dash
        '\u2026',            # ellipsis
        '\u00e9', '\u00e8', '\u00e0', '\u00e2',  # French accented vowels (common in names)
    }
    # Count only truly foreign-alphabet letters (not the whitelisted punctuation)
    adjusted_unsupported = sum(
        1 for ch in cleaned
        if ch.isalpha()
        and ch not in _PUNCT_WHITELIST
        and not (DEVANAGARI_RANGE[0] <= ch <= DEVANAGARI_RANGE[1])
        and not ch.isascii()
    )
    total_letters = latin_letters + devanagari_letters + adjusted_unsupported
    unsupported_ratio = adjusted_unsupported / total_letters if total_letters > 0 else 0.0

    # Only reject as unsupported_script if foreign chars dominate (>60%)
    # AND there is very little usable Latin or Devanagari content (<3 chars each).
    # This prevents over-dropping Hinglish turns that happen to contain a
    # curly apostrophe or a single accented character.
    if adjusted_unsupported > 0 and unsupported_ratio > 0.60 and latin_letters < 3 and devanagari_letters < 3:
        return UserTextAnalysis(
            text,
            cleaned,
            normalized_fallback,
            0.0,
            False,
            "unsupported_script",
            latin_letters,
            devanagari_letters,
            unsupported_letters,
        )

    if devanagari_letters > 0:
        marathi_hits = _count_markers(cleaned, MARATHI_MARKERS)
        hindi_hits = _count_markers(cleaned, HINDI_MARKERS)
        # FIX L: a single Marathi marker (e.g. "ho", which Hindi speakers
        # also use — "kaise ho") was enough to mislabel a mixed Hindi/roman
        # sentence as Marathi. This app explicitly maps mr -> hi downstream,
        # but the raw "mr" label still confused the tracker's own turn logs.
        # Require a clearer majority before preferring Marathi.
        if marathi_hits >= 2 and marathi_hits > hindi_hits:
            return UserTextAnalysis(text, cleaned, "mr", 0.96, True, "clear_marathi", latin_letters, devanagari_letters, unsupported_letters)
        if hindi_hits > 0:
            return UserTextAnalysis(text, cleaned, "hi", 0.96, True, "clear_hindi", latin_letters, devanagari_letters, unsupported_letters)
        return UserTextAnalysis(text, cleaned, "hi", 0.84, True, "devanagari", latin_letters, devanagari_letters, unsupported_letters)

    latin_text = cleaned.casefold()
    hinglish_hits = _count_markers(latin_text, HINGLISH_MARKERS)
    roman_hindi_hits = _count_markers(latin_text, ROMAN_HINDI_MARKERS)
    english_style_hits = _count_markers(latin_text, ENGLISH_STYLE_MARKERS)
    english_words = re.findall(r"\b[a-z]{2,}\b", latin_text)

    """ # Subtract cross-language words from hits to avoid false positives when
    # English speakers say real-estate terms like "flat", "budget", "property".
    cross_hits = sum(1 for w in _CROSS_LANGUAGE_WORDS if re.search(rf"\b{re.escape(w)}\b", latin_text))
    effective_roman_hindi = max(0, roman_hindi_hits - cross_hits) """
    # Real-estate words such as "ghar", "flat", "property", and "budget"
    # can appear in both English and Roman Hindi. Do not subtract them
    # from the Roman Hindi score; language classification should rely on
    # the overall combination of Hindi markers.
    cross_hits = 0
    effective_roman_hindi = roman_hindi_hits

    logger.info(
        "[LANG DEBUG] text=%r | hinglish_hits=%d | roman_hindi_hits=%d | "
        "english_style_hits=%d | cross_hits=%d | effective_roman_hindi=%d",
        text,
        hinglish_hits,
        roman_hindi_hits,
        english_style_hits,
        cross_hits,
        effective_roman_hindi,
    )

    # FIX J: threshold lowered from >=2 to >=1. Unlike the old broad marker
    # sets, ROMAN_HINDI_MARKERS/HINGLISH_MARKERS here contain NO word that
    # also appears in ordinary English (no "budget", "flat", "to", "area").
    # Every word in these lists is exclusively Hindi/Hinglish, so one match
    # is a reliable signal, and requiring >=2 was silently dropping
    # single-marker turns like "2 BHK flat, chahti," to English.
    if effective_roman_hindi >= 1 and english_style_hits == 0:
        return UserTextAnalysis(text, cleaned, "hi", 0.86, True, "roman_hindi", latin_letters, devanagari_letters, unsupported_letters)
    if effective_roman_hindi >= 2 and english_style_hits >= 1:
        return UserTextAnalysis(text, cleaned, "hinglish", 0.88, True, "clear_hinglish", latin_letters, devanagari_letters, unsupported_letters)
    if hinglish_hits >= 2 or (hinglish_hits >= 1 and english_style_hits >= 2):
        return UserTextAnalysis(text, cleaned, "hinglish", 0.85, True, "clear_hinglish", latin_letters, devanagari_letters, unsupported_letters)

    # C5 FIX: Removed the hinglish_hits == 1 check so single Hinglish filler words 
    # fall through to English, preventing permanent language switching on "haan".

    # FIX P — STICKY FALLBACK for genuinely ambiguous turns, content-based
    # instead of word-count-based.
    #
    # FIX J2 (previous round) used "< 4 latin words" as the cutoff, but any
    # Hinglish utterance is ALSO written in latin script — "Haa, me dekhna,
    # chahtuyun." has exactly 4 latin tokens (none of them real English
    # words, none matching a marker due to STT garbling) and slipped past
    # the "< 4" cutoff by exactly one word, flipping the session to English
    # on pure noise. Counting latin tokens was never a reliable signal of
    # English — Hinglish IS latin script. Require actual evidence instead:
    # at least one recognizable, common English word. Zero Hindi signal
    # alone is not enough to leave Hindi.
    _COMMON_ENGLISH_WORDS = {
        "the", "is", "am", "are", "was", "were", "i", "you", "we", "it",
        "yes", "no", "not", "want", "need", "would", "will", "can", "could",
        "please", "thanks", "thank", "sorry", "okay", "ok", "sure",
        "see", "show", "visit", "day", "time", "today", "tomorrow",
        "morning", "evening", "afternoon", "busy", "later", "house", "home",
        "flat", "book", "schedule", "confirm", "cancel", "change", "phone",
        "keep", "call", "speak", "talk", "hello", "hi", "bye", "good",
        "monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday",
    }
    _has_real_english_word = any(w in _COMMON_ENGLISH_WORDS for w in english_words)

    if (
        normalized_fallback in {"hi", "hinglish"}
        and effective_roman_hindi == 0
        and hinglish_hits == 0
        and english_style_hits == 0
        and not _has_real_english_word
    ):
        return UserTextAnalysis(
            text, cleaned, normalized_fallback, 0.55, True,
            "ambiguous_sticky", latin_letters, devanagari_letters, unsupported_letters,
        )

    confidence = 0.87 if len(english_words) >= 2 else 0.68
    return UserTextAnalysis(text, cleaned, "en", confidence, True, "latin_text", latin_letters, devanagari_letters, unsupported_letters)


def detect_language_from_text(text: str, fallback: str = "en") -> str:
    """Infer the user's language from a short utterance."""
    return analyze_user_text(text, fallback=fallback).detected_language


def is_actionable_user_text(text: str, fallback: str = "en") -> bool:
    """Return whether a transcript is safe to send into the conversation state machine."""
    return analyze_user_text(text, fallback=fallback).actionable


def get_language_label(language: str) -> str:
    normalized = language if language in SUPPORTED_LANGUAGES else "en"
    return {
        "en": "English",
        "hi": "Hindi",
        "mr": "Marathi",
        "hinglish": "Hinglish",
    }[normalized]


""" def get_language_instruction(language: str) -> str:
    Return a concise generation directive for the active language.
    normalized = language if language in SUPPORTED_LANGUAGES else "en"
    instructions = {
        "en": (
            "Respond in clear, natural English. Start in English and stay there unless the user clearly and consistently uses another supported language."
        ),
        "hi": (
            "Respond in natural spoken Hindi using standard Devanagari script. Keep it casual, warm, and human. Avoid textbook or overly formal Hindi."
        ),
        "mr": (
            "Respond in conversational, respectful Marathi. Keep it natural, polished, and easy to follow."
        ),
        "hinglish": (
            "Respond in natural Indian Hinglish. Mirror the user's style, keep real-estate terms in English when that sounds natural, and avoid stiff translations."
        ),
    }
    return instructions[normalized] """

def get_language_instruction(language: str) -> str: 
    """ Return the generation instruction for the CURRENT user turn. The language is selected from the latest user message. Conversation context should still be preserved. """ 
    normalized = ( language if language in SUPPORTED_LANGUAGES else "en" ) 
    instructions = { "en": ( "Respond in clear, natural English. " "Use a friendly and professional real-estate conversational style. " "Keep the answer concise and easy to understand." ), 
                     "hi": ( "Respond in natural spoken Hindi using Devanagari script. " "Use a friendly and professional real-estate conversational style. " "Keep the language simple, natural, and easy to understand. " "Do not use overly formal or textbook Hindi." ),
                     "mr": ( "Respond in natural conversational Marathi. " "Use a friendly and professional real-estate conversational style. " "Keep the language simple, natural, and easy to understand." ),
                     "hinglish": ( "Respond in natural Indian Hinglish. " "Mirror the user's Hindi-English speaking style. " "Keep common real-estate terms such as property, budget, location, " "BHK, investment, rent, price, and site visit in English when natural. " "Do not produce a stiff word-for-word translation." ), } 
    return instructions[normalized]


def localize_template(template: str, language: str) -> str:
    """Map shared-flow English templates to Hindi/Hinglish without changing the flow."""
    normalized = language if language in SUPPORTED_LANGUAGES else "en"
    if normalized not in {"hi", "hinglish", "mr"}:
        return template
    variants = LOCALIZED_TEMPLATE_MAP.get(template)
    if not variants:
        return template
    return variants.get(normalized, template)


def _count_markers(text: str, markers: tuple[str, ...]) -> int:
    count = 0
    for marker in markers:
        # H2 FIX: \b only works on ASCII words. For Devanagari, use plain containment check.
        if any('\u0900' <= ch <= '\u097f' for ch in marker):
            if marker in text:
                count += 1
        else:
            if re.search(rf"\b{re.escape(marker)}\b", text):
                count += 1
    return count