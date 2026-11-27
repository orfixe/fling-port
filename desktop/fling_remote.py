# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
"""Remote-control modes for the Fling panel (the "On the TV" section).

  * favourite apps: pin / reorder / rename / remove, add the app that is open on the TV,
    a catalog app or any installed app (list over ADB) — remembered per TV;
  * modes under the app grid: Arrows (the round d-pad), Touchpad (drag = arrows,
    click = OK), Numbers (digits, channels, input, guide…) and Media keys;
  * scenes ("Movie night": turn on the TV → open an app → volume 20 %) — also from the
    tray menu and the command line (`fling scene "Movie night"`);
  * a sleep timer (turn the TV off in N minutes), explicit Turn on / Turn off;
  * optional system-wide TV keys (Windows RegisterHotKey, off by default).

fling_app.py is a monolith; this module keeps the new remote features out of it and talks
to it through `F` (the fling_app module, set by `bind()`), so widgets here reuse its
theme, translation and helpers. Only safe ADB commands are used here (input keyevent,
monkey / am start to open an app, listing packages) — nothing that removes or resets."""
from __future__ import annotations

import math
import re
import threading
import time

from PyQt6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (QApplication, QCheckBox, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                             QListWidget, QMenu, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

F = None          # the fling_app module (bind)

# Android key codes (the Android TV Remote protocol uses the same numbers)
K = dict(power=26, home=3, back=4, up=19, down=20, left=21, right=22, ok=23, vol_up=24, vol_down=25,
         mute=164, play_pause=85, stop=86, next=87, prev=88, rewind=89, ffwd=90,
         info=165, ch_up=166, ch_down=167, guide=172, captions=175, settings=176, input=178,
         red=183, green=184, yellow=185, blue=186, audio=222, sleep=223, wakeup=224)
K.update({str(d): 7 + d for d in range(10)})

MAX_APPS = 15
PKG_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+")
SLEEP_CHOICES = (15, 30, 45, 60, 90, 120)
MODES = (("dpad", "Стрелки"), ("touch", "Тачпад"), ("num", "Цифры"), ("media", "Медиа"))

# Names for packages found on the TV over ADB (only used to label them)
KNOWN_APPS = {
    "com.google.android.youtube.tvmusic": "YouTube Music", "com.google.android.youtube.tvkids": "YouTube Kids",
    "org.xbmc.kodi": "Kodi", "org.videolan.vlc": "VLC", "tv.twitch.android.app": "Twitch",
    "org.jellyfin.androidtv": "Jellyfin", "tv.pluto.android": "Pluto TV", "com.apple.atve.androidtv.appletv": "Apple TV",
    "com.wbd.stream": "Max", "com.hulu.livingroomplus": "Hulu", "com.cbs.ott": "Paramount+",
    "com.teamsmart.videomanager.tv": "SmartTube", "com.liskovsoft.smarttubetv.beta": "SmartTube",
    "ru.ivi.client": "ivi", "ru.more.play": "Okko", "com.android.tv.settings": "Настройки",
    "com.google.android.videos": "Google TV", "com.google.android.play.games": "Play Игры",
    "com.android.vending": "Google Play",
}
_GENERIC = {"tv", "android", "app", "apps", "atv", "androidtv", "leanback", "livingroom", "ott", "client",
            "mobile", "player", "ninja", "launcher", "com", "org", "net", "ru", "beta", "free", "stream"}
_HIDDEN = {"com.google.android.tvlauncher", "com.google.android.apps.tv.launcherx",
           "com.google.android.apps.tv.launcher", "com.google.android.leanbacklauncher"}

EN = [
    # modes
    ("Стрелки", "Arrows"), ("Тачпад", "Touchpad"), ("Цифры", "Numbers"), ("Медиа", "Media"),
    ("Ведите по панели — стрелки\nЩелчок — OK, правая кнопка — Назад",
     "Drag on the pad for arrows\nClick for OK, right-click for Back"),
    ("Тачпад: ведите — стрелки, щелчок — OK, правая кнопка — Назад, средняя — Домой",
     "Touchpad: drag for arrows, click for OK, right-click for Back, middle-click for Home"),
    ("Канал +", "Channel +"), ("Канал −", "Channel −"), ("Источник", "Input"), ("Телегид", "Guide"),
    ("Инфо", "Info"), ("Источник сигнала (HDMI, антенна…)", "Input source (HDMI, antenna…)"),
    ("Телепрограмма", "TV guide"), ("Сведения о передаче", "Programme info"),
    ("Настройки ТВ", "TV settings"), ("Субтитры", "Subtitles"), ("Звуковая дорожка", "Audio track"),
    ("Предыдущий трек", "Previous track"), ("Следующий трек", "Next track"),
    ("Перемотка назад", "Rewind"), ("Перемотка вперёд", "Fast-forward"),
    ("Пауза / Играть", "Play / Pause"), ("Стоп", "Stop"),
    ("Красная кнопка", "Red button"), ("Зелёная кнопка", "Green button"),
    ("Жёлтая кнопка", "Yellow button"), ("Синяя кнопка", "Blue button"),
    ("Цветные кнопки — для телеканалов и телетекста", "Colour buttons — for TV channels and teletext"),
    # favourite apps
    ("Добавить приложение", "Add an app"), ("Сдвинуть влево", "Move left"), ("Сдвинуть вправо", "Move right"),
    ("Переименовать…", "Rename…"), ("Убрать", "Remove"),
    ("Щелчок — открыть на ТВ, правая кнопка — изменить", "Click to open on the TV, right-click to edit"),
    ("Открытое на ТВ: ", "Open on the TV: "), ("Приложения на ТВ", "Apps on the TV"),
    ("Нужен ADB — «Подключение ТВ…»", "Needs ADB — “Connect TV…”"), ("Загружаю список…", "Loading the list…"),
    ("Не удалось получить список", "Couldn't get the list"), ("Все приложения уже добавлены", "All apps are already added"),
    ("Указать пакет…", "Enter a package…"), ("Вернуть стандартный набор", "Restore the default set"),
    ("Мест больше нет — уберите какое-нибудь приложение", "No more room — remove an app first"),
    ("Имя пакета приложения (например, org.videolan.vlc):", "App package name (for example, org.videolan.vlc):"),
    ("Название на кнопке:", "Button label:"), ("Fling — приложение", "Fling — app"),
    ("Это не похоже на имя пакета Android.", "That doesn't look like an Android package name."),
    ("Не удалось открыть приложение: ", "Couldn't open the app: "),
    ("Открыть приложение можно, когда пульт связан с ТВ или подключён ADB.",
     "Apps open once the remote is paired with the TV or ADB is connected."),
    ("приложение не найдено на ТВ", "the app isn't on the TV"),
    # more menu, power, sleep timer
    ("Ещё: сцены, таймер сна, включение", "More: scenes, sleep timer, power"),
    ("Включить ТВ", "Turn on the TV"), ("Выключить ТВ", "Turn off the TV"),
    ("Таймер сна", "Sleep timer"), ("Отменить таймер", "Cancel the timer"),
    ("Таймер сна: ТВ выключится через ", "Sleep timer: the TV turns off in "),
    ("Через минуту ТВ выключится по таймеру сна.", "The sleep timer turns the TV off in a minute."),
    ("Таймер сна: выключаю ТВ.", "Sleep timer: turning the TV off."),
    ("Таймер сна отменён: выбран другой ТВ.", "Sleep timer cancelled: another TV was selected."),
    ("Таймер сна отменён.", "Sleep timer cancelled."),
    ("Отменить", "Cancel"),
    ("Не могу выключить ТВ: нет связи ни с пультом, ни с ADB.",
     "Can't turn the TV off: neither the remote nor ADB is connected."),
    ("Не могу включить ТВ: нет связи ни с пультом, ни с ADB.",
     "Can't turn the TV on: neither the remote nor ADB is connected."),
    ("Клавиши пульта в любой программе", "Remote keys in any app"),
    ("Ctrl+Alt+Shift и стрелки, Enter, Backspace, Home — пульт; Page Up / Page Down — громкость; "
     "Пробел — пауза; End — без звука. Работает, даже когда панель закрыта.",
     "Ctrl+Alt+Shift with the arrows, Enter, Backspace or Home control the TV; Page Up / Page Down set the volume; "
     "Space pauses; End mutes. Works even with the panel closed."),
    ("Медиаклавиши клавиатуры управляют ТВ", "Keyboard media keys control the TV"),
    ("Клавиши «Играть/Пауза», «Следующий», «Предыдущий» и «Стоп» на клавиатуре управляют ТВ, "
     "а не плеерами на компьютере.",
     "The keyboard's Play/Pause, Next, Previous and Stop keys control the TV instead of players on the computer."),
    ("Эти сочетания уже заняты другой программой: ", "These shortcuts are already taken by another app: "),
    # scenes
    ("Сцены", "Scenes"), ("Новая сцена…", "New scene…"), ("Изменить", "Edit"), ("Сцен пока нет", "No scenes yet"),
    ("Вечер кино", "Movie night"), ("Сцена «", "Scene “"), ("»: ", "”: "), ("» не найдена.", "” not found."),
    ("» запущена.", "” started."),
    ("ТВ не сообщает громкость — шаг пропущен.", "the TV doesn't report its volume — step skipped."),
    ("ТВ «", "TV “"), ("» не найден — сцена выполнена на текущем ТВ.", "” not found — the scene ran on the current TV."),
    ("Fling — сцена", "Fling — scene"), ("Сцена", "Scene"),
    ("Сцена выполняет шаги по порядку: например, включает ТВ, открывает приложение и ставит громкость. "
     "Запустить её можно из меню «⋯» на панели, из меню значка в трее или командой ",
     "A scene runs its steps in order: for example, it turns on the TV, opens an app and sets the volume. "
     "Run it from the “⋯” menu on the panel, from the tray icon menu or with the command "),
    ("Название", "Name"), ("Сначала переключиться на ТВ: ", "Switch to this TV first: "),
    ("Добавить шаг", "Add a step"), ("Выше", "Up"), ("Ниже", "Down"), ("Удалить шаг", "Delete step"),
    ("Удалить сцену", "Delete scene"), ("Сохранить", "Save"), ("Запустить", "Run"),
    ("Открыть приложение", "Open an app"), ("Громкость ТВ…", "TV volume…"), ("Кнопка пульта", "Remote button"),
    ("Подождать…", "Wait…"), ("Громкость ТВ, % от максимума:", "TV volume, % of the maximum:"),
    ("Подождать, секунд:", "Wait, seconds:"), ("Открыть: ", "Open: "), ("Громкость ТВ: ", "TV volume: "),
    ("Кнопка: ", "Button: "), ("Подождать ", "Wait "), ("Таймер сна: ", "Sleep timer: "),
    ("Добавьте хотя бы один шаг.", "Add at least one step."), ("Дайте сцене название.", "Give the scene a name."),
    ("Удалить сцену «", "Delete the scene “"), ("»?", "”?"),
    ("Домой", "Home"), ("Назад", "Back"), ("Без звука", "Mute"), ("Play Игры", "Play Games"),
]


def bind(mod) -> None:
    """Called once by fling_app: shared helpers + the English phrases of this module."""
    global F
    if F is not None:
        return
    F = mod
    mod.EN_PHRASES.extend(EN)
    mod.EN_PHRASES.sort(key=lambda kv: -len(kv[0]))
    mod.EN_REGEX.append((re.compile(r"(\d+) мин\b"), r"\1 min"))


def tr(s: str) -> str:
    return F.tr(s)


def _dpr() -> float:
    scr = QGuiApplication.primaryScreen()
    return scr.devicePixelRatio() if scr else 1.0


def icon(name: str, size: int = 16, color: QColor | None = None) -> QIcon:
    """Small drawn icons this module needs and fling_app doesn't have (crisp at any DPI)."""
    dpr = _dpr() * 2
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = QColor(color or F.T.fg)
    s = float(size)
    if name == "more":                               # three dots
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(col)
        r = s * 0.09
        for x in (0.25, 0.5, 0.75):
            p.drawEllipse(QPointF(s * x, s / 2), r, r)
    elif name == "moon":                             # crescent
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(col)
        a = QPainterPath(); a.addEllipse(QRectF(s * .14, s * .14, s * .72, s * .72))
        b = QPainterPath(); b.addEllipse(QRectF(s * .38, s * .02, s * .62, s * .62))
        p.drawPath(a.subtracted(b))
    elif name == "plus":
        pen = QPen(col, s * 0.11); pen.setCapStyle(Qt.PenCapStyle.RoundCap); p.setPen(pen)
        p.drawLine(QPointF(s / 2, s * .25), QPointF(s / 2, s * .75))
        p.drawLine(QPointF(s * .25, s / 2), QPointF(s * .75, s / 2))
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- favourite apps (per TV)

def catalog() -> list[dict]:
    return [{"name": n, "link": l, "pkg": p} for n, l, p in F.TV_APPS]


def label_for(pkg: str) -> str:
    for n, _, p in F.TV_APPS:
        if p == pkg:
            return n
    if pkg in KNOWN_APPS:
        return tr(KNOWN_APPS[pkg])
    parts = [x for x in pkg.split(".") if x.lower() not in _GENERIC]
    tail = parts[-1] if parts else pkg.split(".")[-1]
    return tail[:1].upper() + tail[1:]


def tv_id(s) -> str:
    """Per-TV settings are kept under the TV's name: it survives a new IP from the router."""
    return (s.device or {}).get("name") or s.ip or ""


def fav_apps(s) -> list[dict]:
    got = ((s.conf.get("tv_prefs") or {}).get(tv_id(s)) or {}).get("apps")
    return [dict(a) for a in got] if isinstance(got, list) else catalog()


def set_fav_apps(s, apps: list[dict] | None) -> None:
    prefs = s.conf.setdefault("tv_prefs", {})
    mine = prefs.setdefault(tv_id(s), {})
    if apps is None:
        mine.pop("apps", None)
    else:
        mine["apps"] = [{k: v for k, v in a.items() if k in ("name", "link", "pkg") and v} for a in apps][:MAX_APPS]
    F.save_conf(s.conf)
    s.changed.emit()


def adb_launch_args(app: dict) -> list[list[str]]:
    """ADB ways to open an app, tried in order. Package names are validated: the args go
    through the TV's shell."""
    pkg, link = app.get("pkg") or "", app.get("link") or ""
    out = []
    if PKG_RE.fullmatch(pkg):
        out.append(["shell", "monkey", "-p", pkg, "-c", "android.intent.category.LEANBACK_LAUNCHER", "1"])
        out.append(["shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"])
    elif link and re.fullmatch(r"[A-Za-z][\w+.-]*://[\w\-./?=&%:#~+]*", link):
        out.append(["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", f"'{link}'"])
    return out


def parse_launcher_apps(text: str) -> list[str]:
    """`cmd package query-activities --brief …` / `pm list packages` → package names."""
    pkgs = []
    for line in (text or "").splitlines():
        line = line.strip()
        m = re.match(r"(?:package:)?([A-Za-z][\w.]*\.[\w.]+)(?:/|$)", line)
        if m and PKG_RE.fullmatch(m[1]) and m[1] not in pkgs and m[1] not in _HIDDEN:
            pkgs.append(m[1])
    return pkgs


# ---------------------------------------------------------------- controller: power, sleep timer, scenes, hotkeys

class Remote(QObject):
    """Lives on State (`state.rx`). No polling: timers run only while a sleep timer or a
    scene is running."""

    def __init__(self, state):
        super().__init__()
        self.s = state
        self.sleep_at = None             # monotonic deadline
        self.sleep_ip = None
        self._sleep_t = QTimer(self, singleShot=True)
        self._sleep_t.timeout.connect(self._sleep_tick)
        self._warned = False
        self._tv_apps: dict[str, list[str] | None] = {}     # ip → installed launcher apps (ADB), None = loading
        self._run = None                 # running scene token
        self._hotkeys = None
        state.changed.connect(self._watch_device)
        if F.P.IS_WIN and (state.conf.get("tv_hotkeys") or state.conf.get("tv_media_keys")):
            QTimer.singleShot(0, self.restart_hotkeys)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop_hotkeys)

    # --- keys and apps
    def key(self, code: int):
        if self.s.ip:
            self.s.tv_key(code)

    def launch(self, app: dict):
        s = self.s
        if not s.ip:
            s._set(last_error="Телевизор не выбран — нажмите «Выбрать» внизу панели.")
            return
        if s.tv_paired:
            s.atv.launch(s.ip, app.get("link") or app.get("pkg"))
            return
        if not s.adb_ok():
            s._set(last_error="Открыть приложение можно, когда пульт связан с ТВ или подключён ADB.")
            return
        ip, ways = s.ip, adb_launch_args(app)

        def work():
            for args in ways:
                r = F.adb_cmd(ip, args, timeout=12)
                out = (r.stdout or "") + (r.stderr or "")
                if r.returncode == 0 and "No activities found" not in out and "Error" not in out:
                    return None
            return "приложение не найдено на ТВ"
        s.bg(work, lambda err, e: (e or err) and s._set(last_error=tr("Не удалось открыть приложение: ") + tr(str(e or err))))

    def tv_apps(self, on_done) -> list[str] | None:
        """Launcher apps installed on the TV (ADB). Cached per TV; None while loading."""
        ip = self.s.ip
        if ip in self._tv_apps:
            return self._tv_apps[ip]
        self._tv_apps[ip] = None

        def work():
            r = F.adb_cmd(ip, ["shell", "cmd", "package", "query-activities", "--brief", "-a",
                               "android.intent.action.MAIN", "-c", "android.intent.category.LEANBACK_LAUNCHER"], timeout=15)
            pkgs = parse_launcher_apps(r.stdout) if r.returncode == 0 else []
            if not pkgs:
                r = F.adb_cmd(ip, ["shell", "pm", "list", "packages", "-3"], timeout=15)
                pkgs = parse_launcher_apps(r.stdout) if r.returncode == 0 else []
            return sorted(pkgs, key=lambda p: label_for(p).lower())

        def done(pkgs, e):
            self._tv_apps[ip] = pkgs or []
            if e or not pkgs:
                self._tv_apps.pop(ip, None)      # try again next time
            on_done(pkgs or [], e)
        self.s.bg(work, done)
        return None

    # --- power. POWER toggles, so it is sent only when the TV itself has just reported the
    # opposite state over the remote session; otherwise the one-way WAKEUP / SLEEP keys.
    def tv_on(self):
        s = self.s
        if s.tv_paired and s.ip:
            if s.tv_on is not True:
                s.atv.key(s.ip, K["power"] if s.tv_on is False else K["wakeup"])
        elif s.adb_ok():
            ip = s.ip
            s.bg(lambda: F.adb_cmd(ip, ["shell", "input", "keyevent", "KEYCODE_WAKEUP"], timeout=8))
        else:
            s._set(last_error="Не могу включить ТВ: нет связи ни с пультом, ни с ADB.")
            return False
        return True

    def tv_off(self):
        s = self.s
        if s.tv_paired and s.ip:
            if s.tv_on is not False:
                s.atv.key(s.ip, K["power"] if s.tv_on is True else K["sleep"])
        elif s.adb_ok():
            ip = s.ip
            s.bg(lambda: F.adb_cmd(ip, ["shell", "input", "keyevent", "KEYCODE_SLEEP"], timeout=8))
        else:
            s._set(last_error="Не могу выключить ТВ: нет связи ни с пультом, ни с ADB.")
            return False
        return True

    # --- sleep timer
    @property
    def sleep_left(self) -> float | None:
        return max(0.0, self.sleep_at - time.monotonic()) if self.sleep_at else None

    def set_sleep(self, minutes: int | None, quiet=False):
        if not minutes:
            was = self.sleep_at is not None
            self.sleep_at = self.sleep_ip = None
            self._sleep_t.stop()
            if was and not quiet:
                F.notify_send("Таймер сна отменён.")
            self.s.changed.emit()
            return
        self.sleep_at = time.monotonic() + minutes * 60
        self.sleep_ip = self.s.ip
        self._warned = minutes <= 1
        self._arm()
        if not quiet:
            F.notify_send(f"Таймер сна: ТВ выключится через {minutes} мин")
        self.s.changed.emit()

    def _arm(self):
        left = self.sleep_left
        if left is None:
            return
        if not self._warned and left > 60:
            self._sleep_t.start(int(min(left - 60, 3600) * 1000) + 50)
        else:
            self._sleep_t.start(int(min(left, 3600) * 1000) + 50)

    def _sleep_tick(self):
        left = self.sleep_left
        if left is None:
            return
        if left > 0.5:
            if not self._warned and left <= 61:
                self._warned = True
                F.notify_send("Через минуту ТВ выключится по таймеру сна.")
            self._arm()
            self.s.changed.emit()                 # the "turns off in N min" line
            return
        self.sleep_at = self.sleep_ip = None
        F.notify_send("Таймер сна: выключаю ТВ.")
        self.tv_off()
        self.s.changed.emit()

    def _watch_device(self):
        if self.sleep_at and self.sleep_ip and self.s.ip != self.sleep_ip:
            self.set_sleep(None, quiet=True)
            F.notify_send("Таймер сна отменён: выбран другой ТВ.")

    # --- scenes
    def scenes(self) -> list[dict]:
        got = self.s.conf.get("scenes")
        return [x for x in got if isinstance(x, dict) and x.get("name")] if isinstance(got, list) else []

    def save_scenes(self, scenes: list[dict]):
        self.s.conf["scenes"] = scenes
        F.save_conf(self.s.conf)

    def run_scene_named(self, name: str):
        name = (name or "").strip().strip('"').strip()
        for sc in self.scenes():
            if sc["name"].casefold() == name.casefold():
                return self.run_scene(sc)
        F.notify_send(tr("Сцена «") + name + tr("» не найдена."))

    def run_scene(self, sc: dict):
        token = self._run = object()
        steps = list(sc.get("steps") or [])
        s = self.s
        if not s.panel_visible():
            F.notify_send(tr("Сцена «") + sc["name"] + tr("» запущена."))

        def alive():
            return self._run is token

        def wait_until(cond, timeout, then):
            end = time.monotonic() + timeout
            def poll():
                if not alive():
                    return
                if cond() or time.monotonic() >= end:
                    then()
                else:
                    QTimer.singleShot(250, poll)
            poll()

        def note(msg):
            s._set(last_error=tr("Сцена «") + sc["name"] + tr("»: ") + tr(msg))

        def step(i):
            if not alive() or i >= len(steps):
                if alive():
                    self._run = None
                return
            st = steps[i]
            do = st.get("do")
            nxt = lambda delay=0.4: QTimer.singleShot(int(delay * 1000), lambda: step(i + 1))
            if do == "wake":
                if self.tv_on():
                    # the TV pushes is_on when it is up; a TV known only over ADB gets a few seconds
                    paired = s.tv_paired
                    wait_until(lambda: paired and s.tv_on is True, 12 if paired else 4, lambda: nxt(1.5))
                else:
                    nxt()
            elif do == "off":
                self.tv_off(); nxt(1.0)
            elif do == "app":
                self.launch(st); nxt(2.5)
            elif do == "key" and st.get("key") in K:
                self.key(K[st["key"]]); nxt(0.5)
            elif do == "wait":
                nxt(max(0.0, min(600.0, float(st.get("s", 1) or 0))))
            elif do == "sleep":
                self.set_sleep(int(st.get("min") or 30)); nxt()
            elif do == "volume":
                def set_vol():
                    v = F.tv_volume_known(s)
                    if not v:
                        note("ТВ не сообщает громкость — шаг пропущен.")
                    else:
                        top = int(v["max"])
                        s.atv.set_volume(s.ip, max(0, min(top, round(top * int(st.get("pct", 20)) / 100))))
                    nxt(1.0)
                wait_until(lambda: F.tv_volume_known(s) is not None, 6, set_vol)
            else:
                nxt(0)

        want = sc.get("tv")
        if want and (s.device or {}).get("name") != want:
            d = next((x for x in s.devices if x.get("name") == want), None)
            if d is None:
                s._set(last_error=tr("ТВ «") + want + tr("» не найден — сцена выполнена на текущем ТВ."))
                step(0)
            else:
                s.select_device(d)
                wait_until(lambda: not s.tv_paired or (s.atv.connected and s.atv.host == s.ip), 8, lambda: step(0))
        else:
            step(0)

    def step_text(self, st: dict) -> str:
        do = st.get("do")
        if do == "wake":
            return tr("Включить ТВ")
        if do == "off":
            return tr("Выключить ТВ")
        if do == "app":
            return tr("Открыть: ") + (st.get("name") or st.get("pkg") or st.get("link") or "?")
        if do == "volume":
            return tr("Громкость ТВ: ") + f"{int(st.get('pct', 20))}%"
        if do == "key":
            return tr("Кнопка: ") + tr(dict(SCENE_KEYS).get(st.get("key"), st.get("key") or "?"))
        if do == "wait":
            return tr(f"Подождать {int(st.get('s', 1))} с")
        if do == "sleep":
            return tr(f"Таймер сна: {int(st.get('min') or 30)} мин")
        return str(do)

    # --- system-wide TV keys (Windows, opt-in)
    def restart_hotkeys(self):
        self.stop_hotkeys()
        if not F.P.IS_WIN:
            return
        combos = []
        if self.s.conf.get("tv_hotkeys"):
            m = TvHotkeys.CAS
            combos += [(m, 0x26, "up", True), (m, 0x28, "down", True), (m, 0x25, "left", True),
                       (m, 0x27, "right", True), (m, 0x0D, "ok", False), (m, 0x08, "back", False),
                       (m, 0x24, "home", False), (m, 0x21, "vol_up", True), (m, 0x22, "vol_down", True),
                       (m, 0x20, "play_pause", False), (m, 0x23, "mute", False)]
        if self.s.conf.get("tv_media_keys"):
            combos += [(0, 0xB3, "play_pause", False), (0, 0xB0, "next", False),
                       (0, 0xB1, "prev", False), (0, 0xB2, "stop", False)]
        if not combos:
            return
        self._hotkeys = TvHotkeys(combos, lambda a: self.s.bridge.call.emit(lambda: self.hotkey(a)))
        failed = self._hotkeys.start()
        if failed:
            self.s._set(last_error=tr("Эти сочетания уже заняты другой программой: ") + ", ".join(failed))

    def stop_hotkeys(self):
        if self._hotkeys:
            self._hotkeys.stop()
            self._hotkeys = None

    def hotkey(self, action: str):
        s = self.s
        casting = s.panel == "casting"
        if action == "play_pause" and (casting or not s.remote_ready):
            s.toggle_play() if casting else None
        elif action in ("next", "prev") and s.yt_live:
            s.yt_next() if action == "next" else s.yt_prev()
        elif action in ("vol_up", "vol_down") and not s.remote_ready:
            s.set_volume(s.volume + (5 if action == "vol_up" else -5))
        elif s.remote_ready and action in K:
            s.tv_key(K[action])


