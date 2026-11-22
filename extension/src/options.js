"use strict";

const api = globalThis.browser ?? globalThis.chrome;
const DEFAULTS = { port: 47613, shareTabs: true, pauseAfterCast: true };
const t = (k, ...s) => api.i18n.getMessage(k, s) || k;

for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);

async function check() {
  const r = await api.runtime.sendMessage({ type: "status" }).catch(() => null);
  const el = document.getElementById("state");
  const nt = document.getElementById("notifyTrack");
  nt.disabled = !(r && r.ok);
  if (r && r.ok) nt.checked = r.notify_track !== false;
  if (r && r.ok) {
    el.className = "ok";
    el.textContent = t("optConnected", r.version || "?", r.os || "", r.device ? r.device.name : t("noDevice"));
  } else {
    el.className = "bad";
    el.textContent = t("offlineTitle");
  }
}

(async () => {
  const s = { ...DEFAULTS, ...(await api.storage.local.get(DEFAULTS)) };
  for (const k of ["shareTabs", "pauseAfterCast"]) {
    const el = document.getElementById(k);
    el.checked = !!s[k];
    el.addEventListener("change", () => api.storage.local.set({ [k]: el.checked }));
  }
  const port = document.getElementById("port");
  port.value = s.port;
  port.addEventListener("change", async () => {
    const v = Math.round(Number(port.value));
    if (v >= 1024 && v <= 65535) {
      await api.storage.local.set({ port: v });
      check();
    } else port.value = s.port;
  });
  document.getElementById("notifyTrack").addEventListener("change", (e) =>
    api.runtime.sendMessage({ type: "action", body: { action: "notify_track", on: e.target.checked } }));
  check();
})();
