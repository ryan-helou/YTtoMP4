"""YT to MP4/MP3 — paste links, get files on your device.

A thin FastAPI wrapper around yt-dlp. Every request is a *batch*: one or more
links (playlists expand into their entries), each downloaded/transcoded into a
per-job temp dir by a bounded thread pool. The browser pulls each finished file
— or all of them as one zip — and a reaper deletes everything minutes later.
Nothing is persisted.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

# ---------------------------------------------------------------- config

BASE_DIR = Path(__file__).parent
WORK_DIR = Path(os.environ.get("WORK_DIR", tempfile.gettempdir())) / "ytdl-jobs"
ACCESS_CODE = os.environ.get("ACCESS_CODE", "").strip()
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "2"))
MAX_BATCH = int(os.environ.get("MAX_BATCH", "25"))
MAX_DURATION = int(os.environ.get("MAX_DURATION_SECONDS", "7200"))  # 2h
JOB_TTL = int(os.environ.get("JOB_TTL_SECONDS", "1800"))  # 30 min
ALLOW_ANY_SITE = os.environ.get("ALLOW_ANY_SITE", "").lower() in ("1", "true", "yes")

ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}

# Paste a Netscape-format cookies.txt into YTDLP_COOKIES (or base64 into
# YTDLP_COOKIES_B64). YouTube bot-blocks datacenter IPs outright — cookies are
# the fix. Railway's variable editor can mangle tabs, and a cookie file with
# spaces instead of tabs is silently ignored by yt-dlp, so repair it here and
# report what actually loaded rather than failing mysteriously later.
AUTH_COOKIES = {"SID", "__Secure-3PSID", "__Secure-1PSID", "SAPISID", "__Secure-3PAPISID"}


def load_cookies() -> tuple[str | None, dict[str, Any]]:
    raw = os.environ.get("YTDLP_COOKIES", "")
    if not raw.strip() and os.environ.get("YTDLP_COOKIES_B64", "").strip():
        try:
            raw = base64.b64decode(os.environ["YTDLP_COOKIES_B64"], validate=False).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            return None, {"error": f"YTDLP_COOKIES_B64 is not valid base64: {exc}"}
    if not raw.strip():
        return None, {}

    lines, names, expired = ["# Netscape HTTP Cookie File"], set(), 0
    now = time.time()
    for line in raw.replace("\r\n", "\n").replace("\\n", "\n").split("\n"):
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t") if "\t" in line else line.split(None, 6)
        if len(fields) != 7:
            continue
        lines.append("\t".join(fields))
        names.add(fields[5])
        try:
            exp = float(fields[4])
            if exp and exp < now:
                expired += 1
        except ValueError:
            pass

    if len(lines) == 1:
        return None, {"error": "No cookie lines parsed — is it Netscape format (7 tab-separated fields)?"}

    path = Path(tempfile.gettempdir()) / "yt-cookies.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return str(path), {
        "count": len(lines) - 1,
        "expired": expired,
        "authenticated": bool(names & AUTH_COOKIES),
    }


COOKIE_FILE, COOKIE_INFO = load_cookies()
if COOKIE_FILE:
    print(f"cookies: loaded {COOKIE_INFO['count']} "
          f"(authenticated={COOKIE_INFO['authenticated']}, expired={COOKIE_INFO['expired']})", flush=True)
elif COOKIE_INFO.get("error"):
    print(f"cookies: NOT loaded — {COOKIE_INFO['error']}", flush=True)

# Optional override, e.g. YTDLP_PLAYER_CLIENTS="tv,web_safari"
PLAYER_CLIENTS = [c.strip() for c in os.environ.get("YTDLP_PLAYER_CLIENTS", "").split(",") if c.strip()]

# Optional residential proxy (http://user:pass@host:port) — the other way past
# YouTube's datacenter-IP bot check when you'd rather not upload cookies.
PROXY = os.environ.get("YTDLP_PROXY", "").strip()

# BgUtils PO token provider, started next to us by entrypoint.sh. YouTube wants a
# "proof of origin" token from server IPs; without one, requests get bot-checked.
POT_PORT = os.environ.get("POT_PORT", "4416")
POT_BASE_URL = os.environ.get("POT_BASE_URL", f"http://127.0.0.1:{POT_PORT}")
POT_DISABLED = os.environ.get("POT_DISABLED", "").lower() in ("1", "true", "yes")
VERBOSE = os.environ.get("YTDLP_VERBOSE", "").lower() in ("1", "true", "yes")

WORK_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- state


@dataclass
class Job:
    id: str
    url: str
    fmt: Literal["mp4", "mp3"]
    quality: str
    status: str = "queued"  # queued | working | ready | error
    phase: str = "Queued"
    progress: float = 0.0
    title: str = ""
    thumbnail: str = ""
    error: str = ""
    path: Path | None = None
    size: int = 0
    created: float = field(default_factory=time.time)
    served: float | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id, "status": self.status, "phase": self.phase,
            "progress": round(self.progress, 1), "title": self.title or "…",
            "thumbnail": self.thumbnail, "error": self.error,
            "filename": self.path.name if self.path else "",
            "size": self.size,
        }


@dataclass
class Batch:
    id: str
    job_ids: list[str]
    fmt: str
    created: float = field(default_factory=time.time)
    zip_path: Path | None = None
    zip_lock: threading.Lock = field(default_factory=threading.Lock)


JOBS: dict[str, Job] = {}
BATCHES: dict[str, Batch] = {}
LOCK = threading.Lock()
POOL = ThreadPoolExecutor(max_workers=MAX_CONCURRENT, thread_name_prefix="ytdl")


def job_dir(job_id: str) -> Path:
    return WORK_DIR / job_id


def reaper() -> None:
    """Drop finished jobs a few minutes after they're served, and anything past TTL."""
    while True:
        time.sleep(60)
        now = time.time()
        with LOCK:
            stale = [
                j for j in JOBS.values()
                if now - j.created > JOB_TTL or (j.served and now - j.served > 900)
            ]
            for j in stale:
                JOBS.pop(j.id, None)
            dead_batches = [
                b for b in BATCHES.values()
                if now - b.created > JOB_TTL or all(jid not in JOBS for jid in b.job_ids)
            ]
            for b in dead_batches:
                BATCHES.pop(b.id, None)
        for j in stale:
            shutil.rmtree(job_dir(j.id), ignore_errors=True)
        for b in dead_batches:
            if b.zip_path:
                shutil.rmtree(b.zip_path.parent, ignore_errors=True)