class TvHotkeys:
    """Windows RegisterHotKey for several keys on one private message-loop thread (not a
    keyboard hook: Windows hands us only these combinations)."""
    MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 1, 2, 4, 0x4000
    CAS = MOD_CONTROL | MOD_ALT | MOD_SHIFT
    NAMES = {0x26: "↑", 0x28: "↓", 0x25: "←", 0x27: "→", 0x0D: "Enter", 0x08: "Backspace", 0x24: "Home",
             0x21: "Page Up", 0x22: "Page Down", 0x20: "Space", 0x23: "End", 0xB3: "Play/Pause",
             0xB0: "Next", 0xB1: "Previous", 0xB2: "Stop"}

    def __init__(self, combos, on_action):
        self.combos, self.on_action = combos, on_action
        self._tid = None
        self._failed: list[str] = []
        self._ready = threading.Event()

    def start(self) -> list[str]:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="fling-tv-keys")
        self._thread.start()
        self._ready.wait(2)
        return self._failed

    def _loop(self):
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        ids = {}
        for i, (mods, vk, action, repeat) in enumerate(self.combos, start=100):
            if user32.RegisterHotKey(None, i, mods | (0 if repeat else self.MOD_NOREPEAT), vk):
                ids[i] = action
            else:
                self._failed.append(("Ctrl+Alt+Shift+" if mods else "") + self.NAMES.get(vk, hex(vk)))
        self._ready.set()
        msg = wintypes.MSG()
        while ids and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312 and msg.wParam in ids:      # WM_HOTKEY
                try:
                    self.on_action(ids[msg.wParam])
                except Exception as e:
                    print("tv keys:", e, flush=True)
        for i in ids:
            user32.UnregisterHotKey(None, i)

    def stop(self):
        if self._tid:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)    # WM_QUIT
            self._thread.join(1)          # its keys are free again before a new set registers them


