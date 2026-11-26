# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"""The first-run welcome: language first, then a short animated tour, skippable.

Runs before the rest of the UI is built (fling_app.main), so the language picked here
applies everywhere from the start. Texts carry both languages themselves instead of going
through tr(), because translation is installed only after this window. Every page has a
small looping illustration painted with QPainter in the current theme's colours; nothing
is loaded from disk or the network. Settings → About can replay the tour (`replay`)."""
from __future__ import annotations

import math

from PyQt6.QtCore import (QEasingCurve, QEventLoop, QPointF, QPropertyAnimation, QRectF, Qt,
                          QTimer, QVariantAnimation)
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

F = None          # fling_app, set by run()

# (illustration, title ru/en, body ru/en)
PAGES = [
    ("cast",
     ("Видео и вкладки — на телевизор", "Videos and tabs, on the TV"),
     ("Откройте видео в Chrome или Edge и нажмите «Отправить эту вкладку» — оно продолжится на ТВ "
      "с того же места. Ссылки, файлы, папки и фото тоже отправляются одним действием.",
      "Open a video in Chrome or Edge and press Cast this tab — it carries on on the TV from the same "
      "moment. Links, files, folders and photos go to the TV in one step too.")),
    ("remote",
     ("Пульт прямо в компьютере", "The remote, right on your computer"),
     ("Стрелки, громкость, приложения и набор текста — с клавиатуры и мыши. Скажите в микрофон, "
      "что найти, а «LIVE» покажет экран ТВ в окне.",
      "Arrows, volume, apps and typing from the keyboard and mouse. Say what to look for into the mic, "
      "and LIVE shows the TV's screen in a window.")),
    ("pair",
     ("Подключите телевизор один раз", "Connect your TV once"),
     ("Fling сам найдёт Chromecast и Google TV в этой сети. Для пульта ТВ покажет код — введите его, "
      "и больше Fling не спросит. Другой ТВ можно добавить по IP или через Tailscale.",
      "Fling finds Chromecast and Google TV on this network by itself. For the remote, the TV shows a "
      "code — type it once and Fling won't ask again. Add another TV by IP or over Tailscale.")),
    ("mirror",
     ("Smart View для Samsung", "Smart View for Samsung"),
     ("Весь экран компьютера на Samsung — в один клик, даже в отеле без общей сети: по Wi‑Fi Direct. "
      "Fling сам подберёт разрешение, чтобы картинка не растягивалась.",
      "Your whole screen on a Samsung in one click, even in a hotel with no shared network, over "
      "Wi‑Fi Direct. Fling picks the resolution so the picture isn't stretched.")),
    ("tray",
     ("Fling живёт в трее", "Fling lives in the tray"),
     ("Значок у часов открывает панель, а сочетание клавиш — откуда угодно. В «Настройках» — темы, "
      "язык и всё, что можно скрыть или вернуть.",
      "The icon by the clock opens the panel, and so does the shortcut, from anywhere. Settings has "
      "themes, the language and everything you can hide or bring back.")),
]

UI = {
    "skip": ("Пропустить", "Skip"), "next": ("Далее", "Next"), "back": ("Назад", "Back"),
    "start": ("Начать", "Get started"), "title": ("Добро пожаловать в Fling", "Welcome to Fling"),
    "lang": ("Выберите язык", "Choose your language"),
    "langsub": ("Его можно сменить потом в Настройках.", "You can change it later in Settings."),
}


def _css(c: QColor) -> str:
    return F.Theme.css(c)


# ---------------------------------------------------------------- illustrations

class Art(QWidget):
    """A looping drawing for one page; `kind` picks it. Slides in when the page changes."""

    def __init__(self):
        super().__init__()
        self.kind = "cast"
        self.t = 0.0                    # loop time, seconds
        self.enter = 1.0                # 0 → 1 while the page slides in
        self.setMinimumHeight(190)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(520)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_enter)

    def _tick(self):
        if self.isVisible():
            self.t += 0.016
            self.update()

    def _on_enter(self, v):
        self.enter = float(v)
        self.update()

    def show_kind(self, kind: str):
        self.kind, self.t = kind, 0.0
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def showEvent(self, e):
        self._timer.start(16)
        super().showEvent(e)

    # ---- drawing helpers
    def _c(self, a=1.0, accent=False):
        T = F.T
        c = QColor(T.accent if accent else T.fg)
        c.setAlphaF(max(0.0, min(1.0, a * self.enter)))
        return c

    def _laptop(self, p, x, y, w):
        h = w * 0.62
        p.setPen(QPen(self._c(0.75), 2.2))
        p.setBrush(self._c(0.05))
        p.drawRoundedRect(QRectF(x, y, w, h), 6, 6)
        p.drawLine(QPointF(x - w * 0.12, y + h + 6), QPointF(x + w * 1.12, y + h + 6))
        return QRectF(x + 6, y + 6, w - 12, h - 12)

    def _tv(self, p, x, y, w):
        h = w * 0.58
        p.setPen(QPen(self._c(0.85), 2.4))
        p.setBrush(self._c(0.06))
        p.drawRoundedRect(QRectF(x, y, w, h), 7, 7)
        p.drawLine(QPointF(x + w / 2, y + h), QPointF(x + w / 2, y + h + 12))
        p.drawLine(QPointF(x + w / 2 - 22, y + h + 12), QPointF(x + w / 2 + 22, y + h + 12))
        return QRectF(x + 7, y + 7, w - 14, h - 14)

    def _play(self, p, c: QPointF, r: float, a=1.0):
        path = QPainterPath()
        path.moveTo(c.x() - r * 0.45, c.y() - r * 0.6)
        path.lineTo(c.x() + r * 0.65, c.y())
        path.lineTo(c.x() - r * 0.45, c.y() + r * 0.6)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._c(a, accent=True))
        p.drawPath(path)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        slide = (1 - self.enter) * 36
        p.translate(slide, 0)
        getattr(self, "_draw_" + self.kind, self._draw_cast)(p, self.width(), self.height())
        p.end()

    # ---- pages
    def _draw_cast(self, p, W, H):
        lap = self._laptop(p, W * 0.08, H * 0.42, W * 0.3)
        tv = self._tv(p, W * 0.55, H * 0.14, W * 0.38)
        k = (self.t % 2.6) / 2.6
        start = lap.center()
        end = tv.center()
        ctrl = QPointF((start.x() + end.x()) / 2, H * 0.02)
        if k < 0.7:                                      # the video flies along an arc
            u = k / 0.7
            e = 1 - (1 - u) ** 3
            pt = QPointF((1 - e) ** 2 * start.x() + 2 * (1 - e) * e * ctrl.x() + e * e * end.x(),
                         (1 - e) ** 2 * start.y() + 2 * (1 - e) * e * ctrl.y() + e * e * end.y())
            p.setPen(QPen(self._c(0.18, accent=True), 2, Qt.PenStyle.DashLine))
            path = QPainterPath(start)
            path.quadTo(ctrl, end)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._c(0.9, accent=True))
            p.drawRoundedRect(QRectF(pt.x() - 16, pt.y() - 11, 32, 22), 5, 5)
            self._play_white(p, pt, 7)
            self._play(p, lap.center(), 12, 0.35)
        else:                                            # it plays on the TV
            u = (k - 0.7) / 0.3
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._c(0.25 + 0.5 * u, accent=True))
            p.drawRoundedRect(tv, 4, 4)
            self._play_white(p, tv.center(), 16)
            p.setBrush(self._c(0.7))
            p.drawRect(QRectF(tv.left() + 8, tv.bottom() - 10, (tv.width() - 16) * u, 3))

    def _play_white(self, p, c, r):
        path = QPainterPath()
        path.moveTo(c.x() - r * 0.45, c.y() - r * 0.6)
        path.lineTo(c.x() + r * 0.65, c.y())
        path.lineTo(c.x() - r * 0.45, c.y() + r * 0.6)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        w = QColor("white")
        w.setAlphaF(self.enter)
        p.setBrush(w)
        p.drawPath(path)

    def _draw_remote(self, p, W, H):
        cx, cy, R = W * 0.3, H * 0.52, min(W, H) * 0.3
        p.setPen(QPen(self._c(0.3), 2))
        p.setBrush(self._c(0.05))
        p.drawEllipse(QPointF(cx, cy), R, R)
        order = ["up", "right", "down", "left", "ok"]
        lit = order[int(self.t / 0.55) % len(order)]
        pts = {"up": (0, -1), "right": (1, 0), "down": (0, 1), "left": (-1, 0)}
        for name, (dx, dy) in pts.items():
            c = QPointF(cx + dx * R * 0.62, cy + dy * R * 0.62)
            on = name == lit
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._c(0.9 if on else 0.12, accent=on))
            p.drawEllipse(c, R * 0.2, R * 0.2)
        p.setBrush(self._c(0.9 if lit == "ok" else 0.15, accent=True))
        p.drawEllipse(QPointF(cx, cy), R * 0.3, R * 0.3)
        p.setPen(self._c(1.0) if lit != "ok" else QColor("white"))
        p.setFont(F.font(13, QFont.Weight.Bold))
        p.drawText(QRectF(cx - 30, cy - 12, 60, 24), Qt.AlignmentFlag.AlignCenter, "OK")
        # the TV answers: a cursor moving over tiles
        tv = self._tv(p, W * 0.6, H * 0.2, W * 0.34)
        cols, rows = 3, 2
        tw, th = tv.width() / cols, tv.height() / rows
        sel = {"up": (1, 0), "right": (2, 1), "down": (1, 1), "left": (0, 1), "ok": (1, 1)}[lit]
        for i in range(cols):
            for j in range(rows):
                r = QRectF(tv.left() + i * tw + 3, tv.top() + j * th + 3, tw - 6, th - 6)
                on = (i, j) == sel
                p.setPen(QPen(self._c(0.9, accent=True), 2) if on else Qt.PenStyle.NoPen)
                p.setBrush(self._c(0.35 if on else 0.1, accent=on))
                p.drawRoundedRect(r, 3, 3)

    def _draw_pair(self, p, W, H):
        lap = self._laptop(p, W * 0.1, H * 0.42, W * 0.28)
        c = lap.center()
        for i in range(3):                               # search rings
            k = ((self.t / 1.8) + i / 3) % 1
            p.setPen(QPen(self._c((1 - k) * 0.6, accent=True), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 20 + k * W * 0.45, 20 + k * W * 0.45)
        tv = self._tv(p, W * 0.56, H * 0.14, W * 0.36)
        k = (self.t % 4.0) / 4.0
        code = "4F7A2C"
        shown = int(min(1, max(0, (k - 0.25) / 0.5)) * len(code))
        p.setPen(self._c(0.9))
        p.setFont(F.font(20, QFont.Weight.Bold, mono=True))
        p.drawText(tv, Qt.AlignmentFlag.AlignCenter, " ".join(code[:shown] + "·" * (len(code) - shown)))
        if k > 0.8:                                      # paired: a check mark
            p.setPen(QPen(QColor(F.T.green), 4, cap=Qt.PenCapStyle.RoundCap))
            o = QPointF(tv.right() - 18, tv.top() + 18)
            p.drawLine(o + QPointF(-8, 0), o + QPointF(-2, 6))
            p.drawLine(o + QPointF(-2, 6), o + QPointF(9, -7))

    def _draw_mirror(self, p, W, H):
        lap = self._laptop(p, W * 0.08, H * 0.4, W * 0.3)
        tv = self._tv(p, W * 0.52, H * 0.1, W * 0.42)
        k = (self.t % 3.0) / 3.0
        # the same "desktop" on both: windows sliding in on the laptop, mirrored on the TV
        for r, a in ((lap, 1.0), (tv, min(1.0, max(0.0, (k - 0.25) / 0.3)))):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._c(0.12 * a, accent=True))
            p.drawRect(r)
            p.setBrush(self._c(0.55 * a, accent=True))
            p.drawRoundedRect(QRectF(r.left() + r.width() * 0.08, r.top() + r.height() * 0.15,
                                     r.width() * 0.5, r.height() * 0.45), 3, 3)
            p.setBrush(self._c(0.3 * a))
            p.drawRoundedRect(QRectF(r.left() + r.width() * 0.5, r.top() + r.height() * 0.5,
                                     r.width() * 0.4, r.height() * 0.35), 3, 3)
        # Wi-Fi Direct waves between them
        mid = QPointF((lap.right() + tv.left()) / 2, (lap.center().y() + tv.center().y()) / 2)
        for i in range(3):
            q = ((self.t / 1.2) + i / 3) % 1
            p.setPen(QPen(self._c((1 - q) * 0.8, accent=True), 2.4, cap=Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            rad = 6 + q * 26
            p.drawArc(QRectF(mid.x() - rad, mid.y() - rad, rad * 2, rad * 2), -45 * 16, 90 * 16)

    def _draw_tray(self, p, W, H):
        bar = QRectF(W * 0.08, H * 0.7, W * 0.84, 34)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._c(0.1))
        p.drawRoundedRect(bar, 8, 8)
        p.setPen(self._c(0.6))
        p.setFont(F.font(12))
        p.drawText(bar.adjusted(0, 0, -14, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "21:40")
        icon = QPointF(bar.right() - 80, bar.center().y())
        bounce = abs(math.sin(self.t * 3)) * 6
        p.setBrush(self._c(1.0, accent=True))
        p.drawRoundedRect(QRectF(icon.x() - 11, icon.y() - 11 - bounce * 0.3, 22, 22), 6, 6)
        self._play_white(p, QPointF(icon.x(), icon.y() - bounce * 0.3), 7)
        # the panel pops up above the icon
        k = (self.t % 3.2) / 3.2
        a = min(1.0, max(0.0, (k - 0.2) / 0.2)) * (1 - max(0.0, (k - 0.85) / 0.15))
        panel = QRectF(icon.x() - 110, bar.top() - 12 - 120 * a, 140, 120 * a)
        if a > 0.02:
            p.setBrush(self._c(0.14 * a + 0.02))
            p.setPen(QPen(self._c(0.25 * a), 1))
            p.drawRoundedRect(panel, 10, 10)
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(4):
                p.setBrush(self._c((0.6 if i == 0 else 0.25) * a, accent=i == 0))
                p.drawRoundedRect(QRectF(panel.left() + 12, panel.top() + 14 + i * 24,
                                         panel.width() - 24 - (i % 2) * 30, 12), 4, 4)
        # a pointer coming to the icon
        ptr = QPointF(icon.x() + 40 - 40 * min(1, k * 5), icon.y() - 40 + 40 * min(1, k * 5))
        path = QPainterPath(ptr)
        path.lineTo(ptr + QPointF(0, 18))
        path.lineTo(ptr + QPointF(5, 13))
        path.lineTo(ptr + QPointF(12, 13))
        path.closeSubpath()
        p.setBrush(self._c(0.95))
        p.setPen(QPen(QColor(F.T.bg), 1.2))
        p.drawPath(path)


# ---------------------------------------------------------------- the window

class Welcome(QWidget):
    def __init__(self, lang: str | None, ask_language: bool = True):
        super().__init__(None, Qt.WindowType.Window)
        T = F.T
        self.lang = lang or ("ru" if F.LANG == "ru" else "en")
        self.page = -1 if ask_language else 0
        self.result = None
        self._loop: QEventLoop | None = None
        self.setWindowIcon(F.tray_icon())
        pal = self.palette()
        pal.setColor(self.backgroundRole(), T.bg)
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.setFixedSize(620, 520)

        v = QVBoxLayout(self)
        v.setContentsMargins(30, 26, 30, 22)
        v.setSpacing(12)
        self.content = QWidget()
        self.fx = QGraphicsOpacityEffect(self.content)
        self.content.setGraphicsEffect(self.fx)
        cv = QVBoxLayout(self.content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(12)
        self.logo = QLabel()                       # the language page: the logo, big and centred
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo.setStyleSheet("background:transparent")
        dpr = self.devicePixelRatioF() or 1.0
        self.logo.setPixmap(F.logo_pixmap(112, dpr=dpr))
        cv.addWidget(self.logo, 1)
        self.art = Art()
        cv.addWidget(self.art, 1)
        self.title = QLabel()
        self.title.setFont(F.font(22, QFont.Weight.Bold))
        self.title.setStyleSheet(f"color:{_css(T.fg)};background:transparent")
        self.title.setWordWrap(True)
        cv.addWidget(self.title)
        self.body = QLabel()
        self.body.setFont(F.font(13.5))
        self.body.setStyleSheet(f"color:{_css(T.fg_a(0.75))};background:transparent")
        self.body.setWordWrap(True)
        self.body.setMinimumHeight(64)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        cv.addWidget(self.body)
        # language choice (page -1)
        self.langs = QWidget()
        lh = QHBoxLayout(self.langs)
        lh.setContentsMargins(0, 4, 0, 0)
        lh.setSpacing(12)
        self.lang_btns = {}
        for code, name, sub in (("ru", "Русский", "Russian"), ("en", "English", "Английский")):
            b = QPushButton(f"{name}\n{sub}")
            b.setFont(F.font(15, QFont.Weight.DemiBold))
            b.setFixedHeight(76)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, c=code: self._pick_lang(c))
            self.lang_btns[code] = b
            lh.addWidget(b, 1)
        cv.addWidget(self.langs)
        v.addWidget(self.content, 1)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.skip = self._flat(self._skip)
        nav.addWidget(self.skip)
        nav.addStretch(1)
        self.dots = QLabel()
        self.dots.setStyleSheet("background:transparent")
        nav.addWidget(self.dots)
        nav.addStretch(1)
        self.back = self._flat(self._back)
        nav.addWidget(self.back)
        self.next = QPushButton()
        self.next.setFont(F.font(13, QFont.Weight.DemiBold))
        self.next.setFixedHeight(36)
        self.next.setMinimumWidth(120)
        self.next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next.setStyleSheet(
            f"QPushButton{{border:none;border-radius:10px;padding:0 18px;background:{_css(T.accent)};color:white}}"
            f"QPushButton:hover{{background:{_css(T.acc_a(0.85))}}}")
        self.next.clicked.connect(self._next)
        nav.addWidget(self.next)
        v.addLayout(nav)
        self._show_page(animate=False)

    def _flat(self, on):
        T = F.T
        b = QPushButton()
        b.setFont(F.font(12.5))
        b.setFixedHeight(36)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(f"QPushButton{{border:none;background:transparent;color:{_css(T.fg_a(0.6))};padding:0 10px}}"
                        f"QPushButton:hover{{color:{_css(T.fg)}}}")
        b.clicked.connect(on)
        return b

    def _s(self, pair) -> str:
        return pair[0] if self.lang == "ru" else pair[1]

    def _lang_css(self):
        T = F.T
        for code, b in self.lang_btns.items():
            on = code == self.lang
            b.setStyleSheet(
                f"QPushButton{{border:2px solid {_css(T.accent if on else T.fg_a(0.14))};border-radius:14px;"
                f"background:{_css(T.acc_a(0.14) if on else T.fg_a(0.04))};color:{_css(T.fg)}}}"
                f"QPushButton:hover{{background:{_css(T.acc_a(0.2))}}}")

    def _show_page(self, animate=True):
        def fill():
            lang_page = self.page < 0
            self.langs.setVisible(lang_page)
            self.setWindowTitle(self._s(UI["title"]))
            self.skip.setText(self._s(UI["skip"]))
            self.back.setText(self._s(UI["back"]))
            self.back.setVisible(self.page > 0)
            last = self.page == len(PAGES) - 1
            self.next.setText(self._s(UI["start"] if last else UI["next"]))
            self.logo.setVisible(lang_page)
            self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter if lang_page else Qt.AlignmentFlag.AlignLeft)
            self.body.setAlignment((Qt.AlignmentFlag.AlignHCenter if lang_page else Qt.AlignmentFlag.AlignLeft)
                                   | Qt.AlignmentFlag.AlignTop)
            if lang_page:
                self.art.setVisible(False)
                self.title.setText("Добро пожаловать · Welcome")
                self.body.setText("Выберите язык · Choose your language\n"
                                  "Его можно сменить в Настройках · You can change it in Settings")
                self._lang_css()
                self.skip.setVisible(False)
            else:
                kind, title, body = PAGES[self.page]
                self.art.setVisible(True)
                self.art.show_kind(kind)
                self.title.setText(self._s(title))
                self.body.setText(self._s(body))
                self.skip.setVisible(not last)
            self._dots()
        if not animate:
            fill()
            return
        out = QPropertyAnimation(self.fx, b"opacity", self)
        out.setDuration(140)
        out.setStartValue(1.0)
        out.setEndValue(0.0)

        def back_in():
            fill()
            a = QPropertyAnimation(self.fx, b"opacity", self)
            a.setDuration(260)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        out.finished.connect(back_in)
        out.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _dots(self):
        T = F.T
        if self.page < 0:
            self.dots.setText("")
            return
        parts = []
        for i in range(len(PAGES)):
            c = _css(T.accent) if i == self.page else _css(T.fg_a(0.25))
            parts.append(f'<span style="color:{c};font-size:18px">●</span>')
        self.dots.setText("&nbsp;".join(parts))

    def _pick_lang(self, code):
        self.lang = code
        self._lang_css()
        self.page = 0
        self._show_page()

    def _next(self):
        if self.page >= len(PAGES) - 1:
            self._finish(skipped=False)
            return
        self.page += 1
        self._show_page()

    def _back(self):
        if self.page > 0:
            self.page -= 1
            self._show_page()

    def _skip(self):
        self._finish(skipped=True)

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.page >= 0:
            self._next()
        elif k == Qt.Key.Key_Left:
            self._back()
        elif k == Qt.Key.Key_Escape and self.page >= 0:
            self._skip()
        else:
            super().keyPressEvent(e)

    def _finish(self, skipped):
        self.result = {"lang": self.lang, "skipped": skipped}
        self.close()

    def closeEvent(self, e):
        if self.result is None:                     # closed with the window's ×: same as Skip
            self.result = {"lang": self.lang, "skipped": True}
        if self._loop is not None:
            self._loop.quit()
        super().closeEvent(e)

    def run(self) -> dict:
        self._loop = QEventLoop()
        self.show()
        self.raise_()
        self.activateWindow()
        self._loop.exec()
        return self.result or {"lang": self.lang, "skipped": True}


def needed(conf: dict) -> bool:
    """First run: no welcome yet on this computer and no TV ever used."""
    return not conf.get("welcome_done") and not conf.get("device") and not conf.get("devices")


def run(app_module, conf: dict) -> dict:
    """Language first, then the tour. Returns {"lang", "skipped"}; the caller saves it."""
    global F
    F = app_module
    w = Welcome(conf.get("lang") if conf.get("lang") in ("ru", "en") else None, ask_language=True)
    res = w.run()
    w.deleteLater()
    return res


def replay(app_module, parent=None):
    """Settings → About: the tour again, without the language page."""
    global F
    F = app_module
    w = Welcome(F.LANG, ask_language=False)
    w.show()
    return w
