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

## Chrome extension

`extension/` is a Manifest V3 companion that talks to your deployed server:

- **Right-click** any YouTube link or page → *Grab as MP4 / MP3*.
- **Popup**: grab the current video, or the current playlist/channel (the server expands it).
- **Scan page for all videos** — pulls every `/watch?v=` link off whatever page you're on
  (search results, subscriptions, a channel grid) and queues them all.
- Downloads land via Chrome's own download manager, with a notification per file.
  The background worker owns the queue, so closing the popup doesn't stop anything.

### Install (unpacked)

1. `chrome://extensions` → enable **Developer mode**
2. **Load unpacked** → select the `extension/` folder
3. Extension **Settings** → set the server URL (defaults to the Railway deployment) and
   the access code if you set one

> It can't go on the Chrome Web Store: Google's Developer Program Policies prohibit
> extensions that download content from YouTube, so a listing would be rejected or pulled.
> Loading it unpacked for your own use is unaffected.

The server only accepts cross-origin calls from `chrome-extension://` origins, so a random
web page can't drive your downloader.

## Environment variables

| Variable | Default | What it does |
|---|---|---|
| `ACCESS_CODE` | _(unset)_ | If set, the page asks for this code before anything runs. Recommended for a public URL. |
| `YTDLP_COOKIES` | _(unset)_ | Contents of a Netscape `cookies.txt`. **Fixes "Sign in to confirm you're not a bot".** |
| `MAX_CONCURRENT` | `2` | Videos processed at once. Raise only with more RAM/CPU. |
| `MAX_BATCH` | `25` | Max videos per batch / playlist. |
| `MAX_DURATION_SECONDS` | `7200` | Per-video length limit. |
| `JOB_TTL_SECONDS` | `1800` | How long finished files stay downloadable. |
| `YTDLP_PROXY` | _(unset)_ | `http://user:pass@host:port` — route yt-dlp through a residential proxy. The alternative to cookies. |
| `YTDLP_PLAYER_CLIENTS` | _(unset)_ | e.g. `tv,web_safari` — override yt-dlp's YouTube client if extraction breaks. |
| `ALLOW_ANY_SITE` | `false` | Allow non-YouTube sites yt-dlp supports. Off by default so the URL isn't an open proxy. |

### YouTube blocks the server — read this first

**This is not optional in practice.** Railway's datacenter IPs are bot-checked by YouTube,
so a fresh deploy returns *"Sign in to confirm you're not a bot"* on every video. Verified
on this deployment; alternate player clients (`tv`, `tv_simply`, `android_vr`) did not get
through. Two ways to fix it:

**A. Cookies (free, 2 minutes):**

1. In a logged-in browser, export cookies for `youtube.com` in **Netscape** format
   (any "Get cookies.txt" extension).
2. Paste the whole file into the `YTDLP_COOKIES` variable in Railway → Variables.
3. Redeploy. `/healthz` reports `"cookies": true` when it's loaded.

Use a throwaway Google account — those cookies are full session credentials, and a
flagged account is one you don't want to be your real one.

**B. Residential proxy (paid, no account):** set `YTDLP_PROXY` to a residential endpoint
(Webshare, IPRoyal, Bright Data). No Google account involved, but it costs a few dollars
a month.

Locally neither is needed — home IPs aren't gated.

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