threading.Thread(target=reaper, daemon=True).start()

# ---------------------------------------------------------------- yt-dlp


def base_opts() -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": not VERBOSE,
        "verbose": VERBOSE,
        "no_warnings": not VERBOSE,
        "noplaylist": True,
        "noprogress": True,
        "windowsfilenames": True,  # keeps names portable for any client OS
        "retries": 3,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "geo_bypass": True,
    }
    if COOKIE_FILE:
        opts["cookiefile"] = COOKIE_FILE
    extractor_args: dict[str, Any] = {}
    if PLAYER_CLIENTS:
        extractor_args["youtube"] = {"player_client": PLAYER_CLIENTS}
    if not POT_DISABLED:
        extractor_args["youtubepot-bgutilhttp"] = {"base_url": [POT_BASE_URL]}
    if extractor_args:
        opts["extractor_args"] = extractor_args
    if PROXY:
        opts["proxy"] = PROXY
    return opts


def pot_alive() -> bool:
    if POT_DISABLED:
        return False
    try:
        with urllib.request.urlopen(f"{POT_BASE_URL}/ping", timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def build_opts(job: Job, dest: Path) -> dict[str, Any]:
    opts = base_opts()
    opts["outtmpl"] = str(dest / "%(title).120B [%(id)s].%(ext)s")
    opts["progress_hooks"] = [lambda d: on_progress(job, d)]
    opts["postprocessor_hooks"] = [lambda d: on_postprocess(job, d)]

    if job.fmt == "mp3":
        opts["format"] = "bestaudio/best"
        opts["writethumbnail"] = True
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail", "already_have_thumbnail": False},
        ]
    else:
        h = job.quality
        cap = "" if h == "best" else f"[height<={h}]"
        # Order matters: H.264 video + AAC audio plays natively on iOS, macOS and
        # Windows. Only fall through to VP9/AV1 if no compatible stream exists.
        opts["format"] = "/".join([
            f"bestvideo{cap}[vcodec^=avc1]+bestaudio[acodec^=mp4a]",
            f"bestvideo{cap}[ext=mp4]+bestaudio[ext=m4a]",
            f"best{cap}[ext=mp4]",
            f"bestvideo{cap}+bestaudio",
            f"best{cap}",
            "best",
        ])
        opts["merge_output_format"] = "mp4"
        opts["postprocessors"] = [
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
            {"key": "FFmpegMetadata", "add_metadata": True},
        ]
    return opts


