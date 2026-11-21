#!/usr/bin/env python3
"""Fling for Linux — tray caster + TV remote. A port of the macOS Fling panel
(menu-bar popover) to KDE Plasma: cast the current Firefox tab to a
Chromecast / Google TV via catt, and drive the TV over the Android TV Remote
protocol (androidtvremote2)."""
from __future__ import annotations

import ast
import asyncio
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, parse_qs

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")  # popups need real positioning

def _xwayland_scale():
    """KWin 'apply scaling themselves' for XWayland: match the desktop scale."""
    try:
        import configparser
        c = configparser.ConfigParser()
        c.read(os.path.expanduser("~/.config/kwinrc"))
        return float(c.get("Xwayland", "Scale", fallback="1"))
    except Exception:
        return 1.0

if os.environ["QT_QPA_PLATFORM"] == "xcb" and "QT_SCALE_FACTOR" not in os.environ:
    os.environ["QT_SCALE_FACTOR"] = str(_xwayland_scale())
    os.environ["QT_FONT_DPI"] = "96"

from PyQt6.QtCore import (QObject, Qt, QTimer, QRectF, QPoint, QSize, QUrl,
                          pyqtSignal)
from PyQt6.QtGui import (QAction, QColor, QFont, QIcon, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap, QKeySequence, QShortcut,
                         QCursor, QGuiApplication)
from PyQt6.QtNetwork import (QLocalServer, QLocalSocket, QNetworkAccessManager,
                             QNetworkRequest)
from PyQt6.QtWidgets import (QApplication, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QMenu, QPushButton, QSizePolicy,
                             QSystemTrayIcon, QVBoxLayout, QWidget, QFrame,
                             QSlider, QToolButton)

HOME = os.path.expanduser("~")
CATT = shutil.which("catt") or f"{HOME}/.local/bin/catt"
DATA = f"{HOME}/.local/share/fling"
CONF = f"{HOME}/.config/fling.json"
AUTOSTART = f"{HOME}/.config/autostart/fling.desktop"
SOCKET = "fling-panel"
SEEK = 30
GLOBAL_SHORTCUT = "Meta+Shift+C"
SHORTCUT_LABEL = "Meta ⇧ C"

# ---------------------------------------------------------------- config

def load_conf() -> dict:
    try:
        with open(CONF) as f:
            return json.load(f)
    except Exception:
        return {}

def save_conf(c: dict) -> None:
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    tmp = CONF + ".tmp"
    with open(tmp, "w") as f:
        json.dump(c, f, indent=1, ensure_ascii=False)
    os.replace(tmp, CONF)

# ---------------------------------------------------------------- URL classifier (URLClassifier.swift)

MEDIA_EXT = {"mp4", "m4v", "webm", "ts", "mp3", "m4a", "aac", "ogg", "oga", "wav",
             "flac", "m3u8", "mpd", "mkv"}
YOUTUBE = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
EXTRACTABLE = {"vimeo.com", "player.vimeo.com", "twitch.tv", "www.twitch.tv",
               "dailymotion.com", "www.dailymotion.com", "twitter.com", "x.com",
               "reddit.com", "www.reddit.com", "soundcloud.com",
               "rutube.ru", "vk.com", "vkvideo.ru", "ok.ru", "www.bilibili.com"}

def classify(raw: str | None) -> tuple[str, str | None]:
    """→ (kind, reason). kind: youtube | direct | site | maybe | none"""
    if not raw:
        return "none", "Нет открытой страницы"
    if os.path.isfile(raw):
        return "direct", None
    u = urlparse(raw)
    if u.scheme not in ("http", "https") or not u.hostname:
        return "none", "Это не веб-страница"
    host = u.hostname.lower()
    if host in YOUTUBE:
        return "youtube", None
    if os.path.splitext(u.path)[1].lstrip(".").lower() in MEDIA_EXT:
        return "direct", None
    if host in EXTRACTABLE:
        return "site", None
    # Fling blocks these; yt-dlp knows ~1800 sites, so let the user try.
    return "maybe", None

def youtube_id(raw: str) -> str | None:
    u = urlparse(raw or "")
    host = (u.hostname or "").lower()
    if host not in YOUTUBE:
        return None
    seg = [s for s in u.path.split("/") if s]
    if host == "youtu.be" and seg:
        return seg[0]
    if len(seg) >= 2 and seg[0] in ("embed", "shorts", "live"):
        return seg[1]
    return (parse_qs(u.query).get("v") or [None])[0]

def host_of(raw: str | None) -> str:
    if not raw:
        return "—"
    if os.path.isfile(raw):
        return "файл"
    h = urlparse(raw).hostname or "—"
    return h.removeprefix("www.")

def clock(t) -> str:
    if t is None or t != t or t < 0:
        return "0:00"
    t = int(round(t))
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

# ---------------------------------------------------------------- browser (Firefox session store)

def firefox_running() -> bool:
    return subprocess.run(["pgrep", "-x", "firefox"], capture_output=True).returncode == 0 or \
           subprocess.run(["pgrep", "-f", "/firefox/firefox"], capture_output=True).returncode == 0

def read_firefox_tab() -> dict | None:
    """Active tab of the most recently used Firefox window: {url,title}."""
    import lz4.block
    files = glob.glob(f"{HOME}/snap/firefox/common/.mozilla/firefox/*/sessionstore-backups/recovery.jsonlz4") + \
            glob.glob(f"{HOME}/.mozilla/firefox/*/sessionstore-backups/recovery.jsonlz4")
    if not files or not firefox_running():
        return None
    path = max(files, key=os.path.getmtime)
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:8] != b"mozLz40\0":
        return None
    data = json.loads(lz4.block.decompress(raw[8:]))
    wins = data.get("windows") or []
    if not wins:
        return None
    w = wins[max(0, min(len(wins), data.get("selectedWindow", 1)) - 1)]
    tabs = w.get("tabs") or []
    if not tabs:
        return None
    t = tabs[max(0, min(len(tabs), w.get("selected", 1)) - 1)]
    entries = t.get("entries") or []
    if not entries:
        return None
    e = entries[max(0, min(len(entries), t.get("index", len(entries))) - 1)]
    return {"url": e.get("url", ""), "title": e.get("title", ""), "browser": "Firefox"}

# ---------------------------------------------------------------- catt (CattClient.swift + CattParser.swift)

class CattError(Exception):
    pass

def catt_run(args: list[str], timeout: float = 25) -> str:
    try:
        r = subprocess.run([CATT, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise CattError("catt не ответил вовремя и был остановлен. Попробуйте ещё раз.")
    except FileNotFoundError:
        raise CattError("catt не установлен")
    out, err = r.stdout, r.stderr
    lines = [l.strip() for l in (out + "\n" + err).splitlines() if l.strip()]
    for l in lines:
        if l.startswith("Error:") and l[6:].strip():
            raise CattError(l[6:].strip())
    diag = [l for l in lines if not l.startswith("Title:")]
    if any("RequestTimeout" in l or "timed out" in l for l in diag):
        raise CattError("Телевизор не ответил. Убедитесь, что он включён, и попробуйте снова.")
    if r.returncode != 0:
        el = [l for l in err.splitlines() if l.strip()]
        raise CattError(el[-1] if el else f"catt завершился с ошибкой ({r.returncode})")
    return out

def catt_scan() -> list[dict]:
    out = catt_run(["scan"], timeout=30)
    devs = []
    for l in out.splitlines():
        parts = l.strip().split(" - ")
        if len(parts) >= 3 and not l.startswith("Scanning"):
            devs.append({"ip": parts[0], "name": " - ".join(parts[1:-1]), "model": parts[-1]})
    return devs

def catt_info(ip: str) -> dict:
    """Playback state from `catt info` (richer than status: artwork, content id)."""
    try:
        out = catt_run(["-d", ip, "info"], timeout=12)
    except CattError as e:
        if "Nothing is currently playing" in str(e) or "not playing" in str(e).lower():
            return {"state": "IDLE"}
        raise
    kv = {}
    for l in out.splitlines():
        if ": " in l:
            k, v = l.split(": ", 1)
            kv[k.strip()] = v.strip()
    def num(k):
        try:
            return float(kv[k])
        except Exception:
            return None
    meta = {}
    try:
        meta = ast.literal_eval(kv.get("media_metadata", "{}")) or {}
    except Exception:
        pass
    images = meta.get("images") or []
    vol = num("volume_level")
    return {
        "state": kv.get("player_state", "UNKNOWN").upper(),
        "elapsed": num("current_time"),
        "duration": num("duration"),
        "volume": int(round(vol * 100)) if vol is not None else None,
        "muted": kv.get("volume_muted") == "True",
        "title": meta.get("title") or None,
        "subtitle": meta.get("subtitle") or meta.get("artist") or kv.get("display_name"),
        "image": images[0].get("url") if images and isinstance(images[0], dict) else None,
        "content_id": kv.get("content_id"),
        "app": kv.get("display_name"),
    }

_OEMBED: dict = {}

def youtube_meta(vid: str) -> dict:
    """Title/channel for a YouTube id — the YouTube receiver reports none."""
    if vid in _OEMBED:
        return _OEMBED[vid]
    import urllib.request
    try:
        u = f"https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v={vid}"
        with urllib.request.urlopen(u, timeout=4) as r:
            d = json.load(r)
        _OEMBED[vid] = {"title": d.get("title"), "author": d.get("author_name")}
    except Exception:
        _OEMBED[vid] = {}
    return _OEMBED[vid]

# ---------------------------------------------------------------- Android TV Remote (androidtvremote2)

KEY = dict(power=26, home=3, back=4, up=19, down=20, left=21, right=22, ok=23,
           vol_up=24, vol_down=25, mute=164, play_pause=85, rewind=89, ffwd=90)

TV_APPS = [  # TVApp.swift catalog
    ("YouTube", "vnd.youtube.launch://", "com.google.android.youtube.tv"),
    ("Netflix", "netflix://", "com.netflix.ninja"),
    ("Prime Video", "https://app.primevideo.com", "com.amazon.amazonvideo.livingroom"),
    ("Disney+", "https://www.disneyplus.com", "com.disney.disneyplus"),
    ("Spotify", "spotify://", "com.spotify.tv.android"),
    ("Plex", "plex://", "com.plexapp.android"),
    ("Tubi", "https://tubitv.com/", "com.tubitv"),
]

def app_name(pkg: str | None) -> str | None:
    if not pkg:
        return None
    for name, _, p in TV_APPS:
        if p == pkg:
            return name
    tail = pkg.split(".")[-1]
    return tail[:1].upper() + tail[1:]


class ATV(QObject):
    """Owns an asyncio loop on a background thread; every public method is
    safe to call from the Qt thread and reports back through signals."""
    power = pyqtSignal(object)       # bool | None
    app = pyqtSignal(object)         # str | None
    pairing = pyqtSignal(str, str)   # state, message
    error = pyqtSignal(str)
    voice = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.remote = None
        self.host = None
        self.connected = False
        self._voice_stream = None
        self._rec = None

    def _dir(self, host):
        d = f"{DATA}/atv/{host}"
        os.makedirs(d, exist_ok=True)
        return d

    def paired(self, host) -> bool:
        return bool(host) and os.path.exists(f"{DATA}/atv/{host}/paired")

    def _make(self, host):
        from androidtvremote2 import AndroidTVRemote
        d = self._dir(host)
        return AndroidTVRemote("Fling (Linux)", f"{d}/cert.pem", f"{d}/key.pem", host,
                               loop=self.loop, enable_voice=True)

    def _go(self, coro):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        def done(f):
            e = f.exception()
            if e:
                self.error.emit(self.message(e))
        fut.add_done_callback(done)
        return fut

    @staticmethod
    def message(e: Exception) -> str:
        n = type(e).__name__
        if n == "InvalidAuth":
            return "Телевизор больше не доверяет этому компьютеру — настройте управление ТВ заново."
        if n == "CannotConnect":
            return f"Не удалось связаться с ТВ ({e})."
        if n == "ConnectionClosed":
            return "Телевизор закрыл соединение."
        if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
            return "Телевизор не ответил."
        if n == "VoiceSessionInProgress":
            return "Голосовой поиск уже идёт."
        return str(e) or n

    # pairing
    def start_pairing(self, host):
        async def run():
            self.pairing.emit("starting", "")
            try:
                if self.remote:
                    self.remote.disconnect()
                self.remote = self._make(host)
                self.host = host
                await self.remote.async_generate_cert_if_missing()
                await self.remote.async_start_pairing()
                self.pairing.emit("pin", "")
            except Exception as e:
                self.pairing.emit("failed", self.message(e))
        asyncio.run_coroutine_threadsafe(run(), self.loop)

    def finish_pairing(self, pin):
        async def run():
            self.pairing.emit("verifying", "")
            try:
                await self.remote.async_finish_pairing(pin)
                open(f"{self._dir(self.host)}/paired", "w").close()
                self.pairing.emit("idle", "")
                await self._connect(self.host)
            except Exception as e:
                if type(e).__name__ == "InvalidAuth":
                    self.pairing.emit("failed", "Код не совпадает — посмотрите на экран ТВ ещё раз.")
                else:
                    self.pairing.emit("failed", self.message(e))
        asyncio.run_coroutine_threadsafe(run(), self.loop)

    def cancel_pairing(self):
        async def run():
            if self.remote:
                self.remote.disconnect()
            self.remote = None
            self.connected = False
            self.pairing.emit("idle", "")
        asyncio.run_coroutine_threadsafe(run(), self.loop)

    # session
    async def _connect(self, host):
        if self.connected and self.host == host and self.remote:
            return
        if self.remote:
            self.remote.disconnect()
        self.remote = self._make(host)
        self.host = host
        self.remote.add_is_on_updated_callback(lambda on: self.power.emit(on))
        self.remote.add_current_app_updated_callback(lambda a: self.app.emit(a))
        def avail(ok):
            self.connected = ok
            if not ok:
                self.power.emit(None)
                self.app.emit(None)
        self.remote.add_is_available_updated_callback(avail)
        await self.remote.async_connect()
        self.connected = True
        self.remote.keep_reconnecting()
        self.power.emit(self.remote.is_on)
        self.app.emit(self.remote.current_app)

    def connect(self, host):
        if self.paired(host):
            self._go(self._connect(host))

    def _send(self, host, fn):
        async def run():
            await self._connect(host)
            fn(self.remote)
        return self._go(run())

    def key(self, host, code):
        self._send(host, lambda r: r.send_key_command(code))

    def launch(self, host, link):
        self._send(host, lambda r: r.send_launch_app_command(link))

    def text(self, host, s):
        self._send(host, lambda r: r.send_text(s))

    # voice: mic → 16-bit mono 8 kHz PCM → TV voice search
    def voice_start(self, host):
        rec = shutil.which("parecord") or shutil.which("pw-record")
        async def run():
            await self._connect(host)
            self._voice_stream = await self.remote.start_voice()
            if rec.endswith("parecord"):
                cmd = [rec, "--raw", "--format=s16le", "--rate=8000", "--channels=1"]
            else:
                cmd = [rec, "--format=s16", "--rate=8000", "--channels=1", "-"]
            self._rec = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            stream, proc, loop = self._voice_stream, self._rec, self.loop
            def pump():
                while True:
                    chunk = proc.stdout.read(8192)
                    if not chunk:
                        break
                    loop.call_soon_threadsafe(stream.send_chunk, chunk)
            threading.Thread(target=pump, daemon=True).start()
            self.voice.emit(True)
        if not rec:
            self.error.emit("Нет parecord/pw-record для записи микрофона.")
            return
        self._go(run())

    def voice_stop(self):
        async def run():
            if self._rec:
                self._rec.terminate()
                await asyncio.sleep(0.15)
                self._rec = None
            if self._voice_stream:
                self._voice_stream.end()
                self._voice_stream = None
            self.voice.emit(False)
        self._go(run())

# ---------------------------------------------------------------- app state (AppState.swift)

# ---------------------------------------------------------------- YouTube Lounge (what native YouTube on the TV is really playing)

_YT_STATE = {"Playing": "PLAYING", "Paused": "PAUSED", "Starting": "BUFFERING",
             "Advertisement": "PLAYING", "AdSkipped": "PLAYING", "Cued": "PAUSED"}


class Lounge(QObject):
    """Linked once with the TV's YouTube app ('Link with TV code'); then the
    TV pushes now-playing / position / state live, and accepts play, pause,
    seek, next, previous."""
    update = pyqtSignal(dict)
    linked = pyqtSignal(bool, str)

    def __init__(self, loop, conf: dict):
        super().__init__()
        self.loop, self.conf = loop, conf
        self.api = None
        self._task = None

    @property
    def is_linked(self):
        return bool(self.conf.get("yt_lounge"))

    def start(self):
        if self.is_linked and self._task is None:
            self._task = asyncio.run_coroutine_threadsafe(self._run(), self.loop)

    def _listener(self):
        from pyytlounge import EventListener
        outer = self

        def st(ev):
            return _YT_STATE.get(getattr(ev.state, "name", ""), "IDLE")

        class L(EventListener):
            async def now_playing_changed(self, ev):
                outer.update.emit({"video_id": ev.video_id, "elapsed": ev.current_time,
                                   "duration": ev.duration, "state": st(ev) if ev.video_id else "IDLE"})

            async def playback_state_changed(self, ev):
                outer.update.emit({"elapsed": ev.current_time, "duration": ev.duration, "state": st(ev)})

            async def disconnected(self, ev):
                outer.update.emit({"state": "IDLE", "video_id": None})
        return L()

    async def _run(self):
        from pyytlounge import YtLoungeApi
        while True:
            try:
                async with YtLoungeApi("Fling (Linux)", self._listener()) as api:
                    api.load_auth_state(self.conf["yt_lounge"])
                    self.api = api
                    while True:
                        if not api.linked():
                            await api.refresh_auth()
                            self._save(api)
                        if not api.connected():
                            if not await api.connect():
                                await api.refresh_auth()
                                self._save(api)
                                if not await api.connect():
                                    raise RuntimeError("YouTube на ТВ не отвечает")
                        sub = asyncio.ensure_future(api.subscribe())
                        await asyncio.sleep(1.0)
                        try:
                            await api.get_now_playing()
                        except Exception:
                            pass
                        await sub
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print("lounge:", type(e).__name__, e, flush=True)
            self.api = None
            await asyncio.sleep(5)

    def _save(self, api):
        self.conf["yt_lounge"] = api.store_auth_state()
        save_conf(self.conf)

    def pair(self, code: str):
        code = re.sub(r"\D", "", code)
        async def run():
            from pyytlounge import YtLoungeApi
            try:
                async with YtLoungeApi("Fling (Linux)") as api:
                    if not await api.pair(code):
                        raise RuntimeError("код не подошёл")
                    self._save(api)
                self.linked.emit(True, "")
                self.start()
            except Exception as e:
                self.linked.emit(False, f"Не удалось связать с YouTube: {e or type(e).__name__}. "
                                        "Проверьте код — он действует несколько минут.")
        asyncio.run_coroutine_threadsafe(run(), self.loop)

    def command(self, name, *args):
        api = self.api
        if api is None or not api.connected():
            return False
        asyncio.run_coroutine_threadsafe(getattr(api, name)(*args), self.loop)
        return True


# ---------------------------------------------------------------- Bluetooth remote (bt_remote.py service)

BT_SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "fling-bt.sock")
BT_NAMES = {26: "power", 3: "home", 4: "back", 19: "up", 20: "down", 21: "left", 22: "right",
            23: "ok", 24: "vol_up", 25: "vol_down", 164: "mute", 85: "play_pause", 89: "rewind", 90: "ffwd"}


def bt_call(cmd: str, timeout=1.5) -> dict:
    import socket as _s
    c = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM)
    c.settimeout(timeout)
    try:
        c.connect(BT_SOCK)
        c.sendall(cmd.encode())
        return json.loads(c.recv(65536).decode() or "{}")
    finally:
        c.close()


