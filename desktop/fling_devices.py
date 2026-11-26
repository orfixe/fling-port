# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"""The TV chooser: a small window instead of a context menu.

Opening it starts a search right away; while Fling looks for TVs a radar animation
runs and the TVs already known stay listed. Each TV is a card (name, model, address,
remote pairing); a click switches to it. A TV can be added by IP inline, and TVs added
by hand can be removed. The window follows `State.changed`, so a search started
anywhere else (tray, Connect TV) shows here too.
"""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

F = None          # fling_app, set by open_picker()

PHRASES = [
    ("Fling — телевизоры", "Fling — TVs"),
    ("Телевизоры", "TVs"),
    ("Искать снова", "Search again"),
    ("Ищу телевизоры в этой сети…", "Looking for TVs on this network…"),
    ("Обычно это занимает до 10 секунд.", "This usually takes up to 10 seconds."),
    ("Телевизоры не найдены", "No TVs found"),
    ("Проверьте, что телевизор включён и подключён к той же сети Wi‑Fi, что и компьютер. "
     "Если ТВ в другой подсети, добавьте его по IP-адресу.",
     "Make sure the TV is on and connected to the same Wi‑Fi network as this computer. "
     "If the TV is on another subnet, add it by its IP address."),
    ("Только что найден в сети", "Seen on the network just now"),
    ("Сейчас выбран", "Selected"),
    ("пульт связан", "remote paired"),
    ("добавлен по IP", "added by IP"),
    ("Убрать этот ТВ из списка", "Remove this TV from the list"),
    ("Добавить по IP", "Add by IP"),
    ("Добавить", "Add"),
    ("IP-адрес ТВ, например 192.168.1.50", "TV's IP address, e.g. 192.168.1.50"),
    ("IP-адрес есть на ТВ: Настройки → Сеть.", "The TV shows its IP address in Settings → Network."),
    ("Проверяю {ip}…", "Checking {ip}…"),
    ("Найдено: {n}", "Found: {n}"),
    ("Выбрать ТВ…", "Choose TV…"),
    ("через Tailscale", "via Tailscale"),
]


def _css(c: QColor) -> str:
    return F.Theme.css(c)


# ---------------------------------------------------------------- radar animation

class Radar(QWidget):
    """Three rings spreading from a TV icon while the search runs."""

    def __init__(self, size: int = 64):
        super().__init__()
        self.setFixedSize(size, size)
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)

    def start(self):
        self._t0 = time.monotonic()
        self._timer.start()
        self.show()

    def stop(self):
        self._timer.stop()

    def paintEvent(self, _):
        T = F.T
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width()
        c = s / 2
        t = time.monotonic() - self._t0
        for k in range(3):                        # three rings, a third of a period apart
            ph = (t / 1.8 + k / 3) % 1.0
            r = 10 + ph * (c - 11)
            col = QColor(T.accent)
            col.setAlphaF(max(0.0, 0.55 * (1 - ph)))
            pen = QPen(col, 1.6)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(c - r, c - r, 2 * r, 2 * r))
        dot = QColor(T.accent)
        dot.setAlphaF(0.16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot)
        p.drawEllipse(QRectF(c - 15, c - 15, 30, 30))
        F.glyph("tv", 18, T.accent, 1.6).paint(p, int(c - 9), int(c - 9), 18, 18)
        p.end()


class Spinner(QWidget):
    """A small arc turning inside the "Search again" button."""

    def __init__(self, size: int = 14):
        super().__init__()
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._a = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        self._a = (self._a + 9) % 360
        self.update()

    def set_running(self, on: bool):
        (self._timer.start if on else self._timer.stop)()
        self.update()

    def paintEvent(self, _):
        T = F.T
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width()
        pen = QPen(T.accent, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        r = QRectF(1.5, 1.5, s - 3, s - 3)
        if self._timer.isActive():
            p.drawArc(r, -self._a * 16, 270 * 16)
        else:                                      # still: a "refresh" arrow
            p.drawArc(r, 60 * 16, 280 * 16)
            a = math.radians(60)
            x, y = s / 2 + (s / 2 - 1.5) * math.cos(a), s / 2 - (s / 2 - 1.5) * math.sin(a)
            p.drawLine(int(x), int(y), int(x + 4), int(y))
            p.drawLine(int(x), int(y), int(x), int(y - 4))
        p.end()


# ---------------------------------------------------------------- one TV

class TvCard(QFrame):
    clicked = pyqtSignal()
    remove = pyqtSignal()

    def __init__(self, d: dict, current: bool, paired: bool, fresh: bool, online=None):
        super().__init__()
        T = F.T
        self.setObjectName("card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(58)
        border = _css(T.accent) if current else _css(T.fg_a(0.10))
        bg = _css(T.acc_a(0.12)) if current else _css(T.fg_a(0.04))
        self.setStyleSheet(f"QFrame#card{{background:{bg};border:1px solid {border};border-radius:10px}}"
                           f"QFrame#card:hover{{background:{_css(T.acc_a(0.18) if current else T.fg_a(0.08))}}}")
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 10, 0)
        h.setSpacing(11)
        ic = QLabel()
        ic.setPixmap(F.glyph("tv", 22, T.accent if current else T.fg_a(0.75), 1.6).pixmap(22, 22))
        ic.setFixedSize(22, 22)
        ic.setStyleSheet("background:transparent;border:none")
        h.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.setContentsMargins(0, 0, 0, 0)
        name = F.label(d.get("name") or d["ip"], 13, T.fg, QFont.Weight.DemiBold)
        name.setStyleSheet(name.styleSheet() + ";border:none")
        name.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        col.addWidget(name)
        ip = "" if d["ip"].startswith("wfd:") else d["ip"]      # Miracast: no IP, Wi-Fi Direct
        bits = [b for b in (d.get("model"), ip) if b and b != d.get("name")]
        if paired:
            bits.append(F.tr("пульт связан"))
        if d.get("via") == "tailscale":
            bits.append(F.tr("через Tailscale"))
        elif d.get("manual"):
            bits.append(F.tr("добавлен по IP"))
        sub = F.label(" · ".join(bits), 10.5, T.fg_a(0.55))
        sub.setStyleSheet(sub.styleSheet() + ";border:none")
        sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        col.addWidget(sub)
        h.addLayout(col, 1)
        if online is not None or fresh:                # reachable now (green) / not (red)
            dot = QLabel()
            dot.setFixedSize(7, 7)
            ok = online if online is not None else True
            dot.setToolTip(F.tr("В сети" if online else "Не в сети" if online is False else "Только что найден в сети"))
            dot.setStyleSheet(f"background:{_css(T.green if ok else T.red)};border-radius:3px;border:none")
            h.addWidget(dot)
        if current:
            mark = QLabel()
            mark.setFixedSize(20, 20)
            mark.setToolTip(F.tr("Сейчас выбран"))
            mark.setStyleSheet(f"background:{_css(T.accent)};border-radius:10px;border:none")
            mark.setPixmap(_check_pixmap(20))
            h.addWidget(mark)
        if d.get("manual"):
            x = QPushButton()
            x.setFixedSize(24, 24)
            x.setCursor(Qt.CursorShape.PointingHandCursor)
            x.setToolTip(F.tr("Убрать этот ТВ из списка"))
            x.setIcon(_cross_icon(12, T.fg_a(0.6)))
            x.setIconSize(QSize(12, 12))
            x.setStyleSheet(f"QPushButton{{border:none;border-radius:12px;background:transparent}}"
                            f"QPushButton:hover{{background:{_css(T.fg_a(0.12))}}}")
            x.clicked.connect(self.remove.emit)
            h.addWidget(x)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


def _cross_icon(s: int, color: QColor):
    from PyQt6.QtGui import QPixmap, QGuiApplication, QIcon
    dpr = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1
    pm = QPixmap(int(s * dpr), int(s * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    m = s * 0.2
    p.drawLine(int(m), int(m), int(s - m), int(s - m))
    p.drawLine(int(s - m), int(m), int(m), int(s - m))
    p.end()
    return QIcon(pm)


def _check_pixmap(s: int):
    from PyQt6.QtGui import QPixmap, QGuiApplication
    dpr = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1
    pm = QPixmap(int(s * dpr), int(s * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("white"), 2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(*[__import__("PyQt6.QtCore", fromlist=["QPointF"]).QPointF(x * s, y * s)
                     for x, y in ((0.29, 0.52), (0.44, 0.66), (0.72, 0.36))])
    p.end()
    return pm


# ---------------------------------------------------------------- the window

class DevicePicker(QWidget):
    def __init__(self, state):
        super().__init__(None, Qt.WindowType.Window)
        T = F.T
        self.state = state
        self.setWindowTitle(F.tr("Fling — телевизоры"))
        self.setWindowIcon(F.tray_icon())
        pal = self.palette()
        pal.setColor(self.backgroundRole(), T.bg)
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.setFixedWidth(380)
        self._seen: dict[str, float] = {}       # ip → when the last search saw it
        self._was_scanning = False
        self._added_ip = None

        # Everything sits in a scroll area: with the tailnet group and its security note the
        # window can be taller than a small or scaled screen, and must scroll, not overlap.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.body = QWidget()
        self.body.setObjectName("pickbody")
        self.body.setStyleSheet("QWidget#pickbody{background:transparent}")
        self.page = QScrollArea()
        self.page.setWidget(self.body)
        self.page.setWidgetResizable(True)
        self.page.setFrameShape(QFrame.Shape.NoFrame)
        self.page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page.setStyleSheet("QScrollArea{background:transparent}"
                                "QWidget#qt_scrollarea_viewport{background:transparent}")
        outer.addWidget(self.page)
        v = QVBoxLayout(self.body)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(F.label("Телевизоры", 17, T.fg, QFont.Weight.Bold), 1)
        self.again = QPushButton()
        self.again.setText("Искать снова")
        self.again.setFont(F.font(12))
        self.again.setCursor(Qt.CursorShape.PointingHandCursor)
        self.again.setFixedHeight(28)
        self.again.setStyleSheet(
            f"QPushButton{{border:1px solid {_css(T.fg_a(0.16))};border-radius:14px;"
            f"padding:0 12px 0 30px;color:{_css(T.fg)};background:{_css(T.fg_a(0.05))}}}"
            f"QPushButton:hover{{background:{_css(T.acc_a(0.18))}}}"
            f"QPushButton:disabled{{color:{_css(T.fg_a(0.45))}}}")
        self.again.clicked.connect(self.search)
        self.spin = Spinner(14)
        self.spin.setParent(self.again)
        self.spin.move(11, 7)
        head.addWidget(self.again)
        v.addLayout(head)

        # searching: radar + text
        self.searching = QWidget()
        sh = QHBoxLayout(self.searching)
        sh.setContentsMargins(4, 0, 0, 0)
        sh.setSpacing(14)
        self.radar = Radar(64)
        sh.addWidget(self.radar)
        st = QVBoxLayout()
        st.setSpacing(2)
        st.addStretch(1)
        st.addWidget(F.label("Ищу телевизоры в этой сети…", 13, T.fg, QFont.Weight.DemiBold))
        st.addWidget(F.label("Обычно это занимает до 10 секунд.", 11, T.fg_a(0.55)))
        st.addStretch(1)
        sh.addLayout(st, 1)
        v.addWidget(self.searching)

        # the list
        self.list = QWidget()
        self.list.setStyleSheet("background:transparent")
        self.rows = QVBoxLayout(self.list)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(8)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.list)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea{background:transparent}")
        v.addWidget(self.scroll, 1)

        # nothing found
        self.empty = QFrame()
        self.empty.setObjectName("empty")
        self.empty.setStyleSheet(f"QFrame#empty{{background:{_css(T.fg_a(0.04))};border-radius:10px}}")
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(14, 12, 14, 12)
        el.setSpacing(4)
        el.addWidget(F.label("Телевизоры не найдены", 13, T.fg, QFont.Weight.DemiBold))
        el.addWidget(F.label("Проверьте, что телевизор включён и подключён к той же сети Wi‑Fi, что и компьютер. "
                             "Если ТВ в другой подсети, добавьте его по IP-адресу.", 11, T.fg_a(0.6), wrap=True))
        v.addWidget(self.empty)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{_css(T.fg_a(0.08))}")
        v.addWidget(line)

        # add by IP
        v.addWidget(F.label("Добавить по IP", 12, T.fg, QFont.Weight.DemiBold))
        ar = QHBoxLayout()
        ar.setSpacing(8)
        self.ip = QLineEdit()
        self.ip.setPlaceholderText("IP-адрес ТВ, например 192.168.1.50")
        self.ip.setFont(F.font(12))
        self.ip.setFixedHeight(30)
        self.ip.setStyleSheet(
            f"QLineEdit{{border:1px solid {_css(T.fg_a(0.16))};border-radius:8px;padding:0 9px;"
            f"background:{_css(T.fg_a(0.05))};color:{_css(T.fg)}}}"
            f"QLineEdit:focus{{border-color:{_css(T.accent)}}}")
        self.ip.returnPressed.connect(self.add_ip)
        ar.addWidget(self.ip, 1)
        self.add = QPushButton()
        self.add.setText("Добавить")
        self.add.setFont(F.font(12, QFont.Weight.DemiBold))
        self.add.setFixedHeight(30)
        self.add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add.setStyleSheet(
            f"QPushButton{{border:none;border-radius:8px;padding:0 14px;color:white;background:{_css(T.accent)}}}"
            f"QPushButton:hover{{background:{_css(T.accent.lighter(112))}}}"
            f"QPushButton:disabled{{background:{_css(T.fg_a(0.15))};color:{_css(T.fg_a(0.5))}}}")
        self.add.clicked.connect(self.add_ip)
        ar.addWidget(self.add)
        v.addLayout(ar)
        self.hint = F.label("IP-адрес есть на ТВ: Настройки → Сеть.", 10.5, T.fg_a(0.5), wrap=True)
        v.addWidget(self.hint)

        # TVs on the tailnet (fling_tailscale): remote control away from home
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{_css(T.fg_a(0.08))}")
        v.addWidget(line)
        self.tailnet = F.TS.TailnetSection(state)
        v.addWidget(self.tailnet)

        state.changed.connect(self.sync)
        self.sync()

    # -- actions
    def search(self):
        if not self.state.scanning:
            self.state.scan()
        self.tailnet.refresh()

    def add_ip(self):
        ip = self.ip.text().strip()
        if not ip:
            self.ip.setFocus()
            return
        self.state.last_error = None
        self._added_ip = ip
        if F.TS.wants(ip):                     # a tailnet IP or a MagicDNS name
            F.TS.add(self.state, ip, on_ip=lambda real: setattr(self, "_added_ip", real))
            self.hint.setText(F.tr("Проверяю {ip} через Tailscale…").replace("{ip}", ip))
        else:
            self.state.add_device_ip(ip)
            self.hint.setText(F.tr("Проверяю {ip}…").replace("{ip}", ip))
        self.hint.setStyleSheet(f"color:{_css(F.T.fg_a(0.6))};background:transparent")

    def pick(self, d):
        self.state.select_device(d)
        self.close()

    # -- view
    def sync(self):
        T = F.T
        s = self.state
        scanning = bool(s.scanning)
        if self._was_scanning and not scanning:            # a search just ended
            now = time.monotonic()
            for d in s.devices:
                if not d.get("manual"):
                    self._seen[d["ip"]] = now
        self._was_scanning = scanning
        self.searching.setVisible(scanning)
        (self.radar.start if scanning else self.radar.stop)()
        self.spin.set_running(scanning)
        self.again.setEnabled(not scanning)
        self.empty.setVisible(not scanning and not s.devices)
        # cards
        while self.rows.count():
            it = self.rows.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        cur = s.device["ip"] if s.device else None
        fresh_since = time.monotonic() - 20
        for d in s.devices:
            card = TvCard(d, d["ip"] == cur, bool(s.atv.paired(d["ip"])),
                          self._seen.get(d["ip"], 0) > fresh_since, s.tv_status(d))
            card.clicked.connect(lambda d=d: self.pick(d))
            card.remove.connect(lambda d=d: self.state.forget_device(d))
            self.rows.addWidget(card)
        self.rows.addStretch(1)
        n = len(s.devices)
        self.scroll.setVisible(n > 0)
        self.scroll.setFixedHeight(min(n, 4) * 66 - (8 if n else 0))
        # add-by-IP feedback
        if self._added_ip:
            err = s.last_error
            if err:
                self.hint.setText(F.tr(err))
                self.hint.setStyleSheet(f"color:{_css(T.red)};background:transparent")
                self._added_ip = None
            elif any(d["ip"] == self._added_ip for d in s.devices):
                self.ip.clear()
                self.hint.setText(F.tr("IP-адрес есть на ТВ: Настройки → Сеть."))
                self.hint.setStyleSheet(f"color:{_css(T.fg_a(0.5))};background:transparent")
                self._added_ip = None
        self.adjustSize()

    def sizeHint(self):
        """The whole content, but no taller than the screen (the rest scrolls)."""
        w = self.width()
        lay = self.body.layout()
        # measured as if the scroll bar were there: once shown, it only goes away if the
        # narrower text fits too
        sbw = self.page.verticalScrollBar().sizeHint().width()
        h = lay.totalHeightForWidth(w - sbw) if lay.hasHeightForWidth() else -1
        if h <= 0:
            h = self.body.sizeHint().height()
        scr = self.screen()
        if scr is not None:
            h = min(h, scr.availableGeometry().height() - 60)
        return QSize(w, max(h, 240))

    def adjustSize(self):
        """QWidget.adjustSize caps a window at 2/3 of the screen; fit the content instead
        (also called by the tailnet group after it changes)."""
        self.resize(self.sizeHint())

    def closeEvent(self, e):
        self.radar.stop()
        self.spin.set_running(False)
        super().closeEvent(e)


def open_picker(app_module, holder, search: bool = True):
    """Open (or raise) the TV chooser for the panel `holder`; starts a search unless one runs."""
    global F
    F = app_module
    win = getattr(holder, "_devices", None)
    if win is None:
        win = DevicePicker(holder.state)
        holder._devices = win
    win.show()
    win.raise_()
    win.activateWindow()
    if search:
        win.search()
    return win
