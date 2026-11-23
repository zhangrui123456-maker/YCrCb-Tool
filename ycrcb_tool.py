# -*- coding: utf-8 -*-
"""
YCrCb 图像转换工具
打开图片 → 转换为 YCrCb → 参数调整（Y 增强 / 曲线 / 色度）→ 质量模拟 → 导出

运行：python ycrcb_tool.py [图片路径]
依赖：PySide6、Pillow、numpy
"""

import json
import os
import sys

import numpy as np
from PIL import Image, ImageOps

import ycrcb_core as core
from PySide6 import QtWidgets
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QFont, QIcon, QImage, QKeySequence, QPainter,
                           QPen, QPixmap, QTransform)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDockWidget, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGraphicsPixmapItem, QGraphicsScene,
                               QGraphicsView, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QPushButton, QScrollArea, QSlider,
                               QSplitter, QTabWidget, QVBoxLayout, QWidget)
from widgets import CURVE_PRESETS, CurveWidget, HistogramWidget, WaveformWidget

APP_TITLE = "YCrCb 图像转换工具"
APP_VERSION = "2.2"

OPEN_FILTER = "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff *.gif);;所有文件 (*.*)"
SAVE_FILTER = ("PNG 图像 (*.png);;JPEG 图像 (*.jpg *.jpeg);;BMP 图像 (*.bmp);;"
               "WebP 图像 (*.webp);;TIFF 图像 (*.tiff *.tif)")
EXPORT_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")

PREVIEW_MAX_SIZE = 1600
WAVE_COLS = 160


# ============================================================ 通用组件

