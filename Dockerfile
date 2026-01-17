# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Set working directory
WORKDIR /app

# Install system dependencies
# ffmpeg: for audio normalization
# python3-dev: for building some python packages
# build-essential: compiler tools
RUN apt-get update && apt-get install -y \
    ffmpeg \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create a directory for persistent data (SQLite)
RUN mkdir -p /app/data

# Copy the rest of the application
COPY . .

# Expose port
EXPOSE 8000

# Run the application
# We use the PORT environment variable provided by the host (like Render)
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
