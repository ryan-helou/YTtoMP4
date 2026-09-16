# Grab — YouTube → MP4 / MP3

Paste one or more YouTube links, pick MP4 or MP3, and the files download to your device.
FastAPI + yt-dlp + ffmpeg, containerized for Railway.

## What it does

- **Bulk**: paste many links (one per line, or comma/space separated) — each gets its own
  progress row, its own Save button, and there's a **Download all · .zip** for the batch.
- **Playlists & channels** expand into their individual videos (capped by `MAX_BATCH`).
- **MP4** prefers H.264 + AAC so files play natively on iPhone, Mac and Windows.
- **MP3** is 192 kbps with title/artist metadata and embedded cover art.
- Files live in a temp dir, get streamed to you, then a reaper deletes them (~30 min TTL).
  Nothing is stored, no database.

## Run locally

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python main.py     # http://localhost:8000
```

Requires `ffmpeg` on PATH (`brew install ffmpeg`). The Docker image installs it for you.

## Deploy to Railway

```bash
railway init          # or: railway link
railway up
railway domain        # public URL
```

`railway.json` pins the Dockerfile builder and the `/healthz` healthcheck.

## Environment variables

| Variable | Default | What it does |
|---|---|---|
| `ACCESS_CODE` | _(unset)_ | If set, the page asks for this code before anything runs. Recommended for a public URL. |
| `YTDLP_COOKIES` | _(unset)_ | Contents of a Netscape `cookies.txt`. **Fixes "Sign in to confirm you're not a bot".** |
| `MAX_CONCURRENT` | `2` | Videos processed at once. Raise only with more RAM/CPU. |
| `MAX_BATCH` | `25` | Max videos per batch / playlist. |
| `MAX_DURATION_SECONDS` | `7200` | Per-video length limit. |
| `JOB_TTL_SECONDS` | `1800` | How long finished files stay downloadable. |
| `YTDLP_PLAYER_CLIENTS` | _(unset)_ | e.g. `tv,web_safari` — override yt-dlp's YouTube client if extraction breaks. |
| `ALLOW_ANY_SITE` | `false` | Allow non-YouTube sites yt-dlp supports. Off by default so the URL isn't an open proxy. |

### If YouTube blocks the server

Datacenter IPs (Railway included) get bot-checked far more than home connections. The fix:

1. In a logged-in browser, export cookies for `youtube.com` in **Netscape** format
   (any "Get cookies.txt" extension).
2. Paste the whole file into the `YTDLP_COOKIES` variable in Railway → Variables.
3. Redeploy. `/healthz` reports `"cookies": true` when it's loaded.

Use a throwaway Google account — those cookies are full session credentials.

### Keeping it running

yt-dlp breaks whenever YouTube changes something. Bump it periodically:

```bash
railway redeploy      # rebuilds, pulling the latest yt-dlp per requirements.txt
```

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/batch` | `{urls, format, quality}` → `{id, jobs[]}` |
| `GET /api/batch/{id}` | batch progress |
| `GET /api/batch/{id}/zip` | every finished file as one zip |
| `GET /api/jobs/{id}/file` | one finished file |
| `GET /healthz` | health + active job count |

## Note

Only download content you own or have the right to save. YouTube's Terms of Service
restrict downloading third-party content without permission.
