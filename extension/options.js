const D = { server: "http://localhost:8000", code: "" };
chrome.storage.sync.get(D).then((s) => {
  document.getElementById("server").value = s.server;
  document.getElementById("code").value = s.code;
});
document.getElementById("save").onclick = async () => {
  const server = document.getElementById("server").value.trim().replace(/\/+$/, "");
  const code = document.getElementById("code").value.trim();
  await chrome.storage.sync.set({ server: server || D.server, code });
  // A custom host (not *.up.railway.app / localhost) needs its permission granted.
  try {
    const origin = new URL(server || D.server).origin + "/*";
    await chrome.permissions.request({ origins: [origin] });
  } catch (_) {}
  const el = document.getElementById("saved");
  el.classList.add("on"); setTimeout(() => el.classList.remove("on"), 1600);
};
