# -*- coding: utf-8 -*-
"""自绘分析控件：三通道直方图、Y 波形图、色调曲线编辑器。"""

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

CH_COLORS = {
    "Y": QColor(70, 120, 230),
    "Cr": QColor(220, 70, 70),
    "Cb": QColor(60, 170, 200),
}


def _draw_grid(p: QPainter, rect: QRectF, steps=4):
    p.setPen(QPen(QColor(226, 229, 234), 1))
    for i in range(1, steps):
        x = rect.left() + rect.width() * i / steps
        y = rect.top() + rect.height() * i / steps
        p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))


class HistogramWidget(QWidget):
    """Y/Cr/Cb 三通道直方图。鼠标悬停显示该级像素占比。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.hists = {}          # 通道名 → 256 级计数
        self.limit_height = True  # 裁剪 1% 极值柱以突出主体
        self.hover_x = None
        self.setMouseTracking(True)
        self.setToolTip("直方图：亮蓝=Y，红=Cr，青=Cb；悬停查看各级占比")

    def set_histograms(self, hists):
        self.hists = hists
        self.update()

    def mouseMoveEvent(self, event):
        self.hover_x = event.position().x()
        self.update()
        if not self.hists:
            return
        total = sum(int(h.sum()) for h in self.hists.values()) or 1
        parts = []
        for name in ("Y", "Cr", "Cb"):
            h = self.hists.get(name)
            if h is None:
                continue
            v = self._level_at(h, event.position().x())
            parts.append(f"{name} {v}: {h[v] / total * 100:.2f}%")
        self.setToolTip("  ".join(parts) if parts else "")

    def leaveEvent(self, event):
        self.hover_x = None
        self.update()

    def _plot_rect(self):
        return QRectF(6, 6, max(1, self.width() - 12), max(1, self.height() - 12))

    def _level_at(self, h, x):
        r = self._plot_rect()
        return int(np.clip((x - r.left()) / r.width() * 255, 0, 255))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(255, 255, 255))
        r = self._plot_rect()
        _draw_grid(p, r)
        if not self.hists:
            p.setPen(QColor(160, 164, 172))
            p.drawText(self.rect(), Qt.AlignCenter, "打开图片后显示直方图")
            return

        for name in ("Cr", "Cb", "Y"):
            h = self.hists.get(name)
            if h is None:
                continue
            draw = h.astype(np.float32)
            if self.limit_height:
                nz = draw[draw > 0]
                if nz.size:
                    top = np.percentile(nz, 99.5)
                    draw = np.clip(draw, 0, max(top, 1))
            peak = float(draw.max()) or 1.0
            color = QColor(CH_COLORS[name])
            color.setAlpha(205 if name == "Y" else 80)
            path = QPainterPath()
            path.moveTo(r.left(), r.bottom())
            for i in range(256):
                x = r.left() + r.width() * i / 255.0
                y = r.bottom() - r.height() * draw[i] / peak
                path.lineTo(x, y)
            path.lineTo(r.right(), r.bottom())
            path.closeSubpath()
            p.fillPath(path, color)
            if name == "Y":
                p.setPen(QPen(QColor(50, 95, 210), 1.2))
                p.drawPath(path)

        # 0 / 255 堆积提示（clipping）
        for name in ("Y", "Cr", "Cb"):
            h = self.hists.get(name)
            if h is None:
                continue
            total = float(h.sum()) or 1
            for lv, x in ((0, r.left()), (255, r.right())):
                frac = h[lv] / total
                if frac > 0.01:
                    p.setPen(QPen(QColor(200, 40, 40), 2))
                    p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
                    p.setFont(QFont(self.font().family(), 8))
                    p.drawText(QPointF(x + (2 if lv == 0 else -34), r.top() + 12),
                               f"{frac * 100:.1f}%")

        if self.hover_x is not None and r.contains(QPointF(self.hover_x, r.center().y())):
            p.setPen(QPen(QColor(90, 90, 96), 1, Qt.DashLine))
            p.drawLine(QPointF(self.hover_x, r.top()), QPointF(self.hover_x, r.bottom()))
            lv = self._level_at(self.hists.get("Y"), self.hover_x)
            p.setPen(QColor(60, 64, 70))
            p.setFont(QFont(self.font().family(), 8))
            p.drawText(QPointF(min(self.hover_x + 4, r.right() - 26), r.bottom() - 6), str(lv))


class WaveformWidget(QWidget):
    """Y 通道波形：横轴为图像列，高度表示 Y 值密度。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.wave = None   # 128 列 × 256 级密度 (float)
        self.setToolTip("Y 波形：横轴=图像列，纵轴=亮度；底部为黑，顶部为白")

    def set_waveform(self, wave):
        self.wave = wave
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        r = QRectF(6, 6, max(1, self.width() - 12), max(1, self.height() - 12))
        p.fillRect(self.rect(), QColor(255, 255, 255))
        _draw_grid(p, r, 2)
        p.setPen(QColor(150, 154, 162))
        p.setFont(QFont(self.font().family(), 8))
        p.drawText(QPointF(r.left() + 2, r.top() + 10), "白")
        p.drawText(QPointF(r.left() + 2, r.bottom() - 2), "黑")
        if self.wave is None:
            p.drawText(self.rect(), Qt.AlignCenter, "Y 波形")
            return
        w, h = self.wave.shape
        if np.isinf(self.wave).any():
            return
        norm = np.log1p(self.wave) / np.log1p(self.wave.max() or 1.0)
        image = (np.clip(norm, 0, 1) * 255).T[::-1].astype(np.uint8)   # 256 × 128
        from PySide6.QtGui import QImage
        qi = QImage(image.tobytes(), w, 256, w, QImage.Format_Grayscale8).copy()
        p.drawImage(r, qi)


