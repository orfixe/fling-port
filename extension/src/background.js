// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
// Fling browser extension — background.
// Talks only to the Fling desktop app on this computer (http://127.0.0.1:47613).
// Chrome/Edge run this as a service worker, Firefox and Safari as an event page.
"use strict";

const api = globalThis.browser ?? globalThis.chrome;
const DEFAULTS = { port: 47613, shareTabs: true, pauseAfterCast: true };

// ------------------------------------------------------------ settings

async function settings() {
  const s = await api.storage.local.get(DEFAULTS);
  return { ...DEFAULTS, ...s };
}

function browserName() {
  const ua = navigator.userAgent;
  if (ua.includes("Edg/")) return "Edge";
  if (ua.includes("YaBrowser/")) return "Yandex";
  if (ua.includes("OPR/")) return "Opera";
  if (ua.includes("Vivaldi")) return "Vivaldi";
  if (ua.includes("Firefox/")) return "Firefox";
  if (ua.includes("Safari/") && !ua.includes("Chrome/")) return "Safari";
  return "Chrome";
}

// ------------------------------------------------------------ the desktop app

async function fling(path, body, timeoutMs = 3000) {
  const { port } = await settings();
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const init = body === undefined
      ? { method: "GET", signal: ctl.signal, cache: "no-store" }
      : {
          method: "POST", signal: ctl.signal, cache: "no-store",
          headers: { "Content-Type": "application/json", "X-Fling": "1" },
          body: JSON.stringify(body),
        };
    const r = await fetch(`http://127.0.0.1:${port}${path}`, init);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, error: data.error || `HTTP ${r.status}` };
    return data;
  } catch (e) {
    return { ok: false, offline: true, error: "offline" };
  } finally {
    clearTimeout(timer);
  }
}

const isWeb = (u) => typeof u === "string" && /^https?:\/\//i.test(u);

// ------------------------------------------------------------ active tab → app (so the panel's
// "Cast this tab" and the system-wide shortcut know what you're looking at)

let lastReported = "";

async function focusedTab() {
  // lastFocusedWindow, not currentWindow: the popup is the current window and has no tabs.
  try {
    const [tab] = await api.tabs.query({ active: true, lastFocusedWindow: true });
    return tab || null;
  } catch (e) {
    return null;
  }
}

async function reportActiveTab(force = false) {
  const { shareTabs } = await settings();
  if (!shareTabs) return;
  const tab = await focusedTab();
  if (!tab || !isWeb(tab.url) || tab.incognito) return;
  const key = `${tab.url}\n${tab.title}`;
  if (!force && key === lastReported) return;
  let media = null;
  try { media = await probeTab(tab.id); } catch (e) { media = null; }
  const r = await fling("/v1/tab", {
    url: tab.url, title: tab.title || "", browser: browserName(),
    media: media ? {
      time: media.time, duration: media.duration, paused: media.paused, ended: media.ended, src: media.src || "",
    } : null,
  });
  if (r.ok) lastReported = key;
}

api.tabs.onActivated.addListener(() => reportActiveTab());
api.tabs.onUpdated.addListener((id, info, tab) => {
  if (tab.active && (info.url || info.title || info.status === "complete")) reportActiveTab();
});
api.windows.onFocusChanged.addListener((w) => {
  if (w !== api.windows.WINDOW_ID_NONE) reportActiveTab(true);
});

// heartbeat: the app treats a report older than 3 minutes as "browser closed".
// Only while this browser is actually in front — otherwise Chrome and Firefox steal the tab from each other.
if (api.alarms) {
  api.alarms.create("fling-heartbeat", { periodInMinutes: 1 });
  api.alarms.onAlarm.addListener(async (a) => {
    if (a.name !== "fling-heartbeat") return;
    try {
      const w = await api.windows.getLastFocused();
      if (!w || !w.focused) return;
    } catch (e) {
      return;
    }
    reportActiveTab(true);
  });
}

// ------------------------------------------------------------ the page's player (resume position, stream URL)

// Runs inside the page (every frame we may access). Must be self-contained.
function probeMedia() {
  const els = [...document.querySelectorAll("video, audio")];
  let best = null;
  for (const m of els) {
    const dur = Number.isFinite(m.duration) ? m.duration : 0;
    const area = m.tagName === "VIDEO" ? (m.clientWidth * m.clientHeight) : 1;
    const score = (m.paused ? 0 : 1e9) + (m.currentTime > 0 ? 1e8 : 0) + area;
    if (!best || score > best.score) {
      const src = m.currentSrc || m.src || "";
      best = {
        score, time: m.currentTime || 0, duration: dur, paused: m.paused, ended: m.ended,
        src: /^https?:/i.test(src) ? src : "",          // blob:/MediaSource streams can't be cast
      };
    }
  }
  return best;
}

