"use strict";

const api = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);
const t = (key, ...subs) => api.i18n.getMessage(key, subs) || key;

let st = null;            // last /v1/status
let probe = null;         // this tab's player (resume point)
let volDragging = false;
let busyCast = false;

function send(type, body) {
  return api.runtime.sendMessage({ type, body });
}

// ------------------------------------------------------------ i18n

for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
for (const el of document.querySelectorAll("[data-i18n-title]")) el.title = t(el.dataset.i18nTitle);
for (const el of document.querySelectorAll("[data-i18n-placeholder]")) el.placeholder = t(el.dataset.i18nPlaceholder);

// ------------------------------------------------------------ helpers

function clock(s) {
  if (s == null || !isFinite(s) || s < 0) return "0:00";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return (h ? `${h}:` : "") + `${mm}:${String(sec).padStart(2, "0")}`;
}

function hostOf(u) {
  try { return new URL(u).hostname.replace(/^www\./, ""); } catch (e) { return ""; }
}

async function act(body) {
  const r = await send("action", body);
  if (r && r.ok) render(r);
  else showError(r && r.error);
  return r;
}

function showError(msg) {
  const e = $("error");
  e.textContent = msg && msg !== "offline" ? msg : "";
  e.hidden = !e.textContent;
}

// ------------------------------------------------------------ render

function render(s) {
  if (!s || s.offline || !s.ok) {
    $("offline").hidden = false;
    $("main").hidden = true;
    return;
  }
  st = s;
  $("offline").hidden = true;
  $("main").hidden = false;

  // now playing
  const m = s.media || {};
  const live = ["PLAYING", "PAUSED", "BUFFERING"].includes(m.state);
  $("now").hidden = !live;
  if (live) {
    if (m.image && $("art").getAttribute("src") !== m.image) $("art").src = m.image;
    if (!m.image) $("art").removeAttribute("src");
    $("nowTitle").textContent = m.title || t("playing");
    $("nowSub").textContent = m.subtitle || m.app || "";
    const pct = m.elapsed && m.duration ? Math.min(100, (m.elapsed / m.duration) * 100) : 0;
    $("bar").style.width = `${pct}%`;
    $("el").textContent = clock(m.elapsed);
    $("rem").textContent = m.duration && m.elapsed != null && m.duration > m.elapsed ? `−${clock(m.duration - m.elapsed)}` : "";
    const playing = m.state === "PLAYING" || m.state === "BUFFERING";
    $("icoPause").hidden = !playing;
    $("icoPlay").hidden = playing;
  }

  // this tab
  renderTab();

  // volume
  if (!volDragging) {
    $("volume").value = s.volume ?? 50;
    $("volVal").textContent = s.volume ?? "";
  }

  // remote
  const r = s.remote || {};
  $("remote").hidden = !r.paired;
  $("remoteSetup").hidden = r.paired || !s.device;
  if (r.paired) {
    $("tvNow").textContent = r.on === false ? t("tvOff") : r.app ? t("nowApp", r.app) : (s.device ? s.device.name : "");
    const apps = $("apps");
    if (apps.childElementCount !== (s.apps || []).length) {
      apps.textContent = "";
      for (const name of s.apps || []) {
        const b = document.createElement("button");
        b.textContent = name;
        b.title = name;
        b.addEventListener("click", () => act({ action: "launch", app: name }));
        apps.appendChild(b);
      }
    }
    for (const b of apps.children) b.classList.toggle("on", b.textContent === r.app);
  }

  showError(s.error);

  // footer
  $("dot").classList.toggle("on", !!s.device);
  const sel = $("device");
  const want = JSON.stringify([s.devices, s.device && s.device.ip, s.scanning]);
  if (sel.dataset.sig !== want) {
    sel.dataset.sig = want;
    sel.textContent = "";
    if (!s.devices.length) {
      sel.add(new Option(s.scanning ? t("searching") : t("noDevice"), ""));
    }
    for (const d of s.devices) sel.add(new Option(d.name, d.ip, false, s.device && d.ip === s.device.ip));
    sel.add(new Option(t("rescan"), "__rescan"));
  }
}

