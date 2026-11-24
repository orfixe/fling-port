// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"use strict";

const api = globalThis.browser ?? globalThis.chrome;
const DEFAULTS = { port: 47613, shareTabs: true, pauseAfterCast: true };
const t = (k, ...s) => api.i18n.getMessage(k, s) || k;

for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);

async function check() {
  const r = await api.runtime.sendMessage({ type: "status" }).catch(() => null);
  const el = document.getElementById("state");
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
  check();
})();
