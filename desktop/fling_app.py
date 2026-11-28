#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
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
import fling_remote as R          # remote modes, favourite apps, scenes, sleep timer

P.prepare_qt_env()

from PyQt6.QtCore import (QObject, Qt, QTimer, QRect, QRectF, QPoint, QPointF, QSize, QUrl,
                          QEvent, QPropertyAnimation, QEasingCurve,
                          QSequentialAnimationGroup,
                          pyqtSignal)
from PyQt6.QtGui import (QAction, QColor, QFont, QIcon, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap, QKeySequence, QShortcut,
                         QCursor, QGuiApplication, QKeyEvent)
from PyQt6.QtNetwork import (QLocalServer, QLocalSocket, QNetworkAccessManager,
                             QNetworkRequest)
from PyQt6.QtWidgets import (QApplication, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QMenu, QPushButton, QSizePolicy,
                             QSystemTrayIcon, QVBoxLayout, QWidget, QFrame,
                             QSlider, QToolButton)
import fling_media as M                      # formats, queues, slideshows, resume, recent casts
M.F = sys.modules[__name__]

import fling_tools                     # adb / scrcpy: find, choose, install (the Tools window)
import fling_catalog                   # extra modules from official sources (the Catalog tab)
import fling_devices as D              # the TV chooser window (search animation, cards, add by IP)
import fling_tailscale as TS           # remote access over Tailscale: tailnet TVs, keep-alive, settings page
TS.F = sys.modules[__name__]
import fling_samsung as SV           # Smart View for Samsung TVs: mirroring, remote, apps, DLNA
import fling_welcome                 # first run: language, then an animated tour (skippable)
SV.F = sys.modules[__name__]

HOME = P.HOME
CATT = P.find_catt()
DATA = P.DATA
CONF = P.CONF
SOCKET = "fling-panel"
SEEK = 30
GLOBAL_SHORTCUT = P.GLOBAL_SHORTCUT
SHORTCUT_LABEL = P.SHORTCUT_LABEL
VERSION = "0.7.1"

# ---------------------------------------------------------------- config

def load_conf() -> dict:
    try:
        with open(CONF, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

_CONF_LOCK = threading.Lock()   # the UI thread and the YouTube (asyncio) thread both save

def save_conf(c: dict) -> None:
    """Write fling.json atomically. Never raises: antivirus or OneDrive can briefly lock
    the file on Windows, and a failed save must not take the whole app down."""
    with _CONF_LOCK:
        tmp = f"{CONF}.{os.getpid()}.{threading.get_ident()}.tmp"   # unique per writer
        try:
            os.makedirs(os.path.dirname(CONF), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(c, f, indent=1, ensure_ascii=False)
            for attempt in range(5):                  # a locked target frees up within ~1 s
                try:
                    os.replace(tmp, CONF)
                    return
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.2)
        except Exception as e:
            print(f"fling: could not save settings: {e!r}", file=sys.stderr)
            try:
                os.remove(tmp)
            except OSError:
                pass

# ---------------------------------------------------------------- URL classifier (URLClassifier.swift)

MEDIA_EXT = {"mp4", "m4v", "webm", "ts", "mp3", "m4a", "aac", "ogg", "oga", "wav",
             "flac", "m3u8", "mpd", "mkv", "mov", "opus", "jpg", "jpeg", "png", "gif"}
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

# files dropped on Fling / `fling send`: what goes where — see fling_media.route()
SEND_MAX = M.F_SEND_MAX                      # adb push of bigger files is slow and /sdcard may be FAT


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
                  encoding="utf-8", errors="replace", env=P.CATT_ENV)
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

class CastLink:
    """One lasting Cast connection to the selected TV, inside this process (pychromecast —
    what catt uses). The TV pushes its status, so the 2-second refresh only reads it:
    no `catt info` process (in the setup build a whole second Fling.exe) every poll.
    Without pychromecast (install.ps1 keeps catt in its own env) `available` is False
    and Fling asks catt as before."""
    BACKDROP = "E8C28D3C"

    def __init__(self):
        try:
            import pychromecast  # noqa: F401
            self.available = True
        except Exception:
            self.available = False
        self.lock = threading.Lock()
        self.ip, self.cc, self.next_try = None, None, 0.0

    def close(self):
        with self.lock:
            cc, self.cc, self.ip = self.cc, None, None
        if cc:
            try:
                cc.disconnect(timeout=1)
            except Exception:
                pass

    def _connect(self, ip):
        import pychromecast
        from pychromecast.discovery import get_device_info
        dev = get_device_info(ip)
        if not dev:
            raise CattError("Телевизор не ответил. Убедитесь, что он включён, и попробуйте снова.")
        # tries=None: pychromecast keeps reconnecting by itself when the TV sleeps / wakes
        cc = pychromecast.get_chromecast_from_host((ip, 8009, dev.uuid, dev.model_name, dev.friendly_name),
                                                   tries=None, retry_wait=5, timeout=6)
        cc.wait(timeout=8)
        if cc.status is None:
            try:
                cc.disconnect(timeout=1)
            except Exception:
                pass
            raise CattError("Телевизор не ответил. Убедитесь, что он включён, и попробуйте снова.")
        print("cast link:", ip, dev.friendly_name, flush=True)
        return cc

    def info(self, ip: str) -> dict:
        """The same dict as catt_info(), from the lasting connection."""
        old = None
        with self.lock:
            if ip != self.ip:
                old, self.cc, self.ip, self.next_try = self.cc, None, ip, 0.0
            if self.cc is None:
                if time.monotonic() < self.next_try:
                    raise CattError("Телевизор не ответил.")
                try:
                    self.cc = self._connect(ip)
                except Exception:
                    self.next_try = time.monotonic() + 20      # a sleeping TV: try again later
                    raise
            cc = self.cc
        if old:
            threading.Thread(target=lambda: old.disconnect(timeout=1), daemon=True).start()
        if not cc.socket_client.is_connected:
            raise CattError("Телевизор не ответил.")
        cs, mc = cc.status, cc.media_controller
        # Ask for a fresh status only while a Cast app with the media namespace runs:
        # otherwise pychromecast *launches* the Default Media Receiver on the TV to send it.
        if mc.is_active:
            try:
                mc.update_status()                # it arrives for the next poll
            except Exception:
                pass
        ms = mc.status
        vol = int(round(cs.volume_level * 100)) if cs and cs.volume_level is not None else None
        app = getattr(cs, "app_id", None)
        if not app or app == self.BACKDROP or (ms.player_state in ("UNKNOWN", "IDLE") and not ms.content_id):
            return {"state": "IDLE", "volume": vol}
        meta = ms.media_metadata or {}
        return {
            "state": (ms.player_state or "UNKNOWN").upper(),
            "elapsed": ms.adjusted_current_time,
            "duration": ms.duration,
            "volume": vol,
            "muted": bool(cs and cs.volume_muted),
            "title": meta.get("title") or None,
            "subtitle": meta.get("subtitle") or meta.get("artist") or cs.display_name,
            "image": ms.images[0].url if ms.images else None,
            "content_id": ms.content_id,
            "app": cs.display_name,
        }


CAST = CastLink()

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
    # not Key_VolumeUp / Down / Mute: the laptop's own volume keys stay with the laptop
    "vol_up": _qt_keys("Key_Plus", "Key_Equal", "Key_BracketRight"),
    "vol_down": _qt_keys("Key_Minus", "Key_BracketLeft"),
    "mute": _qt_keys("Key_M", "Key_Cyrillic_Em"),
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


class _Closed(Exception):
    """A connect that close() or a new pairing made pointless: drop it quietly."""


AUTH_FAIL_MEMORY = 60       # s: after "the TV forgot this PC", don't retry / re-knock for this long


class ATV(QObject):
    """Owns an asyncio loop on a background thread; every public method is
    safe to call from the Qt thread and reports back through signals."""
    power = pyqtSignal(object)       # bool | None
    app = pyqtSignal(object)         # str | None
    pairing = pyqtSignal(str, str)   # state, message
    error = pyqtSignal(str)
    voice = pyqtSignal(bool)
    volume = pyqtSignal(object)      # {"level", "max", "muted"} as the TV reports it | None

    def __init__(self):
        super().__init__()
        self._vol_task = None
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.remote = None
        self.host = None
        self.connected = False
        self._lock = asyncio.Lock()   # used only on self.loop (binds to it on first use)
        self._voice_stream = None
        self._rec = None
        # bumped by close() and a new pairing (on self.loop): a connect started earlier sees it
        # after its next await, closes its own remote and gives up without a verdict
        self._gen = 0
        self._retrying = None         # host whose connect is waiting between retries
        self._auth_fail = {}          # host -> monotonic time the TV last refused this PC

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

    def recently_refused(self, host) -> bool:
        return time.monotonic() - self._auth_fail.get(host, -1e9) < AUTH_FAIL_MEMORY

    def may_try(self, host) -> bool:
        """Not marked paired, but worth one attempt: we have a cert the TV may still trust."""
        return bool(host) and os.path.exists(os.path.join(DATA, "atv", host, "cert.pem")) \
            and not self.recently_refused(host)

    def _go(self, coro):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        def done(f):
            e = f.exception()
            if isinstance(e, _Closed):
                return                                # closed on purpose (quit, update, new pairing)
            if e and P.UPDATING:
                print(f"remote: {type(e).__name__} while updating — ignored", flush=True)
                return                                # Fling is closing for the setup: no verdict now
            if e:
                if type(e).__name__ == "InvalidAuth" and self.host:
                    try:
                        os.remove(os.path.join(DATA, "atv", self.host, "paired"))
                    except OSError:
                        pass
                    self.pairing.emit("idle", "")      # the panel now shows "Pair with TV"
                self.error.emit(self.message(e))
        fut.add_done_callback(done)
        return fut

    @staticmethod
    def message(e: Exception) -> str:
        n = type(e).__name__
        print(f"remote: {n}: {e}", flush=True)       # the details go to the log, not to the user
        if n == "InvalidAuth":
            return ("Телевизор забыл этот компьютер — так бывает после сброса или обновления ТВ. "
                    "Нажмите «Связать с ТВ» и введите код с экрана телевизора.")
        if n == "CannotConnect":
            return "Пульт не отвечает: телевизор выключен или в другой сети. Fling подключится сам, когда ТВ появится."
        if n == "ConnectionClosed":
            return "Связь с пультом ТВ прервалась. Fling подключится заново сам."
        if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
            return "Телевизор не ответил вовремя. Попробуйте ещё раз."
        if n == "VoiceSessionInProgress":
            return "Голосовой поиск уже идёт."
        return str(e) or n

    # pairing
    def start_pairing(self, host):
        async def run():
            self.pairing.emit("starting", "")
            self._gen += 1                         # a connect retrying meanwhile gives up now
            try:
                async with self._lock:             # don't swap the remote under a running connect
                    if self.remote:
                        self.remote.disconnect()
                    r = self.remote = self._make(host)
                    self.host = host
                    self.connected = False
                await r.async_generate_cert_if_missing()
                await r.async_start_pairing()
                if r is not self.remote:            # closed meanwhile (quit, update)
                    return
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
                self._auth_fail.pop(self.host, None)
                self.pairing.emit("idle", "")
                await self._connect(self.host)
            except _Closed:
                pass                               # closed meanwhile (quit, update): paired all the same
            except Exception as e:
                if type(e).__name__ == "InvalidAuth":
                    self.pairing.emit("failed", "Код не совпадает — посмотрите на экран ТВ ещё раз.")
                else:
                    self.pairing.emit("failed", self.message(e))
        asyncio.run_coroutine_threadsafe(run(), self.loop)

    def cancel_pairing(self):
        async def run():
            self._gen += 1
            async with self._lock:
                if self.remote:
                    self.remote.disconnect()
                self.remote = None
                self.connected = False
            self.pairing.emit("idle", "")
        asyncio.run_coroutine_threadsafe(run(), self.loop)

    # session
    async def _connect(self, host):
        # One connect at a time: several key presses right after the TV wakes up used to
        # start parallel connects that tore down each other's half-open sessions.
        async with self._lock:
            if self.connected and self.host == host and self.remote:
                return
            gen = self._gen
            if self.remote:
                self.remote.disconnect()
            remote = self.remote = self._make(host)
            self.host = host
            # callbacks from a remote we have already replaced must not touch the live state
            remote.add_is_on_updated_callback(lambda on: remote is self.remote and self.power.emit(on))
            remote.add_current_app_updated_callback(lambda a: remote is self.remote and self.app.emit(a))
            # the TV pushes its own volume whenever it changes (remote, our keys, CEC) — no polling
            remote.add_volume_info_updated_callback(
                lambda v: remote is self.remote and self.volume.emit(dict(v) if v else None))
            def avail(ok):
                if remote is not self.remote:
                    return
                self.connected = ok
                if not ok:
                    self.power.emit(None)
                    self.app.emit(None)
                    self.volume.emit(None)
            remote.add_is_available_updated_callback(avail)
            # androidtvremote2 calls any TLS error "InvalidAuth: Need to pair again". Right after
            # Fling restarts (an update) the TV may still hold the old session and drop the first
            # handshakes — that cost people their pairing. For a known-good pair, try again a few
            # times before believing the TV has really forgotten this computer.
            # Not again for a TV that refused us a moment ago (each queued press would wait
            # another 12 s), and only while the pairing mark is there.
            def alive():
                return gen == self._gen and remote is self.remote

            def drop():
                remote.disconnect()
                raise _Closed()

            waits = (2, 4, 6) if self.paired(host) and not self.recently_refused(host) else ()
            for attempt in range(len(waits) + 1):
                try:
                    await remote.async_connect()
                except BaseException as e:
                    self.connected = False
                    if not alive():
                        drop()
                    if type(e).__name__ != "InvalidAuth" or attempt == len(waits):
                        if type(e).__name__ == "InvalidAuth":
                            self._auth_fail[host] = time.monotonic()
                        raise
                    print(f"remote: {e} — retrying in {waits[attempt]} s", flush=True)
                    self._retrying = host
                    try:
                        await asyncio.sleep(waits[attempt])
                    finally:
                        self._retrying = None
                    if not alive():
                        drop()
                    if not self.paired(host):          # paired again / forgotten meanwhile
                        raise
                    continue
                if not alive():                         # close() came while the handshake ran
                    drop()
                break
            if not self.paired(host):
                # the TV accepted our cert, so it does trust this PC: the mark was lost, not the pairing
                open(os.path.join(self._dir(host), "paired"), "w").close()
                print("remote: the TV still trusts this PC — pairing mark restored", flush=True)
            self._auth_fail.pop(host, None)
            self.connected = True
            remote.keep_reconnecting()
            self.power.emit(remote.is_on)
            self.app.emit(remote.current_app)
            if remote.volume_info:
                self.volume.emit(dict(remote.volume_info))

    def connect(self, host):
        if self.paired(host):
            self._go(self._connect(host))

    def close(self, timeout: float = 2.0):
        """Close the remote session politely (quit, update), so the TV doesn't keep a
        half-open one that trips the next Fling's first connect. Blocks up to `timeout`."""
        async def run():
            self._gen += 1                          # a connect in progress drops its own remote
            r, self.remote, self.connected = self.remote, None, False
            if r:
                r.disconnect()
                await asyncio.sleep(0.2)            # let the TLS close_notify reach the TV
        try:
            asyncio.run_coroutine_threadsafe(run(), self.loop).result(timeout)
        except Exception as e:
            print("remote: close:", e, flush=True)

    def _send(self, host, fn):
        if self._retrying == host:
            # the connect is waiting to retry: a press queued now would run 12 s late
            print("remote: key dropped, still reconnecting", flush=True)
            return None
        async def run():
            await self._connect(host)
            fn(self.remote)
        return self._go(run())

    def key(self, host, code):
        self._send(host, lambda r: r.send_key_command(code))

    def set_volume(self, host, target: int):
        """Walk the TV volume to `target` with VOLUME_UP / VOLUME_DOWN: the remote protocol has
        no "set level" command. A newer target replaces the walk in progress."""
        async def walk():
            await self._connect(host)
            r = self.remote
            stuck = 0
            for _ in range(200):                        # never more steps than a full scale twice
                v = r.volume_info
                if not v or v.get("level") == target:
                    return
                before = v["level"]
                r.send_key_command(24 if target > before else 25)
                # next step only once the TV has answered with its new level (or after 0.4 s),
                # so a slow TV doesn't overshoot
                for _ in range(20):
                    await asyncio.sleep(0.02)
                    if (r.volume_info or {}).get("level") != before:
                        stuck = 0
                        break
                else:
                    stuck += 1
                    if stuck >= 3:                      # the TV ignores the steps (limit, locked): stop
                        return
        async def start():
            if self._vol_task and not self._vol_task.done():
                self._vol_task.cancel()
            self._vol_task = asyncio.ensure_future(walk())
        self._go(start())

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


def _tools_changed():
    """A tool was installed, removed or chosen in the Tools window: use that adb from now on."""
    global _ADB
    _ADB = P.find_adb()
    for ip, (st, _until) in list(_ADB_HOST.items()):
        if st == "missing":
            _ADB_HOST.pop(ip, None)


fling_tools.on_change(_tools_changed)

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


def adb_port_for(ip: str, conf: dict | None = None) -> int:
    """The ADB port that last worked for this TV (each TV its own), else the old global one, else 5555."""
    c = conf if conf is not None else load_conf()
    try:
        return int((c.get("adb_ports") or {}).get(ip) or c.get("adb_port") or 5555)
    except (TypeError, ValueError):
        return 5555


_MDNS: dict = {"at": -99.0, "map": {}}


def adb_mdns(max_age: float = 4.0) -> dict:
    """TVs that announce ADB on the network (adb's own mDNS):
    {ip: {"adb": 5555, "connect": 36319, "pairing": 41233}}. "connect" is Android 11+'s wireless
    debugging, whose port changes on every reboot; "pairing" appears while the TV shows
    "Pair with code". So nobody has to type a port."""
    now = time.monotonic()
    if now - _MDNS["at"] < max_age:
        return _MDNS["map"]
    out: dict = {}
    if P.adb_available():
        try:
            r = P.run([_ADB, "mdns", "services"], capture_output=True, text=True, timeout=6)
            kinds = {"_adb._tcp": "adb", "_adb-tls-connect._tcp": "connect", "_adb-tls-pairing._tcp": "pairing"}
            for line in (r.stdout or "").splitlines():
                parts = line.split()
                if len(parts) < 3:
                    continue
                kind = kinds.get(parts[-2].rstrip("."))
                host, _, port = parts[-1].rpartition(":")
                if kind and port.isdigit() and _is_ipv4(host):
                    out.setdefault(host, {})[kind] = int(port)
        except Exception as e:
            print("adb mdns:", e, flush=True)
    _MDNS.update(at=now, map=out)
    return out