def controller(state) -> Remote:
    rx = getattr(state, "rx", None)
    if rx is None:
        rx = state.rx = Remote(state)
    return rx


SCENE_KEYS = (("home", "Домой"), ("back", "Назад"), ("ok", "OK"), ("play_pause", "Пауза / Играть"),
              ("mute", "Без звука"), ("input", "Источник"), ("settings", "Настройки ТВ"))


# ---------------------------------------------------------------- menus (panel "⋯" and the tray)

def fill_more_menu(m: QMenu, state, panel, tray=False):
    rx = controller(state)
    m.setToolTipsVisible(True)
    sm = m.addMenu(tr("Сцены"))
    fill_scenes_menu(sm, state, panel)
    tm = m.addMenu(tr("Таймер сна"))
    left = rx.sleep_left
    for mins in SLEEP_CHOICES:
        tm.addAction(tr(f"{mins} мин"), lambda n=mins: rx.set_sleep(n))
    if left is not None:
        tm.addSeparator()
        tm.addAction(tr("Отменить таймер"), lambda: rx.set_sleep(None))
    if not tray:
        m.addSeparator()
        m.addAction(tr("Включить ТВ"), rx.tv_on)
        m.addAction(tr("Выключить ТВ"), rx.tv_off)
    if F.P.IS_WIN:
        m.addSeparator()
        for key, title, tip in (
                ("tv_hotkeys", "Клавиши пульта в любой программе",
                 "Ctrl+Alt+Shift и стрелки, Enter, Backspace, Home — пульт; Page Up / Page Down — громкость; "
                 "Пробел — пауза; End — без звука. Работает, даже когда панель закрыта."),
                ("tv_media_keys", "Медиаклавиши клавиатуры управляют ТВ",
                 "Клавиши «Играть/Пауза», «Следующий», «Предыдущий» и «Стоп» на клавиатуре управляют ТВ, "
                 "а не плеерами на компьютере.")):
            a = m.addAction(tr(title), lambda k=key: _toggle_conf(state, k))
            a.setCheckable(True); a.setChecked(bool(state.conf.get(key)))
            a.setToolTip(tr(tip))


