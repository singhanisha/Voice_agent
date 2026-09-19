# Voice Agent Pro: AI Voice Pipeline Architecture

The AI Voice Pipeline is the "brain" of the platform, designed for ultra-low latency (<500ms TTFB) and human-like conversational flow. It is built using the **Pipecat** framework for streaming orchestration.

---

## 🌊 Pipeline Data Flow
The pipeline follows a linear streaming architecture:
`User Audio` → `VAD/STT` → `LLM` → `TTS` → `Client Audio`

### 1. 🎤 Audio Ingress & VAD
- **Ingress**: Audio arrives as raw PCM16 bytes via WebSockets (from Twilio or Browser).
- **Adaptive VAD (Voice Activity Detection)**: 
  - Continuously calibrates a "Noise Floor" (bottom 10% energy).
  - Uses dynamic RMS thresholds to trigger speech start/end.
  - Features a **Barge-in Trigger**: If sustained speech is detected while the AI is speaking, it emits a `CancelFrame` to stop the current AI response.

### 2. 🔤 STT (Speech-to-Text)
- **Engine**: **Groq Cloud STT** (`whisper-large-v3-turbo`).
- **Processing**:
  - Audio is chunked based on silence detection (~1.2s trailing silence).
  - **Hallucination Filter**: Uses a hardcoded list of common Whisper "silence-hallucinations" (e.g., *"Thanks for watching"*, *"MBC News"*) to prevent the AI from responding to background noise.
  - **Language Support**: Auto-detects and supports code-mixing (English, Hindi, Marathi, Hinglish).

### 3. 🧠 LLM (Intelligence & State)
- **Engine**: **Groq Cloud LLM** (`llama-3.1-8b-instant`).
- **State Management**:
  - Uses a **Node-based Conversation Flow** (defined in JSON).
  - The `StateManager` tracks the current node and valid transitions.
  - **GenID Syncing**: Every user turn increments a `gen_id`. Audio frames and transcripts are tagged with this ID to ensure the frontend only plays audio that matches the latest transcript, preventing "ghost" audio from canceled turns.

### 4. 🔊 TTS (Text-to-Speech)
- **Engines**: 
  - **Edge-TTS** (Microsoft): High-quality, variety of voices.
  - **Kokoro-TTS**: Local, high-performance synthesis.
- **Streaming**: Audio is synthesized and pushed downstream in small chunks (~200ms) as soon as they are ready, enabling the user to hear the AI before the full sentence is even finished generating.

---

## ⚡ Latency Optimization Strategies
- **Groq Offloading**: Using Groq's LPU (Language Processing Unit) reduces LLM inference time to ~100-200ms.
- **Async Execution**: I/O-bound tasks (API calls) run in a `ThreadPoolExecutor` within the `asyncio` event loop to prevent blocking.
- **PCM Resampling**: Efficient `scipy.signal.resample_poly` ensures all audio is converted to 16kHz for STT and 24kHz for TTS with minimal overhead.
- **Circuit Breakers**: Every ML stage (STT/LLM/TTS) has a hard timeout (e.g., 4s for LLM). If it times out, a fallback response (e.g., *"Give me a moment..."*) is triggered to maintain conversation continuity.

---

## 🛠 Component Map
| Component | Implementation | Source Path |
| :--- | :--- | :--- |
| **Orchestrator** | Pipecat `Pipeline` | `Backend/flows/runtime.py` |
| **VAD / STT Logic** | `RealEstateSTTProcessor` | `Backend/flows/runtime.py` |
| **LLM Logic** | `RealEstateLLMProcessor` | `Backend/flows/runtime.py` |
| **TTS Logic** | `RealEstateTTSProcessor` | `Backend/flows/runtime.py` |
| **STT Provider** | Groq (Whisper v3 Turbo) | `Backend/stt/stt.py` |
| **LLM Provider** | Groq (Llama 3.1 8B) | `Backend/llm/llm.py` |
| **TTS Provider** | Edge-TTS / Kokoro | `Backend/tts/` |

---

## 🛡 Reliability Features
- **Hallucination Filtering**: Blocks Whisper from hallucinating on silence.
- **Rate-Limit Cooldown**: If Groq returns a 429 (Too Many Requests), the STT module enters a 5-second cooldown to avoid wasting resources.
- **UTF-8 Stream Forcing**: Specifically for Windows environments, the backend forces UTF-8 on stdout to prevent logging crashes when printing Hindi/Marathi characters.
