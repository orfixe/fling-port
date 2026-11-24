# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"""Compose docs/screenshots/hero.png: the four app screenshots as even cards in one row.

    python docs/screenshots/make_hero.py

Every screenshot is cropped to its panel, scaled to the same UI scale (1.5 px per
logical pixel), given rounded corners and a soft shadow, and the cards are
centred on one line on a rounded blue-violet stage in the logo's colours.
"""
import os
import sys

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QRadialGradient
from PyQt6.QtWidgets import (QApplication, QGraphicsDropShadowEffect, QGraphicsPixmapItem,
                             QGraphicsScene)
from PyQt6.QtGui import QPixmap

HERE = os.path.dirname(os.path.abspath(__file__))
SCALE = 1.5           # output px per logical UI px
GAP, MARGIN, RADIUS = 64, 72, 16


def bbox(img, is_panel):
    """Bounding box of the pixels that belong to the panel."""
    w, h = img.width(), img.height()
    xs = [x for x in range(w) if is_panel(img.pixelColor(x, h - 40))]
    ys = [y for y in range(h) if is_panel(img.pixelColor(w // 2, y))]
    return QRect(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def card(img, logical_width):
    k = SCALE * logical_width / img.width()
    img = img.scaled(round(img.width() * k), round(img.height() * k),
                     Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    out = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, img.width(), img.height()), RADIUS, RADIUS)
    p.setClipPath(path)
    p.drawImage(0, 0, img)
    p.end()
    return out


def load():
    shots = []
    # Windows panel (already cut to the panel, 1.5x); trim the 1 px transparent ring
    w = QImage(os.path.join(HERE, "windows-panel-en.png"))
    shots.append(card(w.copy(QRect(1, 1, w.width() - 2, w.height() - 2)), 340))
    # browser extension popup (2x, 320 css px)
    e = QImage(os.path.join(HERE, "extension-popup-en.png"))
    shots.append(card(e, 320))
    # KDE panel: light panel on a dark wallpaper
    l = QImage(os.path.join(HERE, "linux-panel-en.png"))
    r = bbox(l, lambda c: c.lightness() > 200)
    shots.append(card(l.copy(r), 340))
    # Android phone screen (1 dp = 1 px)
    a = QImage(os.path.join(HERE, "android-en.png"))
    shots.append(card(a, 320))
    return shots


def main():
    app = QApplication(sys.argv)   # noqa: F841 (needed for QGraphicsEffect rendering)
    shots = load()
    H = max(s.height() for s in shots)
    W = sum(s.width() for s in shots) + GAP * (len(shots) - 1) + 2 * MARGIN
    scene = QGraphicsScene(0, 0, W, H + 2 * MARGIN)
    x = MARGIN
    for s in shots:
        it = QGraphicsPixmapItem(QPixmap.fromImage(s))
        it.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        it.setPos(QPointF(x, MARGIN + (H - s.height()) / 2))     # centred on one line
        sh = QGraphicsDropShadowEffect()
        sh.setBlurRadius(42); sh.setOffset(0, 10); sh.setColor(QColor(0, 0, 0, 90))
        it.setGraphicsEffect(sh)
        scene.addItem(it)
        x += s.width() + GAP
    out = QImage(int(W), int(H + 2 * MARGIN), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    stage = QPainterPath(); stage.addRoundedRect(QRectF(0, 0, W, H + 2 * MARGIN), 36, 36)
    g = QLinearGradient(0, 0, W, H + 2 * MARGIN)
    g.setColorAt(0, QColor("#1d3b78")); g.setColorAt(0.55, QColor("#2a2a6e")); g.setColorAt(1, QColor("#3b2470"))
    p.fillPath(stage, g)
    glow = QRadialGradient(W * 0.5, -H * 0.2, W * 0.6)
    glow.setColorAt(0, QColor(120, 160, 255, 70)); glow.setColorAt(1, QColor(120, 160, 255, 0))
    p.fillPath(stage, glow)
    scene.render(p)
    p.end()
    path = os.path.join(HERE, "hero.png")
    out.save(path)
    print(path, out.width(), out.height())


if __name__ == "__main__":
    main()
