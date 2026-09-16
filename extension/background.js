/* Grab — background worker.
   Owns every batch so closing the popup never kills a download. State lives in
   chrome.storage.local, so a recycled service worker picks up where it left off. */

const DEFAULTS = { server: "http://localhost:8000", code: "", format: "mp4", quality: "1080" };

async function settings() {
  const s = await chrome.storage.sync.get(DEFAULTS);
  s.server = (s.server || DEFAULTS.server).replace(/\/+$/, "");
  return s;
}

async function getState() {
  const { batches = [], items = {} } = await chrome.storage.local.get({ batches: [], items: {} });
  return { batches, items };
}
const setState = (s) => chrome.storage.local.set(s);

async function api(path, init = {}) {
  const s = await settings();
  const headers = { "Content-Type": "application/json", ...(init.headers || {}) };
  if (s.code) headers["X-Access-Code"] = s.code;
  const res = await fetch(s.server + path, { ...init, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Server error ${res.status}`);
  return data;
}

async function startBatch(urls, format, quality) {
  const list = (Array.isArray(urls) ? urls : [urls]).filter(Boolean);
  if (!list.length) return;
  const d = await api("/api/batch", {
    method: "POST",
    body: JSON.stringify({ urls: list.join("\n"), format, quality }),
  });
  const st = await getState();
  st.batches.push(d.id);
  d.jobs.forEach((j) => { st.items[j.id] = { ...j, batch: d.id, saved: false }; });
  await setState(st);
  chrome.alarms.create("poll", { periodInMinutes: 0.5 });
  tick();
  return d;
}

async function tick() {
  const st = await getState();
  if (!st.batches.length) { await badge(); return; }

  const live = [];
  for (const id of st.batches) {
    let b;
    try {
      b = await api("/api/batch/" + id);
    } catch (e) {
      // 404 = expired on the server; drop it rather than polling forever.
      Object.values(st.items).forEach((it) => {
        if (it.batch === id && it.status !== "ready") { it.status = "error"; it.error = e.message; }
      });
      continue;
    }
    const s = await settings();
    for (const j of b.jobs) {
      const prev = st.items[j.id] || {};
      st.items[j.id] = { ...prev, ...j, batch: id };
      if (j.status === "ready" && !prev.saved) {
        st.items[j.id].saved = true;
        chrome.downloads.download({ url: `${s.server}/api/jobs/${j.id}/file` });
        notify(j.title, j.filename);
      }
    }
    if (!b.done) live.push(id);
  }

  st.batches = live;
  // Forget finished items after a while so the popup list stays short.
  const stale = Object.entries(st.items).filter(([, it]) => !live.includes(it.batch) && it.seen);
  stale.slice(0, Math.max(0, stale.length - 8)).forEach(([k]) => delete st.items[k]);
  Object.values(st.items).forEach((it) => { if (!live.includes(it.batch)) it.seen = true; });

  await setState(st);
  await badge();
  if (!live.length) chrome.alarms.clear("poll");
  else setTimeout(tick, 1500);
}

async function badge() {
  const { items } = await getState();
  const active = Object.values(items).filter((i) => i.status === "queued" || i.status === "working").length;
  await chrome.action.setBadgeText({ text: active ? String(active) : "" });
  await chrome.action.setBadgeBackgroundColor({ color: "#ff2d55" });
}

function notify(title, filename) {
  chrome.notifications.create({
    type: "basic",
    iconUrl: "icons/icon128.png",
    title: "Saved to your device",
    message: filename || title || "Download complete",
  });
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "mp4", title: "Grab as MP4 (video)", contexts: ["link", "page", "video"] });
    chrome.contextMenus.create({ id: "mp3", title: "Grab as MP3 (audio)", contexts: ["link", "page", "video"] });
  });
});

chrome.contextMenus.onClicked.addListener(async (info) => {
  const url = info.linkUrl || info.pageUrl;
  if (!/youtube\.com|youtu\.be/.test(url || "")) return;
  const s = await settings();
  try {
    await startBatch(url, info.menuItemId, s.quality);
  } catch (e) {
    notify("", "Failed: " + e.message);
  }
});

chrome.alarms.onAlarm.addListener((a) => { if (a.name === "poll") tick(); });

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    try {
      if (msg.type === "start") reply({ ok: true, data: await startBatch(msg.urls, msg.format, msg.quality) });
      else if (msg.type === "clear") { await setState({ batches: [], items: {} }); await badge(); reply({ ok: true }); }
      else reply({ ok: false, error: "unknown message" });
    } catch (e) {
      reply({ ok: false, error: e.message });
    }
  })();
  return true; // keep the channel open for the async reply
});