function renderTab() {
  const tab = probe && probe.tab;
  $("tabTitle").textContent = tab ? (tab.title || tab.url) : t("notWebPage");
  $("tabHost").textContent = tab ? hostOf(tab.url) : "";
  const btn = $("castTab");
  btn.disabled = !tab || busyCast || !(st && st.device);
  const resume = probe && probe.resume;
  $("castLabel").textContent = busyCast ? t("casting")
    : resume ? t("resumeAt", clock(resume))
    : (st && st.panel === "casting") ? t("castTabInstead") : t("castTab");
  const why = $("castWhy");
  why.textContent = !tab ? t("openVideoPage") : (st && !st.catt) ? t("needCatt") : "";
  why.hidden = !why.textContent;
}

// ------------------------------------------------------------ wiring

document.addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b || b.disabled) return;
  if (b.dataset.key) act({ action: "key", key: b.dataset.key });
  else if (b.dataset.act === "seek") act({ action: "seek", seconds: Number(b.dataset.sec) });
  else if (b.dataset.act) {
    act({ action: b.dataset.act });
    if (b.dataset.act === "open_panel" || b.dataset.act === "pair_remote") window.close();
  }
});

$("castTab").addEventListener("click", async () => {
  busyCast = true;
  renderTab();
  const r = await send("castTab");
  busyCast = false;
  if (r && r.ok) render(r);
  else showError(r && r.error);
  renderTab();
});

$("volume").addEventListener("input", (e) => {
  volDragging = true;
  $("volVal").textContent = e.target.value;
});
$("volume").addEventListener("change", async (e) => {
  await act({ action: "volume", level: Number(e.target.value) });
  volDragging = false;
});

$("typeForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("typeText").value;
  if (v) act({ action: "text", text: v });
  $("typeText").value = "";
});

$("device").addEventListener("change", (e) => {
  const v = e.target.value;
  if (v === "__rescan") act({ action: "rescan" });
  else if (v) act({ action: "select_device", ip: v });
  e.target.dataset.sig = "";
});

$("opts").addEventListener("click", () => api.runtime.openOptionsPage());
$("retry").addEventListener("click", tick);

// keyboard: Space = play/pause, ←/→ = ±30 s (not while typing)
document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, select")) return;
  const casting = st && st.panel === "casting";
  if (e.code === "Space" && casting) { e.preventDefault(); act({ action: "play_pause" }); }
  else if (e.key === "ArrowLeft" && casting) act({ action: "seek", seconds: -30 });
  else if (e.key === "ArrowRight" && casting) act({ action: "seek", seconds: 30 });
});

// scroll anywhere over the remote = d-pad (like the desktop panel)
let acc = [0, 0], lastStep = 0;
$("remote").addEventListener("wheel", (e) => {
  e.preventDefault();
  acc[0] += e.deltaX; acc[1] += e.deltaY;
  if (Math.abs(acc[0]) > Math.abs(acc[1])) acc[1] = 0; else acc[0] = 0;
  const now = performance.now();
  if (now - lastStep < 90) return;
  const STEP = 55;
  if (Math.abs(acc[0]) >= STEP) {
    act({ action: "key", key: acc[0] > 0 ? "right" : "left" });
    acc[0] = 0; lastStep = now;
  } else if (Math.abs(acc[1]) >= STEP) {
    act({ action: "key", key: acc[1] > 0 ? "down" : "up" });
    acc[1] = 0; lastStep = now;
  }
}, { passive: false });

// shortcut label from the browser's own settings
api.commands?.getAll?.().then((cmds) => {
  const c = (cmds || []).find((x) => x.name === "cast-tab");
  $("castKey").textContent = c && c.shortcut ? c.shortcut : "";
}).catch(() => {});

// ------------------------------------------------------------ polling

async function tick() {
  try { render(await send("status")); } catch (e) { render(null); }
}

(async () => {
  send("reportTab");
  probe = await send("probe").catch(() => null);
  await tick();
  setInterval(tick, 1000);
  setInterval(async () => { probe = await send("probe").catch(() => probe); renderTab(); }, 4000);
})();
