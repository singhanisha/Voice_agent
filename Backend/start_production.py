#!/usr/bin/env python3
"""
Production startup script for Voice AI SaaS Platform.

Usage:
    # Development (single worker, auto-reload):
    python server.py

    # Production (multi-worker, no reload):
    python start_production.py

    # Or run gunicorn directly:
    gunicorn server:app -k uvicorn.workers.UvicornWorker -w 4 --bind 0.0.0.0:3000
"""

import os
import subprocess
import sys


def main():
    workers    = int(os.getenv("WORKERS", "4"))
    host       = os.getenv("HOST", "0.0.0.0")
    port       = int(os.getenv("PORT", "3000"))
    log_level  = os.getenv("LOG_LEVEL", "info")

    cmd = [
        "gunicorn",
        "main:app",
        "-k", "uvicorn.workers.UvicornWorker",
        "-w", str(workers),
        "--bind", f"{host}:{port}",
        "--log-level", log_level,
        "--access-logfile", "voice_agent_access.log",
        "--error-logfile",  "voice_agent_error.log",
        "--timeout", "120",          # allow up to 2min for long calls
        "--keepalive", "65",         # keep WS-compatible keep-alive
        "--worker-connections", "100",
        "--preload-app",             # load DB once then fork
    ]

    print(f"[START] Launching {workers} workers on {host}:{port}")
    print(f"[START] Command: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        print("\n[ERROR] gunicorn not found. Install it with:")
        print("  pip install gunicorn")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[STOP] Server stopped.")


if __name__ == "__main__":
    main()
