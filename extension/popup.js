const DEFAULTS = { server: "http://localhost:8000", code: "", format: "mp4", quality: "1080" };
const $ = (id) => document.getElementById(id);
let fmt = "mp4", tabUrl = "";

const send = (msg) => chrome.runtime.sendMessage(msg);
function note(text, kind) { $("note").className = "note on " + kind; $("note").textContent = text; }
function esc(s) { return String(s == null ? "" : s); }
const mb = (b) => (b ? (b / 1048576).toFixed(1) + " MB" : "");

function setFmt(next) {
  fmt = next;
  $("f-mp4").setAttribute("aria-pressed", String(next === "mp4"));
  $("f-mp3").setAttribute("aria-pressed", String(next === "mp3"));
  $("quality").disabled = next === "mp3";
  chrome.storage.sync.set({ format: next });
}
$("f-mp4").onclick = () => setFmt("mp4");
$("f-mp3").onclick = () => setFmt("mp3");
$("quality").onchange = (e) => chrome.storage.sync.set({ quality: e.target.value });
$("opts").onclick = () => chrome.runtime.openOptionsPage();

(async () => {
  const s = await chrome.storage.sync.get(DEFAULTS);
  setFmt(s.format || "mp4");
  $("quality").value = s.quality || "1080";
  $("open").onclick = () => chrome.tabs.create({ url: s.server });

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  tabUrl = tab?.url || "";
  const isYT = /youtube\.com|youtu\.be/.test(tabUrl);
  const isList = /[?&]list=/.test(tabUrl) || /\/playlist|\/@|\/channel\//.test(tabUrl);
  $("where").textContent = isYT ? (isList ? "Playlist / channel — expands into every video" : (tab.title || tabUrl)) : "Not a YouTube page";
  $("grab").disabled = !isYT;
  $("grab").textContent = isList ? "Grab this playlist" : "Grab this video";
  $("scan").disabled = !isYT;
  render();
})();

$("grab").onclick = async () => {
  $("grab").disabled = true;
  const r = await send({ type: "start", urls: [tabUrl], format: fmt, quality: $("quality").value });
  if (!r?.ok) note(esc(r?.error || "Can't reach the server — is ./run.sh running on your Mac?"), "err");
  else note(`Queued ${r.data.count} video${r.data.count > 1 ? "s" : ""}. You can close this popup.`, "ok");
  $("grab").disabled = false;
};

$("scan").onclick = async () => {
  $("scan").disabled = true;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const [{ result: links = [] } = {}] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const ids = new Set();
        document.querySelectorAll('a[href*="/watch?v="]').forEach((a) => {
          const m = a.href.match(/[?&]v=([\w-]{11})/);
          if (m) ids.add(m[1]);
        });
        return [...ids].map((id) => "https://www.youtube.com/watch?v=" + id);
      },
    });
    if (!links.length) { note("No videos found on this page.", "err"); $("scan").disabled = false; return; }
    const r = await send({ type: "start", urls: links, format: fmt, quality: $("quality").value });
    if (!r?.ok) note(esc(r.error), "err");
    else note(`Queued ${r.data.count} of ${links.length} found. You can close this popup.`, "ok");
  } catch (e) {
    note(esc(e.message), "err");
  }
  $("scan").disabled = false;
};

async function render() {
  const { items = {} } = await chrome.storage.local.get({ items: {} });
  const rows = Object.values(items).sort((a, b) => (a.status === "ready") - (b.status === "ready"));
  $("list").innerHTML = rows.map((j) => {
    const cls = j.status === "ready" ? "done" : j.status === "error" ? "bad" : "";
    const sub = j.status === "ready" ? `Saved · ${mb(j.size)}`
      : j.status === "error" ? j.error
      : `${j.phase}${j.progress > 0 ? " · " + Math.round(j.progress) + "%" : ""}`;
    const bar = j.status === "working" || j.status === "queued"
      ? `<div class="mini"><i style="width:${Math.max(3, j.progress)}%"></i></div>` : "";
    const div = document.createElement("div");
    div.textContent = j.title || "…";
    const t = div.innerHTML;
    div.textContent = sub;
    return `<div class="it ${cls}"><div class="t">${t}</div><div class="s">${div.innerHTML}</div>${bar}</div>`;
  }).join("");
}
chrome.storage.onChanged.addListener((c, area) => { if (area === "local" && c.items) render(); });