class SimpleCheck(QtWidgets.QCheckBox):
    """自绘复选框，规避样式表下系统指示器消失的问题。"""

    _icons = {}

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        if not SimpleCheck._icons:
            SimpleCheck._icons = {False: self._make_icon(False), True: self._make_icon(True)}
        self.setStyleSheet("QCheckBox { padding-left: 24px; }"
                           "QCheckBox::indicator { width: 0px; height: 0px; }")
        self.setMinimumHeight(24)
        self.clicked.connect(self.update)

    @staticmethod
    def _make_icon(checked):
        pm = QPixmap(18, 18)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        if checked:
            p.setBrush(QColor(74, 128, 232))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(1, 1, 16, 16, 3, 3)
            p.setPen(QPen(Qt.white, 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPolyline([QPointF(4.5, 9.5), QPointF(8, 13), QPointF(14, 5.5)])
        else:
            p.setBrush(QColor(255, 255, 255))
            p.setPen(QPen(QColor(150, 155, 163), 1.4))
            p.drawRoundedRect(1.5, 1.5, 15, 15, 3, 3)
        p.end()
        return pm

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.drawPixmap(0, (self.height() - 18) // 2, self._icons[self.isChecked()])
        p.end()

    def hitButton(self, pos):
        # 勾选框绘制在内边距里（padding-left），默认命中区域不包含它；
        # 让整个控件都可点击，点勾选框图标也能切换
        return self.rect().contains(pos)


class SliderRow(QWidget):
    """滑块 + 可编辑数值框。scale 为值→滑块刻度的倍率。"""

    def __init__(self, name, minimum, maximum, default, scale, on_change,
                 decimals=0, suffix=None, width=80, label_width=92):
        super().__init__()
        self.scale = scale
        self.on_change = on_change
        label = QLabel(name)
        label.setMinimumWidth(label_width)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(round(minimum * scale), round(maximum * scale))
        self.slider.setPageStep(max(1, self.slider.maximum() // 10))
        self.slider.setValue(round(default * scale))

        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(decimals)
        self.spin.setRange(minimum, maximum)
        self.spin.setValue(default)
        self.spin.setKeyboardTracking(False)
        self.spin.setAlignment(Qt.AlignRight)
        self.spin.setFixedWidth(width)
        self.spin.valueChanged.connect(self._from_spin)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spin.installEventFilter(self)
        self.unit = QLabel(suffix) if suffix else None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(label)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        if self.unit is not None:
            self.unit.setFixedWidth(16)
            self.unit.setStyleSheet("color:#555; font-size:11px;")
            lay.addWidget(self.unit)
        self.setFixedHeight(32)

    def eventFilter(self, obj, event):
        if obj is self.spin and event.type() == QEvent.Wheel:
            delta = event.angleDelta().y()
            if delta:
                self.spin.setValue(self.spin.value() + (1 / self.scale if delta > 0 else -1 / self.scale))
            return True
        return super().eventFilter(obj, event)

    def _from_slider(self, v):
        self.spin.blockSignals(True)
        self.spin.setValue(v / self.scale)
        self.spin.blockSignals(False)
        self.on_change()

    def _from_spin(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(v * self.scale)))
        self.slider.blockSignals(False)
        self.on_change()

    def value(self):
        return self.slider.value() / self.scale

    def set_value(self, v):
        self.slider.setValue(int(round(v * self.scale)))


class ImageView(QGraphicsView):
    """可缩放/平移、像素拾取的图像视图。"""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.title = ""
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.SmoothTransformation)
        self.scene_.addItem(self.item)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setRenderHint(QPainter.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setBackgroundBrush(QColor(46, 48, 52))
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)
        self._fit_pending = False
        self._img_size = (1, 1)

    def set_pixmap(self, pixmap, refit=False):
        first = self.item.pixmap().isNull()
        self.item.setPixmap(pixmap)
        self.scene_.setSceneRect(pixmap.rect().toRectF())
        self._img_size = (pixmap.width(), pixmap.height())
        if first or refit:
            self.fit_to_window()

    def fit_to_window(self):
        pm = self.item.pixmap()
        if pm.isNull():
            return
        vp = self.viewport().size()
        if vp.width() < 10 or vp.height() < 10:
            self._fit_pending = True
            return
        self._fit_pending = False
        s = min(vp.width() / pm.width(), vp.height() / pm.height()) * 0.98
        self.setTransform(QTransform().scale(s, s))
        self.centerOn(self.item)

    def zoom(self, factor):
        if self.item.pixmap().isNull():
            return
        s = min(max(self.transform().m11() * factor, 0.02), 32.0)
        self.setTransform(QTransform().scale(s, s))

    def zoom_100(self):
        if self.item.pixmap().isNull():
            return
        self.setTransform(QTransform())
        self.centerOn(self.item)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_pending:
            self.fit_to_window()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.zoom(1.15 ** (event.angleDelta().y() / 120))
            event.accept()
        else:
            super().wheelEvent(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self.owner.hover_pixel(self.mapToScene(event.position().toPoint()), self)

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if not self.title:
            return
        painter.save()
        painter.resetTransform()
        font = QFont(self.font().family(), 9)
        font.setBold(True)
        painter.setFont(font)
        w = painter.fontMetrics().horizontalAdvance(self.title) + 16
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawRoundedRect(QRectF(8, 8, w, 22), 4, 4)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(QRectF(8, 8, w, 22), Qt.AlignCenter, self.title)
        painter.restore()


class SplitView(QWidget):
    """并排对比：左为处理结果，右为原始图像；缩放/平移在两个视图间同步。"""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.left = ImageView(owner)
        self.right = ImageView(owner)
        self.left.title = "处理结果"
        self.right.title = "原图"
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.left)
        self.splitter.addWidget(self.right)
        self.splitter.setChildrenCollapsible(False)
        self._syncing = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.splitter)

        def link(src, dst):
            def sync():
                if self._syncing:
                    return
                self._syncing = True
                try:
                    dst.setTransform(src.transform())
                    dst.centerOn(src.mapToScene(src.viewport().rect().center()))
                    dst.update()
                finally:
                    self._syncing = False
            src.horizontalScrollBar().valueChanged.connect(lambda _: sync())
            src.verticalScrollBar().valueChanged.connect(lambda _: sync())
        link(self.left, self.right)
        link(self.right, self.left)

    def fit_to_window(self):
        self.left.fit_to_window()
        self.right.fit_to_window()

    def zoom(self, factor):
        self.left.zoom(factor)
        self.right.zoom(factor)

    def zoom_100(self):
        self.left.zoom_100()
        self.right.zoom_100()

    def set_pixmaps(self, left_pm, right_pm, refit=False):
        self.left.set_pixmap(left_pm, refit=refit)
        self.right.set_pixmap(right_pm, refit=refit)
        if refit:
            self.left.fit_to_window()
            self.right.fit_to_window()


# ============================================================ 对话框

class YuvExportDialog(QDialog):
    """YUV 原始数据导出。"""

    def __init__(self, parent, width, height, has_alpha):
        super().__init__(parent)
        self.setWindowTitle("导出 YUV 原始数据")
        self.setMinimumWidth(480)
        self._size = (width, height)
        form = QFormLayout()

        self.fmt = QComboBox()
        for key, (name, _) in core.FORMATS.items():
            self.fmt.addItem(name, key)
        self.fmt.setCurrentIndex(list(core.FORMATS).index("i420"))
        self.fmt.currentIndexChanged.connect(self._update_hint)
        form.addRow("格式", self.fmt)

        self.depth = QComboBox()
        self.depth.addItem("8-bit", 8)
        self.depth.addItem("10-bit（uint16 存储，低位右移填 0）", 10)
        self.depth.currentIndexChanged.connect(self._update_hint)
        form.addRow("位深", self.depth)

        self.range_box = QComboBox()
        self.range_box.addItem("全范围（full）", "full")
        self.range_box.addItem("有限范围（limited / studio）", "limited")
        self.range_box.currentIndexChanged.connect(self._update_hint)
        form.addRow("范围", self.range_box)

        self.apply_edit = SimpleCheck("应用当前所有编辑（Y 增强 / 曲线 / 调参 / 色度）")
        self.apply_edit.setChecked(True)
        self.apply_edit.toggled.connect(self._update_sim_visibility)
        form.addRow("", self.apply_edit)

        self.apply_sim = SimpleCheck("应用模拟项（色深抖动 / 子采样）")
        self.apply_sim.setChecked(False)
        form.addRow("", self.apply_sim)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#555;")
        form.addRow("", self.hint)
        self._update_sim_visibility()

        if has_alpha:
            note = QLabel("⚠ 图片含透明区域：YUV 无 alpha，透明部分将按黑色合成。")
            note.setStyleSheet("color:#b06000;")
            form.addRow("", note)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("导出…")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(btns)

    def _update_sim_visibility(self):
        self.apply_sim.setEnabled(self.apply_edit.isChecked())
        self._update_hint()

    def _update_hint(self):
        fmt = self.fmt.currentData()
        d = self.depth.currentData()
        w, h = self._size
        mode = core.FORMATS[fmt][1]
        if fmt in ("yuy2", "uyvy") and d == 10:
            self.hint.setText("⚠ YUY2/UYVY 打包格式仅支持 8-bit，导出将回退为 8-bit。")
            return
        n = core.yuv_size(w, h, fmt, d)
        extra = ""
        if mode == "420" and (w % 2 or h % 2):
            extra = f"（奇数边已裁剪到 {w - w % 2}×{h - h % 2}）"
        elif mode == "422" and w % 2:
            extra = f"（宽度已裁剪到 {w - w % 2}）"
        layout = {"444": "Y 平面 + 两个色度平面", "420": "Y 平面 + 两个或交错半尺寸色度平面",
                  "422": "Y 平面 + 两个或打包半宽度色度"}[mode]
        self.hint.setText(f"{layout}{extra}\n文件大小：{n:,} 字节")

    def options(self):
        return (self.fmt.currentData(), self.depth.currentData(),
                self.range_box.currentData() == "limited",
                self.apply_edit.isChecked(), self.apply_sim.isChecked())


class QualityDialog(QDialog):
    """JPEG/WebP 导出质量。"""

    def __init__(self, parent, fmt):
        super().__init__(parent)
        self.setWindowTitle(f"{'JPEG' if fmt == 'JPEG' else 'WebP'} 导出质量")
        self.setMinimumWidth(320)
        self.quality = 92
        lay = QVBoxLayout(self)
        self.row = SliderRow("质量", 1, 100, 92, 1, lambda: None)
        lay.addWidget(self.row)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("导出")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def accept(self):
        self.quality = int(self.row.value())
        super().accept()


# ============================================================ 主窗口

MODE_RECON, MODE_Y, MODE_CR, MODE_CB, MODE_PSEUDO, MODE_ORIG = range(6)
MODE_NAMES = ["重建彩色", "Y 通道", "Cr 通道", "Cb 通道", "通道伪彩色", "原图"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setAcceptDrops(True)
        self.resize(1400, 900)
        self.setMinimumSize(1000, 700)

        # ---- 图像状态
        self.path = None
        self.source_im = None
        self.orig_rgb = None
        self.orig_alpha = None
        self.preview_rgb = None
        self.preview_source = None
        self.base = None           # 预览缓存 (Y, Cr, Cb)
        self.mode = MODE_RECON
        self.show_original = False
        self.peek_original = False
        self.last_proc = None      # 最近一次预览处理结果，供像素拾取

        self._build_ui()
        self._build_menu()
        QApplication.instance().installEventFilter(self)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(120)
        self.refresh_timer.timeout.connect(self.refresh_preview)

        self._update_actions_enabled()

    # ------------------------------------------------------------ UI 构建
    def _build_ui(self):
        self.view = ImageView(self)
        self.view_stack = QtWidgets.QStackedWidget(self)
        self.view_stack.addWidget(self.view)
        self.split_view = SplitView(self)
        self.split_view.hide()
        self.view_stack.addWidget(self.split_view)
        self.setCentralWidget(self.view_stack)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_basic(), "基础")
        self.tabs.addTab(self._tab_y(), "Y 增强")
        self.tabs.addTab(self._tab_color(), "色彩")
        self.tabs.addTab(self._tab_sim(), "模拟")
        self.tabs.setDocumentMode(True)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.tabs)
        scroll.setMinimumWidth(360)
        scroll.setMaximumWidth(440)

        dock = QDockWidget("参数与导出", self)
        dock.setObjectName("dock_params")
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable
                         | QDockWidget.DockWidgetClosable)
        dock.setWidget(scroll)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.dock_params = dock

        # ---------- 基础页
        # 顶部：色彩标准 + 显示模式
        # 详见 _tab_basic

        # ---------- 底部分析面板
        self.hist = HistogramWidget()
        self.wave = WaveformWidget()
        self.curve = CurveWidget()
        self.curve.changed.connect(self.on_curve_changed)
        self.curve_preset = QComboBox()
        self.curve_preset.addItem("曲线预设…")
        for name in CURVE_PRESETS:
            self.curve_preset.addItem(name)
        self.curve_preset.currentIndexChanged.connect(self.on_curve_preset)
        self.btn_curve_reset = QPushButton("曲线复位")
        self.btn_curve_reset.clicked.connect(self.on_curve_reset)
        crow = QHBoxLayout()
        crow.addWidget(self.curve_preset, 1)
        crow.addWidget(self.btn_curve_reset)
        cbox = QVBoxLayout()
        cbox.addWidget(QLabel("Y 色调曲线（拖点编辑，双击加点，右键删点）"))
        cbox.addWidget(self.curve, 1)
        cbox.addLayout(crow)
        cw = QWidget()
        cw.setLayout(cbox)

        atabs = QTabWidget()
        atabs.addTab(self.hist, "直方图")
        atabs.addTab(self.wave, "Y 波形")
        atabs.addTab(cw, "曲线")
        atabs.setDocumentMode(True)

        adock = QDockWidget("分析", self)
        adock.setObjectName("dock_analysis")
        adock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable
                          | QDockWidget.DockWidgetClosable)
        adock.setWidget(atabs)
        self.addDockWidget(Qt.BottomDockWidgetArea, adock)
        self.dock_analysis = adock
        self.resizeDocks([adock], [230], Qt.Vertical)

        sb = self.statusBar()
        self.lbl_info = QLabel("未打开图片")
        self.lbl_pixel = QLabel("")
        self.lbl_pixel.setMinimumWidth(560)
        sb.addWidget(self.lbl_info, 1)
        sb.addPermanentWidget(self.lbl_pixel)

        self.setStyleSheet("""
            QMainWindow { background: #f2f3f5; }
            QWidget { color: #1f2329; }
            QMenuBar { background: #ffffff; color: #1f2329; }
            QMenuBar::item { background: transparent; padding: 4px 10px; }
            QMenuBar::item:selected { background: #e3ebfb; }
            QMenu { background: #ffffff; color: #1f2329; border: 1px solid #d5d7db; }
            QMenu::item { padding: 5px 28px 5px 22px; }
            QMenu::item:selected { background: #e3ebfb; }
            QMenu::item:disabled { color: #aaa; }
            QStatusBar { background: #f2f3f5; color: #1f2329; }
            QDockWidget { font-weight: bold; color: #1f2329; }
            QDockWidget::title { background: #eef0f3; padding: 5px 10px; text-align: left; }
            QDockWidget > QWidget { background: #f7f8fa; }
            QTabWidget::pane { border: 1px solid #d5d7db; border-radius: 4px;
                               background: #f7f8fa; top: -1px; }
            QTabWidget > QWidget { background: #f7f8fa; }
            QTabBar::tab { background: #e8eaee; padding: 6px 14px; margin-right: 2px;
                           border: 1px solid #d5d7db; border-bottom: none;
                           border-top-left-radius: 4px; border-top-right-radius: 4px; }
            QTabBar::tab:selected { background: #f7f8fa; font-weight: bold; }
            QGroupBox { border: 1px solid #d5d7db; border-radius: 6px;
                        margin-top: 10px; background: #ffffff; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px;
                               background: transparent; }
            QPushButton { padding: 6px 10px; border-radius: 4px; color: #1f2329;
                          border: 1px solid #c0c3c8; background: #ffffff; }
            QPushButton:hover { background: #eef3ff; border-color: #7aa3ff; }
            QPushButton:disabled { color: #aaa; background: #f4f4f4; }
            QComboBox { padding: 3px 6px; color: #1f2329; background: #ffffff;
                        border: 1px solid #c0c3c8; border-radius: 4px; }
            QComboBox QAbstractItemView { background: #ffffff; color: #1f2329;
                                          selection-background-color: #e3ebfb;
                                          selection-color: #1f2329; }
            QDoubleSpinBox { color: #1f2329; background: #ffffff;
                             border: 1px solid #c0c3c8; border-radius: 3px; padding: 1px 4px; }
            QCheckBox { color: #1f2329; }
            QToolTip { color: #1f2329; background: #fffbe6; border: 1px solid #d8cf9c; }
            QSlider::groove:horizontal { height: 4px; background: #d5d7db; border-radius: 2px; }
            QSlider::handle:horizontal { width: 14px; margin: -6px 0;
                border-radius: 7px; background: #4a80e8; }
            QSlider::sub-page:horizontal { background: #9dbcf5; border-radius: 2px; }
            QScrollArea { border: none; background: #f7f8fa; }
            QScrollArea > QWidget > QWidget { background: #f7f8fa; }
            QAbstractScrollArea::viewport { background: #f7f8fa; }
            QScrollArea QScrollBar:vertical { width: 10px; background: #eef0f3; }
            QScrollArea QScrollBar:horizontal { height: 10px; background: #eef0f3; }
        """)

    def _tab_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)
        return w, lay

    def _tab_basic(self):
        w, lay = self._tab_page()

        form = QFormLayout()
        self.standard_box = QComboBox()
        self.standard_box.addItem("BT.601", "bt601")
        self.standard_box.addItem("BT.709", "bt709")
        self.standard_box.addItem("BT.2020", "bt2020")
        self.standard_box.currentIndexChanged.connect(self.on_standard_changed)
        form.addRow("色彩标准", self.standard_box)

        self.mode_box = QComboBox()
        self.mode_box.addItems(MODE_NAMES)
        self.mode_box.currentIndexChanged.connect(self.on_mode_changed)
        form.addRow("显示模式", self.mode_box)
        lay.addLayout(form)

        box = QGroupBox("亮度 / 饱和度")
        bl = QVBoxLayout(box)
        bl.setSpacing(2)
        self.rows = {
            "brightness": SliderRow("亮度", -100, 100, 0, 1, self.on_param_changed),
            "contrast": SliderRow("对比度 (%)", 0, 200, 100, 1, self.on_param_changed,
                                  suffix="%"),
            "saturation": SliderRow("饱和度 (%)", 0, 200, 100, 1, self.on_param_changed,
                                    suffix="%"),
            "hue": SliderRow("色相 (°)", -180, 180, 0, 1, self.on_param_changed,
                             suffix="°"),
        }
        for row in self.rows.values():
            bl.addWidget(row)
        lay.addWidget(box)

        self.btn_reset_all = QPushButton("重置全部参数")
        self.btn_reset_all.clicked.connect(self.reset_params)
        lay.addWidget(self.btn_reset_all)

        self.orig_check = SimpleCheck("显示原图对比（按住空格临时查看）")
        self.orig_check.toggled.connect(self.on_toggle_original)
        lay.addWidget(self.orig_check)

        box2 = QGroupBox("导出")
        bl2 = QVBoxLayout(box2)
        self.btn_export_img = QPushButton("导出图像…")
        self.btn_export_img.clicked.connect(self.export_image)
        self.btn_export_yuv = QPushButton("导出 YUV 原始数据…")
        self.btn_export_yuv.clicked.connect(self.export_yuv)
        self.btn_export_ch = QPushButton("导出三通道灰度图…")
        self.btn_export_ch.clicked.connect(self.export_channels)
        self.btn_cube = QPushButton("导出曲线 LUT (.cube)…")
        self.btn_cube.clicked.connect(self.export_cube)
        for b in (self.btn_export_img, self.btn_export_yuv, self.btn_export_ch, self.btn_cube):
            bl2.addWidget(b)
        lay.addWidget(box2)

        box3 = QGroupBox("预设")
        b3 = QVBoxLayout(box3)
        row = QHBoxLayout()
        self.btn_preset_load = QPushButton("加载预设…")
        self.btn_preset_load.clicked.connect(self.load_preset)
        self.btn_preset_save = QPushButton("保存预设…")
        self.btn_preset_save.clicked.connect(self.save_preset)
        row.addWidget(self.btn_preset_load)
        row.addWidget(self.btn_preset_save)
        b3.addLayout(row)
        lay.addWidget(box3)

        lay.addStretch(1)
        hint = QLabel("提示：Ctrl+滚轮缩放，拖拽平移，空格看原图")
        hint.setStyleSheet("color:#777; font-size:11px;")
        lay.addWidget(hint)
        return w

    def _tab_y(self):
        w, lay = self._tab_page()
        box = QGroupBox("自动对比度")
        bl = QVBoxLayout(box)
        self.ac_on = SimpleCheck("启用（两端各掐掉极值后拉满量程）")
        self.ac_on.setChecked(True)
        self.ac_on.toggled.connect(self.on_param_changed)
        bl.addWidget(self.ac_on)
        self.ac_cutoff = SliderRow("截断 (%)", 0, 10, 1.0, 100, self.on_param_changed,
                                   decimals=2, suffix="%")
        bl.addWidget(self.ac_cutoff)
        bl.addWidget(QLabel("<span style='color:#777;font-size:11px'>拉开中间调层次；只作用于 Y</span>"))
        lay.addWidget(box)

        box2 = QGroupBox("USM 锐化")
        b2 = QVBoxLayout(box2)
        self.usm_on = SimpleCheck("启用（高出阈值的边缘才加强，抑制噪点放大）")
        self.usm_on.setChecked(True)
        self.usm_on.toggled.connect(self.on_param_changed)
        b2.addWidget(self.usm_on)
        self.usm_radius = SliderRow("半径", 0, 20, 6.0, 10, self.on_param_changed, decimals=1)
        self.usm_percent = SliderRow("强度 (%)", 0, 300, 80.0, 10, self.on_param_changed,
                                     decimals=1, suffix="%")
        self.usm_threshold = SliderRow("阈值", 0, 20, 2, 1, self.on_param_changed)
        for r in (self.usm_radius, self.usm_percent, self.usm_threshold):
            b2.addWidget(r)
        lay.addWidget(box2)

        box3 = QGroupBox("局部对比度（Clarity）")
        b3 = QVBoxLayout(box3)
        self.clarity_on = SimpleCheck("启用（大半径弱锐化，拉开中等尺度层次）")
        self.clarity_on.toggled.connect(self.on_param_changed)
        b3.addWidget(self.clarity_on)
        self.clarity_radius = SliderRow("半径", 10, 120, 40.0, 10, self.on_param_changed,
                                        decimals=0)
        self.clarity_percent = SliderRow("强度 (%)", 0, 100, 30.0, 10, self.on_param_changed,
                                         decimals=0, suffix="%")
        for r in (self.clarity_radius, self.clarity_percent):
            b3.addWidget(r)
        lay.addWidget(box3)
        lay.addStretch(1)
        return w

    def _tab_color(self):
        w, lay = self._tab_page()

        box = QGroupBox("色度增益（围绕 128 缩放）")
        bl = QVBoxLayout(box)
        self.cr_gain = SliderRow("Cr 增益 (%)", 0, 200, 100, 1, self.on_param_changed,
                                 suffix="%")
        self.cb_gain = SliderRow("Cb 增益 (%)", 0, 200, 100, 1, self.on_param_changed,
                                 suffix="%")
        bl.addWidget(self.cr_gain)
        bl.addWidget(self.cb_gain)
        lay.addWidget(box)

        box2 = QGroupBox("阴影 / 高光（仅 Y）")
        b2 = QVBoxLayout(box2)
        self.shadows = SliderRow("阴影", -100, 100, 0, 1, self.on_param_changed)
        self.highlights = SliderRow("高光", -100, 100, 0, 1, self.on_param_changed)
        b2.addWidget(self.shadows)
        b2.addWidget(self.highlights)
        lay.addWidget(box2)

        box3 = QGroupBox("曲线")
        b3 = QVBoxLayout(box3)
        self.curve_on = SimpleCheck("启用色调曲线（在下方面板「分析 → 曲线」中编辑）")
        self.curve_on.toggled.connect(self.on_param_changed)
        b3.addWidget(self.curve_on)
        self.btn_show_curve = QPushButton("打开曲线编辑面板")
        self.btn_show_curve.clicked.connect(self.show_curve_tab)
        b3.addWidget(self.btn_show_curve)
        lay.addWidget(box3)
        lay.addStretch(1)
        return w

    def _tab_sim(self):
        w, lay = self._tab_page()

        box = QGroupBox("色度子采样预览")
        bl = QVBoxLayout(box)
        self.sub_box = QComboBox()
        self.sub_box.addItem("4:4:4（无损失）", "444")
        self.sub_box.addItem("4:2:2（水平减半）", "422")
        self.sub_box.addItem("4:2:0（水平+垂直减半）", "420")
        self.sub_box.currentIndexChanged.connect(self.on_param_changed)
        bl.addWidget(self.sub_box)
        bl.addWidget(QLabel("<span style='color:#777;font-size:11px'>真实下采样后上采样，观察细线条/彩色文字的色度损失</span>"))
        lay.addWidget(box)

        box2 = QGroupBox("色深模拟 / 抖动")
        b2 = QVBoxLayout(box2)
        self.levels = SliderRow("色阶", 0, 32, 0, 1, self.on_param_changed)
        self.levels.spin.setToolTip("0 或 1 表示关闭；其余为可用灰度级数")
        self.dither_box = QComboBox()
        self.dither_box.addItem("无抖动", "none")
        self.dither_box.addItem("Bayer 4×4 有序抖动", "bayer4")
        self.dither_box.addItem("Bayer 8×8 有序抖动", "bayer8")
        self.dither_box.addItem("随机噪声抖动", "noise")
        self.dither_box.currentIndexChanged.connect(self.on_param_changed)
        self.dither_strength = SliderRow("抖动强度 (%)", 0, 100, 60, 1, self.on_param_changed,
                                         suffix="%")
        b2.addWidget(self.levels)
        b2.addWidget(self.dither_box)
        b2.addWidget(self.dither_strength)
        bl2 = QHBoxLayout()
        self.btn_levels_off = QPushButton("关闭色深模拟")
        self.btn_levels_off.clicked.connect(lambda: (self.levels.set_value(0),
                                                     self.on_param_changed()))
        bl2.addWidget(self.btn_levels_off)
        b2.addLayout(bl2)
        lay.addWidget(box2)

        box3 = QGroupBox("有限范围（limited / studio）")
        b3 = QVBoxLayout(box3)
        self.limited_on = SimpleCheck("模拟 16-235 / 16-240 范围往返")
        self.limited_on.toggled.connect(self.on_param_changed)
        b3.addWidget(self.limited_on)
        b3.addWidget(QLabel("<span style='color:#777;font-size:11px'>往返后暗部略抬高、亮部略压低</span>"))
        lay.addWidget(box3)

        box4 = QGroupBox("色域警告")
        b4 = QVBoxLayout(box4)
        self.gamut_on = SimpleCheck("标记重建后越界像素（品红）")
        self.gamut_on.toggled.connect(self.on_param_changed)
        b4.addWidget(self.gamut_on)
        lay.addWidget(box4)
        lay.addStretch(1)
        return w

    def _act(self, text, slot, shortcut=None, checkable=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        return a

    def _build_menu(self):
        mb = self.menuBar()
        fm = mb.addMenu("文件(&F)")
        self.act_open = self._act("打开图片…", self.open_dialog, "Ctrl+O")
        self.act_export_img = self._act("导出图像…", self.export_image, "Ctrl+S")
        self.act_export_yuv = self._act("导出 YUV 原始数据…", self.export_yuv)
        self.act_export_ch = self._act("导出三通道灰度图…", self.export_channels)
        self.act_export_cube = self._act("导出曲线 LUT (.cube)…", self.export_cube)
        self.act_export_hist = self._act("导出直方图 PNG…", self.export_hist_png)
        self.act_batch = self._act("批量处理文件夹…", self.batch_process, "Ctrl+B")
        for a in (self.act_open, self.act_export_img, self.act_export_yuv,
                  self.act_export_ch, self.act_export_cube, self.act_export_hist):
            fm.addAction(a)
        fm.addSeparator()
        fm.addAction(self.act_batch)
        fm.addSeparator()
        fm.addAction(self._act("退出", self.close))

        vm = mb.addMenu("视图(&V)")
        self.act_fit = self._act("适应窗口", lambda: self.view.fit_to_window(), "F")
        self.act_100 = self._act("实际大小 100%", lambda: self.view.zoom_100())
        self.act_zin = self._act("放大", lambda: self.view.zoom(1.25), "Ctrl+=")
        self.act_zout = self._act("缩小", lambda: self.view.zoom(1 / 1.25), "Ctrl+-")
        for a in (self.act_fit, self.act_100, self.act_zin, self.act_zout):
            vm.addAction(a)
        vm.addSeparator()
        self.act_orig = self._act("显示原图对比（按住空格临时查看）",
                                  self.on_toggle_original_menu, checkable=True)
        vm.addAction(self.act_orig)
        self.act_compare = self._act("并排对比（左：处理结果 / 右：原图）",
                                     self.on_toggle_compare, "Ctrl+D", checkable=True)
        vm.addAction(self.act_compare)

        vm.addSeparator()
        self.act_dock_params = self.dock_params.toggleViewAction()
        self.act_dock_params.setText("参数与导出栏")
        self.act_dock_params.setShortcut(QKeySequence("Ctrl+1"))
        self.act_dock_analysis = self.dock_analysis.toggleViewAction()
        self.act_dock_analysis.setText("分析栏")
        self.act_dock_analysis.setShortcut(QKeySequence("Ctrl+2"))
        vm.addAction(self.act_dock_params)
        vm.addAction(self.act_dock_analysis)
        vm.addAction(self._act("重置布局", self.reset_layout))

        hm = mb.addMenu("帮助(&H)")
        hm.addAction(self._act("关于", self.show_about))

    def reset_layout(self):
        """恢复两个功能栏的默认位置、可见性与尺寸。"""
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_params)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_analysis)
        self.dock_params.setVisible(True)
        self.dock_analysis.setVisible(True)
        self.resizeDocks([self.dock_params], [400], Qt.Horizontal)
        self.resizeDocks([self.dock_analysis], [230], Qt.Vertical)

    def _update_actions_enabled(self):
        has = self.orig_rgb is not None
        for w in (self.act_export_img, self.act_export_yuv, self.act_export_ch,
                  self.btn_export_img, self.btn_export_yuv, self.btn_export_ch,
                  self.act_fit, self.act_100, self.act_zin, self.act_zout,
                  self.act_orig, self.orig_check, self.mode_box, self.standard_box):
            w.setEnabled(has)
        self.act_batch.setEnabled(True)
        self.act_export_hist.setEnabled(has)

    # ------------------------------------------------------------ 打开图片
    def open_dialog(self):
        start = os.path.dirname(self.path) if self.path else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, "打开图片", start, OPEN_FILTER)
        if path:
            self.load(path)

    def load(self, path):
        try:
            im, fmt = load_image(path)
        except Exception as e:
            QMessageBox.critical(self, "打开失败",
                                 f"无法打开图片：\n{path}\n\n{type(e).__name__}: {e}")
            return
        self.path = path
        self.source_im = im
        rgba = np.asarray(im)
        self.orig_rgb = np.ascontiguousarray(rgba[:, :, :3])
        self.orig_alpha = rgba[:, :, 3].copy() if rgba.ndim == 3 and rgba.shape[2] == 4 else None

        thumb = thumbnail(im)
        self.preview_source = thumb
        self.preview_rgb = np.ascontiguousarray(np.asarray(thumb)[:, :, :3])
        self.base = None
        self.peek_original = False
        self.setWindowTitle(f"{os.path.basename(path)} — {APP_TITLE}")
        alpha_txt = "含透明通道" if self.orig_alpha is not None else "无透明通道"
        self.lbl_info.setText(f"{fmt}  |  {im.width}×{im.height}  |  {alpha_txt}")
        self.lbl_pixel.setText("")
        self._update_actions_enabled()
        self.refresh_preview(refit=True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.load(url.toLocalFile())
                break

    # ------------------------------------------------------------ 参数采集
    def settings(self):
        return dict(
            standard=self.standard_box.currentData(),
            limited=self.limited_on.isChecked(),
            brightness=self.rows["brightness"].value(),
            contrast=self.rows["contrast"].value() / 100.0,
            saturation=self.rows["saturation"].value() / 100.0,
            hue=self.rows["hue"].value(),
            cr_gain=self.cr_gain.value() / 100.0,
            cb_gain=self.cb_gain.value() / 100.0,
            ac_on=self.ac_on.isChecked(),
            ac_cutoff=self.ac_cutoff.value(),
            usm_on=self.usm_on.isChecked(),
            usm_radius=self.usm_radius.value(),
            usm_percent=self.usm_percent.value(),
            usm_threshold=int(self.usm_threshold.value()),
            clarity_on=self.clarity_on.isChecked(),
            clarity_radius=self.clarity_radius.value(),
            clarity_percent=self.clarity_percent.value(),
            curve_on=self.curve_on.isChecked(),
            curve_points=[list(p) for p in self.curve.points],
            shadows=self.shadows.value(),
            highlights=self.highlights.value(),
            levels=int(self.levels.value()),
            dither=self.dither_box.currentData(),
            dither_strength=self.dither_strength.value() / 100.0,
            subsampling=self.sub_box.currentData(),
            gamut=self.gamut_on.isChecked(),
        )

    def apply_settings(self, s):
        """预设加载。"""
        self.standard_box.setCurrentIndex(max(0, self.standard_box.findData(s.get("standard", "bt601"))))
        self.rows["brightness"].set_value(s.get("brightness", 0))
        self.rows["contrast"].set_value(s.get("contrast", 1.0) * 100)
        self.rows["saturation"].set_value(s.get("saturation", 1.0) * 100)
        self.rows["hue"].set_value(s.get("hue", 0))
        self.cr_gain.set_value(s.get("cr_gain", 1.0) * 100)
        self.cb_gain.set_value(s.get("cb_gain", 1.0) * 100)
        self.ac_on.setChecked(s.get("ac_on", True))
        self.ac_cutoff.set_value(s.get("ac_cutoff", 1.0))
        self.usm_on.setChecked(s.get("usm_on", True))
        self.usm_radius.set_value(s.get("usm_radius", 6.0))
        self.usm_percent.set_value(s.get("usm_percent", 80.0))
        self.usm_threshold.set_value(s.get("usm_threshold", 2))
        self.clarity_on.setChecked(s.get("clarity_on", False))
        self.clarity_radius.set_value(s.get("clarity_radius", 40.0))
        self.clarity_percent.set_value(s.get("clarity_percent", 30.0))
        self.curve_on.setChecked(s.get("curve_on", False))
        self.curve.set_points(s.get("curve_points", [(0, 0), (255, 255)]))
        self.shadows.set_value(s.get("shadows", 0))
        self.highlights.set_value(s.get("highlights", 0))
        self.levels.set_value(s.get("levels", 0))
        self.dither_box.setCurrentIndex(max(0, self.dither_box.findData(s.get("dither", "none"))))
        self.dither_strength.set_value(s.get("dither_strength", 0.6) * 100)
        self.sub_box.setCurrentIndex(max(0, self.sub_box.findData(s.get("subsampling", "444"))))
        self.gamut_on.setChecked(s.get("gamut", False))
        self.limited_on.setChecked(s.get("limited", False))
        self.on_param_changed()

    def reset_params(self):
        self.apply_settings(dict())
        self.dither_strength.set_value(60)
        self.on_param_changed()

    def params(self):
        """兼容旧接口：亮度/对比度/饱和度/色相。"""
        return (self.rows["brightness"].value(), self.rows["contrast"].value() / 100.0,
                self.rows["saturation"].value() / 100.0, self.rows["hue"].value())

    # ------------------------------------------------------------ 预览刷新
    def on_standard_changed(self, *_):
        self.base = None
        self.on_param_changed()

    def on_param_changed(self, *_):
        if self.orig_rgb is None:
            return
        self.refresh_timer.start()

    def on_mode_changed(self, idx):
        self.mode = idx
        self.on_param_changed()

    def on_curve_changed(self):
        if self.curve_on.isChecked():
            self.on_param_changed()

    def on_curve_preset(self, idx):
        if idx <= 0:
            return
        name = self.curve_preset.itemText(idx)
        if name in CURVE_PRESETS:
            self.curve_on.setChecked(True)
            self.curve.set_points(CURVE_PRESETS[name])
        self.curve_preset.setCurrentIndex(0)

    def on_curve_reset(self):
        self.curve.set_points([(0, 0), (255, 255)])

    def show_curve_tab(self):
        # 找到底部分析停靠中的曲线页
        for w in self.findChildren(QTabWidget):
            if w is self.tabs:
                continue
            for i in range(w.count()):
                if w.tabText(i) == "曲线":
                    w.setCurrentIndex(i)
                    w.setFocus()
                    return

    def on_toggle_original(self, on):
        self.show_original = on
        self.act_orig.setChecked(on)
        self.on_param_changed()

    def on_toggle_original_menu(self, on):
        self.orig_check.setChecked(on)

    def _process_preview(self, include_sim=True):
        s = self.settings()
        y, cr, cb = core.process_pipeline(self.preview_rgb, s, include_sim=include_sim)
        self.last_proc = (s, self.preview_rgb, y, cr, cb)
        return s, y, cr, cb

    def refresh_preview(self, refit=False):
        if self.orig_rgb is None:
            return
        if self.split_mode():
            self._refresh_split(refit=refit)
            return
        if self.show_original or self.peek_original:
            pix = pil_to_qimage(self.preview_source)
        else:
            pix = np_to_qimage(self._rendered_preview())
        self.view.set_pixmap(QPixmap.fromImage(pix), refit=refit)

    def _rendered_preview(self):
        """当前显示模式下处理后的预览数组，并刷新直方图/波形。"""
        s, y, cr, cb = self._process_preview()
        std = s["standard"]
        if self.mode == MODE_Y:
            arr = to_uint8(y)
        elif self.mode == MODE_CR:
            arr = to_uint8(cr)
        elif self.mode == MODE_CB:
            arr = to_uint8(cb)
        elif self.mode == MODE_PSEUDO:
            arr = to_uint8(core.pseudocolor_from_ycrcb(y, cr, cb))
        elif self.mode == MODE_ORIG:
            arr = self.preview_rgb
        else:
            rgb = core.ycrcb_to_rgb(y, cr, cb, std)
            arr = to_uint8(rgb)
            if s.get("gamut"):
                mask = core.gamut_mask(rgb)
                arr[mask] = (255, 0, 200)
        self._update_analysis(y, cr, cb)
        return arr

    def split_mode(self):
        return self.act_compare.isChecked()

    def on_toggle_compare(self, on):
        self.view_stack.setCurrentWidget(self.split_view if on else self.view)
        if self.orig_rgb is None:
            return
        self.refresh_preview(refit=True)

    def _refresh_split(self, refit=False):
        if self.show_original or self.peek_original:
            left_pm = QPixmap.fromImage(pil_to_qimage(self.preview_source))
        else:
            left_pm = QPixmap.fromImage(np_to_qimage(self._rendered_preview()))
        right_pm = QPixmap.fromImage(pil_to_qimage(self.preview_source))
        self.split_view.set_pixmaps(left_pm, right_pm, refit=refit)

    def _update_analysis(self, y, cr, cb):
        self.hist.set_histograms({
            "Y": core.hist_counts(y),
            "Cr": core.hist_counts(cr),
            "Cb": core.hist_counts(cb),
        })
        torch = core.to_uint8(y)
        h, w = torch.shape
        cols = min(WAVE_COLS, w)
        edges = np.linspace(0, w, cols + 1).astype(int)
        wave = np.zeros((cols, 256), np.float32)
        for i in range(cols):
            seg = torch[:, edges[i]:max(edges[i] + 1, edges[i + 1])].ravel()
            wave[i] = np.bincount(seg, minlength=256)
        self.wave.set_waveform(wave)

    # ------------------------------------------------------------ 像素拾取
    def hover_pixel(self, scene_pos, view=None):
        if self.preview_rgb is None or self.last_proc is None:
            self.lbl_pixel.setText("")
            return
        h, w = self.preview_rgb.shape[:2]
        x, y = int(np.floor(scene_pos.x())), int(np.floor(scene_pos.y()))
        if not (0 <= x < w and 0 <= y < h):
            self.lbl_pixel.setText("")
            return
        s, rgb0, yy, cr, cb = self.last_proc
        r, g, b = (int(v) for v in rgb0[y, x])
        yv, crv, cbv = float(yy[y, x]), float(cr[y, x]), float(cb[y, x])
        txt = (f"({x},{y})  R{r} G{g} B{b}   Y{yv:.0f} Cr{crv:.0f} Cb{cbv:.0f}")
        # 显示对应的 YUV 字节偏移（4:2:0 planar，便于调试）
        H, W = self.orig_rgb.shape[:2]
        Y_off = y * W + x
        txt += f"   |  YUV(420) Y@{Y_off} Cb@{W * H + (y // 2) * (W // 2) + x // 2}"
        if self.split_mode() and view is not None:
            txt = "[原图] " + txt if view is self.split_view.right else "[处理] " + txt
        self.lbl_pixel.setText(txt)

    # ------------------------------------------------------------ 空格临时查看原图
    def eventFilter(self, obj, event):
        if (event.type() in (QEvent.KeyPress, QEvent.KeyRelease)
                and event.key() == Qt.Key_Space
                and self.orig_rgb is not None
                and QApplication.activeWindow() is self
                and not isinstance(QApplication.instance().focusWidget(), QLineEdit)):
            if event.isAutoRepeat():
                return True
            pressed = event.type() == QEvent.KeyPress
            if pressed and not self.peek_original:
                self.peek_original = True
                self.refresh_preview()
            elif not pressed and self.peek_original:
                self.peek_original = False
                self.refresh_preview()
            return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------ 导出
    def _compute_full(self, include_sim=True):
        return core.process_pipeline(self.orig_rgb, self.settings(), include_sim=include_sim)

    def _render_full(self):
        y, cr, cb = self._compute_full()
        std = self.standard_box.currentData()
        if self.mode == MODE_Y:
            arr = to_uint8(y)
        elif self.mode == MODE_CR:
            arr = to_uint8(cr)
        elif self.mode == MODE_CB:
            arr = to_uint8(cb)
        elif self.mode == MODE_PSEUDO:
            arr = to_uint8(core.pseudocolor_from_ycrcb(y, cr, cb))
        elif self.mode == MODE_ORIG:
            arr = self.orig_rgb
        else:
            rgb = core.ycrcb_to_rgb(y, cr, cb, std)
            arr = to_uint8(rgb)
            if self.gamut_on.isChecked():
                mask = core.gamut_mask(rgb)
                arr[mask] = (255, 0, 200)
        if (self.orig_alpha is not None and arr.ndim == 3
                and self.mode in (MODE_RECON, MODE_ORIG)):
            arr = np.dstack([arr, self.orig_alpha])
        return arr

    def export_image(self):
        if self.orig_rgb is None:
            return
        base = os.path.splitext(os.path.basename(self.path or "image"))[0]
        stem = base if self.mode == MODE_ORIG else base + "_YCbCr"
        path, flt = QFileDialog.getSaveFileName(
            self, "导出图像", self._default_path(stem + ".png"), SAVE_FILTER)
        if not path:
            return
        if os.path.splitext(path)[1].lower() in EXPORT_EXTS:
            fmt = self._format_from_ext(path)
        else:
            fmt = self._format_from_filter(flt) or "PNG"
            path += "." + {"JPEG": "jpg", "TIFF": "tiff"}.get(fmt, fmt.lower())

        quality = None
        if fmt in ("JPEG", "WEBP"):
            dlg = QualityDialog(self, fmt)
            if dlg.exec() != QDialog.Accepted:
                return
            quality = dlg.quality

        try:
            arr = self._render_full()
            if fmt == "JPEG" and arr.ndim == 3 and arr.shape[2] == 4:
                a = arr[:, :, 3:4].astype(np.float32) / 255.0
                rgb = arr[:, :, :3].astype(np.float32) * a + 255.0 * (1 - a)
                arr = to_uint8(rgb)
            if arr.ndim == 2:
                im = Image.fromarray(arr, "L")
            elif arr.shape[2] == 4:
                im = Image.fromarray(arr, "RGBA")
            else:
                im = Image.fromarray(arr, "RGB")
            kw = {}
            if quality is not None:
                kw["quality"] = quality
                if fmt == "WEBP":
                    kw["method"] = 4
            if fmt == "TIFF":
                kw["compression"] = "tiff_lzw"
            im.save(path, format=fmt, **kw)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"{type(e).__name__}: {e}")
            return
        self._toast(f"已导出图像：{path}")

    def export_yuv(self):
        if self.orig_rgb is None:
            return
        h, w = self.orig_rgb.shape[:2]
        dlg = YuvExportDialog(self, w, h, self.orig_alpha is not None)
        if dlg.exec() != QDialog.Accepted:
            return
        fmt, depth, limited, use_edit, use_sim = dlg.options()
        if fmt in ("yuy2", "uyvy") and depth == 10:
            depth = 8

        base = os.path.splitext(os.path.basename(self.path or "image"))[0]
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 YUV 原始数据", self._default_path(base + f"_{fmt}.yuv"),
            "YUV 原始数据 (*.yuv);;所有文件 (*.*)")
        if not path:
            return
        if not path.lower().endswith(".yuv"):
            path += ".yuv"
        try:
            s = self.settings()
            s["limited"] = limited
            rgb = self.orig_rgb
            if self.orig_alpha is not None:
                a = self.orig_alpha[:, :, None].astype(np.float32) / 255.0
                rgb = rgb.astype(np.float32) * a
            if use_edit:
                y, cr, cb = core.process_pipeline(rgb, s, include_sim=use_sim)
            else:
                y, cr, cb = core.rgb_to_ycrcb(rgb, s["standard"])
                if limited:
                    y, cr, cb = core.to_limited(y, cr, cb)
            data = core.pack_yuv(y, cr, cb, fmt, depth)
            with open(path, "wb") as f:
                f.write(data)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"{type(e).__name__}: {e}")
            return
        self._toast(f"已导出 YUV：{path}（{core.FORMATS[fmt][0]}，{len(data):,} 字节）")

    def export_channels(self):
        if self.orig_rgb is None:
            return
        start = os.path.dirname(self.path) if self.path else os.path.expanduser("~")
        dir_ = QFileDialog.getExistingDirectory(self, "选择三通道灰度图保存目录", start)
        if not dir_:
            return
        try:
            y, cr, cb = self._compute_full()
            base = os.path.splitext(os.path.basename(self.path or "image"))[0]
            for name, ch in (("Y", y), ("Cr", cr), ("Cb", cb)):
                Image.fromarray(to_uint8(ch), "L").save(os.path.join(dir_, f"{base}_{name}.png"))
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"{type(e).__name__}: {e}")
            return
        self._toast(f"三通道灰度图已导出到：{dir_}")

    def export_cube(self):
        try:
            lut = core.curve_lut(self.curve.points) if self.curve_on.isChecked() else np.arange(256, dtype=np.float32)
            path, _ = QFileDialog.getSaveFileName(
                self, "导出曲线 LUT", self._default_path("tone_curve.cube"),
                "Cube LUT (*.cube);;所有文件 (*.*)")
            if not path:
                return
            if not path.lower().endswith(".cube"):
                path += ".cube"
            lines = ["TITLE \"YCrCb tone curve\"", "LUT_1D_SIZE 256"]
            for v in lut:
                f = float(v) / 255.0
                lines.append(f"{f:.6f} {f:.6f} {f:.6f}")
            with open(path, "w", encoding="utf-8") as fp:
                fp.write("\n".join(lines) + "\n")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"{type(e).__name__}: {e}")
            return
        self._toast(f"已导出曲线 LUT：{path}")

    def export_hist_png(self):
        if self.orig_rgb is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出直方图", self._default_path("histogram.png"), "PNG 图像 (*.png)")
        if not path:
            return
        pm = self.hist.grab()
        pm.save(path)
        self._toast(f"已导出直方图：{path}")

    # ------------------------------------------------------------ 预设
    def save_preset(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存预设", self._default_path("ycrcb_preset.json"),
            "JSON 预设 (*.json);;所有文件 (*.*)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.settings(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"{type(e).__name__}: {e}")
            return
        self._toast(f"预设已保存：{path}")

    def load_preset(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "加载预设", self._default_path(""), "JSON 预设 (*.json);;所有文件 (*.*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                s = json.load(f)
            self.apply_settings(s)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"{type(e).__name__}: {e}")
            return
        self._toast(f"已加载预设：{path}")

    # ------------------------------------------------------------ 批量处理
    def batch_process(self):
        in_dir = QFileDialog.getExistingDirectory(self, "选择要处理的文件夹",
                                                  self.path and os.path.dirname(self.path) or os.path.expanduser("~"))
        if not in_dir:
            return
        out_dir = QFileDialog.getExistingDirectory(self, "选择输出文件夹", in_dir)
        if not out_dir:
            return
        exts = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")
        files = [f for f in sorted(os.listdir(in_dir)) if f.lower().endswith(exts)]
        if not files:
            QMessageBox.information(self, "批量处理", "所选文件夹内没有支持的图片。")
            return
        s = self.settings()
        s["gamut"] = False
        ok, failed = 0, []
        from PySide6.QtWidgets import QProgressDialog
        prog = QProgressDialog("正在批量处理…", "取消", 0, len(files), self)
        prog.setWindowTitle("批量处理")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        for i, name in enumerate(files):
            if prog.wasCanceled():
                break
            prog.setValue(i)
            prog.setLabelText(f"({i + 1}/{len(files)}) {name}")
            QApplication.processEvents()
            try:
                im, _ = load_image(os.path.join(in_dir, name))
                rgb = np.asarray(im.convert("RGB"))
                alpha = im.getchannel("A") if "A" in im.getbands() else None
                y, cr, cb = core.process_pipeline(rgb, s)
                arr = to_uint8(core.ycrcb_to_rgb(y, cr, cb, s["standard"]))
                out_im = Image.fromarray(arr, "RGB")
                if alpha is not None:
                    out_im = out_im.convert("RGBA")
                    out_im.putalpha(alpha)
                stem = os.path.splitext(name)[0]
                out_im.save(os.path.join(out_dir, stem + "_YCbCr.png"))
                ok += 1
            except Exception as e:
                failed.append(f"{name}: {type(e).__name__}: {e}")
        prog.setValue(len(files))
        msg = f"批量处理完成：成功 {ok} 张，失败 {len(failed)} 张。\n输出目录：{out_dir}"
        if failed:
            msg += "\n\n" + "\n".join(failed[:10])
        QMessageBox.information(self, "批量处理", msg)

    # ------------------------------------------------------------ 杂项
    def _toast(self, msg):
        self.statusBar().showMessage(msg, 10000)

    def _default_path(self, filename):
        if self.path:
            return os.path.join(os.path.dirname(self.path), filename)
        return filename

    @staticmethod
    def _format_from_filter(flt):
        flt = (flt or "").lower()
        for key, fmt in (("png", "PNG"), ("jpeg", "JPEG"), ("webp", "WEBP"),
                         ("bmp", "BMP"), ("tiff", "TIFF")):
            if key in flt:
                return fmt
        return None

    @staticmethod
    def _format_from_ext(path):
        return {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".bmp": "BMP",
                ".webp": "WEBP", ".tif": "TIFF", ".tiff": "TIFF"}.get(
            os.path.splitext(path)[1].lower())

    def closeEvent(self, event):
        self.refresh_timer.stop()
        super().closeEvent(event)

    def show_about(self):
        QMessageBox.about(
            self, "关于",
            f"<b>{APP_TITLE}</b> v{APP_VERSION}<br><br>"
            "打开图片 → YCrCb（BT.601 / BT.709 / BT.2020）→ 分析 → 编辑 → 导出。<br><br>"
            "Y 通道增强（自动对比度 + USM）、色调曲线、阴影高光、色度独立增益、<br>"
            "子采样/抖动/色深模拟、色域警告、直方图/波形、批量处理、参数预设。<br><br>"
            "导出：图像（PNG/JPEG/BMP/WebP/TIFF）、"
            "YUV（I420/YV12/NV12/NV21/I444/I422/YUY2/UYVY，8/10-bit）、三通道灰度图、曲线 LUT。")


# ============================================================ 图像 I/O 工具

def load_image(path):
    im = Image.open(path)
    fmt = im.format or "?"
    im.seek(0)
    im.load()
    im = ImageOps.exif_transpose(im)
    if im.mode in ("I", "I;16", "I;16B", "I;16L", "F"):
        arr = np.array(im, dtype=np.float32)
        hi = float(arr.max()) if arr.size else 255.0
        if hi > 255:
            arr = arr * (255.0 / hi)
        im = Image.fromarray(to_uint8(arr), "L")
    if im.mode not in ("RGB", "RGBA"):
        alpha = im.getchannel("A") if "A" in im.getbands() else None
        if alpha is not None and alpha.getextrema()[0] < 255:
            rgb = im.convert("RGB")
            rgb.putalpha(alpha)
            im = rgb
        else:
            im = im.convert("RGB")
    return im, fmt


def thumbnail(im, max_size=PREVIEW_MAX_SIZE):
    if max(im.size) <= max_size:
        return im
    scale = max_size / max(im.size)
    size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
    return im.resize(size, Image.Resampling.LANCZOS)


def np_to_qimage(arr):
    arr = np.ascontiguousarray(arr)
    if arr.ndim == 2:
        h, w = arr.shape
        fmt, channels = QImage.Format_Grayscale8, 1
    elif arr.shape[2] == 4:
        h, w, channels = arr.shape
        fmt = QImage.Format_RGBA8888
    else:
        h, w, channels = arr.shape
        fmt = QImage.Format_RGB888
    return QImage(arr.tobytes(), w, h, w * channels, fmt).copy()


def pil_to_qimage(pil_img):
    return np_to_qimage(np.asarray(pil_img))


def to_uint8(arr):
    return core.to_uint8(arr)


# ============================================================ 入口

def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def app_icon():
    path = resource_path("app.ico")
    return QIcon(path) if os.path.isfile(path) else QIcon()


def light_palette():
    """强制浅色调色板：系统处于深色模式时也保持一致的白底深字。"""
    from PySide6.QtGui import QColor as C, QPalette
    pal = QPalette()
    roles = {
        QPalette.Window: C(0xf2, 0xf3, 0xf5),
        QPalette.WindowText: C(0x1f, 0x23, 0x29),
        QPalette.Base: C(0xff, 0xff, 0xff),
        QPalette.AlternateBase: C(0xf7, 0xf8, 0xfa),
        QPalette.Text: C(0x1f, 0x23, 0x29),
        QPalette.Button: C(0xff, 0xff, 0xff),
        QPalette.ButtonText: C(0x1f, 0x23, 0x29),
        QPalette.BrightText: C(0xff, 0xff, 0xff),
        QPalette.Highlight: C(0x4a, 0x80, 0xe8),
        QPalette.HighlightedText: C(0xff, 0xff, 0xff),
        QPalette.ToolTipBase: C(0xff, 0xfb, 0xe6),
        QPalette.ToolTipText: C(0x1f, 0x23, 0x29),
        QPalette.PlaceholderText: C(0x99, 0x9c, 0xa3),
        QPalette.Light: C(0xff, 0xff, 0xff),
        QPalette.Midlight: C(0xe8, 0xea, 0xee),
        QPalette.Mid: C(0xd5, 0xd7, 0xdb),
        QPalette.Dark: C(0xb0, 0xb3, 0xb8),
        QPalette.Shadow: C(0x80, 0x83, 0x88),
    }
    for role, color in roles.items():
        pal.setColor(role, color)
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, C(0xb0, 0xb3, 0xb8))
    return pal


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(app_icon())
    win = MainWindow()
    win.show()
    win.raise_()
    win.activateWindow()
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        win.load(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
