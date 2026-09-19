# 🚀 Cosmic Chameleon: Development & Fine-Tuning Guide

This guide explains how to maintain, improve, and deploy the Voice AI SaaS platform. Follow these patterns to ensure stability and avoid conflicts.

---

## 🏗 Project Architecture

| Component | Path | Description |
| :--- | :--- | :--- |
| **Main Server** | `Backend/main.py` | FastAPI app, REST API, orchestration of WebSocket sessions. |
| **Voice Pipeline** | `Backend/main.py` & `Backend/flows/` | Pipecat processors (STT → LLM → TTS). The "brain" of the agent. |
| **Agent Logic** | `Backend/llm/` | State management (`state_manager.py`) and prompt generation (`llm.py`). |
| **Database** | `Backend/db/` | SQLite database (`platform.db`) and agent JSON schemas. |
| **Frontend** | `frontend-next/src/app` | Next.js App Router dashboard with Firebase Auth. |

---

## 🧠 Fine-Tuning the Agent

### 1. Modifying the Prompt & Persona
The agent's behavior is defined by a JSON schema.
- **Template**: `Backend/Updated_Real_Estate_Agent.json`.
- **Active Agents**: Stored in `Backend/db/agents/{agent_id}.json`.
- **Change behavior**: Edit the `script` or `nodes` inside the JSON. The `StateManager` loads these dynamically at the start of every call.

### 2. Adjusting Voice & Speech
- **Voice IDs**: Map ElevenLabs or Edge IDs in `Backend/main.py` (see `create_agent` endpoint).
- **Latency**: STT sensitivity is controlled in `Backend/stt/` (e.g., `MIN_CHUNK_MS`, `SILENCE_RMS_THRESHOLD`).

### 3. State Machine (Multi-turn Logic)
The agent uses a "Node" based conversation flow defined in the agent JSON.
- **Current Node**: Tracked by `StateManager.current_node_id`.
- **Transitions**: The LLM decides when to jump to the next node based on user intent. Edit `generate_response` in `llm.py` to refine transition logic.

---

## 🚀 Deployment & Production

### 1. Production Run
Use the production wrapper to enable Gunicorn (multi-worker) and proper logging:
```bash
# From Voice_Agent/Backend
python start_production.py
```

### 2. HTTPS Requirement (CRITICAL)
Browsers **block** `navigator.mediaDevices.getUserMedia` (microphone) on non-secure connections.
- **Local Dev**: `http://localhost:3000` is allowed.
- **Remote Prod**: You **MUST** use HTTPS. Configure Nginx with Certbot.

### 3. Nginx WebSocket Configuration
Ensure your Nginx config has the `Upgrade` headers, or the browser mic will fail to connect:
```nginx
location /ws/ {
    proxy_pass http://localhost:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

---

## 🛠 Workflow & Conflict Prevention

### 1. Safe Development Loop
1. **Kill the server** before making major structural changes to `main.py`.
2. **Hard Reload** the browser (`Ctrl+Shift+R`) after frontend changes to clear any cached scripts.
3. **Monitor Logs**: Watch the `voice_agent.log` file for real-time pipeline diagnostics.

### 2. Avoiding Database Conflicts
- The system uses SQLite. Avoid opening the `.db` file in external editors while the server is running.
- **FK Constraints**: When manually editing the DB, ensure `client_id` and `agent_id` exist in their respective tables.

### 3. Environment Secrets
Keep your `.env` file updated in `Backend/.env`:
- `GROQ_API_KEY`: LLM processing.
- `PLATFORM_API_KEY`: If set, all dashboard write-actions require an `X-API-Key` header.

---

## 📈 Status & Monitoring
- **Health Check**: Visit `http://your-ip:8000/health`. It returns 200 OK only if the DB is reachable.
- **Dashboard Hub**: Check the `ws_hub.py` logs to see how many active dashboard connections are open.

---

## 🧪 Testing
- **Mic Test**: Run `python Backend/mic_test.py` to verify PyAudio/Groq access locally.
- **Simulated Call**: Use the "Demo Campaign" in the dashboard to watch the AI talk to itself without using a real phone number.
