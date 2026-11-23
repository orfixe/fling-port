#!/usr/bin/env python3
"""Fling for Linux and Windows — tray caster + TV remote. A port of the macOS
Fling panel (menu-bar popover) to KDE Plasma and Windows: cast the current
browser tab to a Chromecast / Google TV via catt, and drive the TV over the
Android TV Remote protocol (androidtvremote2).  OS-specific code lives in
fling_platform.py; the browser extensions talk to fling_bridge.py."""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fling_platform as P

P.prepare_qt_env()

from PyQt6.QtCore import (QObject, Qt, QTimer, QRectF, QPoint, QPointF, QSize, QUrl,
                          QEvent, QPropertyAnimation, QEasingCurve,
                          QSequentialAnimationGroup, QParallelAnimationGroup,
                          pyqtSignal, pyqtProperty)
from PyQt6.QtGui import (QAction, QColor, QFont, QIcon, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap, QKeySequence, QShortcut,
                         QCursor, QGuiApplication, QKeyEvent)
from PyQt6.QtNetwork import (QLocalServer, QLocalSocket, QNetworkAccessManager,
                             QNetworkRequest)
from PyQt6.QtWidgets import (QApplication, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QMenu, QPushButton, QSizePolicy,
                             QSystemTrayIcon, QVBoxLayout, QWidget, QFrame,
                             QSlider, QToolButton)

HOME = P.HOME
CATT = P.find_catt()
DATA = P.DATA
CONF = P.CONF
SOCKET = "fling-panel"
SEEK = 30
GLOBAL_SHORTCUT = P.GLOBAL_SHORTCUT
SHORTCUT_LABEL = P.SHORTCUT_LABEL
VERSION = "0.3.0"

# ---------------------------------------------------------------- config