CURVE_PRESETS = {
    "线性": [(0, 0), (255, 255)],
    "柔 S": [(0, 0), (64, 50), (192, 205), (255, 255)],
    "中 S": [(0, 0), (48, 30), (128, 128), (208, 226), (255, 255)],
    "强 S": [(0, 0), (40, 16), (128, 128), (216, 240), (255, 255)],
    "提亮": [(0, 10), (128, 140), (255, 255)],
    "压暗": [(0, 0), (128, 116), (255, 245)],
}


class CurveWidget(QWidget):
    """色调曲线编辑器：拖拽控制点，双击空白处加点，右键删点。"""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 170)
        self.points = [[0.0, 0.0], [255.0, 255.0]]
        self.drag_idx = None
        self.draw_diag = True     # 叠加显示恒等对角线
        self.setMouseTracking(True)
        self.setToolTip("拖拽控制点调整曲线；双击空白加点；右键删点\n起点/终点的 X 固定，只能上下移动")

    def _rect(self):
        m = 12
        return QRectF(m, m, max(1, self.width() - 2 * m), max(1, self.height() - 2 * m))

    def _to_widget(self, x, y):
        r = self._rect()
        return QPointF(r.left() + x / 255.0 * r.width(), r.bottom() - y / 255.0 * r.height())

    def _to_curve(self, pos):
        r = self._rect()
        x = np.clip((pos.x() - r.left()) / r.width() * 255.0, 0, 255)
        y = np.clip((r.bottom() - pos.y()) / r.height() * 255.0, 0, 255)
        return [float(x), float(y)]

    def _find_point(self, pos, radius=10):
        for i, (x, y) in enumerate(self.points):
            if (self._to_widget(x, y) - pos).manhattanLength() < radius:
                return i
        return None

    def set_points(self, pts):
        self.points = sorted([[float(x), float(y)] for x, y in pts])
        if len(self.points) < 2:
            self.points = [[0.0, 0.0], [255.0, 255.0]]
        self.update()
        self.changed.emit()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._rect()
        p.fillRect(self.rect(), QColor(255, 255, 255))
        p.setPen(QPen(QColor(222, 225, 230), 1))
        p.drawRect(r)
        _draw_grid(p, r)
        if self.draw_diag:
            p.setPen(QPen(QColor(208, 212, 220), 1, Qt.DotLine))
            p.drawLine(r.topLeft(), r.bottomRight())

        # 曲线（分段线性 + 轻微平滑显示）
        xs = np.array([pt[0] for pt in self.points])
        ys = np.array([pt[1] for pt in self.points])
        samples = np.linspace(0, 255, 128)
        lut = np.interp(samples, xs, ys)
        path = QPainterPath()
        first = self._to_widget(samples[0], lut[0])
        path.moveTo(first)
        for sx, sy in zip(samples[1:], lut[1:]):
            path.lineTo(self._to_widget(sx, sy))
        p.setPen(QPen(QColor(74, 128, 232), 2))
        p.drawPath(path)

        # 直方图背景（可选）
        # 控制点
        for i, (x, y) in enumerate(self.points):
            c = self._to_widget(x, y)
            fill = QColor(74, 128, 232) if i == self.drag_idx else QColor(255, 255, 255)
            p.setBrush(fill)
            p.setPen(QPen(QColor(74, 128, 232), 2))
            p.drawEllipse(c, 5, 5)
        p.setPen(QColor(140, 144, 152))
        p.setFont(QFont(self.font().family(), 8))
        p.drawText(QPointF(r.left() + 2, r.bottom() - 2), "暗")
        p.drawText(QPointF(r.right() - 16, r.bottom() - 2), "亮")

    def mousePressEvent(self, event):
        pos = event.position()
        idx = self._find_point(pos)
        if event.button() == Qt.RightButton:
            if idx is not None and 0 < idx < len(self.points) - 1:
                self.points.pop(idx)
                self.update()
                self.changed.emit()
            return
        if event.button() == Qt.LeftButton:
            if idx is None:
                pt = self._to_curve(pos)
                self.points.append(pt)
                self.points.sort()
                idx = self.points.index(pt)
            self.drag_idx = idx
            self.update()

    def mouseMoveEvent(self, event):
        if self.drag_idx is None:
            return
        pos = event.position()
        pt = self._to_curve(pos)
        i = self.drag_idx
        if i == 0:
            pt[0] = 0.0
        elif i == len(self.points) - 1:
            pt[0] = 255.0
        else:
            lo = self.points[i - 1][0] + 1
            hi = self.points[i + 1][0] - 1
            pt[0] = min(max(pt[0], lo), hi) if lo <= hi else pt[0]
        self.points[i] = pt
        self.update()

    def mouseReleaseEvent(self, event):
        if self.drag_idx is not None:
            self.drag_idx = None
            self.points.sort()
            self.update()
            self.changed.emit()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and len(self.points) < 12:
            pt = self._to_curve(event.position())
            pt[0] = min(max(pt[0], 1), 254)
            self.points.append(pt)
            self.points.sort()
            self.update()
            self.changed.emit()
