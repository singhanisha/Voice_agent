# Voice Agent Pro: Current Architecture Analysis

## 🏗️ High-Level Architecture
Voice Agent Pro is a full-stack SaaS platform for AI-powered voice calling. It follows a decoupled architecture with a **FastAPI** backend and a **Next.js** frontend.

### 🌓 Component Overview
1.  **Frontend (Next.js)**: A React-based web application providing the dashboard, agent management, and live monitoring.
2.  **Backend (FastAPI)**: A high-performance Python server managing the AI voice pipeline, database, and telephony integrations.
3.  **AI Voice Pipeline (Pipecat)**: Orchestrates the flow between STT, LLM, and TTS.
4.  **Database (SQLite)**: Stores persistent data like agents, campaigns, and call logs.

---

## 💻 Frontend Stack
- **Framework**: [Next.js](https://nextjs.org/) (App Router)
- **State Management**: React Context API (AuthContext)
- **Authentication**: [Firebase Auth](https://firebase.google.com/docs/auth) (Email/Password + Google)
- **Styling**: Vanilla CSS / CSS Modules (Premium aesthetics with glassmorphism and animations)
- **Real-time**: WebSockets for live dashboard updates and browser-based voice chat.

---

## ⚙️ Backend Stack
- **Framework**: [FastAPI](https://fastapi.tiangolo.com/)
- **Orchestration**: [Pipecat](https://www.pipecat.ai/) (Pipeline-based processing)
- **Concurrency**: Asynchronous (Python `asyncio`)
- **Database**: [SQLite](https://www.sqlite.org/) (via `platform.db`)
- **Real-time**: [WebSockets](https://fastapi.tiangolo.com/advanced/websockets/) for streaming audio and dashboard events.

---

## 🎙️ AI Voice Pipeline
The core "intelligence" follows this flow:
1.  **STT (Speech-to-Text)**: [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) (Optimized local Whisper implementation).
2.  **LLM (Large Language Model)**: [Groq](https://groq.com/) using `llama-3.1-8b-instant` for ultra-low latency inference.
3.  **TTS (Text-to-Speech)**: 
    - [Edge-TTS](https://github.com/rany2/edge-tts) (Microsoft Azure free tier voices).
    - [Kokoro-TTS](https://github.com/hexgrad/kokoro) (High-quality local TTS).

---

## 📞 Telephony & Connectivity
- **Twilio Integration**: Uses Twilio Media Streams to pipe call audio into the backend.
- **Browser Mic**: Uses `AudioWorklet` and WebSockets to bridge local audio to the server pipeline for "Talk Live" and "Demo" modes.
- **WebSocket Hub**: Managed via `ws_hub.py` to broadcast events across multiple dashboard clients.

---

## 📁 Project Structure
```text
Voice_Agent/
├── Backend/
│   ├── db/              # SQLite DB & JSON data stores
│   ├── flows/           # Pipeline processor logic
│   ├── integrations/    # External service connectors
│   ├── llm/             # Groq & Prompt management
│   ├── stt/             # Whisper STT config & logic
│   ├── tts/             # Edge-TTS & Kokoro logic
│   ├── telephony/       # Twilio TwiML & stream handlers
│   └── main.py          # FastAPI Entry Point
├── frontend-next/
│   ├── src/
│   │   ├── app/         # Next.js Pages & Layouts
│   │   ├── components/  # UI Components
│   │   ├── context/     # Auth & Global State
│   │   └── lib/         # Firebase & Utility functions
│   └── .env.local       # Environment secrets
└── Dockerfile           # Deployment containerization
```

---

## 🚀 Deployment (Inferred)
- **Backend**: Hosted on [Railway](https://railway.app/) for persistent WebSocket and Python support.
- **Frontend**: Hosted on [Vercel](https://vercel.com/) for optimized Next.js delivery.
- **DB**: Local SQLite file persisted via Railway volumes.