def bt_ensure_running():
    try:
        bt_call("status", 0.5)
    except OSError:
        os.makedirs(f"{HOME}/.cache/fling", exist_ok=True)
        subprocess.Popen(["/usr/bin/python3", f"{DATA}/bt_remote.py", "serve"],
                         stdout=open(f"{HOME}/.cache/fling/bt.log", "a"), stderr=subprocess.STDOUT,
                         start_new_session=True)


# ---------------------------------------------------------------- TV media session over ADB (real now-playing)

_THUMB: dict = {}
_ADB = shutil.which("adb") or f"{HOME}/Android/Sdk/platform-tools/adb"


def adb_now_playing(ip: str) -> dict | None:
    """Parse `dumpsys media_session` on the TV: the active app's real title,
    artist, play state and position. None when ADB isn't authorised."""
    serial = f"{ip}:5555"
    def sh(cmd, t=6):
        return subprocess.run([_ADB, "-s", serial, "shell", cmd], capture_output=True, text=True, timeout=t)
    r = sh("dumpsys media_session; echo __UP__; cat /proc/uptime")
    if r.returncode != 0 or "__UP__" not in r.stdout:
        subprocess.run([_ADB, "connect", serial], capture_output=True, timeout=6)
        r = sh("dumpsys media_session; echo __UP__; cat /proc/uptime")
        if r.returncode != 0 or "__UP__" not in r.stdout:
            return None
    body, up = r.stdout.split("__UP__", 1)
    uptime_ms = float(up.split()[0]) * 1000
    sessions, cur = [], None
    for line in body.splitlines():
        l = line.strip()
        if l.startswith("package="):
            cur = {"package": l[8:]}
            sessions.append(cur)
        elif cur is None:
            continue
        elif l.startswith("active="):
            cur["active"] = l.endswith("true")
        elif l.startswith("state=PlaybackState"):
            m = re.search(r"state=(\d+), position=(-?\d+).*?speed=([\d.]+), updated=(\d+)", l)
            if m:
                cur.update(st=int(m[1]), pos=int(m[2]), speed=float(m[3]), upd=int(m[4]))
        elif l.startswith("metadata:") and "description=" in l:
            parts = [x.strip() for x in l.split("description=", 1)[1].split(",")]
            # description = "title, subtitle, description" — titles may contain commas
            if len(parts) >= 3:
                cur["title"] = ", ".join(parts[:-2])
                cur["artist"] = None if parts[-2] == "null" else parts[-2]
            else:
                cur["title"] = parts[0]
    # playing (3) beats buffering (6) beats paused (2); skip Cast's stale mediashell when another app is live
    rank = {3: 0, 6: 1, 2: 2}
    live = [x for x in sessions if x.get("st") in rank and x.get("title") and x["title"] != "null"]
    if not live:
        return {"state": "IDLE"}
    live.sort(key=lambda x: (rank[x["st"]], x["package"] == "com.google.android.apps.mediashell"))
    x = live[0]
    pos = x["pos"] / 1000
    if x["st"] == 3:
        pos += max(0, uptime_ms - x["upd"]) / 1000 * x["speed"]
    return {"state": {3: "PLAYING", 6: "BUFFERING", 2: "PAUSED"}[x["st"]], "elapsed": pos,
            "title": x["title"], "artist": x.get("artist"), "package": x["package"]}