def on_progress(job: Job, d: dict[str, Any]) -> None:
    if d.get("status") == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        job.status = "working"
        job.progress = (done / total * 100) if total else 0.0
        vcodec = (d.get("info_dict") or {}).get("vcodec", "")
        job.phase = "Downloading audio" if vcodec == "none" and job.fmt == "mp4" else "Downloading"
    elif d.get("status") == "finished":
        job.progress = 100.0
        job.phase = "Processing"


def on_postprocess(job: Job, d: dict[str, Any]) -> None:
    if d.get("status") != "started":
        return
    pp = d.get("postprocessor", "")
    if "ExtractAudio" in pp:
        job.phase = "Converting to MP3"
    elif "Merger" in pp:
        job.phase = "Merging video + audio"
    elif "Remux" in pp:
        job.phase = "Finalizing MP4"
    else:
        job.phase = "Tagging"
    job.progress = 100.0


def pick_output(dest: Path, fmt: str) -> Path:
    """The finished artifact is the biggest non-sidecar file in the job dir."""
    sidecars = {".jpg", ".jpeg", ".png", ".webp", ".part", ".ytdl", ".temp", ".vtt", ".srt", ".json"}
    wanted = [p for p in dest.iterdir() if p.is_file() and p.suffix.lower() not in sidecars]
    exact = [p for p in wanted if p.suffix.lower() == f".{fmt}"]
    pool = exact or wanted
    if not pool:
        raise RuntimeError("Download produced no file")
    return max(pool, key=lambda p: p.stat().st_size)


def run_job(job: Job) -> None:
    dest = job_dir(job.id)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        job.status = "working"
        job.phase = "Reading video info"
        with yt_dlp.YoutubeDL(build_opts(job, dest)) as ydl:
            info = ydl.extract_info(job.url, download=False)
            if info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                if not entries:
                    raise RuntimeError("That playlist is empty")
                info = entries[0]
                job.url = info.get("webpage_url") or job.url
            job.title = info.get("title") or job.title or "video"
            job.thumbnail = info.get("thumbnail") or job.thumbnail
            duration = info.get("duration") or 0
            if info.get("is_live"):
                raise RuntimeError("Live streams aren't supported")
            if duration and duration > MAX_DURATION:
                raise RuntimeError(
                    f"Video is {duration // 60} min — the limit is {MAX_DURATION // 60} min"
                )
            job.phase = "Downloading"
            ydl.download([job.url])

        out = pick_output(dest, job.fmt)
        job.path = out
        job.size = out.stat().st_size
        job.progress = 100.0
        job.phase = "Ready"
        job.status = "ready"
    except Exception as exc:  # noqa: BLE001 — surfaced to the user
        if not job.title:
            job.title = job.url.rsplit("/", 1)[-1][:60]
        job.status = "error"
        job.phase = "Failed"
        job.error = humanize_error(exc)
        shutil.rmtree(dest, ignore_errors=True)


def humanize_error(exc: Exception) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).replace("ERROR: ", "").strip()
    msg = re.sub(r"^\[[\w:.-]+\]\s*[\w-]{5,}:\s*", "", msg)   # "[youtube:tab] PLxx: "
    msg = re.sub(r"^\[[\w:.-]+\]\s*", "", msg)                 # "[youtube] "
    msg = re.sub(r"^YouTube said:\s*", "", msg).strip()
    low = msg.lower()
    if "sign in to confirm" in low or ("bot" in low and "confirm" in low):
        if COOKIE_FILE:
            return ("YouTube rejected this server's cookies — they've likely expired. "
                    "Export a fresh cookies.txt and update YTDLP_COOKIES on Railway.")
        return ("YouTube blocks this server's IP until it's signed in. Set YTDLP_COOKIES "
                "on Railway to a cookies.txt exported from a logged-in browser "
                "(see the README), or set YTDLP_PROXY to a residential proxy.")
    if "private video" in low:
        return "That video is private."
    if "unavailable" in low or "not available" in low or "removed by the uploader" in low:
        return "Unavailable — removed, private, region-locked, or age-restricted."
    if "unsupported url" in low or "is not a valid url" in low:
        return "That doesn't look like a YouTube link."
    if "members-only" in low:
        return "Members-only video."
    if "does not exist" in low:
        return "That playlist doesn't exist (or is private)."
    if "age" in low and "confirm" in low:
        return "Age-restricted — add YTDLP_COOKIES on Railway to download it."
    return msg[:300] or "Download failed"


