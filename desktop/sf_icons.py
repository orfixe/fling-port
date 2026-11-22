# icons.py
from PyQt6.QtGui import QPainter, QColor, QPainterPath, QPen, QFont, QImage
from PyQt6.QtCore import QRectF, QPointF, Qt
import sys
import math

NAMES = [
    "chevron.up", "chevron.down", "chevron.left", "chevron.right",
    "power", "house", "arrow.uturn.backward", "speaker",
    "speaker.minus", "speaker.plus", "speaker.slash", "speaker.wave.2",
    "mic", "mic.fill", "play.fill", "pause.fill",
    "goforward.30", "gobackward.30", "arrow.clockwise",
    "tv", "play.rectangle", "doc.on.clipboard",
    "dot.radiowaves", "bluetooth", "magnifyingglass"
]

def _chevron_path(direction: str) -> QPainterPath:
    p = QPainterPath()
    w, h = 8.0, 4.5
    if direction == "up":
        p.moveTo(12 - w/2, 12 + h/2)
        p.lineTo(12, 12 - h/2)
        p.lineTo(12 + w/2, 12 + h/2)
    elif direction == "down":
        p.moveTo(12 - w/2, 12 - h/2)
        p.lineTo(12, 12 + h/2)
        p.lineTo(12 + w/2, 12 - h/2)
    elif direction == "left":
        p.moveTo(12 + h/2, 12 - w/2)
        p.lineTo(12 - h/2, 12)
        p.lineTo(12 + h/2, 12 + w/2)
    elif direction == "right":
        p.moveTo(12 - h/2, 12 - w/2)
        p.lineTo(12 + h/2, 12)
        p.lineTo(12 - h/2, 12 + w/2)
    return p

def _power_path() -> QPainterPath:
    p = QPainterPath()
    # circle arc (open at top)
    p.moveTo(12,5)  # start bottom
    for angle in range(0, 360, 30):
        rad = angle * math.pi / 180
        x = 12 + 7 * math.cos(rad)
        y = 12 + 7 * math.sin(rad)
        p.lineTo(x, y)
    return p

def _house_path() -> QPainterPath:
    p = QPainterPath()
    # roof
    p.moveTo(6,12)
    p.lineTo(12,5)
    p.lineTo(18,12)
    # body
    p.addRect(8,12,8,7)
    return p

def _arrow_uturn_backward_path() -> QPainterPath:
    p = QPainterPath()
    p.moveTo(16,6)
    p.cubicTo(10,6, 10,12, 14,12)
    p.lineTo(14,18)
    p.cubicTo(14,22, 8,22, 8,18)
    return p

def _speaker_path() -> QPainterPath:
    p = QPainterPath()
    # speaker body
    p.addRect(6,10,4,4)
    # cone
    p.moveTo(10,10)
    p.lineTo(14,8)
    p.lineTo(16,12)
    p.lineTo(14,16)
    p.lineTo(10,14)
    return p

def _mic_path(fill: bool = False) -> QPainterPath:
    p = QPainterPath()
    # capsule
    p.addRoundedRect(QRectF(9,6,6,12), 3, 3)
    if fill:
        return p
    # stand arc
    p.moveTo(12,18)
    for angle in range(-90, 91, 30):
        rad = angle * math.pi / 180
        x = 12 + 4 * math.cos(rad)
        y = 18 + 4 * math.sin(rad)
        p.lineTo(x, y)
    return p

def _play_path(fill: bool = False) -> QPainterPath:
    p = QPainterPath()
    if fill:
        p.moveTo(10,8)
        p.lineTo(16,12)
        p.lineTo(10,16)
        p.closeSubpath()
    else:
        p.moveTo(10,8)
        p.lineTo(16,12)
        p.lineTo(10,16)
    return p

def _pause_path(fill: bool = False) -> QPainterPath:
    p = QPainterPath()
    if fill:
        p.addRoundedRect(QRectF(9,8,2,8), 1, 1)
        p.addRoundedRect(QRectF(13,8,2,8), 1, 1)
    else:
        p.moveTo(9,8)
        p.lineTo(11,8)
        p.lineTo(11,16)
        p.lineTo(9,16)
        p.closeSubpath()
        p.moveTo(13,8)
        p.lineTo(15,8)
        p.lineTo(15,16)
        p.lineTo(13,16)
        p.closeSubpath()
    return p

def _goforward_30_path() -> QPainterPath:
    p = QPainterPath()
    # circular arrow
    p.moveTo(12,5)
    for angle in range(0, 360, 15):
        rad = angle * math.pi / 180
        x = 12 + 7 * math.cos(rad)
        y = 12 + 7 * math.sin(rad)
        p.lineTo(x, y)
    return p

def _arrow_clockwise_path() -> QPainterPath:
    p = QPainterPath()
    # full circle (used as placeholder)
    for angle in range(0, 360, 15):
        rad = angle * math.pi / 180
        x = 12 + 7 * math.cos(rad)
        y = 12 + 7 * math.sin(rad)
        p.lineTo(x, y)
    return p

def _tv_path() -> QPainterPath:
    p = QPainterPath()
    # screen
    p.addRoundedRect(QRectF(3,4,18,12), 2, 2)
    # stand
    p.moveTo(12,16)
    p.lineTo(12,20)
    return p

def _play_rectangle_path() -> QPainterPath:
    p = QPainterPath()
    p.addRoundedRect(QRectF(3,4,18,13), 2, 2)
    # triangle
    p.moveTo(10,8)
    p.lineTo(16,12)
    p.lineTo(10,16)
    return p