def _adb_serial(ip: str, port: int | None = None) -> str:
    """`ip:port` for adb -s / connect. Wireless debugging often uses a non-5555 port."""
    return f"{ip}:{int(port or adb_port_for(ip))}"


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
            # "state=3, …" on older Android, "state=PLAYING(3), …" on newer (Google TV)
            m = re.search(r"state=(?:[A-Z_]+\()?(\d+)\)?, position=(-?\d+).*?speed=([\d.]+), updated=(\d+)", l)
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
        self.screen = TvScreen(self)              # scrcpy window: TV screen + mouse pointer
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
        self.tv_volume = None        # {"level", "max", "muted"} pushed by the TV over the remote session
        self.atv.volume.connect(self._on_tv_volume)
        self._tv_vol_want = None
        self._tv_vol_timer = QTimer(singleShot=True, interval=150)
        self._tv_vol_timer.timeout.connect(lambda: self.ip and self._tv_vol_want is not None
                                           and self.atv.set_volume(self.ip, self._tv_vol_want))
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
        self.media = M.Media(self)       # queue / slideshow / resume / recent (fling_media.py)
        self.tailnet = TS.KeepAlive(self)
        self.sv = SV.Hub(self)               # Samsung / Miracast TVs: remote, apps, DLNA, mirroring
        self.online: dict[str, bool | None] = {}   # ip → answers right now (the green / red dots)
        self._online_at = 0.0
        self._online_busy = False  # a TV on the tailnet: ping, reconnect, footer latency
        self.caps: dict = {}           # ip -> ports that answered (6466 remote, 5555 ADB, 8009 Cast…)
        self.suggest = None            # another TV to switch to while this one is away (_think)
        self._away = 0

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
            self._set(last_error="Телевизор не выбран — нажмите «Выбрать» внизу панели.")
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

    def send_files(self, paths: list[str]):
        """Files / folders dropped on Fling or `fling send`: install, play (a queue for several
        videos, songs or photos; playlists and folders too) or copy them to the TV."""
        if SV.is_sv(self.device):
            self.sv.send_files(paths)
            return
        self.media.send(paths)

    def adb_push(self, path: str, why: str = "", then=None):
        """Copy a file to the TV's Download folder (ADB), in its own thread; then() after success."""
        ip, name = self.ip, os.path.basename(path)
        if not ip:
            self._set(last_error="Телевизор не выбран — нажмите «Выбрать» внизу панели.")
            return
        try:
            if os.path.getsize(path) > SEND_MAX:
                self._set(last_error=f"Не отправлю {name}: файл больше 4 ГБ")
                return
        except OSError as ex:
            self._set(last_error=f"Не отправлю {name}: {ex}")
            return

        def run():
            try:
                err = _adb_ready_error(ip, "Отправка файла")
                if err:
                    r = {"error": err + (f" ({why})" if why else "")}
                else:
                    res = adb_cmd(ip, ["push", path, "/sdcard/Download/" + name], timeout=3600)
                    out = (res.stdout or "") + (res.stderr or "")
                    if res.returncode != 0:
                        last = next((l.strip() for l in reversed(out.splitlines()) if l.strip()), "нет ответа")
                        r = {"error": "Файл не скопировался на ТВ: " + last}
                    else:
                        r = {"ok": True}
            except Exception as ex:
                r = {"error": f"Файл не скопировался на ТВ: {ex}"}
            self.bridge.call.emit(lambda: done(r))

        def done(r):
            print("adb push:", name, r, flush=True)
            if r.get("error"):
                self._set(last_error=r["error"])
            else:
                self._set(last_error=None)
                notify_send(f"Файл на ТВ: Download/{name}")
                if then:
                    then()

        notify_send(f"Копирую на ТВ: {name}…")
        threading.Thread(target=run, daemon=True, name="fling-push").start()

    def tv_install_apk(self, paths: list[str]):
        """Install an APK on the TV over ADB (`adb install -r`). Several files at once are
        the parts of one split app (`install-multiple`)."""
        paths = [p for p in paths if p]
        ip = self.ip
        if not paths:
            return
        if not ip:
            self._set(last_error="Телевизор не выбран — нажмите «Выбрать» внизу панели.")
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
        if not ip or SV.is_sv(self.device) or not P.adb_available():
            return False
        hit = _ADB_HOST.get(ip)
        return bool(hit and hit[0] == "device")

    def adb_action(self, action: str):
        """Everyday ADB actions once the TV is authorised."""
        ip = self.ip
        if not ip:
            self._set(last_error="Телевизор не выбран — нажмите «Выбрать» внизу панели.")
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
                self._set(last_error=f"Команда ADB не сработала: {e or 'ТВ не ответил'}")
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
        ip = None if SV.is_sv(self.device) else self.ip    # no Cast status from a Samsung / Miracast TV
        def work():
            tab = current_tab(self.browser_tab) if with_tab else None
            st = None
            if ip:
                try:
                    st = CAST.info(ip) if CAST.available else catt_info(ip)
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
                    self.media.observe(st)
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
        self.sv.scan(self._merge_sv)                   # Samsung TVs and Miracast displays, in parallel
        def done(found, e):
            self.scanning = False                      # search finished (UI stops showing "Searching…")
            if P.UPDATING:                             # the scan was cut off for the setup: change nothing
                return
            if e is not None:
                # catt failed (cut off, crashed, timed out): that says nothing about which TVs are
                # there. Keep the Android TV / Chromecast list we had — treating it as "none found"
                # used to switch the chosen TV to the Samsung found in parallel, and the remote
                # looked unpaired after an update.
                print("scan: catt failed, keeping the known TVs:", e, flush=True)
                found = [d for d in self.devices if not SV.is_sv(d)]
            found = list(found or [])                  # catt scan result, may be empty
            found += [d for d in self.devices if SV.is_sv(d)]   # Samsung / Miracast found meanwhile
            known = {d["ip"] for d in found}           # IPs seen on the network right now
            found += [d for d in self.conf.get("manual", []) if d["ip"] not in known]   # TVs added by IP
            if not found:
                self.changed.emit()
                return
            if self.device and self.device["ip"] not in [d["ip"] for d in found]:
                same = [d for d in found if d["name"] == self.device["name"]
                        and (not d.get("model") or not self.device.get("model") or d["model"] == self.device["model"])]
                if len(same) == 1:                     # the same TV under a new address (DHCP)
                    self.device = same[0]
                else:
                    # the chosen TV didn't answer this time (asleep, off, a slow mDNS reply): keep
                    # it — its dot shows it offline — instead of quietly switching to another TV
                    found.append(self.device)
            self.devices = found
            if not self.device:
                self.device = found[0]
            self.conf.update(devices=[d for d in found if not SV.is_miracast(d)], device=self.device)
            save_conf(self.conf)
            if SV.is_sv(self.device):
                self.sv.select(self.device)
            else:
                self.atv.connect(self.ip)
            self.changed.emit()
            self.refresh()
        self.bg(catt_scan, done)

    def _merge_sv(self, extra):
        """Samsung / Miracast results join the TV list (Cast results keep their place)."""
        if P.UPDATING:                                 # cut off for the setup: change nothing
            return
        ips = {d["ip"] for d in extra}
        self.devices = [d for d in self.devices if not SV.is_sv(d) and d["ip"] not in ips] + extra
        if not self.device and self.devices:
            self.select_device(self.devices[0])
            return
        self.conf["devices"] = [d for d in self.devices if not SV.is_miracast(d)]
        save_conf(self.conf)
        self.changed.emit()

    # ---- is a TV reachable right now: the footer dot, the TV menu and the chooser cards
    _PROBE_PORTS = {"samsung": (8001, 8002, 9197), None: (8009, 8008, 6466, 5555)}

    def tv_status(self, d) -> bool | None:
        """True: answers now · False: doesn't · None: not checked yet / can't tell."""
        return self.online.get(d["ip"]) if d else None

    # what kind of TV: the list is grouped by it (Android TV first: it can do the most)
    KINDS = (("androidtv", "Android TV и Google TV"), ("chromecast", "Chromecast"),
             ("samsung", "Samsung"), ("miracast", "Повтор экрана (Miracast)"))
    _ATV_WORDS = ("google tv", "android tv", "mitv", "mi tv", "bravia", "shield", "tcl", "hisense",
                  "philips", "sharp", "realme", "oneplus", "xiaomi", "sber", "yandex", "haier", "tv box")

    def device_kind(self, d) -> str:
        k = d.get("kind")
        if k in ("samsung", "miracast"):
            return k
        ports = self.caps.get(d["ip"], ())
        if 6466 in ports or 6467 in ports or 5555 in ports or self.atv.paired(d["ip"]):
            return "androidtv"
        model = f"{d.get('model') or ''} {d.get('name') or ''}".lower()
        if any(w in model for w in self._ATV_WORDS):
            return "androidtv"
        if "chromecast" in model or "google home" in model or "nest" in model or ports:
            return "chromecast"
        return "androidtv"                           # not probed yet: Cast devices are mostly TVs

    def grouped_devices(self) -> list[tuple[str, list[dict]]]:
        """[(group title, devices)] in the order the chooser and the menus show: by kind, then
        the current TV, then those online, then by name."""
        cur = self.device["ip"] if self.device else None
        out = []
        for kind, title in self.KINDS:
            ds = [d for d in self.devices if self.device_kind(d) == kind]
            ds.sort(key=lambda d: (d["ip"] != cur, self.online.get(d["ip"]) is not True,
                                   (d.get("name") or d["ip"]).lower()))
            if ds:
                out.append((title, ds))
        return out

    @staticmethod
    def _probe(d, mira_seen):
        """(online, open ports): True / False / None, and which of the TV's ports answered."""
        if SV.is_miracast(d):                       # Wi-Fi Direct: only a search can see it
            seen = mira_seen.get(d["name"])
            return (True if seen and time.monotonic() - seen < 300 else None), ()
        import select, socket as _s
        ports = State._PROBE_PORTS.get(d.get("kind") if d.get("kind") == "samsung" else None)
        socks, port_of, open_ = [], {}, set()
        try:                                         # all ports at once: about 1 s for a TV that's gone
            for port in ports:
                c = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
                c.setblocking(False)
                try:
                    c.connect((d["ip"], port))
                except (BlockingIOError, InterruptedError):
                    pass
                except OSError:
                    c.close(); continue
                socks.append(c); port_of[c] = port
            end = time.monotonic() + 1.0
            while socks and time.monotonic() < end:
                _, w, x = select.select([], socks, socks, max(0.0, end - time.monotonic()))
                for c in w:
                    if c.getsockopt(_s.SOL_SOCKET, _s.SO_ERROR) == 0:
                        open_.add(port_of[c])
                for c in set(w) | set(x):            # answered / refused: done with it
                    if c in socks:
                        socks.remove(c); c.close()
                if not w and not x:
                    break
            return bool(open_), tuple(sorted(open_))
        except (OSError, ValueError):
            return False, ()
        finally:
            for c in socks:
                c.close()

    def _think(self):
        """Small decisions so nobody has to make them: the TV in use stopped answering (two
        checks in a row) while another known TV answers — offer that one in the panel, one click.
        Android TV first (it can do the most), Miracast never (it can't be probed)."""
        d = self.device
        alt = None
        if d and not SV.is_miracast(d):
            self._away = self._away + 1 if self.online.get(d["ip"]) is False else 0
            if self._away >= 2:
                alts = [x for x in self.devices if x["ip"] != d["ip"] and not SV.is_miracast(x)
                        and self.online.get(x["ip"]) is True]
                if alts:
                    alt = min(alts, key=lambda x: (self.device_kind(x) != "androidtv", (x.get("name") or "").lower()))
        if (alt or {}).get("ip") != (self.suggest or {}).get("ip"):
            if alt:
                print(f"think: {d['name']} is away, {alt['name']} answers: offering it", flush=True)
            self.suggest = alt
            self.changed.emit()

    def check_online(self, force=False):
        """Probe every known TV in the background — every 6 s while the panel is open,
        every 25 s otherwise."""
        now = time.monotonic()
        visible = bool(getattr(self, "panel_visible", None) and self.panel_visible())
        if self._online_busy or (not force and now - self._online_at < (6 if visible else 25)):
            return
        devs = list(self.devices)
        if self.device and self.device["ip"] not in {d["ip"] for d in devs}:
            devs.append(self.device)
        if not devs:
            return
        self._online_at, self._online_busy = now, True
        seen = dict(self.sv.mira_seen)

        def work():
            with ThreadPoolExecutor(max_workers=8) as ex:
                return dict(zip([d["ip"] for d in devs], ex.map(lambda d: State._probe(d, seen), devs)))

        def done(r, e):
            self._online_busy = False
            if not r:
                return
            on = {k: v[0] for k, v in r.items()}
            caps = {k: v[1] for k, v in r.items() if v[1]}      # keep what we knew while it's away
            if on != {k: self.online.get(k) for k in on} or any(self.caps.get(k) != v for k, v in caps.items()):
                self.online.update(on)
                self.caps.update(caps)
                self.changed.emit()
            self._think()
        self.bg(work, done)

    def select_device(self, d):
        if self.device and d["ip"] == self.device["ip"]:   # already on this TV: nothing to do
            return
        QTimer.singleShot(0, lambda: self.check_online(force=True))
        self.media.stop(quiet=True)       # the old TV's file server and queue end here
        self.device = d
        self.status = {}
        self.tvm = {}                      # now-playing, remote state and screenshots belong to the old TV
        self.tv_on = self.tv_app = None
        self.tv_pairing, self.tv_pairing_msg = "idle", ""
        self.conf["device"] = d
        save_conf(self.conf)
        self.last_error = None
        self.sv.select(d)                  # Samsung: its remote; any other TV: closes it
        if not SV.is_sv(d):
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
        if TS.is_tailscale(d):                     # reached over the tailnet
            marks.append("Tailscale")
        elif d.get("manual"):                      # added by hand, not found by discovery
            marks.append("по IP")
        return d["name"] + (f"  ·  {', '.join(marks)}" if marks else "")

    def add_device_ip(self, ip, via=None, name=None):
        """Add a TV that discovery can't see (other subnet, mDNS blocked) by its IP address.
        via="tailscale": a tailnet address (fling_tailscale.add) — Cast, remote or ADB answering is enough."""
        ip = ip.strip()
        parts = ip.split(".")                      # IPv4: 4 numbers, each 0..255
        if len(parts) != 4 or not all(x.isascii() and x.isdigit() and int(x) <= 255 for x in parts):
            self._set(last_error="Это не похоже на IP-адрес (пример: 192.168.1.50).")
            return
        def work():
            if via == "tailscale":
                return TS.probe_tv(ip, name)
            import socket as _s
            c = _s.socket(_s.AF_INET, _s.SOCK_STREAM); c.settimeout(2)  # IPv4 only
            try:
                c.connect((ip, 8009))                  # 8009 = Google Cast port
            except OSError:
                raise RuntimeError(f"по адресу {ip} нет Chromecast / Google TV (порт 8009 закрыт)")
            finally:
                c.close()
            tv_name = ip
            try:   # the TV's own name from its DIAL device description
                import urllib.request
                with urllib.request.urlopen(f"http://{ip}:8008/ssdp/device-desc.xml", timeout=4) as r:   # closes the connection
                    xml = r.read().decode("utf-8", "ignore")
                m = re.search(r"<friendlyName>([^<]+)</friendlyName>", xml)
                tv_name = m[1].strip() if m else ip
            except Exception:
                pass
            return {"ip": ip, "name": tv_name, "model": "", "manual": True}
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
            self._set(last_error="Телевизор не выбран — нажмите «Выбрать» внизу панели.")
            return
        def done(r, e):
            self.last_error = str(e) if e else None
            if after:
                after()
            self.changed.emit()
            QTimer.singleShot(400, lambda: self.refresh(with_tab=False))
        self.bg(lambda: catt_run(["-d", ip, *args], timeout=timeout), done)

    def cast(self, url, notify=False, start=None, title=None):
        if M.is_unc(url):     # never touch \\host\share: Windows would hand it the login hash (NTLM)
            self._set(last_error=M.UNC_ERROR)
            return
        if SV.is_sv(self.device):          # Samsung: DLNA for media links, else its app / browser
            if os.path.isfile(url):
                self.sv.send_files([url])
            else:
                self.sv.cast_url(url, title)
            return
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
            self._set(last_error="Телевизор не выбран — нажмите «Выбрать» внизу панели.")
            return
        if TS.cast_blocked(self.device):              # a tailnet TV whose Cast port doesn't answer
            self._set(last_error=TS.cast_blocked(self.device))
            return
        if os.path.isfile(url):                       # a file: played (and served) by fling_media
            self.media.send([url])
            return
        self.media.before_cast()
        start = self.media.start_for(url, start)      # browser position, ?t= in the link, or resume
        self.cast_in_flight = True
        self.changed.emit()
        opts = ["-f"] if kind == "direct" else []
        if start and start > 3:                       # "Resume on TV at 12:34"
            opts += ["-t", str(int(start))]
        if title and kind == "direct":
            opts += ["-l", title[:200]]
        def after():
            self.cast_in_flight = False
            if not self.last_error:
                self.media.url_started(url, title)
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
            self._set(last_error="В буфере обмена нет ссылки.")
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
        if self.media.toggle_slideshow():   # a slideshow pauses its timer, not the TV
            return
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
            for _ in range(min(60, max(1, abs(s) // 10))):   # YouTube TV: one press = 10 s (max 60 presses)
                self.tv_key(KEY["ffwd"] if s > 0 else KEY["rewind"])
            return
        self._catt(["ffwd" if s > 0 else "rewind", str(abs(s))])

    def stop(self):
        self.media.stop()                    # ends Fling's own file server / queue item
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
        # An app started on the TV (YouTube from the TV's remote) has no Cast session to take a Cast
        # volume: with ADB, set the TV's own volume, whatever plays; Cast only without it.
        if self.ip and not SV.is_sv(self.device) and self.adb_ok():
            ip, v = self.ip, self.volume

            def work():
                r = adb_cmd(ip, ["shell", "cmd", "media_session", "volume", "--stream", "3", "--get"], timeout=8)
                m = re.search(r"range \[0\.\.(\d+)\]", r.stdout or "")
                if not m:
                    raise OSError("the TV didn't say its volume range")
                level = round(v * int(m.group(1)) / 100)
                adb_cmd(ip, ["shell", "cmd", "media_session", "volume", "--stream", "3", "--set", str(level)], timeout=8)
            self.bg(work, lambda r, e: e and self._catt(["volume", str(v)]))
            return
        self._catt(["volume", str(self.volume)])

    # TV volume (Android TV Remote): the level the TV itself reports, not the Cast app volume
    def _on_tv_volume(self, v):
        self.tv_volume = v
        if v and self._tv_vol_want == v.get("level"):
            self._tv_vol_want = None                    # arrived
        self.changed.emit()

    def set_tv_volume(self, level: int):
        v = self.tv_volume
        if not v:
            return
        self._tv_vol_want = max(0, min(int(v.get("max") or 100), int(level)))
        self._tv_vol_timer.start()                      # wait until the slider stops moving

    def toggle_tv_mute(self):
        self.tv_key(KEY["mute"])

    def wake(self):
        if SV.is_sv(self.device):
            self.sv.power_on()
            return
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
        # plus ADB as a fallback: keys go as `input keyevent` (slower, but no pairing needed)
        return self.tv_paired or self.bt_connected or self.adb_ok() or self.sv.ready

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
        if SV.is_sv(self.device):                 # Samsung: its own network remote
            self.sv.key(code)
            return
        # Same path as upstream Fling: Android TV Remote / BT — not ADB keyevents
        if self.tv_paired and self.ip:
            self.atv.key(self.ip, code)
        elif self.bt_connected and code in BT_NAMES:
            self._bt(f"key {BT_NAMES[code]}")
        elif self.ip and self.adb_ok():
            self._adb_key(code)
        elif self.ip and self.atv.may_try(self.ip):
            # no pairing mark, but our cert may still be trusted (the mark got lost): try it —
            # success restores the mark; a refusal is remembered, so presses don't keep knocking
            self.atv.key(self.ip, code)
        elif self.ip:
            # not paired and no other way to the TV: say once what to do
            msg = "Пульт не связан с телевизором. Нажмите «Связать с ТВ» и введите код с экрана ТВ."
            if self.last_error != msg:
                self._set(last_error=msg)

    def _adb_key(self, code):
        """Fallback remote: `adb shell input keyevent` (KEY codes are Android key codes).
        One worker thread keeps the presses in order; held arrows don't pile up."""
        ip = self.ip
        if not hasattr(self, "_adb_keys"):
            self._adb_keys = ThreadPoolExecutor(max_workers=1)
            self._adb_queued = 0
        if self._adb_queued > 3:                  # the TV is slower than a held key: drop repeats
            return
        self._adb_queued += 1

        def run():
            try:
                adb_cmd(ip, ["shell", "input", "keyevent", str(int(code))], timeout=6)
            except Exception as ex:
                print("adb key:", ex, flush=True)
            finally:
                self._adb_queued -= 1
        self._adb_keys.submit(run)

    def tv_launch(self, link):
        if SV.is_sv(self.device):
            self.sv.launch(link)
        elif self.ip:
            self.atv.launch(self.ip, link)

    def tv_text(self, s):
        if not s:
            return
        if SV.is_sv(self.device):
            self.sv.text(s)
        elif not self.tv_paired and self.bt_connected:
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
            self._set(last_error="Телевизор не выбран — нажмите «Выбрать» внизу панели.")
            return
        self.atv.start_pairing(self.ip)

# ---------------------------------------------------------------- language (ru / en)

EN_PHRASES = [  # longest first is applied automatically
    ("⏳  Связываюсь с ТВ…", "⏳  Contacting the TV…"),
    (" Без звука", " Mute"), (" Громк +", " Vol +"), (" Громк −", " Vol −"),
    ("Буфер обмена пуст", "Clipboard is empty"),
    ("Введите 12 цифр с экрана:", "Enter the 12 digits shown on screen:"),
    ("Введите код с экрана телевизора", "Enter the code from the TV screen"),
    ("Вкл/выкл ТВ", "Toggle TV power"), ("Включить ТВ", "Turn on the TV"), ("Выключить ТВ", "Turn off the TV"),
    ("Воспроизводится", "Playing"), ("Выйти из Fling", "Quit Fling"),
    ("Остановить голосовой поиск", "Stop voice search"),
    ("Голосовой поиск уже идёт.", "Voice search is already running."), ("Голосовой поиск", "Voice search"),
    ("Движок трансляции не установлен. Установите его один раз — Fling его найдёт.",
     "The Cast engine isn't installed. Install it once and Fling will find it."),
    ("Домой", "Home"), ("Запускать при входе", "Start at login"),
    ("или крутите колёсико / тачпад в любом месте панели", "or scroll anywhere to move around the TV"),
    ("Клавиатура: стрелки/WASD · Enter=OK · Backspace=Назад · Home · +/− громкость · Пробел пауза · колёсико",
     "Keyboard: arrows/WASD · Enter=OK · Backspace=Back · Home · +/− volume · Space pause · scroll"),
    ("Громкость −  (− / [)", "Volume −  (− / [)"),
    ("Громкость +  (+ / ])", "Volume +  (+ / ])"),
    ("Без звука  (M)", "Mute  (M)"),
    ("Звук ТВ: щёлкните, чтобы выключить / включить", "TV sound: click to mute / unmute"),
    ("Громкость телевизора", "TV volume"), ("Громкость трансляции", "Cast volume"),
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
    ("Не вижу активного приложения", "No foreground app found"),
    ("Закрыл ", "Closed "),
    ("Connected!", "Connected!"),
    ("С ТВ: Беспроводная отладка → «IP-адрес и порт». Клик по Android — запрос ADB.",
     "From the TV: Wireless debugging → “IP address & port”. Click the Android to request ADB."),

    ("и тут появятся настоящие обложка, название и время.", "and the real artwork, title and time will show here."),
    ("код не подошёл", "the code didn't work"),
    ("Код не совпадает — посмотрите на экран ТВ ещё раз.", "That code doesn't match — check the TV screen again."),
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
    ("Fling не видит вкладки браузера", "Fling can't see your browser tabs"),
    ("Чтобы Fling видел вкладки Chrome и Edge, нужно один раз добавить в браузер расширение Fling.",
     "For Fling to see Chrome and Edge tabs, add the Fling extension to the browser once."),
    ("Установить расширение…", "Install the extension…"),
    # files sent to the TV
    ("Отпустите — отправлю на ТВ", "Drop — I'll send it to the TV"),
    ("APK — установка; видео, музыка, фото, папки и плейлисты — на экран; остальное — в папку Download",
     "APKs install; videos, music, photos, folders and playlists play on the screen; "
     "everything else goes to the Download folder"),
    ("Не отправлю ", "Not sending "), ("это не файл", "not a file"), ("файл больше 4 ГБ", "the file is over 4 GB"),
    ("Нечего отправить", "Nothing to send"), ("Отправляю на ТВ: ", "Sending to the TV: "),
    ("Не получилось показать ", "Couldn't play "), (" — копирую файл на ТВ", " — copying the file to the TV"),
    ("Отправка файла", "Sending a file"), ("Файл не скопировался на ТВ: ", "The file wasn't copied to the TV: "),
    ("Файл на ТВ: Download/", "File on the TV: Download/"), ("Копирую на ТВ: ", "Copying to the TV: "),
    # TV screen (scrcpy), ADB console
    ("Экран ТВ в отдельном окне", "TV screen in its own window"), ("Мышь на ТВ без картинки", "Mouse on the TV, no picture"),
    ("Курсор на ТВ", "Cursor on the TV"),
    ("ADB-консоль…", "ADB console…"), ("Журнал Fling", "Fling log"),
    ("Fling — экран ТВ", "Fling — TV screen"), ("Fling — курсор: щёлкните здесь, Alt — вернуть мышь", "Fling — cursor: click here, Alt gives the mouse back"),
    ("Fling — ADB-консоль", "Fling — ADB console"),
    ("LIVE — экран ТВ вживую, управление мышью", "LIVE — the TV screen live, control it with the mouse"),
    ("Щёлкните по картинке — мышь станет курсором на ТВ. Alt — вернуть мышь. Клавиатура в окне печатает на ТВ.",
     "Click the picture — the mouse becomes a pointer on the TV. Alt gives it back. The keyboard types on the TV."),
    ("Перезапустить картинку", "Restart the picture"), ("Подключаюсь к экрану ТВ…", "Connecting to the TV screen…"),
    ("Экран ТВ не появился. Нажмите «Перезапустить картинку».",
     "The TV screen didn't appear. Click “Restart the picture”."),
    ("Экран ТВ закрыт.", "The TV screen is closed."),
    ("Нажмите «Перезапустить картинку».", "Click “Restart the picture”."),
    ("Питание ТВ", "TV power"), ("Тише", "Volume down"), ("Громче", "Volume up"),
    ("Закрыть  (Esc)", "Close  (Esc)"), ("Меньше", "Smaller"), ("Больше", "Larger"),
    ("ADB подключён · картинка идёт", "ADB connected · the picture is live"),
    ("Звук ТВ пойдёт на этот компьютер; на самом ТВ он может пропасть, пока открыта картинка. Переключение перезапускает картинку.",
     "The TV's sound goes to this computer; the TV itself may go quiet while the picture is open. Switching restarts the picture."),
    ("Звук с этого ТВ не передаётся", "This TV doesn't send its sound"), ("Звук на ПК", "Sound on PC"),
    ("Щёлкните в окне — мышь станет курсором на ТВ. Alt — вернуть мышь компьютеру.",
     "Click in the window — the mouse becomes a pointer on the TV. Alt gives the mouse back."),
    ("Щелчки в окне — нажатия на ТВ.", "Clicks in the window tap the TV."),
    ("Экран ТВ закрылся: ", "The TV screen closed: "),
    ("Касания", "Taps"), ("Только смотреть", "Just watch"), ("Мышь", "Mouse"), ("Курсор ТВ", "TV cursor"),
    ("Курсор компьютера над картинкой: щелчок попадает ровно туда, куда наведён. Правая кнопка — «Назад», колёсико — прокрутка.",
     "The computer's pointer over the picture: a click lands exactly where it points. Right button — Back, wheel — scroll."),
    ("ТВ рисует свой курсор (для приложений, которым он нужен). Он движется сам по себе и может не совпадать с картинкой. Уведите мышь за край картинки (или Alt) — она вернётся.",
     "The TV draws its own cursor (for apps that need one). It moves on its own and may not match the picture. Move the mouse past the picture's edge (or press Alt) to get it back."),
    ("Качество: авто", "Quality: auto"), ("Чётко", "Sharp"), ("Плавно", "Smooth"), ("Экономно", "Saver"),
    ("Быстро", "Fast"), ("сеть", "network"),
    ("1280 px, 60 кадров/с — само снижается, если сеть не успевает", "1280 px, 60 frames/s — steps down by itself if the network can't keep up"),
    ("1920 px, 30 кадров/с — чётче, но задержка больше", "1920 px, 30 frames/s — sharper, but more delay"),
    ("960 px, 60 кадров/с — самая маленькая задержка, для игр", "960 px, 60 frames/s — the least delay, for games"),
    ("960 px, 2 Мбит/с — когда сеть медленная", "960 px, 2 Mbit/s — for a slow network"),
    ("Это только задержка сети. Картинка ещё ждёт, пока телевизор сожмёт кадр — обычно 50–150 мс, сколько именно, ТВ не сообщает. Меньше всего — в «Быстро».",
     "This is the network delay only. The picture also waits for the TV to compress each frame — usually 50–150 ms, which the TV doesn't report. The least in “Fast”."),
    ("Начинает с хорошего и само снижается, если сеть не успевает",
     "Starts at high quality and steps down by itself when the network can't keep up"),
    ("1920 px, 8 Мбит/с — для быстрой сети", "1920 px, 8 Mbit/s — for a fast network"),
    ("60 кадров/с — для игр и прокрутки", "60 frames/s — for games and scrolling"),
    ("960 px, 2 Мбит/с — когда картинка запаздывает", "960 px, 2 Mbit/s — when the picture lags"),
    ("Проверяю связь с ТВ…", "Checking the link to the TV…"),
    ("ТВ не отвечает по сети", "the TV doesn't answer on the network"),
    ("сеть теряет пакеты", "the network is losing packets"), ("большая задержка сети", "high network latency"),
    ("кадры не успевают", "frames can't keep up"), ("Сеть до ТВ", "Network to the TV"), ("нет ответа", "no answer"),
    ("потеряно", "lost"), ("Кадров", "Frames"), ("пропущено", "skipped"),
    ("(экран ТВ не меняется — это нормально)", "(the TV screen isn't changing — that's fine)"),
    ("Как идёт картинка", "How the picture is doing"), ("Нажмите — выбрать качество.", "Click to choose the quality."),
    ("Картинка может запаздывать: ", "The picture may lag: "), ("Включаю «Экономно»…", "Switching to “Saver”…"),
    ("Попробуйте качество «Экономно».", "Try the “Saver” quality."),
    ("Это уже самое лёгкое качество — проверьте Wi-Fi.", "This is already the lightest quality — check the Wi-Fi."),
    ("Сеть не успевала — включил «Экономно». Качество можно выбрать справа вверху.",
     "The network couldn't keep up — switched to “Saver”. Choose the quality at the top right."),

    ("Пульт и что играет", "The remote and what's playing"),
    ("Экран ТВ вживую, управление мышью", "The TV screen live, control it with the mouse"),
    ("Маленькое окно", "Small window"), ("Среднее окно", "Medium window"), ("Большое окно", "Large window"),
    ("Во весь экран", "Full screen"), ("Без звука", "Mute"), ("Домой", "Home"),
    ("Пульт: стрелки, OK, громкость", "Remote: arrows, OK, volume"),
    ("Звук на ПК — звук ТВ пойдёт на этот компьютер; на самом ТВ он может пропасть, пока открыта картинка. "
     "Переключение перезапускает картинку.",
     "Sound on PC — the TV's sound comes to this computer; the TV itself may go quiet while the picture "
     "is open. Switching restarts the picture."),
    ("Клавиатура: на ТВ — нажмите, чтобы оставить её у компьютера",
     "Keyboard: to the TV — click to keep it on the computer"),
    ("Клавиатура: у компьютера — нажмите, чтобы передавать на ТВ",
     "Keyboard: on the computer — click to send it to the TV"),
    ("Мышь: курсор на ТВ", "Mouse: pointer on the TV"), ("Мышь: касания", "Mouse: taps"),
    ("Мышь: только смотреть", "Mouse: just watch"),
    ("Клавиатура: на ТВ", "Keyboard: to the TV"), ("Клавиатура: у компьютера", "Keyboard: stays on the PC"),
    ("Щёлкните по картинке — мышь станет курсором на ТВ. Alt — вернуть мышь.",
     "Click the picture — the mouse becomes a pointer on the TV. Alt gives it back."),
    ("Щёлкните по картинке — мышь станет курсором на ТВ. Уведите её за край картинки (или Alt) — она вернётся.",
     "Click the picture — the mouse becomes a pointer on the TV. Move it past the picture's edge (or press Alt) "
     "and it comes back."),
    ("Мышь не захватывается: щелчок по картинке — нажатие в этом месте ТВ.",
     "The mouse isn't captured: a click on the picture taps the TV there."),
    ("Только картинка: мышь и клавиатура остаются у компьютера.",
     "Just the picture: the mouse and keyboard stay with the computer."),
    ("Клавиатура в окне печатает на ТВ.", "The keyboard in the window types on the TV."),
    ("Клавиатура не передаётся на ТВ.", "The keyboard isn't sent to the TV."),
    ("Экран ТВ: scrcpy не запустился — ", "TV screen: scrcpy didn't start — "),
    (" работает через ADB — включите его: «Подключение ТВ…» → ADB.",
     " works over ADB — turn it on: “Connect TV…” → ADB."),
    (": ADB не установлен — откройте «Подключение ТВ…» и скачайте platform-tools.",
     ": ADB is not installed — open “Connect TV…” and download platform-tools."),
    (": подтвердите на ТВ «Разрешить отладку».", ": allow debugging on the TV."),
    (": ADB не подключён — откройте «Подключение ТВ…».", ": ADB is not connected — open “Connect TV…”."),
    (": установите scrcpy (sudo apt install scrcpy).", ": install scrcpy (sudo apt install scrcpy)."),
    ("Экран ТВ", "TV screen"),
    ("для этого компьютера нет готовой сборки scrcpy — установите его из пакетов",
     "there is no scrcpy build for this computer — install it from your packages"),
    ("в релизе scrcpy нет сборки для этого компьютера", "the scrcpy release has no build for this computer"),
    ("нет контрольной суммы scrcpy", "no scrcpy checksum"),
    ("в архиве scrcpy нет программы scrcpy", "the scrcpy download has no scrcpy program"),
    ("Выполнить", "Run"), ("Стоп", "Stop"), ("Быстрые команды ▾", "Quick commands ▾"),
    ("Скопировать вывод", "Copy output"), ("Очистить", "Clear"),
    ("ТВ не выбран   ·   ", "No TV selected   ·   "), ("ТВ не выбран.", "No TV selected."), ("ТВ: ", "TV: "),
    ("Команда выполняется в оболочке ТВ (adb shell). adb-команды вроде logcat, install, pull пишите как есть. "
     "↑/↓ — история.",
     "Commands run in the TV's shell (adb shell). Type adb commands like logcat, install, pull as they are. "
     "↑/↓ — history."),
    ("Эта команда может удалить данные, перезагрузить ТВ или разорвать связь ADB. Выполнить?",
     "This command may delete data, restart the TV or cut the ADB link. Run it?"),
    ("консоль всегда работает с выбранным ТВ: -s, connect, pair, kill-server и другие ключи adb здесь не нужны — "
     "подключение настраивается в «Подключение ТВ…».",
     "the console always works with the selected TV: -s, connect, pair, kill-server and other adb options "
     "aren't needed here — the link is set up in “Connect TV…”."),
    ("напишите команду после shell — интерактивная оболочка здесь не открывается.",
     "type a command after shell — an interactive shell doesn't open here."),
    ("кавычки не закрыты", "unclosed quotes"),
    ("Модель и Android", "Model and Android"), ("Что играет", "What's playing"),
    ("Активное приложение", "Foreground app"), ("Установленные приложения", "Installed apps"),
    ("Разрешение экрана", "Screen size"), ("Свободное место", "Free space"), ("Память", "Memory"),
    ("Сеть", "Network"), ("Логи: последние 300 строк", "Logs: last 300 lines"),
    ("Логи вживую: только ошибки", "Live logs: errors only"),
    ("Расширение Fling добавлено в браузер.", "The Fling extension is added to the browser."),
    ("Добавляю расширение в браузер — не трогайте мышь и клавиатуру пару секунд…",
     "Adding the extension to the browser — don't touch the mouse or keyboard for a few seconds…"),
    ("Папка расширения не найдена — переустановите Fling.", "The extension folder is missing — reinstall Fling."),
    ("Расширение Fling для Chrome", "Fling extension for Chrome"), ("Один раз в Chrome:", "Once, in Chrome:"),
    ("1. На открывшейся странице «Расширения» включите «Режим разработчика» (справа вверху).",
     "1. On the Extensions page that opened, turn on “Developer mode” (top right)."),
    ("2. Нажмите «Загрузить распакованное расширение».", "2. Click “Load unpacked”."),
    ("3. Вставьте путь (Ctrl+V — он уже скопирован) и нажмите «Выбор папки».",
     "3. Paste the path (Ctrl+V — it's already copied) and click “Select Folder”."),
    ("Или просто перетащите папку chrome из Проводника на страницу расширений.",
     "Or just drag the chrome folder from Explorer onto the Extensions page."),
    ("Нет модуля sounddevice для записи микрофона.", "The sounddevice module is missing for the microphone."),
    ("Микрофон недоступен: ", "Microphone unavailable: "),
    ("Снимок экрана ТВ: ", "TV screenshot: "),
    ("Уведомлять о смене видео", "Notify when the video changes"), ("Сейчас на ТВ: ", "Now on TV: "), ("Возобновить на ТВ с ", "Resume on TV at "),
    ("Отмена", "Cancel"), ("Отправить скопированную ссылку", "Cast copied link"),
    ("Отправить эту вкладку вместо текущего видео", "Cast this tab instead of the current video"), ("Отправить эту вкладку", "Cast this tab"),
    ("Не в сети — Fling не достаёт до этого ТВ", "Offline — Fling can't reach this TV"),
    ("Нажмите, чтобы переключить телевизор", "Click to switch TVs"), ("Проверяю связь…", "Checking…"),
    ("Не в сети", "Offline"), ("В сети", "Online"),
    ("Убрать отправку вкладок из панели — вернуть можно в Настройках",
     "Remove tab casting from the panel — Settings brings it back"), ("Скрыть", "Hide"),
    ("Отправляю…", "Casting…"), ("Пауза / Играть", "Play / Pause"), ("Пауза на ТВ", "Pause on TV"),
    ("Предыдущее видео", "Previous video"), ("Следующее видео", "Next video"), ("Пробел", "Space"),
    ("Проверьте код — он действует несколько минут.", "Check the code — it's valid for a few minutes."),
    ("Один раз свяжите Fling с ТВ, чтобы открыть приложения, стрелки и ввод текста.",
     "Pair once with the TV to unlock apps, the d-pad and typing."),
    ("Связать с YouTube на ТВ — видеть, что играет", "Link YouTube on the TV — see what's playing"),
    ("Связать с YouTube, чтобы видеть, что играет", "Link YouTube to see what's playing"),
    ("Включить", "Turn on"),
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
    ("Телевизор не ответил.", "The TV didn't respond."),
    ("Телевизор не выбран — нажмите «Выбрать» внизу панели.", "No device selected"), ("Уже отправляю…", "Already sending…"),
    ("Уже отправляю…", "Already sending…"), ("Телевизор не найден", "No TV found"),
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
    ("Оригинальный Fling создали Damion Rashford и Tatiana<br>неофициальный порт — orfixe",
     "Original Fling by Damion Rashford and Tatiana<br>unofficial port by orfixe"),
    ("Оригинальный Fling создали Damion Rashford и Tatiana · неофициальный порт — orfixe",
     "Original Fling by Damion Rashford and Tatiana · unofficial port by orfixe"),
    ("Пульт", "Remote"),
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
    ('страница Google', "Google's page"),
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
    ('Ничего не нашлось. Компьютер и ТВ в одной сети Wi-Fi? ', 'Nothing found. Are the computer and the TV on the same network? '),
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
    ('Не вышло: ', "Failed: "),
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
    ('Введите порт сопряжения и 6-значный код из «Беспроводной отладки» на ТВ.',
     'Enter the pairing port and 6-digit code from “Wireless debugging” on the TV.'),
    ('На экране ТВ — «Разрешить» (лучше с «Всегда»), затем снова нажмите Android.',
     'On the TV screen, choose “Allow” (ideally with “Always”), then click the Android again.'),
    ('Нет ответа ADB. Проверьте IP/порт или сопряжение ниже (Беспроводная отладка → «IP-адрес и порт»).',
     'No answer from ADB. Check the IP/port or the pairing below (Wireless debugging → “IP address & port”).'),
    ('Неизвестное действие: ', 'Unknown action: '),
    ('ошибка', 'error'),
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
EN_PHRASES += [   # guard
    ("Файлы Fling изменены после установки, поэтому Fling не запущен. "
     "Установите Fling заново со страницы релизов на GitHub и проверьте компьютер антивирусом.",
     "Fling's files were changed after installation, so Fling was not started. "
     "Install Fling again from the GitHub releases page and scan the computer for malware."),
]
EN_PHRASES += [   # updates (fling_update)
    ("Доступна версия Fling %s — Настройки → О программе.", "Fling %s is available — Settings → About."),
    ("Обновление Fling не скачалось: ", "The Fling update didn't download: "),
    ("Fling обновляется до %s и сейчас перезапустится.", "Fling is updating to %s and will restart now."),
    ("Установить", "Install"), ("Установить сейчас", "Install now"), ("Скачать", "Download"),
    ("Пропустить эту версию", "Skip this version"), ("пропущена", "skipped"), ("Что нового", "What's new"),
    ("Проверять обновления автоматически", "Check for updates automatically"),
    ("Раз в день Fling смотрит, не вышла ли новая версия.", "Once a day Fling looks for a new version."),
    ("Устанавливать обновления автоматически", "Install updates automatically"),
    ("Новая версия скачивается в фоне, проверяется подписью релиза и ставится, когда панель закрыта и экран ТВ не показывается. Fling перезапустится сам.",
     "A new version downloads in the background, is checked against the release signature and installs while the panel is closed and the TV screen isn't shown. Fling restarts by itself."),
    ("Скачиваю и проверяю подпись…", "Downloading and checking the signature…"),
    ("Не получилось: ", "Didn't work: "),
    ("Версия %s скачана и проверена. Установится, когда панель будет закрыта.", "Version %s is downloaded and verified. It installs once the panel is closed."),
]
EN_PHRASES += [   # the report for the developer (fling_report)
    ("Отчёт для разработчика…", "Report for the developer…"),
    ("Отчёт для разработчика", "Report for the developer"),
    ("Отчёт не получился: ", "The report failed: "),
    ("Отчёт сохранён в «Загрузки»: %s. Приложите его к сообщению об ошибке на GitHub или отправьте тому, кто попросил.",
     "The report is saved in Downloads: %s. Attach it to an issue on GitHub, or send it to whoever asked for it."),
]
EN_PHRASES += [   # plain-words errors
    ("Телевизор не выбран — нажмите «Выбрать» внизу панели.", "No TV chosen — click “Choose” at the bottom of the panel."),
    ("Пульт не связан с телевизором. Нажмите «Связать с ТВ» и введите код с экрана ТВ.",
     "The remote isn't paired with the TV. Click “Pair with TV” and type the code from the TV screen."),
    ("Телевизор забыл этот компьютер — так бывает после сброса или обновления ТВ. Нажмите «Связать с ТВ» и введите код с экрана телевизора.",
     "The TV has forgotten this computer — it happens after a TV reset or update. Click “Pair with TV” and type the code from the TV screen."),
    ("Пульт не отвечает: телевизор выключен или в другой сети. Fling подключится сам, когда ТВ появится.",
     "The remote isn't answering: the TV is off or on another network. Fling reconnects by itself when the TV is back."),
    ("Связь с пультом ТВ прервалась. Fling подключится заново сам.", "The connection to the TV remote dropped. Fling reconnects by itself."),
    ("Телевизор не ответил вовремя. Попробуйте ещё раз.", "The TV didn't answer in time. Try again."),
    ("Экран ТВ не открылся: ", "The TV screen didn't open: "),
    ("В буфере обмена нет ссылки.", "There's no link on the clipboard."),
    ("Включить ТВ", "Turn the TV on"),
]
EN_PHRASES += [   # the TV list by kind, and the switch suggestion
    ("Android TV и Google TV", "Android TV and Google TV"), ("Повтор экрана (Miracast)", "Screen mirroring (Miracast)"),
    ("«{a}» не в сети — переключиться на «{b}»", "“{a}” is offline — switch to “{b}”"),
    # ADB, the simple way
    ("код из 6 цифр с экрана ТВ", "6-digit code from the TV screen"), ("Вручную: IP и порт…", "By hand: IP and port…"),
    ("Скрыть ручные поля", "Hide the manual fields"), ("сопряжения:", "pairing:"), ("порт", "port"),
    ("Обычно не нужно: Fling находит порты сам. С ТВ: Беспроводная отладка → «IP-адрес и порт».",
     "Usually not needed: Fling finds the ports itself. On the TV: Wireless debugging → “IP address & port”."),
    ("Сначала выберите телевизор выше (или впишите IP вручную).", "Choose a TV above first (or type its IP by hand)."),
    ("Посмотрите на экран ТВ: нажмите «Разрешить» и отметьте «Всегда разрешать с этого компьютера». Fling подключится сам, как только вы нажмёте.",
     "Look at the TV: press “Allow” and tick “Always allow from this computer”. Fling connects by itself as soon as you do."),
    ("ТВ пока не пускает по ADB.", "The TV doesn't let ADB in yet."),
    ("Включите на ТВ отладку — Fling заметит это сам:\n1. Настройки → Об устройстве → 7 раз нажмите «Сборка».\n2. Настройки → Для разработчиков → «Отладка по сети» (или «Отладка по USB»).\nНа Android 11+: «Беспроводная отладка» → «Подключить по коду» — код появится на ТВ, впишите его здесь.",
     "Turn on debugging on the TV — Fling notices by itself:\n1. Settings → About → press “Build” 7 times.\n2. Settings → Developer options → “Network debugging” (or “USB debugging”).\nOn Android 11+: “Wireless debugging” → “Pair with pairing code” — the TV shows a code, type it here."),
    ("ТВ показывает код сопряжения.", "The TV shows a pairing code."),
    ("На экране ТВ открыто сопряжение — впишите 6 цифр с экрана и нажмите «Связать».",
     "The TV is showing a pairing code — type the 6 digits and press Pair."),
    ("Код сопряжения — 6 цифр с экрана ТВ.", "The pairing code is 6 digits from the TV screen."),
    ("Окно с кодом на ТВ уже закрыто. На ТВ: Беспроводная отладка → «Подключить по коду» — и впишите новый код.",
     "The code window on the TV is closed. On the TV: Wireless debugging → “Pair with pairing code”, then type the new code."),
    ("Ждём «Разрешить» на экране ТВ…", "Waiting for “Allow” on the TV…"),
]
EN_PHRASES += [   # keyboard capture modes and the key guide
    ("⌨  Клавиатура у ТВ · Esc — закрыть · F1 — подсказка", "⌨  Keyboard drives the TV · Esc closes · F1 help"),
    ("⌨  Клавиши пульта у ТВ, остальные — у компьютера", "⌨  Remote keys drive the TV, the rest stay with the PC"),
    ("Вся клавиатура у ТВ", "Whole keyboard to the TV"), ("Только клавиши пульта", "Only the remote's keys"),
    ("Не захватывать клавиатуру", "Don't capture the keyboard"), ("Не захватывать", "Don't capture"),
    ("Какие клавиши что делают", "Which key does what"), ("Захват", "Capture"),
    ("«%s» не управляет ТВ · F1 — подсказка · Esc — закрыть", "“%s” does nothing on the TV · F1 help · Esc closes"),
    ("Пока открыта панель, клавиатура управляет ТВ", "While the panel is open, the keyboard drives the TV"),
    ("Стрелки или W A S D — навигация · Enter — OK · Backspace — назад · H — домой · Пробел — пауза · + / − — громкость · M — без звука · < > — перемотка · цифровой блок 8 2 4 6 5 — тоже стрелки и OK.",
     "Arrows or W A S D — move · Enter — OK · Backspace — back · H — home · Space — pause · + / − — volume · M — mute · < > — seek · numpad 8 2 4 6 5 — arrows and OK too."),
    ("Хотели печатать в другой программе? Esc закрывает панель и сразу возвращает клавиатуру. Или выберите, как захватывать:",
     "Meant to type in another app? Esc closes the panel and gives the keyboard back at once. Or choose how to capture:"),
    ("Печатать на ТВ", "Type on the TV"), ("Понятно", "Got it"),
    ("Пока панель открыта, клавиатура управляет ТВ: стрелки или WASD, Enter, Пробел, Backspace, H, +/−. Esc закрывает панель. F1 — подсказка.\n«Захват» — вся клавиатура, только клавиши пульта или без захвата. Fling запомнит выбор.",
     "While the panel is open, the keyboard drives the TV: arrows or WASD, Enter, Space, Backspace, H, +/−. Esc closes the panel. F1 for help.\n“Capture”: the whole keyboard, only the remote's keys, or none. Fling remembers the choice."),
]
EN_PHRASES += [   # keyboard captured strip
    ("⌨  Клавиатура захвачена, Esc — выход", "⌨  Keyboard captured, Esc to exit"),
    ("⌨  Клавиатура свободна", "⌨  Keyboard is free"),
    ("Оформление", "Appearance"), ("Как в системе", "As the system"), ("Тёмное", "Dark"),
    ("Светлое", "Light"), ("Цвет акцента", "Accent colour"), ("Системный", "System"),
    ("Синий", "Blue"), ("Фиолетовый", "Purple"), ("Зелёный", "Green"), ("Оранжевый", "Orange"),
    ("Отключить", "Release"), ("Захватить", "Capture"),
    ("Настройки Fling", "Fling settings"), ("Настройки…", "Settings…"),
    ("О программе Fling", "About Fling"),
    ("Пока панель открыта, стрелки, Enter, Пробел, Backspace, Home и +/− управляют ТВ, "
     "а не программой, в которой вы были.\nEsc закрывает панель.\n"
     "Нужна клавиатура в другой программе — нажмите «Отключить»: пульт останется "
     "на кнопках панели. Fling запомнит выбор.",
     "While the panel is open, the arrows, Enter, Space, Backspace, Home and +/− control the TV, "
     "not the app you were in.\nEsc closes the panel.\n"
     "Need the keyboard in another app? Click “Release”: the remote stays on the panel's buttons. "
     "Fling remembers it."),
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
    ("Установка APK: ТВ не ответил за 10 минут.", "Installing an APK: the TV didn't respond within 10 minutes."),
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
EN_PHRASES += fling_tools.EN_PHRASES   # the Tools window keeps its texts next to its code
EN_PHRASES += fling_catalog.EN_PHRASES
EN_PHRASES += M.PHRASES          # formats, queues, slideshows (fling_media.py)
EN_PHRASES += D.PHRASES + [("Найти ТВ…", "Find TVs…")]   # the TV chooser (fling_devices.py)
EN_PHRASES += TS.PHRASES   # remote access over Tailscale (fling_tailscale.py)
EN_PHRASES += SV.PHRASES   # Smart View for Samsung TVs (fling_samsung.py)
EN_PHRASES.sort(key=lambda kv: -len(kv[0]))
EN_REGEX = [(re.compile(r"(\d+) с\b"), r"\1s")]
R.bind(sys.modules[__name__])     # fling_remote reuses the helpers below and adds its phrases


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

# Themes, accent colours and "as the system" live in fling_themes; the Settings window
# (themes with a live preview, About) in fling_settings.
import fling_themes
ACCENTS = fling_themes.ACCENTS
THEMES = ("auto", *fling_themes.PRESETS)


def system_dark(app) -> bool:
    """The system's own light/dark choice (before Fling overrides anything)."""
    try:
        cs = app.styleHints().colorScheme()
        if cs == Qt.ColorScheme.Dark:
            return True
        if cs == Qt.ColorScheme.Light:
            return False
    except Exception:
        pass
    return app.palette().window().color().lightness() < 128


def system_accent() -> QColor | None:
    """Windows: the accent colour from Personalisation → Colours; elsewhere the palette's."""
    if P.IS_WIN:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as k:
                v = int(winreg.QueryValueEx(k, "AccentColor")[0]) & 0xFFFFFFFF   # 0xAABBGGRR
            return QColor(v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)
        except Exception:
            return None
    c = QApplication.palette().highlight().color()
    return c if c.isValid() else None


def apply_color_scheme(app, conf: dict) -> None:
    """The theme chosen in Fling: make Qt's own widgets (menus, fields, dialogs) match."""
    dark = fling_themes.scheme_dark(conf)
    try:
        app.styleHints().setColorScheme({True: Qt.ColorScheme.Dark, False: Qt.ColorScheme.Light}
                                        .get(dark, Qt.ColorScheme.Unknown))
    except Exception:
        pass


class Theme:
    def __init__(self, app, conf: dict | None = None):
        conf = load_conf() if conf is None else conf
        sys_dark = app.palette().window().color().lightness() < 128
        r = fling_themes.resolve(conf, sys_dark,
                                 system_accent() if conf.get("accent", "auto") == "auto" else None)
        self.id = r["id"]
        self.dark, self.bg, self.fg, self.accent = r["dark"], r["bg"], r["fg"], r["accent"]
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

_SHORT_WORD = re.compile(r"(?<![\w-])([А-Яа-яЁё]{1,2}) (?=\S)")


def typo_ru(text: str) -> str:
    """Russian typography for wrapped text: a one- or two-letter word (и, в, с, на, по…)
    never hangs at the end of a line — it is glued to the next word. Russian UI only:
    in English the Russian string is still the translation key."""
    return _SHORT_WORD.sub("\\1\u00a0", text) if LANG != "en" and "<" not in text else text


def label(text="", px=12.5, color=None, weight=QFont.Weight.Normal, wrap=False):
    l = QLabel()
    l.setText(typo_ru(text) if wrap else text)
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
       "tv": "tv", "mic_fill": "mic.fill"}


def home_path(s: float) -> QPainterPath:
    """Home: a roof over walls with a door opening, on an 18 px grid. Wide enough that
    the door never merges with the walls at 100 % (the SF house turned into a blot)."""
    k = s / 18
    pa = QPainterPath()
    pa.moveTo(2.2 * k, 8.6 * k); pa.lineTo(9 * k, 2.8 * k); pa.lineTo(15.8 * k, 8.6 * k)      # roof
    pa.moveTo(4.2 * k, 7.4 * k); pa.lineTo(4.2 * k, 15.3 * k); pa.lineTo(7.4 * k, 15.3 * k)  # left wall
    pa.lineTo(7.4 * k, 11.3 * k); pa.lineTo(10.6 * k, 11.3 * k); pa.lineTo(10.6 * k, 15.3 * k)  # door
    pa.lineTo(13.8 * k, 15.3 * k); pa.lineTo(13.8 * k, 7.4 * k)                              # right wall
    return pa


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
        p.setPen(QPen(pen.color(), 1.4 * s / 18, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawPath(home_path(s))
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
    elif name == "dot":                                      # filled circle: a colour swatch
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(color or T.fg)
        p.drawEllipse(QRectF(s*.15, s*.15, s*.7, s*.7))
    elif name == "keyboard":
        # Keyboard — opens the “type on TV” field. Drawn on a 20 px grid in whole pixels
        # (1 px outline, 2 px keys 2 px apart, a space bar) so it stays crisp at 100 %:
        # smaller anti-aliased keys merged into one white block.
        k = s / 20
        thin = QPen(color or T.fg, 1.0 * k)
        thin.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(thin)
        p.drawRoundedRect(QRectF(2.5 * k, 5.5 * k, 15 * k, 10 * k), 2 * k, 2 * k)
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color or T.fg)
        for x in (5, 9, 13):
            p.drawRect(QRectF(x * k, 8 * k, 2 * k, 2 * k))
        p.drawRect(QRectF(6 * k, 12 * k, 8 * k, 1.2 * k))
        p.restore()
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
        lay = QHBoxLayout(self); lay.setContentsMargins(12, 7, 10, 0); lay.setSpacing(4)
        lay.addWidget(SectionSwitch("remote", lambda: None, panel.go_live))
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
        self.gear = QToolButton()                       # Settings: appearance, options, About
        import fling_settings
        self.gear.setIcon(fling_settings.icon("gear", 15, T.fg_a(0.6))); self.gear.setIconSize(QSize(15, 15))
        self.gear.setToolTip("Настройки Fling")
        self.gear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gear.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.gear.setStyleSheet(self.compact.styleSheet())
        self.gear.clicked.connect(lambda: (panel.hide(), open_settings(panel)))
        lay.addWidget(self.help)
        lay.addWidget(self.gear)
        lay.addWidget(self.compact)

    def sync(self, compact):
        self.compact.setIcon(glyph("expand" if compact else "compact", 14, T.fg_a(0.6)))
        self.compact.setToolTip("Полный вид" if compact else "Компактный вид")


ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def tray_icon(casting=False):
    """Every size drawn from the vector, not one bitmap scaled: sharp in the tray, the taskbar
    and Alt+Tab at any display scale."""
    ic = QIcon()
    for n in ICON_SIZES:
        ic.addPixmap(logo_pixmap(n, casting))
    return ic


def write_ico(path):
    """A Windows .ico with one PNG per size (each drawn at its size, so 16 px isn't a blur)."""
    import struct
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    images = []
    for n in ICON_SIZES:
        ba = QByteArray(); buf = QBuffer(ba); buf.open(QIODevice.OpenModeFlag.WriteOnly)
        logo_pixmap(n).save(buf, "PNG")
        images.append((n, bytes(ba)))
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for n, data in images:
        out += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    with open(path, "wb") as f:
        f.write(out + b"".join(d for _, d in images))


def logo_pixmap(size=64, casting=False, dpr=1.0):
    """The logo at any size (drawn on a 64-unit grid, so it stays sharp when large)."""
    pm = QPixmap(int(round(size * dpr)), int(round(size * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64, size / 64)
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
    return pm

# ---------------------------------------------------------------- widgets (PanelRows.swift / RemoteView.swift)

class MiniSeg(QWidget):
    """A small segmented control (the panel's theme and language), as on Android."""
    def __init__(self, items, selected, on):
        super().__init__()
        lay = QHBoxLayout(self); lay.setContentsMargins(2, 2, 2, 2); lay.setSpacing(0)
        self.setStyleSheet(f"MiniSeg{{background:{Theme.css(T.fg_a(0.07))};border-radius:8px}}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._b = []
        for i, t in enumerate(items):
            b = QPushButton(t)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, i=i: (self._select(i), on(i)))
            lay.addWidget(b); self._b.append(b)
        self._select(selected)

    def _select(self, idx):
        for i, b in enumerate(self._b):
            on = i == idx
            b.setStyleSheet(
                "QPushButton{border:none;border-radius:6px;padding:3px 9px;font-size:11.5px;"
                + (f"background:{Theme.css(T.acc_a(0.22))};color:{Theme.css(T.accent)};font-weight:600}}" if on
                   else f"background:transparent;color:{Theme.css(T.fg_a(0.7))}}}"
                        f"QPushButton:hover{{background:{Theme.css(T.fg_a(0.08))}}}"))


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
        if not icon:                 # never setVisible(True) here: parentless = a flashing window
            self.ic.hide()
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

    # while pressed, a bubble above the knob shows the level; `fmt(value)` makes its text
    fmt = staticmethod(lambda v: f"{v}%")
    _bubble = None

    def _set_from(self, x):
        lo, hi = self.minimum(), self.maximum()           # the TV's own scale, not always 0–100
        self.setValue(lo + round(max(0, min(1, x / max(1, self.width()))) * (hi - lo)))
        self._show_bubble()

    def _show_bubble(self):
        if SlimSlider._bubble is None:
            b = QLabel(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
            b.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            b.setAlignment(Qt.AlignmentFlag.AlignCenter)
            SlimSlider._bubble = b
        b = SlimSlider._bubble
        b.setStyleSheet(f"background:{Theme.css(T.accent)};color:white;border-radius:5px;"
                        "padding:2px 7px;font-weight:600")
        b.setText(self.fmt(self.value()))
        b.adjustSize()
        f = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        knob = self.mapToGlobal(QPoint(round(min(max(5.5, self.width() * f), self.width() - 5.5)), 0))
        b.move(knob.x() - b.width() // 2, knob.y() - b.height() - 6)
        b.show(); b.raise_()

    def mousePressEvent(self, e):
        self._set_from(e.position().x())

    def mouseMoveEvent(self, e):
        self._set_from(e.position().x())

    def mouseReleaseEvent(self, e):
        if SlimSlider._bubble is not None:
            SlimSlider._bubble.hide()
        super().mouseReleaseEvent(e)

    def hideEvent(self, e):
        if SlimSlider._bubble is not None:
            SlimSlider._bubble.hide()
        super().hideEvent(e)

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

    def __init__(self, height, on_refresh=None, on_click=None):
        super().__init__()
        self.setFixedHeight(height)
        self.pix = None
        self.url = None
        self.on_refresh = on_refresh
        self.on_click = on_click      # a click on the picture (not the refresh button)
        self.click_ok = lambda: False  # () -> bool: is that click possible right now
        self.hover_live = False
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
        hand = h or (self.on_click is not None and self.click_ok())
        if hand != self.hover_live and not h:
            self.hover_live = hand
            self.update()
        self.setCursor(Qt.CursorShape.PointingHandCursor if hand else Qt.CursorShape.ArrowCursor)
        self.setToolTip("" if h or not hand else "LIVE — экран ТВ вживую, управление мышью")
        if h != self.hover:
            self.hover = h
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
        elif (not self.pressed and self.on_click and self.click_ok() and e.button() == Qt.MouseButton.LeftButton
              and self.rect().contains(e.position().toPoint())):
            self.on_click()
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
        # artwork comes from TV metadata that any sender on the LAN sets; file:// would let
        # it point Windows at an SMB share (NTLM hash leak), so fetch http(s) only
        if not url or QUrl(url).scheme() not in ("http", "https"):
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
        if self.on_click is not None and self.click_ok():
            self._paint_live(p)
        if self.on_refresh:
            self._paint_button(p)

    def _paint_live(self, p):
        """● LIVE in the bottom-left corner: a click on the picture shows the TV's screen."""
        p.setClipping(False)
        f = font(10, QFont.Weight.Bold)
        p.setFont(f)
        from PyQt6.QtGui import QFontMetricsF
        tw = QFontMetricsF(f).horizontalAdvance("LIVE")
        box = QRectF(8, self.height() - 8 - 22, tw + 28, 22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 170 if self.hover_live else 125))
        p.drawRoundedRect(box, 11, 11)
        p.setBrush(QColor("#ff453a"))
        p.drawEllipse(QPointF(box.left() + 11, box.center().y()), 3.6, 3.6)
        p.setPen(QColor("white"))
        p.drawText(QRectF(box.left() + 19, box.top(), tw + 4, box.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "LIVE")


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


def tv_volume_known(s: "State"):
    """The TV's own volume, if the paired TV reports one ({"level", "max", "muted"}).
    Not every TV does (sound through a soundbar / receiver, some makers) — then None."""
    v = s.tv_volume
    return v if (s.tv_paired and v and v.get("max")) else None


class VolumeRow(QWidget):
    """Volume slider. With the TV reporting its volume over the remote session it shows and
    sets the TV's own level on the TV's scale; otherwise the Cast volume (catt)."""
    def __init__(self, state: State):
        super().__init__()
        self.state = state
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 3, 14, 3)
        lay.setSpacing(9)
        self.ic = QPushButton(); self.ic.setFixedSize(18, 18)
        self.ic.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.ic.setStyleSheet("QPushButton{border:none;background:transparent}")
        self.ic.clicked.connect(self._mute)
        self.slider = SlimSlider()
        self.slider.fmt = self._pct_text                 # the bubble while dragging
        self.slider.valueChanged.connect(self._moved)
        self.num = label("50%", 11, T.fg_a(0.75), QFont.Weight.DemiBold)
        self.num.setFont(font(11, QFont.Weight.DemiBold, mono=True))
        self.num.setFixedWidth(40)
        self.num.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.ic); lay.addWidget(self.slider, 1); lay.addWidget(self.num)
        self._updating = False

    def _pct(self, v) -> int:
        """Level → percent of the TV's own scale (some TVs go to 15 or 60, not 100)."""
        top = self.slider.maximum() or 100
        return round(100 * v / top)

    def _pct_text(self, v) -> str:
        return f"{self._pct(v)}%"

    def _moved(self, v):
        if self._updating:
            return
        if tv_volume_known(self.state):
            self.num.setText(self._pct_text(v))
            self.state.set_tv_volume(v)
        else:
            self.state.set_volume(v)

    def _mute(self):
        if tv_volume_known(self.state):
            self.state.toggle_tv_mute()

    def sync(self):
        s = self.state
        tv = tv_volume_known(s)
        if tv:
            top = int(tv["max"])
            v = s._tv_vol_want if s._tv_vol_want is not None else int(tv["level"])
            muted = bool(tv.get("muted")) or v == 0
        else:
            top, v = 100, s.volume
            muted = v == 0
        self._updating = True
        if self.slider.maximum() != top:
            self.slider.setRange(0, top)
        if self.slider.value() != v:
            self.slider.setValue(v)
        self._updating = False
        self.num.setText(self._pct_text(v))
        self.ic.setIcon(glyph("spk_slash" if muted else "spk_wave", 13, T.fg_a(0.55)))
        self.ic.setCursor(Qt.CursorShape.PointingHandCursor if tv else Qt.CursorShape.ArrowCursor)
        self.ic.setToolTip("Звук ТВ: щёлкните, чтобы выключить / включить" if tv else "")
        self.setToolTip("Громкость телевизора" if tv else "Громкость трансляции")


# ---------------------------------------------------------------- the panel (PanelView.swift)

def status_dot_icon(on) -> QIcon:
    """A small round status mark: green = answers, red = doesn't, grey = not known yet."""
    k = 2.0
    pm = QPixmap(int(12 * k), int(12 * k)); pm.setDevicePixelRatio(k)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(T.green if on is True else T.red if on is False else T.fg_a(0.35))
    p.drawEllipse(QRectF(3, 3, 6, 6)); p.end()
    return QIcon(pm)


def fill_device_menu(menu: QMenu, s: "State", parent=None):
    """Device chooser used by the panel footer, the tray menu and the Connect TV window."""
    menu.clear()
    groups = s.grouped_devices()
    for title, ds in groups:
        if len(groups) > 1:                        # a heading only when there's more than one kind
            menu.addSection(tr(title))
        for d in ds:
            on = s.tv_status(d)
            a = menu.addAction(status_dot_icon(on), s.device_label(d))
            a.setCheckable(True)
            a.setChecked(bool(s.device) and d["ip"] == s.device["ip"])
            a.setToolTip({True: "В сети", False: "Не в сети"}.get(on, "Проверяю связь…"))
            a.triggered.connect(lambda _=False, d=d: s.select_device(d))
    if not s.devices:
        a = menu.addAction("ТВ не найдены"); a.setEnabled(False)
    menu.addSeparator()
    def find():                                    # the chooser window: animation + cards
        holder = getattr(s, "_panel_ref", None)
        D.open_picker(sys.modules[__name__], holder) if holder is not None else s.scan()
    menu.addAction("Найти ТВ…", find)
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


# ---------------------------------------------------------------- TV screen + mouse (scrcpy)

def _adb_ready_error(ip: str, what: str) -> str | None:
    """None when ADB to this TV works (one connect is fine: the user asked), else what to do."""
    if not P.adb_available():
        return what + ": ADB не установлен — откройте «Подключение ТВ…» и скачайте platform-tools."
    st = adb_state(ip, force=True)
    if st == "device":
        return None
    return {"unauthorized": what + ": подтвердите на ТВ «Разрешить отладку».",
            "closed": what + " работает через ADB — включите его: «Подключение ТВ…» → ADB."}.get(
        st, what + ": ADB не подключён — откройте «Подключение ТВ…».")


def scrcpy_download() -> str:
    """Install the latest scrcpy for this computer into P.SCRCPY_DIR through the toolkit
    (checked against the release's SHA256SUMS.txt; progress shows in the Tools window)
    and return the scrcpy binary. Call it off the UI thread."""
    try:
        fling_tools.run_now("scrcpy")
    except fling_tools.ToolError as e:
        raise RuntimeError(tr(str(e))) from None
    exe = P.find_scrcpy()
    if not exe:
        raise RuntimeError(tr("в архиве нет программы ") + "scrcpy")
    return exe


# LIVE picture quality: (max size, max fps, video bit rate). "auto" starts as "clear-ish"
# and steps down to "saver" by itself when the network can't keep up.
# (max size, frames/s, bit rate). 60 frames/s where the network allows: the TV's hardware
# encoder holds a frame or two before it sends them, so twice the frames is about half that wait.
QUALITY = {"auto": (1280, 60, "6M"), "clear": (1920, 30, "8M"),
           "smooth": (960, 60, "4M"), "saver": (960, 30, "2M")}
# Ask the TV's encoder for low latency: realtime priority and at most one frame held
# (MediaCodec KEY_PRIORITY / KEY_LATENCY). A TV whose encoder refuses them gets the plain
# start again, and Fling remembers that TV (conf "live_plain").
LOW_LATENCY = "priority:int=0,latency:int=1"


class TvScreen(QObject):
    """The TV's screen in a window (scrcpy), the computer mouse as a real pointer on the TV.

    mode "screen": video + mouse;  mode "mouse": no video, a small window that captures
    the mouse and keyboard for the TV. --mouse=uhid makes Android show its own cursor;
    TVs without UHID fall back to taps (--mouse=sdk)."""
    changed = pyqtSignal()

    def __init__(self, state: "State"):
        super().__init__()
        self.state = state
        self.proc = None
        self.mode = None
        self.busy = False
        self.live_geom = None        # (x, y, w, h) in physical pixels: where the live video goes
        self.live_audio = False      # live picture: TV sound on this PC too (scrcpy audio, Android 11+)
        self.live_mouse = "sdk"      # live picture: sdk = the PC's mouse, exactly where it points (default);
                                     # uhid = the TV's own cursor (relative, drifts from the picture); view
        self.live_kb = "on"          # live picture: keyboard to the TV (on) or kept on the PC (off)
        self.live_quality = "auto"   # live picture: auto / clear / smooth / saver (QUALITY)
        self.output = []             # what the running scrcpy printed so far
        self._quiet = set()          # processes we stopped ourselves: their exit is no error

    def running(self, mode=None) -> bool:
        from PyQt6.QtCore import QProcess
        if mode == "screen" and P.IS_WIN:
            mode = "live"
        on = self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning
        return on and (mode is None or mode == self.mode)

    def toggle(self, mode="screen"):
        """Open the window; the same menu item again closes it. On Windows the TV screen
        opens inside a Fling window (LiveWindow) with the remote beside it."""
        if mode == "screen" and P.IS_WIN:
            open_live(self.state)
            return
        if self.running(mode):
            self.stop()
            return
        self.open(mode)

    def stop(self):
        if self.running():
            self._quiet.add(id(self.proc))
            self.proc.terminate()                # scrcpy cleans up on the TV when it can
            p = self.proc
            QTimer.singleShot(3000, lambda: p.kill() if p.state() != p.ProcessState.NotRunning else None)

    def open(self, mode="screen", on_started=None):
        """on_started(process, window title): for "live", to find and embed scrcpy's window."""
        self._on_started = on_started
        s = self.state
        ip = s.ip
        if not ip:
            s._set(last_error="Телевизор не выбран — нажмите «Выбрать» внизу панели.")
            return
        if self.busy:
            return
        self.busy = True
        what = "Курсор на ТВ" if mode == "mouse" else "Экран ТВ"

        def work():
            err = _adb_ready_error(ip, what)
            if err:
                return {"error": err}
            exe = P.find_scrcpy()
            if not exe:
                if P.IS_LINUX and not P.scrcpy_asset_prefix():
                    return {"error": what + ": установите scrcpy (sudo apt install scrcpy)."}
                self.state.bridge.call.emit(lambda: (notify_send("Скачиваю scrcpy — ход загрузки в окне «Инструменты»…"),
                                                     fling_tools.open_window()))
                exe = scrcpy_download()
            return {"exe": exe}

        def done(r, e):
            self.busy = False
            if e or not r:
                s._set(last_error=f"{what}: {e}")
                return
            if r.get("error"):
                s._set(last_error=r["error"])
                return
            self.stop()
            self._start(r["exe"], ip, mode, self.live_mouse if mode == "live" else "uhid")

        s.bg(work, done)

    def _start(self, exe, ip, mode, mouse):
        from PyQt6.QtCore import QProcess, QProcessEnvironment
        title = tr("Fling — курсор: щёлкните здесь, Alt — вернуть мышь") if mode == "mouse" else tr("Fling — экран ТВ")
        if mode == "live":                       # a unique title: LiveWindow finds the window by it
            title = f"fling-live-{os.getpid()}-{int(time.monotonic() * 1000)}"
        audio = mode == "live" and self.live_audio
        args = ["-s", _adb_serial(ip), f"--window-title={title}"]
        if mouse == "view":                      # just watch: no mouse, no keys to the TV
            args.append("--no-control")
        else:
            args.append(f"--mouse={mouse}")
            if mode == "live" and self.live_kb == "off":
                args.append("--keyboard=disabled")
        if not audio:
            args.append("--no-audio")
        if mode in ("screen", "live"):
            q = QUALITY.get(self.live_quality if mode == "live" else
                            ("saver" if TS.prefers_saver(self.state) else "auto"), QUALITY["auto"])
            # no extra buffering anywhere: --video-buffer=0 shows each frame as soon as it's decoded
            args += [f"--max-size={q[0]}", f"--max-fps={q[1]}", f"--video-bit-rate={q[2]}", "--video-buffer=0"]
            if ip not in self.state.conf.get("live_plain", []):
                args.append(f"--video-codec-options={LOW_LATENCY}")
        if mode == "live":
            args.append("--print-fps")           # LiveWindow reads it: frames shown / skipped
        if mode == "live":                       # born off-screen and frameless, then moved into Fling
            # born frameless right over LiveWindow's video area: resizing a window that SDL
            # created off-screen crashes scrcpy (divide by zero), moving an on-screen one is fine
            x, y, w, h = self.live_geom or (100, 100, 960, 540)
            args += ["--window-borderless", f"--window-x={x}", f"--window-y={y}",
                     f"--window-width={w}", f"--window-height={h}"]
        elif mode == "mouse":
            args += ["--no-video", "--window-width=440", "--window-height=120"]
        p = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        if os.path.isfile(_ADB):
            env.insert("ADB", _ADB)              # one adb server: Fling's, not scrcpy's own copy
        p.setProcessEnvironment(env)
        p.setWorkingDirectory(os.path.dirname(exe))
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        p.setProgram(exe)
        p.setArguments(args)
        out = []
        self.output = out
        pending = []                             # characters scrcpy couldn't type, waiting to go by the remote

        def flush_text():
            if pending:
                s = "".join(pending)
                pending.clear()
                print(f"live: typing {len(s)} char(s) scrcpy can't inject via the TV remote", flush=True)
                self.state.tv_text(s)

        def read():
            chunk = bytes(p.readAllStandardOutput()).decode(errors="replace")
            out.append(chunk)
            if len(out) > 600:                   # --print-fps adds a line a second: keep the start + the recent
                del out[50:350]
            # scrcpy's keyboard injects ASCII only: Russian letters (and the like) come back as
            # "Could not inject char u+0444". The Android TV remote types any text: send them that way.
            if mode == "live":
                for hexcode in re.findall(r"Could not inject char u\+([0-9a-fA-F]{4,6})", chunk):
                    try:
                        pending.append(chr(int(hexcode, 16)))
                    except ValueError:
                        pass
                if pending:
                    QTimer.singleShot(120, flush_text)
        p.readyReadStandardOutput.connect(read)
        started = time.monotonic()

        def finished(code, _status):
            text = "".join(out)
            print(f"scrcpy {mode} ({mouse}) exit {code}:\n{text[-1500:]}", flush=True)
            if self.proc is p:
                self.proc, self.mode = None, None
            self.changed.emit()
            if id(p) in self._quiet or code == 0:
                self._quiet.discard(id(p))
                return
            quick = time.monotonic() - started < 15
            low = any(a.startswith("--video-codec-options=") for a in args)
            tl = text.lower()
            if quick and low and ("mediacodec" in tl or "codecexception" in tl or "illegalargument" in tl
                                  or ("encoder" in tl and "error" in tl)):
                # this TV's encoder won't take the low-latency options: start it plain, and remember
                plain = self.state.conf.setdefault("live_plain", [])
                if ip not in plain:
                    plain.append(ip)
                    save_conf(self.state.conf)
                print("scrcpy: the TV's encoder refused the low-latency options; plain start", flush=True)
                self._start(exe, ip, mode, mouse)
                return
            if quick and mouse == "uhid" and "uhid" in text.lower():
                self._start(exe, ip, mode, "sdk")    # no UHID on this TV: taps instead of a cursor
                return
            err = [l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("ERROR:")]
            self.state._set(last_error=tr("Экран ТВ закрылся: ") + (err[-1] if err else f"scrcpy {code}"))

        p.finished.connect(finished)
        def failed(err):
            print("scrcpy error:", err, flush=True)
            if err == QProcess.ProcessError.FailedToStart:
                if self.proc is p:
                    self.proc, self.mode = None, None
                self.state._set(last_error=tr("Экран ТВ не открылся: ") + p.errorString())
                self.changed.emit()

        p.errorOccurred.connect(failed)
        self.proc, self.mode = p, mode
        print("scrcpy:", exe, *args, flush=True)
        p.start()
        self.changed.emit()
        if getattr(self, "_on_started", None):
            self._on_started(p, title)
        if mode == "live":
            return
        notify_send("Щёлкните в окне — мышь станет курсором на ТВ. Alt — вернуть мышь компьютеру."
                    if mouse == "uhid" else "Щелчки в окне — нажатия на ТВ.")


def live_icon(name: str, size: int = 18, color=None) -> QIcon:
    """Line icons for the LIVE toolbar, all drawn the same way (2× for crisp HiDPI)."""
    import math
    dpr = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1
    pm = QPixmap(int(size * dpr * 2), int(size * dpr * 2))
    pm.setDevicePixelRatio(dpr * 2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = QColor(color or T.fg)
    pen = QPen(col, size * 0.085)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap); pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    s = size
    u = s / 24.0                                     # draw on a 24-unit grid

    def P_(x, y):
        return QPointF(x * u, y * u)

    def path(*pts, close=False, fill=False):
        pa = QPainterPath(P_(*pts[0]))
        for q in pts[1:]:
            pa.lineTo(P_(*q))
        if close:
            pa.closeSubpath()
        if fill:
            p.save(); p.setBrush(col); p.drawPath(pa); p.restore()
        else:
            p.drawPath(pa)

    def speaker():
        path((4, 9.5), (7.5, 9.5), (12, 5.5), (12, 18.5), (7.5, 14.5), (4, 14.5), close=True)

    if name == "back":
        pa = QPainterPath(P_(9, 6)); pa.lineTo(P_(4.5, 10.5)); pa.lineTo(P_(9, 15))
        p.drawPath(pa)
        pa = QPainterPath(P_(4.5, 10.5)); pa.lineTo(P_(14, 10.5))
        pa.cubicTo(P_(18, 10.5), P_(20, 13), P_(20, 15.5)); pa.cubicTo(P_(20, 18), P_(18, 19.5), P_(15, 19.5))
        pa.lineTo(P_(11, 19.5)); p.drawPath(pa)
    elif name == "home":
        p.translate(s * 3 / 24, s * 3 / 24)                  # the 18 px house centred in 24
        p.drawPath(home_path(s * 18 / 24))
    elif name == "playpause":
        path((4, 6), (4, 18), (11, 12), close=True, fill=True)
        p.save(); pb = QPen(col, s * 0.11); pb.setCapStyle(Qt.PenCapStyle.RoundCap); p.setPen(pb)
        p.drawLine(P_(15, 7), P_(15, 17)); p.drawLine(P_(19.5, 7), P_(19.5, 17)); p.restore()
    elif name in ("vol_down", "vol_up", "mute"):
        speaker()
        if name == "vol_down":
            p.drawLine(P_(15.5, 12), P_(20.5, 12))
        elif name == "vol_up":
            p.drawLine(P_(15.5, 12), P_(20.5, 12)); p.drawLine(P_(18, 9.5), P_(18, 14.5))
        else:
            p.drawLine(P_(15.5, 9.5), P_(20.5, 14.5)); p.drawLine(P_(20.5, 9.5), P_(15.5, 14.5))
    elif name == "power":
        p.drawArc(QRectF(P_(5, 5.5), P_(19, 19.5)), 120 * 16, 300 * 16)
        p.drawLine(P_(12, 3.5), P_(12, 11))
    elif name == "cursor":
        path((6, 3.5), (6, 18.5), (10, 14.8), (12.8, 20.5), (15.2, 19.3), (12.5, 13.8), (17.8, 13.5), close=True)
    elif name == "tap":
        p.drawEllipse(P_(12, 12), 3.2 * u, 3.2 * u)
        p.save(); pen2 = QPen(col, s * 0.07); pen2.setCapStyle(Qt.PenCapStyle.RoundCap); p.setPen(pen2)
        p.drawArc(QRectF(P_(4.5, 4.5), P_(19.5, 19.5)), 30 * 16, 120 * 16)
        p.drawArc(QRectF(P_(4.5, 4.5), P_(19.5, 19.5)), 210 * 16, 120 * 16); p.restore()
    elif name == "eye":
        pa = QPainterPath(P_(2.5, 12)); pa.cubicTo(P_(6, 5.5), P_(18, 5.5), P_(21.5, 12))
        pa.cubicTo(P_(18, 18.5), P_(6, 18.5), P_(2.5, 12)); p.drawPath(pa)
        p.drawEllipse(P_(12, 12), 3 * u, 3 * u)
    elif name in ("keyboard", "keyboard_off"):
        p.drawRoundedRect(QRectF(P_(2.5, 6), P_(21.5, 18.5)), 2.2 * u, 2.2 * u)
        p.save(); p.setPen(Qt.PenStyle.NoPen); p.setBrush(col)
        for row, y in ((0, 9.3), (1, 12.3)):
            for i in range(6 - row):
                x = 5.4 + i * 2.65 + row * 1.3
                p.drawRoundedRect(QRectF(P_(x - 0.8, y - 0.8), P_(x + 0.8, y + 0.8)), 0.3 * u, 0.3 * u)
        p.drawRoundedRect(QRectF(P_(7.5, 14.6), P_(16.5, 16.2)), 0.5 * u, 0.5 * u)
        p.restore()
        if name == "keyboard_off":
            p.drawLine(P_(3.5, 3.5), P_(20.5, 20.5))
    elif name in ("sound_pc", "sound_pc_off"):
        p.drawRoundedRect(QRectF(P_(2.5, 4), P_(21.5, 16.5)), 1.8 * u, 1.8 * u)
        p.drawLine(P_(9, 20), P_(15, 20)); p.drawLine(P_(12, 16.5), P_(12, 20))
        path((7.5, 9), (9.5, 9), (12, 7), (12, 13.5), (9.5, 11.5), (7.5, 11.5), close=True)
        if name == "sound_pc":
            p.drawArc(QRectF(P_(12, 7.2), P_(16.5, 13.2)), -60 * 16, 120 * 16)
        else:
            p.drawLine(P_(14, 8.5), P_(17.5, 12)); p.drawLine(P_(17.5, 8.5), P_(14, 12))
    elif name == "remote":
        p.drawEllipse(P_(12, 12), 8.5 * u, 8.5 * u)
        p.drawEllipse(P_(12, 12), 2.8 * u, 2.8 * u)
        for a in (0, 90, 180, 270):
            r = math.radians(a)
            c = P_(12 + 6 * math.cos(r), 12 - 6 * math.sin(r))
            p.drawPoint(c)
    elif name == "restart":
        p.drawArc(QRectF(P_(4.5, 4.5), P_(19.5, 19.5)), 60 * 16, 290 * 16)
        path((16.2, 3.2), (16.5, 7.2), (12.6, 7.6))
    elif name == "full":
        for (x, y, dx, dy) in ((4, 9, 0, -5), (4, 4, 5, 0), (20, 9, 0, -5), (20, 4, -5, 0),
                               (4, 15, 0, 5), (4, 20, 5, 0), (20, 15, 0, 5), (20, 20, -5, 0)):
            p.drawLine(P_(x, y), P_(x + dx, y + dy))
    elif name == "close":
        p.drawLine(P_(6.5, 6.5), P_(17.5, 17.5)); p.drawLine(P_(17.5, 6.5), P_(6.5, 17.5))
    p.end()
    return QIcon(pm)


class SectionSwitch(QWidget):
    """"Пульт | ● LIVE" — the two sections of the Fling window, as one pill."""

    def __init__(self, current: str, on_remote, on_live):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setObjectName("secsw")
        self.setStyleSheet(f"QWidget#secsw{{background:{Theme.css(T.fg_a(0.07))};border-radius:9px}}")
        lay = QHBoxLayout(self); lay.setContentsMargins(2, 2, 2, 2); lay.setSpacing(0)
        for key, text, fn, tip in (("remote", "Пульт", on_remote, "Пульт и что играет"),
                                   ("live", "●  LIVE", on_live, "Экран ТВ вживую, управление мышью")):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setFont(font(10.5, QFont.Weight.DemiBold))
            b.setFixedHeight(22)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            on = key == current
            red = "#ff453a"
            col = (T.fg if on else T.fg_a(0.55)) if key == "remote" else (QColor(red) if on else T.fg_a(0.55))
            b.setStyleSheet(
                f"QToolButton{{border:none;border-radius:7px;padding:0 10px;color:{Theme.css(col)};"
                f"background:{Theme.css(T.bg) if on else 'transparent'}}}"
                f"QToolButton:hover{{color:{Theme.css(T.fg)};background:{Theme.css(T.bg) if on else Theme.css(T.fg_a(0.08))}}}")
            if not on:
                b.clicked.connect(fn)
            lay.addWidget(b)


class Morph(QWidget):
    """The switch between the panel and LIVE: one window that grows (or shrinks) from one
    to the other while their pictures cross-fade — so the two read as one window."""

    def __init__(self, a: QPixmap, b: QPixmap, ra, rb, done, ms=300):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.NoDropShadowWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.a, self.b, self.ra, self.rb, self.done = a, b, ra, rb, done
        self.t = 0.0
        # The window covers both rectangles and never moves: only the painting animates, in
        # fractional pixels. Moving + resizing a layered window every frame rounded its edges
        # differently each time (worst at 125/150 % scaling), so the whole morph shook.
        self.box = ra.united(rb)
        self.setGeometry(self.box)
        from PyQt6.QtCore import QVariantAnimation
        self.anim = QVariantAnimation(self)
        self.anim.setStartValue(0.0); self.anim.setEndValue(1.0); self.anim.setDuration(ms)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._step)
        self.anim.finished.connect(self._end)

    def start(self):
        self.show()
        self.anim.start()

    def _step(self, v):
        self.t = float(v)
        self.update()

    def _cur(self) -> QRectF:
        """The morphing rectangle now, in this window's coordinates (fractional)."""
        lerp = lambda x, y: x + (y - x) * self.t
        ra, rb, o = self.ra, self.rb, self.box.topLeft()
        return QRectF(lerp(ra.x(), rb.x()) - o.x(), lerp(ra.y(), rb.y()) - o.y(),
                      lerp(ra.width(), rb.width()), lerp(ra.height(), rb.height()))

    def _end(self):
        self.done()
        QTimer.singleShot(60, self.close)        # the real window is up: let go a frame later
        QTimer.singleShot(80, self.deleteLater)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Everything lands on whole device pixels: a picture slid by fractions of a pixel is
        # resampled every frame, and its text and edges shimmer — the "shaking" (worst in the
        # small compact panel at 125/150 % scaling). Pictures are drawn 1:1, never scaled.
        dpr = self.devicePixelRatioF() or 1.0
        snap = lambda v: round(v * dpr) / dpr
        c = self._cur()
        cur = QRectF(QPointF(snap(c.left()), snap(c.top())), QPointF(snap(c.right()), snap(c.bottom())))
        r = cur.adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath(); clip.addRoundedRect(r, 10, 10)
        bg = QColor(T.bg); bg.setAlphaF(1.0)
        p.setPen(QPen(T.fg_a(0.16), 1)); p.setBrush(bg)
        p.drawRoundedRect(r, 10, 10)
        p.setClipPath(clip)
        # each picture keeps its own scale, pinned to the corner by the tray (bottom-right)
        for pm, op in ((self.a, 1.0 - self.t), (self.b, self.t)):
            if pm is None or pm.isNull() or op <= 0.01:
                continue
            w = pm.width() / pm.devicePixelRatio(); h = pm.height() / pm.devicePixelRatio()
            p.setOpacity(op)
            p.drawPixmap(QPointF(snap(cur.right() - w), snap(cur.bottom() - h)), pm)
        p.setOpacity(1.0)


def _screen_shot(widget) -> QPixmap:
    """What is on screen where `widget` is — includes scrcpy's video over the LIVE window."""
    g = widget.frameGeometry()
    scr = widget.screen() or QGuiApplication.primaryScreen()
    return scr.grabWindow(0, g.x() - scr.geometry().x(), g.y() - scr.geometry().y(), g.width(), g.height())


class LiveWindow(QWidget):
    """Windows: the TV's live screen next to the tray — video first.

    A slim header (● LIVE · TV · what's on, sizes, close), the video, and one row of icon
    buttons under it: TV keys on the left, how the mouse / keyboard / sound work on the
    right. The d-pad opens from the remote button. A tool window, not a popup: clicks on the
    video must not close it.

    scrcpy draws the video (decoded on the GPU) into its own frameless window, born right
    over the video area and *owned* by this window — SDL crashes when its window is
    reparented, or resized after being created off-screen; it is moved only on later
    moves / resizes, and the owner is released before scrcpy stops."""

    SIZES = {"S": (640, 360), "M": (854, 480), "L": (1280, 720)}
    # "sdk" first and the default: the PC's own pointer over the picture, every click and hover
    # lands exactly where it points. "uhid" makes the TV draw its own cursor from relative moves
    # with Android's acceleration — it can't stay in step with the picture, so it's the option
    # for apps that need a real Android cursor.
    MOUSE = (("sdk", "tap", "Мышь",
              "Курсор компьютера над картинкой: щелчок попадает ровно туда, куда наведён. "
              "Правая кнопка — «Назад», колёсико — прокрутка."),
             ("uhid", "cursor", "Курсор ТВ",
              "ТВ рисует свой курсор (для приложений, которым он нужен). Он движется сам по себе и может "
              "не совпадать с картинкой. Уведите мышь за край картинки (или Alt) — она вернётся."),
             ("view", "eye", "Только смотреть",
              "Только картинка: мышь и клавиатура остаются у компьютера."))

    def __init__(self, state: "State"):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.NoDropShadowWindowHint)
        self.state = state
        self.proc = None
        self.title = None
        self.container = None
        self._tries = 0
        self._drag = None
        self._full = False
        self.translucent = P.translucent_ok(state.conf)
        if self.translucent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(tr("Fling — экран ТВ"))
        self.setWindowIcon(tray_icon())
        root = QVBoxLayout(self); root.setContentsMargins(1, 1, 1, 1); root.setSpacing(0)
        root.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        # ---- header: ● LIVE · TV · what's on        S M L ⛶ ✕   (drag the window by it)
        self.head = QWidget()
        hl = QHBoxLayout(self.head); hl.setContentsMargins(12, 6, 6, 6); hl.setSpacing(6)
        hl.addWidget(SectionSwitch("live", self.back_to_panel, lambda: None))
        hl.addSpacing(4)
        self.h_name = label("", 11, T.fg_a(0.6))
        hl.addWidget(self.h_name)
        self.h_now = label("", 11, T.fg_a(0.45))
        self.h_now.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        hl.addWidget(self.h_now, 1)
        # how the picture is doing: a dot + the network delay; click for the quality menu
        self.health = QToolButton()
        self.health.setCursor(Qt.CursorShape.PointingHandCursor)
        self.health.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.health.setFont(font(10.5))
        self.health.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.health.setStyleSheet("QToolButton{border:none;border-radius:7px;padding:2px 8px;"
                                  f"color:{Theme.css(T.fg_a(0.6))};background:transparent}}"
                                  f"QToolButton:hover{{background:{Theme.css(T.fg_a(0.09))}}}"
                                  "QToolButton::menu-indicator{image:none;width:0}")
        qm = QMenu(self.health)
        qm.aboutToShow.connect(lambda m=qm: self._quality_menu(m))
        self.health.setMenu(qm)
        hl.addWidget(self.health)
        self._pings, self._fps, self._bad = [], [], 0
        self._health_details = ""
        self._fps_seen = 0
        self.health_timer = QTimer(self, interval=2000)
        self.health_timer.timeout.connect(self._check_health)
        self.size_btns = {}
        for key in self.SIZES:
            b = self._seg(key, lambda _=False, k=key: self.set_size(k), text=True)
            b.setToolTip({"S": "Маленькое окно", "M": "Среднее окно", "L": "Большое окно"}[key])
            hl.addWidget(b)
            self.size_btns[key] = b
        self.full_btn = self._seg("full", lambda: self.set_size("F"))
        self.full_btn.setToolTip("Во весь экран")
        hl.addWidget(self.full_btn)
        hl.addSpacing(4)
        x = self._tool("close", "Закрыть  (Esc)", self.close)
        hl.addWidget(x)
        root.addWidget(self.head)

        # ---- the video
        self.host = QWidget()
        self.host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.host.setStyleSheet("background:black")
        self.host_l = QVBoxLayout(self.host); self.host_l.setContentsMargins(0, 0, 0, 0)
        self.msg = label("", 12.5, QColor(255, 255, 255, 170), wrap=True)
        self.msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.host_l.addWidget(self.msg)
        root.addWidget(padded(self.host, 8, 0, 8, 0))

        # ---- the toolbar: TV keys | how input works
        bar = QWidget()
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 6, 8, 2); bl.setSpacing(2)
        k = lambda n: (lambda: state.tv_key(KEY[n]))
        for icon, tip, fn in (("back", "Назад", k("back")), ("home", "Домой", k("home")),
                              ("playpause", "Пауза / Играть", k("play_pause"))):
            bl.addWidget(self._tool(icon, tip, fn))
        bl.addWidget(self._sep())
        for icon, tip, fn in (("vol_down", "Тише", k("vol_down")), ("mute", "Без звука", k("mute")),
                              ("vol_up", "Громче", k("vol_up"))):
            bl.addWidget(self._tool(icon, tip, fn, repeat=icon != "mute"))
        bl.addWidget(self._sep())
        bl.addWidget(self._tool("power", "Питание ТВ", k("power")))
        self.remote_btn = self._tool("remote", "Пульт: стрелки, OK, громкость", self._show_remote)
        bl.addWidget(self.remote_btn)
        bl.addStretch(1)
        self.mouse_btns = {}
        for key, icon, title, tip in self.MOUSE:
            b = self._seg(icon, lambda _=False, k=key: self._set_input("live_mouse", k))
            b.setToolTip(title + " — " + tip)
            bl.addWidget(b)
            self.mouse_btns[key] = b
        bl.addWidget(self._sep())
        self.kb_btn = self._seg("keyboard", lambda: self._set_input(
            "live_kb", "off" if self.state.conf.get("live_kb", "on") == "on" else "on"))
        bl.addWidget(self.kb_btn)
        self.audio_btn = self._seg("sound_pc", lambda: self._toggle_audio(not self.state.conf.get("live_audio")))
        self.audio_btn.setToolTip("Звук на ПК — звук ТВ пойдёт на этот компьютер; на самом ТВ он может "
                                  "пропасть, пока открыта картинка. Переключение перезапускает картинку.")
        bl.addWidget(self.audio_btn)
        bl.addWidget(self._sep())
        bl.addWidget(self._tool("restart", "Перезапустить картинку", self.restart))
        root.addWidget(bar)
        # one quiet line: what a click on the picture does right now / what's going on
        self.hint = label("", 10.5, T.fg_a(0.5))
        self.hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        root.addWidget(padded(self.hint, 14, 0, 14, 8))
        self.status_text = ""

        self.remote = None                        # the d-pad flyout, made on first use
        self._apply_size()
        self._input_labels()

        sc = QShortcut(QKeySequence("Escape"), self)
        sc.activated.connect(self.close)
        state.screen.changed.connect(self._sync)
        state.changed.connect(self._sync_now)
        self._sync_now()
        install_file_drop(self, state)            # files dropped on the window go to the TV

    # --- small widgets
    def _tool(self, icon, tip, on, repeat=False):
        b = QToolButton()
        b.setIcon(live_icon(icon, 18, T.fg_a(0.85)))
        b.setIconSize(QSize(18, 18))
        b.setFixedSize(32, 30)
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setAutoRepeat(repeat)
        if repeat:
            b.setAutoRepeatDelay(350); b.setAutoRepeatInterval(140)
        b.setStyleSheet("QToolButton{border:none;border-radius:7px;background:transparent}"
                        f"QToolButton:hover{{background:{Theme.css(T.fg_a(0.09))}}}"
                        f"QToolButton:pressed{{background:{Theme.css(T.acc_a(0.25))}}}")
        b.clicked.connect(on)
        return b

    def _seg(self, icon, on, text=False):
        """A toggle in a group: lit with the accent while it is the current choice."""
        b = QToolButton()
        b.setCheckable(True)
        if text:
            b.setText(icon); b.setFont(font(10.5, QFont.Weight.DemiBold)); b.setFixedSize(26, 24)
        else:
            b.setIcon(live_icon(icon, 18, T.fg_a(0.85))); b.setIconSize(QSize(18, 18)); b.setFixedSize(32, 30)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setStyleSheet(f"QToolButton{{border:none;border-radius:7px;background:transparent;color:{Theme.css(T.fg_a(0.6))}}}"
                        f"QToolButton:hover{{background:{Theme.css(T.fg_a(0.09))}}}"
                        f"QToolButton:checked{{background:{Theme.css(T.acc_a(0.24))};color:{Theme.css(T.accent)}}}")
        b.clicked.connect(on)
        return b

    def _sep(self):
        s = QFrame(); s.setFixedSize(9, 18)
        s.setStyleSheet(f"border:none;border-left:1px solid {Theme.css(T.fg_a(0.14))};margin-left:4px")
        return s

    # --- the d-pad flyout
    def _show_remote(self):
        if self.remote is None:
            f = QWidget(self, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint |
                        Qt.WindowType.NoDropShadowWindowHint)
            f.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            f.setStyleSheet(f"QWidget#fly{{background:{Theme.css(T.bg)};border:1px solid "
                            f"{Theme.css(T.fg_a(0.16))};border-radius:10px}}")
            f.setObjectName("fly")
            fl = QVBoxLayout(f); fl.setContentsMargins(6, 6, 6, 8); fl.setSpacing(4)
            k = lambda n: (lambda: self.state.tv_key(KEY[n]))
            pad, _ = build_dpad(k)
            fl.addWidget(pad)
            self.r_vol = VolumeRow(self.state)
            fl.addWidget(self.r_vol)
            self.remote = f
        self.r_vol.setVisible(bool(tv_volume_known(self.state)))
        if self.r_vol.isVisible():
            self.r_vol.sync()
        f = self.remote
        f.adjustSize()
        b = self.remote_btn
        pt = b.mapToGlobal(QPoint(b.width() // 2 - f.width() // 2, -f.height() - 6))
        f.move(pt)
        f.show()

    # --- input choices (remembered; scrcpy takes them only at start, so switching restarts it)
    def _input_labels(self):
        c = self.state.conf
        mouse = c.get("live_mouse", "sdk")
        mouse = mouse if mouse in self.mouse_btns else "sdk"
        for key, b in self.mouse_btns.items():
            b.setChecked(key == mouse)
        kb_on = c.get("live_kb", "on") != "off"
        self.kb_btn.setChecked(kb_on and mouse != "view")
        self.kb_btn.setEnabled(mouse != "view")
        self.kb_btn.setIcon(live_icon("keyboard" if kb_on else "keyboard_off", 18, T.fg_a(0.85)))
        self.kb_btn.setToolTip(tr("Клавиатура: на ТВ — нажмите, чтобы оставить её у компьютера")
                               if kb_on else tr("Клавиатура: у компьютера — нажмите, чтобы передавать на ТВ"))
        audio = bool(c.get("live_audio"))
        self.audio_btn.setChecked(audio)
        self.audio_btn.setIcon(live_icon("sound_pc" if audio else "sound_pc_off", 18, T.fg_a(0.85)))
        tip = next(t for k, _, _, t in self.MOUSE if k == mouse)
        if mouse != "view":
            tip += " " + ("Клавиатура в окне печатает на ТВ." if kb_on else "Клавиатура не передаётся на ТВ.")
        self.mode_text = tr(tip)
        self._hint()

    def _hint(self):
        self.hint.setText(self.status_text or self.mode_text)

    def _set_input(self, name, value):
        self._input_labels()                      # undo the button's own toggle until saved
        if self.state.conf.get(name, {"live_mouse": "sdk", "live_kb": "on"}.get(name)) == value:
            return
        self.state.conf[name] = value
        save_conf(self.state.conf)
        self._input_labels()
        if self.isVisible():
            self.restart()

    # --- picture quality and how it's doing
    QUALITY_NAMES = (("auto", "Качество: авто", "1280 px, 60 кадров/с — само снижается, если сеть не успевает"),
                     ("clear", "Чётко", "1920 px, 30 кадров/с — чётче, но задержка больше"),
                     ("smooth", "Быстро", "960 px, 60 кадров/с — самая маленькая задержка, для игр"),
                     ("saver", "Экономно", "960 px, 2 Мбит/с — когда сеть медленная"))

    def _quality(self) -> str:
        q = self.state.conf.get("live_quality", "auto")
        return q if q in QUALITY else "auto"

    def _quality_menu(self, m):
        m.clear()
        cur = self._quality()
        for key, title, tip in self.QUALITY_NAMES:
            a = m.addAction(title, lambda k=key: self._set_quality(k))
            a.setCheckable(True); a.setChecked(key == cur); a.setToolTip(tr(tip))
        m.setToolTipsVisible(True)
        m.addSeparator()
        m.addAction(self._health_details or tr("Проверяю связь с ТВ…")).setEnabled(False)

    def _set_quality(self, key, note=""):
        self.state.conf["live_quality"] = key
        save_conf(self.state.conf)
        self._bad = 0
        if note:
            self._status(note)
        if self.isVisible():
            self.restart()

    def _check_health(self):
        """Every 2 s while the picture runs: the network delay to the TV (one ping, no
        process) and how many frames scrcpy had to skip to stay live."""
        sc = self.state.screen
        if not self.isVisible() or not sc.running("live") or not self.container:
            return
        text = "".join(sc.output)
        lines = re.findall(r"(\d+) fps(?: \(\+(\d+) frames? skipped\))?", text)
        new = lines[self._fps_seen:]
        self._fps_seen = len(lines)
        for f, skipped in new:
            self._fps = (self._fps + [(int(f), int(skipped or 0))])[-5:]
        ip = self.state.ip

        def work():
            return P.ping_ms(ip) if ip else None

        def done(rtt, e):
            self._pings = (self._pings + [rtt])[-5:]
            self._show_health()
        self.state.bg(work, done)

    def _show_health(self):
        _ms, _s = ("ms", "s") if LANG == "en" else ("мс", "с")
        pings = [p for p in self._pings if p is not None]
        lost = len(self._pings) - len(pings)
        rtt = sorted(pings)[len(pings) // 2] if pings else None          # median: one slow ping isn't a problem
        shown = sum(f for f, _ in self._fps); skipped = sum(k for _, k in self._fps)
        skip_part = skipped / (shown + skipped) if shown + skipped >= 10 else 0.0
        fps = self._fps[-1][0] if self._fps else None
        level, why = 0, []
        if rtt is None and self._pings:
            level, why = 2, [tr("ТВ не отвечает по сети")]
        else:
            if lost >= 2:
                level = max(level, 2); why.append(tr("сеть теряет пакеты"))
            if rtt is not None and rtt > 120:
                level = max(level, 2); why.append(tr("большая задержка сети") + f" ({rtt:.0f} {_ms})")
            elif rtt is not None and rtt > 50:
                level = max(level, 1)
            if skip_part > 0.25:
                level = max(level, 2); why.append(tr("кадры не успевают") + f" ({skip_part:.0%})")
            elif skip_part > 0.08:
                level = max(level, 1)
        col = {0: "#30d158", 1: "#ffd60a", 2: "#ff453a"}[level]
        # only the network: the picture also waits for the TV's encoder (it doesn't report it)
        label_ = (tr("сеть") + f" {rtt:.0f} {_ms}" if rtt is not None else "—")
        qname = next(t for k, t, _ in self.QUALITY_NAMES if k == self._quality())
        self.health.setText(f"{label_}  ·  {tr(qname)}")
        details = [tr("Сеть до ТВ") + ": " + (f"{rtt:.0f} {_ms}" if rtt is not None else tr("нет ответа"))
                   + (f", {tr('потеряно')} {lost}/{len(self._pings)}" if lost else "")]
        if fps is not None:
            details.append(tr("Кадров") + f": {fps}/{_s}" + (f", {tr('пропущено')} {skip_part:.0%}" if skipped else "")
                           + ("" if fps else " " + tr("(экран ТВ не меняется — это нормально)")))
        self._health_details = " · ".join(details)
        self.health.setToolTip(tr("Как идёт картинка") + "\n" + self._health_details + "\n" +
                               tr("Это только задержка сети. Картинка ещё ждёт, пока телевизор сожмёт кадр — "
                                  "обычно 50–150 мс, сколько именно, ТВ не сообщает. Меньше всего — в «Быстро».")
                               + "\n" + tr("Нажмите — выбрать качество."))
        self._paint_health_dot(col)
        self._bad = self._bad + 1 if level == 2 else 0
        if level == 2 and self._bad >= 2:
            msg = tr("Картинка может запаздывать: ") + ", ".join(why) + "."
            if self._quality() == "auto" and self.state.screen.live_quality != "saver":
                self.state.conf["live_quality_auto_step"] = True
                self._status(msg + " " + tr("Включаю «Экономно»…"))
                self._bad = 0
                QTimer.singleShot(1500, lambda: self._restart_saver())
            else:
                saver = self.state.screen.live_quality == "saver"     # what runs now, auto included
                self._status(msg + " " + (tr("Это уже самое лёгкое качество — проверьте Wi-Fi.") if saver
                                          else tr("Попробуйте качество «Экономно».")))
        elif level < 2 and self.status_text.startswith(tr("Картинка может запаздывать: ")):
            self._status("")

    def _restart_saver(self):
        """Auto quality: the network can't keep up, so the next start uses "saver"."""
        self._auto_saver = True
        if self.isVisible():
            self.restart()

    def _paint_health_dot(self, col):
        b = self.health
        b.setStyleSheet("QToolButton{border:none;border-radius:7px;padding:2px 8px;"
                        f"color:{Theme.css(T.fg_a(0.65))};background:transparent}}"
                        f"QToolButton:hover{{background:{Theme.css(T.fg_a(0.09))}}}"
                        "QToolButton::menu-indicator{image:none;width:0}")
        # the dot in its colour, the rest quiet: rich text isn't possible on a QToolButton, so an icon
        pm = QPixmap(20, 20); pm.setDevicePixelRatio(2); pm.fill(Qt.GlobalColor.transparent)
        pp = QPainter(pm); pp.setRenderHint(QPainter.RenderHint.Antialiasing)
        pp.setPen(Qt.PenStyle.NoPen); pp.setBrush(QColor(col)); pp.drawEllipse(QRectF(2, 2, 6, 6)); pp.end()
        b.setIcon(QIcon(pm)); b.setIconSize(QSize(10, 10))
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

    def _toggle_audio(self, on):
        self.state.conf["live_audio"] = bool(on)
        save_conf(self.state.conf)
        self._input_labels()
        if self.isVisible():
            self.restart()

    def _check_audio(self):
        """scrcpy keeps the video when the TV can't send sound: say so, quietly."""
        if not self.state.screen.live_audio or not self.state.screen.running("live"):
            return
        text = "".join(self.state.screen.output).lower()
        bad = [l for l in text.splitlines() if "audio" in l and
               any(w in l for w in ("disabled", "fail", "error", "not supported", "could not", "cannot"))
               and "retrying" not in l]
        if bad:
            self.status_text = tr("Звук с этого ТВ не передаётся")
            self._hint()

    # --- look
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(T.fg_a(0.16), 1))
        bg = QColor(T.bg); bg.setAlphaF(1.0)
        if not self.translucent:
            p.fillRect(self.rect(), bg)
        p.setBrush(bg)
        p.drawRoundedRect(r, 10, 10)

    def _size(self) -> str:
        s = self.state.conf.get("live_size")
        return s if s in self.SIZES else "M"

    def _video_size(self):
        if self._full:                            # the largest 16:9 that fits the screen
            scr = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
            w = scr.width() - 2 - 16
            h = scr.height() - 2 - 44 - 40 - 26
            w = min(w, int(h * 16 / 9)) // 2 * 2
            return w, w * 9 // 16
        return self.SIZES[self._size()]

    def _apply_size(self):
        w, h = self._video_size()
        self.host.setFixedSize(w, h)
        for key, b in self.size_btns.items():
            b.setChecked(not self._full and key == self._size())
        self.full_btn.setChecked(self._full)

    def set_size(self, key):
        if key == "F":
            self._full = not self._full
        else:
            self._full = False
            self.state.conf["live_size"] = key
            save_conf(self.state.conf)
        self._apply_size()
        self.layout().activate()                  # the new size now, so place() sees it
        self.adjustSize()
        self.place()
        QTimer.singleShot(0, self.place)
        QTimer.singleShot(30, self._place)

    def place(self):
        """Next to the tray, like the panel; centred when it fills the screen."""
        self.adjustSize()
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        full, av = scr.geometry(), scr.availableGeometry()
        if self._full:
            self.move(av.left() + (av.width() - self.width()) // 2, av.top() + (av.height() - self.height()) // 2)
            return
        m = 8
        x = av.right() - self.width() - m
        y = av.top() + m if av.top() > full.top() else av.bottom() - self.height() - m
        if av.left() > full.left():
            x = av.left() + m
        self.move(max(av.left(), x), max(av.top(), y))

    def _sync_now(self):
        s = self.state
        dev = s.device["name"] if s.device else ""
        self.h_name.setText("· " + dev if dev else "")
        st = s.status if s.panel == "casting" else {}
        tvm = getattr(s, "tvm", {}) or {}
        title = st.get("title") or tvm.get("title") or ""
        sub = st.get("subtitle") or tvm.get("artist") or app_name(tvm.get("package")) or ""
        now = " — ".join(x for x in (title, sub) if x)
        self.h_now.setText(("· " + now) if now else "")
        self.h_now.setToolTip(now)
        if self.remote is not None and self.remote.isVisible() and self.r_vol.isVisible():
            self.r_vol.sync()

    def show_from(self, panel):
        """Grow out of the panel: the panel's picture turns into this window's."""
        a, ra = panel.grab(), panel.frameGeometry()
        self._full = False
        self._apply_size()
        self.layout().activate()
        self.adjustSize()
        self.place()
        rb = QRect(self.pos(), self.size())
        self.msg.setText(tr("Подключаюсь к экрану ТВ…"))
        self.msg.show()
        b = self.grab()
        panel.hide()

        def done():
            self.move(rb.topLeft())
            self.show(); self.raise_(); self.activateWindow()
            P.force_foreground(int(self.winId()))
        self._morph = Morph(a, b, ra, rb, done)
        self._morph.start()

    def back_to_panel(self):
        """Shrink back into the panel (the video stops)."""
        panel = getattr(self.state, "_panel_ref", None)
        a, ra = _screen_shot(self), self.frameGeometry()
        if self.remote is not None:
            self.remote.hide()
        self.close()
        if panel is None:
            return
        panel.sync()
        panel.place()
        rb = QRect(panel.pos(), panel.size())
        b = panel.grab()
        self._morph = Morph(a, b, ra, rb, panel.show_panel, ms=260)
        self._morph.start()

    # --- dragging by the header
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.head.geometry().contains(e.position().toPoint()):
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        if self.head.geometry().contains(e.position().toPoint()):
            self.set_size("F")                    # double-click the header: full screen and back

    # --- scrcpy lifecycle
    def showEvent(self, e):
        super().showEvent(e)
        if self.container is None and not self.state.screen.running("live") and not self.state.screen.busy:
            QTimer.singleShot(50, self.start)     # after the layout settles: scrcpy is born in place

    def _status(self, text):
        self.status_text = text
        self._hint()

    def start(self):
        self._drop_container()
        self.msg.setText(tr("Подключаюсь к экрану ТВ…"))
        self.msg.show()
        self._status(tr("Подключаюсь к экрану ТВ…"))
        sc = self.state.screen
        c = self.state.conf
        if c.get("live_mouse_v") != 2:            # 0.5.4: "uhid" was the default and drifted: move to "sdk" once
            if c.get("live_mouse", "uhid") == "uhid":
                c["live_mouse"] = "sdk"
            c["live_mouse_v"] = 2
            save_conf(c)
        sc.live_geom = self._geom()
        sc.live_audio = bool(self.state.conf.get("live_audio"))
        sc.live_mouse = self.state.conf.get("live_mouse", "sdk")
        sc.live_kb = self.state.conf.get("live_kb", "on")
        q = self._quality()
        sc.live_quality = ("saver" if q == "auto" and (getattr(self, "_auto_saver", False)
                                                       or TS.prefers_saver(self.state)) else q)   # tailnet: start light
        self._pings, self._fps, self._fps_seen, self._bad = [], [], 0, 0
        self._health_details = ""
        self.health.setIcon(QIcon()); self.health.setText(tr("Качество: авто") if q == "auto" else
                                                          tr(next(t for k, t, _ in self.QUALITY_NAMES if k == q)))
        sc.open("live", on_started=self._started)

    def restart(self):
        self._drop_container()
        self.state.screen.stop()
        QTimer.singleShot(700, self.start)

    def _started(self, proc, title):
        self.proc, self.title = proc, title
        self._tries = 0
        self._find()

    def _find(self):
        """Wait for scrcpy's window (it opens once the video starts), then adopt it."""
        if self.proc is None or self.proc.state() == self.proc.ProcessState.NotRunning:
            return
        import ctypes
        hwnd = ctypes.windll.user32.FindWindowW(None, self.title)
        if not hwnd:
            self._tries += 1
            if self._tries < 100:                 # ~20 s
                QTimer.singleShot(200, self._find)
            else:
                self.msg.setText(tr("Экран ТВ не появился. Нажмите «Перезапустить картинку»."))
            return
        GWLP_HWNDPARENT = -8                      # owner: above this window, minimised with it
        ctypes.windll.user32.SetWindowLongPtrW(ctypes.c_void_p(hwnd), GWLP_HWNDPARENT,
                                               ctypes.c_void_p(int(self.winId())))
        self.container = int(hwnd)
        self.msg.hide()                           # it was born in place: no move needed now
        QTimer.singleShot(100, self._focus_picture)   # the keyboard goes to the TV from the start
        saver_auto = self.state.screen.live_quality == "saver" and self._quality() == "auto"
        self._status("" if not saver_auto else
                     tr("Через Tailscale — качество «Экономно». Другое можно выбрать справа вверху.")
                     if TS.prefers_saver(self.state) and not getattr(self, "_auto_saver", False) else
                     tr("Сеть не успевала — включил «Экономно». Качество можно выбрать справа вверху."))
        self.health_timer.start()
        QTimer.singleShot(300, self._check_health)
        if self.state.screen.live_mouse == "uhid":   # the mouse comes back by itself at the picture's edge
            self._edge = P.MouseEdgeRelease(self.container)
            self._edge.rect = self._rect()
            self._edge.start()
        for ms in (1500, 5000):
            QTimer.singleShot(ms, self._check_audio)

    def _geom(self):
        """The video area in physical screen pixels."""
        dpr = self.host.devicePixelRatioF()
        tl = self.host.mapToGlobal(QPoint(0, 0))
        scr = self.host.screen().geometry()
        # Qt's global coordinates are logical; Windows wants physical pixels of that screen
        x = int(scr.x() + (tl.x() - scr.x()) * dpr)
        y = int(scr.y() + (tl.y() - scr.y()) * dpr)
        return x, y, max(int(self.host.width() * dpr), 160), max(int(self.host.height() * dpr), 90)

    def _rect(self):
        x, y, w, h = self._geom()
        return x, y, x + w, y + h

    def _place(self):
        """Keep scrcpy's window exactly over the video area."""
        if not self.container:
            return
        import ctypes
        x, y, w, h = self._geom()
        if getattr(self, "_edge", None):
            self._edge.rect = (x, y, x + w, y + h)
        SWP_NOACTIVATE, SWP_SHOWWINDOW, HWND_TOP = 0x10, 0x40, 0
        ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(self.container), HWND_TOP, x, y, w, h,
                                          SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._place)

    def moveEvent(self, e):
        super().moveEvent(e)
        self._place()

    def _keys_to_tv(self) -> bool:
        c = self.state.conf
        return c.get("live_kb", "on") != "off" and c.get("live_mouse", "sdk") != "view"

    def _focus_picture(self):
        """Typing in LIVE goes to the TV: the picture's window (scrcpy) gets the keyboard, not
        this one — before, keys reached the TV only after a click on the picture."""
        if self.container and self.isVisible() and self._keys_to_tv() and self.isActiveWindow():
            P.force_foreground(self.container)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            QTimer.singleShot(60, self._focus_picture)

    def _drop_container(self):
        """Let go of scrcpy's window first: SDL crashes when its owner disappears under it."""
        if getattr(self, "_edge", None):
            self._edge.stop()
            self._edge = None
        if self.container:
            import ctypes
            ctypes.windll.user32.SetWindowLongPtrW(ctypes.c_void_p(self.container), -8, ctypes.c_void_p(0))
        self.container = None

    def _sync(self):
        sc = self.state.screen
        if self.isVisible() and not sc.running("live") and not sc.busy and self.proc is not None:
            self.proc = None
            self._drop_container()
            err = self.state.last_error or ""
            self.msg.setText(tr("Экран ТВ закрыт.") + ("\n" + tr(err) if err else "") +
                             "\n" + tr("Нажмите «Перезапустить картинку»."))
            self.msg.show()
            self._status(tr("Экран ТВ закрыт."))

    def closeEvent(self, e):
        self.proc = None
        self.health_timer.stop()
        self._auto_saver = False                  # next time auto starts from the top again
        if self.remote is not None:
            self.remote.hide()
        self._drop_container()
        self.state.screen.stop()
        self._full = False
        super().closeEvent(e)


def open_live(state: "State"):
    """The Live window by the tray; the panel steps aside for it."""
    panel = getattr(state, "_panel_ref", None)
    if panel is not None and panel.isVisible():
        panel.hide()
    w = getattr(state, "_live", None)
    if w is None:
        w = state._live = LiveWindow(state)
    if not w.isVisible():
        w.place()
    w.show(); w.raise_(); w.activateWindow()
    P.force_foreground(int(w.winId()))          # a tray app's window would open behind others


# ---------------------------------------------------------------- ADB console

# adb subcommands typed as-is; anything else runs as `adb shell <text>`
_ADB_SUBCMDS = {"shell", "exec-out", "logcat", "install", "install-multiple", "uninstall", "pull", "push",
                "reboot", "get-state", "get-serialno", "get-devpath", "bugreport", "forward", "reverse",
                "root", "unroot", "remount", "tcpip", "usb", "sideload", "jdwp", "features", "version",
                "disconnect", "wait-for-device", "emu", "devices"}
# typed adb options that would aim the console at another device / server
_ADB_FORBIDDEN = {"-s", "-t", "-H", "-P", "-L", "-d", "-e", "connect", "pair", "kill-server", "start-server",
                  "-a", "nodaemon", "server"}
# asks first: may lose data, reboot the TV or cut the ADB link
_ADB_RISKY_SUB = {"reboot", "uninstall", "sideload", "tcpip", "usb", "root", "unroot", "remount", "disconnect"}
_ADB_RISKY = re.compile(        # inside `adb shell …`
    r"(^|[;&|\s/'\"(`$])(reboot|wipe|recovery|rm|mv|dd|setprop|svc|"
    r"pm\s+(clear|uninstall|disable|disable-user|hide|suspend|reset-permissions)|"
    r"settings\s+(put|delete|reset)|cmd\s+package\s+(uninstall|clear)|am\s+force-stop)(['\")`]|\s|$)", re.I)

ADB_QUICK = (
    ("Модель и Android", "getprop ro.product.manufacturer; getprop ro.product.model; "
                         "getprop ro.build.version.release; getprop ro.build.version.sdk"),
    ("Что играет", "dumpsys media_session | grep -E 'package=|active=|state=PlaybackState|description='"),
    ("Активное приложение", "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'"),
    ("Установленные приложения", "pm list packages -3"),
    ("Разрешение экрана", "wm size; wm density"),
    ("Свободное место", "df -h /data"),
    ("Память", "head -3 /proc/meminfo"),
    ("Сеть", "ip -4 addr show"),
    ("Логи: последние 300 строк", "logcat -d -t 300"),
    ("Логи вживую: только ошибки", "logcat *:E"),
)


def adb_console_args(text: str) -> tuple[list[str] | None, str | None]:
    """What the user typed → (adb arguments after `-s TV`, or None and why not)."""
    import shlex
    t = text.strip()
    if t.lower().startswith("adb "):
        t = t[4:].strip()
    if not t:
        return None, None
    try:
        words = shlex.split(t, posix=True)
    except ValueError as ex:
        return None, f"кавычки не закрыты ({ex})"
    first = words[0]
    if first in _ADB_FORBIDDEN or first.startswith("-"):
        return None, ("консоль всегда работает с выбранным ТВ: -s, connect, pair, kill-server "
                      "и другие ключи adb здесь не нужны — подключение настраивается в «Подключение ТВ…».")
    if first in ("shell", "exec-out"):
        rest = t.split(None, 1)[1] if len(words) > 1 else ""
        if not rest:
            return None, "напишите команду после shell — интерактивная оболочка здесь не открывается."
        return [first, rest], None                # the TV's shell parses the rest itself
    if first in _ADB_SUBCMDS:
        return words, None
    return ["shell", t], None


class AdbConsole(QWidget):
    """Run adb commands against the selected TV and watch the output live."""
    MAX_HISTORY = 50

    def __init__(self, state: "State"):
        super().__init__(None, Qt.WindowType.Window)
        from PyQt6.QtWidgets import QPlainTextEdit
        self.state = state
        self.proc = None
        self.hist = list(state.conf.get("adb_history") or [])[-self.MAX_HISTORY:]
        self.hpos = len(self.hist)
        self.setWindowTitle(tr("Fling — ADB-консоль"))
        install_file_drop(self, state)
        self.resize(760, 520)
        pal = self.palette(); pal.setColor(self.backgroundRole(), T.bg); self.setPalette(pal)
        self.setAutoFillBackground(True)
        v = QVBoxLayout(self); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(8)
        self.head = label("", 11, T.fg_a(0.6), wrap=True)
        v.addWidget(self.head)
        row = QHBoxLayout(); row.setSpacing(6)
        self.cmd = field("getprop ro.product.model   ·   logcat -d -t 100   ·   pm list packages -3", mono=True)
        self.cmd.returnPressed.connect(self.run)
        self.cmd.installEventFilter(self)
        row.addWidget(self.cmd, 1)
        self.go = small_button("Выполнить", self.run)
        row.addWidget(self.go)
        self.stop_btn = small_button("Стоп", self.stop)
        self.stop_btn.setEnabled(False)
        row.addWidget(self.stop_btn)
        v.addLayout(row)
        row2 = QHBoxLayout(); row2.setSpacing(6)
        quick = small_button("Быстрые команды ▾", lambda: None)
        qm = QMenu(quick)
        for title, c in ADB_QUICK:
            qm.addAction(title, lambda c=c: (self.cmd.setText(c), self.run()))
        quick.setMenu(qm)
        row2.addWidget(quick)
        row2.addStretch(1)
        row2.addWidget(small_button("Скопировать вывод", lambda: QApplication.clipboard().setText(self.out.toPlainText())))
        row2.addWidget(small_button("Очистить", lambda: self.out.clear()))
        v.addLayout(row2)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumBlockCount(20000)
        self.out.setFont(font(12, mono=True))
        self.out.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.out.setStyleSheet(f"QPlainTextEdit{{background:{Theme.css(T.fg_a(0.05))};border:none;"
                               "border-radius:6px;padding:6px}")
        v.addWidget(self.out, 1)
        self._head()

    def _head(self):
        ip = self.state.ip
        self.head.setText((tr("ТВ: ") + _adb_serial(ip) + "   ·   " if ip else tr("ТВ не выбран   ·   ")) +
                          tr("Команда выполняется в оболочке ТВ (adb shell). adb-команды вроде logcat, "
                             "install, pull пишите как есть. ↑/↓ — история."))

    def showEvent(self, e):
        self._head()
        self.cmd.setFocus()
        super().showEvent(e)

    def eventFilter(self, obj, e):
        if obj is self.cmd and e.type() == QEvent.Type.KeyPress and self.hist:
            if e.key() == Qt.Key.Key_Up:
                self.hpos = max(0, self.hpos - 1)
                self.cmd.setText(self.hist[self.hpos]); return True
            if e.key() == Qt.Key.Key_Down:
                self.hpos = min(len(self.hist), self.hpos + 1)
                self.cmd.setText(self.hist[self.hpos] if self.hpos < len(self.hist) else ""); return True
        return super().eventFilter(obj, e)

    def _print(self, text):
        from PyQt6.QtGui import QTextCursor
        c = self.out.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        c.insertText(text)
        self.out.setTextCursor(c)
        self.out.ensureCursorVisible()

    def running(self) -> bool:
        from PyQt6.QtCore import QProcess
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    def run(self):
        from PyQt6.QtWidgets import QMessageBox
        text = self.cmd.text().strip()
        if not text or self.running():
            return
        ip = self.state.ip
        if not ip:
            self._print(tr("ТВ не выбран.") + "\n"); return
        args, why = adb_console_args(text)
        if not args:
            if why:
                self._print("✗ " + tr(why) + "\n")
            return
        if args[0] in _ADB_RISKY_SUB or (args[0] in ("shell", "exec-out") and _ADB_RISKY.search(args[1])):
            ok = QMessageBox.question(self, "Fling", tr("Эта команда может удалить данные, перезагрузить ТВ "
                                                        "или разорвать связь ADB. Выполнить?") + "\n\n" + text)
            if ok != QMessageBox.StandardButton.Yes:
                return
        if not self.hist or self.hist[-1] != text:
            self.hist = (self.hist + [text])[-self.MAX_HISTORY:]
            self.state.conf["adb_history"] = self.hist
            save_conf(self.state.conf)
        self.hpos = len(self.hist)
        self.cmd.clear()
        self.go.setEnabled(False)
        self._print(f"\n$ adb -s {_adb_serial(ip)} " + " ".join(args) + "\n")

        def work():
            return _adb_ready_error(ip, "ADB")

        def done(err, e):
            if e or err:
                self.go.setEnabled(True)
                self._print("✗ " + tr(str(e or err)) + "\n")
                return
            self._launch(ip, args)
        self.state.bg(work, done)

    def _launch(self, ip, args):
        from PyQt6.QtCore import QProcess
        p = QProcess(self)
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        p.readyReadStandardOutput.connect(
            lambda: self._print(bytes(p.readAllStandardOutput()).decode("utf-8", errors="replace").replace("\r\n", "\n")))
        started = time.monotonic()

        def finished(code, _st):
            en = LANG == "en"
            what = ("stopped" if en else "остановлено") if getattr(p, "killed", False) else \
                f"{'exit' if en else 'код'} {code}"
            self._print(f"[{what} · {time.monotonic() - started:.1f} {'s' if en else 'с'}]\n")
            self.go.setEnabled(True); self.stop_btn.setEnabled(False)
            print("adb console:", *args, "→", code, flush=True)

        p.finished.connect(finished)
        def failed(err):
            if err == QProcess.ProcessError.FailedToStart:
                self._print(f"✗ adb: {p.errorString()}\n")
                self.go.setEnabled(True); self.stop_btn.setEnabled(False)

        p.errorOccurred.connect(failed)
        p.setWorkingDirectory(P.downloads_dir())      # `adb pull` / `bugreport` files go here
        p.setProgram(_ADB)
        p.setArguments(["-s", _adb_serial(ip), *args])
        self.proc = p
        self.stop_btn.setEnabled(True)
        p.start()
        p.closeWriteChannel()                    # no stdin: commands never wait for input

    def stop(self):
        if self.running():
            self.proc.killed = True
            self.proc.kill()

    def closeEvent(self, e):
        self.stop()
        super().closeEvent(e)


def open_adb_console(holder, state: "State"):
    if not getattr(holder, "_adb_console", None):
        holder._adb_console = AdbConsole(state)
    w = holder._adb_console
    w.show(); w.raise_(); w.activateWindow()


class DropHint(QWidget):
    """Shown over a window while files are dragged onto it."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        p.setBrush(QColor(T.bg.red(), T.bg.green(), T.bg.blue(), 225))
        pen = QPen(T.accent, 2, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(r, 12, 12)
        p.setPen(T.accent)
        p.setFont(font(14, QFont.Weight.DemiBold))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                   tr("Отпустите — отправлю на ТВ") + "\n")
        p.setPen(T.fg_a(0.6))
        p.setFont(font(11))
        p.drawText(r.adjusted(16, 40, -16, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                   tr("APK — установка; видео, музыка, фото, папки и плейлисты — на экран; "
                      "остальное — в папку Download"))


class _FileDrop(QObject):
    """Event filter that lets a window take files dragged from Explorer → State.send_files."""
    def __init__(self, widget, state):
        super().__init__(widget)
        self.w, self.state = widget, state
        self.hint = DropHint(widget)

    @staticmethod
    def _files(e):
        md = e.mimeData()
        if md is None or not md.hasUrls():
            return []
        # files and folders on this computer; never \\server\share (NTLM), see fling_media.is_unc
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile() and not M.is_unc(u.toLocalFile())
                and os.path.exists(u.toLocalFile())]

    def eventFilter(self, obj, e):
        t = e.type()
        if t in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if self._files(e):
                e.acceptProposedAction()
                if not self.hint.isVisible():
                    self.hint.setGeometry(self.w.rect())
                    self.hint.raise_()
                    self.hint.show()
                return True
        elif t == QEvent.Type.DragLeave:
            self.hint.hide()
        elif t == QEvent.Type.Drop:
            self.hint.hide()
            files = self._files(e)
            if files:
                e.acceptProposedAction()
                QTimer.singleShot(0, lambda: self.state.send_files(files))
                return True
        return False


def install_file_drop(widget, state):
    """Let `widget` accept files dropped from Explorer / Finder / Dolphin."""
    widget.setAcceptDrops(True)
    widget._file_drop = _FileDrop(widget, state)
    widget.installEventFilter(widget._file_drop)


def extension_dir() -> str | None:
    """The unpacked Chrome/Edge extension that came with this Fling: next to Fling.exe in the
    setup build, in %LOCALAPPDATA%\\Fling for install.ps1, or the repo's build output."""
    here = os.path.dirname(os.path.abspath(sys.executable if P.FROZEN else __file__))
    for d in (os.path.join(here, "extension", "chrome"),
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "Fling", "extension", "chrome"),
              os.path.join(here, "..", "extension", "dist", "chrome")):
        if os.path.isfile(os.path.join(d, "manifest.json")):
            return os.path.normpath(d)
    return None


def _chrome_exe() -> tuple[str, str] | None:
    """(browser exe, its extensions page): Chrome first, then Edge."""
    roots = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    for rel, page in ((r"Google\Chrome\Application\chrome.exe", "chrome://extensions"),
                      (r"Microsoft\Edge\Application\msedge.exe", "edge://extensions")):
        for r in roots:
            p = os.path.join(r, rel)
            if r and os.path.isfile(p):
                return p, page
    return None


def _ext_script() -> str | None:
    """install-extension.ps1: next to Fling.exe (setup), in %LOCALAPPDATA%\\Fling (install.ps1),
    or in the repo's windows folder."""
    here = os.path.dirname(os.path.abspath(sys.executable if P.FROZEN else __file__))
    for d in (here, os.path.join(here, ".."), os.path.join(here, "..", "windows")):
        p = os.path.join(d, "install-extension.ps1")
        if os.path.isfile(p):
            return os.path.normpath(p)
    return None


_ext_busy = False


def install_extension(parent=None, state=None):
    """Windows: load the unpacked extension into Chrome/Edge by driving the browser
    (install-extension.ps1, in the background). Anything else, or if that fails: the
    manual walk-through."""
    global _ext_busy
    d = extension_dir()
    script, b = (_ext_script(), _chrome_exe()) if P.IS_WIN else (None, None)
    if not (d and script and b and state):
        _install_extension_by_hand(parent, d)
        return
    if _ext_busy:
        return
    _ext_busy = True

    def run():
        try:
            r = P.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-File", script,
                       "-Dir", d, "-Exe", b[0], "-Page", b[1]],
                      capture_output=True, text=True, timeout=120)
            out = (r.stdout or "").strip().splitlines()
            res = out[-1].strip() if out else "fail:" + ((r.stderr or "").strip()[-200:] or "no output")
        except Exception as ex:
            res = f"fail:{ex}"
        state.bridge.call.emit(lambda: done(res))

    def done(res):
        global _ext_busy
        _ext_busy = False
        print("extension install:", res, flush=True)
        if res in ("ok", "already"):
            notify_send("Расширение Fling добавлено в браузер.")
        else:
            _install_extension_by_hand(parent, d)

    notify_send("Добавляю расширение в браузер — не трогайте мышь и клавиатуру пару секунд…")
    threading.Thread(target=run, daemon=True, name="fling-ext").start()


def _install_extension_by_hand(parent, d):
    """Walk the user through loading the unpacked extension: the folder path goes to the
    clipboard, Explorer shows the folder, the browser opens its extensions page."""
    from PyQt6.QtWidgets import QMessageBox
    if not d:
        QMessageBox.warning(parent, "Fling", tr("Папка расширения не найдена — переустановите Fling."))
        return
    QApplication.clipboard().setText(d)
    if P.IS_WIN:
        P.popen(["explorer", "/select,", d])
        b = _chrome_exe()
        if b:
            P.popen([b[0], b[1]])
    else:
        P.open_path(os.path.dirname(d))
    box = QMessageBox(parent)
    box.setWindowTitle(tr("Расширение Fling для Chrome"))
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(tr("Один раз в Chrome:"))
    box.setInformativeText(
        tr("1. На открывшейся странице «Расширения» включите «Режим разработчика» (справа вверху).") + "\n" +
        tr("2. Нажмите «Загрузить распакованное расширение».") + "\n" +
        tr("3. Вставьте путь (Ctrl+V — он уже скопирован) и нажмите «Выбор папки».") + "\n\n" +
        tr("Или просто перетащите папку chrome из Проводника на страницу расширений.") + "\n\n" + d)
    box.exec()


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
        install_file_drop(self, state)        # files dragged onto the panel go to the TV
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
        # shown while the laptop keyboard drives the TV (Windows keyboard hook is on),
        # with a button to free the keyboard (remembered) and back
        self.kb_row = QWidget()
        self.kb_row.setObjectName("kbrow")
        self.kb_row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        kr = QHBoxLayout(self.kb_row); kr.setContentsMargins(8, 3, 4, 3); kr.setSpacing(6)
        self.kb_strip = label("", 10.5, T.accent, QFont.Weight.DemiBold, wrap=True)
        kr.addWidget(self.kb_strip, 1)
        self.kb_toggle = QPushButton()
        self.kb_toggle.setFont(font(10.5, QFont.Weight.DemiBold))
        self.kb_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.kb_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.kb_toggle.setFixedHeight(22)
        self.kb_toggle.clicked.connect(self._toggle_kb_capture)
        kr.addWidget(self.kb_toggle)
        self.kb_row.setToolTip(
            "Пока панель открыта, клавиатура управляет ТВ: стрелки или WASD, Enter, Пробел, "
            "Backspace, H, +/−. Esc закрывает панель. F1 — подсказка.\n"
            "«Захват» — вся клавиатура, только клавиши пульта или без захвата. Fling запомнит выбор.")
        self.kb_box = padded(self.kb_row, 10, 2, 10, 4)
        self.kb_box.hide()
        v.addWidget(self.kb_box)
        # the key guide and the ways out (F1, the menu, or a few keys in a row that do nothing)
        _tr = globals()["tr"]                 # `tr` is a local name further down in __init__
        self.kb_help = QWidget()
        self.kb_help.setObjectName("kbhelp")
        self.kb_help.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.kb_help.setStyleSheet(f"QWidget#kbhelp{{background:{Theme.css(T.fg_a(0.05))};border-radius:6px}}")
        hl = QVBoxLayout(self.kb_help); hl.setContentsMargins(10, 8, 10, 8); hl.setSpacing(6)
        hl.addWidget(label("Пока открыта панель, клавиатура управляет ТВ", 11, T.fg, QFont.Weight.DemiBold, wrap=True))
        hl.addWidget(label("Стрелки или W A S D — навигация · Enter — OK · Backspace — назад · H — домой · "
                           "Пробел — пауза · + / − — громкость · M — без звука · < > — перемотка · "
                           "цифровой блок 8 2 4 6 5 — тоже стрелки и OK.", 10.5, T.fg_a(0.75), wrap=True))
        hl.addWidget(label("Хотели печатать в другой программе? Esc закрывает панель и сразу возвращает "
                           "клавиатуру. Или выберите, как захватывать:", 10.5, T.fg_a(0.75), wrap=True))
        bss = (f"QPushButton{{border:none;border-radius:5px;padding:4px 10px;color:{Theme.css(T.fg)};"
               f"background:{Theme.css(T.fg_a(0.08))}}}QPushButton:hover{{background:{Theme.css(T.acc_a(0.22))}}}")

        def hbtn(text, fn, bold=False):
            b = QPushButton(_tr(text))
            b.setFont(font(10.5, QFont.Weight.DemiBold if bold else QFont.Weight.Normal))
            b.setCursor(Qt.CursorShape.PointingHandCursor); b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setStyleSheet(bss)
            b.clicked.connect(fn)
            return b
        r1 = QHBoxLayout(); r1.setSpacing(6)
        r1.addWidget(hbtn("Только клавиши пульта", lambda: self._set_kb_mode("remote")))
        r1.addWidget(hbtn("Не захватывать", lambda: self._set_kb_mode("off")))
        r1.addStretch(1)
        r2 = QHBoxLayout(); r2.setSpacing(6)
        r2.addWidget(hbtn("Печатать на ТВ", lambda: (self._kb_help(False), self.kb_btn.click())))
        r2.addStretch(1)
        r2.addWidget(hbtn("Понятно", lambda: self._kb_help(False), bold=True))
        hl.addLayout(r1); hl.addLayout(r2)
        self.kb_help_box = padded(self.kb_help, 10, 0, 10, 4)
        self.kb_help.hide()
        v.addWidget(self.kb_help_box)

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
        self.c_art = Artwork(120, on_refresh=self._refresh_now, on_click=self._open_screen)
        self.c_art.click_ok = state.adb_ok
        cl.addWidget(padded(self.c_art, 12, 10, 12, 0))
        self.c_title = label("", 12.5, weight=QFont.Weight.DemiBold, wrap=True)
        self.c_host = label("", 11, T.fg_a(0.55))
        box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(14, 10, 14, 0); bl.setSpacing(3)
        bl.addWidget(self.c_title); bl.addWidget(self.c_host)
        cl.addWidget(box)
        # YouTube-on-TV link: the only source of what native YouTube really plays
        self.yt_row = MenuRow("Связать с YouTube, чтобы видеть, что играет", icon="tv", accented=True,
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
        cl.addWidget(M.QueueStrip(state, "casting"))     # queue / slideshow / subtitles / speed
        # compact view: one row of icon keys, the words live in the tooltips
        self.c_bar = key_bar(
            ("prev", "Предыдущее видео", state.yt_prev, 28, False),
            ("rew", f"Назад {SEEK} с  (←)", lambda: state.seek(-SEEK), 32, False),
            ("pause", "Пауза / Играть  (Пробел)", state.toggle_play, 40, True),
            ("fwd", f"Вперёд {SEEK} с  (→)", lambda: state.seek(SEEK), 32, False),
            ("next", "Следующее видео", state.yt_next, 28, False),
            ("stop", "Остановить трансляцию  (Ctrl+.)", state.stop, 28, False),
            ("play_on_tv", "Отправить эту вкладку вместо текущего видео", state.cast_tab, 28, False))
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
        self.i_art = Artwork(108, on_refresh=self._refresh_now, on_click=self._open_screen)
        self.i_art.click_ok = state.adb_ok
        self.i_art_box = padded(self.i_art, 12, 10, 12, 0)
        il.addWidget(self.i_art_box)
        self.i_title = label("", 12.5, weight=QFont.Weight.DemiBold, wrap=True)
        self.i_host = label("", 11, T.fg_a(0.55))
        self.i_kind = label("", 11, T.green)
        box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(14, 10, 14, 6); bl.setSpacing(3)
        th = QHBoxLayout(); th.setContentsMargins(0, 0, 0, 0); th.setSpacing(6)
        th.addWidget(self.i_title, 1)
        # always reachable: not everyone casts browser tabs (Settings → General brings it back)
        self.i_tabs_x = QToolButton()
        self.i_tabs_x.setText("✕")
        self.i_tabs_x.setFont(font(11))
        self.i_tabs_x.setCursor(Qt.CursorShape.PointingHandCursor)
        self.i_tabs_x.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.i_tabs_x.setFixedSize(20, 20)
        self.i_tabs_x.setStyleSheet(f"QToolButton{{border:none;border-radius:10px;color:{Theme.css(T.fg_a(0.4))};"
                                    f"background:transparent}}QToolButton:hover{{color:{Theme.css(T.fg)};"
                                    f"background:{Theme.css(T.fg_a(0.1))}}}")
        self.i_tabs_x.setToolTip("Убрать отправку вкладок из панели — вернуть можно в Настройках")
        self.i_tabs_x.clicked.connect(self._hide_tabs)
        th.addWidget(self.i_tabs_x, 0, Qt.AlignmentFlag.AlignTop)
        bl.addLayout(th)
        self.i_host_row = QWidget()
        hl = QHBoxLayout(self.i_host_row); hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4); hl.addWidget(self.i_host); hl.addWidget(label("·", 11, T.fg_a(0.55))); hl.addWidget(self.i_kind); hl.addStretch(1)
        bl.addWidget(self.i_host_row)
        il.addWidget(box)
        self.i_tabbox = box                              # the browser tab's title and site
        il.addWidget(M.QueueStrip(state, "idle"))        # a stopped queue, a slideshow between photos
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
        self.i_why = label("", 11, T.fg_a(0.6), wrap=True)
        self.i_why_box = padded(self.i_why, 14, 0, 14, 7)
        il.addWidget(self.i_why_box)
        # no browser extension has ever talked to Fling: say so plainly, with a button
        self.i_ext = QWidget()
        el = QVBoxLayout(self.i_ext); el.setContentsMargins(14, 2, 14, 8); el.setSpacing(6)
        el.addWidget(label("Чтобы Fling видел вкладки Chrome и Edge, нужно один раз добавить в браузер "
                           "расширение Fling.", 11, T.fg_a(0.6), wrap=True))
        eh = QHBoxLayout(); eh.setSpacing(0)
        eh.addWidget(small_button("Установить расширение…", lambda: install_extension(self, state)))
        eh.addStretch(1)
        hide = small_button("Скрыть", self._hide_tabs)   # not everyone casts tabs: Settings brings it back
        hide.setToolTip("Убрать отправку вкладок из панели — вернуть можно в Настройках")
        eh.addWidget(hide)
        el.addLayout(eh)
        il.addWidget(self.i_ext)
        self.i_clip = MenuRow("Отправить скопированную ссылку", icon="clipboard", on=state.cast_clipboard)
        il.addWidget(self.i_clip)
        il.addWidget(M.OpenRow(state, self))             # files, folders, slideshow, recent casts
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
        sl.addWidget(padded(label("Один раз свяжите Fling с ТВ, чтобы открыть приложения, стрелки и ввод текста.",
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
        self.r_more = R.more_button(self)     # ⋯: scenes, sleep timer, turn on / off
        hdr.addWidget(self.r_more)
        self.r_power = KeyButton("power", size=26, on=lambda: state.tv_key(KEY["power"]))
        hdr.addWidget(self.r_power)
        rl.addLayout(hdr)
        self.r_sleep = R.SleepRow(state)      # "Sleep timer: the TV turns off in N min"
        rl.addWidget(self.r_sleep)
        # favourite apps of this TV (pin / reorder / add by package), see fling_remote
        self.grid_box = R.AppGrid(state, self)
        rl.addWidget(self.grid_box)
        k = lambda n: (lambda: state.tv_key(KEY[n]))
        # Round pad like a physical remote; Back · Home below its sides
        # (self._dpad: lit up for a moment when the same key comes from the laptop keyboard)
        pad, self._dpad = build_dpad(k)
        # modes: Arrows (the pad) · Touchpad · Numbers · Media
        self.modes = R.ModeStack(state, pad)
        rl.addWidget(self.modes)              # keyboard keys: in each button's tooltip
        # TV volume: the level the TV reports, as a slider; TVs that don't report one keep the keys
        self.r_vol = VolumeRow(state)
        self.r_vol.layout().setContentsMargins(14, 8, 14, 4)
        self.r_vol.hide()
        rl.addWidget(self.r_vol)
        self.vol_keys = QWidget()
        vk = QHBoxLayout(self.vol_keys); vk.setContentsMargins(12, 8, 12, 4); vk.setSpacing(5)
        vd = Pill("spk_minus", " Громк −", k("vol_down"), repeat=True)
        vd.setToolTip("Громкость −  (− / [)")
        vm = Pill("spk_slash", " Без звука", k("mute"))
        vm.setToolTip("Без звука  (M)")
        vu = Pill("spk_plus", " Громк +", k("vol_up"), repeat=True)
        vu.setToolTip("Громкость +  (+ / ])")
        vk.addWidget(vd); vk.addWidget(vm); vk.addWidget(vu)
        rl.addWidget(self.vol_keys)
        # Keyboard icon → reveal type field (sendText over Android TV Remote, as in Damion's Fling)
        ty = QVBoxLayout(); ty.setContentsMargins(14, 4, 14, 9); ty.setSpacing(3)
        tr = QHBoxLayout(); tr.setSpacing(6)
        self.kb_btn = QPushButton()
        self.kb_btn.setFixedSize(30, 24)
        self.kb_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.kb_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.kb_btn.setIcon(glyph("keyboard", 20, T.fg, weight=1.5))
        self.kb_btn.setIconSize(QSize(20, 20))
        self.kb_btn.setToolTip("Клавиатура — печать на ТВ")
        self.kb_btn.setStyleSheet(                    # same soft look as the mic button next to it
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

        self.r_fail = label("", 11, T.fg_a(0.6), wrap=True)
        self.r_fail_box = padded(self.r_fail, 14, 0, 14, 7)
        v.addWidget(self.r_fail_box)

        self.err = label("", 10.5, T.fg_a(0.6), wrap=True)
        self.err_box = padded(self.err, 14, 4, 14, 4)
        v.addWidget(self.err_box)

        self.house = QWidget()
        hv = QVBoxLayout(self.house); hv.setContentsMargins(0, 0, 0, 0); hv.setSpacing(0)
        v.addWidget(self.house)
        v_main, v = v, hv
        v.addWidget(Separator())
        # Samsung / Miracast: mirroring and the screen layout, right in the panel
        self.sv_box = QWidget()
        sb = QVBoxLayout(self.sv_box); sb.setContentsMargins(0, 0, 0, 0); sb.setSpacing(0)
        self.sv_mirror = MenuRow("Зеркалировать экран", icon="play_on_tv", accented=True,
                                 on=lambda: (self.hide(), state.sv.mirror()))
        sb.addWidget(self.sv_mirror)
        if P.IS_WIN:
            lay = QHBoxLayout(); lay.setContentsMargins(14, 2, 14, 6); lay.setSpacing(6)
            self.sv_layout = {}
            for title, mode, tip in (("Только ТВ", "external", "Экран только на ТВ, в его разрешении"),
                                     ("Расширить", "extend", "ТВ — второй экран, в его разрешении"),
                                     ("Дублировать", "clone", "Одна картинка на обоих экранах (на ТВ может растянуться)")):
                b = small_button(title, lambda _=False, m=mode: state.sv.layout(m))
                b.setToolTip(tip)
                self.sv_layout[mode] = b
                lay.addWidget(b, 1)
            sb.addLayout(lay)
        v.addWidget(self.sv_box)
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
        # the TV's name is a quick switcher: every TV with its status, one click away
        self.dev_name.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dev_name.mousePressEvent = lambda e: self._tv_menu()
        self.dot.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dot.mousePressEvent = lambda e: self._tv_menu()
        self.adb_badge = AdbBadge(18, compact=True)
        self.adb_badge.clicked.connect(self._adb_badge_click)
        self.switch = QToolButton()
        self.switch.setText("Сменить")
        self.switch.setFont(font(11))
        self.switch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.switch.setStyleSheet(f"QToolButton{{border:none;color:{Theme.css(T.accent)};background:transparent}}"
                                  "QToolButton::menu-indicator{image:none}")
        # the TV chooser window: searches at once, with an animation and a card per TV
        self.switch.clicked.connect(lambda: D.open_picker(sys.modules[__name__], self))
        self.ts_note = label("", 10.5, T.fg_a(0.5))   # "via Tailscale · 38 ms" for a tailnet TV
        self.ts_note.hide()
        ft.addWidget(self.dot)
        ft.addWidget(self.dev_name, 1)
        ft.addWidget(self.ts_note)
        ft.addWidget(self.adb_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        ft.addWidget(self.switch)
        fw = QWidget(); fw.setLayout(ft)
        # the TV in use is away and another one answers: switch in one click (State._think)
        self.suggest_btn = QPushButton()
        self.suggest_btn.setFont(font(11))
        self.suggest_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.suggest_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.suggest_btn.setStyleSheet(
            f"QPushButton{{border:none;border-radius:6px;padding:6px 10px;text-align:left;"
            f"color:{Theme.css(T.accent)};background:{Theme.css(T.acc_a(0.10))}}}"
            f"QPushButton:hover{{background:{Theme.css(T.acc_a(0.18))}}}")
        self.suggest_btn.clicked.connect(lambda: self.state.suggest and self.state.select_device(self.state.suggest))
        self.suggest_btn.hide()
        v.addWidget(padded(self.suggest_btn, 10, 2, 10, 0))
        v.addWidget(fw)
        # authorship: Fling is Damion Rashford's and Tatiana's app; this is an unofficial port by orfixe
        credit = label(f'<a href="https://github.com/tatianathevisionary/fling" style="color:{Theme.css(T.fg_a(0.45))};text-decoration:none">'
                       'Оригинальный Fling создали Damion Rashford и Tatiana<br>неофициальный порт — orfixe</a>', 10, T.fg_a(0.4),
                       wrap=True)
        credit.setOpenExternalLinks(True)
        credit.setToolTip("github.com/tatianathevisionary/fling")
        # theme and language right on the panel: a moon with Auto | Light | Dark, a globe with RU | EN
        import fling_settings
        qs = QHBoxLayout(); qs.setContentsMargins(14, 2, 14, 6); qs.setSpacing(6)
        moon = QLabel(); moon.setPixmap(fling_settings.icon("moon", 15, T.fg_a(0.6)).pixmap(15, 15))
        qs.addWidget(moon)
        cur = load_conf().get("theme", "auto")
        names = ["Auto", "Light", "Dark"] if LANG == "en" else ["Авто", "Светлая", "Тёмная"]
        self.theme_seg = MiniSeg(names, 1 if cur in fling_themes.LIGHT_IDS else 2 if cur in fling_themes.DARK_IDS else 0,
                                 self._set_theme)
        qs.addWidget(self.theme_seg)
        qs.addStretch(1)
        globe = QLabel(); globe.setPixmap(fling_settings.icon("globe", 15, T.fg_a(0.6)).pixmap(15, 15))
        qs.addWidget(globe)
        self.lang_seg = MiniSeg(["RU", "EN"], 0 if LANG == "ru" else 1,
                                lambda i: self._toggle_lang() if ("ru", "en")[i] != LANG else None)
        qs.addWidget(self.lang_seg)
        v.addLayout(qs)
        cr = QHBoxLayout(); cr.setContentsMargins(14, 0, 10, 6); cr.setSpacing(6)
        cr.addWidget(credit, 1)
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
              0xBD: Qt.Key.Key_Minus, 0x6D: Qt.Key.Key_Minus,
              0xBC: Qt.Key.Key_Comma, 0xBE: Qt.Key.Key_Period,
              # WASD and the letter keys (the same codes on the Russian layout)
              0x57: Qt.Key.Key_W, 0x41: Qt.Key.Key_A, 0x53: Qt.Key.Key_S, 0x44: Qt.Key.Key_D,
              0x48: Qt.Key.Key_H, 0x4D: Qt.Key.Key_M, 0x50: Qt.Key.Key_P,
              0x2E: Qt.Key.Key_Delete,
              # the numpad (NumLock on): 8 2 4 6 arrows, 5 OK, 0 back, 3 home
              0x68: Qt.Key.Key_8, 0x62: Qt.Key.Key_2, 0x64: Qt.Key.Key_4, 0x66: Qt.Key.Key_6,
              0x65: Qt.Key.Key_5, 0x60: Qt.Key.Key_0, 0x63: Qt.Key.Key_3}
    _HELP_VK = {0x70, 0xBF}              # F1, the "/ ?" key: what the keys do

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
        mode = self._kb_mode()
        capture = mode != "off"
        tv = s.remote_ready or s.panel == "casting"
        if not capture:                      # the user freed the keyboard: only Esc closes the panel
            h.keys = {0x1B}
        elif s.remote_ready:
            h.keys = set(h.KEYS) | set(self._VK_QT) | self._HELP_VK | (set(R.DIGIT_VK) if R.numbers_mode(self) else set())
        elif s.panel == "casting":           # no remote yet: ← / → seek, Space pauses
            h.keys = {0x25, 0x27, 0x20, 0x1B} | self._HELP_VK
        else:
            h.keys = {0x1B}
        # "all keys": the keyboard belongs to the TV while the panel is open — nothing typed
        # lands in the app behind it; a key the TV has no use for shows a hint instead
        h.swallow_all = capture and mode == "all" and tv
        active = self.isVisible() and not editing
        if active != h.active:
            print(f"remote keys: {'on' if active else 'off'} (mode={mode}, remote={s.remote_ready}, "
                  f"panel={s.panel}, editing={editing})", flush=True)
        h.active = active
        # say so while the keys go to the TV instead of the app the user was in
        if not h.active:
            self._kb_help(False)
        self._show_kb_strip(h.active and tv, mode)

    # Keyboard capture, one switch with three modes (conf "kb_mode"):
    #   all     the whole keyboard goes to Fling while the panel is open (default): remote keys
    #           drive the TV, any other key shows what to press instead
    #   remote  only the remote's keys; the rest still types in the app behind the panel
    #   off     nothing is taken; the remote is the panel's buttons (Esc still closes it)
    KB_MODES = (("all", "Вся клавиатура у ТВ"), ("remote", "Только клавиши пульта"), ("off", "Не захватывать клавиатуру"))

    def _kb_mode(self) -> str:
        c = self.state.conf
        m = c.get("kb_mode")
        if m not in ("all", "remote", "off"):
            m = "all" if c.get("kb_capture", True) else "off"     # the older on/off switch
        return m

    def _kb_capture(self) -> bool:
        return self._kb_mode() != "off"

    def _set_kb_mode(self, m: str):
        s = self.state
        s.conf["kb_mode"] = m
        s.conf.pop("kb_capture", None)
        save_conf(s.conf)
        print("remote keys: mode", m, flush=True)
        if m != "off":
            self._grab_keys()
        else:
            self._ungrab_keys()
        self._kb_help(False)
        self._update_keyhook()

    def _toggle_kb_capture(self):
        """The strip's button: a menu with the three modes and the key guide."""
        m = QMenu(self)
        cur = self._kb_mode()
        for key, title in self.KB_MODES:
            a = m.addAction(tr(title), lambda k=key: self._set_kb_mode(k))
            a.setCheckable(True); a.setChecked(key == cur)
        m.addSeparator()
        m.addAction(tr("Какие клавиши что делают"), lambda: self._kb_help(True))
        m.exec(self.kb_toggle.mapToGlobal(self.kb_toggle.rect().bottomLeft()))

    _KB_TEXT = {"all": "⌨  Клавиатура у ТВ · Esc — закрыть · F1 — подсказка",
                "remote": "⌨  Клавиши пульта у ТВ, остальные — у компьютера",
                "off": "⌨  Клавиатура свободна"}

    def _show_kb_strip(self, on: bool, mode="all"):
        strip, box = getattr(self, "kb_strip", None), getattr(self, "kb_box", None)
        if strip is None or box is None:
            return
        if mode is True or mode is False:            # older callers
            mode = "all" if mode else "off"
        capture = mode != "off"
        text = self._KB_TEXT[mode]
        if getattr(self, "_kb_hint_until", 0) > time.time():
            text = None                              # a "that key does nothing" hint is showing
        if text and strip.text() != tr(text):
            strip.setText(text)
        if getattr(self, "_kb_style", None) != mode:
            self._kb_style = mode
            col = T.accent if capture else T.fg_a(0.55)
            strip.setStyleSheet(f"color:{Theme.css(col)};background:transparent")
            self.kb_row.setStyleSheet(
                f"QWidget#kbrow{{background:{Theme.css(T.acc_a(0.12) if capture else T.fg_a(0.05))};"
                "border-radius:6px}")
            self.kb_toggle.setText(tr("Захват") + " ▾")
            self.kb_toggle.setStyleSheet(
                f"QPushButton{{border:none;border-radius:5px;padding:0 8px;color:{Theme.css(col)};"
                f"background:{Theme.css(T.fg_a(0.07))}}}"
                f"QPushButton:hover{{background:{Theme.css(T.acc_a(0.22))}}}")
        if box.isVisibleTo(self) != on:
            box.setVisible(on)
            QTimer.singleShot(0, self.place)  # the panel grows / shrinks by one line

    def _kb_unknown(self, vk: int):
        """A key the TV has no use for, pressed while the whole keyboard is captured: say what it
        is and what to press; a few in a row (the user is probably typing somewhere else) open
        the guide with the ways out."""
        now = time.time()
        recent = [t for t in getattr(self, "_kb_misses", []) if now - t < 6] + [now]
        self._kb_misses = recent
        print(f"remote keys: vk {vk:#04x} ({P.key_name(vk)}) does nothing on the TV", flush=True)
        if len(recent) >= 3:
            self._kb_misses = []
            self._kb_help(True)
            return
        self._kb_hint_until = now + 3.5
        self.kb_strip.setText(tr("«%s» не управляет ТВ · F1 — подсказка · Esc — закрыть") % P.key_name(vk))
        QTimer.singleShot(3600, lambda: (setattr(self, "_kb_hint_until", 0), self._update_keyhook()))

    def _kb_help(self, show: bool):
        box = getattr(self, "kb_help", None)
        if box is None or box.isVisibleTo(self) == show:
            return
        box.setVisible(show)
        QTimer.singleShot(0, self.place)

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
        if not repeat and R.hook_digit(self, vk):    # Numbers page: digit keys type digits
            return
        if vk in self._HELP_VK and not repeat:
            self._kb_help(not self.kb_help.isVisibleTo(self))
            return
        key = self._VK_QT.get(vk)
        if key is None:
            if not repeat:
                self._kb_unknown(vk)
            return
        text = " " if vk == 0x20 else ""
        ev = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, text, repeat)
        handled = self._handle_key(ev)
        if not repeat:
            print(f"remote keys: vk {vk:#04x} -> {_REMOTE_BY_QT.get(key)} "
                  f"{'sent' if handled else 'not sent (remote=' + str(self.state.remote_ready) + ')'}", flush=True)
        if not handled and not repeat and self._kb_mode() == "all":
            self._kb_unknown(vk)
        if handled:
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
        if self.isVisible() and not self._editing() and self._kb_capture():
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
                # the panel had the focus (not the hook): light the button up all the same
                self._flash(_REMOTE_BY_QT.get(e.key()) or _REMOTE_BY_TEXT.get((e.text() or "").lower()))
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

        if not e.isAutoRepeat() and R.digit_key(self, k):   # Numbers page: digits, not numpad arrows
            return True
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

    def _open_screen(self):
        """Click on the cover: the LIVE section."""
        self.go_live()

    def go_live(self):
        """The LIVE section: on Windows the panel grows into the LIVE window; elsewhere
        scrcpy's own window opens."""
        s = self.state
        if not P.IS_WIN:
            self.hide()
            if not s.screen.running("screen"):
                s.screen.open("screen")
            return
        w = getattr(s, "_live", None)
        if w is not None and w.isVisible():
            self.hide(); w.raise_(); w.activateWindow()
            return
        if w is None:
            w = s._live = LiveWindow(s)
        w.show_from(self)

    def _set_theme(self, i):
        c = load_conf()
        want = ("auto", "light", "dark")[i]
        if (want == "dark" and c.get("theme") in fling_themes.DARK_IDS) or                 (want == "light" and c.get("theme") in fling_themes.LIGHT_IDS):
            return                                   # already such a theme (Graphite, OLED…): keep it
        if c.get("theme", "auto") == want:
            return
        c["theme"] = want
        save_conf(c)
        restart_ui()

    def _toggle_lang(self):
        c = load_conf()
        c["lang"] = "en" if LANG == "ru" else "ru"
        save_conf(c)
        restart_ui()

    def open_setup(self):
        # One Connect TV window; close the help guide so they don't stack
        if getattr(self, "_help", None):
            self._help.hide()
        if not hasattr(self, "_setup"):
            self._setup = SetupWindow(self.state)
            self.state._setup_ref = self._setup
        self._setup.show(); self._setup.raise_(); self._setup.activateWindow()

    def _tv_menu(self):
        m = QMenu(self)
        fill_device_menu(m, self.state, self)
        self.state.check_online(force=True)
        m.exec(self.dev_name.mapToGlobal(self.dev_name.rect().bottomLeft()))

    def _hide_tabs(self):
        self.state.conf["hide_tabs"] = True
        save_conf(self.state.conf)
        self.state.changed.emit()

    def _adb_badge_click(self):
        """Footer Android: connect if off, otherwise a menu of everyday ADB actions."""
        if self.state.adb_ok():
            m = QMenu(self)
            for title, act in (
                ("Разбудить", "wake"),
                ("Усыпить", "sleep"),
                ("Домой", "home"),
                ("Снимок экрана", "screenshot"),
                ("Экран ТВ в отдельном окне", "screen"),
                ("Мышь на ТВ без картинки", "mouse"),
                ("ADB-консоль…", "console"),
                (None, None),
                ("Настройки", "settings"),
                ("Приложения", "apps"),
                ("Закрыть приложение", "force_stop"),
                ("Установить APK…", "apk"),
                ("Перезагрузить", "reboot"),
                ("Отключить ADB", "disconnect"),
                (None, None),
                ("Подключение ТВ…", "setup"),
                ("Инструменты…", "tools"),
            ):
                if title is None:
                    m.addSeparator(); continue
                if act == "setup":
                    m.addAction(title, lambda: (self.hide(), self.open_setup()))
                elif act == "tools":
                    m.addAction(title, lambda: (self.hide(), fling_tools.open_window()))
                elif act == "screenshot":
                    m.addAction(title, self.state.tv_screenshot)
                elif act in ("screen", "mouse"):
                    a = m.addAction(title, lambda a=act: self.state.screen.toggle(a))
                    a.setCheckable(True); a.setChecked(self.state.screen.running(act))
                elif act == "console":
                    m.addAction(title, lambda: (self.hide(), open_adb_console(self, self.state)))
                elif act == "apk":
                    # the popup would close under the file dialog anyway
                    m.addAction(title, lambda: (self.hide(), pick_apk(self.state)))
                else:
                    m.addAction(title, lambda a=act: self.state.adb_action(a))
            m.exec(self.adb_badge.mapToGlobal(self.adb_badge.rect().bottomLeft()))
        elif not P.adb_available():                 # nothing to connect with: install adb first
            self.hide()
            fling_tools.open_window()
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
            tabs_on = not s.conf.get("hide_tabs")
            self.i_art_box.setVisible(bool(yid) and tabs_on)
            self.i_art.set_url(f"https://img.youtube.com/vi/{yid}/hqdefault.jpg" if yid else None)
            no_ext = tabs_on and not s.tab and not s.conf.get("ext_seen") and not P.IS_MAC
            self.i_tabbox.setVisible(tabs_on)
            self.i_title.setText((s.tab or {}).get("title") or (
                "Fling не видит вкладки браузера" if no_ext else
                "Нет открытой страницы" if not s.tab else url))
            self.i_host.setText(host_of(url))
            kind, reason = classify(url)
            self.i_host_row.setVisible(bool(s.tab))     # no "— · No page open" under the same title
            self.i_ext.setVisible(no_ext)
            if reason:
                self.i_kind.setText(reason); self.i_kind.setStyleSheet(f"color:{Theme.css(T.fg_a(0.55))}")
            elif kind == "maybe":
                self.i_kind.setText("можно попробовать"); self.i_kind.setStyleSheet(f"color:{Theme.css(T.fg_a(0.55))}")
            else:
                self.i_kind.setText("можно отправить"); self.i_kind.setStyleSheet(f"color:{Theme.css(T.green)}")
            ok = reason is None
            self.i_cast.setText("Отправляю…" if s.cast_in_flight else "Отправить эту вкладку")
            self.i_cast.set_accented(ok)
            self.i_cast.setEnabled(ok and not s.cast_in_flight)
            # "No page open" is already the title; the extension hint covers the rest
            why = None if reason == "Нет открытой страницы" else reason
            self.i_why.setText(why or "")
            self.i_why_box.setVisible(bool(why) and tabs_on)
            cast_vol = not s.tv_paired and not SV.is_sv(s.device)   # the Cast volume: not on Samsung
            self.i_vol_sep.setVisible(cast_vol)
            self.i_vol.setVisible(cast_vol)
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
        sv = SV.is_sv(s.device)                       # Samsung / Miracast: no Google TV pairing
        self.sv_box.setVisible(sv)
        self.r_setup.setVisible(show_remote and idle_like and not ready and not s.bt_setup and not sv)
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
        bt_only = ready and not s.tv_paired and not s.adb_ok() and not sv
        self.grid_box.setVisible(not bt_only)      # apps open over the network remote or ADB
        self.grid_box.sync()
        self.modes.sync(bt_only)
        self.r_more.setVisible(not bt_only)
        self.r_sleep.sync()
        self.mic.setVisible(s.tv_paired)           # voice search needs the network remote
        # the TV's own volume as a slider when it reports one; otherwise the − / mute / + keys
        tv_vol = tv_volume_known(s) is not None
        self.r_vol.setVisible(tv_vol)
        self.vol_keys.setVisible(not tv_vol)
        if tv_vol:
            self.r_vol.sync()
        self.kb_btn.setVisible(True)
        if bt_only:
            self.r_now.setText(f"Bluetooth-пульт · {s.bt.get('peer') or 'ТВ'}")
            self.r_now.setVisible(True)
        elif sv:
            self.r_now.setText(s.sv.status_text())
            self.r_now.setVisible(True)
        elif ready and not s.tv_paired:            # keys go over ADB: the TV doesn't report its app
            self.r_now.setVisible(False)
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
            on = s.tv_voice
            self.mic.setIcon(glyph("mic_fill" if on else "mic", 14, T.red if on else T.fg))
            bg = QColor(T.red); bg.setAlphaF(0.15)
            self.mic.setStyleSheet(f"QPushButton{{border:none;border-radius:5px;background:{Theme.css(bg if on else T.fg_a(0.07))}}}")
            self.mic.setToolTip("Остановить голосовой поиск" if on else "Голосовой поиск")
            self.listening.setVisible(on)
        else:                                      # no voice search without the network remote
            self.listening.setVisible(False)
        self.shot_row.setVisible(not sv)           # screenshots go over ADB: not on Samsung / Miracast

        self.err.setText(s.last_error or "")
        busy = (s.last_error or "").rstrip().endswith("…")
        self.err.setStyleSheet(f"color:{Theme.css(T.fg_a(0.6) if busy else T.red)};background:transparent")
        self.err_box.setVisible(bool(s.last_error))
        if s.last_error and s.last_error != getattr(self, "_err_shown", None):
            # a message is a hint, not a state: it goes away by itself after 10 s
            def fade(msg=s.last_error):
                if s.last_error == msg:
                    s._set(last_error=None)
            QTimer.singleShot(10000, fade)
        self._err_shown = s.last_error

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
        tabs_on = not s.conf.get("hide_tabs")            # Settings → General → Browser tabs
        if not tabs_on:
            self.i_rows.setVisible(False)
        self.c_cast.setVisible(tabs_on)
        self.i_bar_cast.setVisible(tabs_on)
        self.c_bar.keys[0].setVisible(tabs_on)
        self.c_bar.setVisible(compact)
        self.i_bar.setVisible(compact)
        if compact:
            self.c_bar_prev.setVisible(self.c_prev.isVisibleTo(self.c_rows))
            self.c_bar_next.setVisible(self.c_next.isVisibleTo(self.c_rows))
            self.c_bar_play.setIcon(glyph("pause" if s.is_playing else "play", 16, T.accent, 2.0))
            self.i_bar_cast.setEnabled(self.i_cast.isEnabled())

        # footer
        on = s.tv_status(s.device)                     # a real check, not just "a TV is selected"
        col = T.green if on is True else T.red if on is False else T.fg_a(0.4)
        self.dot.setStyleSheet(f"background:{Theme.css(col)};border-radius:3px")
        tip = ({True: "В сети", False: "Не в сети — Fling не достаёт до этого ТВ"}.get(on, "Проверяю связь…")
               + "\n" + tr("Нажмите, чтобы переключить телевизор")) if s.device else ""
        self.dev_name.setToolTip(tip); self.dot.setToolTip(tip)
        dup = bool(s.device) and sum(1 for x in s.devices if x["name"] == s.device["name"]) > 1
        self.dev_name.setText((s.device["name"] + (f" ({s.device['ip']})" if dup else "")) if s.device
                              else ("Ищу ТВ…" if s.scanning else "Телевизор не найден"))
        alt = s.suggest
        if alt and s.device:
            self.suggest_btn.setText(tr("«{a}» не в сети — переключиться на «{b}»")
                                     .replace("{a}", s.device["name"]).replace("{b}", alt["name"]))
        if self.suggest_btn.isVisibleTo(self) != bool(alt and s.device):
            self.suggest_btn.setVisible(bool(alt and s.device))
            QTimer.singleShot(0, self.place)
        ts_text = s.tailnet.footer_text()
        self.ts_note.setText(ts_text)
        self.ts_note.setToolTip(s.tailnet.footer_tip())
        self.ts_note.setVisible(bool(ts_text))
        if ts_text and s.tailnet.online is False:
            self.dot.setStyleSheet(f"background:{Theme.css(T.orange)};border-radius:3px")
        self.switch.setVisible(True)                   # always available: rescan / add by IP even with one TV
        self.switch.setText("Выбрать")                 # short: the TV's name is right next to it
        # Keep the footer Android in sync (don't restart the Connected! animation every tick)
        if getattr(self, "adb_badge", None):
            self.adb_badge.setVisible(not SV.is_sv(s.device))   # no ADB on Samsung / Miracast
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
    saved = (conf.get("adb_ports") or {}).get(ip)
    if saved:
        ports.append(int(saved))
    # what the TV announces right now: after a reboot wireless debugging has a new port
    ann = adb_mdns().get(ip, {})
    for k in ("connect", "adb"):
        if ann.get(k):
            ports.append(ann[k])
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
                conf.setdefault("adb_ports", {})[ip] = p          # this TV's own port
                save_conf(conf)
                if p != saved:
                    print(f"adb: {ip} answers on port {p}", flush=True)
                _ADB_HOST[ip] = ("device", 0.0)
                return "device"
            last = st if st in _ADB_BACKOFF else "offline"
            _adb_remember(ip, last)
            return last
        last = "offline"
    _adb_remember(ip, last)
    return last


def _adb_missing_html() -> str:
    """adb is not installed: a link that installs it (Tools window), or how to do it by hand."""
    return fling_tools.missing_adb_html()


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
        self.msg.linkActivated.connect(fling_tools.link)     # web links + "install automatically"
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
                self.msg.setStyleSheet(f"color:{Theme.css(T.fg_a(0.6))};background:transparent")
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
        self.go = QPushButton(tr("Найти и подключить всё"))
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
                   'Оригинальный Fling создали Damion Rashford и Tatiana · неофициальный порт — orfixe</a>', 10.5, T.fg_a(0.45))
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
            choose = small_button("Выбрать ТВ…", lambda: D.open_picker(
                sys.modules[__name__], getattr(self.state, "_panel_ref", None) or self))
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
            # what to do next, in plain words (the watchers below keep it current)
            self.adb_hint = label("", 11, T.fg_a(0.75), wrap=True)
            self.adb_hint.hide()
            lay.addWidget(self.adb_hint)
            # the 6-digit code: only while the TV shows "Pair with code" (Fling sees it by mDNS)
            self.adb_pairbox = QWidget()
            pb = QHBoxLayout(self.adb_pairbox); pb.setContentsMargins(0, 2, 0, 0); pb.setSpacing(6)
            self.wcode = field("код из 6 цифр с экрана ТВ", mono=True)
            self.wcode.returnPressed.connect(self.adb_pair)
            pb.addWidget(self.wcode, 1)
            pb.addWidget(small_button("Связать", self.adb_pair))
            self.adb_pairbox.hide()
            lay.addWidget(self.adb_pairbox)
            # by hand — IP and ports, only if the TV can't be seen on the network
            self.adb_more = small_button("Вручную: IP и порт…", self._adb_toggle_manual)
            mr = QHBoxLayout(); mr.setContentsMargins(0, 0, 0, 0); mr.addWidget(self.adb_more); mr.addStretch(1)
            lay.addLayout(mr)
            self.adb_manual = QWidget()
            lay_m = QVBoxLayout(self.adb_manual); lay_m.setContentsMargins(0, 0, 0, 0); lay_m.setSpacing(4)
            self.adb_manual.hide()
            lay.addWidget(self.adb_manual)
            self._adb_watch = QTimer(self)
            self._adb_watch.setInterval(2500)
            self._adb_watch.timeout.connect(self._adb_tick)
            self._adb_mode, self._adb_until = None, 0.0
            self.wip = field("192.168.1.50", mono=True)
            self.wconn = field("порт, напр. 5555", mono=True)
            if self.state.ip:
                self.wip.setText(self.state.ip)
            saved_port = adb_port_for(self.state.ip) if self.state.ip else None
            if saved_port and saved_port != 5555:
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
            lay_m.addLayout(ip_row)
            port_row = QHBoxLayout(); port_row.setContentsMargins(0, 2, 0, 0); port_row.setSpacing(6)
            port_row.addWidget(label("Порт:", 11, T.fg_a(0.6)))
            port_row.addWidget(self.wconn, 1)
            port_row.addWidget(label("сопряжения:", 11, T.fg_a(0.6)))
            self.wport = field("порт", mono=True); self.wport.setFixedWidth(80)
            port_row.addWidget(self.wport)
            lay_m.addLayout(port_row)
            lay_m.addWidget(label("Обычно не нужно: Fling находит порты сам. С ТВ: Беспроводная отладка → "
                                  "«IP-адрес и порт».", 10.5, T.fg_a(0.45), wrap=True))
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
                self._set("find", "fail", f"Ничего не нашлось. Компьютер и ТВ в одной сети Wi-Fi? {e or ''}", "Искать снова")
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

    def _adb_toggle_manual(self):
        on = not self.adb_manual.isVisible()
        self.adb_manual.setVisible(on)
        self.adb_more.setText(tr("Скрыть ручные поля") if on else tr("Вручную: IP и порт…"))

    def _adb_hint(self, text: str):
        self.adb_hint.setText(tr(text) if text else "")
        self.adb_hint.setVisible(bool(text))

    def adb_connect(self):
        """One button: the selected TV, the port it announces (or the one that worked), and then
        whatever the TV needs — "Allow" on its screen, a pairing code, or debugging switched on."""
        if hasattr(self, "adb_badge"):
            self.adb_badge.set_state("run")
        if not P.adb_available():
            self._adb_result("missing")
            return
        if self.adb_manual.isVisible():
            ip, port, err = self._adb_endpoint()
            if err:
                self._adb_result("err", err)
                return
            self._on_adb_endpoint_edited()
        else:
            ip, port = self.state.ip, None
            if not ip or not _is_ipv4(ip):
                self._adb_result("err", "Сначала выберите телевизор выше (или впишите IP вручную).")
                return
        self._use_adb_ip(ip)
        self._adb_ip = ip
        self._set("adb", "run", "", None)

        def work():
            return adb_state(ip, force=True, port=port), adb_mdns(0).get(ip, {})
        self.state.bg(work, lambda r, e: self._adb_after(ip, *(r or ("offline", {}))))

    def _adb_after(self, ip, st, ann):
        print(f"adb: {ip} -> {st}, announces {ann}", flush=True)
        if st == "device":
            self._adb_stop_watch()
            self._adb_result("device")
        elif st == "unauthorized":
            self._adb_result("unauthorized")
            self._adb_hint("Посмотрите на экран ТВ: нажмите «Разрешить» и отметьте «Всегда разрешать с этого "
                           "компьютера». Fling подключится сам, как только вы нажмёте.")
            self._adb_start_watch("allow", 120)
        elif st == "missing":
            self._adb_result("missing")
        elif ann.get("pairing"):
            self._adb_ask_code()
        else:
            self._adb_result("err", "ТВ пока не пускает по ADB.")
            self._adb_hint("Включите на ТВ отладку — Fling заметит это сам:\n"
                           "1. Настройки → Об устройстве → 7 раз нажмите «Сборка».\n"
                           "2. Настройки → Для разработчиков → «Отладка по сети» (или «Отладка по USB»).\n"
                           "На Android 11+: «Беспроводная отладка» → «Подключить по коду» — код появится "
                           "на ТВ, впишите его здесь.")
            self._adb_start_watch("enable", 300)

    def _adb_ask_code(self):
        self._set("adb", "need", "", None)
        if hasattr(self, "adb_badge"):
            self.adb_badge.set_state("run")                 # not an error: the TV waits for the code
        self._adb_hint("На экране ТВ открыто сопряжение — впишите 6 цифр с экрана и нажмите «Связать».")
        self.adb_pairbox.show()
        self.wcode.setFocus()
        self._adb_stop_watch()

    def _adb_start_watch(self, mode: str, seconds: int):
        self._adb_mode, self._adb_until = mode, time.monotonic() + seconds
        self._adb_busy = False
        self._adb_watch.start()

    def _adb_stop_watch(self):
        self._adb_mode = None
        self._adb_watch.stop()

    def _adb_tick(self):
        """While waiting for the user at the TV: "allow" — has the TV accepted this computer?
        "enable" — has debugging appeared on the network (then connect, or ask for the code)?"""
        ip, mode = getattr(self, "_adb_ip", None), self._adb_mode
        if not ip or not mode or time.monotonic() > self._adb_until or not self.isVisible():
            self._adb_stop_watch()
            return
        if getattr(self, "_adb_busy", False):
            return
        self._adb_busy = True

        def work():
            if mode == "allow":
                out = P.run([_ADB, "devices"], capture_output=True, text=True, timeout=5).stdout
                for line in out.splitlines():
                    if line.startswith(ip + ":") and len(line.split()) > 1:
                        return ("state", line.split()[1], line.split()[0])
                return ("state", "gone", "")
            return ("mdns", adb_mdns(0).get(ip, {}), "")

        def done(r, e):
            self._adb_busy = False
            if not r or self._adb_mode != mode:
                return
            kind, val, serial = r
            if kind == "state" and val == "device":
                p = serial.rpartition(":")[2]
                c = load_conf()
                if p.isdigit():
                    c.setdefault("adb_ports", {})[ip] = int(p); c["adb_port"] = int(p); save_conf(c)
                _ADB_HOST[ip] = ("device", 0.0)
                self._adb_stop_watch()
                self._adb_hint("")
                self._adb_result("device")
            elif kind == "mdns" and val.get("pairing"):
                self._adb_ask_code()
            elif kind == "mdns" and (val.get("connect") or val.get("adb")):
                self._adb_stop_watch()
                self._adb_hint("")
                self.adb_connect()
        self.state.bg(work, done)

    def adb_pair(self):
        if hasattr(self, "adb_badge"):
            self.adb_badge.set_state("run")
        if not P.adb_available():
            self._adb_result("missing")
            return
        if self.adb_manual.isVisible():
            ip, _, err = self._adb_endpoint()
            if err:
                self._adb_result("err", err)
                return
        else:
            ip = getattr(self, "_adb_ip", None) or self.state.ip
        self._use_adb_ip(ip)
        typed, code = self.wport.text().strip(), re.sub(r"\D", "", self.wcode.text())
        if len(code) != 6:
            self._adb_result("err", "Код сопряжения — 6 цифр с экрана ТВ.")
            return
        def work():
            port = typed if typed.isdigit() else adb_mdns(0).get(ip, {}).get("pairing")
            if not port:
                return "no-pairing-port"
            r = P.run([_ADB, "pair", f"{ip}:{port}", code], capture_output=True, text=True, timeout=20)
            return (r.stdout + r.stderr).strip()
        def done(out, e):
            ok = out and "Successfully paired" in out
            if ok:
                self.adb_pairbox.hide(); self.wcode.clear()
                self._adb_hint("")
                if hasattr(self, "adb_badge"):
                    self.adb_badge.set_state("run")
                _MDNS["at"] = -99.0                      # the connect port appears right after pairing
                QTimer.singleShot(1200, self.adb_connect)
            elif out == "no-pairing-port":
                self._adb_result("err", "Окно с кодом на ТВ уже закрыто. На ТВ: Беспроводная отладка → "
                                        "«Подключить по коду» — и впишите новый код.")
            else:
                self._adb_result("err", f"Не вышло: {out or e}")
        self.state.bg(work, done)

    def _adb_result(self, st, err: str = ""):
        self.adb = st if st != "err" else "fail"
        badge = getattr(self, "adb_badge", None)
        # Keep the card mark in sync; detail lives on the Android badge
        if st == "device":
            p = adb_port_for(getattr(self, "_adb_ip", None) or self.state.ip or "")
            self._set("adb", "ok", "", None)
            if hasattr(self, "adb_hint"):
                self._adb_hint("")
                self.adb_pairbox.hide()
            if hasattr(self, "wconn"):
                self.wconn.setText(str(p))
            if badge:
                badge.set_state("ok")
            if hasattr(self, "adb_actions"):
                self.adb_actions.setVisible(True)
            self.state.poll_tvm()
            self.state._set_adb_ui("ok")
        elif st == "unauthorized":
            msg = ("Ждём «Разрешить» на экране ТВ…")
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
            msg = err or "ТВ пока не пускает по ADB."
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
        if self.adb == "missing" and P.adb_available():    # installed meanwhile (Tools window)
            self.adb = "?"
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
        if hasattr(self, "wconn") and not self.wconn.hasFocus() and not self.wconn.text().strip() and s.ip:
            saved = adb_port_for(s.ip)
            if saved and saved != 5555:
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
        # updates: checked by itself; on Windows downloaded, verified and installed when idle
        import fling_update
        self.updater = state.updater = fling_update.Updater(sys.modules[__name__], state, P.notify, QApplication.quit)
        self.icon.messageClicked.connect(lambda: self.updater.rel and open_settings(self.panel, "about"))
        self.updater.start()
        self._casting = None
        state.changed.connect(self.sync)
        self.timer = QTimer(interval=2000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        state.pause_polling = self.timer.stop            # the updater: nothing new before the setup
        state.resume_polling = self.timer.start

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
        st.check_online()                          # green / red dots: throttled inside
        self._tick = getattr(self, "_tick", 0) + 1
        visible = self.panel.isVisible()
        if not visible and on_battery():          # on battery with the panel closed: no background polling
            return
        # panel closed: every 10 s while something plays on the TV, once a minute when idle —
        # each read is an adb.exe start (plus its conhost), six a minute added up for nothing
        playing = st.tvm_live or st.has_media or st.panel == "casting"
        if not visible and self._tick % (5 if playing else 30):
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
        if not s.conf.get("notify_track", True) or self.state.panel_visible() or s.media.quiet_track:
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
        a = m.addAction("Отправить эту вкладку вместо текущего видео" if casting else "Отправить эту вкладку", s.cast_tab)
        a.setShortcut(QKeySequence(GLOBAL_SHORTCUT)); a.setEnabled(castable)
        a.setVisible(not s.conf.get("hide_tabs"))
        m.addAction("Отправить скопированную ссылку", s.cast_clipboard)
        M.fill_tray_menu(m.addMenu("Файлы с компьютера на ТВ"), s)
        m.addSeparator()
        # playback of what's on the TV now
        m.addAction("Пауза" if s.is_playing else "Играть", s.toggle_play).setEnabled(casting)
        m.addAction(f"Назад {SEEK} с", lambda: s.seek(-SEEK)).setEnabled(casting)
        m.addAction(f"Вперёд {SEEK} с", lambda: s.seek(SEEK)).setEnabled(casting)
        vm = m.addMenu(f"Громкость — {s.volume}%")
        for lvl in range(0, 101, 10):
            va = vm.addAction(f"{lvl}%", lambda l=lvl: s.set_volume(l))
            va.setCheckable(True); va.setChecked(abs(s.volume - lvl) < 5)
        a = m.addAction("Остановить трансляцию", s.stop); a.setEnabled(casting)
        m.addSeparator()
        # the TV and its tools
        dm = m.addMenu("Телевизор" + (f" — {s.device['name']}" if s.device else ""))
        fill_device_menu(dm, s, None)
        R.tray_menu(m, s, self.panel)            # scenes and the sleep timer
        m.addAction("Сделать снимок", s.tv_screenshot)
        tm = m.addMenu("ADB")
        a = tm.addAction("Экран ТВ в отдельном окне", lambda: s.screen.toggle("screen"))
        a.setCheckable(True); a.setChecked(s.screen.running("screen"))
        a = tm.addAction("Мышь на ТВ без картинки", lambda: s.screen.toggle("mouse"))
        a.setCheckable(True); a.setChecked(s.screen.running("mouse"))
        tm.addAction("ADB-консоль…", lambda: open_adb_console(self.panel, s))
        m.addSeparator()
        # options
        na = m.addAction("Уведомлять о смене видео", self.toggle_notify)
        na.setCheckable(True); na.setChecked(s.conf.get("notify_track", True))
        la = m.addAction("Запускать при входе", toggle_login)
        la.setCheckable(True); la.setChecked(login_enabled())
        m.addSeparator()
        # windows (the log is in Settings → About and `fling log`)
        m.addAction("Подключение ТВ…", self.panel.open_setup)
        if SV.is_sv(s.device):
            m.addAction("Зеркалировать экран", s.sv.mirror)
        m.addAction("Инструменты…", lambda: fling_tools.open_window())
        m.addAction("Настройки…", lambda: open_settings(self.panel))
        m.addAction("Как пользоваться", lambda: open_help(self.panel))   # guide from the tray menu
        m.addAction("О программе Fling", lambda: open_settings(self.panel, "about"))
        m.addAction("Отчёт для разработчика…", lambda: make_report(self.state))
        m.addSeparator()
        m.addAction("Выйти из Fling", QApplication.quit)


def make_report(state=None):
    """The zip for the developer (fling_report): made in the background, then shown in
    Explorer with a note where to send it."""
    import fling_report
    import traceback

    def done(path, err):
        if err:
            P.notify(tr("Отчёт не получился: ") + err)
            return
        fling_report.reveal(path)
        P.notify(tr("Отчёт сохранён в «Загрузки»: %s. Приложите его к сообщению об ошибке на GitHub "
                    "или отправьте тому, кто попросил.") % os.path.basename(path))

    def work():
        try:
            p = fling_report.make(sys.modules[__name__], state)
            e = None
        except Exception as ex:
            p, e = None, repr(ex)
            traceback.print_exc()
        if state is not None:
            state.bridge.call.emit(lambda: done(p, e))
        else:
            done(p, e)
    threading.Thread(target=work, daemon=True, name="fling-report").start()


_restarting = False


def restart_ui():
    """Start a fresh Fling and quit this one (language / appearance changes)."""
    global _restarting
    if _restarting:
        return
    _restarting = True
    P.restart_app()
    QApplication.quit()


def follow_system_scheme(app):
    """Appearance "as the system": restart when Windows / KDE switches light ↔ dark or the
    accent colour — once no Fling window is open (see fling_themes.SystemWatch)."""
    app._fling_watch = fling_themes.SystemWatch(app, load_conf, restart_ui,
                                                lambda: system_dark(app), system_accent)


def open_settings(holder, page=None):
    """Open (or raise) the Settings window; `page` = "appearance", "general", "remote",
    "cast" or "about"."""
    import fling_settings
    fling_settings.open_settings(sys.modules[__name__], holder, page)


# ---------------------------------------------------------------- single instance + commands

def send_to_running(cmd: str) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(SOCKET)
    if not sock.waitForConnected(400):
        return False
    sock.write(cmd.encode())
    sock.flush()
    sock.waitForBytesWritten(400)
    # The server answers "ok" once it has handled the command. No answer means the other
    # Fling is shutting down (after a theme / language restart): don't hand the command to a
    # dying process — start this one instead.
    answered = sock.waitForReadyRead(1500) and b"ok" in bytes(sock.readAll())
    sock.disconnectFromServer()
    return answered


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
            "yt_live": state.yt_live,              # YouTube via the TV link: previous / next work
            "notify_track": state.conf.get("notify_track", True),
            "apps": [name for name, _, _ in TV_APPS],
            "error": tr(state.last_error) if state.last_error else None,
        }

    def handle(path, body):
        if not state.conf.get("ext_seen"):        # an extension is installed: drop the install hint
            state.conf["ext_seen"] = True
            save_conf(state.conf)
            state.changed.emit()
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
            # Web links only. A local or UNC path here would be served to the whole LAN by
            # catt, and os.path.isfile on a \\host\share path leaks the Windows login hash (NTLM).
            if urlparse(url).scheme not in ("http", "https"):
                return {"ok": False, "error": "Это не веб-страница"}
            if urlparse(src).scheme not in ("http", "https"):
                src = ""
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
            state.seek(max(-3600, min(3600, int(body.get("seconds") or SEEK))))   # bounded: see seek()
        elif a == "stop":
            state.stop()
        elif a == "next":
            state.yt_next()
        elif a == "prev":
            state.yt_prev()
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
    # optional pin: "bridge_extension_ids": ["<chrome id>", "<firefox uuid>"] in fling.json
    handle.allowed_ids = tuple(str(x).lower() for x in state.conf.get("bridge_extension_ids") or () if x)
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
    try:
        return cm()
    except OSError as e:
        # The Fling that started us went away (an update or restart closed it) and took our
        # pipe with it: Windows reports the write as EINVAL (22) or EPIPE. Nobody reads us
        # any more — exit quietly instead of PyInstaller's "Unhandled exception" window.
        import errno
        # only a broken stdio pipe (no file name): a real "invalid argument" on a path is reported
        if e.filename is None and (e.errno in (errno.EINVAL, errno.EPIPE) or isinstance(e, BrokenPipeError)):
            os._exit(1)
        raise


fling_tools.bind_ui(sys.modules[__name__])   # the Tools window uses label(), glyph(), T …


def main():
    global T
    if len(sys.argv) > 1 and sys.argv[1] == "--catt":
        return catt_main(sys.argv[2:])
    P.setup_logging()
    # PyQt6 aborts the whole process on an exception inside a slot unless an excepthook
    # is set. Log it and keep running: one failed click must not kill the tray icon.
    import traceback
    def excepthook(kind, value, tb):
        traceback.print_exception(kind, value, tb, file=sys.stderr)
    sys.excepthook = excepthook
    if "--after-restart" in sys.argv:              # theme / language switch: let the old instance exit first
        i = sys.argv.index("--after-restart")
        sys.argv.pop(i)
        old = sys.argv.pop(i) if i < len(sys.argv) and sys.argv[i].isdigit() else None
        if not (old and P.wait_pid_exit(int(old), 15)):
            time.sleep(1.2)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd.lower().endswith(".apk") and os.path.isfile(cmd):     # "Open with Fling" on an .apk
        cmd = "install-apk"
        sys.argv.insert(1, cmd)
    if cmd == "install-apk":                       # `fling install-apk app.apk [split2.apk …]`
        cmd = "\t".join(["install-apk", *(os.path.abspath(a) for a in sys.argv[2:])])
    if cmd == "send":                              # `fling send file…` (also Explorer's Send to)
        cmd = "\t".join(["send", *(os.path.abspath(a) for a in sys.argv[2:])])
    if cmd in ("scene", "sleep-timer"):            # `fling scene "Movie night"`, `fling sleep-timer 30`
        cmd = "\t".join([cmd, " ".join(sys.argv[2:])])
    if cmd in ("play", "slideshow"):               # `fling play file|folder|playlist|link…`, `fling slideshow folder [s]`
        cmd = "\t".join([cmd, *M.cli_args(sys.argv[2:])])
    from PyQt6.QtCore import qInstallMessageHandler
    qInstallMessageHandler(P.qt_log_handler)
    app = QApplication(sys.argv)
    app.setApplicationName("Fling")
    app.setDesktopFileName("fling")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(tray_icon())
    if cmd == "--make-icon":                       # installer: write fling.ico
        write_ico(sys.argv[2])
        return 0
    if send_to_running(cmd):
        return 0
    if cmd == "quit":                              # nothing running to quit: don't start Fling for it
        return 0                                   # (the uninstaller and the setup call `Fling.exe quit`)
    if P.start_guard() == "tampered":              # see fling_platform: guard
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "Fling", tr(
            "Файлы Fling изменены после установки, поэтому Fling не запущен. "
            "Установите Fling заново со страницы релизов на GitHub и проверьте компьютер антивирусом."))
        return 2
    conf0 = load_conf()
    apply_color_scheme(app, conf0)
    T = Theme(app, conf0)
    if fling_welcome.needed(conf0):               # first run: the language first, then the tour
        res = fling_welcome.run(sys.modules[__name__], conf0)
        conf0 = load_conf()
        conf0["lang"] = res["lang"]
        conf0["welcome_done"] = VERSION
        save_conf(conf0)
        sys.modules[__name__].LANG = lang()        # tr() and install_translation read it
    install_translation()
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    state = State()
    fling_catalog.bind_state(state)                # the Catalog installs on / reads from this TV
    panel = Panel(state)
    state._panel_ref = panel
    state.panel_visible = panel.isVisible
    tray = Tray(state, panel)
    if "auto" in (conf0.get("theme", "auto"), conf0.get("accent", "auto")):
        follow_system_scheme(app)
    # Restarted by Settings (theme, accent, language): show the panel in its new look, not
    # Settings again — reopening Settings left people with only the themes window on screen.
    if conf0.get("reopen_settings"):
        c = load_conf(); c.pop("reopen_settings", None); save_conf(c)
        state.conf.pop("reopen_settings", None)

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
        elif c in ("smartview", "mirror"):                   # `fling mirror`: screen to a Samsung / Miracast TV
            state.sv.mirror()
        elif c.startswith("send\t"):
            state.send_files([f for f in c.split("\t")[1:] if f])
        elif c.split("\t")[0] in ("play", "slideshow", "next", "prev"):   # fling_media verbs
            M.cli(state, c.split("\t"), panel)
        elif c.startswith("install-apk"):
            files = [f for f in c.split("\t")[1:] if f]
            state.tv_install_apk(files) if files else pick_apk(state)
        elif c == "setup":
            panel.open_setup()
        elif c == "settings" or c.startswith("settings:"):   # `fling settings`
            open_settings(panel, c.partition(":")[2] or None)
        elif c == "about":                                   # `fling about`
            open_settings(panel, "about")
        elif c in ("screen", "mouse"):                       # `fling screen` / `fling mouse`
            state.screen.toggle(c)
        elif c == "adb":                                     # `fling adb`: the ADB console
            open_adb_console(panel, state)
        elif c == "tools":                                   # `fling tools`: adb / scrcpy
            fling_tools.open_window()
        elif c == "catalog":                                 # `fling catalog`: extra modules
            fling_tools.open_window(tab="catalog")
        elif c == "log":                                     # `fling log`: open app.log
            P.open_path(P.LOG)
        elif c == "report":                                  # `fling report`: the zip for the developer
            make_report(state)
        elif c == "install-extension":
            install_extension(None, state)
        elif c.startswith("scene\t"):                        # Task Scheduler: wake the TV on a schedule
            R.controller(state).run_scene_named(c.split("\t", 1)[1])
        elif c.startswith("sleep-timer\t"):                  # minutes; 0 cancels
            arg = c.split("\t", 1)[1].strip()
            R.controller(state).set_sleep(int(arg) if arg.isdigit() else None)
        elif c in ("tv-on", "tv-off"):
            (R.controller(state).tv_on if c == "tv-on" else R.controller(state).tv_off)()
        elif c == "quit":
            app.quit()

    app.aboutToQuit.connect(state.media.shutdown)        # stop serving files to the LAN
    app.aboutToQuit.connect(CAST.close)
    app.aboutToQuit.connect(state.screen.stop)
    app.aboutToQuit.connect(state.atv.close)             # no half-open remote session left on the TV
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

    fling_tools.cleanup()                          # leftovers of an interrupted tool install
    fling_tools.first_run_scan()                   # pick adb / scrcpy copies found on this computer
    if SV.is_sv(state.device):                     # the last TV was a Samsung / Miracast one
        state.sv.select(state.device)
    else:
        state.atv.connect(state.ip)
    state.refresh()
    if cmd not in ("tray", "background"):
        QTimer.singleShot(300, lambda: handle(cmd))
    # Do not auto-open the help window on first launch — one panel is enough;
    # the guide stays behind the ? button / tray “How to use”.
    if not state.conf.get("help_seen"):
        state.conf["help_seen"] = True
        save_conf(state.conf)
    rc = app.exec()
    # Quit means quit now. Worker threads (a `catt scan` still running, the YouTube loop)
    # would otherwise keep the process alive for seconds after the windows are gone; a
    # restarted Fling then found this dying copy on the socket and exited too. Settings are
    # written atomically (save_conf), so nothing is half-saved.
    P.hard_exit(rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