function pauseMedia() {
  for (const m of document.querySelectorAll("video, audio")) {
    try { m.pause(); } catch (e) { /* ignore */ }
  }
}

async function inject(tabId, func) {
  try {
    return await api.scripting.executeScript({ target: { tabId, allFrames: true }, func });
  } catch (e) {
    try {   // some frames refused (cross-origin player): the top frame alone still helps
      return await api.scripting.executeScript({ target: { tabId }, func });
    } catch (e2) {
      return [];
    }
  }
}

async function probeTab(tabId) {
  const results = await inject(tabId, probeMedia);
  let best = null;
  for (const r of results || []) {
    const m = r && r.result;
    if (m && (!best || m.score > best.score)) best = m;
  }
  return best;
}

function resumePoint(m) {
  if (!m || m.ended || !m.duration) return null;
  if (m.time < 5 || m.duration - m.time < 10) return null;
  return Math.floor(m.time);
}

// ------------------------------------------------------------ casting

async function flash(text, color) {
  try {
    await api.action.setBadgeBackgroundColor({ color });
    await api.action.setBadgeText({ text });
    setTimeout(() => api.action.setBadgeText({ text: "" }), 2500);
  } catch (e) { /* Safari may not support badges on all versions */ }
}

async function castTab(tab) {
  if (!tab || !isWeb(tab.url)) {
    await flash("!", "#ff9f0a");
    return { ok: false, error: api.i18n.getMessage("notWebPage") };
  }
  const media = await probeTab(tab.id);
  const body = {
    action: "cast", url: tab.url, title: tab.title || "",
    start: resumePoint(media), media_src: media && media.src ? media.src : "",
  };
  const r = await fling("/v1/action", body, 5000);
  // The app answers as soon as it accepts the request. Wait until the cast finishes
  // before the green badge and before pausing the page — otherwise a failure looks like success.
  let last = r;
  if (r && r.ok && !r.error) {
    for (let i = 0; i < 20; i++) {
      await new Promise((res) => setTimeout(res, 1000));
      const st = await fling("/v1/status");
      if (!st) continue;
      last = st;
      if (st.error || !st.casting) break;
    }
  }
  if (last && last.ok && !last.error) {
    await flash("▶", "#30d158");
    const { pauseAfterCast } = await settings();
    if (pauseAfterCast && media && !media.paused) inject(tab.id, pauseMedia);
  } else {
    await flash("!", "#ff453a");
  }
  return last;
}

async function castUrl(url, title) {
  if (!isWeb(url)) return { ok: false, error: api.i18n.getMessage("notWebPage") };
  const r = await fling("/v1/action", { action: "cast", url, title: title || "" }, 5000);
  await flash(r.ok ? "▶" : "!", r.ok ? "#30d158" : "#ff453a");
  return r;
}

// keyboard shortcut (Alt+Shift+C by default)
api.commands.onCommand.addListener(async (cmd, tabArg) => {
  if (cmd !== "cast-tab") return;
  const tab = tabArg || await focusedTab();
  castTab(tab);
});

// right-click menu
function buildMenus() {
  const menus = api.contextMenus || api.menus;
  if (!menus) return;
  menus.removeAll(() => {
    menus.create({ id: "fling-link", title: api.i18n.getMessage("menuCastLink"), contexts: ["link"] });
    menus.create({ id: "fling-media", title: api.i18n.getMessage("menuCastMedia"), contexts: ["video", "audio"] });
    menus.create({ id: "fling-page", title: api.i18n.getMessage("menuCastPage"), contexts: ["page"] });
  });
}
(api.contextMenus || api.menus)?.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "fling-link") castUrl(info.linkUrl, info.linkText || "");
  else if (info.menuItemId === "fling-media") {
    if (isWeb(info.srcUrl)) castUrl(info.srcUrl, tab && tab.title);
    else castTab(tab);                      // blob:/MSE player: cast the page, resume position included
  } else if (info.menuItemId === "fling-page") castTab(tab);
});

api.runtime.onInstalled.addListener(() => { buildMenus(); reportActiveTab(true); });
api.runtime.onStartup?.addListener(() => { buildMenus(); reportActiveTab(true); });

// ------------------------------------------------------------ popup ↔ background

api.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    switch (msg && msg.type) {
      case "status": return fling("/v1/status");
      case "action": return fling("/v1/action", msg.body, 5000);
      case "castTab": {
        return castTab(await focusedTab());
      }
      case "probe": {
        const tab = await focusedTab();
        if (!tab || !isWeb(tab.url)) return { tab: null };
        const media = await probeTab(tab.id);
        return { tab: { url: tab.url, title: tab.title }, media, resume: resumePoint(media) };
      }
      case "reportTab": await reportActiveTab(true); return { ok: true };
      default: return { ok: false, error: "unknown message" };
    }
  })().then(reply, (e) => reply({ ok: false, error: String(e) }));
  return true;   // async reply
});