def _toggle_conf(state, key):
    state.conf[key] = not state.conf.get(key)
    F.save_conf(state.conf)
    controller(state).restart_hotkeys()


def fill_scenes_menu(sm: QMenu, state, panel):
    rx = controller(state)
    scenes = rx.scenes()
    for sc in scenes:
        sm.addAction(sc["name"], lambda sc=sc: rx.run_scene(sc))
    if not scenes:
        sm.addAction(tr("Сцен пока нет")).setEnabled(False)
    sm.addSeparator()
    sm.addAction(tr("Новая сцена…"), lambda: open_scene_editor(state, panel, None))
    if scenes:
        em = sm.addMenu(tr("Изменить"))
        for i, sc in enumerate(scenes):
            em.addAction(sc["name"], lambda i=i: open_scene_editor(state, panel, i))


def tray_menu(m: QMenu, state, panel):
    """Scenes and the sleep timer in the tray menu: no need to open the panel at night."""
    rx = controller(state)
    sm = m.addMenu(tr("Сцены"))
    fill_scenes_menu(sm, state, panel)
    left = rx.sleep_left
    tm = m.addMenu(tr("Таймер сна") + (" — " + tr(f"{max(1, math.ceil(left / 60))} мин") if left is not None else ""))
    for mins in SLEEP_CHOICES:
        tm.addAction(tr(f"{mins} мин"), lambda n=mins: rx.set_sleep(n))
    if rx.sleep_left is not None:
        tm.addSeparator()
        tm.addAction(tr("Отменить таймер"), lambda: rx.set_sleep(None))


