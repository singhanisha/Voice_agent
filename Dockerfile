FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    espeak-ng \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set working directory to /app/Backend
WORKDIR /app/Backend

# Copy requirements from Backend folder and install
COPY Backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project to /app
# Frontend will be at /app/Frontend
# Backend will be at /app/Backend
COPY . /app/

# Expose port 3000
EXPOSE 3000

# Run the server from the Backend directory
CMD ["python", "main.py"]