def youtube_lookup(title: str, artist: str | None) -> dict:
    """Best-effort artwork + duration for a title: first YouTube search hit."""
    key = f"{title}|{artist}"
    if key in _THUMB:
        return _THUMB[key]
    import urllib.parse, urllib.request
    out = {}
    try:
        q = urllib.parse.quote(f"{title} {artist or ''}".strip())
        req = urllib.request.Request(f"https://www.youtube.com/results?search_query={q}",
                                     headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"})
        html = urllib.request.urlopen(req, timeout=6).read().decode("utf-8", "ignore")
        m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', html)
        if m:
            out["video_id"] = m[1]
            out["image"] = f"https://img.youtube.com/vi/{m[1]}/hqdefault.jpg"
            seg = html[m.start(): m.start() + 6000]
            d = re.search(r'"lengthText":\{"accessibility":\{"accessibilityData":\{"label":"[^"]*"\}\},"simpleText":"([\d:]+)"', seg)
            if d:
                parts = [int(x) for x in d[1].split(":")]
                sec = 0
                for p_ in parts:
                    sec = sec * 60 + p_
                out["duration"] = sec
    except Exception:
        pass
    _THUMB[key] = out
    return out


class Bridge(QObject):
    call = pyqtSignal(object)


class State(QObject):
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.conf = load_conf()
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.bridge = Bridge()
        self.bridge.call.connect(lambda f: f())
        self.catt_ok = os.path.exists(CATT)
        self.tab = None
        self.status = {}
        self.volume = 50
        self.devices = self.conf.get("devices", [])
        self.device = self.conf.get("device") or (self.devices[0] if self.devices else None)
        self.cast_in_flight = False
        self.last_error = None
        self.tv_pairing = "idle"     # idle | starting | pin | verifying | failed
        self.tv_pairing_msg = ""
        self.tv_on = None
        self.tv_app = None
        self.tv_voice = False
        self._refreshing = False
        self._scanned = False
        self._vol_timer = QTimer(singleShot=True, interval=180)
        self._vol_timer.timeout.connect(self._send_volume)
        self.atv = ATV()
        self.atv.power.connect(lambda v: self._set(tv_on=v))
        self.atv.app.connect(lambda v: self._set(tv_app=v))
        self.atv.error.connect(lambda m: self._set(last_error=m))
        self.atv.voice.connect(lambda v: self._set(tv_voice=v))
        self.atv.pairing.connect(self._on_pairing)
        self.yt = {}
        self.bt = {}
        self.tvm = {}            # now-playing from the TV over ADB
        self._tvm_busy = False
        self.bt_setup = False
        bt_ensure_running()
        self.yt_pairing = "idle"   # idle | entry | linking
        self.lounge = Lounge(self.atv.loop, self.conf)
        self.lounge.update.connect(self._on_yt)
        self.lounge.linked.connect(self._on_yt_linked)
        self.lounge.start()

    # --- YouTube lounge
    def _on_yt(self, d):
        old = self.yt.get("video_id")
        self.yt.update({k: v for k, v in d.items() if v is not None or k == "video_id"})
        self.yt["at"] = time.monotonic()
        vid = self.yt.get("video_id")
        if vid and vid != old and vid not in _OEMBED:
            self.bg(lambda: youtube_meta(vid), lambda r, e: self.changed.emit())
        self.changed.emit()

    def _on_yt_linked(self, ok, msg):
        self.yt_pairing = "idle" if ok else "entry"
        self.last_error = None if ok else msg
        self.changed.emit()

    def yt_link(self, code):
        self.yt_pairing = "linking"
        self.changed.emit()
        self.lounge.pair(code)

    @property
    def yt_live(self):
        return bool(self.yt.get("video_id")) and self.yt.get("state") in ("PLAYING", "PAUSED", "BUFFERING")

    @property
    def tvm_live(self):
        return self.tvm.get("state") in ("PLAYING", "PAUSED", "BUFFERING")

    def tv_screenshot(self):
        """Grab the TV screen over ADB → ~/Pictures/Fling/tv-*.png and open it."""
        ip = self.ip
        if not ip:
            self._set(last_error="Устройство не выбрано")
            return
        def work():
            st = adb_state(ip)
            if st != "device":
                return {"error": {"unauthorized": "Снимок экрана: подтвердите на ТВ «Разрешить отладку».",
                                  "closed": "Снимок экрана работает через ADB — включите его: «Подключение ТВ…» → ADB."}
                        .get(st, "Снимок экрана: ADB не подключён — откройте «Подключение ТВ…».")}
            r = subprocess.run([_ADB, "-s", f"{ip}:5555", "exec-out", "screencap", "-p"],
                               capture_output=True, timeout=20)
            if r.returncode != 0 or not r.stdout.startswith(b"\x89PNG"):
                return {"error": "Снимок экрана не получился: " + (r.stderr.decode(errors="ignore").strip() or "нет данных")}
            pics = subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True, text=True).stdout.strip() or f"{HOME}/Pictures"
            folder = os.path.join(pics, "Fling")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, time.strftime("tv-%Y%m%d-%H%M%S.png"))
            with open(path, "wb") as f:
                f.write(r.stdout)
            from PyQt6.QtGui import QImage
            im = QImage(path).scaled(48, 27)
            bright = max(im.pixelColor(x, y).lightness() for x in range(im.width()) for y in range(im.height()))
            return {"path": path, "black": bright < 8}
        def done(r, e):
            if e or not r:
                self._set(last_error=f"Снимок экрана: {e}")
                return
            if r.get("error"):
                self._set(last_error=r["error"])
                return
            subprocess.Popen(["xdg-open", r["path"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r["black"]:
                self._set(last_error="Снимок чёрный: видео защищено от снимков (DRM у YouTube/Netflix). "
                                     "Меню и главный экран ТВ снимаются нормально.")
            else:
                notify_send(f"Снимок экрана ТВ: {r['path']}")
                self._set(last_error=None)
        self.bg(work, done)

    def poll_tvm(self):
        ip = self.ip
        if not ip or self._tvm_busy:
            return
        self._tvm_busy = True
        def work():
            d = adb_now_playing(ip)
            if d and d.get("title"):
                d.update(youtube_lookup(d["title"], d.get("artist")))
            return d
        def done(d, e):
            self._tvm_busy = False
            if d is not None:
                d["at"] = time.monotonic()
                self.tvm = d
                self.changed.emit()
        self.bg(work, done)

    @property
    def status(self):
        if not self.yt_live and self.tvm_live:
            m = self.tvm
            el = m.get("elapsed")
            if m["state"] == "PLAYING" and el is not None:
                el += time.monotonic() - m["at"]
            du = m.get("duration")
            if du and el:
                el = min(el, du)
            return {"state": m["state"], "elapsed": el, "duration": du,
                    "volume": self._cast_status.get("volume"), "title": m["title"],
                    "subtitle": " · ".join(x for x in (m.get("artist"), app_name(m.get("package"))) if x),
                    "image": m.get("image"), "content_id": m.get("video_id"), "app": app_name(m.get("package")),
                    "tvm": True}
        if not self.yt_live:
            return self._cast_status
        y = self.yt
        el, du = y.get("elapsed"), y.get("duration") or None
        if y.get("state") == "PLAYING" and el is not None:
            el = el + (time.monotonic() - y.get("at", time.monotonic()))
            if du:
                el = min(el, du)
        vid = y["video_id"]
        m = _OEMBED.get(vid) or {}
        return {"state": y["state"], "elapsed": el, "duration": du,
                "volume": self._cast_status.get("volume"),
                "title": m.get("title") or "YouTube",
                "subtitle": (f"{m['author']} · YouTube" if m.get("author") else "YouTube"),
                "image": f"https://img.youtube.com/vi/{vid}/hqdefault.jpg",
                "content_id": vid, "app": "YouTube", "lounge": True}

    @status.setter
    def status(self, v):
        self._cast_status = v

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.changed.emit()

    def _on_pairing(self, st, msg):
        self.tv_pairing, self.tv_pairing_msg = st, msg
        self.changed.emit()

    def bg(self, fn, done=None):
        def run():
            try:
                r, e = fn(), None
            except Exception as ex:
                r, e = None, ex
            if done:
                self.bridge.call.emit(lambda: done(r, e))
        self.pool.submit(run)

    # derived
    @property
    def ip(self):
        return self.device["ip"] if self.device else None

    @property
    def tv_paired(self):
        return self.atv.paired(self.ip)

    @property
    def has_media(self):
        return self.status.get("state") in ("PLAYING", "PAUSED", "BUFFERING")

    @property
    def is_playing(self):
        return self.status.get("state") in ("PLAYING", "BUFFERING")

    @property
    def panel(self):
        if not self.catt_ok:
            return "setup"
        if self.has_media:
            return "casting"
        kind, _ = classify(self.tab["url"] if self.tab else None)
        return "idle_castable" if kind != "none" else "idle_blocked"

    @property
    def block_reason(self):
        return classify(self.tab["url"] if self.tab else None)[1]

    @property
    def progress(self):
        e, d = self.status.get("elapsed"), self.status.get("duration")
        return min(1.0, e / d) if e and d else 0.0

    @property
    def elapsed_label(self):
        return clock(self.status.get("elapsed"))

    @property
    def remaining_label(self):
        e, d = self.status.get("elapsed"), self.status.get("duration")
        return "−" + clock(d - e) if e is not None and d and d > e else ""

    # refresh
    def refresh(self, with_tab=True):
        if self._refreshing:
            return
        self._refreshing = True
        ip = self.ip
        def work():
            tab = None
            if with_tab:
                try:
                    tab = read_firefox_tab()
                except Exception:
                    tab = None
            st = None
            if ip:
                try:
                    st = catt_info(ip)
                except Exception:
                    st = {}
                cid = st.get("content_id") or ""
                if st.get("title") and not st.get("image") and not re.fullmatch(r"[\w-]{11}", cid):
                    # YouTube-app Cast sessions: title but no artwork/id — find it by title
                    found = youtube_lookup(st["title"], st.get("subtitle"))
                    st["image"] = found.get("image")
                    if not st.get("duration"):
                        st["duration"] = found.get("duration")
                if not st.get("title") and re.fullmatch(r"[\w-]{11}", cid):
                    m = youtube_meta(cid)
                    st["title"] = m.get("title")
                    if m.get("author"):
                        st["subtitle"] = f"{m['author']} · YouTube"
            return tab, st
        def done(r, e):
            self._refreshing = False
            if r:
                tab, st = r
                if with_tab:
                    self.tab = tab
                if st is not None and ip == self.ip:
                    self.status = st
                    if st.get("volume") is not None and not self._vol_timer.isActive():
                        self.volume = st["volume"]
            self.changed.emit()
            if not self._scanned:
                self.scan()
        self.bg(work, done)

    def scan(self):
        self._scanned = True
        def done(found, e):
            if not found:
                return
            self.devices = found
            if not self.device or self.device["ip"] not in [d["ip"] for d in found]:
                same = [d for d in found if self.device and d["name"] == self.device["name"]]
                self.device = same[0] if same else found[0]
            self.conf.update(devices=found, device=self.device)
            save_conf(self.conf)
            self.atv.connect(self.ip)
            self.changed.emit()
            self.refresh()
        self.bg(catt_scan, done)

    def select_device(self, d):
        self.device = d
        self.status = {}
        self.conf["device"] = d
        save_conf(self.conf)
        self.last_error = None
        self.atv.connect(self.ip)
        self.changed.emit()
        self.refresh()

    # actions
    def _catt(self, args, timeout=25, after=None):
        ip = self.ip
        if not ip:
            self._set(last_error="Устройство не выбрано")
            return
        def done(r, e):
            self.last_error = str(e) if e else None
            if after:
                after()
            self.changed.emit()
            QTimer.singleShot(400, lambda: self.refresh(with_tab=False))
        self.bg(lambda: catt_run(["-d", ip, *args], timeout=timeout), done)

    def cast(self, url, notify=False):
        kind, reason = classify(url)
        if kind == "none":
            self._set(last_error=reason)
            if notify:
                notify_send(f"Нельзя отправить: {reason}")
            return
        if self.cast_in_flight:
            return
        self.cast_in_flight = True
        self.changed.emit()
        opts = ["-f"] if kind == "direct" and not os.path.isfile(url) else []
        def after():
            self.cast_in_flight = False
            if notify:
                notify_send(self.last_error or f"На ТВ: {url[:100]}")
        self._catt(["cast", *opts, url], timeout=120, after=after)

    def cast_tab(self, notify=False):
        def done(tab, e):
            if not tab:
                self._set(last_error="Нет открытой страницы")
                if notify:
                    notify_send("Нет открытой вкладки Firefox")
                return
            self.tab = tab
            self.cast(tab["url"], notify)
        self.bg(read_firefox_tab, done)

    def cast_clipboard(self, notify=False):
        txt = (QApplication.clipboard().text() or "").strip()
        if txt.startswith("file://"):
            txt = QUrl(txt).toLocalFile()
        if not txt:
            self._set(last_error="Буфер обмена пуст")
            return
        self.cast(txt, notify)

    @property
    def cast_stuck(self):
        """Native YouTube on Google TV reports a frozen BUFFERING session over
        Cast and silently ignores pause/seek sent that way."""
        if self.yt_live or self.tvm_live:
            return False
        return self.status.get("state") == "BUFFERING" and not self.status.get("elapsed")

    def toggle_play(self):
        if self.yt_live:
            playing = self.yt.get("state") == "PLAYING"
            if self.lounge.command("pause" if playing else "play"):
                self._on_yt({"state": "PAUSED" if playing else "PLAYING",
                             "elapsed": self.status.get("elapsed")})
                return
        if self.tvm_live and self.remote_ready:
            self.tv_key(KEY["play_pause"])
            self.tvm["state"] = "PAUSED" if self.tvm["state"] == "PLAYING" else "PLAYING"
            self.tvm["elapsed"] = self.status.get("elapsed"); self.tvm["at"] = time.monotonic()
            self.changed.emit()
            QTimer.singleShot(1200, self.poll_tvm)
            return
        if self.remote_ready:         # remote key works in every TV app
            self.tv_key(KEY["play_pause"])
            self.status["state"] = "PAUSED" if self.is_playing else "PLAYING"
            self.changed.emit()
            return
        before = self.status.get("state")
        want = "pause" if self.is_playing else "play"
        def check():
            if self.status.get("state") == before and self.cast_stuck:
                self._set(last_error="Телевизор игнорирует паузу по Chromecast (так ведёт себя YouTube на ТВ). "
                                     "Нажмите «Настроить управление ТВ…» ниже — тогда пауза пойдёт как кнопка пульта.")
        self._catt([want], after=lambda: QTimer.singleShot(1500, check))

    def seek(self, s):
        if self.yt_live:
            t = max(0.0, (self.status.get("elapsed") or 0) + s)
            if self.lounge.command("seek_to", t):
                self._on_yt({"elapsed": t})
                return
        if self.remote_ready and (self.cast_stuck or self.tvm_live):
            QTimer.singleShot(1500, self.poll_tvm)
            for _ in range(max(1, abs(s) // 10)):   # YouTube TV: one press = 10 s
                self.tv_key(KEY["ffwd"] if s > 0 else KEY["rewind"])
            return
        self._catt(["ffwd" if s > 0 else "rewind", str(abs(s))])

    def stop(self):
        if self.yt_live and self.lounge.command("pause"):
            self._on_yt({"state": "PAUSED"})
            return
        self._catt(["stop"])

    def yt_next(self):
        self.lounge.command("next")

    def yt_prev(self):
        self.lounge.command("previous")

    def set_volume(self, v):
        self.volume = max(0, min(100, int(v)))
        self._vol_timer.start()
        self.changed.emit()

    def _send_volume(self):
        self._catt(["volume", str(self.volume)])

    def wake(self):
        ip = self.ip
        def work():
            try:
                catt_run(["-d", ip, "cast_site", "https://example.com"], timeout=15)
            except CattError:
                pass
            end = time.time() + 8
            while True:
                try:
                    catt_run(["-d", ip, "stop"]); return
                except CattError:
                    if time.time() > end:
                        raise
                    time.sleep(0.5)
        if ip:
            self.bg(work, lambda r, e: self._set(last_error=str(e) if e else None))

    # TV remote: network remote when paired, else the Bluetooth remote
    @property
    def bt_connected(self):
        return bool(self.bt.get("connected"))

    @property
    def remote_ready(self):
        return self.tv_paired or self.bt_connected

    def poll_bt(self):
        def done(r, e):
            new = r or {}
            if new != self.bt:
                was = self.bt_connected
                self.bt = new
                if self.bt_connected and not was:
                    self.bt_setup = False
                self.changed.emit()
        self.bg(lambda: bt_call("status"), done)

    def bt_pair(self):
        bt_ensure_running()
        self.bt_setup = True
        self.changed.emit()
        self.bg(lambda: bt_call("pair 180"),
                lambda r, e: self._set(last_error=f"Bluetooth: {e}") if e or (r and r.get("error")) else None)

    def _bt(self, cmd):
        def done(r, e):
            err = str(e) if e else (r or {}).get("error")
            if err:
                self._set(last_error=f"Bluetooth: {err}")
        self.bg(lambda: bt_call(cmd), done)

    def tv_key(self, code):
        if self.tv_paired and self.ip:
            self.atv.key(self.ip, code)
        elif self.bt_connected and code in BT_NAMES:
            self._bt(f"key {BT_NAMES[code]}")
        elif self.ip:
            self.atv.key(self.ip, code)

    def tv_launch(self, link):
        if self.ip:
            self.atv.launch(self.ip, link)

    def tv_text(self, s):
        if not s:
            return
        if not self.tv_paired and self.bt_connected:
            def done(r, e):
                if r and r.get("skipped"):
                    self._set(last_error="По Bluetooth можно печатать только латиницу и цифры.")
            self.bg(lambda: bt_call("text " + s), done)
        elif self.ip:
            self.atv.text(self.ip, s)

    def tv_voice_toggle(self):
        if self.tv_voice:
            self.atv.voice_stop()
        elif self.ip:
            self.atv.voice_start(self.ip)

    def start_pairing(self):
        if not self.ip:
            self._set(last_error="Устройство не выбрано")
            return
        self.atv.start_pairing(self.ip)

# ---------------------------------------------------------------- language (ru / en)

EN_PHRASES = [  # longest first is applied automatically
    ("⏳  Связываюсь с ТВ…", "⏳  Contacting TV…"),
    (" Без звука", " Mute"), (" Громк +", " Vol +"), (" Громк −", " Vol −"),
    ("Буфер обмена пуст", "Clipboard is empty"),
    ("Введите 12 цифр с экрана:", "Enter the 12 digits shown on screen:"),
    ("Введите код с экрана телевизора", "Enter the code on the TV screen"),
    ("Вкл/выкл ТВ", "Toggle TV power"), ("Включить ТВ", "Turn TV On"), ("Выключить ТВ", "Turn TV off"),
    ("Воспроизводится", "Playing"), ("Выйти из Fling", "Quit Fling"),
    ("Остановить голосовой поиск", "Stop voice search"),
    ("Голосовой поиск уже идёт.", "Voice search is already running."), ("Голосовой поиск", "Voice search"),
    ("Движок трансляции не установлен. Установите его один раз — Fling его найдёт.",
     "The Cast engine isn't installed. Install it once and Fling will find it."),
    ("Домой", "Home"), ("Запускать при входе", "Open at Login"),
    ("или крутите колёсико / тачпад в любом месте панели", "or scroll anywhere to move around the TV"),
    ("и тут появятся настоящие обложка, название и время.", "and the real artwork, title and time will show here."),
    ("код не подошёл", "the code didn't work"),
    ("Код не совпадает — посмотрите на экран ТВ ещё раз.", "That code doesn't match — read it off the TV again."),
    ("Код YouTube — это 12 цифр с экрана ТВ.", "The YouTube code is the 12 digits on the TV screen."),
    ("можно отправить", "castable"), ("можно попробовать", "worth a try"),
    ("Нажмите «Настроить управление ТВ…» ниже — тогда пауза пойдёт как кнопка пульта.",
     "Click “Set Up TV Power…” below and pause will be sent as a remote button."),
    ("Напечатать на ТВ", "Type on TV"), ("Настроить управление ТВ…", "Set Up TV Power…"),
    ("На ТВ: YouTube → Настройки → «Подключить телефон / Связать с ТВ по коду». ",
     "On the TV: YouTube → Settings → “Link with TV code”. "),
    ("НА ТЕЛЕВИЗОРЕ", "ON THE TV"),
    ("Нет открытой вкладки Firefox", "No Firefox tab open"), ("Нет открытой страницы", "No page open"),
    ("Нет parecord/pw-record для записи микрофона.", "parecord/pw-record not found for the microphone."),
    ("Остановить трансляцию", "Stop casting"),
    ("Откройте вкладку в Firefox и попробуйте снова.", "Open a tab in Firefox, then try again."),
    ("Отмена", "Cancel"), ("Отправить ссылку из буфера", "Cast clipboard URL"),
    ("Отправить эту вкладку вместо", "Cast this tab instead"), ("Отправить эту вкладку", "Cast this tab"),
    ("Отправляю…", "Casting…"), ("Пауза / Играть", "Play / Pause"), ("Пауза на ТВ", "Pause on TV"),
    ("Предыдущее видео", "Previous video"), ("Следующее видео", "Next video"), ("Пробел", "Space"),
    ("Проверьте код — он действует несколько минут.", "Check the code — it's valid for a few minutes."),
    ("Свяжите один раз с ТВ, чтобы открыть приложения, крестовину и ввод текста.",
     "Pair once with the TV to unlock apps, the d-pad, and typing."),
    ("Связать с YouTube на ТВ — видеть, что играет", "Link YouTube on the TV — see what's playing"),
    ("Связать", "Pair"), ("Скопировать команду установки", "Copy install command"),
    ("Слушаю — нажмите микрофон ещё раз, когда закончите.", "Listening — click the mic again when done."),
    ("Сменить", "Switch"), ("ТВ выключен", "TV is off"),
    ("ТВ не сообщает, что сейчас играет. Свяжите с YouTube ниже — ",
     "The TV doesn't report what's playing. Link YouTube below — "),
    ("Текст попадёт в активное поле ввода на телевизоре", "Lands in whatever text field is focused on the TV"),
    ("Телевизор больше не доверяет этому компьютеру — настройте управление ТВ заново.",
     "The TV no longer trusts this computer — run TV power setup again."),
    ("Телевизор закрыл соединение.", "The TV closed the connection."),
    ("Телевизор игнорирует паузу по Chromecast (так ведёт себя YouTube на ТВ). ",
     "The TV ignores pause over Chromecast (YouTube on the TV does that). "),
    ("Телевизор не ответил. Убедитесь, что он включён, и попробуйте снова.",
     "The TV did not respond. Make sure it is awake, then try again."),
    ("Телевизор не ответил.", "The TV didn't answer."),
    ("Устройство не выбрано", "No device selected"), ("Устройство не найдено", "No device found"),
    ("Устройство", "Device"), ("Это не веб-страница", "Not a web page"),
    ("catt не ответил вовремя и был остановлен. Попробуйте ещё раз.", "catt did not finish in time and was stopped. Try again."),
    ("catt не установлен", "catt is not installed"), ("Fling нужен catt", "Fling needs catt"),
    ("YouTube на ТВ не отвечает", "YouTube on the TV isn't answering"),
    ("Не удалось связать с YouTube: ", "Couldn't link YouTube: "),
    ("Не удалось связаться с ТВ", "Couldn't reach the TV"), ("Нельзя отправить: ", "Can't cast: "),
    ("catt завершился с ошибкой", "catt failed"), ("Язык: Русский", "Language: English"),
    ("Пауза", "Pause"), ("Играть", "Play"), ("Назад", "Back"), ("Вперёд", "Forward"),
    ("Громкость", "Volume"), ("Сейчас: ", "Now: "), ("На ТВ: ", "On TV: "), (" на ТВ", " on the TV"),
    ("файл", "file"),
    ("Подключить как Bluetooth-пульт…", "Connect as Bluetooth remote…"),
    ("Подключить как Bluetooth-пульт", "Connect as Bluetooth remote"),
    ("BLUETOOTH-ПУЛЬТ", "BLUETOOTH REMOTE"),
    ("1. На ТВ: Настройки → Пульты и аксессуары → Добавить аксессуар.\n2. Выберите «Fling Remote».\n3. Если KDE спросит про сопряжение — подтвердите.",
     "1. On the TV: Settings → Remotes & Accessories → Pair accessory.\n2. Choose “Fling Remote”.\n3. If KDE asks to confirm pairing — accept."),
    ("✓ Подключено: ", "✓ Connected: "),
    ("⏳ «Fling Remote» виден телевизору — ждёт подключения (3 мин).", "⏳ “Fling Remote” is visible to the TV — waiting for it (3 min)."),
    ("Служба Bluetooth-пульта не запущена.", "The Bluetooth remote service isn't running."),
    ("Окно сопряжения закрылось — нажмите «Подключить как Bluetooth-пульт» ещё раз.", "The pairing window closed — click “Connect as Bluetooth remote” again."),
    ("Bluetooth-пульт · ", "Bluetooth remote · "), ("Готово", "Done"),
    ("По Bluetooth можно печатать только латиницу и цифры.", "Over Bluetooth only Latin letters and digits can be typed."),
]
EN_PHRASES.sort(key=lambda kv: -len(kv[0]))
EN_REGEX = [(re.compile(r"(\d+) с\b"), r"\1s")]


def lang() -> str:
    l = load_conf().get("lang", "auto")
    if l == "auto":
        import locale
        l = "ru" if (locale.getlocale()[0] or os.environ.get("LANG", "")).lower().startswith("ru") else "en"
    return l


LANG = lang()


def tr(text):
    if LANG != "en" or not isinstance(text, str) or not text:
        return text
    for ru, en in EN_PHRASES:
        if ru in text:
            text = text.replace(ru, en)
    for rx, rep in EN_REGEX:
        text = rx.sub(rep, text)
    return text


def install_translation():
    """Route every user-visible Qt string setter through tr()."""
    if LANG != "en":
        return
    from PyQt6.QtWidgets import QAbstractButton
    def wrap(cls, name):
        orig = getattr(cls, name)
        def f(self, *a):
            a = list(a)
            for i, v in enumerate(a):
                if isinstance(v, str):
                    a[i] = tr(v)
                    break
            return orig(self, *a)
        setattr(cls, name, f)
    for cls, name in ((QLabel, "setText"), (QAbstractButton, "setText"), (QLineEdit, "setPlaceholderText"),
                      (QWidget, "setToolTip"), (QMenu, "addAction"), (QMenu, "addMenu"),
                      (QSystemTrayIcon, "setToolTip"), (QAction, "setText")):
        wrap(cls, name)


# ---------------------------------------------------------------- helpers

def notify_send(msg):
    msg = tr(msg)
    subprocess.Popen(["notify-send", "-a", "Fling", "-i", "video-display", "Fling", msg],
                     stderr=subprocess.DEVNULL)

def login_enabled():
    return os.path.exists(AUTOSTART)

def toggle_login():
    if login_enabled():
        os.remove(AUTOSTART)
    else:
        os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
        with open(AUTOSTART, "w") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Fling\n"
                    f"Exec={HOME}/.local/bin/fling\nIcon=video-display\n"
                    "X-KDE-autostart-after=panel\n")

# ---------------------------------------------------------------- theme

class Theme:
    def __init__(self, app):
        pal = app.palette()
        self.dark = pal.window().color().lightness() < 128
        self.fg = QColor(pal.windowText().color())
        self.bg = QColor("#2a2a2c") if self.dark else QColor("#f6f6f6")
        self.accent = QColor("#0a84ff") if self.dark else QColor("#007aff")
        self.green = QColor("#30d158") if self.dark else QColor("#28c840")
        self.orange = QColor("#ff9f0a") if self.dark else QColor("#e08600")
        self.red = QColor("#ff453a") if self.dark else QColor("#ff3b30")

    def fg_a(self, a):
        c = QColor(self.fg); c.setAlphaF(a); return c

    def acc_a(self, a):
        c = QColor(self.accent); c.setAlphaF(a); return c

    @staticmethod
    def css(c: QColor) -> str:
        return f"rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.3f})"

T: Theme = None  # set in main

def font(px, weight=QFont.Weight.Normal, mono=False):
    f = QFont("monospace" if mono else QApplication.font().family())
    f.setPixelSize(int(round(px)))
    f.setWeight(weight)
    if mono:
        f.setStyleHint(QFont.StyleHint.Monospace)
    return f

def label(text="", px=12.5, color=None, weight=QFont.Weight.Normal, wrap=False):
    l = QLabel()
    l.setText(text)
    l.setFont(font(px, weight))
    l.setStyleSheet(f"color:{Theme.css(color or T.fg)};background:transparent")
    l.setWordWrap(wrap)
    return l

def padded(w, l=14, t=0, r=14, b=0):
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(l, t, r, b)
    lay.setSpacing(0)
    lay.addWidget(w)
    return box

# ---------------------------------------------------------------- icons (drawn, SF-Symbols-like)

_SF = {"up": "chevron.up", "down": "chevron.down", "left": "chevron.left", "right": "chevron.right",
       "home": "house", "tv": "tv", "mic_fill": "mic.fill"}


def glyph(name, size=14, color=None, weight=1.7):
    """Small line icons drawn with QPainter so the look doesn't depend on the
    icon theme."""
    if name in _SF:
        try:
            import sf_icons
            dpr = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1
            pm = QPixmap(int(size * dpr * 2), int(size * dpr * 2))
            pm.setDevicePixelRatio(dpr * 2)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            sf_icons.draw_icon(p, _SF[name], QRectF(0, 0, size, size), color or T.fg, weight / 1.7 * 1.1)
            p.end()
            return QIcon(pm)
        except Exception as e:
            print("sf_icons:", e, flush=True)
    dpr = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1
    pm = QPixmap(int(size * dpr * 2), int(size * dpr * 2))
    pm.setDevicePixelRatio(dpr * 2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color or T.fg, weight * size / 14)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    s = size
    def path(*pts, close=False):
        pa = QPainterPath(); pa.moveTo(*pts[0])
        for q in pts[1:]:
            pa.lineTo(*q)
        if close:
            pa.closeSubpath()
        p.drawPath(pa)
    c = s / 2
    if name == "up":
        path((s*.28, s*.62), (c, s*.36), (s*.72, s*.62))
    elif name == "down":
        path((s*.28, s*.38), (c, s*.64), (s*.72, s*.38))
    elif name == "left":
        path((s*.62, s*.28), (s*.36, c), (s*.62, s*.72))
    elif name == "right":
        path((s*.38, s*.28), (s*.64, c), (s*.38, s*.72))
    elif name == "power":
        p.drawArc(QRectF(s*.2, s*.22, s*.6, s*.6), 120 * 16, 300 * 16)
        p.drawLine(QPoint(int(c), int(s*.14)), QPoint(int(c), int(s*.5)))
    elif name == "home":
        path((s*.2, s*.5), (c, s*.2), (s*.8, s*.5))
        path((s*.28, s*.44), (s*.28, s*.82), (s*.72, s*.82), (s*.72, s*.44))
        path((s*.43, s*.82), (s*.43, s*.62), (s*.57, s*.62), (s*.57, s*.82))
    elif name == "back":
        pa = QPainterPath(); pa.moveTo(s*.3, s*.38); pa.lineTo(s*.62, s*.38)
        pa.arcTo(QRectF(s*.44, s*.38, s*.36, s*.36), 90, -180); pa.lineTo(s*.4, s*.74)
        p.drawPath(pa)
        path((s*.42, s*.24), (s*.28, s*.38), (s*.42, s*.52))
    elif name in ("spk", "spk_minus", "spk_plus", "spk_slash", "spk_wave"):
        p.setBrush(color or T.fg) if name == "spk_wave" else None
        path((s*.14, s*.4), (s*.3, s*.4), (s*.5, s*.22), (s*.5, s*.78), (s*.3, s*.6), (s*.14, s*.6), close=True)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if name == "spk_minus":
            p.drawLine(QPoint(int(s*.64), int(c)), QPoint(int(s*.88), int(c)))
        elif name == "spk_plus":
            p.drawLine(QPoint(int(s*.64), int(c)), QPoint(int(s*.88), int(c)))
            p.drawLine(QPoint(int(s*.76), int(s*.38)), QPoint(int(s*.76), int(s*.62)))
        elif name == "spk_slash":
            p.drawLine(QPoint(int(s*.62), int(s*.38)), QPoint(int(s*.86), int(s*.62)))
            p.drawLine(QPoint(int(s*.86), int(s*.38)), QPoint(int(s*.62), int(s*.62)))
        elif name == "spk_wave":
            p.drawArc(QRectF(s*.42, s*.32, s*.3, s*.36), -50*16, 100*16)
            p.drawArc(QRectF(s*.42, s*.2, s*.46, s*.6), -50*16, 100*16)
    elif name in ("mic", "mic_fill"):
        if name == "mic_fill":
            p.setBrush(color or T.fg)
        p.drawRoundedRect(QRectF(s*.38, s*.12, s*.24, s*.46), s*.12, s*.12)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(s*.26, s*.3, s*.48, s*.42), 180*16, 180*16)
        p.drawLine(QPoint(int(c), int(s*.72)), QPoint(int(c), int(s*.86)))
    elif name == "play_on_tv":
        p.drawRoundedRect(QRectF(s*.12, s*.22, s*.76, s*.52), s*.08, s*.08)
        p.setBrush(color or T.fg)
        path((s*.43, s*.36), (s*.43, s*.6), (s*.62, s*.48), close=True)
    elif name == "clipboard":
        p.drawRoundedRect(QRectF(s*.24, s*.2, s*.52, s*.66), s*.07, s*.07)
        p.drawRoundedRect(QRectF(s*.38, s*.12, s*.24, s*.14), s*.04, s*.04)
    elif name == "waves":
        p.drawEllipse(QRectF(c - s*.06, c - s*.06, s*.12, s*.12))
        p.drawArc(QRectF(s*.28, s*.28, s*.44, s*.44), -45*16, 90*16)
        p.drawArc(QRectF(s*.28, s*.28, s*.44, s*.44), 135*16, 90*16)
        p.drawArc(QRectF(s*.12, s*.12, s*.76, s*.76), -45*16, 90*16)
        p.drawArc(QRectF(s*.12, s*.12, s*.76, s*.76), 135*16, 90*16)
    elif name == "bt":
        path((s*.3, s*.3), (s*.68, s*.66), (c, s*.86), (c, s*.14), (s*.68, s*.34), (s*.3, s*.7))
    elif name == "camera":
        p.drawRoundedRect(QRectF(s*.12, s*.3, s*.76, s*.52), s*.08, s*.08)
        path((s*.36, s*.3), (s*.42, s*.18), (s*.58, s*.18), (s*.64, s*.3))
        p.drawEllipse(QRectF(c - s*.14, s*.42, s*.28, s*.28))
    elif name == "tv":
        p.drawRoundedRect(QRectF(s*.1, s*.2, s*.8, s*.54), s*.08, s*.08)
        p.drawLine(QPoint(int(s*.32), int(s*.86)), QPoint(int(s*.68), int(s*.86)))
    p.end()
    return QIcon(pm)


def tray_icon(casting=False):
    """Fling logo: rounded blue→violet square with a screen and cast dots."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, 64, 64)
    g.setColorAt(0, QColor("#3E8BFF")); g.setColorAt(1, QColor("#5B4DFF"))
    p.setBrush(g); p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(0, 0, 64, 64), 14, 14)
    p.setPen(QPen(QColor("white"), 3.5))
    p.setBrush(QColor(255, 255, 255, 36))
    p.drawRoundedRect(QRectF(13, 26, 38, 25), 6, 6)
    p.setPen(Qt.PenStyle.NoPen)
    for x, y, r, a in ((16.5, 12.5, 2, 140), (23.5, 16, 2.75, 190), (30.5, 21.5, 3.5, 230)):
        p.setBrush(QColor(255, 255, 255, a)); p.drawEllipse(QRectF(x - r, y - r, 2*r, 2*r))
    p.setBrush(QColor("#30d158") if casting else QColor("white"))
    p.drawEllipse(QRectF(37.5 - 5.25, 35 - 5.25, 10.5, 10.5))
    p.end()
    return QIcon(pm)

# ---------------------------------------------------------------- widgets (PanelRows.swift / RemoteView.swift)

class Separator(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(11)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(12, 5, self.width() - 24, 1, T.fg_a(0.13))


class MenuRow(QPushButton):
    """Menu-flavoured row with optional icon and right-aligned shortcut."""
    def __init__(self, title, icon=None, shortcut=None, accented=False, on=None):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._icon_name = icon
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(8)
        self.ic = QLabel()
        self.ic.setFixedWidth(15)
        self.ic.setVisible(bool(icon))
        self.ic.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.t = label(title, 12.5)
        self.t.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.k = label(shortcut or "", 12.5, T.fg_a(0.3))
        self.k.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self.ic); lay.addWidget(self.t); lay.addStretch(1); lay.addWidget(self.k)
        self.setFixedHeight(25)
        self.setStyleSheet(
            "QPushButton{border:none;border-radius:5px;background:transparent;margin:0 6px}"
            f"QPushButton:hover:enabled{{background:{Theme.css(T.acc_a(0.2))}}}")
        self.set_accented(accented)
        if on:
            self.clicked.connect(on)

    def set_accented(self, a):
        self.accented = a
        col = T.accent if a else T.fg
        self.t.setStyleSheet(f"color:{Theme.css(col)};background:transparent")
        self.t.setFont(font(12.5, QFont.Weight.DemiBold if a else QFont.Weight.Normal))
        if self._icon_name:
            self.ic.setPixmap(glyph(self._icon_name, 14, T.accent if a else T.fg_a(0.55)).pixmap(15, 15))

    def setText(self, s):
        self.t.setText(s)

    def setEnabled(self, en):
        super().setEnabled(en)
        eff = None
        self.setWindowOpacity(1)
        for w in (self.t, self.ic, self.k):
            w.setEnabled(en)
        self.t.setStyleSheet(f"color:{Theme.css(T.accent if self.accented else T.fg) if en else Theme.css(T.fg_a(0.32))};background:transparent")


class SlimSlider(QSlider):
    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setRange(0, 100)
        self.setFixedHeight(13)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h, k = self.width(), self.height(), 11
        f = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        y = h / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.fg_a(0.18)); p.drawRoundedRect(QRectF(0, y - 1.5, w, 3), 1.5, 1.5)
        p.setBrush(T.fg_a(0.85)); p.drawRoundedRect(QRectF(0, y - 1.5, w * f, 3), 1.5, 1.5)
        x = min(max(0, w * f - k / 2), w - k)
        p.setBrush(QColor(0, 0, 0, 90)); p.drawEllipse(QRectF(x, y - k/2 + 0.6, k, k))
        p.setBrush(QColor("white")); p.drawEllipse(QRectF(x, y - k/2, k, k))

    def _set_from(self, x):
        self.setValue(round(max(0, min(1, x / max(1, self.width()))) * 100))

    def mousePressEvent(self, e):
        self._set_from(e.position().x())

    def mouseMoveEvent(self, e):
        self._set_from(e.position().x())

    def wheelEvent(self, e):
        e.ignore()  # the panel's wheel is the TV d-pad


class SlimProgress(QWidget):
    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.setFixedHeight(3)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        w = self.width()
        p.setBrush(T.fg_a(0.18)); p.drawRoundedRect(QRectF(0, 0, w, 3), 1.5, 1.5)
        p.setBrush(T.accent); p.drawRoundedRect(QRectF(0, 0, w * self.value, 3), 1.5, 1.5)


class Artwork(QWidget):
    nam = None

    BTN = 28

    def __init__(self, height, on_refresh=None):
        super().__init__()
        self.setFixedHeight(height)
        self.pix = None
        self.url = None
        self.on_refresh = on_refresh
        self.hover = False
        self.pressed = False
        self.angle = 0.0
        self.spin_until = 0.0
        self.spin = QTimer(self, interval=16)
        self.spin.timeout.connect(self._tick)
        self.setMouseTracking(True)
        if Artwork.nam is None:
            Artwork.nam = QNetworkAccessManager()

    # --- refresh button drawn on the image
    def _btn_rect(self):
        b = self.BTN
        return QRectF(self.width() - b - 8, 8, b, b)

    def _tick(self):
        busy = time.monotonic() < self.spin_until or getattr(self, "busy", lambda: False)()
        if busy:
            self.angle = (self.angle + 9) % 360
        else:
            # ease to the next full turn, then stop
            rest = (360 - self.angle) % 360
            if rest < 10:
                self.angle = 0; self.spin.stop()
            else:
                self.angle += max(4, rest * 0.18)
        self.update()

    def start_spin(self):
        self.spin_until = time.monotonic() + 0.7
        if not self.spin.isActive():
            self.spin.start()

    def mouseMoveEvent(self, e):
        h = self.on_refresh is not None and self._btn_rect().contains(e.position())
        if h != self.hover:
            self.hover = h
            self.setCursor(Qt.CursorShape.PointingHandCursor if h else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self.hover = self.pressed = False
        self.update()

    def mousePressEvent(self, e):
        if self.on_refresh and self._btn_rect().contains(e.position()):
            self.pressed = True
            self.update()

    def mouseReleaseEvent(self, e):
        if self.pressed and self._btn_rect().contains(e.position()):
            self.start_spin()
            self.on_refresh()
        self.pressed = False
        self.update()

    def _paint_button(self, p):
        r = self._btn_rect()
        if self.pressed:
            r = r.adjusted(1.2, 1.2, -1.2, -1.2)
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 95 if self.hover else 60), 1))
        p.setBrush(QColor(0, 0, 0, 150 if self.hover else 110))
        p.drawEllipse(r)
        c = r.center()
        p.save()
        p.translate(c)
        p.rotate(self.angle)
        rad = r.width() * 0.25
        pen = QPen(QColor("white"), 1.9)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(-rad, -rad, 2 * rad, 2 * rad), 70 * 16, 290 * 16)
        # arrowhead at the arc's start (top, pointing clockwise)
        import math
        a = math.radians(70)
        tip = (rad * math.cos(a), -rad * math.sin(a))
        p.setBrush(QColor("white"))
        p.setPen(Qt.PenStyle.NoPen)
        head = QPainterPath()
        head.moveTo(tip[0] + 4.2, tip[1] + 0.4)
        head.lineTo(tip[0] - 1.6, tip[1] - 3.6)
        head.lineTo(tip[0] - 1.0, tip[1] + 3.4)
        head.closeSubpath()
        p.drawPath(head)
        p.restore()

    def set_url(self, url):
        if url == self.url:
            return
        self.url, self.pix = url, None
        self.update()
        if not url:
            return
        reply = Artwork.nam.get(QNetworkRequest(QUrl(url)))
        def done():
            if self.url == url:
                pm = QPixmap()
                if pm.loadFromData(bytes(reply.readAll())):
                    self.pix = pm
                    self.update()
            reply.deleteLater()
        reply.finished.connect(done)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        clip = QPainterPath(); clip.addRoundedRect(r, 8, 8)
        p.setClipPath(clip)
        if self.pix:
            pm = self.pix.scaled(self.size() * self.devicePixelRatioF(),
                                 Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(self.devicePixelRatioF())
            sx = (pm.width() / pm.devicePixelRatio() - r.width()) / 2
            sy = (pm.height() / pm.devicePixelRatio() - r.height()) / 2
            p.drawPixmap(QPoint(int(-sx), int(-sy)), pm)
        else:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, QColor.fromRgbF(0.18, 0.23, 0.32)); g.setColorAt(1, QColor.fromRgbF(0.29, 0.20, 0.32))
            p.fillRect(r, g)
            c = r.center(); s = 11
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(255, 255, 255, 90))
            tri = QPainterPath(); tri.moveTo(c.x() - s*.6, c.y() - s); tri.lineTo(c.x() - s*.6, c.y() + s)
            tri.lineTo(c.x() + s, c.y()); tri.closeSubpath(); p.drawPath(tri)
        if getattr(self, "dim", False) and self.pix:
            p.fillRect(r, QColor(0, 0, 0, 110))
        if self.on_refresh:
            self._paint_button(p)


class KeyButton(QPushButton):
    """Round d-pad / power key."""
    def __init__(self, glyph_name=None, text=None, size=30, accented=False, on=None):
        super().__init__()
        self.setFixedSize(size, size)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        col = T.accent if accented else T.fg
        if glyph_name:
            self.setIcon(glyph(glyph_name, 16, col, 2.0))
            self.setIconSize(QSize(int(size * .5), int(size * .5)))
        else:
            self.setText(text)
            self.setFont(font(size * .3, QFont.Weight.DemiBold))
        bg = T.fg_a(0.1 if accented else 0.07)
        self.setStyleSheet(
            f"QPushButton{{border:none;border-radius:{size//2}px;background:{Theme.css(bg)};color:{Theme.css(col)}}}"
            f"QPushButton:hover{{background:{Theme.css(T.acc_a(0.22))}}}"
            f"QPushButton:pressed{{background:{Theme.css(T.acc_a(0.35))}}}")
        if on:
            self.clicked.connect(on)


class Pill(QPushButton):
    def __init__(self, glyph_name, text, on):
        super().__init__()
        self.setText(text)
        self.setIcon(glyph(glyph_name, 14, T.fg))
        self.setIconSize(QSize(13, 13))
        self.setFont(font(11))
        self.setFixedHeight(25)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QPushButton{{border:none;border-radius:5px;background:{Theme.css(T.fg_a(0.06))};color:{Theme.css(T.fg)}}}"
            f"QPushButton:hover{{background:{Theme.css(T.acc_a(0.2))}}}"
            f"QPushButton:pressed{{background:{Theme.css(T.acc_a(0.35))}}}")
        self.clicked.connect(on)


class Chip(QPushButton):
    def __init__(self, text, on):
        super().__init__()
        self.setText(text)
        self.setFixedHeight(24)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.clicked.connect(on)
        self.set_active(False)

    def set_active(self, a):
        self.setFont(font(10.5, QFont.Weight.DemiBold if a else QFont.Weight.Normal))
        bg = T.acc_a(0.18) if a else T.fg_a(0.06)
        col = T.accent if a else T.fg
        self.setStyleSheet(
            f"QPushButton{{border:none;border-radius:5px;background:{Theme.css(bg)};color:{Theme.css(col)}}}"
            f"QPushButton:hover{{background:{Theme.css(T.acc_a(0.2))}}}")


def field(placeholder, mono=False):
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setFont(font(12, mono=mono))
    e.setFixedHeight(24)
    e.setStyleSheet(
        f"QLineEdit{{border:1px solid {Theme.css(T.fg_a(0.16))};border-radius:5px;padding:0 6px;"
        f"background:{Theme.css(QColor(255,255,255,18) if T.dark else QColor('white'))};color:{Theme.css(T.fg)}}}"
        f"QLineEdit:focus{{border:1px solid {Theme.css(T.acc_a(0.7))}}}")
    return e


def small_button(text, on):
    b = QPushButton()
    b.setText(text)
    b.setFont(font(11.5))
    b.setFixedHeight(24)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(
        f"QPushButton{{border:1px solid {Theme.css(T.fg_a(0.16))};border-radius:5px;padding:0 10px;"
        f"background:{Theme.css(T.fg_a(0.05))};color:{Theme.css(T.fg)}}}"
        f"QPushButton:hover{{background:{Theme.css(T.acc_a(0.2))}}}"
        f"QPushButton:disabled{{color:{Theme.css(T.fg_a(0.3))}}}")
    b.clicked.connect(on)
    return b


class VolumeRow(QWidget):
    def __init__(self, state: State):
        super().__init__()
        self.state = state
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 3, 14, 3)
        lay.setSpacing(9)
        self.ic = QLabel(); self.ic.setFixedWidth(13)
        self.slider = SlimSlider()
        self.slider.valueChanged.connect(self._moved)
        self.num = label("50", 10.5, T.fg_a(0.3))
        self.num.setFont(font(10.5, mono=True))
        self.num.setFixedWidth(22)
        self.num.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.ic); lay.addWidget(self.slider, 1); lay.addWidget(self.num)
        self._updating = False

    def _moved(self, v):
        if not self._updating:
            self.state.set_volume(v)

    def sync(self):
        v = self.state.volume
        self._updating = True
        if self.slider.value() != v:
            self.slider.setValue(v)
        self._updating = False
        self.num.setText(str(v))
        self.ic.setPixmap(glyph("spk_slash" if v == 0 else "spk_wave", 13, T.fg_a(0.55)).pixmap(13, 13))


# ---------------------------------------------------------------- the panel (PanelView.swift)

class Panel(QWidget):
    def __init__(self, state: State):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Fling")
        self.state = state
        self.hidden_at = 0.0
        self._wheel = [0.0, 0.0, 0.0]  # accX, accY, last emit
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)
        self.body = QWidget()
        self.body.setFixedWidth(340)
        root.addWidget(self.body)
        v = QVBoxLayout(self.body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # setup (catt missing)
        self.setup = QWidget()
        sl = QVBoxLayout(self.setup); sl.setContentsMargins(14, 14, 14, 14); sl.setSpacing(8)
        sl.addWidget(label("Fling нужен catt", 13, weight=QFont.Weight.DemiBold))
        sl.addWidget(label("Движок трансляции не установлен. Установите его один раз — Fling его найдёт.", 11.5, T.fg_a(0.55), wrap=True))
        sl.addWidget(small_button("Скопировать команду установки",
                                  lambda: QApplication.clipboard().setText("uv tool install catt")))
        v.addWidget(self.setup)

        # ---- casting
        self.casting = QWidget()
        cl = QVBoxLayout(self.casting); cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(0)
        self.c_art = Artwork(120, on_refresh=self._refresh_now)
        cl.addWidget(padded(self.c_art, 12, 10, 12, 0))
        self.c_title = label("", 12.5, weight=QFont.Weight.DemiBold, wrap=True)
        self.c_host = label("", 11, T.fg_a(0.55))
        box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(14, 10, 14, 0); bl.setSpacing(3)
        bl.addWidget(self.c_title); bl.addWidget(self.c_host)
        cl.addWidget(box)
        # YouTube-on-TV link: the only source of what native YouTube really plays
        self.yt_row = MenuRow("Связать с YouTube на ТВ — видеть, что играет", icon="tv", accented=True,
                              on=self._yt_entry)
        cl.addWidget(padded(self.yt_row, 0, 6, 0, 0))
        self.yt_box = QWidget()
        yl = QVBoxLayout(self.yt_box); yl.setContentsMargins(14, 4, 14, 4); yl.setSpacing(6)
        yl.addWidget(label("На ТВ: YouTube → Настройки → «Подключить телефон / Связать с ТВ по коду». "
                           "Введите 12 цифр с экрана:", 11, T.fg_a(0.55), wrap=True))
        yr = QHBoxLayout(); yr.setSpacing(6)
        self.yt_edit = field("123 456 789 012", mono=True)
        self.yt_edit.returnPressed.connect(self._yt_submit)
        self.yt_ok = small_button("Связать", self._yt_submit)
        self.yt_cancel = small_button("Отмена", self._yt_cancel)
        self.yt_wait = label("⏳", 12)
        yr.addWidget(self.yt_edit, 1); yr.addWidget(self.yt_ok); yr.addWidget(self.yt_cancel); yr.addWidget(self.yt_wait)
        yl.addLayout(yr)
        cl.addWidget(self.yt_box)
        self.c_prog = SlimProgress()
        self.c_el = label("", 10.5, T.fg_a(0.3)); self.c_el.setFont(font(10.5, mono=True))
        self.c_rem = label("", 10.5, T.fg_a(0.3)); self.c_rem.setFont(font(10.5, mono=True))
        pw = QWidget(); pl = QVBoxLayout(pw); pl.setContentsMargins(14, 8, 14, 5); pl.setSpacing(5)
        pl.addWidget(self.c_prog)
        tl = QHBoxLayout(); tl.addWidget(self.c_el); tl.addStretch(1); tl.addWidget(self.c_rem)
        pl.addLayout(tl)
        cl.addWidget(pw)
        self.c_play = MenuRow("Пауза", shortcut="Пробел", on=state.toggle_play)
        cl.addWidget(self.c_play)
        cl.addWidget(MenuRow(f"Назад {SEEK} с", shortcut="←", on=lambda: state.seek(-SEEK)))
        cl.addWidget(MenuRow(f"Вперёд {SEEK} с", shortcut="→", on=lambda: state.seek(SEEK)))
        self.c_prev = MenuRow("Предыдущее видео", on=state.yt_prev)
        self.c_next = MenuRow("Следующее видео", on=state.yt_next)
        cl.addWidget(self.c_prev); cl.addWidget(self.c_next)
        self.c_vol = VolumeRow(state)
        cl.addWidget(self.c_vol)
        cl.addWidget(Separator())
        self.c_cast = MenuRow("Отправить эту вкладку", shortcut=SHORTCUT_LABEL, on=state.cast_tab)
        cl.addWidget(self.c_cast)
        cl.addWidget(MenuRow("Остановить трансляцию", shortcut="Ctrl+.", on=state.stop))
        v.addWidget(self.casting)

        # ---- idle
        self.idle = QWidget()
        il = QVBoxLayout(self.idle); il.setContentsMargins(0, 0, 0, 0); il.setSpacing(0)
        self.i_art = Artwork(108, on_refresh=self._refresh_now)
        self.i_art_box = padded(self.i_art, 12, 10, 12, 0)
        il.addWidget(self.i_art_box)
        self.i_title = label("", 12.5, weight=QFont.Weight.DemiBold, wrap=True)
        self.i_host = label("", 11, T.fg_a(0.55))
        self.i_kind = label("", 11, T.green)
        box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(14, 10, 14, 6); bl.setSpacing(3)
        bl.addWidget(self.i_title)
        hl = QHBoxLayout(); hl.setSpacing(4); hl.addWidget(self.i_host); hl.addWidget(label("·", 11, T.fg_a(0.55))); hl.addWidget(self.i_kind); hl.addStretch(1)
        bl.addLayout(hl)
        il.addWidget(box)
        il.addWidget(Separator())
        self.i_cast = MenuRow("Отправить эту вкладку", icon="play_on_tv", shortcut=SHORTCUT_LABEL,
                              accented=True, on=state.cast_tab)
        il.addWidget(self.i_cast)
        self.i_why = label("", 11, T.orange, wrap=True)
        self.i_why_box = padded(self.i_why, 14, 0, 14, 7)
        il.addWidget(self.i_why_box)
        il.addWidget(MenuRow("Отправить ссылку из буфера", icon="clipboard", on=state.cast_clipboard))
        self.i_vol_sep = Separator()
        il.addWidget(self.i_vol_sep)
        self.i_vol = VolumeRow(state)
        il.addWidget(self.i_vol)
        v.addWidget(self.idle)

        self.sep_remote = Separator()
        v.addWidget(self.sep_remote)

        # ---- remote: starting / pin / setup / controls
        self.r_starting = label("⏳  Связываюсь с ТВ…", 11.5, T.fg_a(0.55))
        self.r_starting_box = padded(self.r_starting, 14, 10, 14, 10)
        v.addWidget(self.r_starting_box)

        self.r_pin = QWidget()
        pl = QVBoxLayout(self.r_pin); pl.setContentsMargins(14, 6, 14, 6); pl.setSpacing(6)
        pl.addWidget(label("Введите код с экрана телевизора", 11.5, T.fg_a(0.55)))
        row = QHBoxLayout(); row.setSpacing(6)
        self.pin_edit = field("A1B2C3", mono=True)
        self.pin_edit.textChanged.connect(self._pin_changed)
        self.pin_edit.returnPressed.connect(self._pin_submit)
        self.pin_ok = small_button("Связать", self._pin_submit)
        self.pin_cancel = small_button("Отмена", lambda: state.atv.cancel_pairing())
        self.pin_wait = label("⏳", 12)
        row.addWidget(self.pin_edit, 1); row.addWidget(self.pin_ok); row.addWidget(self.pin_cancel); row.addWidget(self.pin_wait)
        pl.addLayout(row)
        pad = QGridLayout(); pad.setSpacing(4)
        for i, ch in enumerate("0123456789ABCDEF⌫"):
            b = Chip(ch, lambda _=False, c=ch: self._pin_key(c))
            b.setFont(font(12, QFont.Weight.DemiBold, mono=True))
            pad.addWidget(b, i // 6, i % 6)
        pl.addLayout(pad)
        pl.addWidget(label("Набирайте мышкой или с клавиатуры — русская раскладка тоже подойдёт.", 10, T.fg_a(0.45), wrap=True))
        v.addWidget(self.r_pin)

        self.r_setup = QWidget()
        sl = QVBoxLayout(self.r_setup); sl.setContentsMargins(0, 0, 0, 4); sl.setSpacing(0)
        sl.addWidget(padded(label("Свяжите один раз с ТВ, чтобы открыть приложения, крестовину и ввод текста.",
                                  11, T.fg_a(0.55), wrap=True), 14, 10, 14, 6))
        sl.addWidget(MenuRow("Включить ТВ", icon="power", on=state.wake))
        sl.addWidget(MenuRow("Настроить управление ТВ…", icon="waves", accented=True, on=state.start_pairing))
        sl.addWidget(MenuRow("Подключить как Bluetooth-пульт…", icon="bt", on=state.bt_pair))
        v.addWidget(self.r_setup)

        self.r_bt = QWidget()
        bl2 = QVBoxLayout(self.r_bt); bl2.setContentsMargins(14, 6, 14, 8); bl2.setSpacing(6)
        bl2.addWidget(label("BLUETOOTH-ПУЛЬТ", 9.5, T.fg_a(0.55), QFont.Weight.Bold))
        bl2.addWidget(label("1. На ТВ: Настройки → Пульты и аксессуары → Добавить аксессуар.\n"
                            "2. Выберите «Fling Remote».\n"
                            "3. Если KDE спросит про сопряжение — подтвердите.", 11, T.fg, wrap=True))
        self.r_bt_state = label("", 11, T.fg_a(0.55), wrap=True)
        bl2.addWidget(self.r_bt_state)
        bl2.addWidget(small_button("Готово", lambda: (setattr(state, "bt_setup", False), state.changed.emit())))
        v.addWidget(self.r_bt)

        self.r_ctl = QWidget()
        rl = QVBoxLayout(self.r_ctl); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(0)
        hdr = QHBoxLayout(); hdr.setContentsMargins(14, 10, 14, 8); hdr.setSpacing(8)
        hv = QVBoxLayout(); hv.setSpacing(2)
        lab = label("НА ТЕЛЕВИЗОРЕ", 9.5, T.fg_a(0.55), QFont.Weight.Bold)
        f = lab.font(); f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8); lab.setFont(f)
        hv.addWidget(lab)
        self.r_now = label("", 12.5, weight=QFont.Weight.DemiBold)
        hv.addWidget(self.r_now)
        hdr.addLayout(hv, 1)
        self.r_power = KeyButton("power", size=26, on=lambda: state.tv_key(KEY["power"]))
        hdr.addWidget(self.r_power)
        rl.addLayout(hdr)
        self.grid_box = QWidget()
        grid = QGridLayout(self.grid_box); grid.setContentsMargins(12, 0, 12, 8); grid.setSpacing(4)
        self.chips = []
        for i, (name, link, pkg) in enumerate(TV_APPS):
            ch = Chip(name, lambda _=False, l=link: state.tv_launch(l))
            ch.pkg = pkg
            self.chips.append(ch)
            grid.addWidget(ch, i // 4, i % 4)
        for c in range(4):
            grid.setColumnStretch(c, 1)
        rl.addWidget(self.grid_box)
        dp = QVBoxLayout(); dp.setContentsMargins(0, 6, 0, 8); dp.setSpacing(6)
        k = lambda n: (lambda: state.tv_key(KEY[n]))
        dp.addWidget(KeyButton("up", on=k("up")), 0, Qt.AlignmentFlag.AlignHCenter)
        mid = QHBoxLayout(); mid.setSpacing(13); mid.addStretch(1)
        b = KeyButton("back", on=k("back")); b.setToolTip("Назад"); mid.addWidget(b)
        mid.addWidget(KeyButton("left", on=k("left")))
        mid.addWidget(KeyButton(text="OK", size=40, accented=True, on=k("ok")))
        mid.addWidget(KeyButton("right", on=k("right")))
        b = KeyButton("home", on=k("home")); b.setToolTip("Домой"); mid.addWidget(b)
        mid.addStretch(1)
        dp.addLayout(mid)
        dp.addWidget(KeyButton("down", on=k("down")), 0, Qt.AlignmentFlag.AlignHCenter)
        hint = label("или крутите колёсико / тачпад в любом месте панели", 9.5, T.fg_a(0.25))
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        dp.addWidget(hint)
        rl.addLayout(dp)
        vk = QHBoxLayout(); vk.setContentsMargins(12, 8, 12, 4); vk.setSpacing(5)
        vk.addWidget(Pill("spk_minus", " Громк −", k("vol_down")))
        vk.addWidget(Pill("spk_slash", " Без звука", k("mute")))
        vk.addWidget(Pill("spk_plus", " Громк +", k("vol_up")))
        rl.addLayout(vk)
        ty = QVBoxLayout(); ty.setContentsMargins(14, 4, 14, 9); ty.setSpacing(3)
        tr = QHBoxLayout(); tr.setSpacing(6)
        self.type_edit = field("Напечатать на ТВ")
        self.type_edit.setToolTip("Текст попадёт в активное поле ввода на телевизоре")
        self.type_edit.returnPressed.connect(self._type_submit)
        self.mic = QPushButton()
        self.mic.setFixedSize(24, 22)
        self.mic.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic.clicked.connect(state.tv_voice_toggle)
        tr.addWidget(self.type_edit, 1); tr.addWidget(self.mic)
        ty.addLayout(tr)
        self.listening = label("Слушаю — нажмите микрофон ещё раз, когда закончите.", 10, T.red)
        ty.addWidget(self.listening)
        rl.addLayout(ty)
        v.addWidget(self.r_ctl)

        self.r_fail = label("", 11, T.orange, wrap=True)
        self.r_fail_box = padded(self.r_fail, 14, 0, 14, 7)
        v.addWidget(self.r_fail_box)

        self.err = label("", 10.5, T.orange, wrap=True)
        self.err_box = padded(self.err, 14, 4, 14, 4)
        v.addWidget(self.err_box)

        v.addWidget(Separator())
        self.shot_row = MenuRow("Снимок экрана ТВ", icon="camera", on=state.tv_screenshot)
        v.addWidget(self.shot_row)
        self.setup_row = MenuRow("Подключение ТВ…", icon="tv", on=lambda: (self.hide(), self.open_setup()))
        v.addWidget(self.setup_row)
        self.login = MenuRow("Запускать при входе", on=self._toggle_login)
        v.addWidget(self.login)
        self.lang_row = MenuRow("Язык: Русский" if LANG == "ru" else "Language: English", on=self._toggle_lang)
        v.addWidget(self.lang_row)
        v.addWidget(MenuRow("Выйти из Fling", shortcut="Ctrl+Q", on=QApplication.quit))
        v.addWidget(Separator())

        # footer
        ft = QHBoxLayout(); ft.setContentsMargins(14, 5, 14, 9); ft.setSpacing(7)
        self.dot = QLabel(); self.dot.setFixedSize(6, 6)
        self.dev_name = label("", 11.5)
        self.switch = QToolButton()
        self.switch.setText("Сменить")
        self.switch.setFont(font(11))
        self.switch.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.switch.setStyleSheet(f"QToolButton{{border:none;color:{Theme.css(T.accent)};background:transparent}}"
                                  "QToolButton::menu-indicator{image:none}")
        self.switch_menu = QMenu(self.switch)
        self.switch.setMenu(self.switch_menu)
        ft.addWidget(self.dot); ft.addWidget(self.dev_name, 1); ft.addWidget(self.switch)
        fw = QWidget(); fw.setLayout(ft)
        v.addWidget(fw)
        # authorship: Fling is Damion Rashford's and Tatiana's app; this is an unofficial port
        credit = label(f'<a href="https://github.com/tatianathevisionary/fling" style="color:{Theme.css(T.fg_a(0.45))};text-decoration:none">'
                       'Fling создан Damion Rashford и Tatiana · неофициальный порт</a>', 10, T.fg_a(0.4))
        credit.setOpenExternalLinks(True)
        credit.setToolTip("github.com/tatianathevisionary/fling")
        v.addWidget(padded(credit, 14, 0, 14, 8))

        # keyboard (menu equivalents)
        for seq, fn in (("Space", self._space), ("Left", lambda: self._casting_only(lambda: state.seek(-SEEK))),
                        ("Right", lambda: self._casting_only(lambda: state.seek(SEEK))),
                        ("Ctrl+.", state.stop), ("Ctrl+Q", QApplication.quit),
                        ("Ctrl+Shift+C", state.cast_tab), ("Ctrl+V", state.cast_clipboard),
                        ("Escape", self.hide)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(fn)

        state.changed.connect(self.sync)
        self.sync()

    # --- input helpers
    def _editing(self):
        return isinstance(QApplication.focusWidget(), QLineEdit)

    def _space(self):
        if self._editing():
            QApplication.focusWidget().insert(" ")
        elif self.state.panel == "casting":
            self.state.toggle_play()

    def _casting_only(self, fn):
        if self._editing():
            return
        if self.state.panel == "casting":
            fn()

    # Russian layout: the key labelled A types «ф», B → «и», C → «с», D → «в», E → «у», F → «а»
    _RU_KEYS = str.maketrans("фисвуаФИСВУА", "ABCDEFABCDEF")

    def _pin_changed(self, t):
        clean = "".join(ch for ch in t.translate(self._RU_KEYS).upper() if ch in "0123456789ABCDEF")[:6]
        if clean != t:
            self.pin_edit.blockSignals(True)
            self.pin_edit.setText(clean)
            self.pin_edit.blockSignals(False)
        self.pin_ok.setEnabled(len(clean) == 6)

    def _pin_key(self, ch):
        t = self.pin_edit.text()
        self.pin_edit.setText(t[:-1] if ch == "⌫" else (t + ch)[:6])
        if len(self.pin_edit.text()) == 6 and ch != "⌫":
            QTimer.singleShot(150, self._pin_submit)

    def _pin_submit(self):
        code = self.pin_edit.text().strip().upper()
        if len(code) == 6:
            self.state.atv.finish_pairing(code)

    def _type_submit(self):
        t = self.type_edit.text()
        self.type_edit.clear()
        self.state.tv_text(t)

    def _yt_entry(self):
        self.state.yt_pairing = "entry"
        self.state.changed.emit()
        QTimer.singleShot(0, self.yt_edit.setFocus)

    def _yt_submit(self):
        code = re.sub(r"\D", "", self.yt_edit.text())
        if len(code) < 10:
            self.state._set(last_error="Код YouTube — это 12 цифр с экрана ТВ.")
            return
        self.yt_edit.clear()
        self.state.yt_link(code)

    def _yt_cancel(self):
        self.state.yt_pairing = "idle"
        self.state.changed.emit()

    def _refresh_now(self):
        self.state.lounge.command("get_now_playing")
        self.state.refresh()
        for a in (self.c_art, self.i_art):
            a.busy = lambda: self.state._refreshing

    def _toggle_lang(self):
        c = load_conf()
        c["lang"] = "en" if LANG == "ru" else "ru"
        save_conf(c)
        subprocess.Popen(["bash", "-c", f"sleep 1; exec {HOME}/.local/bin/fling show"],
                         start_new_session=True)
        QApplication.quit()

    def open_setup(self):
        if not hasattr(self, "_setup"):
            self._setup = SetupWindow(self.state)
        self._setup.show(); self._setup.raise_(); self._setup.activateWindow()

    def _toggle_login(self):
        toggle_login()
        self.sync()

    # --- state → widgets
    def sync(self):
        s = self.state
        panel = s.panel
        self.setup.setVisible(panel == "setup")
        self.casting.setVisible(panel == "casting")
        self.idle.setVisible(panel.startswith("idle"))
        url = s.tab["url"] if s.tab else None

        if panel == "casting":
            st = s.status
            cid = st.get("content_id") or ""
            art = st.get("image") or (f"https://img.youtube.com/vi/{cid}/hqdefault.jpg"
                                      if re.fullmatch(r"[\w-]{11}", cid) else None)
            if s.cast_stuck:
                # stale Cast session: its cover/title are from long ago — don't show them
                self.c_art.set_url(None)
                self.c_title.setText(f"{app_name(s.tv_app) or st.get('app') or 'YouTube'} на ТВ")
                self.c_host.setText("ТВ не сообщает, что сейчас играет. Свяжите с YouTube ниже — "
                                    "и тут появятся настоящие обложка, название и время.")
                self.c_host.setWordWrap(True)
            else:
                self.c_art.set_url(art)
                self.c_title.setText(st.get("title") or "Воспроизводится")
            if s.cast_stuck:
                pass
            else:
                self.c_host.setText(st.get("subtitle") or st.get("app") or "")
            self.c_art.dim = False
            self.c_prog.value = s.progress; self.c_prog.update()
            self.c_el.setText(s.elapsed_label); self.c_rem.setText(s.remaining_label)
            self.c_play.setText("Пауза / Играть" if s.cast_stuck else ("Пауза" if s.is_playing else "Играть"))
            self.c_vol.sync()
            linked = s.lounge.is_linked
            self.yt_row.setVisible(not linked and s.yt_pairing == "idle" and s.status.get("app") == "YouTube")
            self.yt_box.setVisible(not linked and s.yt_pairing in ("entry", "linking"))
            busy = s.yt_pairing == "linking"
            self.yt_ok.setVisible(not busy); self.yt_cancel.setVisible(not busy); self.yt_wait.setVisible(busy)
            self.yt_edit.setEnabled(not busy)
            self.c_prev.setVisible(s.yt_live); self.c_next.setVisible(s.yt_live)
            self.c_stop_text = None
        elif panel.startswith("idle"):
            yid = youtube_id(url) if url else None
            self.i_art_box.setVisible(bool(yid))
            self.i_art.set_url(f"https://img.youtube.com/vi/{yid}/hqdefault.jpg" if yid else None)
            self.i_title.setText((s.tab or {}).get("title") or ("Нет открытой страницы" if not s.tab else url))
            self.i_host.setText(host_of(url))
            kind, reason = classify(url)
            if reason:
                self.i_kind.setText(reason); self.i_kind.setStyleSheet(f"color:{Theme.css(T.orange)}")
            elif kind == "maybe":
                self.i_kind.setText("можно попробовать"); self.i_kind.setStyleSheet(f"color:{Theme.css(T.fg_a(0.55))}")
            else:
                self.i_kind.setText("можно отправить"); self.i_kind.setStyleSheet(f"color:{Theme.css(T.green)}")
            ok = reason is None
            self.i_cast.setText("Отправляю…" if s.cast_in_flight else "Отправить эту вкладку")
            self.i_cast.set_accented(ok)
            self.i_cast.setEnabled(ok and not s.cast_in_flight)
            why = {"Нет открытой страницы": "Откройте вкладку в Firefox и попробуйте снова."}.get(reason, reason)
            self.i_why.setText(why or "")
            self.i_why_box.setVisible(bool(why))
            self.i_vol_sep.setVisible(not s.tv_paired)
            self.i_vol.setVisible(not s.tv_paired)
            self.i_vol.sync()

        # remote
        p = s.tv_pairing
        show_remote = panel != "setup"
        self.sep_remote.setVisible(show_remote)
        self.r_starting_box.setVisible(show_remote and p == "starting")
        self.r_pin.setVisible(show_remote and p in ("pin", "verifying"))
        verifying = p == "verifying"
        self.pin_edit.setEnabled(not verifying)
        self.pin_ok.setVisible(not verifying); self.pin_cancel.setVisible(not verifying)
        self.pin_wait.setVisible(verifying)
        self.pin_ok.setEnabled(len(self.pin_edit.text().strip()) == 6)
        idle_like = p in ("idle", "failed")
        ready = s.remote_ready
        self.r_setup.setVisible(show_remote and idle_like and not ready and not s.bt_setup)
        self.r_bt.setVisible(show_remote and idle_like and not s.tv_paired and s.bt_setup)
        if s.bt.get("connected"):
            self.r_bt_state.setText(f"✓ Подключено: {s.bt.get('peer') or 'ТВ'}")
        elif s.bt.get("pairing"):
            self.r_bt_state.setText("⏳ «Fling Remote» виден телевизору — ждёт подключения (3 мин).")
        elif not s.bt.get("running"):
            self.r_bt_state.setText("Служба Bluetooth-пульта не запущена.")
        else:
            self.r_bt_state.setText("Окно сопряжения закрылось — нажмите «Подключить как Bluetooth-пульт» ещё раз.")
        self.r_ctl.setVisible(show_remote and idle_like and ready)
        bt_only = ready and not s.tv_paired
        self.grid_box.setVisible(not bt_only)      # app links need the network remote
        self.mic.setVisible(not bt_only)
        if bt_only:
            self.r_now.setText(f"Bluetooth-пульт · {s.bt.get('peer') or 'ТВ'}")
            self.r_now.setVisible(True)
        self.r_fail_box.setVisible(show_remote and p == "failed")
        self.r_fail.setText(s.tv_pairing_msg)
        if p != "pin" and self.pin_edit.text():
            self.pin_edit.clear() if p == "idle" else None
        if s.tv_paired:
            sub = f"Сейчас: {app_name(s.tv_app)}" if s.tv_app else ("ТВ выключен" if s.tv_on is False else "")
            self.r_now.setText(sub); self.r_now.setVisible(bool(sub))
            self.r_power.setToolTip({True: "Выключить ТВ", False: "Включить ТВ"}.get(s.tv_on, "Вкл/выкл ТВ"))
            for ch in self.chips:
                ch.set_active(ch.pkg == s.tv_app)
            on = s.tv_voice
            self.mic.setIcon(glyph("mic_fill" if on else "mic", 14, T.red if on else T.fg))
            bg = QColor(T.red); bg.setAlphaF(0.15)
            self.mic.setStyleSheet(f"QPushButton{{border:none;border-radius:5px;background:{Theme.css(bg if on else T.fg_a(0.07))}}}")
            self.mic.setToolTip("Остановить голосовой поиск" if on else "Голосовой поиск")
            self.listening.setVisible(on)

        self.err.setText(s.last_error or "")
        self.err_box.setVisible(bool(s.last_error))
        self.login.setText(("✓ " if login_enabled() else "") + "Запускать при входе")

        # footer
        col = T.green if s.device else T.fg_a(0.4)
        self.dot.setStyleSheet(f"background:{Theme.css(col)};border-radius:3px")
        self.dev_name.setText(s.device["name"] if s.device else "Устройство не найдено")
        self.switch.setVisible(len(s.devices) > 1)
        self.switch_menu.clear()
        for d in s.devices:
            a = self.switch_menu.addAction(d["name"])
            a.setCheckable(True); a.setChecked(bool(s.device) and d["ip"] == s.device["ip"])
            a.triggered.connect(lambda _=False, d=d: s.select_device(d))
        if self.isVisible():
            QTimer.singleShot(0, self.place)

    # --- window
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(T.fg_a(0.16), 1))
        bg = QColor(T.bg); bg.setAlphaF(0.86)
        p.setBrush(bg)
        p.drawRoundedRect(r, 10, 10)

    def place(self):
        self.adjustSize()
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        full, av = scr.geometry(), scr.availableGeometry()
        m = 8
        x = av.right() - self.width() - m
        if av.top() > full.top():                       # panel at top
            y = av.top() + m
        else:                                           # bottom (default) or side
            y = av.bottom() - self.height() - m
        if av.left() > full.left():
            x = av.left() + m
        self.move(max(av.left(), x), max(av.top(), y))
        if os.environ.get("FLING_DEBUG"):
            print("place", full, av, self.geometry(), scr.devicePixelRatio(), flush=True)

    def toggle(self):
        if self.isVisible():
            self.hide()
        elif time.monotonic() - self.hidden_at > 0.35:  # click on tray closed it just now
            self.show_panel()

    def _blur(self):
        """KWin blur behind the translucent panel (X11 property, honoured for XWayland)."""
        try:
            subprocess.Popen(["xprop", "-id", str(int(self.winId())), "-f", "_KDE_NET_WM_BLUR_BEHIND_REGION",
                              "32c", "-set", "_KDE_NET_WM_BLUR_BEHIND_REGION", "0"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def show_panel(self):
        self.sync()
        self.place()
        self.show()
        self._blur()
        self.activateWindow()
        self.state.refresh()

    def hideEvent(self, e):
        self.hidden_at = time.monotonic()
        self._wheel = [0.0, 0.0, 0.0]
        super().hideEvent(e)

    # ScrollRemote.swift + ScrollNav.swift: wheel/touchpad anywhere → d-pad
    def wheelEvent(self, e):
        if not self.state.tv_paired:
            return
        px = e.pixelDelta()
        if not px.isNull():
            dx, dy = px.x(), px.y()
        else:
            ad = e.angleDelta()
            dx, dy = ad.x() / 120 * 55, ad.y() / 120 * 55
        acc = self._wheel
        acc[0] += dx; acc[1] += dy
        if abs(acc[0]) > abs(acc[1]):
            acc[1] = 0
        else:
            acc[0] = 0
        now = time.monotonic()
        sent = 0
        while max(abs(acc[0]), abs(acc[1])) >= 55 and sent < 2 and now - acc[2] >= 0.09:
            if abs(acc[0]) > abs(acc[1]):
                self.state.tv_key(KEY["left"] if acc[0] > 0 else KEY["right"])
                acc[0] -= 55 if acc[0] > 0 else -55
            else:
                self.state.tv_key(KEY["up"] if acc[1] > 0 else KEY["down"])
                acc[1] -= 55 if acc[1] > 0 else -55
            acc[2] = now
            sent += 1
        e.accept()


# ---------------------------------------------------------------- tray (StatusItemController.swift + ContextMenuBuilder.swift)

# ---------------------------------------------------------------- "Connect TV" window: find / pair remote / ADB / YouTube / BT

def adb_state(ip: str) -> str:
    """device | unauthorized | closed | offline"""
    import socket as _s
    c = _s.socket(); c.settimeout(1.5)
    try:
        c.connect((ip, 5555))
    except OSError:
        return "closed"
    finally:
        c.close()
    serial = f"{ip}:5555"
    subprocess.run([_ADB, "connect", serial], capture_output=True, timeout=8)
    out = subprocess.run([_ADB, "devices"], capture_output=True, text=True, timeout=5).stdout
    for line in out.splitlines():
        if line.startswith(serial):
            return line.split()[1]
    return "offline"


class SetupWindow(QWidget):
    def __init__(self, state: State):
        super().__init__(None, Qt.WindowType.Window)
        self.state = state
        self.setWindowTitle("Fling — подключение ТВ")
        self.setMinimumWidth(460)
        self.adb = "?"
        self.found = []
        self.auto = False
        pal = self.palette(); pal.setColor(self.backgroundRole(), T.bg); self.setPalette(pal)
        self.setAutoFillBackground(True)
        v = QVBoxLayout(self); v.setContentsMargins(18, 16, 18, 16); v.setSpacing(10)
        v.addWidget(label("Подключение ТВ", 17, weight=QFont.Weight.Bold))
        v.addWidget(label("Одна кнопка находит ТВ в сети и подключает всё, что можно. "
                          "Шаги, где нужно ваше действие на ТВ, подскажут, что нажать.", 11.5, T.fg_a(0.6), wrap=True))
        self.go = QPushButton("Найти и подключить всё")
        self.go.setFixedHeight(34); self.go.setFont(font(13, QFont.Weight.DemiBold))
        self.go.setCursor(Qt.CursorShape.PointingHandCursor)
        self.go.setStyleSheet(f"QPushButton{{border:none;border-radius:8px;background:{Theme.css(T.accent)};color:white}}"
                              f"QPushButton:hover{{background:{Theme.css(T.acc_a(0.85))}}}")
        self.go.clicked.connect(self.run_all)
        v.addWidget(self.go)
        self.rows = {}
        for key, title in (("find", "Поиск ТВ в локальной сети"), ("remote", "Пульт ТВ (по сети)"),
                           ("adb", "ADB: что играет, название и обложка"), ("yt", "YouTube на ТВ (по коду)"),
                           ("bt", "Bluetooth-пульт (запасной)")):
            v.addWidget(self._row(key, title))
        v.addStretch(1)
        c2 = label(f'<a href="https://github.com/tatianathevisionary/fling" style="color:{Theme.css(T.fg_a(0.55))}">'
                   'Fling создан Damion Rashford и Tatiana · неофициальный порт</a>', 10.5, T.fg_a(0.45))
        c2.setOpenExternalLinks(True)
        v.addWidget(c2)
        self.timer = QTimer(self, interval=2000); self.timer.timeout.connect(self.refresh)
        state.changed.connect(self.sync)

    def _row(self, key, title):
        box = QFrame()
        box.setObjectName("card")
        box.setStyleSheet(f"QFrame#card{{background:{Theme.css(T.fg_a(0.045))};border-radius:10px}}")
        lay = QVBoxLayout(box); lay.setContentsMargins(12, 10, 12, 10); lay.setSpacing(6)
        top = QHBoxLayout(); top.setSpacing(8)
        mark = label("•", 15, T.fg_a(0.4), QFont.Weight.Bold); mark.setFixedWidth(18)
        top.addWidget(mark)
        top.addWidget(label(title, 12.5, weight=QFont.Weight.DemiBold), 1)
        btn = small_button("…", lambda: None)
        top.addWidget(btn)
        lay.addLayout(top)
        info = label("", 11, T.fg_a(0.6), wrap=True)
        lay.addWidget(info)
        extra = QWidget(); el = QHBoxLayout(extra); el.setContentsMargins(0, 0, 0, 0); el.setSpacing(6)
        lay.addWidget(extra)
        extra.hide()
        self.rows[key] = {"mark": mark, "btn": btn, "info": info, "extra": extra, "el": el}
        if key == "find":
            self.dev_menu = QMenu(btn)
            btn.clicked.connect(self.find)
        elif key == "remote":
            btn.clicked.connect(self.state.start_pairing)
            self.pin = field("Код с экрана ТВ, 6 символов", mono=True)
            self.pin.returnPressed.connect(self._pin)
            el.addWidget(self.pin, 1); el.addWidget(small_button("Связать", self._pin))
        elif key == "adb":
            btn.clicked.connect(self.adb_connect)
            self.wport = field("порт", mono=True); self.wport.setFixedWidth(80)
            self.wcode = field("код из 6 цифр", mono=True)
            el.addWidget(label("Беспроводная отладка:", 11, T.fg_a(0.6)))
            el.addWidget(self.wport); el.addWidget(self.wcode, 1)
            el.addWidget(small_button("Связать", self.adb_pair))
        elif key == "yt":
            btn.clicked.connect(lambda: (self.rows["yt"]["extra"].show(), self.ytcode.setFocus()))
            self.ytcode = field("12 цифр с экрана ТВ", mono=True)
            self.ytcode.returnPressed.connect(self._yt)
            el.addWidget(self.ytcode, 1); el.addWidget(small_button("Связать", self._yt))
        elif key == "bt":
            btn.clicked.connect(self.state.bt_pair)
        return box

    def _set(self, key, mark, info, btn=None, extra=None):
        r = self.rows[key]
        sym, col = {"ok": ("✓", T.green), "run": ("⏳", T.accent), "need": ("!", T.orange),
                    "fail": ("✗", T.red), "off": ("•", T.fg_a(0.4))}[mark]
        r["mark"].setText(sym); r["mark"].setStyleSheet(f"color:{Theme.css(col)}")
        r["info"].setText(info)
        if btn is None:
            r["btn"].hide()
        else:
            r["btn"].setText(btn); r["btn"].show()
        if extra is not None:
            r["extra"].setVisible(extra)

    def showEvent(self, e):
        self.timer.start(); self.refresh(); self.sync()
        if self.adb == "?":
            QTimer.singleShot(300, self.adb_connect)
        super().showEvent(e)

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    # ---- actions
    def run_all(self):
        self.auto = True
        self.find()

    def find(self):
        self._set("find", "run", "Ищу Chromecast / Google TV в сети… (до 10 с)", None)
        def done(found, e):
            if e or not found:
                self._set("find", "fail", f"Ничего не нашлось. Ноутбук и ТВ в одной сети Wi-Fi? {e or ''}", "Искать снова")
                self.auto = False
                return
            self.found = found
            s = self.state
            s.devices = found
            if not s.device or s.device["ip"] not in [d["ip"] for d in found]:
                s.select_device(found[0])
            s.conf.update(devices=found); save_conf(s.conf)
            s.changed.emit()
            if self.auto:
                QTimer.singleShot(200, self.auto_next)
        self.state.bg(catt_scan, done)

    def auto_next(self):
        s = self.state
        if s.ip and not s.tv_paired and s.tv_pairing in ("idle", "failed"):
            s.start_pairing()
        self.adb_connect()
        self.auto = False

    def _pin(self):
        code = self.pin.text().translate(Panel._RU_KEYS).upper().strip()
        if len(code) == 6:
            self.pin.clear()
            self.state.atv.finish_pairing(code)

    def adb_connect(self):
        ip = self.state.ip
        if not ip:
            return
        self._set("adb", "run", "Подключаюсь к ТВ по ADB…", None)
        self.state.bg(lambda: adb_state(ip), lambda r, e: self._adb_result(r or "offline"))

    def adb_pair(self):
        ip, port, code = self.state.ip, self.wport.text().strip(), re.sub(r"\D", "", self.wcode.text())
        if not (ip and port.isdigit() and len(code) == 6):
            self._set("adb", "need", "Введите порт и 6-значный код из «Беспроводной отладки» на ТВ.", "Проверить", True)
            return
        def work():
            r = subprocess.run([_ADB, "pair", f"{ip}:{port}", code], capture_output=True, text=True, timeout=20)
            return (r.stdout + r.stderr).strip()
        def done(out, e):
            ok = out and "Successfully paired" in out
            self._set("adb", "ok" if ok else "fail", "Связано. Подключаюсь…" if ok else f"Не вышло: {out or e}", "Проверить", not ok)
            if ok:
                self.adb_connect()
        self.state.bg(work, done)

    def _adb_result(self, st):
        self.adb = st
        if st == "device":
            self._set("adb", "ok", "Подключено: панель видит, что играет на ТВ.", "Проверить", False)
            self.state.poll_tvm()
        elif st == "unauthorized":
            self._set("adb", "need", "На экране ТВ появился запрос — нажмите «Разрешить» (лучше с галочкой «Всегда»).",
                      "Проверить", False)
        else:
            self._set("adb", "need", "ADB на ТВ выключен. На ТВ: Настройки → Система → О системе → 7 раз нажать «Сборка ОС» → "
                      "Настройки → Система → Для разработчиков → включить «Отладка по USB» и «Беспроводная отладка». "
                      "Если ТВ показывает код сопряжения — впишите порт и код ниже.", "Проверить", True)

    def _yt(self):
        code = re.sub(r"\D", "", self.ytcode.text())
        if len(code) >= 10:
            self.ytcode.clear()
            self.state.yt_link(code)

    def refresh(self):
        self.state.poll_bt()

    # ---- state → rows
    def sync(self):
        if not self.isVisible():
            return
        s = self.state
        if s.device and not self.rows["find"]["mark"].text() == "⏳":
            n = len(s.devices)
            self._set("find", "ok", f"Выбран: {s.device['name']} ({s.device['ip']})" + (f" · найдено {n}" if n > 1 else ""),
                      "Искать снова")
        elif not s.device:
            self._set("find", "off", "ТВ ещё не найден.", "Искать")
        p = s.tv_pairing
        if s.tv_paired:
            self._set("remote", "ok", "Связан: кнопки приложений, крестовина, пауза, ввод текста.", "Связать заново", False)
        elif p == "starting":
            self._set("remote", "run", "Связываюсь с ТВ…", None, False)
        elif p in ("pin", "verifying"):
            self._set("remote", "need", "Введите код, который показал ТВ (A–F и цифры; русская раскладка подойдёт).", None, True)
        elif p == "failed":
            self._set("remote", "fail", s.tv_pairing_msg or "Не вышло.", "Попробовать снова", False)
        else:
            self._set("remote", "off", "Не связан.", "Связать", False)
        if self.adb == "?":
            self._set("adb", "off", "Не проверено.", "Подключить")
        if s.lounge.is_linked:
            self._set("yt", "ok", "Связан: видно точное видео и время в YouTube.", None, False)
        elif s.yt_pairing == "linking":
            self._set("yt", "run", "Связываю…", None)
        else:
            self._set("yt", "off", "Необязательно (ADB уже показывает, что играет). "
                      "На ТВ: YouTube → Настройки → «Связать с ТВ по коду».", "Ввести код")
        b = s.bt
        if b.get("connected"):
            self._set("bt", "ok", f"Подключён: {b.get('peer') or 'ТВ'}", None)
        elif b.get("pairing"):
            self._set("bt", "run", "Виден ТВ 3 минуты. На ТВ: Настройки → Пульты и аксессуары → Добавить → «Fling Remote».", None)
        else:
            self._set("bt", "off", "Не нужен, если пульт по сети связан.", "Подключить")


class Tray(QObject):
    def __init__(self, state: State, panel: Panel):
        super().__init__()
        self.state, self.panel = state, panel
        self.icon = QSystemTrayIcon(tray_icon())
        self.icon.setToolTip("Fling")
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.build_menu)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self.activated)
        self.icon.show()
        self._casting = None
        state.changed.connect(self.sync)
        self.timer = QTimer(interval=2000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.panel.toggle()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            if self.state.panel == "casting":
                self.state.toggle_play()

    def poll(self):
        self.state.poll_bt()
        self.state.poll_tvm()
        if self.state.yt_live:
            self.state.changed.emit()
        if self.panel.isVisible():
            self.state.refresh()
        elif self.state.panel == "casting":
            self.state.refresh(with_tab=False)

    def sync(self):
        s = self.state
        casting = s.panel == "casting"
        if casting != self._casting:
            self.icon.setIcon(tray_icon(casting))
            self._casting = casting
        if casting:
            title = s.status.get("title") or "Воспроизводится"
            self.icon.setToolTip(f"Fling — {s.elapsed_label}  {title}")
        else:
            self.icon.setToolTip("Fling" + (f" — {s.device['name']}" if s.device else ""))

    def build_menu(self):
        s, m = self.state, self.menu
        m.clear()
        casting = s.panel == "casting"
        castable = s.panel == "idle_castable" or casting
        a = m.addAction("Отправить эту вкладку вместо" if casting else "Отправить эту вкладку", s.cast_tab)
        a.setShortcut(QKeySequence(GLOBAL_SHORTCUT)); a.setEnabled(castable)
        m.addAction("Отправить ссылку из буфера", s.cast_clipboard)
        m.addSeparator()
        if s.devices:
            dm = m.addMenu("Устройство")
            for d in s.devices:
                da = dm.addAction(d["name"], lambda d=d: s.select_device(d))
                da.setCheckable(True); da.setChecked(bool(s.device) and d["ip"] == s.device["ip"])
        m.addSeparator()
        m.addAction("Пауза" if s.is_playing else "Играть", s.toggle_play).setEnabled(casting)
        m.addAction(f"Назад {SEEK} с", lambda: s.seek(-SEEK)).setEnabled(casting)
        m.addAction(f"Вперёд {SEEK} с", lambda: s.seek(SEEK)).setEnabled(casting)
        vm = m.addMenu(f"Громкость — {s.volume}%")
        for lvl in range(0, 101, 10):
            va = vm.addAction(f"{lvl}%", lambda l=lvl: s.set_volume(l))
            va.setCheckable(True); va.setChecked(abs(s.volume - lvl) < 5)
        m.addSeparator()
        a = m.addAction("Остановить трансляцию", s.stop); a.setEnabled(casting)
        m.addSeparator()
        la = m.addAction("Запускать при входе", toggle_login)
        la.setCheckable(True); la.setChecked(login_enabled())
        m.addAction("Снимок экрана ТВ", s.tv_screenshot)
        m.addAction("Подключение ТВ…", self.panel.open_setup)
        m.addAction("Выйти из Fling", QApplication.quit)


# ---------------------------------------------------------------- single instance + commands

def send_to_running(cmd: str) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(SOCKET)
    if not sock.waitForConnected(400):
        return False
    sock.write(cmd.encode())
    sock.flush()
    sock.waitForBytesWritten(400)
    sock.disconnectFromServer()
    return True


def main():
    global T
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    app = QApplication(sys.argv)
    app.setApplicationName("Fling")
    app.setDesktopFileName("fling")
    app.setQuitOnLastWindowClosed(False)
    if send_to_running(cmd):
        return 0
    T = Theme(app)
    install_translation()
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    state = State()
    panel = Panel(state)
    tray = Tray(state, panel)

    def handle(c):
        c = c.strip()
        if c in ("show", "toggle"):
            panel.toggle() if c == "toggle" else panel.show_panel()
        elif c == "cast-tab":
            state.cast_tab(notify=not panel.isVisible())
        elif c == "clip":
            state.cast_clipboard(notify=not panel.isVisible())
        elif c == "screenshot":
            state.tv_screenshot()
        elif c == "setup":
            panel.open_setup()
        elif c == "quit":
            app.quit()

    QLocalServer.removeServer(SOCKET)
    server = QLocalServer()
    server.listen(SOCKET)
    def incoming():
        conn = server.nextPendingConnection()
        def read():
            handle(bytes(conn.readAll()).decode(errors="ignore"))
        conn.readyRead.connect(read)
        conn.disconnected.connect(conn.deleteLater)
    server.newConnection.connect(incoming)

    state.atv.connect(state.ip)
    state.refresh()
    if cmd not in ("tray", "background"):
        QTimer.singleShot(300, lambda: handle(cmd))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
