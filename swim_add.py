#!/usr/bin/env python3
"""Find songs on YouTube, convert to MP3, load them onto the Shokz.

    ./swim_add.py --dry "Artist - Title" ...     preview the matches
    ./swim_add.py "Artist - Title" ...           download + copy to the device
    ./swim_add.py -f songs.txt                   one "Artist - Title" per line

The matcher REFUSES to guess: a candidate must actually contain the requested
title, or the song is reported unmatched rather than silently substituted.
(Asking for "John Michel - ONEWAY" once returned Michael Jackson's "Speed
Demon", which is exactly what this guards against.)
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

YTDLP = str(Path(__file__).parent / ".venv/bin/yt-dlp")
DEVICE = Path("/Volumes/SWIM PRO")
STAGE = Path("/tmp/swim-stage")
BAD = re.compile(r"\b(live|cover|remix|reaction|sped up|slowed|nightcore|8d|karaoke|instrumental|mashup|performs?)\b", re.I)
# Talk-show uploads are live performances even when the title doesn't say "live".
TV = re.compile(r"tonight show|late (night|show)|fallon|colbert|kimmel|corden|ellen|saturday night live|jools holland|tiny desk", re.I)
OFFICIAL_AUDIO = re.compile(r"official audio|\(audio\)", re.I)
REUPLOAD = re.compile(r"lyrics?\b|\blyric video", re.I)


def norm(s: str) -> str:
    """Lowercase, strip everything but letters and digits."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def search(query: str, n: int = 6) -> list[dict]:
    out = subprocess.run(
        [YTDLP, "--no-warnings", "--flat-playlist", "--dump-json", f"ytsearch{n}:{query}"],
        capture_output=True, text=True, timeout=180,
    ).stdout
    rows = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows.append({
            "id": d.get("id", ""),
            "title": d.get("title") or "",
            "channel": d.get("channel") or d.get("uploader") or "",
            "duration": d.get("duration") or 0,
        })
    return rows


def pick(artist: str, title: str, rows: list[dict]) -> tuple[dict | None, str]:
    """Return (best, why). best is None when nothing genuinely matches."""
    want_title, want_artist = norm(title), norm(artist)
    scored = []
    for r in rows:
        t, c = norm(r["title"]), norm(r["channel"])
        dur = r["duration"] or 0
        # Hard gate: the result must actually be this song.
        if want_title not in t and want_title not in c:
            continue
        if dur and not (45 <= dur <= 900):
            continue
        s = 0
        on_artist_channel = bool(want_artist) and (want_artist in c or c in want_artist)
        if on_artist_channel:
            s += 6                                    # the artist's own upload
        if re.search(r" - topic$", r["channel"], re.I):
            s += 5                                    # label-uploaded official audio
        if want_artist and want_artist in t:
            s += 2                                    # artist named in the title
        if OFFICIAL_AUDIO.search(r["title"]):
            s += 2
        if REUPLOAD.search(r["title"]) and not on_artist_channel:
            s -= 2                                    # lyrics-video reupload
        if BAD.search(r["title"]) or TV.search(r["title"]) or TV.search(r["channel"]):
            s -= 6                                    # live / cover / talk-show
        scored.append((s, r))
    if not scored:
        return None, "no result actually contained that title"
    scored.sort(key=lambda x: -x[0])
    best_score, best = scored[0]
    if best_score < 0:
        return None, "only live/cover/remix versions found"
    if re.search(r" - topic$", best["channel"], re.I) or (
            want_artist and norm(best["channel"]) and want_artist in norm(best["channel"])):
        why = "official channel"
    elif best_score >= 2:
        why = "good match"
    else:
        why = "REUPLOAD — no official source found"
    return best, why


def fetch(artist_title: str, vid: str) -> Path | None:
    STAGE.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[/:*?"<>|\\]', "-", artist_title).strip()
    subprocess.run(
        [YTDLP, "--no-warnings", "--quiet", "--no-playlist",
         "-f", "bestaudio[ext=m4a]/bestaudio",
         "-x", "--audio-format", "mp3", "--audio-quality", "0",
         "--embed-metadata", "--embed-thumbnail",
         "-N", "8", "--continue", "--retries", "infinite", "--fragment-retries", "infinite",
         "-o", str(STAGE / f"{safe}.%(ext)s"),
         f"https://www.youtube.com/watch?v={vid}"],
        capture_output=True, text=True, timeout=900,
    )
    f = STAGE / f"{safe}.mp3"
    return f if f.exists() else None


def main() -> int:
    global DEVICE
    args = sys.argv[1:]
    dry = "--dry" in args
    args = [a for a in args if a != "--dry"]
    if "--dest" in args:                      # send somewhere other than the Shokz
        i = args.index("--dest")
        DEVICE = Path(args[i + 1]).expanduser()
        DEVICE.mkdir(parents=True, exist_ok=True)
        del args[i:i + 2]
    if args[:1] == ["-f"]:
        args = [l.strip() for l in Path(args[1]).read_text().splitlines() if l.strip()]

    added, skipped = [], []
    for song in args:
        artist, _, title = song.partition(" - ")
        if not title:
            artist, title = "", song
        best, why = pick(artist, title, search(song))
        if not best:
            print(f"  SKIP      {song:44.44}  ({why})")
            skipped.append(song)
            continue
        d = best["duration"] or 0
        print(f"  {'match ' if dry else 'get   '}    {song:44.44} -> {best['title'][:42]:42} "
              f"[{best['channel'][:18]}, {d//60}:{d%60:02d}, {why}]")
        if dry:
            continue
        f = fetch(song, best["id"])
        if not f:
            print(f"      FAILED to download {song}")
            skipped.append(song)
            continue
        if DEVICE.is_dir():
            shutil.copy2(f, DEVICE / f.name)
            print(f"      ADDED {f.name}  ({f.stat().st_size/1048576:.1f} MB)")
        added.append(f.name)

    if not dry and DEVICE.is_dir() and str(DEVICE).startswith("/Volumes/"):
        subprocess.run(["dot_clean", str(DEVICE)], capture_output=True)
        for junk in DEVICE.glob("._*"):
            junk.unlink(missing_ok=True)
    print(f"\n{len(added)} added, {len(skipped)} skipped")
    if skipped:
        print("skipped: " + ", ".join(skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
