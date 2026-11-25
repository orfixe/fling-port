# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"""Colour themes of the desktop app: the presets, the accent colours, and how the
"as the system" choice resolves against the current Windows / KDE theme.

Pure data and small functions only (no windows), so fling_app can import it at start-up.
Config keys (fling.json):
    theme        "auto" | a preset id below         (default "auto")
    theme_dark   preset used by "auto" on a dark system   (default "dark")
    theme_light  preset used by "auto" on a light system  (default "light")
    accent       "auto" (system) | "theme" | an ACCENTS key   (default "auto")
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, QEvent
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QWidget

# id: (Russian name, English name, dark?, background, text, the theme's own accent)
# "dark" and "light" keep their old ids: fling.json from 0.3.3 still means the same.
PRESETS: dict[str, tuple[str, str, bool, str, str, str]] = {
    "dark":     ("Графит",   "Graphite", True,  "#2a2a2c", "#f2f2f7", "#0a84ff"),
    "midnight": ("Полночь",  "Midnight", True,  "#151b28", "#e4eaf4", "#5b8cff"),
    "oled":     ("Чёрная",   "Black",    True,  "#000000", "#f5f5f7", "#0a84ff"),
    "nord":     ("Фьорд",    "Fjord",    True,  "#2e3440", "#eceff4", "#88c0d0"),
    "mocha":    ("Мокко",    "Mocha",    True,  "#1e1e2e", "#cdd6f4", "#cba6f7"),
    "forest":   ("Лес",      "Forest",   True,  "#17211b", "#e3ede6", "#4cc38a"),
    "light":    ("Светлая",  "Light",    False, "#f6f6f6", "#1c1c1e", "#007aff"),
    "frost":    ("Иней",     "Frost",    False, "#edf2f8", "#1b2430", "#2f6fde"),
    "paper":    ("Бумага",   "Paper",    False, "#f5efe3", "#3a3026", "#b5651d"),
    "latte":    ("Латте",    "Latte",    False, "#eff1f5", "#4c4f69", "#8839ef"),
    "sage":     ("Шалфей",   "Sage",     False, "#ecf2ec", "#1f2a22", "#2f8a57"),
}
DARK_IDS = [k for k, v in PRESETS.items() if v[2]]
LIGHT_IDS = [k for k, v in PRESETS.items() if not v[2]]

# accent: (on dark, on light). "auto" = the system's, "theme" = the preset's own.
ACCENTS: dict[str, tuple[str, str]] = {
    "blue": ("#0a84ff", "#007aff"), "purple": ("#bf5af2", "#8e44ad"),
    "pink": ("#ff375f", "#d6204e"), "red": ("#ff453a", "#d70015"),
    "orange": ("#ff9f0a", "#c25e00"), "yellow": ("#ffd60a", "#9a7400"),
    "green": ("#30d158", "#1e8e3e"), "teal": ("#40c8e0", "#0a7f99"),
    "graphite": ("#a1a1a6", "#6e6e73"),
}
ACCENT_NAMES = {   # Russian (the translation key) for the tooltips
    "auto": "Системный", "theme": "Из темы", "blue": "Синий", "purple": "Фиолетовый",
    "pink": "Розовый", "red": "Красный", "orange": "Оранжевый", "yellow": "Жёлтый",
    "green": "Зелёный", "teal": "Бирюзовый", "graphite": "Графитовый",
}


def name(pid: str, lang: str = "ru") -> str:
    p = PRESETS.get(pid)
    return (p[1] if lang == "en" else p[0]) if p else pid


def readable(c: QColor, dark: bool) -> QColor:
    """Keep an accent legible on the panel: light enough on dark, dark enough on light."""
    h, s, l, _ = c.getHsl()
    if dark and l < 150:
        l = 150
    elif not dark and l > 115:
        l = 115
    return QColor.fromHsl(max(h, 0), s, l, 255)


def preset_for(conf: dict, system_dark: bool) -> str:
    """The preset the panel shows for this config on a system that is dark / light now."""
    t = conf.get("theme", "auto")
    if t in PRESETS:
        return t
    if system_dark:
        d = conf.get("theme_dark", "dark")
        return d if d in DARK_IDS else "dark"
    l = conf.get("theme_light", "light")
    return l if l in LIGHT_IDS else "light"


def resolve(conf: dict, system_dark: bool, system_accent: QColor | None) -> dict:
    """{"id", "dark", "bg", "fg", "accent"} (QColors) for a config — used by the app's
    Theme and by the live preview in Settings."""
    pid = preset_for(conf, system_dark)
    _, _, dark, bg, fg, own = PRESETS[pid]
    acc = conf.get("accent", "auto")
    if acc == "auto" and system_accent is not None and system_accent.isValid():
        accent = readable(QColor(system_accent), dark)
    elif acc in ACCENTS:
        accent = QColor(ACCENTS[acc][0 if dark else 1])
    else:                                        # "theme", or "auto" with no system colour
        accent = QColor(own)
    return {"id": pid, "dark": dark, "bg": QColor(bg), "fg": QColor(fg), "accent": accent}


def scheme_dark(conf: dict) -> bool | None:
    """Light/dark that Qt's own widgets should use: True / False, or None = the system's."""
    t = conf.get("theme", "auto")
    return PRESETS[t][2] if t in PRESETS else None


class SystemWatch(QObject):
    """"As the system": restart Fling when Windows / KDE switches light ↔ dark or changes
    its accent colour — but never under the user's hands: wait until no Fling window is
    open. Qt says when the scheme or the palette changes; Windows doesn't always say when
    only the accent changes, so there it is also re-read once a minute (a registry read)."""

    def __init__(self, app, load_conf, restart, read_dark, read_accent):
        super().__init__(app)
        self._load, self._restart = load_conf, restart
        self._read_dark, self._read_accent = read_dark, read_accent
        self._dark0, self._acc0 = read_dark(), self._acc_key()
        self._pending = QTimer(self, interval=15000)
        self._pending.timeout.connect(self._try_now)
        try:
            app.styleHints().colorSchemeChanged.connect(lambda *_: QTimer.singleShot(500, self._check))
        except Exception:
            pass
        # a hidden widget gets ThemeChange / PaletteChange without a costly app-wide event filter
        self._probe = _Probe(lambda: QTimer.singleShot(500, self._check))
        import sys
        if sys.platform == "win32":
            self._slow = QTimer(self, interval=60000)
            self._slow.timeout.connect(self._check)
            self._slow.start()

    def _acc_key(self):
        c = self._read_accent()
        return c.name() if c is not None and c.isValid() else None

    def _check(self):
        conf = self._load()
        follow_scheme = conf.get("theme", "auto") == "auto"
        follow_accent = conf.get("accent", "auto") == "auto"
        changed = (follow_scheme and self._read_dark() != self._dark0) or \
                  (follow_accent and self._acc_key() != self._acc0)
        if changed:
            self._try_now()

    def _try_now(self):
        if any(w.isVisible() for w in QApplication.topLevelWidgets() if w.isWindow()):
            if not self._pending.isActive():
                self._pending.start()
            return
        self._pending.stop()
        self._restart()


class _Probe(QWidget):
    def __init__(self, on_change):
        super().__init__()
        self._on = on_change

    # PaletteChange, ApplicationPaletteChange and ThemeChange (210: PyQt6 doesn't name it)
    _TYPES = {QEvent.Type.PaletteChange.value, QEvent.Type.ApplicationPaletteChange.value, 210}

    def event(self, e):
        if e.type().value in self._TYPES:
            self._on()
        return super().event(e)
