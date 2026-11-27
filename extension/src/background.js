// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
// Fling browser extension — background.
// Talks only to the Fling desktop app on this computer (http://127.0.0.1:47613).
// Chrome/Edge run this as a service worker, Firefox and Safari as an event page.
"use strict";

const api = globalThis.browser ?? globalThis.chrome;
const DEFAULTS = { port: 47613, shareTabs: true, pauseAfterCast: true, keepRecent: true, recent: [] };
const RECENT_MAX = 8;
const VOL_STEP = 5;

// ------------------------------------------------------------ settings

async function settings() {
  const s = await api.storage.local.get(DEFAULTS);
  return { ...DEFAULTS, ...s };
}

function validPort(p) {
  const n = Number(p);
  return Number.isInteger(n) && n >= 1024 && n <= 65535 ? n : DEFAULTS.port;
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
  const port = validPort((await settings()).port);
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

// ------------------------------------------------------------ timestamps in links
// youtube.com/watch?v=…&t=1m30s, youtu.be/…?t=90, twitch.tv/videos/…?t=1h2m3s,
// vimeo.com/…#t=90s, video.mp4#t=90 (media fragment).

function parseClock(v) {
  if (v == null) return null;
  v = String(v).trim().toLowerCase();
  if (!v) return null;
  let m = /^(\d+(?:\.\d+)?)s?$/.exec(v);
  if (m) return Math.floor(parseFloat(m[1]));
  m = /^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/.exec(v);
  if (m && (m[1] || m[2] || m[3])) return (+m[1] || 0) * 3600 + (+m[2] || 0) * 60 + (+m[3] || 0);
  m = /^(?:(\d+):)?(\d{1,2}):(\d{2})$/.exec(v);
  if (m) return (+m[1] || 0) * 3600 + (+m[2]) * 60 + (+m[3]);
  return null;
}

function urlStart(raw) {
  let u;
  try { u = new URL(raw); } catch (e) { return null; }
  const hash = new URLSearchParams(u.hash.replace(/^#/, ""));
  const host = u.hostname.toLowerCase().replace(/^(www|m|music)\./, "");
  let v = null;
  if (host === "youtube.com" || host === "youtu.be") v = u.searchParams.get("t") || u.searchParams.get("start") || hash.get("t");
  else if (host.endsWith("twitch.tv")) v = u.searchParams.get("t");
  else v = hash.get("t") || (host.endsWith("vimeo.com") ? u.searchParams.get("t") : null);
  if (v && v.includes(",")) v = v.split(",")[0];        // media fragment t=start,end
  const s = parseClock(v);
  return s && s > 3 ? s : null;
}

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
  try { media = await probeTab(tab.id); } catch (e) { media = null; }   // the tab is reported either way
  const r = await fling("/v1/tab", {
    url: tab.url, title: tab.title || "", browser: browserName(),
    media: media ? {
      time: media.time, duration: media.duration, paused: media.paused, ended: media.ended, src: media.src || "",
    } : null,
  });
  if (r.ok) lastReported = key;
}

// A loading page fires onUpdated many times (url, title, favicon, status…): report once it settles.
let reportTimer = null;
function reportSoon() {
  clearTimeout(reportTimer);
  reportTimer = setTimeout(() => reportActiveTab(), 600);
}

api.tabs.onActivated.addListener(reportSoon);
api.tabs.onUpdated.addListener((id, info, tab) => {
  if (tab.active && (info.url || info.title || info.status === "complete")) reportSoon();
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
  const direct = /\.(mp4|m4v|webm|mkv|mp3|m4a|aac|ogg|oga|wav|flac|m3u8|mpd)(\?|#|$)/i;
  let best = null;
  for (const m of document.querySelectorAll("video, audio")) {
    const video = m.tagName === "VIDEO";
    const area = video ? m.clientWidth * m.clientHeight : 1;
    if (video && area < 1600 && m.paused && !(m.currentTime > 0)) continue;   // thumbnails, hover previews
    const ad = !!m.closest(".ad-showing, .ad-interrupting");  // YouTube: the ad's clock isn't the video's
    const dur = Number.isFinite(m.duration) && !ad ? m.duration : 0;
    const time = ad ? 0 : m.currentTime || 0;
    const score = (m.paused ? 0 : 1e9) + (time > 0 ? 1e8 : 0) + area;
    if (!best || score > best.score) {
      const src = m.currentSrc || m.src || "";
      best = {
        score, time, duration: dur, paused: m.paused, ended: m.ended, video,
        src: /^https?:/i.test(src) ? src : "",          // blob:/MediaSource streams can't be cast
      };
    }
  }
  if (best && !best.src) {
    // the page announces its own file (og:video) — handy for sites yt-dlp doesn't know
    const meta = document.querySelector('meta[property="og:video:secure_url"], meta[property="og:video:url"], meta[property="og:video"]');
    const u = meta && meta.content;
    if (u && /^https?:/i.test(u) && direct.test(u)) best.src = u;
  }
  if (best) {
    try {
      const md = navigator.mediaSession && navigator.mediaSession.metadata;
      if (md && md.title) best.title = String(md.title).slice(0, 200);
    } catch (e) { /* ignore */ }
  }
  return best;
}

function pauseMedia() {
  for (const m of document.querySelectorAll("video, audio")) {
    try { m.pause(); } catch (e) { /* ignore */ }
  }
}

// executeScript waits for every frame, and some never answer (ad and embed iframes on some
// YouTube videos, frames still loading): without a limit the popup's "Cast" stayed disabled and
// the app never heard of the tab. So: all frames for a moment, then the top frame alone.
function withTimeout(p, ms) {
  return Promise.race([p, new Promise((_, no) => setTimeout(() => no(new Error("timeout")), ms))]);
}

async function inject(tabId, func) {
  try {
    return await withTimeout(api.scripting.executeScript({ target: { tabId, allFrames: true }, func }), 1500);
  } catch (e) {
    try {   // some frames refused (cross-origin player) or never answered: the top frame alone still helps
      return await withTimeout(api.scripting.executeScript({ target: { tabId }, func }), 1000);
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

// "from this moment": the exact position, also near the start and the end (the app ignores ≤ 3 s)
function exactPoint(m) {
  if (!m || m.ended || !(m.time > 3)) return null;
  if (m.duration && m.duration - m.time < 2) return null;
  return Math.floor(m.time);
}

// ------------------------------------------------------------ badge

const DEFAULT_TITLE = "Fling";
let badgeTimer = null, titleTimer = null;

async function flash(text, color, why) {
  try {
    clearTimeout(badgeTimer);
    await api.action.setBadgeBackgroundColor({ color });
    await api.action.setBadgeText({ text });
    if (text !== "…") badgeTimer = setTimeout(() => api.action.setBadgeText({ text: "" }), 2500);
    // the reason stays in the button's tooltip for a while (no notifications permission needed)
    clearTimeout(titleTimer);
    await api.action.setTitle({ title: why ? `Fling: ${why}` : DEFAULT_TITLE });
    if (why) titleTimer = setTimeout(() => api.action.setTitle({ title: DEFAULT_TITLE }), 15000);
  } catch (e) { /* Safari may not support badges on all versions */ }
}

const failBadge = (r) => flash("!", "#ff453a",
  r && r.error && r.error !== "offline" ? r.error : api.i18n.getMessage("offlineTitle"));

// ------------------------------------------------------------ recent casts (kept in this browser only)

async function remember(url, title, incognito) {
  const s = await settings();
  if (!s.keepRecent || incognito) return;
  const list = (Array.isArray(s.recent) ? s.recent : []).filter((x) => x && x.url !== url);
  list.unshift({ url, title: (title || "").slice(0, 200), at: Date.now() });
  await api.storage.local.set({ recent: list.slice(0, RECENT_MAX) });
}

// ------------------------------------------------------------ casting

let castBusy = false;

// Sends the cast and waits until the app has finished it: the app answers as soon as it
// accepts the request, so without waiting a failure would look like success.
async function sendCast(body) {
  if (castBusy) return { ok: false, error: api.i18n.getMessage("casting") };
  castBusy = true;
  try {
    await flash("…", "#8e8e93");
    const r = await fling("/v1/action", { action: "cast", ...body }, 5000);
    let last = r;
    if (r && r.ok && !r.error) {
      for (let i = 0; i < 25; i++) {
        await new Promise((res) => setTimeout(res, 1000));
        const st = await fling("/v1/status");
        if (!st || st.offline) continue;
        last = st;
        if (st.error || !st.casting) break;
      }
    }
    if (last && last.ok && !last.error) await flash("▶", "#30d158");
    else await failBadge(last);
    return last;
  } finally {
    castBusy = false;
  }
}

// mode: "auto"  — where the page's player is (if well into the video), else the link's timestamp;
//       "start" — from the beginning;
//       "now"   — exactly where the page's player is.
async function castTab(tab, mode = "auto") {
  if (!tab || !isWeb(tab.url)) {
    const why = api.i18n.getMessage("notWebPage");
    await flash("!", "#ff9f0a", why);
    return { ok: false, error: why };
  }
  const media = await probeTab(tab.id);
  let start = null;
  if (mode === "now") start = exactPoint(media) ?? urlStart(tab.url);
  else if (mode === "auto") start = resumePoint(media) ?? (media && media.time > 0 ? null : urlStart(tab.url));
  const r = await sendCast({
    url: tab.url, title: (media && media.title) || tab.title || "",
    start, media_src: media && media.src ? media.src : "",
  });
  if (r && r.ok && !r.error) {
    remember(tab.url, tab.title, tab.incognito);
    const { pauseAfterCast } = await settings();
    if (pauseAfterCast && media && !media.paused) inject(tab.id, pauseMedia);
  }
  return r;
}

async function castUrl(url, title, incognito) {
  if (!isWeb(url)) {
    const why = api.i18n.getMessage("notWebPage");
    await flash("!", "#ff9f0a", why);
    return { ok: false, error: why };
  }
  const r = await sendCast({ url, title: title || "", start: urlStart(url) });
  if (r && r.ok && !r.error) remember(url, title, incognito);
  return r;
}

// ------------------------------------------------------------ quick remote actions (shortcuts, menus)

async function quick(body) {
  const r = await fling("/v1/action", body);
  if (!r || !r.ok || r.error) await failBadge(r);
  return r;
}

async function volumeStep(dir) {
  const st = await fling("/v1/status");
  if (!st || !st.ok) return failBadge(st);
  // a paired TV: its own volume (works in every app); otherwise the Cast volume
  if (st.remote && st.remote.paired && st.remote.on !== false) {
    return quick({ action: "key", key: dir > 0 ? "vol_up" : "vol_down" });
  }
  const level = Math.max(0, Math.min(100, (Number(st.volume) || 0) + dir * VOL_STEP));
  return quick({ action: "volume", level });
}

async function typeOnTv(text) {
  text = String(text || "").trim().slice(0, 500);
  if (!text) return undefined;
  const st = await fling("/v1/status");
  if (!st || !st.ok) return failBadge(st);
  if (!st.remote || !st.remote.paired) return flash("!", "#ff9f0a", api.i18n.getMessage("pairHint"));
  const r = await quick({ action: "text", text });
  if (r && r.ok && !r.error) await flash("✓", "#30d158");
  return r;
}

// keyboard shortcuts (only "cast-tab" has a default key; the rest are assigned in the browser)
api.commands.onCommand.addListener(async (cmd, tabArg) => {
  switch (cmd) {
    case "cast-tab": return castTab(tabArg || await focusedTab());
    case "cast-tab-now": return castTab(tabArg || await focusedTab(), "now");
    case "play-pause": return quick({ action: "play_pause" });
    case "seek-back": return quick({ action: "seek", seconds: -30 });
    case "seek-forward": return quick({ action: "seek", seconds: 30 });
    case "volume-up": return volumeStep(1);
    case "volume-down": return volumeStep(-1);
    default: return undefined;
  }
});

// ------------------------------------------------------------ right-click menu

const menus = api.contextMenus || api.menus;

async function buildMenus() {
  if (!menus) return;
  const m = (key) => api.i18n.getMessage(key);
  try { await menus.removeAll(); } catch (e) { /* nothing to remove */ }
  const items = [
    { id: "fling-page", title: m("menuCastPage"), contexts: ["page"] },
    { id: "fling-page-now", title: m("menuCastFromHere"), contexts: ["page"] },
    { id: "fling-link", title: m("menuCastLink"), contexts: ["link"] },
    { id: "fling-video", title: m("menuCastMedia"), contexts: ["video"] },
    { id: "fling-audio", title: m("menuCastAudio"), contexts: ["audio"] },
    { id: "fling-media-now", title: m("menuCastFromHere"), contexts: ["video", "audio"] },
    { id: "fling-text", title: m("menuTypeOnTv"), contexts: ["selection"] },
  ];
  for (const it of items) {
    try { menus.create(it, () => void api.runtime.lastError); } catch (e) { /* already there */ }
  }
}

menus?.onClicked.addListener((info, tab) => {
  const incognito = !!(tab && tab.incognito);
  switch (info.menuItemId) {
    case "fling-page": return castTab(tab);
    case "fling-page-now":
    case "fling-media-now": return castTab(tab, "now");
    case "fling-link": return castUrl(info.linkUrl, info.linkText || "", incognito);
    case "fling-video":
    case "fling-audio":
      // a plain file: cast it; a blob:/MSE player: cast the page, resume position included
      return isWeb(info.srcUrl) ? castUrl(info.srcUrl, tab && tab.title, incognito) : castTab(tab);
    case "fling-text": return typeOnTv(info.selectionText);
    default: return undefined;
  }
});

api.runtime.onInstalled.addListener(() => { buildMenus(); reportActiveTab(true); });
api.runtime.onStartup?.addListener(() => { buildMenus(); reportActiveTab(true); });

// ------------------------------------------------------------ popup / options ↔ background

api.runtime.onMessage.addListener((msg, sender, reply) => {
  if (sender && sender.id && sender.id !== api.runtime.id) return false;   // our own pages only
  (async () => {
    const body = (msg && msg.body) || {};
    switch (msg && msg.type) {
      case "status": return fling("/v1/status");
      case "action": return fling("/v1/action", msg.body, 5000);
      case "castTab": return castTab(await focusedTab(), body.mode || "auto");
      case "castUrl": {
        const tab = await focusedTab();
        return castUrl(body.url, body.title, !!(tab && tab.incognito));
      }
      case "probe": {
        const tab = await focusedTab();
        if (!tab || !isWeb(tab.url)) return { tab: null };
        const media = await probeTab(tab.id);
        return {
          tab: { url: tab.url, title: tab.title, incognito: !!tab.incognito },
          media, resume: resumePoint(media), now: exactPoint(media), linkStart: urlStart(tab.url),
        };
      }
      case "recent": {
        const s = await settings();
        return { ok: true, keep: s.keepRecent, recent: Array.isArray(s.recent) ? s.recent : [] };
      }
      case "clearRecent": await api.storage.local.set({ recent: [] }); return { ok: true };
      case "reportTab": await reportActiveTab(true); return { ok: true };
      default: return { ok: false, error: "unknown message" };
    }
  })().then(reply, (e) => reply({ ok: false, error: String(e) }));
  return true;   // async reply
});