def more_button(panel) -> QPushButton:
    """The "⋯" key in the remote header: scenes, sleep timer, turn on / off."""
    b = F.KeyButton(text=" ", size=26)
    b.setText("")
    b.setIcon(icon("more", 16, F.T.fg))
    b.setIconSize(QSize(16, 16))
    b.setToolTip("Ещё: сцены, таймер сна, включение")

    def show():
        m = QMenu(panel)
        fill_more_menu(m, panel.state, panel)
        m.exec(b.mapToGlobal(b.rect().bottomLeft()))
    b.clicked.connect(show)
    return b


class SleepRow(QWidget):
    """"Sleep timer: the TV turns off in 25 min  [Cancel]" under the remote header."""

    def __init__(self, state):
        super().__init__()
        self.s = state
        self.row = QWidget()
        self.row.setObjectName("sleeprow")
        self.row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.row.setStyleSheet(f"QWidget#sleeprow{{background:{F.Theme.css(F.T.acc_a(0.12))};border-radius:6px}}")
        h = QHBoxLayout(self.row); h.setContentsMargins(8, 3, 4, 3); h.setSpacing(6)
        moon = QLabel(); moon.setPixmap(icon("moon", 12, F.T.accent).pixmap(12, 12)); moon.setFixedSize(12, 12)
        self.text = F.label("", 10.5, F.T.accent, QFont.Weight.DemiBold)
        cancel = QPushButton()
        cancel.setText("Отменить")
        cancel.setFont(F.font(10.5, QFont.Weight.DemiBold))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        cancel.setFixedHeight(22)
        cancel.setStyleSheet(
            f"QPushButton{{border:none;border-radius:5px;padding:0 8px;color:{F.Theme.css(F.T.accent)};"
            f"background:{F.Theme.css(F.T.fg_a(0.07))}}}"
            f"QPushButton:hover{{background:{F.Theme.css(F.T.acc_a(0.22))}}}")
        cancel.clicked.connect(lambda: controller(state).set_sleep(None))
        h.addWidget(moon, 0, Qt.AlignmentFlag.AlignVCenter); h.addWidget(self.text, 1); h.addWidget(cancel)
        v = QVBoxLayout(self); v.setContentsMargins(10, 0, 10, 6); v.addWidget(self.row)
        self.hide()

    def sync(self):
        left = controller(self.s).sleep_left
        if left is None:
            self.setVisible(False)
            return
        self.text.setText(f"Таймер сна: ТВ выключится через {max(1, math.ceil(left / 60))} мин")
        self.setVisible(True)


# ---------------------------------------------------------------- the favourite-apps grid

