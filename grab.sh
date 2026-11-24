#!/bin/sh
# Download YouTube videos straight into a folder, resiliently.
#   ./grab.sh ~/Downloads/"plane videos" <url|id> ...
#
# -N 8 + chunked ranges beats YouTube's per-connection throttling (~10x on a
# slow line); --continue resumes the .part file when the connection drops.
YTDLP="$(dirname "$0")/.venv/bin/yt-dlp"
DEST="$1"; shift
mkdir -p "$DEST"
for u in "$@"; do
  case "$u" in http*) url="$u" ;; *) url="https://www.youtube.com/watch?v=$u" ;; esac
  "$YTDLP" --no-warnings --no-playlist --newline \
    --continue -N 8 --http-chunk-size 10M \
    --retries infinite --fragment-retries infinite \
    --retry-sleep 'linear=1:20:3' --socket-timeout 30 \
    -f "bestvideo[height<=1080][vcodec^=avc1]+bestaudio[acodec^=mp4a]/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]" \
    --merge-output-format mp4 --embed-metadata \
    -o "$DEST/%(title).120B [%(id)s].%(ext)s" \
    "$url" 2>&1 | grep -E "^\[download\] Destination|has already been downloaded|ERROR" | head -2
  f=$(ls -t "$DEST"/*.mp4 2>/dev/null | head -1)
  [ -n "$f" ] && printf "  SAVED  %5s  %s\n" "$(du -h "$f" | cut -f1 | tr -d ' ')" "$(basename "$f")"
done