# ---------------------------------------------------------------- helpers

URL_RE = re.compile(r"https?://\S+|(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/\S+", re.I)


def clean_url(raw: str) -> str:
    raw = (raw or "").strip().strip(",;")
    if not raw:
        raise HTTPException(status_code=400, detail="Paste a link first")
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail=f"Not a valid link: {raw[:60]}")
    if not ALLOW_ANY_SITE and parsed.hostname.lower() not in ALLOWED_HOSTS:
        raise HTTPException(status_code=400, detail=f"Only YouTube links are allowed: {raw[:60]}")
    return raw


def split_urls(blob: str) -> list[str]:
    """Accept links separated by newlines, spaces, or commas; de-dupe, keep order."""
    found = URL_RE.findall(blob or "")
    if not found:
        found = [p for p in re.split(r"[\s,]+", blob or "") if p.strip()]
    out, seen = [], set()
    for raw in found:
        url = clean_url(raw)
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def expand(urls: list[str]) -> list[dict[str, str]]:
    """Turn playlist/channel links into their individual videos (flat, one call each)."""
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    flat = base_opts() | {"noplaylist": False, "extract_flat": "in_playlist", "playlistend": MAX_BATCH}
    for url in urls:
        is_list = ("list=" in url) or ("/playlist" in url) or ("/@" in url) or ("/channel/" in url)
        if not is_list:
            if url not in seen:
                seen.add(url)
                items.append({"url": url, "title": "", "thumbnail": ""})
            continue
        try:
            with yt_dlp.YoutubeDL(flat) as ydl:
                data = ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=humanize_error(exc)) from exc
        entries = [e for e in (data.get("entries") or []) if e] if data.get("_type") == "playlist" else [data]
        for e in entries:
            link = e.get("url") or e.get("webpage_url") or ""
            if link and not link.startswith("http"):
                link = f"https://www.youtube.com/watch?v={link}"
            if link and link not in seen:
                seen.add(link)
                items.append({
                    "url": link,
                    "title": e.get("title") or "",
                    "thumbnail": (e.get("thumbnails") or [{}])[0].get("url", "") if e.get("thumbnails") else "",
                })
        if len(items) >= MAX_BATCH:
            break
    return items[:MAX_BATCH]


# ---------------------------------------------------------------- app

app = FastAPI(title="YT to MP4/MP3", docs_url=None, redoc_url=None)

# Let the companion Chrome extension call the API, but nothing else cross-origin —
# a random web page shouldn't be able to drive someone's downloader.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class BatchRequest(BaseModel):
    urls: str
    format: Literal["mp4", "mp3"] = "mp4"
    quality: str = "1080"


class InfoRequest(BaseModel):
    url: str


