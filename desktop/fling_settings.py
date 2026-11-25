# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"""The Settings window of the desktop app: Appearance (themes and accent colours with a
live preview of the panel), General, Remote, Casting and About.

fling_app binds itself in open_settings() (it runs as __main__, so this module never
imports it). UI strings are Russian and go through fling_app.tr() like everywhere else;
their English pairs are in PHRASES below."""
from __future__ import annotations

import json
import os
import platform
import sys

from PyQt6.QtCore import Qt, QRectF, QPointF, QSize, QUrl, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QConicalGradient, QDesktopServices, QFont, QGuiApplication,
                         QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap)
from PyQt6.QtWidgets import (QAbstractButton, QApplication, QButtonGroup, QFrame, QGridLayout,
                             QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSizePolicy,
                             QStackedWidget, QToolButton, QVBoxLayout, QWidget)

import fling_themes as TH

F = None          # the fling_app module, set by open_settings()

REPO = "https://github.com/orfixe/fling-port"
ORIGINAL = "https://github.com/tatianathevisionary/fling"
LINKS = {
    "repo": REPO,
    "issues": REPO + "/issues",
    "releases": REPO + "/releases",
    "changelog": REPO + "/blob/main/CHANGELOG.md",
    "license": REPO + "/blob/main/LICENSE",
    "notices": REPO + "/blob/main/THIRD-PARTY-NOTICES.md",
    "credits": REPO + "/blob/main/CREDITS.md",
}
LATEST_API = "https://api.github.com/repos/orfixe/fling-port/releases/latest"

PAGES = (   # key, sidebar title, icon, tile colour
    ("appearance", "Оформление", "palette", "#af52de"),
    ("general", "Основные", "gear", "#8e8e93"),
    ("remote", "Пульт", "remote", "#0a84ff"),
    ("cast", "Трансляция", "cast", "#30b158"),
    ("about", "О программе", "info", None),          # None: the Fling logo
)

PHRASES = [
    ("Fling — настройки", "Fling — Settings"),
    ("Оформление", "Appearance"), ("Основные", "General"), ("Пульт", "Remote"),
    ("Трансляция", "Casting"), ("О программе", "About"),
    # Appearance
    ("Тема", "Theme"), ("Тёмные", "Dark"), ("Светлые", "Light"), ("Как в системе", "As the system"),
    ("Светлая или тёмная — как в Windows, и меняется вместе с ней.",
     "Light or dark, as in Windows, and it switches along with it."),
    ("Светлая или тёмная — как в системе, и меняется вместе с ней.",
     "Light or dark, as in your system, and it switches along with it."),
    ("Днём:", "Day:"), ("Ночью:", "Night:"),
    ("Цвет акцента", "Accent colour"),
    ("Системный — цвет акцента Windows, меняется вместе с ним",
     "System — the Windows accent colour, and it changes along with it"),
    ("Системный — цвет акцента системы, меняется вместе с ним",
     "System — your system's accent colour, and it changes along with it"),
    ("Из темы — свой цвет у каждой темы", "From the theme — each theme has its own colour"),
    ("Синий", "Blue"), ("Фиолетовый", "Purple"), ("Розовый", "Pink"), ("Красный", "Red"),
    ("Оранжевый", "Orange"), ("Жёлтый", "Yellow"), ("Зелёный", "Green"), ("Бирюзовый", "Teal"),
    ("Графитовый", "Graphite"),
    ("Предпросмотр", "Preview"),
    ("Наведите на тему, чтобы посмотреть, как будет выглядеть панель.",
     "Hover over a theme to see how the panel will look."),
    ("Применить", "Apply"), ("Отменить", "Cancel"),
    ("Чтобы применить тему, Fling перезапустится — это пара секунд.",
     "Fling restarts to apply the theme; it takes a couple of seconds."),
    # the preview's sample content
    ("Лоу-фай для работы и учёбы", "Lo-fi beats to work and study to"),
    ("YouTube · Гостиная", "YouTube · Living room"), ("Гостиная", "Living room"),
    # General
    ("Язык", "Language"),
    ("Fling перезапустится на выбранном языке.", "Fling restarts in the chosen language."),
    ("Запускать при входе в систему", "Start at login"),
    ("Fling ждёт в области уведомлений и откроется по клику или Win+Shift+C.",
     "Fling waits in the notification area and opens on a click or Win+Shift+C."),
    ("Fling ждёт в трее и откроется по клику.", "Fling waits in the tray and opens on a click."),
    ("Компактная панель", "Compact panel"),
    ("Только то, что играет, громкость и выбор ТВ.", "Only what's playing, the volume and the TV switcher."),
    ("Как пользоваться", "How to use"), ("Журнал Fling", "Fling log"), ("Папка настроек", "Settings folder"),
    # Remote
    ("ТВ не выбран", "No TV selected"),
    ("пульт по сети связан", "network remote paired"), ("пульт по сети не связан", "network remote not paired"),
    ("ADB подключён", "ADB connected"),
    ("Клавиатура управляет ТВ", "The keyboard drives the TV"),
    ("Пока панель открыта, стрелки, Enter, Пробел, Backspace, Home и +/− уходят на ТВ. "
     "Esc закрывает панель.",
     "While the panel is open, the arrows, Enter, Space, Backspace, Home and +/− go to the TV. "
     "Esc closes the panel."),
    ("ADB-консоль…", "ADB console…"),
    # Casting
    ("Отправить вкладку", "Cast the tab"),
    ("Сочетание клавиш работает везде, даже когда панель закрыта.",
     "The shortcut works anywhere, even when the panel is closed."),
    ("Уведомлять о смене видео", "Notify when the video changes"),
    ("Когда на ТВ начинается другое видео или трек, а панель закрыта.",
     "When the TV moves on to another video or track while the panel is closed."),
    ("Связь с расширением браузера", "Browser extension link"),
    ("Расширение показывает Fling открытую вкладку и добавляет кнопку «На ТВ». "
     "Изменение вступит в силу после перезапуска Fling.",
     "The extension tells Fling which tab is open and adds a “To the TV” button. "
     "The change takes effect after Fling restarts."),
    ("Установить расширение…", "Install the extension…"),
    # About
    ("Версия ", "Version "),
    ("Отправляйте видео и вкладки браузера на Chromecast и Google TV и управляйте телевизором "
     "с компьютера: пульт, громкость, приложения, экран ТВ вживую.",
     "Cast videos and browser tabs to a Chromecast or Google TV and drive the TV from your "
     "computer: remote, volume, apps and the TV's screen, live."),
    ("Авторы", "Authors"),
    ("© 2026 orfixe и участники порта Fling", "© 2026 orfixe and the Fling port contributors"),
    ("Оригинальный Fling для macOS создали {a} и {b}", "The original Fling for macOS was made by {a} and {b}"),
    ("Это неофициальный порт: его авторы Fling не проверяли и не одобряли.",
     "This is an unofficial port, not reviewed or endorsed by the authors of Fling."),
    ("Лицензия MIT — для кода этого порта.", "MIT License, for the code of this port."),
    ("Лицензия", "License"), ("Сторонние компоненты", "Third-party notices"), ("Благодарности", "Credits"),
    ("Ссылки", "Links"), ("Исходный код", "Source code"), ("Сообщить о проблеме", "Report a problem"),
    ("Выпуски", "Releases"), ("Что нового", "What's new"),
    ("Проверить обновления", "Check for updates"), ("Проверяю…", "Checking…"),
    ("У вас последняя версия.", "You have the latest version."),
    ("Доступна версия ", "Version available: "), ("Скачать", "Download"),
    ("Репозиторий пока закрыт, проверить автоматически нельзя. Загляните в «Выпуски» на GitHub.",
     "The repository is private for now, so there's nothing to check automatically. See Releases on GitHub."),
    ("Не удалось связаться с GitHub.", "Couldn't reach GitHub."),
    ("Скопировать сведения", "Copy details"), ("Скопировано", "Copied"),
    ("Версия Fling, системы, Python и Qt — для сообщения о проблеме.",
     "The Fling, system, Python and Qt versions, for a problem report."),
]

_registered = False


def _register_phrases():
    global _registered
    if _registered or F is None:
        return
    _registered = True
    F.EN_PHRASES.extend(PHRASES)
    F.EN_PHRASES.sort(key=lambda kv: -len(kv[0]))     # tr() replaces the longest first


# ---------------------------------------------------------------- icons

def _dpr():
    s = QGuiApplication.primaryScreen()
    return s.devicePixelRatio() if s else 1.0


def icon(name, size=14, color=None, weight=1.6) -> QIcon:
    """Line icons for Settings (same drawing style as fling_app.glyph)."""
    color = QColor(color) if color is not None else QColor("#888")
    k = _dpr() * 2
    pm = QPixmap(int(size * k), int(size * k)); pm.setDevicePixelRatio(k)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint_icon(p, name, QRectF(0, 0, size, size), color, weight)
    p.end()
    return QIcon(pm)


def paint_icon(p: QPainter, name, r: QRectF, color: QColor, weight=1.6):
    import math
    s = r.width(); x0, y0 = r.x(), r.y()
    c = QPointF(x0 + s / 2, y0 + s / 2)
    pen = QPen(color, weight * s / 14); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.save(); p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)

    def at(fx, fy):
        return QPointF(x0 + s * fx, y0 + s * fy)

    if name == "gear":
        p.drawEllipse(c, s * .26, s * .26)
        p.drawEllipse(c, s * .09, s * .09)
        for i in range(8):
            a = math.radians(i * 45 + 22.5)
            p.drawLine(QPointF(c.x() + math.cos(a) * s * .3, c.y() + math.sin(a) * s * .3),
                       QPointF(c.x() + math.cos(a) * s * .42, c.y() + math.sin(a) * s * .42))
    elif name == "palette":
        pa = QPainterPath()
        pa.moveTo(at(.5, .1))
        pa.cubicTo(at(.82, .1), at(.92, .36), at(.88, .52))
        pa.cubicTo(at(.84, .66), at(.66, .6), at(.6, .7))
        pa.cubicTo(at(.54, .8), at(.66, .9), at(.5, .9))
        pa.cubicTo(at(.22, .9), at(.1, .7), at(.1, .5))
        pa.cubicTo(at(.1, .28), at(.28, .1), at(.5, .1))
        p.drawPath(pa)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(color)
        for fx, fy in ((.32, .42), (.46, .28), (.64, .32), (.3, .62)):
            p.drawEllipse(at(fx, fy), s * .065, s * .065)
    elif name == "sliders":
        for fy, fx in ((.28, .66), (.5, .34), (.72, .58)):
            p.drawLine(at(.12, fy), at(.88, fy))
            p.setBrush(color); p.drawEllipse(at(fx, fy), s * .07, s * .07); p.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "remote":
        p.drawRoundedRect(QRectF(x0 + s * .3, y0 + s * .08, s * .4, s * .84), s * .14, s * .14)
        p.drawEllipse(at(.5, .32), s * .1, s * .1)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(color)
        for fy in (.58, .72):
            p.drawEllipse(at(.42, fy), s * .04, s * .04); p.drawEllipse(at(.58, fy), s * .04, s * .04)
    elif name == "cast":
        p.drawRoundedRect(QRectF(x0 + s * .1, y0 + s * .2, s * .8, s * .56), s * .08, s * .08)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(color)
        tri = QPainterPath(); tri.moveTo(at(.42, .35)); tri.lineTo(at(.42, .61)); tri.lineTo(at(.63, .48))
        tri.closeSubpath(); p.drawPath(tri)
        p.setPen(pen); p.drawLine(at(.34, .88), at(.66, .88))
    elif name == "info":
        p.drawEllipse(c, s * .38, s * .38)
        p.drawLine(at(.5, .46), at(.5, .7))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(color); p.drawEllipse(at(.5, .32), s * .06, s * .06)
    elif name == "link":                                  # arrow out of a box
        pa = QPainterPath(); pa.moveTo(at(.44, .2)); pa.lineTo(at(.2, .2)); pa.lineTo(at(.2, .8))
        pa.lineTo(at(.8, .8)); pa.lineTo(at(.8, .56)); p.drawPath(pa)
        p.drawLine(at(.5, .5), at(.82, .18))
        pa = QPainterPath(); pa.moveTo(at(.6, .18)); pa.lineTo(at(.82, .18)); pa.lineTo(at(.82, .4)); p.drawPath(pa)
    elif name == "refresh":
        p.drawArc(QRectF(x0 + s * .18, y0 + s * .18, s * .64, s * .64), 60 * 16, 270 * 16)
        pa = QPainterPath(); pa.moveTo(at(.56, .1)); pa.lineTo(at(.68, .22)); pa.lineTo(at(.54, .32)); p.drawPath(pa)
    elif name == "copy":
        p.drawRoundedRect(QRectF(x0 + s * .32, y0 + s * .3, s * .5, s * .56), s * .08, s * .08)
        pa = QPainterPath(); pa.moveTo(at(.2, .66)); pa.lineTo(at(.2, .2)); pa.lineTo(at(.6, .2))
        p.drawPath(pa)
    elif name == "check":
        pa = QPainterPath(); pa.moveTo(at(.24, .52)); pa.lineTo(at(.42, .7)); pa.lineTo(at(.76, .32))
        p.drawPath(pa)
    elif name == "chevron":                                # small "▾" for drop-downs
        pa = QPainterPath(); pa.moveTo(at(.28, .4)); pa.lineTo(at(.5, .62)); pa.lineTo(at(.72, .4))
        p.drawPath(pa)
    p.restore()


def tile_icon(name, tint, size=22) -> QIcon:
    """macOS-like sidebar tile: a white line icon on a coloured rounded square
    (tint None = the Fling logo)."""
    k = _dpr() * 2
    pm = QPixmap(int(size * k), int(size * k)); pm.setDevicePixelRatio(k)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if tint is None:
        p.drawPixmap(QRectF(0, 0, size, size), F.logo_pixmap(size, dpr=k), QRectF(0, 0, size * k, size * k))
    else:
        g = QLinearGradient(0, 0, 0, size)
        top = QColor(tint).lighter(118); g.setColorAt(0, top); g.setColorAt(1, QColor(tint))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(g)
        p.drawRoundedRect(QRectF(0, 0, size, size), size * .24, size * .24)
        m = size * .18
        paint_icon(p, name, QRectF(m, m, size - 2 * m, size - 2 * m), QColor("white"), 1.9)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- small widgets

def _css(c):
    return F.Theme.css(c)


def _a(c: QColor, a: float) -> QColor:
    c = QColor(c); c.setAlphaF(a); return c


def heading(text, px=17):
    return F.label(text, px, weight=QFont.Weight.Bold)


def section(text):
    l = F.label(text, 11, F.T.fg_a(0.55), QFont.Weight.DemiBold)
    l.setContentsMargins(2, 6, 0, 0)
    return l


def note(text, px=11):
    l = F.label(text, px, F.T.fg_a(0.58), wrap=True)
    return l


def card():
    box = QFrame(); box.setObjectName("card")
    box.setStyleSheet(f"QFrame#card{{background:{_css(F.T.fg_a(0.045))};border-radius:10px}}")
    lay = QVBoxLayout(box); lay.setContentsMargins(14, 12, 14, 12); lay.setSpacing(8)
    return box, lay


def button(text, on, primary=False, icon_name=None):
    b = QPushButton()
    b.setText(text)
    b.setFont(F.font(12, QFont.Weight.DemiBold if primary else QFont.Weight.Normal))
    b.setFixedHeight(28)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    T = F.T
    if primary:
        b.setStyleSheet(
            f"QPushButton{{border:none;border-radius:7px;padding:0 16px;background:{_css(T.accent)};color:white}}"
            f"QPushButton:hover{{background:{_css(T.acc_a(0.85))}}}"
            f"QPushButton:disabled{{background:{_css(T.fg_a(0.1))};color:{_css(T.fg_a(0.35))}}}")
    else:
        b.setStyleSheet(
            f"QPushButton{{border:1px solid {_css(T.fg_a(0.16))};border-radius:7px;padding:0 12px;"
            f"background:{_css(T.fg_a(0.05))};color:{_css(T.fg)}}}"
            f"QPushButton:hover{{background:{_css(T.acc_a(0.2))}}}"
            f"QPushButton:disabled{{color:{_css(T.fg_a(0.3))}}}")
    if icon_name:
        b.setIcon(icon(icon_name, 14, T.fg_a(0.75)))
        b.setIconSize(QSize(14, 14))
    b.clicked.connect(on)
    return b


def link_button(text, url_or_fn, icon_name="link"):
    """Flat accent-coloured link with a small icon."""
    b = QPushButton()
    b.setText(text)
    b.setFont(F.font(12))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setIcon(icon(icon_name, 13, F.T.accent)); b.setIconSize(QSize(13, 13))
    b.setFixedHeight(26)
    b.setStyleSheet(f"QPushButton{{border:none;border-radius:6px;padding:0 8px;text-align:left;"
                    f"background:transparent;color:{_css(F.T.accent)}}}"
                    f"QPushButton:hover{{background:{_css(F.T.acc_a(0.14))}}}")
    if isinstance(url_or_fn, str):
        b.setToolTip(url_or_fn)
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url_or_fn)))
    else:
        b.clicked.connect(url_or_fn)
    return b


class Toggle(QAbstractButton):
    """An on/off switch (macOS style)."""

    def __init__(self, on=False):
        super().__init__()
        self.setCheckable(True); self.setChecked(on)
        self.setFixedSize(36, 21)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        return QSize(36, 21)

    def paintEvent(self, e):
        T = F.T
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        on = self.isChecked()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.accent if on else T.fg_a(0.2 if self.underMouse() else 0.16))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 4
        x = r.right() - 2 - d if on else r.left() + 2
        p.setBrush(QColor(0, 0, 0, 40)); p.drawEllipse(QRectF(x, r.top() + 2.6, d, d))
        p.setBrush(QColor("white")); p.drawEllipse(QRectF(x, r.top() + 2, d, d))
        if self.hasFocus():
            p.setPen(QPen(T.acc_a(0.5), 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)


def toggle_row(title, desc, on, changed):
    """A titled switch with a quiet description under it."""
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(12)
    v = QVBoxLayout(); v.setSpacing(2); v.setContentsMargins(0, 0, 0, 0)
    t = F.label(title, 12.5, weight=QFont.Weight.DemiBold)
    v.addWidget(t)
    if desc:
        v.addWidget(note(desc))
    h.addLayout(v, 1)
    sw = Toggle(on)
    sw.toggled.connect(changed)
    h.addWidget(sw, 0, Qt.AlignmentFlag.AlignTop)
    w.toggle = sw
    return w


# ---------------------------------------------------------------- the panel, in miniature

class PreviewPanel(QWidget):
    """A small drawing of the Fling panel in a given palette: header, what's playing,
    transport, the d-pad, two menu rows and the footer."""

    W, H = 256, 468

    def __init__(self):
        super().__init__()
        self.setFixedSize(self.W, self.H)
        self.r = None

    def set_palette(self, r: dict):
        self.r = r
        self.update()

    def paintEvent(self, e):
        if not self.r:
            return
        r = self.r
        bg, fg, acc, dark = r["bg"], r["fg"], r["accent"], r["dark"]
        tr = F.tr
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        W = self.W
        panel = QRectF(6.5, 4.5, W - 13, self.H - 13)
        # soft shadow, then the panel itself
        for i, a in ((6, 10), (4, 16), (2, 24)):
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(panel.adjusted(-i / 2, -i / 4, i / 2, i), 12 + i / 2, 12 + i / 2)
        p.setPen(QPen(_a(fg, 0.16), 1)); p.setBrush(bg)
        p.drawRoundedRect(panel, 10, 10)
        L, R = panel.left() + 12, panel.right() - 12

        def text(x, y, w, h, s, px, col, weight=QFont.Weight.Normal, align=Qt.AlignmentFlag.AlignLeft):
            p.setPen(col); p.setFont(F.font(px, weight))
            p.drawText(QRectF(x, y, w, h), int(align | Qt.AlignmentFlag.AlignVCenter), tr(s))

        def glyph(name, x, y, size, col, weight=1.7):
            pm = F.glyph(name, size, col, weight).pixmap(QSize(int(size), int(size)))
            p.drawPixmap(QRectF(x, y, size, size), pm, QRectF(pm.rect()))

        y = panel.top() + 9
        # header: "Remote | ● LIVE" pill, then ? / gear / compact
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(_a(fg, 0.07))
        p.drawRoundedRect(QRectF(L, y, 112, 22), 9, 9)
        p.setBrush(bg); p.drawRoundedRect(QRectF(L + 2, y + 2, 50, 18), 7, 7)
        text(L + 2, y + 2, 50, 18, "Пульт", 9.5, fg, QFont.Weight.DemiBold, Qt.AlignmentFlag.AlignHCenter)
        text(L + 54, y + 2, 56, 18, "●  LIVE", 9.5, _a(fg, 0.55), QFont.Weight.DemiBold, Qt.AlignmentFlag.AlignHCenter)
        glyph("help", R - 50, y + 4, 14, _a(fg, 0.6))
        icon_pm = icon("gear", 14, _a(fg, 0.6)).pixmap(QSize(14, 14))
        p.drawPixmap(QRectF(R - 32, y + 4, 14, 14), icon_pm, QRectF(icon_pm.rect()))
        glyph("compact", R - 14, y + 4, 13, _a(fg, 0.6))
        y += 30
        # artwork: a gradient in the accent colour with a play mark
        art = QRectF(L, y, R - L, 104)
        g = QLinearGradient(art.topLeft(), art.bottomRight())
        g.setColorAt(0, acc.lighter(125)); g.setColorAt(1, acc.darker(170))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(g); p.drawRoundedRect(art, 8, 8)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawEllipse(art.center(), 18, 18)
        glyph("play", art.center().x() - 8, art.center().y() - 8, 16, QColor("white"))
        y += 112
        text(L, y, R - L, 17, "Лоу-фай для работы и учёбы", 12, fg, QFont.Weight.DemiBold)
        y += 17
        text(L, y, R - L, 15, "YouTube · Гостиная", 10, _a(fg, 0.6))
        y += 21
        # progress
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(_a(fg, 0.15))
        p.drawRoundedRect(QRectF(L, y, R - L, 4), 2, 2)
        p.setBrush(acc); p.drawRoundedRect(QRectF(L, y, (R - L) * .38, 4), 2, 2)
        p.setBrush(QColor("white") if dark else bg); p.setPen(QPen(_a(fg, 0.25), 0.8))
        p.drawEllipse(QPointF(L + (R - L) * .38, y + 2), 4.5, 4.5)
        y += 8
        text(L, y, 60, 14, "12:04", 9.5, _a(fg, 0.45))
        text(R - 60, y, 60, 14, "34:10", 9.5, _a(fg, 0.45), align=Qt.AlignmentFlag.AlignRight)
        y += 18
        # transport: back 30 · play · forward 30
        cx = (L + R) / 2
        for dx, name, big in ((-50, "rew", False), (0, "pause", True), (50, "fwd", False)):
            rad = 17 if big else 14
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(_a(fg, 0.1 if big else 0.07))
            p.drawEllipse(QPointF(cx + dx, y + 17), rad, rad)
            gs = 15 if big else 12
            glyph(name, cx + dx - gs / 2, y + 17 - gs / 2, gs, acc if big else fg)
        y += 42
        # the d-pad ring with OK, Back and Home beside it
        d = 92
        ring = QRectF(cx - d / 2, y, d, d)
        p.setPen(QPen(_a(fg, 0.08), 1)); p.setBrush(_a(fg, 0.045)); p.drawEllipse(ring)
        rc = ring.center()
        for name, ox, oy in (("up", 0, -1), ("down", 0, 1), ("left", -1, 0), ("right", 1, 0)):
            q = QPointF(rc.x() + ox * 32, rc.y() + oy * 32)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(_a(fg, 0.07)); p.drawEllipse(q, 11, 11)
            glyph(name, q.x() - 6, q.y() - 6, 12, fg, 2.0)
        p.setBrush(_a(fg, 0.1)); p.drawEllipse(rc, 15, 15)
        text(rc.x() - 15, rc.y() - 15, 30, 30, "OK", 9.5, acc, QFont.Weight.DemiBold, Qt.AlignmentFlag.AlignHCenter)
        for name, x in (("back", L + 12), ("home", R - 12)):
            q = QPointF(x, ring.bottom() - 10)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(_a(fg, 0.07)); p.drawEllipse(q, 12, 12)
            glyph(name, q.x() - 7, q.y() - 7, 14, fg)
        y += d + 10
        # separator + two menu rows (the first one accented, the second one hovered)
        p.fillRect(QRectF(L, y, R - L, 1), _a(fg, 0.13)); y += 6
        row = QRectF(L - 6, y, R - L + 12, 24)
        glyph("play_on_tv", row.left() + 8, y + 5, 14, acc)
        text(row.left() + 30, y, 150, 24, "Отправить эту вкладку", 11.5, acc, QFont.Weight.DemiBold)
        text(row.right() - 88, y, 80, 24, F.SHORTCUT_LABEL, 10.5, _a(fg, 0.3), align=Qt.AlignmentFlag.AlignRight)
        y += 25
        row = QRectF(L - 6, y, R - L + 12, 24)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(_a(acc, 0.2)); p.drawRoundedRect(row, 5, 5)
        glyph("tv", row.left() + 8, y + 5, 14, _a(fg, 0.55))
        text(row.left() + 30, y, 160, 24, "Подключение ТВ…", 11.5, fg)
        y += 30
        p.fillRect(QRectF(L, y, R - L, 1), _a(fg, 0.13)); y += 8
        # footer: status dot · TV name · switcher
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor("#30d158") if dark else QColor("#28c840"))
        p.drawEllipse(QPointF(L + 3, y + 9), 3, 3)
        text(L + 12, y, 130, 18, "Гостиная", 11, fg)
        text(R - 90, y, 90, 18, "Сменить", 10.5, acc, align=Qt.AlignmentFlag.AlignRight)
        p.end()


class ThemeCard(QWidget):
    """A theme to pick: a swatch that looks like a tiny panel, and its name."""

    picked = pyqtSignal(str)
    hovered = pyqtSignal(str)        # "" when the mouse leaves

    W, H = 100, 68

    def __init__(self, pid: str, swatch):
        super().__init__()
        self.pid = pid
        self.swatch = swatch            # () -> list of palette dicts (two for "as the system")
        self.selected = False
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def set_selected(self, on):
        if on != self.selected:
            self.selected = on
            self.update()

    def title(self):
        return F.tr("Как в системе") if self.pid == "auto" else TH.name(self.pid, F.LANG)

    def enterEvent(self, e):
        self.hovered.emit(self.pid); self.update()

    def leaveEvent(self, e):
        self.hovered.emit(""); self.update()

    def focusInEvent(self, e):
        self.hovered.emit(self.pid); self.update()

    def focusOutEvent(self, e):
        self.hovered.emit(""); self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.picked.emit(self.pid)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.picked.emit(self.pid)
        else:
            super().keyPressEvent(e)

    def paintEvent(self, e):
        T = F.T
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sw = QRectF(3, 3, self.W - 6, 44)
        pals = self.swatch()
        clip = QPainterPath(); clip.addRoundedRect(sw, 8, 8)
        p.save(); p.setClipPath(clip)
        if len(pals) == 2:                    # as the system: dark and light halves, on a diagonal
            self._mini(p, sw, pals[1])
            half = QPainterPath(); half.moveTo(sw.topLeft()); half.lineTo(sw.right() - 20, sw.top())
            half.lineTo(sw.left() + 20, sw.bottom()); half.lineTo(sw.bottomLeft()); half.closeSubpath()
            p.setClipPath(half, Qt.ClipOperation.IntersectClip)
            self._mini(p, sw, pals[0])
        else:
            self._mini(p, sw, pals[0])
        p.restore()
        hover = self.underMouse() or self.hasFocus()
        if self.selected:
            p.setPen(QPen(T.accent, 2.2)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(sw.adjusted(-1.6, -1.6, 1.6, 1.6), 9.5, 9.5)
        else:
            p.setPen(QPen(T.fg_a(0.32 if hover else 0.14), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(sw.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        p.setPen(T.accent if self.selected else T.fg_a(0.85 if hover else 0.72))
        p.setFont(F.font(11, QFont.Weight.DemiBold if self.selected else QFont.Weight.Normal))
        p.drawText(QRectF(0, sw.bottom() + 3, self.W, self.H - sw.bottom() - 3),
                   int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), self.title())
        p.end()

    @staticmethod
    def _mini(p, r: QRectF, pal):
        bg, fg, acc = pal["bg"], pal["fg"], pal["accent"]
        p.fillRect(r, bg)
        x, y = r.left() + 8, r.top() + 8
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(acc.darker(115) if pal["dark"] else acc)
        p.drawRoundedRect(QRectF(x, y, 22, 16), 3, 3)              # artwork
        p.setBrush(_a(fg, 0.85)); p.drawRoundedRect(QRectF(x + 28, y + 1, 40, 4), 2, 2)
        p.setBrush(_a(fg, 0.4)); p.drawRoundedRect(QRectF(x + 28, y + 9, 26, 3.5), 1.75, 1.75)
        y2 = r.top() + 31
        p.setBrush(_a(fg, 0.16)); p.drawRoundedRect(QRectF(x, y2, r.width() - 18, 3), 1.5, 1.5)
        p.setBrush(acc); p.drawRoundedRect(QRectF(x, y2, (r.width() - 18) * .45, 3), 1.5, 1.5)
        p.setBrush(_a(fg, 0.1)); p.drawEllipse(QPointF(r.right() - 15, r.top() + 16), 7, 7)
        p.setBrush(acc); p.drawEllipse(QPointF(r.right() - 15, r.top() + 16), 2.6, 2.6)


class AccentDot(QAbstractButton):
    """One accent colour to pick: a filled circle, ringed when chosen."""

    hovered = pyqtSignal(str)

    def __init__(self, key: str, color_fn):
        super().__init__()
        self.key, self.color_fn = key, color_fn
        self.setCheckable(True)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def enterEvent(self, e):
        self.hovered.emit(self.key)

    def leaveEvent(self, e):
        self.hovered.emit("")

    def paintEvent(self, e):
        T = F.T
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        rad = 9.5
        if self.key == "auto":                          # the system's colour: a colour wheel
            g = QConicalGradient(c, 90)
            for i, col in enumerate(("#ff453a", "#ff9f0a", "#ffd60a", "#30d158", "#40c8e0",
                                     "#0a84ff", "#bf5af2", "#ff375f", "#ff453a")):
                g.setColorAt(i / 8, QColor(col))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(g); p.drawEllipse(c, rad, rad)
            sys_c = self.color_fn()
            if sys_c is not None:
                p.setBrush(T.bg); p.drawEllipse(c, 5.2, 5.2)
                p.setBrush(sys_c); p.drawEllipse(c, 3.8, 3.8)
        elif self.key == "theme":                       # the theme's own colour, on the theme
            own, bg = self.color_fn()
            p.setPen(QPen(T.fg_a(0.2), 1)); p.setBrush(bg); p.drawEllipse(c, rad, rad)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(own); p.drawEllipse(c, 5, 5)
        else:
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(self.color_fn()); p.drawEllipse(c, rad, rad)
        if self.isChecked():
            p.setPen(QPen(T.fg_a(0.85), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, rad + 3.2, rad + 3.2)
        elif self.underMouse():
            p.setPen(QPen(T.fg_a(0.3), 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, rad + 3.2, rad + 3.2)
        p.end()


# ---------------------------------------------------------------- the window

class SettingsWindow(QWidget):
    def __init__(self, state, holder=None):
        super().__init__(None, Qt.WindowType.Window)
        T = F.T
        self.state, self.holder = state, holder
        self.setWindowTitle(F.tr("Fling — настройки"))
        self.setWindowIcon(F.tray_icon())
        pal = self.palette(); pal.setColor(self.backgroundRole(), T.bg); self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.resize(900, 690)
        self.setMinimumSize(840, 560)

        root = QHBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        # sidebar
        side = QFrame(); side.setObjectName("side"); side.setFixedWidth(200)
        side.setStyleSheet(f"QFrame#side{{background:{_css(T.fg_a(0.035))};"
                           f"border-right:1px solid {_css(T.fg_a(0.08))}}}")
        sl = QVBoxLayout(side); sl.setContentsMargins(10, 14, 10, 12); sl.setSpacing(2)
        brand = QHBoxLayout(); brand.setContentsMargins(6, 0, 0, 10); brand.setSpacing(9)
        logo = QLabel(); logo.setPixmap(F.logo_pixmap(30, dpr=_dpr()))
        logo.setFixedSize(30, 30)
        brand.addWidget(logo)
        bt = QVBoxLayout(); bt.setSpacing(0)
        bt.addWidget(F.label("Fling", 14, weight=QFont.Weight.Bold))
        bt.addWidget(F.label(F.tr("Версия ") + F.VERSION, 10.5, T.fg_a(0.5)))
        brand.addLayout(bt, 1)
        sl.addLayout(brand)
        self.nav = {}
        group = QButtonGroup(self); group.setExclusive(True)
        for key, title, ic, tint in PAGES:
            b = QPushButton()
            b.setText(title)
            b.setIcon(tile_icon(ic, tint, 20)); b.setIconSize(QSize(20, 20))
            b.setCheckable(True)
            b.setFont(F.font(12.5))
            b.setFixedHeight(32)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            b.setStyleSheet(
                f"QPushButton{{border:none;border-radius:7px;padding:0 8px;text-align:left;"
                f"background:transparent;color:{_css(T.fg)}}}"
                f"QPushButton:hover{{background:{_css(T.fg_a(0.06))}}}"
                f"QPushButton:checked{{background:{_css(T.acc_a(0.2))};color:{_css(T.fg)}}}")
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
            group.addButton(b)
            sl.addWidget(b)
            self.nav[key] = b
        sl.addStretch(1)
        root.addWidget(side)

        self.stack = QStackedWidget()
        self.pages = {}
        for key, *_ in PAGES:
            page = getattr(self, "_page_" + key)()
            self.pages[key] = page
            self.stack.addWidget(self._scroll(page))
        root.addWidget(self.stack, 1)
        self.show_page("appearance")

    @staticmethod
    def _scroll(w):
        area = QScrollArea(); area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        w.setObjectName("page")
        area.setStyleSheet("QScrollArea{background:transparent;border:none}"
                           "QWidget#qt_scrollarea_viewport{background:transparent}"
                           "QWidget#page{background:transparent}")
        area.setWidget(w)
        return area

    @staticmethod
    def _page():
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(26, 18, 26, 18); v.setSpacing(10)
        return w, v

    def show_page(self, key):
        if key not in self.pages:
            key = "appearance"
        self.nav[key].setChecked(True)
        self.stack.setCurrentIndex([k for k, *_ in PAGES].index(key))
        if key == "remote":
            self._sync_remote()

    def _save(self, **kw):
        conf = self.state.conf
        for k, v in kw.items():
            if v is None:
                conf.pop(k, None)
            else:
                conf[k] = v
        F.save_conf(conf)

    def _restart_into(self, page):
        self._save(reopen_settings=page)
        F.restart_ui()

    # ----------------------------------------------------------- Appearance
    def _page_appearance(self):
        T = F.T
        w, v = self._page()
        v.addWidget(heading("Оформление"))
        c = self.state.conf
        self.saved = {k: c.get(k, d) for k, d in (("theme", "auto"), ("theme_dark", "dark"),
                                                  ("theme_light", "light"), ("accent", "auto"))}
        self.pend = dict(self.saved)
        self.hover_theme = self.hover_accent = ""
        self.sys_accent = F.system_accent()
        self.sys_dark = F.system_dark(QApplication.instance())

        body = QHBoxLayout(); body.setSpacing(22); body.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout(); left.setSpacing(8); left.setContentsMargins(0, 0, 0, 0)
        self.cards = {}

        # as the system: the card, and which theme to use by day / by night
        left.addWidget(section("Тема"))
        auto_row = QHBoxLayout(); auto_row.setSpacing(14)
        auto_row.addWidget(self._card("auto"), 0, Qt.AlignmentFlag.AlignTop)
        av = QVBoxLayout(); av.setSpacing(6); av.setContentsMargins(0, 2, 0, 0)
        av.addWidget(note("Светлая или тёмная — как в Windows, и меняется вместе с ней." if sys.platform == "win32"
                          else "Светлая или тёмная — как в системе, и меняется вместе с ней."))
        self.pick_dark = self._variant_button("theme_dark", TH.DARK_IDS)
        self.pick_light = self._variant_button("theme_light", TH.LIGHT_IDS)
        g = QGridLayout(); g.setHorizontalSpacing(8); g.setVerticalSpacing(5)
        g.addWidget(F.label("Днём:", 11.5, T.fg_a(0.7)), 0, 0)
        g.addWidget(self.pick_light, 0, 1)
        g.addWidget(F.label("Ночью:", 11.5, T.fg_a(0.7)), 1, 0)
        g.addWidget(self.pick_dark, 1, 1)
        g.setColumnStretch(2, 1)
        av.addLayout(g)
        av.addStretch(1)
        auto_row.addLayout(av, 1)
        left.addLayout(auto_row)

        for title, ids in (("Тёмные", TH.DARK_IDS), ("Светлые", TH.LIGHT_IDS)):
            left.addWidget(section(title))
            grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(8)
            for i, pid in enumerate(ids):
                grid.addWidget(self._card(pid), i // 3, i % 3)
            grid.setColumnStretch(3, 1)
            left.addLayout(grid)

        left.addWidget(section("Цвет акцента"))
        ar = QHBoxLayout(); ar.setSpacing(2); ar.setContentsMargins(0, 0, 0, 0)
        self.dots = {}
        tips = {"auto": "Системный — цвет акцента Windows, меняется вместе с ним" if sys.platform == "win32"
                        else "Системный — цвет акцента системы, меняется вместе с ним",
                "theme": "Из темы — свой цвет у каждой темы"}
        for key in ("auto", "theme", *TH.ACCENTS):
            d = AccentDot(key, self._dot_color(key))
            d.setToolTip(tips.get(key, TH.ACCENT_NAMES[key]))
            d.clicked.connect(lambda _=False, k=key: self._pick_accent(k))
            d.hovered.connect(self._hover_accent)
            ar.addWidget(d)
            self.dots[key] = d
        ar.addStretch(1)
        left.addLayout(ar)
        left.addStretch(1)
        body.addLayout(left, 1)

        right = QVBoxLayout(); right.setSpacing(6); right.setContentsMargins(0, 0, 0, 0)
        self.preview_title = F.label("", 11, T.fg_a(0.55), QFont.Weight.DemiBold)
        self.preview_title.setContentsMargins(8, 6, 0, 0)
        right.addWidget(self.preview_title)
        self.preview = PreviewPanel()
        right.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        hint = note("Наведите на тему, чтобы посмотреть, как будет выглядеть панель.", 10.5)
        hint.setFixedWidth(PreviewPanel.W - 12)
        hint.setContentsMargins(8, 0, 0, 0)
        right.addWidget(hint)
        right.addStretch(1)
        body.addLayout(right, 0)
        v.addLayout(body, 1)

        # Apply / Cancel
        bar = QHBoxLayout(); bar.setSpacing(8); bar.setContentsMargins(0, 8, 0, 0)
        self.apply_note = note("Чтобы применить тему, Fling перезапустится — это пара секунд.", 10.5)
        sp = self.apply_note.sizePolicy(); sp.setRetainSizeWhenHidden(True)
        self.apply_note.setSizePolicy(sp)
        bar.addWidget(self.apply_note, 1)
        self.b_cancel = button("Отменить", self._cancel)
        self.b_apply = button("Применить", self._apply, primary=True)
        bar.addWidget(self.b_cancel); bar.addWidget(self.b_apply)
        v.addLayout(bar)
        self._refresh_look()
        return w

    def _card(self, pid):
        card_ = ThemeCard(pid, lambda pid=pid: self._card_palettes(pid))
        card_.picked.connect(self._pick_theme)
        card_.hovered.connect(self._hover_theme)
        self.cards[pid] = card_
        return card_

    def _variant_button(self, key, ids):
        T = F.T
        b = QToolButton()
        b.setFont(F.font(11.5))
        b.setFixedHeight(24)
        b.setMinimumWidth(116)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        b.setStyleSheet(
            f"QToolButton{{border:1px solid {_css(T.fg_a(0.16))};border-radius:6px;padding:0 22px 0 9px;"
            f"background:{_css(T.fg_a(0.05))};color:{_css(T.fg)};text-align:left}}"
            f"QToolButton:hover{{background:{_css(T.acc_a(0.2))}}}"
            "QToolButton::menu-indicator{image:none;width:0}")
        chev = QLabel(b); chev.setPixmap(icon("chevron", 12, T.fg_a(0.6)).pixmap(QSize(12, 12)))
        chev.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        b._chev = chev
        m = QMenu(b)
        for pid in ids:
            a = m.addAction(TH.name(pid, F.LANG), lambda pid=pid: self._pick_variant(key, pid))
            a.hovered.connect(lambda pid=pid: self._hover_theme(pid))
        m.aboutToHide.connect(lambda: self._hover_theme(""))
        b.setMenu(m)
        old_resize = b.resizeEvent

        def resize(e, b=b):
            b._chev.move(b.width() - 19, (b.height() - 12) // 2)
            old_resize(e)
        b.resizeEvent = resize
        return b

    def _dot_color(self, key):
        if key == "auto":
            return lambda: self.sys_accent
        if key == "theme":
            def own():
                pid = TH.preset_for(self._look(), self.sys_dark)
                pr = TH.PRESETS[pid]
                return QColor(pr[5]), QColor(pr[3])
            return own
        def col():
            dark = TH.PRESETS[TH.preset_for(self._look(), self.sys_dark)][2]
            return QColor(TH.ACCENTS[key][0 if dark else 1])
        return col

    def _look(self, theme=None, accent=None):
        c = dict(self.pend)
        if theme:
            c["theme"] = theme
        if accent:
            c["accent"] = accent
        return c

    def _resolve(self, conf):
        return TH.resolve(conf, self.sys_dark, self.sys_accent)

    def _card_palettes(self, pid):
        if pid == "auto":                       # by night | by day
            return [TH.resolve(self._look("auto"), True, self.sys_accent),
                    TH.resolve(self._look("auto"), False, self.sys_accent)]
        return [self._resolve(self._look(pid))]

    def _pick_theme(self, pid):
        self.pend["theme"] = pid
        self._refresh_look()

    def _pick_variant(self, key, pid):
        self.pend[key] = pid
        self.pend["theme"] = "auto"           # choosing the day / night theme means "as the system"
        self._refresh_look()

    def _pick_accent(self, key):
        self.pend["accent"] = key
        self._refresh_look()

    def _hover_theme(self, pid):
        self.hover_theme = pid
        self._refresh_preview()

    def _hover_accent(self, key):
        self.hover_accent = key
        self._refresh_preview()

    def _refresh_preview(self):
        look = self._look(self.hover_theme or None, self.hover_accent or None)
        r = self._resolve(look)
        self.preview.set_palette(r)
        shown = self.hover_theme or self.pend["theme"]
        name = TH.name(r["id"], F.LANG)
        if shown == "auto":
            name = F.tr("Как в системе") + " · " + name
        self.preview_title.setText(F.tr("Предпросмотр") + " · " + name)

    def _refresh_look(self):
        for pid, c in self.cards.items():
            c.set_selected(pid == self.pend["theme"])
            c.update()
        for key, d in self.dots.items():
            d.setChecked(key == self.pend["accent"])
            d.update()
        self.pick_dark.setText(TH.name(self.pend["theme_dark"], F.LANG))
        self.pick_light.setText(TH.name(self.pend["theme_light"], F.LANG))
        dirty = self.pend != self.saved
        self.b_apply.setEnabled(dirty)
        self.b_cancel.setEnabled(dirty)
        self.apply_note.setVisible(dirty)
        self._refresh_preview()

    def _cancel(self):
        self.pend = dict(self.saved)
        self._refresh_look()

    def _apply(self):
        if self.pend == self.saved:
            return
        self._save(**self.pend)
        self._restart_into("appearance")

    # ----------------------------------------------------------- General
    def _page_general(self):
        T = F.T
        w, v = self._page()
        v.addWidget(heading("Основные"))
        box, lay = card()
        lay.addWidget(F.label("Язык", 12.5, weight=QFont.Weight.DemiBold))
        row = QHBoxLayout(); row.setSpacing(6)
        for code, title in (("ru", "Русский"), ("en", "English")):
            b = QPushButton()
            b.setText("  " + title)     # each language in its own name, never translated
            b.setIcon(F.flag_icon(code, 18, 12)); b.setIconSize(QSize(18, 12))
            b.setFont(F.font(12, QFont.Weight.DemiBold if code == F.LANG else QFont.Weight.Normal))
            b.setFixedHeight(28)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            on = code == F.LANG
            b.setStyleSheet(
                f"QPushButton{{border:1px solid {_css(T.accent if on else T.fg_a(0.16))};border-radius:7px;"
                f"padding:0 12px;background:{_css(T.acc_a(0.16) if on else T.fg_a(0.05))};"
                f"color:{_css(T.accent if on else T.fg)}}}"
                f"QPushButton:hover{{background:{_css(T.acc_a(0.2))}}}")
            if not on:
                b.clicked.connect(lambda _=False, c=code: self._set_lang(c))
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(note("Fling перезапустится на выбранном языке."))
        v.addWidget(box)

        box, lay = card()
        self.t_login = toggle_row(
            "Запускать при входе в систему",
            "Fling ждёт в области уведомлений и откроется по клику или Win+Shift+C." if sys.platform == "win32"
            else "Fling ждёт в трее и откроется по клику.",
            F.login_enabled(), lambda on: on != F.login_enabled() and F.toggle_login())
        lay.addWidget(self.t_login)
        lay.addWidget(self._hr())
        lay.addWidget(toggle_row("Компактная панель", "Только то, что играет, громкость и выбор ТВ.",
                                 bool(self.state.conf.get("compact")), self._set_compact))
        v.addWidget(box)

        row = QHBoxLayout(); row.setSpacing(8)
        row.addWidget(button("Как пользоваться", lambda: F.open_help(self.holder or self)))
        row.addWidget(button("Журнал Fling", lambda: F.P.open_path(F.P.LOG)))
        row.addWidget(button("Папка настроек", lambda: F.P.open_path(os.path.dirname(F.CONF))))
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        return w

    def _hr(self):
        line = QFrame(); line.setFixedHeight(1)
        line.setStyleSheet(f"background:{_css(F.T.fg_a(0.08))}")
        return line

    def _set_lang(self, code):
        self._save(lang=code)
        self._restart_into("general")

    def _set_compact(self, on):
        self._save(compact=bool(on))
        self.state.changed.emit()

    # ----------------------------------------------------------- Remote
    def _page_remote(self):
        w, v = self._page()
        v.addWidget(heading("Пульт"))
        box, lay = card()
        top = QHBoxLayout(); top.setSpacing(10)
        tv = QLabel(); tv.setPixmap(F.glyph("tv", 26, F.T.fg_a(0.7)).pixmap(QSize(26, 26)))
        top.addWidget(tv, 0, Qt.AlignmentFlag.AlignTop)
        tl = QVBoxLayout(); tl.setSpacing(1)
        self.tv_name = F.label("", 13, weight=QFont.Weight.DemiBold)
        self.tv_info = note("")
        tl.addWidget(self.tv_name); tl.addWidget(self.tv_info)
        top.addLayout(tl, 1)
        top.addWidget(button("Подключение ТВ…", self._open_setup), 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(top)
        v.addWidget(box)

        box, lay = card()
        lay.addWidget(toggle_row(
            "Клавиатура управляет ТВ",
            "Пока панель открыта, стрелки, Enter, Пробел, Backspace, Home и +/− уходят на ТВ. "
            "Esc закрывает панель.",
            self.state.conf.get("kb_capture", True), self._set_kb))
        v.addWidget(box)
        row = QHBoxLayout(); row.setSpacing(8)
        row.addWidget(button("ADB-консоль…", lambda: F.open_adb_console(self.holder or self, self.state)))
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        return w

    def _sync_remote(self):
        s = self.state
        dev = s.device
        self.tv_name.setText(dev["name"] if dev else F.tr("ТВ не выбран"))
        bits = []
        if dev:
            bits.append(dev.get("ip", ""))
            try:
                bits.append(F.tr("пульт по сети связан" if s.tv_paired else "пульт по сети не связан"))
                if s.adb_ok():
                    bits.append(F.tr("ADB подключён"))
            except Exception:
                pass
        self.tv_info.setText(" · ".join(b for b in bits if b))
        self.tv_info.setVisible(bool(bits))

    def _open_setup(self):
        h = self.holder
        if h is not None and hasattr(h, "open_setup"):
            h.open_setup()

    def _set_kb(self, on):
        p = self.holder
        if p is not None and hasattr(p, "_toggle_kb_capture") and p._kb_capture() != bool(on):
            p._toggle_kb_capture()               # also grabs / frees the keys right away
        else:
            self._save(kb_capture=bool(on))

    # ----------------------------------------------------------- Casting
    def _page_cast(self):
        T = F.T
        w, v = self._page()
        v.addWidget(heading("Трансляция"))
        box, lay = card()
        row = QHBoxLayout(); row.setSpacing(10)
        col = QVBoxLayout(); col.setSpacing(2)
        col.addWidget(F.label("Отправить вкладку", 12.5, weight=QFont.Weight.DemiBold))
        col.addWidget(note("Сочетание клавиш работает везде, даже когда панель закрыта."))
        row.addLayout(col, 1)
        key = F.label(F.SHORTCUT_LABEL, 12, T.fg, QFont.Weight.DemiBold)
        key.setStyleSheet(f"color:{_css(T.fg)};background:{_css(T.fg_a(0.08))};"
                          f"border:1px solid {_css(T.fg_a(0.14))};border-radius:6px;padding:3px 8px")
        row.addWidget(key, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(row)
        v.addWidget(box)

        box, lay = card()
        lay.addWidget(toggle_row("Уведомлять о смене видео",
                                 "Когда на ТВ начинается другое видео или трек, а панель закрыта.",
                                 self.state.conf.get("notify_track", True),
                                 lambda on: (self._save(notify_track=bool(on)), self.state.changed.emit())))
        lay.addWidget(self._hr())
        lay.addWidget(toggle_row("Связь с расширением браузера",
                                 "Расширение показывает Fling открытую вкладку и добавляет кнопку «На ТВ». "
                                 "Изменение вступит в силу после перезапуска Fling.",
                                 self.state.conf.get("bridge", True),
                                 lambda on: self._save(bridge=bool(on))))
        v.addWidget(box)
        row = QHBoxLayout(); row.setSpacing(8)
        row.addWidget(button("Установить расширение…", lambda: F.install_extension(self, self.state)))
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        return w

    # ----------------------------------------------------------- About
    def _page_about(self):
        T = F.T
        w, v = self._page()
        v.setSpacing(12)
        top = QHBoxLayout(); top.setSpacing(18)
        logo = QLabel(); logo.setPixmap(F.logo_pixmap(84, dpr=_dpr())); logo.setFixedSize(84, 84)
        top.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        tv = QVBoxLayout(); tv.setSpacing(3)
        tv.addWidget(F.label("Fling", 26, weight=QFont.Weight.Bold))
        tv.addWidget(F.label(F.tr("Версия ") + F.VERSION + " · " + F.P.OS_NAME, 12, T.fg_a(0.6)))
        d = F.label("Отправляйте видео и вкладки браузера на Chromecast и Google TV и управляйте телевизором "
                    "с компьютера: пульт, громкость, приложения, экран ТВ вживую.", 12.5, T.fg_a(0.85), wrap=True)
        d.setContentsMargins(0, 4, 0, 0)
        tv.addWidget(d)
        top.addLayout(tv, 1)
        v.addLayout(top)

        # updates
        box, lay = card()
        ur = QHBoxLayout(); ur.setSpacing(10)
        self.b_update = button("Проверить обновления", self._check_updates, icon_name="refresh")
        ur.addWidget(self.b_update)
        self.update_msg = F.label("", 11.5, T.fg_a(0.7), wrap=True)
        self.update_msg.setOpenExternalLinks(True)
        self.update_msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        ur.addWidget(self.update_msg, 1)
        lay.addLayout(ur)
        v.addWidget(box)

        # authors
        box, lay = card()
        lay.addWidget(F.label("Авторы", 12.5, weight=QFont.Weight.DemiBold))
        lay.addWidget(F.label("© 2026 orfixe и участники порта Fling", 12, T.fg_a(0.85)))
        a = lambda url, text: f'<a href="{url}" style="color:{_css(T.accent)};text-decoration:none">{text}</a>'
        orig = F.label(F.tr("Оригинальный Fling для macOS создали {a} и {b}").format(
                           a=a("https://github.com/damionrashford", "Damion Rashford"),
                           b=a("https://github.com/tatianathevisionary", "Tatiana"))
                       + " · " + a(ORIGINAL, "github.com/tatianathevisionary/fling"), 12, T.fg_a(0.85), wrap=True)
        orig.setOpenExternalLinks(True)
        orig.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        lay.addWidget(orig)
        lay.addWidget(note("Это неофициальный порт: его авторы Fling не проверяли и не одобряли."))
        lay.addWidget(self._hr())
        lay.addWidget(F.label("Лицензия MIT — для кода этого порта.", 12, T.fg_a(0.85)))
        lr = QHBoxLayout(); lr.setSpacing(2); lr.setContentsMargins(-8, 0, 0, 0)
        for title, fname, key in (("Лицензия", "LICENSE", "license"),
                                  ("Сторонние компоненты", "THIRD-PARTY-NOTICES.md", "notices"),
                                  ("Благодарности", "CREDITS.md", "credits")):
            lr.addWidget(link_button(title, lambda _=False, f=fname, k=key: self._open_doc(f, k)))
        lr.addStretch(1)
        lay.addLayout(lr)
        v.addWidget(box)

        # links
        box, lay = card()
        lay.addWidget(F.label("Ссылки", 12.5, weight=QFont.Weight.DemiBold))
        g = QGridLayout(); g.setHorizontalSpacing(4); g.setVerticalSpacing(0); g.setContentsMargins(-8, 0, 0, 0)
        for i, (title, key) in enumerate((("Исходный код", "repo"), ("Сообщить о проблеме", "issues"),
                                          ("Выпуски", "releases"), ("Что нового", "changelog"))):
            g.addWidget(link_button(title, LINKS[key]), i // 2, i % 2)
        g.setColumnStretch(2, 1)
        lay.addLayout(g)
        v.addWidget(box)

        row = QHBoxLayout(); row.setSpacing(10)
        self.b_copy = button("Скопировать сведения", self._copy_info, icon_name="copy")
        self.b_copy.setToolTip("Версия Fling, системы, Python и Qt — для сообщения о проблеме.")
        row.addWidget(self.b_copy)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        return w

    def _open_doc(self, fname, key):
        """The copy that ships with the program if there is one, else the page on GitHub."""
        here = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else \
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, fname)
        if os.path.isfile(path):
            F.P.open_path(path)
        else:
            QDesktopServices.openUrl(QUrl(LINKS[key]))

    @staticmethod
    def system_info() -> str:
        from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
        osname = f"{platform.system()} {platform.release()} ({platform.version()})"
        return (f"Fling {F.VERSION} · {osname} · Python {platform.python_version()} · "
                f"Qt {QT_VERSION_STR} · PyQt {PYQT_VERSION_STR} · {F.LANG}"
                + (" · frozen" if getattr(sys, "frozen", False) else ""))

    def _copy_info(self):
        QApplication.clipboard().setText(self.system_info())
        self.b_copy.setText("Скопировано")
        QTimer.singleShot(1800, lambda: self.b_copy.setText("Скопировать сведения"))

    def _check_updates(self):
        """Ask GitHub for the latest release. Anonymous, no token: while the repository
        is private GitHub answers 404, and we say so."""
        from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
        if not hasattr(self, "_nam"):
            self._nam = QNetworkAccessManager(self)
        req = QNetworkRequest(QUrl(LATEST_API))
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        req.setRawHeader(b"User-Agent", f"Fling/{F.VERSION}".encode())
        req.setTransferTimeout(10000)
        self.b_update.setEnabled(False)
        self.update_msg.setText(F.tr("Проверяю…"))
        reply = self._nam.get(req)

        def done():
            reply.deleteLater()
            self.b_update.setEnabled(True)
            code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if code == 404:
                self.update_msg.setText(F.tr("Репозиторий пока закрыт, проверить автоматически нельзя. "
                                             "Загляните в «Выпуски» на GitHub."))
                return
            if reply.error() != QNetworkReply.NetworkError.NoError or code != 200:
                self.update_msg.setText(F.tr("Не удалось связаться с GitHub."))
                return
            try:
                data = json.loads(bytes(reply.readAll()))
                tag = str(data.get("tag_name") or "").lstrip("vV")
                url = str(data.get("html_url") or LINKS["releases"])
            except Exception:
                self.update_msg.setText(F.tr("Не удалось связаться с GitHub."))
                return
            self.update_msg.setText(self.update_text(tag, url))
        reply.finished.connect(done)

    @staticmethod
    def update_text(tag: str, url: str) -> str:
        if _ver(tag) > _ver(F.VERSION):
            if not url.startswith("https://github.com/"):
                url = LINKS["releases"]
            return (F.tr("Доступна версия ") + tag + " · "
                    f'<a href="{url}" style="color:{_css(F.T.accent)}">{F.tr("Скачать")}</a>')
        return F.tr("У вас последняя версия.")


def _ver(s: str) -> tuple:
    out = []
    for part in s.split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out)


def open_settings(app_module, holder=None, page=None):
    """Open (or raise) the single Settings window. `holder` (the panel) keeps it alive."""
    global F
    F = app_module
    _register_phrases()
    state = getattr(holder, "state", None)
    for name in ("_help", "_setup"):                 # don't stack extra windows on each other
        other = getattr(holder, name, None)
        if other is not None and other.isVisible():
            other.hide()
    win = getattr(holder, "_settings", None)
    if win is None:
        win = SettingsWindow(state, holder)
        if holder is not None:
            holder._settings = win
    if page:
        win.show_page(page)
    win.show(); win.raise_(); win.activateWindow()
    return win
