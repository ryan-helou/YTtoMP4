# ---- stage 1: build the BgUtils PO token provider ------------------------
# YouTube demands a "proof of origin" token from server IPs; this sidecar mints
# them locally so yt-dlp doesn't get bounced with "confirm you're not a bot".
FROM node:20-bookworm-slim AS pot
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /pot
RUN git clone --single-branch --branch 2.0.0 --depth 1 \
      https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git . \
 && cd server && npm ci && npx tsc && npm prune --omit=dev

# ---- stage 1b: Deno, the JS runtime yt-dlp uses ---------------------------
# Without one, yt-dlp can't run YouTube's JS challenge and falls back to the
# visionos client, which answers LOGIN_REQUIRED from a server IP.
FROM denoland/deno:bin-2.5.3 AS deno

# ---- stage 2: the app ----------------------------------------------------
FROM python:3.12-slim

# ffmpeg merges video+audio and makes the MP3s.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Node runtime + the built provider. It listens on 127.0.0.1 only (binding
# 0.0.0.0 was the RCE in < 2.0.0), so it's reachable by this container alone.
COPY --from=pot /usr/local/bin/node /usr/local/bin/node
COPY --from=pot /pot/server /opt/pot
COPY --from=deno /deno /usr/local/bin/deno

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py entrypoint.sh ./
COPY static ./static
RUN chmod +x entrypoint.sh

ENV PORT=8000 \
    PYTHONUNBUFFERED=1 \
    WORK_DIR=/tmp \
    POT_PORT=4416

EXPOSE 8000
CMD ["./entrypoint.sh"]
