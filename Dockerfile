# --- Stage 1: build Vite dashboard ---
FROM node:20-bookworm-slim AS frontend
WORKDIR /app
COPY app/frontend/package.json app/frontend/package-lock.json ./
RUN npm ci
COPY app/frontend/ ./
RUN npm run build

# --- Stage 2: FastAPI hub + static UI ---
FROM python:3.12-slim-bookworm

# OpenCV headless and common native deps for wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY app/backend/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY app/backend/ .

COPY --from=frontend /app/dist ./static

ENV PYTHONUNBUFFERED=1
ENV OMNIBOT_DATA_DIR=/data
ENV OMNIBOT_STATIC_ROOT=/app/backend/static
ENV HOST=0.0.0.0
ENV PORT=8000

RUN mkdir -p /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
    CMD python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/ping")'

CMD ["python", "app.py"]
