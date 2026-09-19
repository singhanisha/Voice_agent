web: bash -c "cd /app/Backend && PYTHONPATH=. gunicorn main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT"