def check_access(code: str | None) -> None:
    if ACCESS_CODE and (code or "").strip() != ACCESS_CODE:
        raise HTTPException(status_code=401, detail="Wrong access code")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((BASE_DIR / "static" / "index.html").read_text(encoding="utf-8"))


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    with LOCK:
        active = sum(1 for j in JOBS.values() if j.status in ("queued", "working"))
    return {
        "ok": True, "active": active,
        "cookies": bool(COOKIE_FILE), "cookieInfo": COOKIE_INFO,
        "proxy": bool(PROXY), "potoken": pot_alive(),
    }


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {"needsCode": bool(ACCESS_CODE), "maxMinutes": MAX_DURATION // 60, "maxBatch": MAX_BATCH}


@app.post("/api/info")
def info(req: InfoRequest, x_access_code: str | None = Header(default=None)) -> dict[str, Any]:
    check_access(x_access_code)
    url = clean_url(req.url)
    try:
        with yt_dlp.YoutubeDL(base_opts()) as ydl:
            data = ydl.extract_info(url, download=False)
        if data.get("_type") == "playlist":
            entries = [e for e in (data.get("entries") or []) if e]
            if not entries:
                raise HTTPException(status_code=400, detail="That playlist is empty")
            data = entries[0]
        return {
            "title": data.get("title") or "Untitled",
            "uploader": data.get("uploader") or data.get("channel") or "",
            "duration": data.get("duration") or 0,
            "thumbnail": data.get("thumbnail") or "",
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=humanize_error(exc)) from exc


@app.post("/api/batch")
def create_batch(req: BatchRequest, x_access_code: str | None = Header(default=None)) -> dict[str, Any]:
    check_access(x_access_code)
    urls = split_urls(req.urls)
    if not urls:
        raise HTTPException(status_code=400, detail="Paste at least one YouTube link")
    items = expand(urls)
    if not items:
        raise HTTPException(status_code=400, detail="Found no videos in those links")

    with LOCK:
        pending = sum(1 for j in JOBS.values() if j.status in ("queued", "working"))
        if pending + len(items) > MAX_BATCH * 2:
            raise HTTPException(status_code=429, detail="Server is busy — try again in a minute")
        jobs = []
        for it in items:
            job = Job(id=uuid.uuid4().hex[:12], url=it["url"], fmt=req.format, quality=req.quality)
            job.title = it["title"]
            job.thumbnail = it["thumbnail"]
            JOBS[job.id] = job
            jobs.append(job)
        batch = Batch(id=uuid.uuid4().hex[:12], job_ids=[j.id for j in jobs], fmt=req.format)
        BATCHES[batch.id] = batch

    for job in jobs:
        POOL.submit(run_job, job)
    return {"id": batch.id, "count": len(jobs), "jobs": [j.public() for j in jobs]}


@app.get("/api/batch/{batch_id}")
def batch_status(batch_id: str) -> dict[str, Any]:
    batch = BATCHES.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="That batch expired — start a new one")
    jobs = [JOBS[jid] for jid in batch.job_ids if jid in JOBS]
    ready = [j for j in jobs if j.status == "ready"]
    return {
        "id": batch.id,
        "jobs": [j.public() for j in jobs],
        "done": all(j.status in ("ready", "error") for j in jobs),
        "ready": len(ready),
        "total": len(jobs),
        "totalSize": sum(j.size for j in ready),
    }


@app.get("/api/batch/{batch_id}/zip")
def batch_zip(batch_id: str) -> FileResponse:
    batch = BATCHES.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="That batch expired — start a new one")

    with batch.zip_lock:
        if not (batch.zip_path and batch.zip_path.exists()):
            files = [
                JOBS[jid].path for jid in batch.job_ids
                if jid in JOBS and JOBS[jid].status == "ready" and JOBS[jid].path and JOBS[jid].path.exists()
            ]
            if not files:
                raise HTTPException(status_code=404, detail="Nothing finished to zip")
            zdir = WORK_DIR / f"zip-{batch.id}"
            zdir.mkdir(parents=True, exist_ok=True)
            zpath = zdir / f"grab-{len(files)}-{batch.fmt}.zip"
            used: set[str] = set()
            # ZIP_STORED: media is already compressed, so deflating just burns CPU.
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
                for f in files:
                    name, n = f.name, 1
                    while name in used:
                        name = f"{f.stem} ({n}){f.suffix}"
                        n += 1
                    used.add(name)
                    zf.write(f, arcname=name)
            batch.zip_path = zpath

    for jid in batch.job_ids:
        if jid in JOBS:
            JOBS[jid].served = time.time()
    return FileResponse(batch.zip_path, media_type="application/zip", filename=batch.zip_path.name)


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="That job expired — start a new one")
    return job.public()


@app.get("/api/jobs/{job_id}/file")
def job_file(job_id: str) -> FileResponse:
    job = JOBS.get(job_id)
    if not job or job.status != "ready" or not job.path or not job.path.exists():
        raise HTTPException(status_code=404, detail="File expired — start a new download")
    job.served = time.time()
    media = "audio/mpeg" if job.fmt == "mp3" else "video/mp4"
    return FileResponse(job.path, media_type=media, filename=job.path.name)


@app.exception_handler(HTTPException)
def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
