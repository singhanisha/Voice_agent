# Cosmic Chameleon: Voice AI Calling SaaS Platform

A production-ready, modular AI voice agent platform. This project features a high-performance voice pipeline (Pipecat + Groq + Faster-Whisper + Edge-TTS) with a premium Next.js dashboard.

---

## 📂 Project Structure

The project is divided into two main components:

- **[`frontend-next/`](./frontend-next)**: A modern Next.js dashboard for administrators and clients, featuring real-time monitoring and Firebase authentication.
- **[`Backend/`](./Backend)**: The FastAPI server and AI processing modules (STT, LLM, TTS).
- **[`DEVELOPMENT_GUIDE.md`](./DEVELOPMENT_GUIDE.md)**: Guide for fine-tuning agents, deployment, and conflict avoidance.

---

## 🚀 Quick Start

### 1. Prerequisites
- **Python 3.10+**
- **Node.js 18+**
- **FFmpeg**: Required for audio processing.

### 2. Installation

#### Backend
1. Navigate to the `Backend/` directory.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

#### Frontend
1. Navigate to the `frontend-next/` directory.
2. Install dependencies:
   ```bash
   npm install
   ```

### 3. Configuration
Create a `.env` file in the `Backend/` directory:
```env
GROQ_API_KEY=your_groq_key_here
PLATFORM_API_KEY=your_secure_password # For platform security
```

Create a `.env.local` in `frontend-next/`:
```env
NEXT_PUBLIC_API_URL=http://localhost:8000
# Add Firebase config if needed
```

### 4. Running the Platform

#### Start Backend
From the `Backend/` directory:
```bash
python main.py
```
The API will be available at `http://localhost:8000`.

#### Start Frontend
From the `frontend-next/` directory:
```bash
npm run dev
```
The dashboard will be available at `http://localhost:3000`.

---

## 🏗 Architecture

| Component | Technology | Role |
|-----------|------------|------|
| **Frontend** | Next.js / Tailwind / Firebase | Dashboard and Monitoring Interface |
| **Backend** | FastAPI / Python | API and WebSocket Orchestration |
| **LLM** | Groq (Llama-3.1-8B) | Core logic and persona handling |
| **STT** | Faster-Whisper | High-speed local speech-to-text |
| **TTS** | Edge-TTS / Kokoro | Neural voice synthesis |
| **Pipeline** | Pipecat-AI | Streaming orchestration |

---

## 🔧 Maintenance & Fine-Tuning
For detailed instructions on how to improve the agent, update prompts, and push changes to production without conflicts, please refer to the:
👉 **[Development & Fine-Tuning Guide](./DEVELOPMENT_GUIDE.md)**

---

## ⚠️ Known Issues & Integration Troubleshooting

### 1. Secure Context (HTTPS) Requirement
Modern browsers block microphone access (`getUserMedia`) on non-secure origins. 
- **Localhost**: Works fine over `http://localhost:3000`.
- **Remote Prod**: You **MUST** use HTTPS.

### 2. Browser Autoplay Policy
Browsers will not play audio automatically. The dashboard requires a user gesture to resume the `AudioContext`.

### 3. First-Run Latency
On the first run, the system may download AI model weights. Subsequent runs are instant.

---

## 🛠 Deployment
For production deployment steps:
**[`deployment_steps.md`](./deployment_steps.md)**
