FROM python:3.12-slim

# ffmpeg is required for merging video+audio and for MP3 conversion.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY static ./static

ENV PORT=8000 \
    PYTHONUNBUFFERED=1 \
    WORK_DIR=/tmp

EXPOSE 8000

# Single worker keeps the in-memory job table coherent; concurrency is handled
# by the thread pool inside the app (MAX_CONCURRENT).
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --timeout-keep-alive 75"]
