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
| `YTDLP_COOKIES` | _(unset)_ | Contents of a Netscape `cookies.txt`. **Required in production** — see below. |
| `YTDLP_COOKIES_B64` | _(unset)_ | Same thing base64-encoded, for when a dashboard mangles tabs. |
| `POT_DISABLED` | `false` | Turn off the in-container PO token provider. |
| `YTDLP_VERBOSE` | `false` | Full yt-dlp debug output into the Railway logs. |
| `MAX_CONCURRENT` | `2` | Videos processed at once. Raise only with more RAM/CPU. |
| `MAX_BATCH` | `25` | Max videos per batch / playlist. |
| `MAX_DURATION_SECONDS` | `7200` | Per-video length limit. |
| `JOB_TTL_SECONDS` | `1800` | How long finished files stay downloadable. |
| `YTDLP_PROXY` | _(unset)_ | `http://user:pass@host:port` — route yt-dlp through a residential proxy. The alternative to cookies. |
| `YTDLP_PLAYER_CLIENTS` | _(unset)_ | e.g. `tv,web_safari` — override yt-dlp's YouTube client if extraction breaks. |
| `ALLOW_ANY_SITE` | `false` | Allow non-YouTube sites yt-dlp supports. Off by default so the URL isn't an open proxy. |

### YouTube blocks the server — you must add cookies

Railway's datacenter IPs are blocked by YouTube outright: every video returns
*"Sign in to confirm you're not a bot"*. This was chased to the end on the live
deployment, so don't repeat the experiments:

| Attempted | Result |
|---|---|
| Alternate player clients (`tv`, `tv_simply`, `android_vr`, `mweb`, `web_embedded`, `web_safari`) | All `LOGIN_REQUIRED` |
| **BgUtils PO token provider** running in-container (`/healthz` → `potoken: true`) | Still `LOGIN_REQUIRED` |
| **Deno** JS runtime so yt-dlp can solve the JS challenge | Still `LOGIN_REQUIRED` |

The PO provider and Deno are both kept in the image — they're needed once you *are*
signed in — but they don't substitute for authentication. The IP is the problem.

**Fix A — cookies (free, ~2 min).** Order matters; YouTube rotates cookies on any tab
that stays open, which is why a normal-window export dies within hours:

1. Open a **private / incognito window** and log into YouTube. Use a **throwaway Google
   account** — this is a full session credential and the account can get flagged.
2. In that same tab, go to `https://www.youtube.com/robots.txt`.
3. Export `youtube.com` cookies with a "Get cookies.txt" extension (Netscape format).
4. **Close the private window without logging out.** Never reopen that session.
5. Railway → `grab-yt` → **Variables** → new variable `YTDLP_COOKIES`, paste the whole
   file. If the editor mangles the tabs, base64 the file instead and use
   `YTDLP_COOKIES_B64` (`base64 -i cookies.txt | pbcopy`).

The app repairs space-mangled pastes, skips junk lines, and tells you what it got:

```bash
curl -s https://grab-yt-production.up.railway.app/healthz
# {"cookies":true,"cookieInfo":{"count":24,"expired":0,"authenticated":true},...}
```

`authenticated: true` means real session cookies are present. If it says `false`, the
export missed the login cookies — redo it from the private window.

**Fix B — residential proxy (paid, no Google account).** Set `YTDLP_PROXY` to a
residential endpoint (Webshare, IPRoyal, Bright Data). A few dollars a month, nothing to
flag or expire.

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
