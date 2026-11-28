# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
"""Renders docs/screenshots/flight.gif — the README's short animation: a command (Fling's white
ball, as in the logo) is flung from a phone, a Windows PC, a Mac and a Linux PC to the TV, the
bubbles trailing it, and the TV shows what arrived.

    python docs/screenshots/make_flight.py frames_dir       # PyQt6: draws the frames
    python docs/screenshots/make_flight.py --gif frames_dir # Pillow: frames → flight.gif
"""
import math
import os
import sys

W, H, FPS = 880, 360, 25
SHOT = 1.55                     # seconds per device
DEVICES = [("Android", "phone", "Cast video"), ("Windows", "laptop", "Volume +"),
           ("macOS", "laptop", "Pause"), ("Linux", "monitor", "Open YouTube")]
TV = (560, 70, 270, 165)        # x, y, w, h of the screen
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flight.gif")


def ease(t):
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


def frames(out_dir):
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen
    from PyQt6.QtWidgets import QApplication
    app = QApplication([])                          # kept: Qt needs it alive while drawing text
    os.makedirs(out_dir, exist_ok=True)
    white = QColor("white")
    rows = [(70, 46 + i * 78) for i in range(4)]          # each device's centre-left
    total = int((len(DEVICES) * SHOT + 1.2) * FPS)

    def device(p, kind, x, y, on):
        col = QColor(255, 255, 255, 255 if on else 120)
        p.setPen(QPen(col, 2.6)); p.setBrush(QColor(255, 255, 255, 40 if on else 10))
        if kind == "phone":
            p.drawRoundedRect(QRectF(x + 14, y - 26, 30, 52), 7, 7)
        elif kind == "laptop":
            p.drawRoundedRect(QRectF(x, y - 22, 58, 36), 5, 5)
            p.drawLine(QPointF(x - 8, y + 20), QPointF(x + 66, y + 20))
        else:
            p.drawRoundedRect(QRectF(x, y - 26, 58, 38), 5, 5)
            p.drawLine(QPointF(x + 29, y + 12), QPointF(x + 29, y + 22))
            p.drawLine(QPointF(x + 16, y + 23), QPointF(x + 42, y + 23))

    for f in range(total):
        t = f / FPS
        img = QImage(W, H, QImage.Format.Format_RGB32)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QLinearGradient(0, 0, W, H)
        g.setColorAt(0, QColor("#3E8BFF")); g.setColorAt(1, QColor("#5B4DFF"))
        p.fillRect(0, 0, W, H, g)
        shot = int(t // SHOT)
        local = t - shot * SHOT
        done = shot >= len(DEVICES)

        # the devices, the active one bright, its name under it
        f_name = QFont("Segoe UI", 11); f_name.setWeight(QFont.Weight.DemiBold)
        for i, (name, kind, _) in enumerate(DEVICES):
            x, y = rows[i]
            on = i == shot
            device(p, kind, x, y, on)
            p.setFont(f_name); p.setPen(QColor(255, 255, 255, 255 if on else 140))
            p.drawText(QRectF(x + 80, y - 12, 120, 24), Qt.AlignmentFlag.AlignVCenter, name)

        # the TV
        tx, ty, tw, th = TV
        landed = not done and local > 0.85
        flash = max(0.0, 1 - (local - 0.85) / 0.6) if landed else 0.0
        p.setPen(QPen(white, 7)); p.setBrush(QColor(255, 255, 255, int(36 + 120 * flash)))
        p.drawRoundedRect(QRectF(tx, ty, tw, th), 22, 22)
        p.setPen(QPen(white, 5))
        p.drawLine(QPointF(tx + tw / 2 - 40, ty + th + 26), QPointF(tx + tw / 2 + 40, ty + th + 26))
        p.drawLine(QPointF(tx + tw / 2, ty + th + 4), QPointF(tx + tw / 2, ty + th + 24))

        # what arrived: the command, on the TV until the next one lands
        last = shot - (0 if landed else 1)
        if 0 <= last < len(DEVICES) or done:
            cmd = DEVICES[min(last, len(DEVICES) - 1)][2] if not done else ""
            if cmd:
                fc = QFont("Segoe UI", 20); fc.setWeight(QFont.Weight.Bold)
                p.setFont(fc); p.setPen(white)
                p.drawText(QRectF(tx, ty + th - 62, tw, 40), Qt.AlignmentFlag.AlignCenter, cmd)

        # the ball: up from the device, over and down into the TV, the bubbles trailing it
        if not done and local < 0.85:
            k = ease(local / 0.85)
            x0, y0 = rows[shot][0] + 70, rows[shot][1] - 20
            x1, y1 = tx + tw * 0.62, ty + th * 0.42
            path = QPainterPath(QPointF(x0, y0))
            top = 34                                              # the arc's highest point stays on screen
            path.cubicTo(QPointF(x0 + 150, max(top, y0 - 120)), QPointF(x1 - 170, top), QPointF(x1, y1))
            for j in range(1, 4):                                  # the trail
                kj = k - j * 0.09
                if kj > 0:
                    pt = path.pointAtPercent(kj)
                    r = 12 - j * 2.5
                    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(255, 255, 255, 230 - j * 55))
                    p.drawEllipse(pt, r, r)
            pt = path.pointAtPercent(k)
            r = 12 + 6 * k
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(white)
            p.drawEllipse(pt, r, r)
        elif landed:                                               # resting in the screen, like the logo's dot
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(white)
            r = 18 * (1 + 0.25 * flash)
            p.drawEllipse(QPointF(tx + tw * 0.62, ty + th * 0.42 - 18), r, r)

        if done:                                                   # the end card
            k = ease((t - len(DEVICES) * SHOT) / 0.6)
            p.fillRect(0, 0, W, H, QColor(62, 100, 255, int(235 * k)))
            ft = QFont("Segoe UI", 40); ft.setWeight(QFont.Weight.Bold)
            p.setFont(ft); p.setPen(QColor(255, 255, 255, int(255 * k)))
            p.drawText(QRectF(0, H / 2 - 60, W, 70), Qt.AlignmentFlag.AlignCenter, "Fling")
            fs = QFont("Segoe UI", 14)
            p.setFont(fs)
            p.drawText(QRectF(0, H / 2 + 10, W, 40), Qt.AlignmentFlag.AlignCenter,
                       "Your TV, from the phone and every computer")
        p.end()
        img.save(os.path.join(out_dir, f"{f:04d}.png"))
    print(total, "frames")


def gif(out_dir):
    from PIL import Image
    names = sorted(n for n in os.listdir(out_dir) if n.endswith(".png"))
    ims = [Image.open(os.path.join(out_dir, n)).convert("RGB").quantize(colors=96, method=Image.Quantize.MEDIANCUT)
           for n in names]
    ims[0].save(OUT, save_all=True, append_images=ims[1:], duration=int(1000 / FPS), loop=0, optimize=True)
    print(OUT, os.path.getsize(OUT) // 1024, "KB")


if __name__ == "__main__":
    if sys.argv[1] == "--gif":
        gif(sys.argv[2])
    else:
        frames(sys.argv[1])
