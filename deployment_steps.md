# Deployment Guide: Voice Agent Platform

This guide provides instructions for deploying the restructured Voice Agent Platform.

## 1. Project Structure

The project is now organized into two main directories:
- **`frontend-next/`**: Contains the Next.js web interface.
- **`Backend/`**: Contains the FastAPI server, speech processing logic, and databases.

## 2. Local Deployment (Manual)

### Prerequisites
- Python 3.10+
- Node.js 18+
- FFmpeg installed on your system (for audio processing)

### Setup Steps

#### Backend
1. **Navigate to the Backend directory**:
   ```bash
   cd Backend
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables**:
   Update the `.env` file in the `Backend/` directory with your API keys:
   - `GROQ_API_KEY`
   - `PLATFORM_API_KEY` (if required)

4. **Run the Server**:
   ```bash
   python main.py
   ```
   The backend will start at `http://localhost:8000`.

#### Frontend
1. **Navigate to the frontend-next directory**:
   ```bash
   cd frontend-next
   ```

2. **Install dependencies**:
   ```bash
   npm install
   ```

3. **Run the Dashboard**:
   ```bash
   npm run dev
   ```
   The frontend will start at `http://localhost:3000`.

---

## 3. Docker Deployment (Recommended)

### Prerequisites
- Docker and Docker Compose installed.

### Deploying with Docker Compose
1. **Build and Start**:
   From the project root directory (where `docker-compose.yml` is located):
   ```bash
   docker-compose up --build
   ```

2. **Access the Platform**:
   The application dashboard will be available at `http://localhost:3000`.

### Key Docker configurations:
- The **DB volume** is mapped to `./Backend/db` on your host machine to ensure data persistence.
- The **Environment variables** are passed through the `docker-compose.yml` or your system environment.

---

## 4. Troubleshooting
- **API Connection Errors**: Ensure the `NEXT_PUBLIC_API_URL` in your frontend environment matches the backend URL (e.g., `http://localhost:8000`).
- **Audio issues**: Ensure FFmpeg is installed and accessible in the system path or within the Docker container.
- **WebSocket Disconnection**: Check if the backend is running and the port 8000 is open and not blocked by a firewall.
