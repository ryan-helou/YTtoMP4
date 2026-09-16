#!/bin/sh
# Run Grab on this machine. No cookies needed here — YouTube only bot-blocks
# datacenter IPs, not home connections.
set -e
cd "$(dirname "$0")"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is missing. Install it with:  brew install ffmpeg"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Setting up (one time)…"
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
fi
./.venv/bin/pip install -q -r requirements.txt

PORT="${PORT:-8000}"
LAN=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")

echo ""
echo "  Grab is running:"
echo "    This Mac:  http://localhost:$PORT"
[ -n "$LAN" ] && echo "    Phone/iPad on the same Wi-Fi:  http://$LAN:$PORT"
echo ""
echo "  Ctrl-C to stop."
echo ""

( sleep 2; open "http://localhost:$PORT" >/dev/null 2>&1 || true ) &

# 0.0.0.0 so other devices on your Wi-Fi can reach it too.
PORT="$PORT" exec ./.venv/bin/python -c "
import os, uvicorn, main
uvicorn.run(main.app, host='0.0.0.0', port=int(os.environ['PORT']))
"