def _doc_on_clipboard_path() -> QPainterPath:
    p = QPainterPath()
    p.addRoundedRect(QRectF(4,6,14,12), 2, 2)
    # clip tab
    p.moveTo(8,4)
    p.lineTo(16,4)
    p.lineTo(16,6)
    return p

def _dot_radiowaves_path() -> QPainterPath:
    p = QPainterPath()
    # dot
    p.addEllipse(QRectF(11,11,2,2))
    # left arcs
    for r in [4, 7]:
        p.moveTo(12 - r, 12)
        p.arcTo(QRectF(12-r-1, 12-r-1, 2*r+2, 2*r+2), 0, 180)
    # right arcs
    for r in [4, 7]:
        p.moveTo(12 + r, 12)
        p.arcTo(QRectF(12+r-1, 12-r-1, 2*r+2, 2*r+2), 180, 180)
    return p

def _bluetooth_path() -> QPainterPath:
    p = QPainterPath()
    # upper triangle
    p.moveTo(10,8)
    p.lineTo(14,12)
    p.lineTo(10,16)
    p.closeSubpath()
    # lower triangle
    p.moveTo(10,16)
    p.lineTo(14,20)
    p.lineTo(10,24)
    p.closeSubpath()
    return p

def _magnifyingglass_path() -> QPainterPath:
    p = QPainterPath()
    # circle
    p.addEllipse(QRectF(6,6,8,8))
    # handle
    p.moveTo(12,14)
    p.lineTo(18,20)
    return p

def _get_icon(name: str):
    if name == "chevron.up":
        return _chevron_path("up"), False
    if name == "chevron.down":
        return _chevron_path("down"), False
    if name == "chevron.left":
        return _chevron_path("left"), False
    if name == "chevron.right":
        return _chevron_path("right"), False
    if name == "power":
        return _power_path(), False
    if name == "house":
        return _house_path(), False
    if name == "arrow.uturn.backward":
        return _arrow_uturn_backward_path(), False
    if name == "speaker":
        return _speaker_path(), False
    if name == "speaker.minus":
        p = _speaker_path()
        # minus sign
        p.moveTo(12,10)
        p.lineTo(14,10)
        return p, False
    if name == "speaker.plus":
        p = _speaker_path()
        # plus sign
        p.moveTo(13,9)
        p.lineTo(13,11)
        p.moveTo(12,10)
        p.lineTo(14,10)
        return p, False
    if name == "speaker.slash":
        p = _speaker_path()
        # slash
        p.moveTo(10,10)
        p.lineTo(16,16)
        return p, False
    if name == "speaker.wave.2":
        p = _speaker_path()
        # two arcs to the right
        for r in [4, 7]:
            p.moveTo(12 + r, 12)
            p.arcTo(QRectF(12+r-1, 12-r-1, 2*r+2, 2*r+2), 180, 180)
        return p, False
    if name == "mic":
        return _mic_path(False), False
    if name == "mic.fill":
        return _mic_path(True), True
    if name == "play.fill":
        return _play_path(True), True
    if name == "pause.fill":
        return _pause_path(True), True
    if name == "goforward.30":
        return _goforward_30_path(), False
    if name == "gobackward.30":
        return _goforward_30_path(), False  # same shape, will rotate later
    if name == "arrow.clockwise":
        return _arrow_clockwise_path(), False
    if name == "tv":
        return _tv_path(), False
    if name == "play.rectangle":
        return _play_rectangle_path(), False
    if name == "doc.on.clipboard":
        return _doc_on_clipboard_path(), False
    if name == "dot.radiowaves":
        return _dot_radiowaves_path(), False
    if name == "bluetooth":
        return _bluetooth_path(), False
    if name == "magnifyingglass":
        return _magnifyingglass_path(), False
    return None, False

def draw_icon(p: QPainter, name: str, rect: QRectF, color: QColor, weight: float = 1.0) -> None:
    """Draw an SF‑Symbols style icon inside `rect`."""
    p.save()
    p.translate(rect.topLeft())
    scale = rect.width() / 24
    p.scale(scale, scale)
    # antialiasing
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    path, is_filled = _get_icon(name)
    if path is None:
        p.restore()
        return

    pen = QPen(color)
    pen.setWidthF(1.8 * weight)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)

    if is_filled:
        p.setBrush(color)
        p.drawPath(path)
    else:
        p.drawPath(path)

    # special cases with text
    if name in ("goforward.30", "gobackward.30"):
        font = QFont()
        font.setPixelSize(7)
        font.setBold(True)
        p.setPen(color)
        p.setFont(font)
        p.drawText(QRectF(0, 0, 24, 24), Qt.AlignmentFlag.AlignCenter, "30")

    p.restore()

if __name__ == "__main__":
    from PyQt6.QtGui import QGuiApplication
    app = QGuiApplication(sys.argv)

    cols = 6
    cell_size = 64
    rows = (len(NAMES) + cols - 1) // cols
    img = QImage(cols * cell_size, rows * cell_size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.white)

    painter = QPainter(img)
    for idx, name in enumerate(NAMES):
        col = idx % cols
        row = idx // cols
        x = col * cell_size
        y = row * cell_size
        # icon rect 40x40 centered with padding 12
        icon_rect = QRectF(x + 12, y + 12, 40, 40)
        draw_icon(painter, name, icon_rect, QColor(0, 0, 0), weight=1.0)

        # text below
        font = QFont()
        font.setPixelSize(9)
        painter.setPen(QColor(0, 0, 0))
        painter.setFont(font)
        txt_rect = QRectF(x, y + 52, cell_size, 12)
        painter.drawText(txt_rect, Qt.AlignmentFlag.AlignCenter, name)

    painter.end()
    out_path = sys.argv[1] if len(sys.argv) > 1 else "icons_preview.png"
    img.save(out_path)
