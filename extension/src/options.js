// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"use strict";

const api = globalThis.browser ?? globalThis.chrome;
const DEFAULTS = { port: 47613, shareTabs: true, pauseAfterCast: true, keepRecent: true };
const t = (k, ...s) => api.i18n.getMessage(k, s) || k;
const $ = (id) => document.getElementById(id);

for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);

// ------------------------------------------------------------ connection

async function check() {
  const r = await api.runtime.sendMessage({ type: "status" }).catch(() => null);
  const el = $("state");
  const notify = $("notify");
  if (r && r.ok) {
    el.className = "ok";
    el.textContent = t("optConnected", r.version || "?", r.os || "", r.device ? r.device.name : t("noDevice"));
    notify.disabled = false;
    notify.checked = r.notify_track !== false;
  } else {
    el.className = "bad";
    el.textContent = t("offlineTitle");
    notify.disabled = true;
  }
}

function portValue() {
  const raw = $("port").value.trim();
  const n = Number(raw);
  return /^\d+$/.test(raw) && n >= 1024 && n <= 65535 ? n : null;
}

async function savePort() {
  const n = portValue();
  $("port").classList.toggle("invalid", n == null);
  $("portErr").hidden = n != null;
  $("portErr").textContent = n == null ? t("optPortBad") : "";
  if (n == null) return;
  await api.storage.local.set({ port: n });
  check();
}

// ------------------------------------------------------------ shortcuts

async function showKeys() {
  const table = $("keys");
  let cmds = [];
  try { cmds = (await api.commands.getAll()) || []; } catch (e) { cmds = []; }
  table.textContent = "";
  for (const c of cmds) {
    if (!c.description || c.name.startsWith("_")) continue;
    const tr = table.insertRow();
    tr.insertCell().textContent = c.description;
    const td = tr.insertCell();
    td.className = "key";
    if (c.shortcut) {
      const k = document.createElement("kbd");
      k.textContent = c.shortcut;
      td.appendChild(k);
    } else {
      td.textContent = t("optNoShortcut");
    }
  }
}

$("editKeys").addEventListener("click", async () => {
  const ua = navigator.userAgent;
  try {
    if (api.commands.openShortcutSettings) {          // Firefox 137+
      await api.commands.openShortcutSettings();
      return;
    }
    if (!ua.includes("Firefox/")) {
      const page = ua.includes("Edg/") ? "edge://extensions/shortcuts"
        : ua.includes("OPR/") ? "opera://extensions/shortcuts"
        : ua.includes("YaBrowser/") ? "browser://extensions/shortcuts"
        : "chrome://extensions/shortcuts";
      await api.tabs.create({ url: page });
      return;
    }
  } catch (e) { /* fall through to the hint */ }
  $("keysHint").textContent = t(ua.includes("Firefox/") ? "optShortcutsFirefox" : "optShortcutsManual");
  $("keysHint").hidden = false;
});

// ------------------------------------------------------------ start

(async () => {
  const s = { ...DEFAULTS, ...(await api.storage.local.get(DEFAULTS)) };
  for (const k of ["shareTabs", "pauseAfterCast", "keepRecent"]) {
    const el = $(k);
    el.checked = !!s[k];
    el.addEventListener("change", async () => {
      await api.storage.local.set({ [k]: el.checked });
      if (k === "keepRecent" && !el.checked) await api.storage.local.set({ recent: [] });
    });
  }
  $("notify").addEventListener("change", async (e) => {
    const r = await api.runtime.sendMessage({ type: "action", body: { action: "notify_track", on: e.target.checked } })
      .catch(() => null);
    if (!r || !r.ok) check();
  });
  $("clearRecent").addEventListener("click", async (e) => {
    e.preventDefault();             // inside a <label>: don't toggle the checkbox
    await api.runtime.sendMessage({ type: "clearRecent" }).catch(() => null);
    e.target.disabled = true;
    e.target.textContent = t("cleared");
  });
  $("port").value = s.port;
  $("port").addEventListener("change", savePort);
  $("port").addEventListener("keydown", (e) => { if (e.key === "Enter") savePort(); });
  $("check").addEventListener("click", savePort);
  try { $("extId").textContent = new URL(api.runtime.getURL("")).host; } catch (e) { /* ignore */ }
  showKeys();
  check();
})();