def load_conf() -> dict:
    try:
        with open(CONF, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_conf(c: dict) -> None:
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    tmp = CONF + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
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
    return P.process_running("firefox")

def read_firefox_tab() -> dict | None:
    """Active tab of the most recently used Firefox window: {url,title}."""
    import lz4.block
    files = P.firefox_session_files()
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

BROWSER_TAB_TTL = 180      # s: the extensions re-report the active tab every minute while the browser runs


def current_tab(browser_tab: dict | None) -> dict | None:
    """The tab to cast: what a Fling browser extension last reported, else Firefox's session store."""
    if browser_tab and time.monotonic() - browser_tab.get("at", 0) < BROWSER_TAB_TTL:
        return browser_tab
    try:
        return read_firefox_tab()
    except Exception:
        return None

# ---------------------------------------------------------------- catt (CattClient.swift + CattParser.swift)

class CattError(Exception):
    pass

def catt_run(args: list[str], timeout: float = 25) -> str:
    try:
        r = P.run(P.catt_cmd(CATT, args), capture_output=True, text=True, timeout=timeout,
                  encoding="utf-8", errors="replace")
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

def _is_ipv4(ip: str) -> bool:
    """Cast/ADB in Fling use IPv4 only — drop IPv6 / scoped addresses from discovery."""
    parts = (ip or "").split(".")
    return len(parts) == 4 and all(p.isdigit() and int(p) <= 255 for p in parts)


def catt_scan() -> list[dict]:
    out = catt_run(["scan"], timeout=30)
    devs = []
    for l in out.splitlines():
        parts = l.strip().split(" - ")
        if len(parts) >= 3 and not l.startswith("Scanning"):
            ip = parts[0].strip()
            if not _is_ipv4(ip):
                continue                  # ignore IPv6 hits (they re-prompt / fail ADB)
            devs.append({"ip": ip, "name": " - ".join(parts[1:-1]), "model": parts[-1]})
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

# Laptop / desktop keyboard → TV remote. Several aliases per action so EN, RU,
# numpad, media keys and common laptop layouts all work the same.
def _qt_keys(*names):
    out = []
    for n in names:
        k = getattr(Qt.Key, n, None)
        if k is not None:
            out.append(k)
    return out

# name → list of Qt.Key that send that TV key (when the remote is ready)
REMOTE_KEYS = {
    "up": _qt_keys("Key_Up", "Key_W", "Key_Cyrillic_Tse", "Key_8"),
    "down": _qt_keys("Key_Down", "Key_S", "Key_Cyrillic_Yeru", "Key_2"),
    "left": _qt_keys("Key_Left", "Key_A", "Key_Cyrillic_Ef", "Key_4"),
    "right": _qt_keys("Key_Right", "Key_D", "Key_Cyrillic_Ve", "Key_6"),
    "ok": _qt_keys("Key_Return", "Key_Enter", "Key_Select", "Key_5"),
    # Escape closes the panel (see shortcuts) — Backspace / Delete / BrowserBack go to the TV
    "back": _qt_keys("Key_Backspace", "Key_Delete", "Key_Back", "Key_0"),
    "home": _qt_keys("Key_Home", "Key_H", "Key_Cyrillic_Ha", "Key_3"),
    "vol_up": _qt_keys("Key_VolumeUp", "Key_Plus", "Key_Equal", "Key_BracketRight"),
    "vol_down": _qt_keys("Key_VolumeDown", "Key_Minus", "Key_BracketLeft"),
    "mute": _qt_keys("Key_VolumeMute", "Key_M", "Key_Cyrillic_Em"),
    "play_pause": _qt_keys("Key_MediaTogglePlayPause", "Key_MediaPlay", "Key_MediaPause",
                           "Key_Space", "Key_P", "Key_Cyrillic_Ze"),
    "ffwd": _qt_keys("Key_MediaNext", "Key_Period", "Key_Greater"),
    "rewind": _qt_keys("Key_MediaPrevious", "Key_Comma", "Key_Less"),
    "power": _qt_keys("Key_PowerOff", "Key_Standby"),
}
# Fast reverse lookup for the event filter
_REMOTE_BY_QT = {qt: name for name, keys in REMOTE_KEYS.items() for qt in keys}
# Keys that should auto-repeat while held (d-pad + volume)
_REMOTE_REPEAT = {"up", "down", "left", "right", "vol_up", "vol_down"}
# RU layout letters on the physical WASD / H / M / P keys (Qt may lack Cyrillic Key_* enums)
_REMOTE_BY_TEXT = {
    "ц": "up", "w": "up",
    "ф": "left", "a": "left",
    "ы": "down", "s": "down",
    "в": "right", "d": "right",
    "р": "home", "h": "home",
    "ь": "mute", "m": "mute",
    "з": "play_pause", "p": "play_pause",
}

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
        d = os.path.join(DATA, "atv", host)
        os.makedirs(d, exist_ok=True)
        return d

    def paired(self, host) -> bool:
        return bool(host) and os.path.exists(os.path.join(DATA, "atv", host, "paired"))

    def _make(self, host):
        from androidtvremote2 import AndroidTVRemote
        d = self._dir(host)
        return AndroidTVRemote(P.CLIENT_NAME, os.path.join(d, "cert.pem"), os.path.join(d, "key.pem"), host,
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
            return "Телевизор больше не доверяет этому компьютеру — свяжите его с ТВ заново."
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
                open(os.path.join(self._dir(self.host), "paired"), "w").close()
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
        why = P.MicRecorder.available()
        async def run():
            await self._connect(host)
            self._voice_stream = await self.remote.start_voice()
            stream, loop = self._voice_stream, self.loop
            self._rec = P.MicRecorder()
            try:
                self._rec.start(lambda chunk: loop.call_soon_threadsafe(stream.send_chunk, chunk))
            except Exception:
                self._rec = None
                stream.end()
                self._voice_stream = None
                raise
            self.voice.emit(True)
        if why:
            self.error.emit(why)
            return
        self._go(run())

    def voice_stop(self):
        async def run():
            if self._rec:
                self._rec.stop()
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
                async with YtLoungeApi(P.CLIENT_NAME, self._listener()) as api:
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
                async with YtLoungeApi(P.CLIENT_NAME) as api:
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
BT_SUPPORTED = P.IS_LINUX          # BlueZ GATT peripheral; Windows can't act as a BLE HID remote
BT_NAMES = {26: "power", 3: "home", 4: "back", 19: "up", 20: "down", 21: "left", 22: "right",
            23: "ok", 24: "vol_up", 25: "vol_down", 164: "mute", 85: "play_pause", 89: "rewind", 90: "ffwd"}


on_battery = P.on_battery


def bt_call(cmd: str, timeout=1.5) -> dict:
    if not BT_SUPPORTED:
        return {}
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
    if not BT_SUPPORTED:
        return
    try:
        bt_call("status", 0.5)
    except OSError:
        os.makedirs(f"{HOME}/.cache/fling", exist_ok=True)
        subprocess.Popen(["/usr/bin/python3", f"{DATA}/bt_remote.py", "serve"],
                         stdout=open(f"{HOME}/.cache/fling/bt.log", "a"), stderr=subprocess.STDOUT,
                         start_new_session=True)


# ---------------------------------------------------------------- TV media session over ADB (real now-playing)

_THUMB: dict = {}
_ADB = P.find_adb()

# Per-TV ADB state so we don't re-hit the TV's "Allow debugging?" every few seconds.
# state → device | unauthorized | closed | offline | missing; until → monotonic deadline
_ADB_HOST: dict[str, tuple[str, float]] = {}
_ADB_BACKOFF = {"unauthorized": 300.0, "closed": 60.0, "offline": 30.0, "missing": 3600.0}


def _adb_remember(ip: str, state: str):
    _ADB_HOST[ip] = (state, time.monotonic() + _ADB_BACKOFF.get(state, 15.0))


def _adb_cached(ip: str) -> str | None:
    hit = _ADB_HOST.get(ip)
    if not hit:
        return None
    state, until = hit
    if state == "device":
        return None                       # always re-check a working link lightly via shell
    if time.monotonic() < until:
        return state
    return None


def _adb_serial(ip: str, port: int | None = None) -> str:
    """`ip:port` for adb -s / connect. Wireless debugging often uses a non-5555 port."""
    p = int(port or load_conf().get("adb_port") or 5555)
    return f"{ip}:{p}"


def adb_cmd(ip: str, args: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    """Run `adb -s ip:port …args` against the remembered wireless port."""
    return P.run([_ADB, "-s", _adb_serial(ip), *args],
                 capture_output=True, text=True, timeout=timeout,
                 encoding="utf-8", errors="replace")


def adb_now_playing(ip: str) -> dict | None:
    """Parse `dumpsys media_session` on the TV: the active app's real title,
    artist, play state and position. None when ADB isn't authorised."""
    if not _is_ipv4(ip) or not P.adb_available():
        return None
    cached = _adb_cached(ip)
    if cached in ("unauthorized", "closed", "missing", "offline"):
        return None                       # do not `adb connect` again — that re-prompts the TV
    serial = _adb_serial(ip)
    def sh(cmd, t=6):
        return P.run([_ADB, "-s", serial, "shell", cmd], capture_output=True, text=True, timeout=t,
                     encoding="utf-8", errors="replace")
    r = sh("dumpsys media_session; echo __UP__; cat /proc/uptime")
    if r.returncode != 0 or "__UP__" not in r.stdout:
        # Only one quiet reconnect attempt; skip if the TV already refused us
        if _adb_cached(ip) == "unauthorized":
            return None
        P.run([_ADB, "connect", serial], capture_output=True, timeout=6)
        # If devices says unauthorized, remember and stop hammering the TV
        out = P.run([_ADB, "devices"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            if serial in line.split()[:1] or line.startswith(serial):
                if "unauthorized" in line:
                    _adb_remember(ip, "unauthorized")
                    return None
        r = sh("dumpsys media_session; echo __UP__; cat /proc/uptime")
        if r.returncode != 0 or "__UP__" not in r.stdout:
            return None
    _ADB_HOST[ip] = ("device", 0.0)
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
        self.browser_tab = None      # active tab reported by a browser extension (fling_bridge)
        self.panel_visible = lambda: False
        self.status = {}
        self.volume = 50
        # Drop any saved IPv6 entries — Cast/ADB here are IPv4-only
        self.devices = [d for d in self.conf.get("devices", []) if _is_ipv4(d.get("ip", ""))]
        saved = self.conf.get("device")
        self.device = saved if saved and _is_ipv4(saved.get("ip", "")) else (self.devices[0] if self.devices else None)
        self.cast_in_flight = False
        self.scanning = False
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
        # Bluetooth remote service is started only on demand ("Connect as Bluetooth remote"):
        # advertising all the time costs battery when nothing is paired.
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
            if not P.adb_available():
                return {"error": "Снимок экрана: ADB не установлен — откройте «Подключение ТВ…» и скачайте platform-tools."}
            st = adb_state(ip, force=True)           # user asked for a shot — one connect is ok
            if st != "device":
                return {"error": {"unauthorized": "Снимок экрана: подтвердите на ТВ «Разрешить отладку».",
                                  "closed": "Снимок экрана работает через ADB — включите его: «Подключение ТВ…» → ADB.",
                                  "missing": "Снимок экрана: ADB не установлен — откройте «Подключение ТВ…» и скачайте platform-tools."}
                        .get(st, "Снимок экрана: ADB не подключён — откройте «Подключение ТВ…».")}
            r = P.run([_ADB, "-s", _adb_serial(ip), "exec-out", "screencap", "-p"],
                               capture_output=True, timeout=20)
            if r.returncode != 0 or not r.stdout.startswith(b"\x89PNG"):
                return {"error": "Снимок экрана не получился: " + (r.stderr.decode(errors="ignore").strip() or "нет данных")}
            pics = P.pictures_dir()
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
            P.open_path(r["path"])
            if r["black"]:
                self._set(last_error="Снимок чёрный: видео защищено от снимков (DRM у YouTube/Netflix). "
                                     "Меню и главный экран ТВ снимаются нормально.")
            else:
                notify_send(f"Снимок экрана ТВ: {r['path']}")
                self._set(last_error=None)
        self.bg(work, done)

    # adb install failure codes → what to do about them
    _APK_ERRORS = {
        "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "на ТВ уже есть это приложение с другой подписью — удалите его на ТВ и попробуйте снова.",
        "INSTALL_FAILED_VERSION_DOWNGRADE": "на ТВ уже стоит более новая версия.",
        "INSTALL_FAILED_NO_MATCHING_ABIS": "этот APK собран для другого процессора — нужна версия для armeabi-v7a / arm64.",
        "INSTALL_FAILED_OLDER_SDK": "этот APK требует более новый Android, чем на ТВ.",
        "INSTALL_FAILED_INSUFFICIENT_STORAGE": "на ТВ не хватает места.",
        "INSTALL_PARSE_FAILED_NO_CERTIFICATES": "APK не подписан.",
        "INSTALL_FAILED_INVALID_APK": "файл повреждён или это не APK.",
        "INSTALL_PARSE_FAILED_NOT_APK": "файл повреждён или это не APK.",
        "INSTALL_FAILED_MISSING_SPLIT": "приложение состоит из нескольких APK — выберите их все сразу.",
        "INSTALL_FAILED_USER_RESTRICTED": "ТВ запретил установку — подтвердите её на экране ТВ.",
    }

    def tv_install_apk(self, paths: list[str]):
        """Install an APK on the TV over ADB (`adb install -r`). Several files at once are
        the parts of one split app (`install-multiple`)."""
        paths = [p for p in paths if p]
        ip = self.ip
        if not paths:
            return
        if not ip:
            self._set(last_error="Устройство не выбрано")
            return
        name = os.path.basename(paths[0])

        def work():
            if not P.adb_available():
                return {"error": "Установка APK: ADB не установлен — откройте «Подключение ТВ…» и скачайте platform-tools."}
            st = adb_state(ip, force=True)
            if st != "device":
                return {"error": {"unauthorized": "Установка APK: подтвердите на ТВ «Разрешить отладку».",
                                  "closed": "Установка APK работает через ADB — включите его: «Подключение ТВ…» → ADB."}
                        .get(st, "Установка APK: ADB не подключён — откройте «Подключение ТВ…».")}
            cmd = ["install-multiple", "-r"] if len(paths) > 1 else ["install", "-r"]
            try:
                r = adb_cmd(ip, cmd + paths, timeout=600)
            except subprocess.TimeoutExpired:
                return {"error": "Установка APK: ТВ не ответил за 10 минут."}
            out = f"{r.stdout}\n{r.stderr}"
            if r.returncode == 0 and "Success" in out:
                return {"ok": True}
            m = re.search(r"(INSTALL_[A-Z_]+)", out)
            why = self._APK_ERRORS.get(m[1]) if m else None
            last = next((l.strip() for l in reversed(out.splitlines()) if l.strip()), "нет ответа")
            return {"error": "Установка APK не удалась: " + (why or (m[1] if m else last))}

        def run():
            try:
                r, e = work(), None
            except Exception as ex:
                r, e = None, ex
            self.bridge.call.emit(lambda: done(r, e))

        def done(r, e):
            print("apk:", name, e or r, flush=True)
            if e or not r:
                self._set(last_error=f"Установка APK: {e}")
            elif r.get("error"):
                self._set(last_error=r["error"])
            else:
                self._set(last_error=None)
                notify_send(f"Установлено на ТВ: {name}")

        notify_send(f"Устанавливаю на ТВ: {name}…")
        # its own thread: a big APK can take minutes and must not hold up the pool
        threading.Thread(target=run, daemon=True, name="fling-apk").start()

    def adb_ok(self) -> bool:
        """True when this TV recently answered as an authorized ADB device."""
        ip = self.ip
        if not ip or not P.adb_available():
            return False
        hit = _ADB_HOST.get(ip)
        return bool(hit and hit[0] == "device")

    def adb_action(self, action: str):
        """Everyday ADB actions once the TV is authorised."""
        ip = self.ip
        if not ip:
            self._set(last_error="Устройство не выбрано")
            return
        serial = _adb_serial(ip)

        def work():
            if action == "disconnect":
                P.run([_ADB, "disconnect", serial], capture_output=True, timeout=8)
                _adb_remember(ip, "offline")
                return {"ok": True, "msg": "ADB отключён", "state": "off"}
            if action == "reboot":
                r = adb_cmd(ip, ["reboot"], timeout=15)
                _adb_remember(ip, "closed")
                return {"ok": r.returncode == 0, "msg": "ТВ перезагружается…" if r.returncode == 0 else (r.stderr or r.stdout or "reboot failed"),
                        "state": "off"}
            if action == "wake":
                r = adb_cmd(ip, ["shell", "input", "keyevent", "KEYCODE_WAKEUP"])
                return {"ok": r.returncode == 0, "msg": "ТВ разбужен" if r.returncode == 0 else (r.stderr or "не вышло")}
            if action == "sleep":
                # SLEEP first; fall back to POWER if the TV ignores it
                r = adb_cmd(ip, ["shell", "input", "keyevent", "KEYCODE_SLEEP"])
                if r.returncode != 0:
                    r = adb_cmd(ip, ["shell", "input", "keyevent", "KEYCODE_POWER"])
                return {"ok": r.returncode == 0, "msg": "Экран выключен" if r.returncode == 0 else (r.stderr or "не вышло")}
            if action == "settings":
                r = adb_cmd(ip, ["shell", "am", "start", "-a", "android.settings.SETTINGS"])
                return {"ok": r.returncode == 0, "msg": "Открыл настройки ТВ" if r.returncode == 0 else (r.stderr or "не вышло")}
            if action == "apps":
                r = adb_cmd(ip, ["shell", "am", "start", "-a", "android.settings.APPLICATION_SETTINGS"])
                return {"ok": r.returncode == 0, "msg": "Открыл список приложений" if r.returncode == 0 else (r.stderr or "не вышло")}
            if action == "force_stop":
                # Stop whatever currently has window focus (YouTube, Netflix, …)
                dump = adb_cmd(ip, ["shell", "dumpsys", "window", "windows"], timeout=12)
                pkg = None
                for line in (dump.stdout or "").splitlines():
                    if "mCurrentFocus" in line or "mFocusedApp" in line:
                        m = re.search(r"([a-zA-Z0-9_.]+)/[a-zA-Z0-9_.]+", line)
                        if m and m[1] not in ("null", "com.google.android.apps.tv.launcherx",
                                              "com.google.android.apps.tv.launcher"):
                            pkg = m[1]; break
                if not pkg:
                    return {"ok": False, "msg": "Не вижу активного приложения"}
                r = adb_cmd(ip, ["shell", "am", "force-stop", pkg])
                return {"ok": r.returncode == 0,
                        "msg": f"Закрыл {app_name(pkg) or pkg}" if r.returncode == 0 else (r.stderr or "не вышло")}
            if action == "home":
                r = adb_cmd(ip, ["shell", "input", "keyevent", "KEYCODE_HOME"])
                return {"ok": r.returncode == 0, "msg": "Домой" if r.returncode == 0 else (r.stderr or "не вышло")}
            return {"ok": False, "msg": f"Неизвестное действие: {action}"}

        def done(r, e):
            if e or not r:
                self._set(last_error=f"ADB: {e or 'ошибка'}")
                return
            if not r.get("ok"):
                self._set(last_error=f"ADB: {r.get('msg')}")
                return
            self._set(last_error=None)
            if r.get("msg"):
                notify_send(r["msg"])
            if r.get("state") == "off":
                self.tvm = {}
                self.changed.emit()
                self._set_adb_ui("off")

        self.bg(work, done)

    def _set_adb_ui(self, st: str, err: str = ""):
        """Push ADB badge state to the panel and Connect TV window, if open."""
        panel = getattr(self, "_panel_ref", None)
        targets = []
        if panel and getattr(panel, "adb_badge", None):
            targets.append(panel)
        if panel and getattr(panel, "_setup", None):
            targets.append(panel._setup)
        setup = getattr(self, "_setup_ref", None)
        if setup:
            targets.append(setup)
        for w in targets:
            if getattr(w, "adb_badge", None):
                w.adb_badge.set_state(st, err)
            if hasattr(w, "adb_actions"):
                w.adb_actions.setVisible(st == "ok")
            if hasattr(w, "adb"):
                w.adb = "device" if st == "ok" else ("?" if st == "off" else w.adb)

    def poll_tvm(self):
        ip = self.ip
        if not ip or self._tvm_busy:
            return
        if not P.adb_available() or not _is_ipv4(ip):
            return
        # Quiet while the TV is refusing / ADB is off — polling would re-prompt
        if _adb_cached(ip) in ("unauthorized", "closed", "missing", "offline"):
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
            tab = current_tab(self.browser_tab) if with_tab else None
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
        self.scanning = True
        self.changed.emit()
        def done(found, e):
            self.scanning = False                      # search finished (UI stops showing "Searching…")
            found = list(found or [])                  # catt scan result, may be empty
            known = {d["ip"] for d in found}           # IPs seen on the network right now
            found += [d for d in self.conf.get("manual", []) if d["ip"] not in known]   # TVs added by IP
            if not found:
                self.changed.emit()
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
        if self.device and d["ip"] == self.device["ip"]:   # already on this TV: nothing to do
            return
        self.device = d
        self.status = {}
        self.tvm = {}                      # now-playing, remote state and screenshots belong to the old TV
        self.tv_on = self.tv_app = None
        self.tv_pairing, self.tv_pairing_msg = "idle", ""
        self.conf["device"] = d
        save_conf(self.conf)
        self.last_error = None
        self.atv.connect(self.ip)
        self.changed.emit()
        self.refresh()

    def device_label(self, d):
        """Menu text for one TV: its name plus hints (address if names clash, remote paired, added by IP)."""
        marks = []
        if sum(1 for x in self.devices if x["name"] == d["name"]) > 1:
            marks.append(d["ip"])          # identical names: tell them apart by address
        if self.atv.paired(d["ip"]):               # network remote already paired with this TV
            marks.append("пульт ✓")
        if d.get("manual"):                        # added by hand, not found by discovery
            marks.append("по IP")
        return d["name"] + (f"  ·  {', '.join(marks)}" if marks else "")

    def add_device_ip(self, ip):
        """Add a TV that discovery can't see (other subnet, mDNS blocked) by its IP address."""
        ip = ip.strip()
        parts = ip.split(".")                      # IPv4: 4 numbers, each 0..255
        if len(parts) != 4 or not all(x.isdigit() and int(x) <= 255 for x in parts):
            self._set(last_error="Это не похоже на IP-адрес (пример: 192.168.1.50).")
            return
        def work():
            import socket as _s
            c = _s.socket(_s.AF_INET, _s.SOCK_STREAM); c.settimeout(2)  # IPv4 only
            try:
                c.connect((ip, 8009))                  # 8009 = Google Cast port
            except OSError:
                raise RuntimeError(f"по адресу {ip} нет Chromecast / Google TV (порт 8009 закрыт)")
            finally:
                c.close()
            name = ip
            try:   # the TV's own name from its DIAL device description
                import urllib.request
                with urllib.request.urlopen(f"http://{ip}:8008/ssdp/device-desc.xml", timeout=4) as r:   # closes the connection
                    xml = r.read().decode("utf-8", "ignore")
                m = re.search(r"<friendlyName>([^<]+)</friendlyName>", xml)
                name = m[1].strip() if m else ip
            except Exception:
                pass
            return {"ip": ip, "name": name, "model": "", "manual": True}
        def done(d, e):
            if e or not d:
                self._set(last_error=f"Добавить ТВ: {e}")
                return
            manual = [x for x in self.conf.get("manual", []) if x["ip"] != ip] + [d]   # replace an older entry
            self.conf["manual"] = manual                                               # kept across restarts
            if ip not in [x["ip"] for x in self.devices]:
                self.devices = self.devices + [d]
            self.conf["devices"] = self.devices
            save_conf(self.conf)
            self.select_device(d)                      # switch to the TV that was just added
        self.bg(work, done)                            # network checks run off the UI thread

    def forget_device(self, d):
        """Remove a TV that was added by IP (discovered TVs come back on the next search)."""
        self.conf["manual"] = [x for x in self.conf.get("manual", []) if x["ip"] != d["ip"]]
        self.devices = [x for x in self.devices if x["ip"] != d["ip"]]
        self.conf["devices"] = self.devices
        save_conf(self.conf)
        if self.device and self.device["ip"] == d["ip"]:
            if self.devices:
                self.select_device(self.devices[0])
            else:
                self.device = None
                self.status = {}
                self.conf["device"] = None
                save_conf(self.conf)
        self.changed.emit()

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

    def cast(self, url, notify=False, start=None, title=None):
        kind, reason = classify(url)
        if kind == "none":
            self._set(last_error=reason)
            if notify:
                notify_send(f"Нельзя отправить: {reason}")
            return
        if self.cast_in_flight:
            self._set(last_error="Уже отправляю…")
            return
        if not self.ip:
            self._set(last_error="Устройство не выбрано")
            return
        self.cast_in_flight = True
        self.changed.emit()
        opts = ["-f"] if kind == "direct" and not os.path.isfile(url) else []
        if start and start > 3:                       # "Resume on TV at 12:34"
            opts += ["-t", str(int(start))]
        if title and kind == "direct":
            opts += ["-l", title[:200]]
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
                    notify_send("Нет открытой вкладки браузера")
                return
            self.tab = tab
            url, start = tab["url"], None
            m = tab.get("media") or {}
            if m.get("src") and classify(url)[0] == "maybe" and classify(m["src"])[0] == "direct":
                url = m["src"]                         # the page's own stream, for sites yt-dlp can't crack
            if m.get("time") and m.get("duration") and m["duration"] - m["time"] > 10:
                start = m["time"]
            self.cast(url, notify, start=start, title=tab.get("title"))
        self.bg(lambda: current_tab(self.browser_tab), done)

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
                                     "Нажмите «Связать с ТВ» ниже — тогда пауза пойдёт как кнопка пульта.")
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
        # Damion's Fling: d-pad / typing need the Android TV Remote (or BT) session
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
        # Same path as upstream Fling: Android TV Remote / BT — not ADB keyevents
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
    ("Вкл/выкл ТВ", "Toggle TV power"), ("Включить ТВ", "Turn on the TV"), ("Выключить ТВ", "Turn TV off"),
    ("Воспроизводится", "Playing"), ("Выйти из Fling", "Quit Fling"),
    ("Остановить голосовой поиск", "Stop voice search"),
    ("Голосовой поиск уже идёт.", "Voice search is already running."), ("Голосовой поиск", "Voice search"),
    ("Движок трансляции не установлен. Установите его один раз — Fling его найдёт.",
     "The Cast engine isn't installed. Install it once and Fling will find it."),
    ("Домой", "Home"), ("Запускать при входе", "Start at login"),
    ("или крутите колёсико / тачпад в любом месте панели", "or scroll anywhere to move around the TV"),
    ("Клавиатура: стрелки/WASD · Enter=OK · Backspace=Назад · Home · +/− громкость · Пробел пауза · колёсико",
     "Keyboard: arrows/WASD · Enter=OK · Backspace=Back · Home · +/− volume · Space pause · scroll"),
    ("Громкость −  (− / [ / VolumeDown)", "Volume −  (− / [ / VolumeDown)"),
    ("Громкость +  (+ / ] / VolumeUp)", "Volume +  (+ / ] / VolumeUp)"),
    ("Без звука  (M / VolumeMute)", "Mute  (M / VolumeMute)"),
    ("Вверх  (↑ / W)", "Up  (↑ / W)"),
    ("Вниз  (↓ / S)", "Down  (↓ / S)"),
    ("Влево  (← / A)", "Left  (← / A)"),
    ("Вправо  (→ / D)", "Right  (→ / D)"),
    ("OK  (Enter / Space)", "OK  (Enter / Space)"),
    ("Назад  (Backspace / Delete)", "Back  (Backspace / Delete)"),
    ("Домой  (Home / H)", "Home  (Home / H)"),
    ("ADB неактивен", "ADB inactive"),
    ("ADB неактивен — нажмите, чтобы подключить", "ADB inactive — click to connect"),
    ("Подключаюсь к ТВ по ADB…", "Connecting to the TV over ADB…"),
    ("ADB подключён — нажмите, чтобы проверить снова", "ADB connected — click to check again"),
    ("Ошибка ADB — нажмите, чтобы повторить", "ADB error — click to retry"),
    ("Подключаюсь…", "Connecting…"),
    ("Команды ADB", "ADB commands"),
    ("Разбудить", "Wake"),
    ("Усыпить", "Sleep"),
    ("Снимок", "Screenshot"),
    ("Настройки", "Settings"),
    ("Приложения", "Apps"),
    ("Закрыть приложение", "Force-stop app"),
    ("Перезагрузить", "Reboot"),
    ("Отключить ADB", "Disconnect ADB"),
    ("Перезагрузить ТВ", "Reboot TV"),
    ("Настройки ТВ", "TV settings"),
    ("Снимок экрана", "Screenshot"),
    ("ADB отключён", "ADB disconnected"),
    ("ТВ перезагружается…", "TV is rebooting…"),
    ("ТВ разбужен", "TV woken"),
    ("Экран выключен", "Screen off"),
    ("Открыл настройки ТВ", "Opened TV settings"),
    ("Открыл список приложений", "Opened app list"),
    ("Не вижу активного приложения", "No foreground app seen"),
    ("Закрыл ", "Closed "),
    ("Connected!", "Connected!"),
    ("С ТВ: Беспроводная отладка → «IP-адрес и порт». Клик по Android — запрос ADB.",
     "From the TV: Wireless debugging → “IP address & port”. Click the Android to request ADB."),

    ("и тут появятся настоящие обложка, название и время.", "and the real artwork, title and time will show here."),
    ("код не подошёл", "the code didn't work"),
    ("Код не совпадает — посмотрите на экран ТВ ещё раз.", "That code doesn't match — read it off the TV again."),
    ("Код YouTube — это 12 цифр с экрана ТВ.", "The YouTube code is the 12 digits on the TV screen."),
    ("можно отправить", "castable"), ("можно попробовать", "worth a try"),
    ("Нажмите «Связать с ТВ» ниже — тогда пауза пойдёт как кнопка пульта.",
     "Click “Pair with TV” below and pause will be sent as a remote button."),
    ("Напечатать на ТВ", "Type on TV"), ("Печать на ТВ", "Type on TV"),
    ("Клавиатура — печать на ТВ", "Keyboard — type on the TV"),
    ("Связать с ТВ", "Pair with TV"), ("Связать с ТВ", "Pair with TV"),
    ("На ТВ: YouTube → Настройки → «Подключить телефон / Связать с ТВ по коду». ",
     "On the TV: YouTube → Settings → “Link with TV code”. "),
    ("НА ТЕЛЕВИЗОРЕ", "ON THE TV"),
    ("Нет открытой вкладки браузера", "No browser tab open"), ("Нет открытой страницы", "No page open"),
    ("Нет parecord/pw-record для записи микрофона.", "parecord/pw-record not found for the microphone."),
    ("Остановить трансляцию", "Stop casting"),
    ("Откройте вкладку в браузере и попробуйте снова.", "Open a tab in your browser, then try again."),
    ("Нет модуля sounddevice для записи микрофона.", "The sounddevice module is missing for the microphone."),
    ("Микрофон недоступен: ", "Microphone unavailable: "),
    ("Снимок экрана ТВ: ", "TV screenshot: "),
    ("Уведомлять о смене видео", "Notify when the video changes"), ("Сейчас на ТВ: ", "Now on TV: "), ("Возобновить на ТВ с ", "Resume on TV at "),
    ("Отмена", "Cancel"), ("Отправить скопированную ссылку", "Cast copied link"),
    ("Отправить эту вкладку вместо", "Cast this tab instead"), ("Отправить эту вкладку", "Cast this tab"),
    ("Отправляю…", "Casting…"), ("Пауза / Играть", "Play / Pause"), ("Пауза на ТВ", "Pause on TV"),
    ("Предыдущее видео", "Previous video"), ("Следующее видео", "Next video"), ("Пробел", "Space"),
    ("Проверьте код — он действует несколько минут.", "Check the code — it's valid for a few minutes."),
    ("Свяжите один раз с ТВ, чтобы открыть приложения, стрелки и ввод текста.",
     "Pair once with the TV to unlock apps, the d-pad, and typing."),
    ("Связать с YouTube на ТВ — видеть, что играет", "Link YouTube on the TV — see what's playing"),
    ("Связать", "Pair"), ("Скопировать команду установки", "Copy install command"),
    ("Слушаю — нажмите микрофон ещё раз, когда закончите.", "Listening — click the mic again when done."),
    ("Сменить", "Switch"), ("ТВ выключен", "TV is off"),
    ("ТВ не сообщает, что сейчас играет. Свяжите с YouTube ниже — ",
     "The TV doesn't report what's playing. Link YouTube below — "),
    ("Текст попадёт в активное поле ввода на телевизоре", "Lands in whatever text field is focused on the TV"),
    ("Телевизор больше не доверяет этому компьютеру — свяжите его с ТВ заново.",
     "The TV no longer trusts this computer — pair with the TV again."),
    ("Телевизор закрыл соединение.", "The TV closed the connection."),
    ("Телевизор игнорирует паузу по Chromecast (так ведёт себя YouTube на ТВ). ",
     "The TV ignores pause over Chromecast (YouTube on the TV does that). "),
    ("Телевизор не ответил. Убедитесь, что он включён, и попробуйте снова.",
     "The TV did not respond. Make sure it is awake, then try again."),
    ("Телевизор не ответил.", "The TV didn't answer."),
    ("Устройство не выбрано", "No device selected"), ("Уже отправляю…", "Already sending…"),
    ("Уже отправляю…", "Already sending…"), ("Устройство не найдено", "No device found"),
    ("Устройство", "Device"), ("Это не веб-страница", "Not a web page"),
    ("catt не ответил вовремя и был остановлен. Попробуйте ещё раз.", "catt did not finish in time and was stopped. Try again."),
    ("catt не установлен", "catt is not installed"), ("Fling нужен catt", "Fling needs catt"),
    ("YouTube на ТВ не отвечает", "YouTube on the TV isn't answering"),
    ("Не удалось связать с YouTube: ", "Couldn't link YouTube: "),
    ("Не удалось связаться с ТВ", "Couldn't reach the TV"), ("Нельзя отправить: ", "Can't cast: "),
    ("catt завершился с ошибкой", "catt failed"),
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
EN_PHRASES += [   # rows that had no English text yet
    ("Снимок экрана ТВ", "TV screenshot"), ("Сделать снимок", "Take screenshot"), ("Как пользоваться", "How to use"),
    ("Подключение ТВ…", "Connect TV…"), ("Fling — подключение ТВ", "Fling — Connect TV"),
    ("Fling — как пользоваться", "Fling — How to use"),
    ("Сменить ТВ", "Switch TV"), ("Выбрать ТВ", "Choose TV"), ("Выбрать", "Select"),
    ("Компактный вид", "Compact view"), ("Пауза / Играть", "Play / Pause"), ("Полный вид", "Full view"),
    ("Уведомления включены", "Notifications on"), ("Уведомления выключены", "Notifications off"),
    ("Запускать при входе", "Start at login"), ("Связать с ТВ", "Pair with TV"),
    ("Отправить скопированную ссылку", "Cast copied link"), ("Ищу ТВ…", "Looking for TVs…"), ("Телевизор", "TV"), ("ТВ…", "TV…"),
    ("Fling создан Damion Rashford и Tatiana · неофициальный порт",
     "Fling by Damion Rashford and Tatiana · unofficial port"),
]
EN_PHRASES += [   # Connect TV window, TV menu, screenshots
    ('Добавить ТВ по IP…', 'Add TV by IP…'),
    ('Добавить ТВ по IP', 'Add TV by IP'),
    ('IP-адрес телевизора (Настройки → Сеть на ТВ):', "The TV's IP address (Settings → Network on the TV):"),
    ('ТВ не найдены', 'No TVs found'),
    ('Убрать добавленный вручную', 'Remove the TV added by IP'),
    ('Найти ТВ заново', 'Search for TVs again'),
    ('Ищу…', 'Searching…'),
    ('пульт ✓', 'remote ✓'),
    ('по IP', 'by IP'),
    ('Это не похоже на IP-адрес (пример: 192.168.1.50).', "That doesn't look like an IP address (e.g. 192.168.1.50)."),
    (' нет Chromecast / Google TV (порт 8009 закрыт)', ' there is no Chromecast / Google TV (port 8009 is closed)'),
    ('по адресу ', 'at '),
    ('Добавить ТВ: ', 'Add TV: '),
    ('Набирайте мышкой или с клавиатуры — русская раскладка тоже подойдёт.', 'Click the keys or type — a Russian keyboard layout works too.'),
    ('Fling — подключение ТВ', 'Fling — Connect TV'),
    ('Подключение ТВ', 'Connect TV'),
    ('Одна кнопка находит ТВ в сети и подключает всё, что можно. Шаги, где нужно ваше действие на ТВ, подскажут, что нажать.', 'One button finds the TV on your network and connects everything it can. Steps that need you on the TV say what to press.'),
    ('Одна кнопка находит ТВ в сети и подключает пульт и ADB. Шаги, где нужно ваше действие на ТВ, подскажут, что нажать.', 'One button finds the TV on your network and connects the remote and ADB. Steps that need you on the TV say what to press.'),
    ('Не проверено — нажмите «Подключить», когда будете у ТВ.', 'Not checked yet — press “Connect” when you are at the TV.'),
    ('На экране ТВ появился запрос — нажмите «Разрешить» (лучше с галочкой «Всегда»). Пока не нажмёте — Fling больше не будет спрашивать сам.',
     'The TV is asking for permission — choose “Allow” (ideally with “Always”). Until you do, Fling will not ask again on its own.'),
    ('ADB не найден на этом компьютере. Скачайте Android platform-tools ', 'ADB was not found on this computer. Download Android platform-tools '),
    ('zip для этой ОС', 'zip for this OS'),
    ('страница Google', 'Google’s page'),
    ('распакуйте папку platform-tools в ', 'unzip the platform-tools folder into '),
    (' или положите ', ' or put '),
    (' в PATH — затем нажмите «Проверить».', ' on your PATH — then press “Check”.'),
    ('Снимок экрана: ADB не установлен — откройте «Подключение ТВ…» и скачайте platform-tools.',
     'Screenshot: ADB is not installed — open “Connect TV…” and download platform-tools.'),
    ('Сначала найдите или выберите ТВ выше — без IP ADB подключить нельзя.',
     'Find or select a TV above first — ADB needs an IP.'),
    ('порт сопряжения', 'pairing port'),
    ('порт подключения (часто не 5555)', 'connection port (often not 5555)'),
    ('Порт подключения:', 'Connection port:'),
    ('Порт ADB:', 'ADB port:'),
    ('IP ТВ:', 'TV IP:'),
    ('Порт:', 'Port:'),
    ('порт, напр. 5555', 'port, e.g. 5555'),
    ('Сопряжение:', 'Pairing:'),
    ('например 5555 или 37123', 'e.g. 5555 or 37123'),
    ('Можно вписать IP и порт вручную или вставить «IP:порт» в любое поле.',
     'You can type the IP and port by hand, or paste “IP:port” into either field.'),
    ('Порт ADB — только цифры (или вставьте «IP:порт» с экрана ТВ).',
     'ADB port must be digits (or paste “IP:port” from the TV screen).'),
    ('IP ТВ — четыре числа через точку (пример: 192.168.1.50) или «IP:порт».',
     'TV IP is four numbers with dots (e.g. 192.168.1.50) or “IP:port”.'),
    ('IP ТВ — четыре числа через точку (пример: 192.168.1.50).',
     'TV IP is four numbers with dots (e.g. 192.168.1.50).'),
    ('Впишите IP телевизора вручную или найдите ТВ выше.',
     'Type the TV IP by hand, or find a TV above.'),
    ('Впишите IP и порт с ТВ (Беспроводная отладка → «IP-адрес и порт») и нажмите «Подключить». Если ТВ показывает код сопряжения — заполните «Сопряжение» → «Связать».',
     'Type the IP and port from the TV (Wireless debugging → “IP address & port”) and press “Connect”. If the TV shows a pairing code, fill in Pairing → Pair.'),
    ('IP и порт подключения — в полях выше.', 'IP and connection port are in the fields above.'),
    ('Впишите порт ADB с ТВ (Беспроводная отладка → «IP-адрес и порт») и нажмите «Подключить». Если ТВ показывает код сопряжения — заполните строку «Сопряжение» и нажмите «Связать».',
     'Type the ADB port from the TV (Wireless debugging → “IP address & port”) and press “Connect”. If the TV shows a pairing code, fill in Pairing and press “Pair”.'),
    ('Введите порт сопряжения и 6-значный код из «Беспроводной отладки» на ТВ. Порт подключения (строка «IP-адрес и порт») — отдельно, ниже.',
     'Enter the pairing port and 6-digit code from “Wireless debugging” on the TV. The connection port (“IP address & port”) is the separate field below.'),
    ('Связано. Подключаюсь по порту подключения…', 'Paired. Connecting on the connection port…'),
    ('ADB на ТВ не отвечает на этом порту. На ТВ: «Для разработчиков» → включите «Отладка по USB» и «Беспроводная отладка». Возьмите с экрана ТВ порт сопряжения + код (Связать) и отдельно порт из строки «IP-адрес и порт» (поле «Порт подключения», часто не 5555) → Проверить.',
     'ADB on the TV is not answering on this port. On the TV: Developer options → turn on “USB debugging” and “Wireless debugging”. Use the pairing port + code (Pair), then the port from “IP address & port” (Connection port field, often not 5555) → Check.'),
    ('На экране ТВ появился запрос — нажмите «Разрешить» (лучше с галочкой «Всегда»). Потом нажмите «Проверить». Пока не разрешите — Fling сам повторно спрашивать не будет.',
     'The TV is asking for permission — choose “Allow” (ideally with “Always”), then press “Check”. Until you allow it, Fling will not ask again on its own.'),
    ('Подключено (', 'Connected ('),
    ('): панель видит, что играет на ТВ.', '): the panel sees what the TV is playing.'),
    ('Подключаюсь к ТВ по ADB (', 'Connecting to the TV over ADB ('),

    ('Найти и подключить всё', 'Find and connect everything'),
    ('Поиск ТВ в локальной сети', 'Find the TV on the local network'),
    ('Пульт ТВ (по сети)', 'TV remote (network)'),
    ('ADB: что играет, название и обложка', "ADB: what's playing, title and artwork"),
    ('YouTube на ТВ (по коду)', 'YouTube on the TV (code)'),
    ('Bluetooth-пульт (запасной)', 'Bluetooth remote (backup)'),
    ('Ищу Chromecast / Google TV в сети… (до 10 с)', 'Looking for Chromecast / Google TV… (up to 10 s)'),
    ('Ничего не нашлось. Ноутбук и ТВ в одной сети Wi-Fi? ', 'Nothing found. Are the computer and the TV on the same network? '),
    ('Искать снова', 'Search again'),
    ('Искать', 'Search'),
    ('Выбран: ', 'Selected: '),
    (' · найдено ', ' · found '),
    ('ТВ ещё не найден.', 'No TV found yet.'),
    ('Связан: кнопки приложений, стрелки, пауза, ввод текста.', 'Paired: app buttons, d-pad, pause, typing.'),
    ('Связать заново', 'Pair again'),
    ('Связываюсь с ТВ…', 'Contacting the TV…'),
    ('Введите код, который показал ТВ (A–F и цифры; русская раскладка подойдёт).', 'Enter the code shown on the TV (A–F and digits; a Russian layout works too).'),
    ('Код с экрана ТВ, 6 символов', 'Code on the TV, 6 characters'),
    ('Не вышло.', "That didn't work."),
    ('Не вышло: ', "That didn't work: "),
    ('Попробовать снова', 'Try again'),
    ('Не связан.', 'Not paired.'),
    ('Не проверено.', 'Not checked yet.'),
    ('Подключить', 'Connect'),
    ('Проверить', 'Check'),
    ('Подключаюсь к ТВ по ADB…', 'Connecting to the TV over ADB…'),
    ('Подключено: панель видит, что играет на ТВ.', 'Connected: the panel sees what the TV is playing.'),
    ('На экране ТВ появился запрос — нажмите «Разрешить» (лучше с галочкой «Всегда»).', 'The TV is asking for permission — choose “Allow” (ideally with “Always”).'),
    ('ADB на ТВ выключен. На ТВ: Настройки → Система → О системе → 7 раз нажать «Сборка ОС» → Настройки → Система → Для разработчиков → включить «Отладка по USB» и «Беспроводная отладка». Если ТВ показывает код сопряжения — впишите порт и код ниже.', 'ADB is off on the TV. On the TV: Settings → System → About → tap “Android TV OS build” 7 times → Settings → System → Developer options → turn on “USB debugging” and “Wireless debugging”. If the TV shows a pairing code, enter the port and code below.'),
    ('Беспроводная отладка:', 'Wireless debugging:'),
    ('порт', 'port'),
    ('код из 6 цифр', '6-digit code'),
    ('Введите порт и 6-значный код из «Беспроводной отладки» на ТВ.', 'Enter the port and 6-digit code from “Wireless debugging” on the TV.'),
    ('Связано. Подключаюсь…', 'Paired. Connecting…'),
    ('Связан: видно точное видео и время в YouTube.', 'Linked: the exact YouTube video and time are visible.'),
    ('Связываю…', 'Linking…'),
    ('Ввести код', 'Enter code'),
    ('12 цифр с экрана ТВ', '12 digits on the TV'),
    ('Необязательно (ADB уже показывает, что играет). На ТВ: YouTube → Настройки → «Связать с ТВ по коду».', "Optional (ADB already shows what's playing). On the TV: YouTube → Settings → “Link with TV code”."),
    ('Подключён: ', 'Connected: '),
    ('✓ Подключено: ', '✓ Connected: '),
    ('Bluetooth-пульт · ', 'Bluetooth remote · '),
    ('Виден ТВ 3 минуты. На ТВ: Настройки → Пульты и аксессуары → Добавить → «Fling Remote».', 'Visible to the TV for 3 minutes. On the TV: Settings → Remotes & Accessories → Pair → “Fling Remote”.'),
    ('Не нужен, если пульт по сети связан.', 'Not needed when the network remote is paired.'),
    ('Снимок экрана: подтвердите на ТВ «Разрешить отладку».', 'Screenshot: confirm “Allow debugging” on the TV.'),
    ('Снимок экрана работает через ADB — включите его: «Подключение ТВ…» → ADB.', 'Screenshots work over ADB — turn it on: “Connect TV…” → ADB.'),
    ('Снимок экрана: ADB не подключён — откройте «Подключение ТВ…».', "Screenshot: ADB isn't connected — open “Connect TV…”."),
    ('Снимок экрана не получился: ', 'The screenshot failed: '),
    ('Снимок экрана: ', 'Screenshot: '),
    ('нет данных', 'no data'),
    ('Снимок чёрный: видео защищено от снимков (DRM у YouTube/Netflix). Меню и главный экран ТВ снимаются нормально.', 'The screenshot is black: the video is copy-protected (DRM on YouTube/Netflix). Menus and the home screen capture fine.'),
    ('ТВ', 'TV'),
]
EN_PHRASES += [   # keyboard captured strip
    ("⌨  Клавиатура захвачена — Esc, чтобы выйти", "⌨  Keyboard captured — Esc to exit"),
]
EN_PHRASES += [   # installing an APK on the TV
    ("Установить APK на ТВ", "Install APK on the TV"),
    ("Установить APK…", "Install APK…"),
    ("Приложения Android (*.apk);;Все файлы (*)", "Android apps (*.apk);;All files (*)"),
    ("Установка APK: ADB не установлен — откройте «Подключение ТВ…» и скачайте platform-tools.",
     "Installing an APK: ADB is not installed — open “Connect TV…” and download platform-tools."),
    ("Установка APK: подтвердите на ТВ «Разрешить отладку».", "Installing an APK: confirm “Allow debugging” on the TV."),
    ("Установка APK работает через ADB — включите его: «Подключение ТВ…» → ADB.",
     "Installing an APK works over ADB — turn it on: “Connect TV…” → ADB."),
    ("Установка APK: ADB не подключён — откройте «Подключение ТВ…».", "Installing an APK: ADB isn't connected — open “Connect TV…”."),
    ("Установка APK: ТВ не ответил за 10 минут.", "Installing an APK: the TV didn't answer for 10 minutes."),
    ("Установка APK не удалась: ", "The APK didn't install: "),
    ("Установка APK: ", "Installing an APK: "),
    ("Устанавливаю на ТВ: ", "Installing on the TV: "),
    ("Установлено на ТВ: ", "Installed on the TV: "),
    ("на ТВ уже есть это приложение с другой подписью — удалите его на ТВ и попробуйте снова.",
     "the TV already has this app with a different signature — uninstall it on the TV and try again."),
    ("на ТВ уже стоит более новая версия.", "the TV already has a newer version."),
    ("этот APK собран для другого процессора — нужна версия для armeabi-v7a / arm64.",
     "this APK is built for another processor — get the armeabi-v7a / arm64 version."),
    ("этот APK требует более новый Android, чем на ТВ.", "this APK needs a newer Android than the TV has."),
    ("на ТВ не хватает места.", "the TV is out of storage."),
    ("APK не подписан.", "the APK isn't signed."),
    ("файл повреждён или это не APK.", "the file is damaged or isn't an APK."),
    ("приложение состоит из нескольких APK — выберите их все сразу.",
     "the app comes in several APKs — select them all at once."),
    ("ТВ запретил установку — подтвердите её на экране ТВ.", "the TV blocked the install — confirm it on the TV screen."),
    ("нет ответа", "no answer"),
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
    P.notify(tr(msg))

login_enabled = P.login_enabled
toggle_login = P.toggle_login

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
    f = QFont(P.MONO_FONT if mono else QApplication.font().family())
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
    elif name == "keyboard":
        # Compact keyboard glyph — opens the “type on TV” field
        p.drawRoundedRect(QRectF(s*.12, s*.28, s*.76, s*.48), s*.08, s*.08)
        for i in range(3):
            for j in range(5):
                x = s * (.22 + j * .12)
                y = s * (.38 + i * .12)
                p.drawLine(QPointF(x, y), QPointF(x + s * .06, y))
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
    elif name == "help":                                     # question mark in a circle
        p.drawEllipse(QRectF(s*.12, s*.12, s*.76, s*.76))
        pa = QPainterPath(); pa.moveTo(s*.38, s*.4)
        pa.arcTo(QRectF(s*.38, s*.26, s*.24, s*.22), 180, -270)  # the hook of the "?"
        pa.lineTo(c, s*.6)
        p.drawPath(pa)
        p.drawPoint(QPoint(int(c), int(s*.72)))              # the dot
    elif name == "camera":
        p.drawRoundedRect(QRectF(s*.12, s*.3, s*.76, s*.52), s*.08, s*.08)
        path((s*.36, s*.3), (s*.42, s*.18), (s*.58, s*.18), (s*.64, s*.3))
        p.drawEllipse(QRectF(c - s*.14, s*.42, s*.28, s*.28))
    elif name == "bell":                                     # notifications
        pa = QPainterPath(); pa.moveTo(s*.22, s*.7)
        pa.cubicTo(s*.3, s*.6, s*.28, s*.5, s*.3, s*.4)
        pa.cubicTo(s*.32, s*.18, s*.68, s*.18, s*.7, s*.4)
        pa.cubicTo(s*.72, s*.5, s*.7, s*.6, s*.78, s*.7); pa.closeSubpath()
        p.drawPath(pa)
        p.drawArc(QRectF(s*.42, s*.66, s*.16, s*.16), 180*16, 180*16)
    elif name == "login":                                    # arrow into a door: start at login
        path((s*.5, s*.16), (s*.82, s*.16), (s*.82, s*.84), (s*.5, s*.84))
        path((s*.14, c), (s*.6, c))
        path((s*.44, s*.34), (s*.6, c), (s*.44, s*.66))
    elif name == "quit":                                     # arrow out of a door
        path((s*.5, s*.16), (s*.18, s*.16), (s*.18, s*.84), (s*.5, s*.84))
        path((s*.4, c), (s*.86, c))
        path((s*.7, s*.34), (s*.86, c), (s*.7, s*.66))
    elif name in ("play", "pause", "stop", "rew", "fwd", "prev", "next"):
        p.setBrush(color or T.fg); p.setPen(Qt.PenStyle.NoPen)
        if name == "play":
            path((s*.32, s*.2), (s*.32, s*.8), (s*.8, c), close=True)
        elif name == "pause":
            p.drawRoundedRect(QRectF(s*.26, s*.2, s*.16, s*.6), s*.04, s*.04)
            p.drawRoundedRect(QRectF(s*.58, s*.2, s*.16, s*.6), s*.04, s*.04)
        elif name == "stop":
            p.drawRoundedRect(QRectF(s*.25, s*.25, s*.5, s*.5), s*.06, s*.06)
        elif name == "rew":
            path((s*.5, s*.24), (s*.5, s*.76), (s*.14, c), close=True)
            path((s*.86, s*.24), (s*.86, s*.76), (s*.5, c), close=True)
        elif name == "fwd":
            path((s*.5, s*.24), (s*.5, s*.76), (s*.86, c), close=True)
            path((s*.14, s*.24), (s*.14, s*.76), (s*.5, c), close=True)
        elif name == "prev":
            p.drawRect(QRectF(s*.2, s*.24, s*.09, s*.52))
            path((s*.8, s*.24), (s*.8, s*.76), (s*.3, c), close=True)
        elif name == "next":
            p.drawRect(QRectF(s*.71, s*.24, s*.09, s*.52))
            path((s*.2, s*.24), (s*.2, s*.76), (s*.7, c), close=True)
    elif name == "compact":                                  # arrows pointing inwards
        path((s*.18, s*.4), (s*.4, s*.4), (s*.4, s*.18))
        path((s*.82, s*.6), (s*.6, s*.6), (s*.6, s*.82))
        path((s*.12, s*.12), (s*.4, s*.4)); path((s*.88, s*.88), (s*.6, s*.6))
    elif name == "expand":                                   # arrows pointing outwards
        path((s*.14, s*.36), (s*.14, s*.14), (s*.36, s*.14))
        path((s*.86, s*.64), (s*.86, s*.86), (s*.64, s*.86))
        path((s*.14, s*.14), (s*.42, s*.42)); path((s*.86, s*.86), (s*.58, s*.58))
    elif name == "android":                                  # tiny Android robot (ADB status)
        # head
        p.setBrush(color or T.fg); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(s*.28, s*.30, s*.44, s*.36), s*.1, s*.1)
        # antennae
        pen = QPen(color or T.fg, max(1.2, weight * size / 18))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPoint(int(s*.38), int(s*.30)), QPoint(int(s*.28), int(s*.16)))
        p.drawLine(QPoint(int(s*.62), int(s*.30)), QPoint(int(s*.72), int(s*.16)))
        # eyes
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(T.bg if T else QColor(255, 255, 255)))
        p.drawEllipse(QRectF(s*.36, s*.40, s*.08, s*.08))
        p.drawEllipse(QRectF(s*.56, s*.40, s*.08, s*.08))
        # body + arms + legs
        p.setBrush(color or T.fg)
        p.drawRoundedRect(QRectF(s*.30, s*.66, s*.40, s*.22), s*.06, s*.06)
        p.drawRoundedRect(QRectF(s*.14, s*.68, s*.12, s*.14), s*.04, s*.04)
        p.drawRoundedRect(QRectF(s*.74, s*.68, s*.12, s*.14), s*.04, s*.04)
        p.drawRoundedRect(QRectF(s*.34, s*.86, s*.12, s*.10), s*.04, s*.04)
        p.drawRoundedRect(QRectF(s*.54, s*.86, s*.12, s*.10), s*.04, s*.04)
    elif name == "tv_plus":                                  # a TV with a plus: connect to a TV
        p.drawRoundedRect(QRectF(s*.08, s*.18, s*.66, s*.48), s*.07, s*.07)
        p.drawLine(QPoint(int(s*.26), int(s*.82)), QPoint(int(s*.56), int(s*.82)))
        p.drawLine(QPoint(int(s*.82), int(s*.56)), QPoint(int(s*.82), int(s*.92)))
        p.drawLine(QPoint(int(s*.64), int(s*.74)), QPoint(int(s)-1, int(s*.74)))
    elif name == "tv":
        p.drawRoundedRect(QRectF(s*.1, s*.2, s*.8, s*.54), s*.08, s*.08)
        p.drawLine(QPoint(int(s*.32), int(s*.86)), QPoint(int(s*.68), int(s*.86)))
    p.end()
    return QIcon(pm)


def flag_icon(code, w=22, h=15):
    """Small rounded flag: "en" = United Kingdom, "ru" = Russia."""
    dpr = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1
    k = dpr * 2
    pm = QPixmap(int(w * k), int(h * k)); pm.setDevicePixelRatio(k)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath(); clip.addRoundedRect(QRectF(0, 0, w, h), 2.5, 2.5)
    p.setClipPath(clip)
    if code == "ru":
        for i, col in enumerate(("#ffffff", "#0039a6", "#d52b1e")):
            p.fillRect(QRectF(0, i * h / 3, w, h / 3 + 0.5), QColor(col))
    else:
        p.fillRect(QRectF(0, 0, w, h), QColor("#012169"))
        for col, width in (("#ffffff", h * .2), ("#c8102e", h * .08)):
            p.setPen(QPen(QColor(col), width))
            p.drawLine(QPoint(0, 0), QPoint(w, h)); p.drawLine(QPoint(0, h), QPoint(w, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.fillRect(QRectF(w / 2 - h * .17, 0, h * .34, h), QColor("#ffffff"))
        p.fillRect(QRectF(0, h / 2 - h * .17, w, h * .34), QColor("#ffffff"))
        p.fillRect(QRectF(w / 2 - h * .1, 0, h * .2, h), QColor("#c8102e"))
        p.fillRect(QRectF(0, h / 2 - h * .1, w, h * .2), QColor("#c8102e"))
    p.setClipping(False)
    p.setPen(QPen(QColor(0, 0, 0, 60), 0.8)); p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(0.4, 0.4, w - 0.8, h - 0.8), 2.5, 2.5)
    p.end()
    return QIcon(pm)


class CornerBar(QWidget):
    """Top-right corner of the panel: the guide (?) and the compact-mode toggle."""
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        lay = QHBoxLayout(self); lay.setContentsMargins(14, 7, 10, 0); lay.setSpacing(4)
        lay.addStretch(1)
        self.compact = QToolButton()
        self.compact.setIconSize(QSize(14, 14))
        self.compact.setCursor(Qt.CursorShape.PointingHandCursor)
        self.compact.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.compact.setStyleSheet(f"QToolButton{{border:none;border-radius:4px;padding:2px}}"
                                   f"QToolButton:hover{{background:{Theme.css(T.acc_a(0.2))}}}")
        self.compact.clicked.connect(panel.toggle_compact)
        self.help = QToolButton()                       # "How to use" guide
        self.help.setIcon(glyph("help", 15, T.fg_a(0.6))); self.help.setIconSize(QSize(15, 15))
        self.help.setToolTip("Как пользоваться")
        self.help.setCursor(Qt.CursorShape.PointingHandCursor)
        self.help.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.help.setStyleSheet(self.compact.styleSheet())
        self.help.clicked.connect(lambda: (panel.hide(), open_help(panel)))
        lay.addWidget(self.help)
        lay.addWidget(self.compact)

    def sync(self, compact):
        self.compact.setIcon(glyph("expand" if compact else "compact", 14, T.fg_a(0.6)))
        self.compact.setToolTip("Полный вид" if compact else "Компактный вид")


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

    def set_shortcut(self, s):
        self.k.setText(s)

    def set_icon_color(self, color):
        if self._icon_name:
            self.ic.setPixmap(glyph(self._icon_name, 14, color).pixmap(15, 15))

    def setEnabled(self, en):
        super().setEnabled(en)
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
    """Round d-pad / power key.

    `repeat=True` holds like a real remote (auto-fire while pressed) — used for
    the arrow keys so laptop hold-to-scroll through TV menus works.
    """
    def __init__(self, glyph_name=None, text=None, size=30, accented=False, on=None, repeat=False):
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
            if repeat:
                # Fire on press and keep sending while held (no clicked — that would double-fire)
                self.setAutoRepeat(True)
                self.setAutoRepeatDelay(350)
                self.setAutoRepeatInterval(110)
                self.pressed.connect(on)
            else:
                self.clicked.connect(on)


class DPadRing(QWidget):
    """Round pad like a physical remote: ↑ ↓ ← → sit on one ring around OK, all at the
    same distance from it."""
    def __init__(self, up, down, left, right, ok, diameter=140):
        super().__init__()
        self.setFixedSize(diameter, diameter)
        c = diameter / 2
        r = c - 5 - up.width() / 2          # arrow centres, just inside the rim
        for b, dx, dy in ((up, 0, -1), (down, 0, 1), (left, -1, 0), (right, 1, 0), (ok, 0, 0)):
            b.setParent(self)
            b.move(round(c + dx * r - b.width() / 2), round(c + dy * r - b.height() / 2))
        self._arrows = {"up": up, "down": down, "left": left, "right": right}
        self._ok_r = ok.width() / 2
        self._held = None
        self._rep = QTimer(self)
        self._rep.setInterval(110)          # same hold-to-repeat as the arrow dots
        self._rep.timeout.connect(lambda: self._held and self._held.pressed.emit())
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _sector(self, pos):
        """Like a real d-pad, the whole quarter of the ring is the key, not just the dot."""
        c = self.width() / 2
        dx, dy = pos.x() - c, pos.y() - c
        d2 = dx * dx + dy * dy
        if d2 > c * c or d2 < self._ok_r ** 2:
            return None
        if abs(dx) > abs(dy):
            return self._arrows["right" if dx > 0 else "left"]
        return self._arrows["down" if dy > 0 else "up"]

    def mousePressEvent(self, e):
        b = self._sector(e.position())
        if b is None:
            return super().mousePressEvent(e)
        self._held = b
        b.setDown(True)
        b.pressed.emit()                     # arrows act on press, like the dots themselves
        QTimer.singleShot(350, lambda: self._held is b and self._rep.start())

    def mouseReleaseEvent(self, e):
        if self._held is not None:
            self._held.setDown(False)
            self._held = None
            self._rep.stop()
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(T.fg_a(0.08), 1))
        p.setBrush(T.fg_a(0.045))
        p.drawEllipse(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1))


def build_dpad(key):
    """D-pad block: the ring, with Back below-left and Home below-right like on a TV remote.
    `key(name)` returns the callback for a TV key. Returns (widget, {name: button})."""
    up = KeyButton("up", size=34, on=key("up"), repeat=True); up.setToolTip("Вверх  (↑ / W)")
    down = KeyButton("down", size=34, on=key("down"), repeat=True); down.setToolTip("Вниз  (↓ / S)")
    left = KeyButton("left", size=34, on=key("left"), repeat=True); left.setToolTip("Влево  (← / A)")
    right = KeyButton("right", size=34, on=key("right"), repeat=True); right.setToolTip("Вправо  (→ / D)")
    ok = KeyButton(text="OK", size=44, accented=True, on=key("ok")); ok.setToolTip("OK  (Enter / Space)")
    back = KeyButton("back", size=32, on=key("back")); back.setToolTip("Назад  (Backspace / Delete)")
    home = KeyButton("home", size=32, on=key("home")); home.setToolTip("Домой  (Home / H)")
    w = QWidget()
    row = QHBoxLayout(w); row.setContentsMargins(12, 8, 12, 4); row.setSpacing(6)
    bottom = Qt.AlignmentFlag.AlignBottom
    row.addStretch(1)
    row.addWidget(back, 0, bottom)
    row.addWidget(DPadRing(up, down, left, right, ok))
    row.addWidget(home, 0, bottom)
    row.addStretch(1)
    return w, {"up": up, "down": down, "left": left, "right": right,
               "ok": ok, "back": back, "home": home}


def key_bar(*keys):
    """Compact row of round icon buttons: keys = (glyph, tooltip, callback, size, accented)."""
    w = QWidget()
    lay = QHBoxLayout(w); lay.setContentsMargins(12, 8, 12, 6); lay.setSpacing(10)
    lay.addStretch(1)
    out = []
    for g, tip, fn, size, acc in keys:
        b = KeyButton(g, size=size, accented=acc, on=fn)
        b.setToolTip(tip)
        lay.addWidget(b)
        out.append(b)
    lay.addStretch(1)
    w.keys = out
    return w


class Pill(QPushButton):
    def __init__(self, glyph_name, text, on, repeat=False):
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
        if repeat:
            self.setAutoRepeat(True)
            self.setAutoRepeatDelay(350)
            self.setAutoRepeatInterval(90)
            self.pressed.connect(on)
        else:
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

def fill_device_menu(menu: QMenu, s: "State", parent=None):
    """Device chooser used by the panel footer, the tray menu and the Connect TV window."""
    menu.clear()
    for d in s.devices:
        a = menu.addAction(s.device_label(d))
        a.setCheckable(True)
        a.setChecked(bool(s.device) and d["ip"] == s.device["ip"])
        a.setToolTip(d["ip"])
        a.triggered.connect(lambda _=False, d=d: s.select_device(d))
    if not s.devices:
        a = menu.addAction("ТВ не найдены"); a.setEnabled(False)
    menu.addSeparator()
    menu.addAction("Ищу…" if s.scanning else "Найти ТВ заново", s.scan).setEnabled(not s.scanning)
    def ask_ip():
        from PyQt6.QtWidgets import QInputDialog
        ip, ok = QInputDialog.getText(parent, tr("Добавить ТВ по IP"),
                                      tr("IP-адрес телевизора (Настройки → Сеть на ТВ):"), text="192.168.1.")
        if ok and ip:
            s.add_device_ip(ip)
    menu.addAction("Добавить ТВ по IP…", ask_ip)
    manual = [d for d in s.devices if d.get("manual")]
    if manual:
        fm = menu.addMenu("Убрать добавленный вручную")
        for d in manual:
            fm.addAction(d["name"], lambda d=d: s.forget_device(d))


def pick_apk(state: "State"):
    """Choose APK file(s) and install them on the TV. Several files = one split app."""
    from PyQt6.QtWidgets import QFileDialog
    start = os.path.join(os.path.expanduser("~"), "Downloads")
    paths, _ = QFileDialog.getOpenFileNames(
        None, tr("Установить APK на ТВ"), start if os.path.isdir(start) else os.path.expanduser("~"),
        tr("Приложения Android (*.apk);;Все файлы (*)"))
    if paths:
        state.tv_install_apk(paths)


class Panel(QWidget):
    def __init__(self, state: State):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.NoDropShadowWindowHint)
        self.translucent = P.translucent_ok(state.conf)
        if self.translucent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Fling")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.state = state
        self.hidden_at = 0.0
        # Catch keys even when a child button has focus (arrows still drive the TV)
        QApplication.instance().installEventFilter(self)
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

        self.corner = CornerBar(self)
        v.addWidget(self.corner)
        # shown while the laptop keyboard drives the TV (Windows keyboard hook is on)
        self.kb_strip = label("", 10.5, T.accent, QFont.Weight.DemiBold, wrap=True)
        self.kb_strip.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.kb_strip.setStyleSheet(f"color:{Theme.css(T.accent)};background:{Theme.css(T.acc_a(0.12))};"
                                    "border-radius:6px;padding:5px 8px")
        self.kb_box = padded(self.kb_strip, 10, 2, 10, 4)
        self.kb_box.hide()
        v.addWidget(self.kb_box)

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
        # compact view: one row of icon keys, the words live in the tooltips
        self.c_bar = key_bar(
            ("prev", "Предыдущее видео", state.yt_prev, 28, False),
            ("rew", f"Назад {SEEK} с  (←)", lambda: state.seek(-SEEK), 32, False),
            ("pause", "Пауза / Играть  (Пробел)", state.toggle_play, 40, True),
            ("fwd", f"Вперёд {SEEK} с  (→)", lambda: state.seek(SEEK), 32, False),
            ("next", "Следующее видео", state.yt_next, 28, False),
            ("stop", "Остановить трансляцию  (Ctrl+.)", state.stop, 28, False),
            ("play_on_tv", "Отправить эту вкладку вместо", state.cast_tab, 28, False))
        self.c_bar_prev, _, self.c_bar_play, _, self.c_bar_next, _, _ = self.c_bar.keys
        cl.addWidget(self.c_bar)
        self.c_rows = QWidget()
        cr = QVBoxLayout(self.c_rows); cr.setContentsMargins(0, 0, 0, 0); cr.setSpacing(0)
        self.c_play = MenuRow("Пауза", shortcut="Пробел", on=state.toggle_play)
        cr.addWidget(self.c_play)
        cr.addWidget(MenuRow(f"Назад {SEEK} с", shortcut="←", on=lambda: state.seek(-SEEK)))
        cr.addWidget(MenuRow(f"Вперёд {SEEK} с", shortcut="→", on=lambda: state.seek(SEEK)))
        self.c_prev = MenuRow("Предыдущее видео", on=state.yt_prev)
        self.c_next = MenuRow("Следующее видео", on=state.yt_next)
        cr.addWidget(self.c_prev); cr.addWidget(self.c_next)
        cl.addWidget(self.c_rows)
        self.c_vol = VolumeRow(state)
        cl.addWidget(self.c_vol)
        self.c_tail = QWidget()
        ct = QVBoxLayout(self.c_tail); ct.setContentsMargins(0, 0, 0, 0); ct.setSpacing(0)
        ct.addWidget(Separator())
        self.c_cast = MenuRow("Отправить эту вкладку", shortcut=SHORTCUT_LABEL, on=state.cast_tab)
        ct.addWidget(self.c_cast)
        ct.addWidget(MenuRow("Остановить трансляцию", shortcut="Ctrl+.", on=state.stop))
        cl.addWidget(self.c_tail)
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
        self.i_bar = key_bar(
            ("play_on_tv", f"Отправить эту вкладку  ({SHORTCUT_LABEL})", state.cast_tab, 40, True),
            ("clipboard", "Отправить скопированную ссылку", state.cast_clipboard, 32, False))
        self.i_bar_cast = self.i_bar.keys[0]
        il.addWidget(self.i_bar)
        self.i_rows = QWidget()
        ir = QVBoxLayout(self.i_rows); ir.setContentsMargins(0, 0, 0, 0); ir.setSpacing(0)
        ir.addWidget(Separator())
        self.i_cast = MenuRow("Отправить эту вкладку", icon="play_on_tv", shortcut=SHORTCUT_LABEL,
                              accented=True, on=state.cast_tab)
        ir.addWidget(self.i_cast)
        il.addWidget(self.i_rows)
        self.i_why = label("", 11, T.orange, wrap=True)
        self.i_why_box = padded(self.i_why, 14, 0, 14, 7)
        il.addWidget(self.i_why_box)
        self.i_clip = MenuRow("Отправить скопированную ссылку", icon="clipboard", on=state.cast_clipboard)
        il.addWidget(self.i_clip)
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
        sl.addWidget(padded(label("Свяжите один раз с ТВ, чтобы открыть приложения, стрелки и ввод текста.",
                                  11, T.fg_a(0.55), wrap=True), 14, 10, 14, 6))
        sl.addWidget(MenuRow("Включить ТВ", icon="power", on=state.wake))
        pair = MenuRow("Связать с ТВ", icon="tv_plus", accented=True, on=state.start_pairing)
        pair.set_icon_color(T.green)
        sl.addWidget(pair)
        if BT_SUPPORTED:
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
        k = lambda n: (lambda: state.tv_key(KEY[n]))
        # Round pad like a physical remote; Back · Home below its sides
        # (self._dpad: lit up for a moment when the same key comes from the laptop keyboard)
        pad, self._dpad = build_dpad(k)
        rl.addWidget(pad)
        hint = label("Клавиатура: стрелки/WASD · Enter=OK · Backspace=Назад · Home · "
                     "+/− громкость · Пробел пауза · колёсико", 9.5, T.fg_a(0.4), wrap=True)
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        rl.addWidget(padded(hint, 10, 0, 10, 6))
        vk = QHBoxLayout(); vk.setContentsMargins(12, 8, 12, 4); vk.setSpacing(5)
        vd = Pill("spk_minus", " Громк −", k("vol_down"), repeat=True)
        vd.setToolTip("Громкость −  (− / [ / VolumeDown)")
        vm = Pill("spk_slash", " Без звука", k("mute"))
        vm.setToolTip("Без звука  (M / VolumeMute)")
        vu = Pill("spk_plus", " Громк +", k("vol_up"), repeat=True)
        vu.setToolTip("Громкость +  (+ / ] / VolumeUp)")
        vk.addWidget(vd); vk.addWidget(vm); vk.addWidget(vu)
        rl.addLayout(vk)
        # Keyboard icon → reveal type field (sendText over Android TV Remote, as in Damion's Fling)
        ty = QVBoxLayout(); ty.setContentsMargins(14, 4, 14, 9); ty.setSpacing(3)
        tr = QHBoxLayout(); tr.setSpacing(6)
        self.kb_btn = QPushButton()
        self.kb_btn.setFixedSize(24, 22)
        self.kb_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.kb_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.kb_btn.setIcon(glyph("keyboard", 14, T.fg))
        self.kb_btn.setIconSize(QSize(14, 14))
        self.kb_btn.setToolTip("Клавиатура — печать на ТВ")
        self.kb_btn.setStyleSheet(
            f"QPushButton{{border:none;border-radius:5px;background:{Theme.css(T.fg_a(0.07))}}}"
            f"QPushButton:hover{{background:{Theme.css(T.acc_a(0.2))}}}")
        self.kb_btn.clicked.connect(self._toggle_type)
        self.type_edit = field("Печать на ТВ")
        self.type_edit.setToolTip("Текст попадёт в активное поле ввода на телевизоре")
        self.type_edit.returnPressed.connect(self._type_submit)
        self.type_edit.hide()
        self.mic = QPushButton()
        self.mic.setFixedSize(24, 22)
        self.mic.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic.clicked.connect(state.tv_voice_toggle)
        tr.addWidget(self.kb_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        tr.addWidget(self.type_edit, 1)
        tr.addWidget(self.mic, 0, Qt.AlignmentFlag.AlignVCenter)
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

        self.house = QWidget()
        hv = QVBoxLayout(self.house); hv.setContentsMargins(0, 0, 0, 0); hv.setSpacing(0)
        v.addWidget(self.house)
        v_main, v = v, hv
        v.addWidget(Separator())
        self.shot_row = MenuRow("Сделать снимок", icon="camera", on=state.tv_screenshot)
        v.addWidget(self.shot_row)
        self.setup_row = MenuRow("Подключение ТВ…", icon="tv", on=lambda: (self.hide(), self.open_setup()))
        v.addWidget(self.setup_row)
        # Notify / start-at-login live only in the tray menu — keeps the panel short
        v.addWidget(MenuRow("Выйти из Fling", icon="quit", shortcut="Ctrl+Q", on=QApplication.quit))
        v = v_main
        v.addWidget(Separator())

        # footer: status dot · TV name · ADB android · switcher
        ft = QHBoxLayout(); ft.setContentsMargins(14, 5, 14, 9); ft.setSpacing(7)
        self.dot = QLabel(); self.dot.setFixedSize(6, 6)
        self.dev_name = label("", 11.5)
        self.adb_badge = AdbBadge(18, compact=True)
        self.adb_badge.clicked.connect(self._adb_badge_click)
        self.switch = QToolButton()
        self.switch.setText("Сменить")
        self.switch.setFont(font(11))
        self.switch.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.switch.setStyleSheet(f"QToolButton{{border:none;color:{Theme.css(T.accent)};background:transparent}}"
                                  "QToolButton::menu-indicator{image:none}")
        self.switch_menu = QMenu(self.switch)
        self.switch_menu.aboutToShow.connect(lambda: fill_device_menu(self.switch_menu, self.state, None))
        self.switch.setMenu(self.switch_menu)
        ft.addWidget(self.dot)
        ft.addWidget(self.dev_name, 1)
        ft.addWidget(self.adb_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        ft.addWidget(self.switch)
        fw = QWidget(); fw.setLayout(ft)
        v.addWidget(fw)
        # authorship: Fling is Damion Rashford's and Tatiana's app; this is an unofficial port
        credit = label(f'<a href="https://github.com/tatianathevisionary/fling" style="color:{Theme.css(T.fg_a(0.45))};text-decoration:none">'
                       'Fling создан Damion Rashford и Tatiana · неофициальный порт</a>', 10, T.fg_a(0.4))
        credit.setOpenExternalLinks(True)
        credit.setToolTip("github.com/tatianathevisionary/fling")
        # bottom line: credit on the left, a small dimmed language flag on the right
        cr = QHBoxLayout(); cr.setContentsMargins(14, 0, 10, 6); cr.setSpacing(6)
        cr.addWidget(credit, 1)
        self.lang = QToolButton()
        self.lang.setIcon(flag_icon(LANG, 14, 10)); self.lang.setIconSize(QSize(14, 10))
        self.lang.setToolTip({"ru": "Русский → English", "en": "English → Русский"}[LANG])
        self.lang.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lang.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lang.setStyleSheet(f"QToolButton{{border:none;border-radius:3px;padding:2px}}"
                                f"QToolButton:hover{{background:{Theme.css(T.fg_a(0.08))}}}")
        dim = QGraphicsOpacityEffect(self.lang); dim.setOpacity(0.45)   # quiet until the mouse is over it
        self.lang.setGraphicsEffect(dim)
        self.lang.enterEvent = lambda e: dim.setOpacity(1.0)
        self.lang.leaveEvent = lambda e: dim.setOpacity(0.45)
        self.lang.clicked.connect(self._toggle_lang)
        cr.addWidget(self.lang, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(cr)

        # App shortcuts that aren't TV-remote keys (remote keys go through eventFilter)
        for seq, fn in (("Shift+Left", lambda: self._casting_only(lambda: state.seek(-SEEK))),
                        ("Shift+Right", lambda: self._casting_only(lambda: state.seek(SEEK))),
                        ("Ctrl+Left", lambda: self._casting_only(lambda: state.seek(-SEEK))),
                        ("Ctrl+Right", lambda: self._casting_only(lambda: state.seek(SEEK))),
                        ("Ctrl+.", state.stop), ("Ctrl+Q", QApplication.quit),
                        ("Ctrl+Shift+C", state.cast_tab),
                        ("Escape", self._escape)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(fn)

        # Windows: the tray popup never becomes the active window, so Qt gets no keys.
        # A keyboard hook, installed only while the panel is open, hands us the arrow
        # cluster, Enter, Backspace, Space, Home, Esc and +/− instead.
        self._keyhook = P.RemoteKeyHook(lambda vk, shift, rep: state.bridge.call.emit(
            lambda: self._hook_key(vk, shift, rep))) if P.IS_WIN else None
        self._was_editing = False
        state.changed.connect(self.sync)
        state.changed.connect(self._update_keyhook)
        self.sync()

    # --- laptop keyboard → TV (Windows hook; see RemoteKeyHook)
    _VK_QT = {0x25: Qt.Key.Key_Left, 0x26: Qt.Key.Key_Up, 0x27: Qt.Key.Key_Right, 0x28: Qt.Key.Key_Down,
              0x0D: Qt.Key.Key_Return, 0x08: Qt.Key.Key_Backspace, 0x20: Qt.Key.Key_Space,
              0x24: Qt.Key.Key_Home, 0xBB: Qt.Key.Key_Equal, 0x6B: Qt.Key.Key_Plus,
              0xBD: Qt.Key.Key_Minus, 0x6D: Qt.Key.Key_Minus}

    def _visible_field(self):
        return any(e.isVisible() for e in self.findChildren(QLineEdit))

    def _update_keyhook(self):
        h = getattr(self, "_keyhook", None)
        if h is None:
            return
        editing = self.isVisible() and self._visible_field()
        if editing and not self._was_editing:
            # a text field just appeared (type on TV, pairing code): give it the keyboard
            QTimer.singleShot(0, lambda: P.force_foreground(int(self.winId())))
        self._was_editing = editing
        s = self.state
        if s.remote_ready:
            h.keys = set(h.KEYS)
        elif s.panel == "casting":           # no remote yet: ← / → seek, Space pauses
            h.keys = {0x25, 0x27, 0x20, 0x1B}
        else:
            h.keys = {0x1B}
        h.active = self.isVisible() and not editing
        # say so while the keys go to the TV instead of the app the user was in
        self._show_kb_strip(h.active and (s.remote_ready or s.panel == "casting"),
                            s.remote_ready)

    def _show_kb_strip(self, on: bool, remote: bool):
        strip, box = getattr(self, "kb_strip", None), getattr(self, "kb_box", None)
        if strip is None or box is None:
            return
        text = "⌨  Клавиатура захвачена — Esc, чтобы выйти"
        if strip.text() != tr(text):
            strip.setText(text)
        if box.isVisibleTo(self) != on:
            box.setVisible(on)
            QTimer.singleShot(0, self.place)  # the panel grows / shrinks by one line

    def _hook_key(self, vk, shift, repeat):
        if not self.isVisible():
            return
        if vk == 0x1B:
            self._escape()
            return
        if shift and vk in (0x25, 0x27) and self.state.panel == "casting":
            if not repeat:
                self.state.seek(SEEK if vk == 0x27 else -SEEK)
            return
        key = self._VK_QT.get(vk)
        if key is None:
            return
        text = " " if vk == 0x20 else ""
        ev = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, text, repeat)
        if self._handle_key(ev):
            name = _REMOTE_BY_QT.get(key)
            if name == "play_pause" and self.state.panel != "casting":
                name = "ok"
            self._flash(name)

    def _flash(self, name):
        b = getattr(self, "_dpad", {}).get(name)
        if b is None or not b.isVisible():
            return
        b.setDown(True)
        QTimer.singleShot(120, lambda: b.setDown(False))

    # --- input helpers
    def _editing(self):
        w = QApplication.focusWidget()
        return isinstance(w, QLineEdit)

    def _casting_only(self, fn):
        if self._editing():
            return
        if self.state.panel == "casting":
            fn()

    def _remote_key(self, name: str) -> bool:
        """Send a TV remote key if the Android TV Remote / BT session is ready."""
        if not self.state.remote_ready:
            return False
        code = KEY.get(name)
        if code is None:
            return False
        self.state.tv_key(code)
        return True

    def _grab_keys(self):
        if self.isVisible() and not self._editing():
            try:
                self.grabKeyboard()
            except Exception:
                pass

    def _ungrab_keys(self):
        try:
            self.releaseKeyboard()
        except Exception:
            pass

    def eventFilter(self, obj, e):
        """Universal keyboard remote while the panel is open (except text fields)."""
        et = e.type()
        # Let QLineEdit own the keyboard while typing on the TV / PIN / YouTube code
        if et == QEvent.Type.FocusIn and isinstance(obj, QLineEdit) and self.isAncestorOf(obj):
            self._ungrab_keys()
        elif et == QEvent.Type.FocusOut and isinstance(obj, QLineEdit) and self.isAncestorOf(obj):
            QTimer.singleShot(0, self._grab_keys)
        if et == QEvent.Type.KeyPress and self.isVisible():
            # Ignore keys meant for another Fling window (Connect TV / help)
            fw = QApplication.focusWidget()
            if fw is not None and not (fw is self or self.isAncestorOf(fw)):
                return super().eventFilter(obj, e)
            if self._handle_key(e):
                return True
        return super().eventFilter(obj, e)

    def keyPressEvent(self, e):
        if self._handle_key(e):
            e.accept()
            return
        super().keyPressEvent(e)

    def _handle_key(self, e) -> bool:
        """Map a key press to TV remote / cast actions. Max layout compatibility."""
        if self._editing():
            return False
        k = e.key()
        mods = e.modifiers()
        # Don't steal chorded app shortcuts (Ctrl/Meta except media)
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            return False
        if mods & Qt.KeyboardModifier.AltModifier:
            # Alt+← / Alt+→ → Back / (optional) — common browser habit → TV Back
            if k == Qt.Key.Key_Left:
                return self._remote_key("back")
            if k == Qt.Key.Key_Home:
                return self._remote_key("home")
            return False

        name = _REMOTE_BY_QT.get(k)
        if name is None:
            # RU/EN letter fallback via the character produced (layout-independent intent)
            ch = (e.text() or "").lower()
            if len(ch) == 1:
                name = _REMOTE_BY_TEXT.get(ch)
        if name in _REMOTE_REPEAT and e.isAutoRepeat():
            pass  # allow hold-to-repeat for d-pad / volume
        elif e.isAutoRepeat() and name not in _REMOTE_REPEAT:
            return False

        if name and self.state.remote_ready:
            # Space while casting: play/pause is more useful than OK
            if name == "play_pause" and self.state.panel == "casting":
                self.state.toggle_play()
                return True
            if name == "play_pause" and self.state.panel != "casting":
                # idle + remote: Space = OK (select)
                return self._remote_key("ok")
            if name in ("ffwd", "rewind") and self.state.panel == "casting":
                self.state.seek(SEEK if name == "ffwd" else -SEEK)
                return True
            return self._remote_key(name)

        # No remote paired yet — keep cast transport on the arrow keys / space
        if self.state.panel == "casting":
            if k in (Qt.Key.Key_Left, Qt.Key.Key_Comma):
                self.state.seek(-SEEK); return True
            if k in (Qt.Key.Key_Right, Qt.Key.Key_Period):
                self.state.seek(SEEK); return True
            if k in (Qt.Key.Key_Space, Qt.Key.Key_MediaTogglePlayPause, Qt.Key.Key_MediaPlay):
                self.state.toggle_play(); return True
        return False
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

    def _toggle_type(self):
        """Keyboard icon: show/hide the type-on-TV field (Android TV Remote sendText)."""
        if self.type_edit.isVisible():
            self._hide_type()
        else:
            self._show_type()

    def _show_type(self):
        self.type_edit.show()
        self.type_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._ungrab_keys()
        self._update_keyhook()
        self.kb_btn.setStyleSheet(
            f"QPushButton{{border:none;border-radius:5px;background:{Theme.css(T.acc_a(0.22))}}}")
        QTimer.singleShot(0, self.place)

    def _hide_type(self):
        self.type_edit.clear()
        self.type_edit.clearFocus()
        self.type_edit.hide()
        self.kb_btn.setStyleSheet(
            f"QPushButton{{border:none;border-radius:5px;background:{Theme.css(T.fg_a(0.07))}}}"
            f"QPushButton:hover{{background:{Theme.css(T.acc_a(0.2))}}}")
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._grab_keys()
        self._update_keyhook()
        QTimer.singleShot(0, self.place)

    def _type_submit(self):
        t = self.type_edit.text()
        self.type_edit.clear()
        if t:
            self.state.tv_text(t)  # Damion: ATV sendText / BT text — not ADB

    def _escape(self):
        if getattr(self, "type_edit", None) and self.type_edit.isVisible():
            self._hide_type()
            return
        self.hide()

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
        P.restart_app()
        QApplication.quit()

    def open_setup(self):
        # One Connect TV window; close the help guide so they don't stack
        if getattr(self, "_help", None):
            self._help.hide()
        if not hasattr(self, "_setup"):
            self._setup = SetupWindow(self.state)
            self.state._setup_ref = self._setup
        self._setup.show(); self._setup.raise_(); self._setup.activateWindow()

    def _adb_badge_click(self):
        """Footer Android: connect if off, otherwise a menu of everyday ADB actions."""
        if self.state.adb_ok():
            m = QMenu(self)
            for title, act in (
                ("Разбудить", "wake"),
                ("Усыпить", "sleep"),
                ("Домой", "home"),
                ("Снимок экрана", "screenshot"),
                ("Настройки ТВ", "settings"),
                ("Приложения", "apps"),
                ("Закрыть приложение", "force_stop"),
                ("Установить APK…", "apk"),
                ("Перезагрузить ТВ", "reboot"),
                ("Отключить ADB", "disconnect"),
                (None, None),
                ("Подключение ТВ…", "setup"),
            ):
                if title is None:
                    m.addSeparator(); continue
                if act == "setup":
                    m.addAction(title, lambda: (self.hide(), self.open_setup()))
                elif act == "screenshot":
                    m.addAction(title, self.state.tv_screenshot)
                elif act == "apk":
                    # the popup would close under the file dialog anyway
                    m.addAction(title, lambda: (self.hide(), pick_apk(self.state)))
                else:
                    m.addAction(title, lambda a=act: self.state.adb_action(a))
            m.exec(self.adb_badge.mapToGlobal(self.adb_badge.rect().bottomLeft()))
        else:
            self.hide()
            self.open_setup()

    def toggle_compact(self):
        s = self.state
        s.conf["compact"] = not s.conf.get("compact")
        save_conf(s.conf)
        s.changed.emit()

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
            why = {"Нет открытой страницы": "Откройте вкладку в браузере и попробуйте снова."}.get(reason, reason)
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
        self.kb_btn.setVisible(True)
        if bt_only:
            self.r_now.setText(f"Bluetooth-пульт · {s.bt.get('peer') or 'ТВ'}")
            self.r_now.setVisible(True)
        if not ready and self.type_edit.isVisible():
            self._hide_type()
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

        # compact mode: just what's playing / this tab, volume and the footer
        compact = bool(s.conf.get("compact"))
        self.corner.sync(compact)
        if compact:
            for w in (self.sep_remote, self.r_starting_box, self.r_pin, self.r_setup, self.r_bt,
                      self.r_ctl, self.r_fail_box, self.house):
                w.setVisible(False)
        else:
            self.house.setVisible(True)
        for w in (self.c_rows, self.c_tail, self.i_rows, self.i_clip):
            w.setVisible(not compact)
        self.c_bar.setVisible(compact)
        self.i_bar.setVisible(compact)
        if compact:
            self.c_bar_prev.setVisible(self.c_prev.isVisibleTo(self.c_rows))
            self.c_bar_next.setVisible(self.c_next.isVisibleTo(self.c_rows))
            self.c_bar_play.setIcon(glyph("pause" if s.is_playing else "play", 16, T.accent, 2.0))
            self.i_bar_cast.setEnabled(self.i_cast.isEnabled())

        # footer
        col = T.green if s.device else T.fg_a(0.4)
        self.dot.setStyleSheet(f"background:{Theme.css(col)};border-radius:3px")
        dup = bool(s.device) and sum(1 for x in s.devices if x["name"] == s.device["name"]) > 1
        self.dev_name.setText((s.device["name"] + (f" ({s.device['ip']})" if dup else "")) if s.device
                              else ("Ищу ТВ…" if s.scanning else "Устройство не найдено"))
        self.switch.setVisible(True)                   # always available: rescan / add by IP even with one TV
        self.switch.setText("Выбрать")                 # short: the TV's name is right next to it
        # Keep the footer Android in sync (don't restart the Connected! animation every tick)
        if getattr(self, "adb_badge", None):
            want = "ok" if s.adb_ok() else "off"
            if self.adb_badge._state in ("run", "err"):
                pass
            elif self.adb_badge._state != want:
                self.adb_badge.set_state(want)
        if self.isVisible():
            QTimer.singleShot(0, self.place)

    # --- window
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(T.fg_a(0.16), 1))
        bg = QColor(T.bg); bg.setAlphaF(0.86 if P.IS_LINUX else 1.0)   # no blur behind on Windows: opaque
        if not self.translucent:                         # square, solid panel (RDP, VMs, old drivers)
            bg.setAlphaF(1.0)
            p.fillRect(self.rect(), bg)
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
        if not P.IS_LINUX:
            return
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
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        # Tray Popup often never becomes the "active" window — grab keys so arrows reach us
        self._grab_keys()
        if getattr(self, "_keyhook", None):
            self._keyhook.start()
            self._update_keyhook()
        self.state.refresh()

    def hideEvent(self, e):
        self._ungrab_keys()
        if getattr(self, "_keyhook", None):
            self._keyhook.active = False
            self._keyhook.stop()
        self._was_editing = False
        self.hidden_at = time.monotonic()
        self._wheel = [0.0, 0.0, 0.0]
        super().hideEvent(e)

    # ScrollRemote.swift + ScrollNav.swift: wheel/touchpad anywhere → d-pad
    def wheelEvent(self, e):
        # Damion's ScrollRemote: only while the TV remote session is paired
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

def adb_state(ip: str, force: bool = False, port: int | None = None) -> str:
    """device | unauthorized | closed | offline | missing

    `force=True` bypasses the backoff (user clicked Check / Pair).
    Otherwise we avoid fresh `adb connect` while unauthorized — each connect
    re-shows the Allow debugging dialog on the TV.
    """
    if not P.adb_available():
        _adb_remember(ip or "", "missing")
        return "missing"
    if not ip or not _is_ipv4(ip):
        return "offline"
    if force:
        _ADB_HOST.pop(ip, None)           # user retry — drop stale closed/unauthorized
    elif (cached := _adb_cached(ip)):
        return cached
    conf = load_conf()
    ports = []
    if port:
        ports.append(int(port))
    conf_port = conf.get("adb_port")
    if conf_port:
        try:
            ports.append(int(conf_port))
        except (TypeError, ValueError):
            pass
    if 5555 not in ports:
        ports.append(5555)
    # unique, keep order
    seen = set(); ports = [p for p in ports if not (p in seen or seen.add(p))]

    import socket as _s
    last = "closed"
    for p in ports:
        c = _s.socket(_s.AF_INET, _s.SOCK_STREAM); c.settimeout(1.5)
        try:
            c.connect((ip, p))
        except OSError:
            last = "closed"
            continue
        finally:
            c.close()
        serial = f"{ip}:{p}"
        P.run([_ADB, "connect", serial], capture_output=True, timeout=8)
        out = P.run([_ADB, "devices"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            if not line.startswith(serial):
                continue
            st = line.split()[1] if len(line.split()) > 1 else "offline"
            if st == "device":
                conf["adb_port"] = p
                save_conf(conf)
                _ADB_HOST[ip] = ("device", 0.0)
                return "device"
            last = st if st in _ADB_BACKOFF else "offline"
            _adb_remember(ip, last)
            return last
        last = "offline"
    _adb_remember(ip, last)
    return last


def _adb_missing_html() -> str:
    """Clickable download links when platform-tools / adb is not installed."""
    zip_url = P.platform_tools_zip_url()
    page = P.PLATFORM_TOOLS_PAGE
    dest = P.DATA
    col = Theme.css(T.accent)
    if LANG == "en":
        return (
            "ADB was not found on this computer. Download Android platform-tools "
            f'(<a href="{zip_url}" style="color:{col}">zip for this OS</a> · '
            f'<a href="{page}" style="color:{col}">Google’s page</a>), '
            f"unzip the <code>platform-tools</code> folder into <code>{dest}</code> "
            "or put <code>adb</code> on your PATH — then press “Check”."
        )
    return (
        "ADB не найден на этом компьютере. Скачайте Android platform-tools "
        f'(<a href="{zip_url}" style="color:{col}">zip для этой ОС</a> · '
        f'<a href="{page}" style="color:{col}">страница Google</a>), '
        f"распакуйте папку <code>platform-tools</code> в <code>{dest}</code> "
        "или положите <code>adb</code> в PATH — затем нажмите «Проверить»."
    )


class AdbBadge(QWidget):
    """Tiny red/green Android: click to (re)connect ADB; shows errors + animated Connected!

    compact=True → icon only (panel footer); full mode shows status text beside it.
    """
    clicked = pyqtSignal()

    def __init__(self, size=22, compact=False, parent=None):
        super().__init__(parent)
        self._state = "off"          # off | run | ok | err
        self._err = ""
        self._size = size
        self._compact = compact
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        self.btn = QToolButton()
        self.btn.setFixedSize(size + 6, size + 6)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn.setStyleSheet("QToolButton{border:none;border-radius:5px;padding:2px;background:transparent}"
                               f"QToolButton:hover{{background:{Theme.css(T.fg_a(0.08))}}}")
        self.btn.clicked.connect(self.clicked.emit)
        lay.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.msg = label("", 11, T.fg_a(0.55), wrap=True)
        self.msg.setOpenExternalLinks(True)
        self.msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        if compact:
            self.msg.hide()
        else:
            lay.addWidget(self.msg, 1)
        self.ok = QLabel("Connected!")
        self.ok.setFont(font(12.5 if not compact else 11, QFont.Weight.DemiBold))
        self.ok.setStyleSheet(f"color:{Theme.css(T.green)};background:transparent")
        self.ok.hide()
        lay.addWidget(self.ok, 0, Qt.AlignmentFlag.AlignVCenter)
        self._fx = QGraphicsOpacityEffect(self.ok); self._fx.setOpacity(0.0)
        self.ok.setGraphicsEffect(self._fx)
        self._anim = None
        self.set_state("off")

    def set_state(self, st: str, err: str = ""):
        """off=red inactive, run=orange busy, ok=green, err=red + message."""
        self._state, self._err = st, err or ""
        color = {"off": T.red, "run": T.orange, "ok": T.green, "err": T.red}.get(st, T.red)
        self.btn.setIcon(glyph("android", self._size, color))
        self.btn.setIconSize(QSize(self._size, self._size))
        tips = {
            "off": "ADB неактивен — нажмите, чтобы подключить",
            "run": "Подключаюсь к ТВ по ADB…",
            "ok": "ADB подключён — нажмите, чтобы проверить снова",
            "err": (err.replace("<", " ").replace(">", " ")[:180] if err else "Ошибка ADB — нажмите, чтобы повторить"),
        }
        self.btn.setToolTip(tips.get(st, ""))
        if st == "ok":
            if not self._compact:
                self.msg.clear(); self.msg.hide()
            self._flash_connected()
        elif st == "err" and err:
            self.ok.hide()
            if self._compact:
                self.msg.hide()
            else:
                self.msg.setStyleSheet(f"color:{Theme.css(T.red)};background:transparent")
                self.msg.setText(err); self.msg.show()
        elif st == "run":
            self.ok.hide()
            if self._compact:
                self.msg.hide()
            else:
                self.msg.setStyleSheet(f"color:{Theme.css(T.orange)};background:transparent")
                self.msg.setText("Подключаюсь…"); self.msg.show()
        else:
            self.ok.hide()
            if self._compact:
                self.msg.hide()
            else:
                self.msg.setStyleSheet(f"color:{Theme.css(T.fg_a(0.5))};background:transparent")
                self.msg.setText("ADB неактивен"); self.msg.show()

    def _flash_connected(self):
        self.ok.show()
        self._fx.setOpacity(0.0)
        fade_in = QPropertyAnimation(self._fx, b"opacity", self)
        fade_in.setDuration(280); fade_in.setStartValue(0.0); fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        hold = QPropertyAnimation(self._fx, b"opacity", self)
        hold.setDuration(1400); hold.setStartValue(1.0); hold.setEndValue(1.0)
        fade_out = QPropertyAnimation(self._fx, b"opacity", self)
        fade_out.setDuration(450); fade_out.setStartValue(1.0); fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(fade_in); seq.addAnimation(hold); seq.addAnimation(fade_out)
        seq.finished.connect(lambda: self.ok.hide())
        self._anim = seq
        seq.start()


class SetupWindow(QWidget):
    def __init__(self, state: State):
        super().__init__(None, Qt.WindowType.Window)
        from PyQt6.QtWidgets import QScrollArea
        self.state = state
        self.setWindowTitle(tr("Fling — подключение ТВ"))
        self.setMinimumWidth(480)
        self.resize(500, 760)
        self.adb = "?"
        self.found = []
        self.auto = False
        pal = self.palette(); pal.setColor(self.backgroundRole(), T.bg); self.setPalette(pal)
        self.setAutoFillBackground(True)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        area = QScrollArea(); area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setStyleSheet("QScrollArea{background:transparent}")
        body = QWidget()
        v = QVBoxLayout(body); v.setContentsMargins(18, 16, 18, 16); v.setSpacing(10)
        v.addWidget(label("Подключение ТВ", 17, weight=QFont.Weight.Bold))
        v.addWidget(label("Одна кнопка находит ТВ в сети и подключает пульт и ADB. "
                          "Шаги, где нужно ваше действие на ТВ, подскажут, что нажать.", 11.5, T.fg_a(0.6), wrap=True))
        self.go = QPushButton("Найти и подключить всё")
        self.go.setFixedHeight(34); self.go.setFont(font(13, QFont.Weight.DemiBold))
        self.go.setCursor(Qt.CursorShape.PointingHandCursor)
        self.go.setStyleSheet(f"QPushButton{{border:none;border-radius:8px;background:{Theme.css(T.accent)};color:white}}"
                              f"QPushButton:hover{{background:{Theme.css(T.acc_a(0.85))}}}")
        self.go.clicked.connect(self.run_all)
        v.addWidget(self.go)
        self.rows = {}
        # Only the essentials: find TV, network remote, ADB (no YouTube-code / BT clutter)
        for key, title in (("find", "Поиск ТВ в локальной сети"), ("remote", "Пульт ТВ (по сети)"),
                           ("adb", "ADB: что играет, название и обложка")):
            v.addWidget(self._row(key, title))
        v.addStretch(1)
        c2 = label(f'<a href="https://github.com/tatianathevisionary/fling" style="color:{Theme.css(T.fg_a(0.55))}">'
                   'Fling создан Damion Rashford и Tatiana · неофициальный порт</a>', 10.5, T.fg_a(0.45))
        c2.setOpenExternalLinks(True)
        v.addWidget(c2)
        area.setWidget(body)
        outer.addWidget(area)
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
        info.setOpenExternalLinks(True)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        lay.addWidget(info)
        extra = QWidget(); el = QHBoxLayout(extra); el.setContentsMargins(0, 0, 0, 0); el.setSpacing(6)
        lay.addWidget(extra)
        extra.hide()
        self.rows[key] = {"mark": mark, "btn": btn, "info": info, "extra": extra, "el": el}
        if key == "find":
            btn.clicked.connect(self.find)
            choose = small_button("Выбрать ТВ…", lambda: None)
            self.dev_menu = QMenu(choose)
            self.dev_menu.aboutToShow.connect(lambda: fill_device_menu(self.dev_menu, self.state, self))
            choose.setMenu(self.dev_menu)
            top.insertWidget(top.count() - 1, choose)
        elif key == "remote":
            btn.clicked.connect(self.state.start_pairing)
            self.pin = field("Код с экрана ТВ, 6 символов", mono=True)
            self.pin.returnPressed.connect(self._pin)
            el.addWidget(self.pin, 1); el.addWidget(small_button("Связать", self._pin))
        elif key == "adb":
            btn.hide()                                   # connect is the Android badge
            # Logical order: status → IP → port → pairing
            self.adb_badge = AdbBadge(22)
            self.adb_badge.clicked.connect(self.adb_connect)
            lay.addWidget(self.adb_badge)
            self.wip = field("192.168.1.50", mono=True)
            self.wconn = field("порт, напр. 5555", mono=True)
            if self.state.ip:
                self.wip.setText(self.state.ip)
            saved_port = load_conf().get("adb_port")
            if saved_port:
                self.wconn.setText(str(saved_port))
            self.wip.setClearButtonEnabled(True)
            self.wconn.setClearButtonEnabled(True)
            self.wip.editingFinished.connect(self._on_adb_endpoint_edited)
            self.wconn.editingFinished.connect(self._on_adb_endpoint_edited)
            self.wip.returnPressed.connect(self.adb_connect)
            self.wconn.returnPressed.connect(self.adb_connect)
            ip_row = QHBoxLayout(); ip_row.setContentsMargins(0, 2, 0, 0); ip_row.setSpacing(6)
            ip_row.addWidget(label("IP:", 11, T.fg_a(0.6)))
            ip_row.addWidget(self.wip, 1)
            lay.addLayout(ip_row)
            port_row = QHBoxLayout(); port_row.setContentsMargins(0, 2, 0, 0); port_row.setSpacing(6)
            port_row.addWidget(label("Порт:", 11, T.fg_a(0.6)))
            port_row.addWidget(self.wconn, 1)
            lay.addLayout(port_row)
            tip = label("С ТВ: Беспроводная отладка → «IP-адрес и порт». Клик по Android — запрос ADB.",
                        10.5, T.fg_a(0.45), wrap=True)
            lay.addWidget(tip)
            self.wport = field("порт сопряжения", mono=True); self.wport.setFixedWidth(110)
            self.wcode = field("код из 6 цифр", mono=True)
            pair_row = QHBoxLayout(); pair_row.setContentsMargins(0, 2, 0, 0); pair_row.setSpacing(6)
            pair_row.addWidget(label("Сопряжение:", 11, T.fg_a(0.6)))
            pair_row.addWidget(self.wport)
            pair_row.addWidget(self.wcode, 1)
            pair_row.addWidget(small_button("Связать", self.adb_pair))
            lay.addLayout(pair_row)
            # Everyday ADB tools — only visible while authorised
            self.adb_actions = QWidget()
            al = QVBoxLayout(self.adb_actions); al.setContentsMargins(0, 6, 0, 0); al.setSpacing(4)
            al.addWidget(label("Команды ADB", 11, T.fg_a(0.55), QFont.Weight.DemiBold))
            grid = QGridLayout(); grid.setSpacing(5)
            actions = [
                ("Разбудить", "wake"),
                ("Усыпить", "sleep"),
                ("Домой", "home"),
                ("Снимок", "screenshot"),
                ("Настройки", "settings"),
                ("Приложения", "apps"),
                ("Закрыть приложение", "force_stop"),
                ("Перезагрузить", "reboot"),
                ("Отключить ADB", "disconnect"),
                ("Установить APK…", "apk"),
            ]
            for i, (title, act) in enumerate(actions):
                b = small_button(title, lambda _=False, a=act: self._adb_tool(a))
                grid.addWidget(b, i // 3, i % 3)
            al.addLayout(grid)
            self.adb_actions.hide()
            lay.addWidget(self.adb_actions)
            # hide the unused legacy info line under the badge
            info.hide()
        return box

    def _set(self, key, mark, info, btn=None, extra=None):
        r = self.rows.get(key)
        if r is None:
            return
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
        self.sync()
        # Do NOT auto-run adb_connect on open — that re-prompts the TV every time
        super().showEvent(e)

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
            s.conf.update(devices=s.devices); save_conf(s.conf)
            s.changed.emit()
            if self.auto:
                QTimer.singleShot(200, self.auto_next)
        self.state.bg(catt_scan, done)

    def auto_next(self):
        s = self.state
        if s.ip and not s.tv_paired and s.tv_pairing in ("idle", "failed"):
            s.start_pairing()
        # Only try ADB once from "Find all", and only if we don't already know it's refused
        if self.adb in ("?", "closed", "offline", "missing") or _adb_cached(s.ip or "") != "unauthorized":
            self.adb_connect()
        self.auto = False

    def _pin(self):
        code = self.pin.text().translate(Panel._RU_KEYS).upper().strip()
        if len(code) == 6:
            self.pin.clear()
            self.state.atv.finish_pairing(code)

    def _parse_ip_port(self, text: str) -> tuple[str | None, int | None]:
        """Accept `192.168.1.50`, `192.168.1.50:37123`, or a bare port."""
        t = (text or "").strip()
        if not t:
            return None, None
        if ":" in t:
            host, _, rest = t.rpartition(":")
            host, rest = host.strip(), rest.strip()
            port = int(rest) if rest.isdigit() else None
            if host and _is_ipv4(host):
                return host, port
            if not host and rest.isdigit():
                return None, int(rest)
            return None, None
        if _is_ipv4(t):
            return t, None
        if t.isdigit():
            return None, int(t)
        return None, None

    def _adb_endpoint(self) -> tuple[str | None, int | None, str | None]:
        """IP + port from the manual fields (and optional IP:port paste). Error string if invalid."""
        ip_a, port_a = self._parse_ip_port(self.wip.text() if hasattr(self, "wip") else "")
        ip_b, port_b = self._parse_ip_port(self.wconn.text() if hasattr(self, "wconn") else "")
        # If user pasted IP:port into the port box, split it across both fields
        if ip_b and port_b and hasattr(self, "wip") and hasattr(self, "wconn"):
            if not (self.wip.text() or "").strip():
                self.wip.setText(ip_b)
            if self.wconn.text().strip() == f"{ip_b}:{port_b}":
                self.wconn.setText(str(port_b))
        ip = ip_a or ip_b or self.state.ip
        port = port_a or port_b
        if (self.wip.text() if hasattr(self, "wip") else "").strip() and not ip_a and not ip_b:
            # typed something in IP box that isn't a valid IPv4 / IP:port
            raw = self.wip.text().strip()
            if ":" not in raw and not _is_ipv4(raw):
                return None, None, "IP ТВ — четыре числа через точку (пример: 192.168.1.50) или «IP:порт»."
        if (self.wconn.text() if hasattr(self, "wconn") else "").strip() and port_b is None and ip_b is None:
            return None, None, "Порт ADB — только цифры (или вставьте «IP:порт» с экрана ТВ)."
        if not ip:
            return None, None, "Впишите IP телевизора вручную или найдите ТВ выше."
        if not _is_ipv4(ip):
            return None, None, "IP ТВ — четыре числа через точку (пример: 192.168.1.50)."
        return ip, port, None

    def _on_adb_endpoint_edited(self):
        # Split a pasted "IP:port" into the two boxes and remember the port
        for box in (getattr(self, "wip", None), getattr(self, "wconn", None)):
            if box is None:
                continue
            ip, port = self._parse_ip_port(box.text())
            if ip and port and ":" in box.text():
                self.wip.setText(ip)
                self.wconn.setText(str(port))
                break
        ip, port, _ = self._adb_endpoint()
        c = load_conf()
        changed = False
        if port:
            c["adb_port"] = port
            changed = True
        if ip:
            c["adb_ip"] = ip
            changed = True
        if changed:
            save_conf(c)
            self.state.conf = c

    def _use_adb_ip(self, ip: str):
        """Prefer the typed IP as the current TV for ADB (and the rest of the panel)."""
        s = self.state
        if s.device and s.device.get("ip") == ip:
            return
        known = next((d for d in s.devices if d.get("ip") == ip), None)
        if known:
            s.select_device(known)
            return
        d = {"ip": ip, "name": ip, "model": "", "manual": True}
        s.devices = [x for x in s.devices if x.get("ip") != ip] + [d]
        manual = [x for x in s.conf.get("manual", []) if x.get("ip") != ip] + [d]
        s.conf["manual"] = manual
        s.device = d
        s.conf["device"] = d
        save_conf(s.conf)
        s.atv.connect(ip)
        s.changed.emit()

    def adb_connect(self):
        if hasattr(self, "adb_badge"):
            self.adb_badge.set_state("run")
        if not P.adb_available():
            self._adb_result("missing")
            return
        ip, port, err = self._adb_endpoint()
        if err:
            self._adb_result("err", err)
            return
        self._on_adb_endpoint_edited()
        self._use_adb_ip(ip)
        self._set("adb", "run", "", None)
        self.state.bg(lambda: adb_state(ip, force=True, port=port),
                     lambda r, e: self._adb_result(r or "offline"))

    def adb_pair(self):
        if hasattr(self, "adb_badge"):
            self.adb_badge.set_state("run")
        if not P.adb_available():
            self._adb_result("missing")
            return
        ip, _, err = self._adb_endpoint()
        if err:
            self._adb_result("err", err)
            return
        self._use_adb_ip(ip)
        port, code = self.wport.text().strip(), re.sub(r"\D", "", self.wcode.text())
        if not (port.isdigit() and len(code) == 6):
            self._adb_result("err", "Введите порт сопряжения и 6-значный код из «Беспроводной отладки» на ТВ.")
            return
        def work():
            r = P.run([_ADB, "pair", f"{ip}:{port}", code], capture_output=True, text=True, timeout=20)
            return (r.stdout + r.stderr).strip()
        def done(out, e):
            ok = out and "Successfully paired" in out
            if ok:
                if hasattr(self, "adb_badge"):
                    self.adb_badge.set_state("run")
                self.adb_connect()
            else:
                self._adb_result("err", f"Не вышло: {out or e}")
        self.state.bg(work, done)

    def _adb_result(self, st, err: str = ""):
        self.adb = st if st != "err" else "fail"
        badge = getattr(self, "adb_badge", None)
        # Keep the card mark in sync; detail lives on the Android badge
        if st == "device":
            p = load_conf().get("adb_port") or 5555
            self._set("adb", "ok", "", None)
            if hasattr(self, "wconn"):
                self.wconn.setText(str(p))
            if badge:
                badge.set_state("ok")
            if hasattr(self, "adb_actions"):
                self.adb_actions.setVisible(True)
            self.state.poll_tvm()
            self.state._set_adb_ui("ok")
        elif st == "unauthorized":
            msg = ("На экране ТВ — «Разрешить» (лучше с «Всегда»), затем снова нажмите Android.")
            self._set("adb", "need", "", None)
            if badge:
                badge.set_state("err", msg)
            if hasattr(self, "adb_actions"):
                self.adb_actions.hide()
            self.state._set_adb_ui("err", msg)
        elif st == "missing":
            self._set("adb", "need", "", None)
            if badge:
                badge.set_state("err", _adb_missing_html())
            if hasattr(self, "adb_actions"):
                self.adb_actions.hide()
        else:
            msg = err or (
                "Нет ответа ADB. Проверьте IP/порт или сопряжение ниже "
                "(Беспроводная отладка → «IP-адрес и порт»)."
            )
            self._set("adb", "need", "", None)
            if badge:
                badge.set_state("err", msg)
            if hasattr(self, "adb_actions"):
                self.adb_actions.hide()
            self.state._set_adb_ui("err", msg)

    def _adb_tool(self, action: str):
        if action == "screenshot":
            self.state.tv_screenshot()
            return
        if action == "apk":
            pick_apk(self.state)
            return
        self.state.adb_action(action)

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
            self._set("remote", "ok", "Связан: кнопки приложений, стрелки, пауза, ввод текста.", "Связать заново", False)
        elif p == "starting":
            self._set("remote", "run", "Связываюсь с ТВ…", None, False)
        elif p in ("pin", "verifying"):
            self._set("remote", "need", "Введите код, который показал ТВ (A–F и цифры; русская раскладка подойдёт).", None, True)
        elif p == "failed":
            self._set("remote", "fail", s.tv_pairing_msg or "Не вышло.", "Попробовать снова", False)
        else:
            self._set("remote", "off", "Не связан.", "Связать", False)
        # Prefer live authorize state so the tools appear without re-clicking Android
        if s.adb_ok() or self.adb == "device":
            self.adb = "device"
            self._set("adb", "ok", "", None)
            if hasattr(self, "adb_badge") and self.adb_badge._state not in ("ok", "run"):
                self.adb_badge.set_state("ok")
            if hasattr(self, "adb_actions"):
                self.adb_actions.setVisible(True)
        elif self.adb == "?":
            if not P.adb_available():
                self._adb_result("missing")
            else:
                self._set("adb", "off", "", None)
                if hasattr(self, "adb_badge"):
                    self.adb_badge.set_state("off")
                if hasattr(self, "adb_actions"):
                    self.adb_actions.hide()
        else:
            if hasattr(self, "adb_actions"):
                self.adb_actions.hide()
        # Prefill empty fields from the selected TV / saved conf — never overwrite what the user is typing
        if hasattr(self, "wip") and not self.wip.hasFocus() and not self.wip.text().strip():
            self.wip.setText(s.ip or str(load_conf().get("adb_ip") or ""))
        if hasattr(self, "wconn") and not self.wconn.hasFocus() and not self.wconn.text().strip():
            saved = load_conf().get("adb_port")
            if saved:
                self.wconn.setText(str(saved))


# ---------------------------------------------------------------- "How to use" window

class HelpWindow(QWidget):
    """Scrollable guide built from fling_help.HELP, in the app's current language."""
    def __init__(self):
        super().__init__(None, Qt.WindowType.Window)
        from PyQt6.QtWidgets import QScrollArea
        import fling_help                                    # texts live in their own file
        self.setWindowTitle(tr("Fling — как пользоваться"))
        self.resize(520, 680)
        pal = self.palette(); pal.setColor(self.backgroundRole(), T.bg); self.setPalette(pal)
        self.setAutoFillBackground(True)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        area = QScrollArea(); area.setWidgetResizable(True)  # long text: scroll inside the window
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setStyleSheet("QScrollArea{background:transparent}")
        body = QWidget(); v = QVBoxLayout(body); v.setContentsMargins(22, 18, 22, 18); v.setSpacing(4)
        v.addWidget(label(tr("Как пользоваться"), 19, weight=QFont.Weight.Bold))
        for title, text in fling_help.sections("en" if LANG == "en" else "ru", P.IS_WIN):   # follow the language switch
            t = label(title, 14, weight=QFont.Weight.DemiBold)
            t.setContentsMargins(0, 14, 0, 2)                # space above each section
            v.addWidget(t)
            b = label(text, 12.5, T.fg_a(0.78), wrap=True)   # bullet steps, already translated
            b.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)   # commands can be copied
            v.addWidget(b)
        v.addStretch(1)
        area.setWidget(body)
        outer.addWidget(area)


def open_help(holder):
    """Open (or raise) the single help window; `holder` keeps a reference so it isn't garbage-collected."""
    # Close Connect TV if open — avoid stacking two extra windows on the panel
    setup = getattr(holder, "_setup", None)
    if setup is not None:
        setup.hide()
    if not getattr(holder, "_help", None):
        holder._help = HelpWindow()
    holder._help.show(); holder._help.raise_(); holder._help.activateWindow()


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
        if not P.IS_LINUX:                 # no notify-send: show toasts from the tray icon
            P.set_notifier(lambda msg: self.icon.showMessage("Fling", msg, tray_icon(), 4000))
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
        """Every 2 s while the panel is open; every 10 s in the background, and then
        only the cheap ADB read (catt costs ~0.2 s CPU per call)."""
        st = self.state
        self._tick = getattr(self, "_tick", 0) + 1
        visible = self.panel.isVisible()
        if not visible and on_battery():          # on battery with the panel closed: no background polling
            return
        if not visible and self._tick % 5:
            if st.yt_live:
                st.changed.emit()          # keep the tray tooltip clock moving
            return
        st.poll_bt()
        st.poll_tvm()
        if st.yt_live:
            st.changed.emit()
        if visible:
            st.refresh()
        elif st.panel == "casting" and not st.tvm_live and not st.cast_stuck:
            st.refresh(with_tab=False)     # a real Cast session ADB can't see

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
        self._track_changed()

    def _track_changed(self):
        """Toast when the TV moves to another video / track (switchable: "Notify when the video changes")."""
        s = self.state
        st = s.status if s.panel == "casting" else {}
        title = (st.get("title") or "").strip()
        key = (s.ip, title) if title and title not in ("YouTube", "Воспроизводится") else None
        prev, self._track = getattr(self, "_track", None), key
        if not key or key == prev or prev is None or prev[0] != key[0]:
            return                       # nothing playing, same track, first sight, or another TV
        if not s.conf.get("notify_track", True) or self.state.panel_visible():
            return                       # switched off, or the panel already shows it
        sub = st.get("subtitle") or st.get("app") or ""
        notify_send(f"Сейчас на ТВ: {title}" + (f" — {sub}" if sub else ""))

    def toggle_notify(self):
        s = self.state
        s.conf["notify_track"] = not s.conf.get("notify_track", True)
        save_conf(s.conf)
        s.changed.emit()

    def build_menu(self):
        s, m = self.state, self.menu
        m.clear()
        casting = s.panel == "casting"
        castable = s.panel == "idle_castable" or casting
        a = m.addAction("Отправить эту вкладку вместо" if casting else "Отправить эту вкладку", s.cast_tab)
        a.setShortcut(QKeySequence(GLOBAL_SHORTCUT)); a.setEnabled(castable)
        m.addAction("Отправить скопированную ссылку", s.cast_clipboard)
        m.addSeparator()
        dm = m.addMenu("Телевизор" + (f" — {s.device['name']}" if s.device else ""))
        fill_device_menu(dm, s, None)
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
        na = m.addAction("Уведомлять о смене видео", self.toggle_notify)
        na.setCheckable(True); na.setChecked(s.conf.get("notify_track", True))
        la = m.addAction("Запускать при входе", toggle_login)
        la.setCheckable(True); la.setChecked(login_enabled())
        m.addAction("Как пользоваться", lambda: open_help(self.panel))   # guide from the tray menu
        m.addAction("Сделать снимок", s.tv_screenshot)
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
    sock.waitForReadyRead(800)   # server answers "ok" once it has the command
    sock.disconnectFromServer()
    return True


class _UiCall(QObject):
    """Runs callables posted from other threads (hotkey, browser bridge) on the Qt thread."""
    call = pyqtSignal(object)


def bridge_handler(state: "State", panel: "Panel"):
    """Requests from the browser extensions (fling_bridge.py) → State actions. Runs on the Qt thread."""
    apps = {name.lower(): link for name, link, _ in TV_APPS}

    def snapshot():
        st = state.status or {}
        return {
            "ok": True, "app": "fling", "version": VERSION, "os": P.OS_NAME, "lang": LANG,
            "catt": state.catt_ok,
            "device": ({"name": state.device["name"], "ip": state.device["ip"]} if state.device else None),
            "devices": [{"name": d["name"], "ip": d["ip"]} for d in state.devices],
            "scanning": state.scanning,
            "panel": state.panel, "casting": state.cast_in_flight,
            "media": {k: st.get(k) for k in ("state", "title", "subtitle", "image", "elapsed", "duration", "app")},
            "volume": state.volume,
            "remote": {"paired": state.tv_paired, "on": state.tv_on, "app": app_name(state.tv_app),
                       "pairing": state.tv_pairing, "voice": state.tv_voice},
            "youtube_linked": state.lounge.is_linked,
            "notify_track": state.conf.get("notify_track", True),
            "apps": [name for name, _, _ in TV_APPS],
            "error": tr(state.last_error) if state.last_error else None,
        }

    def handle(path, body):
        if path in ("/v1/ping", "/v1/status"):
            return snapshot()
        if path == "/v1/tab":                      # the active tab of the browser that has focus
            url = str(body.get("url") or "")[:4096]
            if url.startswith(("http://", "https://")):
                media = body.get("media") if isinstance(body.get("media"), dict) else None
                state.browser_tab = {"url": url, "title": str(body.get("title") or "")[:300],
                                     "browser": str(body.get("browser") or "Browser")[:40],
                                     "media": media, "at": time.monotonic()}
                if panel.isVisible():
                    state.tab = state.browser_tab
                    state.changed.emit()
            return {"ok": True}
        a = body.get("action")
        if a == "cast":
            url = str(body.get("url") or "").strip()
            src = str(body.get("media_src") or "")
            if src and classify(url)[0] == "maybe" and classify(src)[0] == "direct":
                url = src
            start = body.get("start")
            state.last_error = None
            state.cast(url, notify=not panel.isVisible(),
                       start=float(start) if isinstance(start, (int, float)) else None,
                       title=str(body.get("title") or "") or None)
        elif a == "play_pause":
            state.toggle_play()
        elif a == "seek":
            state.seek(int(body.get("seconds") or SEEK))
        elif a == "stop":
            state.stop()
        elif a == "volume":
            state.set_volume(int(body.get("level", state.volume)))
        elif a == "key":
            name = body.get("key")
            if name not in KEY:
                raise ValueError(f"unknown key {name!r}")
            state.tv_key(KEY[name])
        elif a == "launch":
            link = apps.get(str(body.get("app") or "").lower())
            if not link:
                raise ValueError("unknown app")
            state.tv_launch(link)
        elif a == "text":
            state.tv_text(str(body.get("text") or "")[:500])
        elif a == "wake":
            state.wake()
        elif a == "select_device":
            d = next((x for x in state.devices if x["ip"] == body.get("ip")), None)
            if d:
                state.select_device(d)
        elif a == "notify_track":
            state.conf["notify_track"] = bool(body.get("on", not state.conf.get("notify_track", True)))
            save_conf(state.conf)
            state.changed.emit()
        elif a == "rescan":
            state.scan()
        elif a == "refresh":
            state.refresh(with_tab=False)
        elif a == "open_panel":
            panel.show_panel()
        elif a == "open_setup":
            panel.open_setup()
        elif a == "pair_remote":
            panel.show_panel()
            state.start_pairing()
        else:
            raise ValueError(f"unknown action {a!r}")
        return snapshot()
    return handle


def catt_main(args):
    """Frozen build: `Fling.exe --catt …` is the bundled catt CLI."""
    sys.argv = ["catt", *args]
    if P.IS_WIN and (sys.stdout is None or sys.stderr is None):
        # windowed build: Python has no stdio, but the parent gave us pipes — attach to them
        import ctypes, msvcrt
        k32 = ctypes.windll.kernel32
        for std, name, mode in ((-11, "stdout", "w"), (-12, "stderr", "w")):
            h = k32.GetStdHandle(std)
            if h and h != -1:
                try:
                    fd = msvcrt.open_osfhandle(h, os.O_WRONLY)
                    setattr(sys, name, open(fd, mode, encoding="utf-8", errors="replace", buffering=1))
                except OSError:
                    pass
            if getattr(sys, name) is None:
                setattr(sys, name, open(os.devnull, "w"))
    from catt.cli import main as cm
    return cm()


def main():
    global T
    if len(sys.argv) > 1 and sys.argv[1] == "--catt":
        return catt_main(sys.argv[2:])
    P.setup_logging()
    if "--after-restart" in sys.argv:              # language switch: let the old instance exit first
        sys.argv.remove("--after-restart")
        time.sleep(1.2)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd.lower().endswith(".apk") and os.path.isfile(cmd):     # "Open with Fling" on an .apk
        cmd = "install-apk"
        sys.argv.insert(1, cmd)
    if cmd == "install-apk":                       # `fling install-apk app.apk [split2.apk …]`
        cmd = "\t".join(["install-apk", *(os.path.abspath(a) for a in sys.argv[2:])])
    app = QApplication(sys.argv)
    app.setApplicationName("Fling")
    app.setDesktopFileName("fling")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(tray_icon())
    if cmd == "--make-icon":                       # installer: write fling.ico
        tray_icon().pixmap(256, 256).save(sys.argv[2])
        return 0
    if send_to_running(cmd):
        return 0
    T = Theme(app)
    install_translation()
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    state = State()
    panel = Panel(state)
    state._panel_ref = panel
    state.panel_visible = panel.isVisible
    tray = Tray(state, panel)

    def handle(c):
        c = c.strip()
        if c in ("show", "toggle"):
            panel.toggle() if c == "toggle" else panel.show_panel()
        elif c == "cast-tab":
            state.cast_tab(notify=not panel.isVisible())
        elif c == "clip":
            state.cast_clipboard(notify=not panel.isVisible())
        elif c == "help":                                    # `fling help`
            open_help(panel)
        elif c == "screenshot":
            state.tv_screenshot()
        elif c.startswith("install-apk"):
            files = [f for f in c.split("\t")[1:] if f]
            state.tv_install_apk(files) if files else pick_apk(state)
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
            data = bytes(conn.readAll())
            if data:
                handle(data.decode(errors="ignore"))
                conn.write(b"ok\n")
                conn.flush()
        conn.readyRead.connect(read)
        conn.disconnected.connect(conn.deleteLater)
        if conn.bytesAvailable():
            read()
    server.newConnection.connect(incoming)

    ui = _UiCall()
    ui.call.connect(lambda f: f(), Qt.ConnectionType.QueuedConnection)

    # Windows: system-wide Win+Shift+C (Linux registers its shortcut with KDE in install.sh)
    hotkey = P.GlobalHotkey(lambda: ui.call.emit(lambda: handle("cast-tab")))
    if P.IS_WIN:
        got = hotkey.start()
        if got:
            for row in (panel.c_cast, panel.i_cast):
                row.set_shortcut(got)
        app.aboutToQuit.connect(hotkey.stop)

    # browser extensions → this app
    from fling_bridge import BrowserBridge, DEFAULT_PORT
    bridge = BrowserBridge(bridge_handler(state, panel), ui.call.emit,
                           int(state.conf.get("bridge_port") or DEFAULT_PORT))
    if state.conf.get("bridge", True):
        bridge.start()
        app.aboutToQuit.connect(bridge.stop)

    state.atv.connect(state.ip)
    state.refresh()
    if cmd not in ("tray", "background"):
        QTimer.singleShot(300, lambda: handle(cmd))
    # Do not auto-open the help window on first launch — one panel is enough;
    # the guide stays behind the ? button / tray “How to use”.
    if not state.conf.get("help_seen"):
        state.conf["help_seen"] = True
        save_conf(state.conf)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