class AppGrid(QWidget):
    """The app chips of the remote: the TV's favourites (pinned / custom) plus a "+" chip."""
    COLS = 4

    def __init__(self, state, panel):
        super().__init__()
        self.s, self.panel = state, panel
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(12, 0, 12, 8)
        self.grid.setSpacing(4)
        for c in range(self.COLS):
            self.grid.setColumnStretch(c, 1)
        self.chips: list = []
        self._key = None
        self._active = object()
        self.sync()

    def _build(self, apps):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)        # gone from the panel now, freed on the next loop turn
                w.deleteLater()
        self.chips = []
        fm = QFontMetrics(F.font(10.5, QFont.Weight.DemiBold))
        for i, a in enumerate(apps):
            name = a.get("name") or label_for(a.get("pkg") or "")
            ch = F.Chip(fm.elidedText(name, Qt.TextElideMode.ElideRight, 66), lambda _=False, a=a: controller(self.s).launch(a))
            ch.pkg = a.get("pkg")
            ch.setToolTip(name + "\n" + tr("Щелчок — открыть на ТВ, правая кнопка — изменить"))
            ch.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            ch.customContextMenuRequested.connect(lambda pos, i=i, ch=ch: self._chip_menu(i, ch.mapToGlobal(pos)))
            self.chips.append(ch)
            self.grid.addWidget(ch, i // self.COLS, i % self.COLS)
        plus = F.Chip("", lambda: None)
        plus.setIcon(icon("plus", 12, F.T.fg_a(0.7)))
        plus.setIconSize(QSize(12, 12))
        plus.setToolTip("Добавить приложение")
        plus.clicked.disconnect()
        plus.clicked.connect(lambda: self._add_menu(plus.mapToGlobal(plus.rect().bottomLeft())))
        self.plus = plus
        n = len(apps)
        self.grid.addWidget(plus, n // self.COLS, n % self.COLS)
        self._active = object()

    def sync(self):
        apps = fav_apps(self.s)
        key = (tv_id(self.s), tuple((a.get("name"), a.get("pkg"), a.get("link")) for a in apps))
        if key != self._key:
            self._key = key
            self._build(apps)
        if self._active != self.s.tv_app:
            self._active = self.s.tv_app
            for ch in self.chips:
                ch.set_active(bool(ch.pkg) and ch.pkg == self.s.tv_app)

    # --- editing
    def _chip_menu(self, i, at):
        apps = fav_apps(self.s)
        m = QMenu(self.panel)
        a = m.addAction(tr("Сдвинуть влево"), lambda: self._move(i, -1)); a.setEnabled(i > 0)
        a = m.addAction(tr("Сдвинуть вправо"), lambda: self._move(i, 1)); a.setEnabled(i < len(apps) - 1)
        m.addAction(tr("Переименовать…"), lambda: self._rename(i))
        m.addSeparator()
        m.addAction(tr("Убрать"), lambda: self._remove(i))
        m.exec(at)

    def _move(self, i, d):
        apps = fav_apps(self.s)
        j = i + d
        if 0 <= i < len(apps) and 0 <= j < len(apps):
            apps[i], apps[j] = apps[j], apps[i]
            set_fav_apps(self.s, apps)

    def _remove(self, i):
        apps = fav_apps(self.s)
        if 0 <= i < len(apps):
            del apps[i]
            set_fav_apps(self.s, apps)

    def _ask(self, prompt, text=""):
        """Text input over the popup panel: hide it, ask, bring it back."""
        self.panel.hide()
        val, ok = QInputDialog.getText(None, tr("Fling — приложение"), tr(prompt), text=text)
        QTimer.singleShot(0, self.panel.show_panel)
        return val.strip() if ok else None

    def _rename(self, i):
        apps = fav_apps(self.s)
        if not 0 <= i < len(apps):
            return
        name = self._ask("Название на кнопке:", apps[i].get("name") or "")
        if name:
            apps[i]["name"] = name[:40]
            set_fav_apps(self.s, apps)

    def _add(self, app: dict):
        apps = fav_apps(self.s)
        if len(apps) >= MAX_APPS:
            self.s._set(last_error="Мест больше нет — уберите какое-нибудь приложение")
            return
        if any(a.get("pkg") == app.get("pkg") for a in apps if app.get("pkg")):
            return
        set_fav_apps(self.s, apps + [app])

    def _add_package(self):
        pkg = self._ask("Имя пакета приложения (например, org.videolan.vlc):")
        if not pkg:
            return
        if not PKG_RE.fullmatch(pkg):
            self.s._set(last_error="Это не похоже на имя пакета Android.")
            return
        self._add({"name": label_for(pkg), "pkg": pkg})

    def _add_menu(self, at):
        s, rx = self.s, controller(self.s)
        apps = fav_apps(s)
        have = {a.get("pkg") for a in apps}
        m = QMenu(self.panel)
        full = len(apps) >= MAX_APPS
        if full:
            m.addAction(tr("Мест больше нет — уберите какое-нибудь приложение")).setEnabled(False)
            m.addSeparator()
        if s.tv_app and s.tv_app not in have and s.tv_app not in _HIDDEN:
            a = m.addAction(tr("Открытое на ТВ: ") + label_for(s.tv_app),
                            lambda p=s.tv_app: self._add({"name": label_for(p), "pkg": p}))
            a.setEnabled(not full)
            m.addSeparator()
        for c in catalog():
            if c["pkg"] not in have:
                m.addAction(c["name"], lambda c=c: self._add(c)).setEnabled(not full)
        tv = m.addMenu(tr("Приложения на ТВ"))

        def loaded(pkgs, err):
            try:
                fill(pkgs, err)              # the menu may still be open: fill it in place
            except RuntimeError:             # …or already gone
                pass

        def fill(pkgs=None, err=None):
            tv.clear()
            if not s.adb_ok():
                tv.addAction(tr("Нужен ADB — «Подключение ТВ…»")).setEnabled(False)
                return
            if pkgs is None:
                pkgs = rx.tv_apps(loaded)
            if pkgs is None:
                tv.addAction(tr("Загружаю список…")).setEnabled(False)
                return
            if err or not pkgs:
                tv.addAction(tr("Не удалось получить список")).setEnabled(False)
                return
            free = [p for p in pkgs if p not in have]
            if not free:
                tv.addAction(tr("Все приложения уже добавлены")).setEnabled(False)
            for p in free:
                tv.addAction(label_for(p), lambda p=p: self._add({"name": label_for(p), "pkg": p})).setEnabled(not full)
        tv.aboutToShow.connect(lambda: fill())
        m.addAction(tr("Указать пакет…"), self._add_package).setEnabled(not full)
        m.addSeparator()
        m.addAction(tr("Вернуть стандартный набор"), lambda: set_fav_apps(s, None))
        m.exec(at)


# ---------------------------------------------------------------- modes: arrows · touchpad · numbers · media

class TouchPad(QWidget):
    """Drag like on a laptop touchpad: every STEP pixels along the main direction is one
    arrow press; a click without movement is OK; right button = Back, middle = Home."""
    STEP = 34

    def __init__(self, send, w=200, h=140):
        super().__init__()
        self.send = send                       # send(name) → TV key
        self.setFixedSize(w, h)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Тачпад: ведите — стрелки, щелчок — OK, правая кнопка — Назад, средняя — Домой")
        self._start = self._last = None
        self._acc = [0.0, 0.0]
        self._moved = False
        self._t0 = 0.0
        self._flash = None                     # (direction, monotonic)

    def mousePressEvent(self, e):
        b = e.button()
        if b == Qt.MouseButton.RightButton:
            self._fire("back"); return
        if b == Qt.MouseButton.MiddleButton:
            self._fire("home"); return
        if b != Qt.MouseButton.LeftButton:
            return
        self._start = self._last = e.position()
        self._acc = [0.0, 0.0]
        self._moved = False
        self._t0 = time.monotonic()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.update()

    def mouseMoveEvent(self, e):
        if self._last is None:
            return
        p = e.position()
        self._acc[0] += p.x() - self._last.x()
        self._acc[1] += p.y() - self._last.y()
        self._last = p
        ax, ay = self._acc
        # one axis at a time, like a d-pad; the other axis' drift is dropped
        if abs(ax) >= self.STEP and abs(ax) >= abs(ay):
            self._fire("right" if ax > 0 else "left")
            self._acc = [ax - math.copysign(self.STEP, ax), 0.0]
        elif abs(ay) >= self.STEP:
            self._fire("down" if ay > 0 else "up")
            self._acc = [0.0, ay - math.copysign(self.STEP, ay)]
        if (p - self._start).manhattanLength() > 6:
            self._moved = True
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        if not self._moved and time.monotonic() - self._t0 < 0.6:
            self._fire("ok")
        self._start = self._last = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.update()

    def _fire(self, name):
        self.send(name)
        self._flash = (name, time.monotonic())
        self.update()
        QTimer.singleShot(180, self.update)

    def paintEvent(self, e):
        T = F.T
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(T.fg_a(0.08), 1))
        p.setBrush(T.acc_a(0.07) if self._start is not None else T.fg_a(0.045))
        p.drawRoundedRect(r, 14, 14)
        fl = self._flash
        if fl and time.monotonic() - fl[1] < 0.17:      # a short glow on the edge that was "pressed"
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(T.acc_a(0.35))
            w, h = self.width(), self.height()
            edge = {"up": QRectF(w * .3, 4, w * .4, 4), "down": QRectF(w * .3, h - 8, w * .4, 4),
                    "left": QRectF(4, h * .3, 4, h * .4), "right": QRectF(w - 8, h * .3, 4, h * .4)}.get(fl[0])
            if edge is not None:
                p.drawRoundedRect(edge, 2, 2)
            elif fl[0] == "ok":
                p.drawEllipse(QPointF(w / 2, h / 2), 16, 16)
        if self._start is None:
            p.setPen(T.fg_a(0.4))
            p.setFont(F.font(10.5))
            p.drawText(r.adjusted(10, 10, -10, -10), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       tr("Ведите по панели — стрелки\nЩелчок — OK, правая кнопка — Назад"))
        elif self._last is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(T.acc_a(0.5))
            p.drawEllipse(self._last, 7, 7)
        p.end()


def _text_key(text, tip, on, w=None, h=26):
    """A rounded key with a word on it (the Pill look without an icon)."""
    b = QPushButton()
    b.setText(text)
    b.setToolTip(tip or "")
    b.setFont(F.font(11))
    b.setFixedHeight(h)
    if w:
        b.setFixedWidth(w)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    T = F.T
    b.setStyleSheet(
        f"QPushButton{{border:none;border-radius:6px;padding:0 8px;background:{F.Theme.css(T.fg_a(0.07))};"
        f"color:{F.Theme.css(T.fg)}}}"
        f"QPushButton:hover{{background:{F.Theme.css(T.acc_a(0.22))}}}"
        f"QPushButton:pressed{{background:{F.Theme.css(T.acc_a(0.35))}}}")
    b.clicked.connect(on)
    return b


def _color_key(color: str, tip: str, on):
    b = QPushButton()
    b.setFixedSize(30, 14)
    b.setToolTip(tip)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    c = QColor(color)
    hov = QColor(c); hov = hov.lighter(125)
    b.setStyleSheet(f"QPushButton{{border:none;border-radius:7px;background:{c.name()}}}"
                    f"QPushButton:hover{{background:{hov.name()}}}")
    b.clicked.connect(on)
    return b


class ModeStack(QWidget):
    """Tabs (Arrows · Touchpad · Numbers · Media) over pages of the same height, so the
    panel doesn't jump when switching. The choice is remembered."""

    def __init__(self, state, dpad: QWidget):
        super().__init__()
        self.s = state
        rx = controller(state)
        key = lambda n: (lambda: rx.key(K[n]))
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        # a segmented control, so it doesn't read as one more row of app chips
        self.tabs_box = QWidget()
        ol = QHBoxLayout(self.tabs_box); ol.setContentsMargins(40, 4, 40, 0); ol.setSpacing(0)
        seg = QWidget()
        seg.setObjectName("modeseg")
        seg.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        seg.setStyleSheet(f"QWidget#modeseg{{background:{F.Theme.css(F.T.fg_a(0.06))};border-radius:7px}}")
        tl = QHBoxLayout(seg); tl.setContentsMargins(2, 2, 2, 2); tl.setSpacing(2)
        self.tabs = {}
        for mode, title in MODES:
            c = QPushButton()
            c.setText(title)
            c.setFixedHeight(20)
            c.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            c.setCursor(Qt.CursorShape.PointingHandCursor)
            c.clicked.connect(lambda _=False, m=mode: self.set_mode(m))
            self.tabs[mode] = c
            tl.addWidget(c, 1)
        ol.addWidget(seg)
        v.addWidget(self.tabs_box)
        self.stack = QStackedWidget()
        v.addWidget(self.stack)
        self.pages = {"dpad": dpad, "touch": self._touch_page(key), "num": self._num_page(key),
                      "media": self._media_page(key)}
        for m, _ in MODES:
            self.stack.addWidget(self.pages[m])
        self.mode = None
        self.set_mode(state.conf.get("remote_mode", "dpad"), save=False)

    def set_mode(self, mode, save=True):
        if mode not in self.pages:
            mode = "dpad"
        if mode == self.mode:
            return
        self.mode = mode
        self.stack.setCurrentWidget(self.pages[mode])
        T, css = F.T, F.Theme.css
        for m, c in self.tabs.items():
            on = m == mode
            c.setFont(F.font(10.5, QFont.Weight.DemiBold if on else QFont.Weight.Normal))
            c.setStyleSheet(
                f"QPushButton{{border:none;border-radius:5px;padding:0 6px;"
                f"background:{css(T.acc_a(0.2) if on else QColor(0, 0, 0, 0))};"
                f"color:{css(T.accent if on else T.fg_a(0.75))}}}"
                + ("" if on else f"QPushButton:hover{{background:{css(T.fg_a(0.07))}}}"))
        if save and self.s.conf.get("remote_mode") != mode:
            self.s.conf["remote_mode"] = mode
            F.save_conf(self.s.conf)
        if save:
            self.s.changed.emit()        # the panel re-reads which keys the keyboard hook takes

    def sync(self, bt_only=False):
        """Bluetooth-only remote: just the arrows (it knows only the basic keys)."""
        self.tabs_box.setVisible(not bt_only)
        if bt_only and self.mode != "dpad":
            self.stack.setCurrentWidget(self.pages["dpad"])
        elif not bt_only:
            self.stack.setCurrentWidget(self.pages[self.mode])

    def _touch_page(self, key):
        w = QWidget()
        row = QHBoxLayout(w); row.setContentsMargins(12, 8, 12, 4); row.setSpacing(6)
        back = F.KeyButton("back", size=32, on=key("back")); back.setToolTip("Назад  (Backspace / Delete)")
        home = F.KeyButton("home", size=32, on=key("home")); home.setToolTip("Домой  (Home / H)")
        bottom = Qt.AlignmentFlag.AlignBottom
        row.addStretch(1)
        row.addWidget(back, 0, bottom)
        row.addWidget(TouchPad(lambda n: controller(self.s).key(K[n])))
        row.addWidget(home, 0, bottom)
        row.addStretch(1)
        return w

    def _num_page(self, key):
        w = QWidget()
        row = QHBoxLayout(w); row.setContentsMargins(12, 8, 12, 4); row.setSpacing(14)
        g = QGridLayout(); g.setSpacing(5)
        for i, d in enumerate("123456789"):
            g.addWidget(F.KeyButton(text=d, size=30, on=key(d)), i // 3, i % 3)
        back = F.KeyButton("back", size=30, on=key("back")); back.setToolTip("Назад")
        ok = F.KeyButton(text="OK", size=30, accented=True, on=key("ok")); ok.setToolTip("OK")
        g.addWidget(back, 3, 0); g.addWidget(F.KeyButton(text="0", size=30, on=key("0")), 3, 1); g.addWidget(ok, 3, 2)
        row.addStretch(1)
        row.addLayout(g)
        ch = QVBoxLayout(); ch.setSpacing(5)
        up = F.KeyButton("up", size=30, on=key("ch_up"), repeat=True); up.setToolTip("Канал +")
        dn = F.KeyButton("down", size=30, on=key("ch_down"), repeat=True); dn.setToolTip("Канал −")
        lab = F.label("CH", 10, F.T.fg_a(0.5), QFont.Weight.Bold)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ch.addStretch(1); ch.addWidget(up); ch.addWidget(lab); ch.addWidget(dn); ch.addStretch(1)
        row.addLayout(ch)
        fn = QVBoxLayout(); fn.setSpacing(5)
        for title, tip, name in (("Источник", "Источник сигнала (HDMI, антенна…)", "input"),
                                 ("Телегид", "Телепрограмма", "guide"),
                                 ("Инфо", "Сведения о передаче", "info"),
                                 ("Настройки", "Настройки ТВ", "settings")):
            fn.addWidget(_text_key(title, tip, key(name), w=96, h=30))
        row.addLayout(fn)
        row.addStretch(1)
        return w

    def _media_page(self, key):
        s = self.s
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(12, 10, 12, 4); v.setSpacing(10)
        r1 = QHBoxLayout(); r1.setSpacing(10)
        play = lambda: s.toggle_play() if s.panel == "casting" else controller(s).key(K["play_pause"])
        r1.addStretch(1)
        for g, tip, fn, size, acc in (("prev", "Предыдущий трек", key("prev"), 30, False),
                                      ("rew", "Перемотка назад", key("rewind"), 34, False),
                                      ("pause", "Пауза / Играть", play, 44, True),
                                      ("fwd", "Перемотка вперёд", key("ffwd"), 34, False),
                                      ("next", "Следующий трек", key("next"), 30, False)):
            b = F.KeyButton(g, size=size, accented=acc, on=fn); b.setToolTip(tip)
            r1.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        r1.addStretch(1)
        v.addLayout(r1)
        r2 = QHBoxLayout(); r2.setSpacing(6)
        stop = F.KeyButton("stop", size=28, on=key("stop")); stop.setToolTip("Стоп")
        r2.addStretch(1)
        r2.addWidget(stop)
        r2.addWidget(_text_key("Субтитры", "", key("captions"), h=28))
        r2.addWidget(_text_key("Звуковая дорожка", "", key("audio"), h=28))
        r2.addStretch(1)
        v.addLayout(r2)
        r3 = QHBoxLayout(); r3.setSpacing(8)
        r3.addStretch(1)
        for color, tip, name in (("#e5483f", "Красная кнопка", "red"), ("#34b55a", "Зелёная кнопка", "green"),
                                 ("#e8c22e", "Жёлтая кнопка", "yellow"), ("#3a7be0", "Синяя кнопка", "blue")):
            r3.addWidget(_color_key(color, tip, key(name)))
        r3.addStretch(1)
        v.addLayout(r3)
        hint = F.label("Цветные кнопки — для телеканалов и телетекста", 9.5, F.T.fg_a(0.4))
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(hint)
        v.addStretch(1)
        return w


# ---------------------------------------------------------------- Numbers mode: the laptop's digit keys type digits

DIGIT_VK = {**{0x30 + i: str(i) for i in range(10)}, **{0x60 + i: str(i) for i in range(10)}}   # top row, numpad


def numbers_mode(panel) -> bool:
    m = getattr(panel, "modes", None)
    return bool(m and m.mode == "num" and not m.tabs_box.isHidden() and panel.state.remote_ready)


def digit_key(panel, key: int) -> bool:
    """Qt key → TV digit while the Numbers page is open (otherwise 8/4/6/2 stay arrows)."""
    k0 = Qt.Key.Key_0.value
    if numbers_mode(panel) and k0 <= int(key) <= k0 + 9:
        panel.state.tv_key(K[str(int(key) - k0)])
        return True
    return False


def hook_digit(panel, vk: int) -> bool:
    """The same for the Windows keyboard hook (virtual-key codes)."""
    if vk in DIGIT_VK and numbers_mode(panel):
        panel.state.tv_key(K[DIGIT_VK[vk]])
        return True
    return False


# ---------------------------------------------------------------- scene editor

def open_scene_editor(state, panel, index):
    if panel is not None and panel.isVisible():
        panel.hide()
    w = getattr(state, "_scene_editor", None)
    if w is not None:
        w.close()
    w = state._scene_editor = SceneEditor(state, index)
    w.show(); w.raise_(); w.activateWindow()


class SceneEditor(QWidget):
    def __init__(self, state, index=None):
        super().__init__(None, Qt.WindowType.Window)
        self.s, self.rx, self.index = state, controller(state), index
        T = F.T
        scenes = self.rx.scenes()
        if index is not None and 0 <= index < len(scenes):
            sc = dict(scenes[index])
        else:
            self.index = None
            sc = self._template()
        self.steps = [dict(x) for x in sc.get("steps") or []]
        self.setWindowTitle(tr("Fling — сцена"))
        self.setMinimumWidth(440)
        pal = self.palette(); pal.setColor(self.backgroundRole(), T.bg); self.setPalette(pal)
        self.setAutoFillBackground(True)
        v = QVBoxLayout(self); v.setContentsMargins(18, 16, 18, 16); v.setSpacing(10)
        v.addWidget(F.label("Сцена", 17, weight=QFont.Weight.Bold))
        hint = F.label("Сцена выполняет шаги по порядку: например, включает ТВ, открывает приложение и ставит громкость. "
                       "Запустить её можно из меню «⋯» на панели, из меню значка в трее или командой ",
                       11.5, T.fg_a(0.6), wrap=True)
        hint.setText(hint.text() + "fling scene \"…\".")
        v.addWidget(hint)
        v.addWidget(F.label("Название", 11, T.fg_a(0.6), QFont.Weight.DemiBold))
        self.name = F.field("Вечер кино")
        self.name.setFixedHeight(28)
        self.name.setText(sc.get("name") or "")
        v.addWidget(self.name)
        self.tv = QCheckBox()
        cur = (state.device or {}).get("name")
        self.tv.setText(tr("Сначала переключиться на ТВ: ") + (sc.get("tv") or cur or "—"))
        self.tv.setChecked(bool(sc.get("tv")))
        self.tv.setEnabled(bool(sc.get("tv") or cur))
        self.tv.setFont(F.font(11.5))
        self.tv.setStyleSheet(f"QCheckBox{{color:{F.Theme.css(T.fg)};spacing:8px}}"
                              f"QCheckBox::indicator{{width:13px;height:13px;border-radius:4px;"
                              f"border:1px solid {F.Theme.css(T.fg_a(0.35))};background:{F.Theme.css(T.fg_a(0.06))}}}"
                              f"QCheckBox::indicator:checked{{background:{F.Theme.css(T.accent)};"
                              f"border:1px solid {F.Theme.css(T.accent)}}}")
        self._tv_name = sc.get("tv") or cur
        self.tv.setVisible(len(state.devices) > 1 or bool(sc.get("tv")))
        v.addWidget(self.tv)
        self.list = QListWidget()
        self.list.setFont(F.font(12))
        self.list.setMinimumHeight(170)
        self.list.setStyleSheet(
            f"QListWidget{{background:{F.Theme.css(T.fg_a(0.05))};border:none;border-radius:8px;padding:4px;"
            f"color:{F.Theme.css(T.fg)}}}"
            f"QListWidget::item{{padding:5px 6px;border-radius:5px}}"
            f"QListWidget::item:selected{{background:{F.Theme.css(T.acc_a(0.25))};color:{F.Theme.css(T.fg)}}}")
        v.addWidget(self.list, 1)
        row = QHBoxLayout(); row.setSpacing(6)
        add = F.small_button("Добавить шаг", lambda: None)
        add.setText(tr("Добавить шаг"))
        add_menu = QMenu(add)
        add_menu.aboutToShow.connect(lambda: self._fill_add(add_menu))
        add.setMenu(add_menu)
        row.addWidget(add)
        row.addStretch(1)
        self.up = F.small_button("Выше", lambda: self._move(-1))
        self.down = F.small_button("Ниже", lambda: self._move(1))
        self.rm = F.small_button("Удалить шаг", self._remove)
        row.addWidget(self.up); row.addWidget(self.down); row.addWidget(self.rm)
        v.addLayout(row)
        self.msg = F.label("", 11, T.red, wrap=True)
        self.msg.hide()
        v.addWidget(self.msg)
        bottom = QHBoxLayout(); bottom.setSpacing(6)
        if self.index is not None:
            bottom.addWidget(F.small_button("Удалить сцену", self._delete))
        bottom.addStretch(1)
        bottom.addWidget(F.small_button("Запустить", self._run))
        bottom.addWidget(F.small_button("Отмена", self.close))
        save = F.small_button("Сохранить", self._save)
        save.setStyleSheet(save.styleSheet() + f"QPushButton{{background:{F.Theme.css(T.accent)};color:white;border:none}}"
                           f"QPushButton:hover{{background:{F.Theme.css(T.acc_a(0.85))}}}")
        bottom.addWidget(save)
        v.addLayout(bottom)
        self.list.currentRowChanged.connect(lambda _: self._buttons())
        self._refresh()
        self.resize(460, 520)

    def _template(self) -> dict:
        s = self.s
        apps = fav_apps(s)
        app = next((a for a in apps if a.get("pkg") == s.tv_app), apps[0] if apps else None)
        steps = [{"do": "wake"}]
        if app:
            steps.append({"do": "app", **{k: app[k] for k in ("name", "link", "pkg") if app.get(k)}})
        steps.append({"do": "volume", "pct": 20})
        return {"name": tr("Вечер кино"), "steps": steps}

    def _refresh(self, select=None):
        self.list.clear()
        for i, st in enumerate(self.steps, 1):
            self.list.addItem(f"{i}.  {self.rx.step_text(st)}")
        if select is not None and 0 <= select < len(self.steps):
            self.list.setCurrentRow(select)
        self._buttons()

    def _buttons(self):
        r = self.list.currentRow()
        self.up.setEnabled(r > 0)
        self.down.setEnabled(0 <= r < len(self.steps) - 1)
        self.rm.setEnabled(r >= 0)

    def _insert(self, st):
        r = self.list.currentRow()
        at = r + 1 if r >= 0 else len(self.steps)
        self.steps.insert(at, st)
        self._refresh(at)

    def _fill_add(self, m: QMenu):
        m.clear()
        m.addAction(tr("Включить ТВ"), lambda: self._insert({"do": "wake"}))
        am = m.addMenu(tr("Открыть приложение"))
        seen = set()
        for a in fav_apps(self.s) + catalog():
            k = a.get("pkg") or a.get("link")
            if k in seen:
                continue
            seen.add(k)
            am.addAction(a.get("name") or label_for(a.get("pkg") or ""),
                         lambda a=a: self._insert({"do": "app", **{x: a[x] for x in ("name", "link", "pkg") if a.get(x)}}))
        m.addAction(tr("Громкость ТВ…"), self._ask_volume)
        km = m.addMenu(tr("Кнопка пульта"))
        for k, title in SCENE_KEYS:
            km.addAction(tr(title), lambda k=k: self._insert({"do": "key", "key": k}))
        m.addAction(tr("Подождать…"), self._ask_wait)
        tm = m.addMenu(tr("Таймер сна"))
        for mins in SLEEP_CHOICES:
            tm.addAction(tr(f"{mins} мин"), lambda n=mins: self._insert({"do": "sleep", "min": n}))
        m.addAction(tr("Выключить ТВ"), lambda: self._insert({"do": "off"}))

    def _ask_volume(self):
        n, ok = QInputDialog.getInt(self, tr("Fling — сцена"), tr("Громкость ТВ, % от максимума:"), 20, 0, 100, 5)
        if ok:
            self._insert({"do": "volume", "pct": n})

    def _ask_wait(self):
        n, ok = QInputDialog.getInt(self, tr("Fling — сцена"), tr("Подождать, секунд:"), 3, 1, 600, 1)
        if ok:
            self._insert({"do": "wait", "s": n})

    def _move(self, d):
        r = self.list.currentRow()
        j = r + d
        if 0 <= r < len(self.steps) and 0 <= j < len(self.steps):
            self.steps[r], self.steps[j] = self.steps[j], self.steps[r]
            self._refresh(j)

    def _remove(self):
        r = self.list.currentRow()
        if 0 <= r < len(self.steps):
            del self.steps[r]
            self._refresh(min(r, len(self.steps) - 1))

    def _scene(self) -> dict | None:
        name = self.name.text().strip()
        err = None if name else "Дайте сцене название."
        if not err and not self.steps:
            err = "Добавьте хотя бы один шаг."
        if err:
            self.msg.setText(err); self.msg.show()
            return None
        self.msg.hide()
        sc = {"name": name[:60], "steps": self.steps}
        if self.tv.isChecked() and self._tv_name:
            sc["tv"] = self._tv_name
        return sc

    def _save(self):
        sc = self._scene()
        if sc is None:
            return
        scenes = self.rx.scenes()
        others = [x for i, x in enumerate(scenes) if i != self.index]
        if any(x["name"].casefold() == sc["name"].casefold() for x in others):
            sc["name"] += " 2"
        if self.index is None:
            scenes.append(sc)
        else:
            scenes[self.index] = sc
        self.rx.save_scenes(scenes)
        self.close()

    def _run(self):
        sc = self._scene()
        if sc is not None:
            self.rx.run_scene(sc)

    def _delete(self):
        from PyQt6.QtWidgets import QMessageBox
        name = self.name.text().strip() or "?"
        if QMessageBox.question(self, tr("Fling — сцена"), tr("Удалить сцену «") + name + tr("»?")) \
                != QMessageBox.StandardButton.Yes:
            return
        scenes = self.rx.scenes()
        if self.index is not None and 0 <= self.index < len(scenes):
            del scenes[self.index]
            self.rx.save_scenes(scenes)
        self.close()
