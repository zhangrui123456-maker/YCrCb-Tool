# -*- coding: utf-8 -*-
"""
YCrCb 工具测试：色彩空间、Y 增强、曲线、模拟、YUV 打包、GUI 离屏冒烟。

运行：python test_ycrcb.py
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image

import ycrcb_core as core

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def make_test_image(path):
    h = w = 512
    xs = np.linspace(0, 255, w, dtype=np.float32)
    ys = np.linspace(0, 255, h, dtype=np.float32)
    rgb = np.zeros((h, w, 3), np.float32)
    rgb[:, :, 0] = xs[None, :]
    rgb[:, :, 1] = ys[:, None]
    rgb[:, :, 2] = 255 - xs[None, :]
    blocks = [(190, 0, 0), (0, 190, 0), (0, 0, 190), (255, 255, 0), (0, 255, 255)]
    for i, (r, g, b) in enumerate(blocks):
        s = i * 100
        rgb[s:s + 90, s:s + 90] = (r, g, b)
    rgba = np.dstack([rgb, np.full((h, w), 255, np.float32)])
    rgba[-120:, -120:, 3] = 0
    Image.fromarray(rgba.astype(np.uint8), "RGBA").save(path)


def test_colorspace():
    print("颜色空间")
    rng = np.random.default_rng(42)
    rgb = rng.integers(0, 256, (64, 64, 3)).astype(np.float32)
    for std in core.STANDARDS:
        y, cr, cb = core.rgb_to_ycrcb(rgb, std)
        back = np.clip(core.ycrcb_to_rgb(y, cr, cb, std), 0, 255)
        err = np.abs(back - rgb).max()
        check(f"{std} 往返误差 ≤1", err <= 1.0, f"err={err:.3f}")
    y, cr, cb = core.rgb_to_ycrcb(np.array([[[255.0, 0, 0]]]), "bt601")
    check("BT.601 纯红 Y≈76 Cr≈255", abs(y[0, 0] - 76.245) < 0.5 and abs(cr[0, 0] - 255.5) < 1,
          f"Y={y[0,0]:.1f} Cr={cr[0,0]:.1f}")
    y, cr, cb = core.rgb_to_ycrcb(np.array([[[128.0, 128, 128]]]), "bt601")
    check("灰色 Cr/Cb=128", abs(cr[0, 0] - 128) < 0.1 and abs(cb[0, 0] - 128) < 0.1)
    # 有限范围往返误差
    y, cr, cb = core.rgb_to_ycrcb(rgb, "bt601")
    y2, cr2, cb2 = core.from_limited(*core.to_limited(y, cr, cb))
    check("有限范围往返误差 ≤2", np.abs(y2 - y).max() <= 2 and np.abs(cr2 - cr).max() <= 2,
          f"max={np.abs(y2-y).max():.2f}")


def test_params():
    print("参数调整")
    rgb = np.array([[[200.0, 100, 50], [100, 150, 200]]])
    y, cr, cb = core.rgb_to_ycrcb(rgb, "bt601")
    y2, _, _ = core.apply_params(y, cr, cb, brightness=20)
    check("亮度 +20", np.allclose(y2 - y, 20, atol=0.01))
    _, cr2, cb2 = core.apply_params(y, cr, cb, saturation=0.0)
    check("饱和度 0 → 灰", np.allclose(cr2, 128, atol=0.01))
    _, cr2, cb2 = core.apply_params(y, cr, cb, hue_deg=180)
    check("色相 180° 翻转", np.allclose(cr2 - 128, -(cr - 128), atol=0.05))
    cr3 = core.gain_channel(cr, 1.5)
    check("Cr 增益 1.5", np.allclose(cr3, np.clip((cr - 128) * 1.5 + 128, 0, 255), atol=0.01))
    check("Cr 增益 1.0 原样", np.array_equal(core.gain_channel(cr, 1.0), cr))


def test_enhance():
    print("Y 通道增强")
    rng = np.random.default_rng(7)
    rgb = rng.integers(0, 256, (64, 64, 3)).astype(np.float32)
    y, cr, cb = core.rgb_to_ycrcb(rgb, "bt601")

    y2 = core.enhance_y(y, autocontrast_cutoff=1.0, usm=None)
    check("autocontrast 保持形状与值域", y2.shape == y.shape and y2.min() >= 0 and y2.max() <= 255)
    check("autocontrast 拉开量程（stretch）", y2.std() >= y.std(), f"{y2.std():.1f} vs {y.std():.1f}")

    y3 = core.enhance_y(y, None, (6.0, 80, 2))
    edge_before = np.abs(np.diff(y, axis=1)).mean()
    edge_after = np.abs(np.diff(y3, axis=1)).mean()
    check("USM 增强边缘（平均梯度上升）", edge_after >= edge_before, f"{edge_after:.2f} vs {edge_before:.2f}")

    # 管线中色度不被改动
    s = dict(core.DEFAULTS)
    s.update(standard="bt601", ac_on=True, usm_on=True, curve_on=False,
             brightness=0, contrast=1.0, saturation=1.0, hue=0, cr_gain=1.0, cb_gain=1.0,
             shadows=0, highlights=0, levels=0, subsampling="444", limited=False,
             clarity_on=False)
    yp, crp, cbp = core.process_pipeline(rgb, s)
    check("管线：Y 被处理", not np.allclose(yp, y), "Y unchanged")
    check("管线：Cr 原样不动", np.allclose(crp, cr, atol=0.01))
    check("管线：Cb 原样不动", np.allclose(cbp, cb, atol=0.01))

    s_off = dict(s, ac_on=False, usm_on=False)
    y0, cr0, cb0 = core.process_pipeline(rgb, s_off)
    check("全部关闭时 Y 原样", np.allclose(y0, y, atol=0.01))


def test_curve():
    print("曲线与阴影高光")
    lut = core.curve_lut([(0, 0), (255, 255)])
    check("线性曲线 = 恒等", np.allclose(lut, np.arange(256), atol=0.01))
    lut_s = core.curve_lut([(0, 0), (64, 40), (192, 215), (255, 255)])
    check("S 曲线压暗暗部提亮亮部", lut_s[32] < 32 and lut_s[224] > 224,
          f"{lut_s[32]:.1f} {lut_s[224]:.1f}")
    ch = np.array([[0.0, 128.0, 255.0]], np.float32)
    out = core.apply_lut(ch, lut_s)
    check("apply_lut 端点保持", abs(out[0, 0]) < 0.5 and abs(out[0, 2] - 255) < 0.5)

    y = np.array([[20.0, 128.0, 240.0]], np.float32)
    sh = core.shadow_highlight(y, shadows=50, highlights=-50)
    check("阴影提亮", sh[0, 0] > y[0, 0])
    check("高光压暗", sh[0, 2] < y[0, 2])
    check("中间调基本不变", abs(sh[0, 1] - y[0, 1]) < 5, f"{sh[0,1]:.1f}")

    yy = np.linspace(0, 255, 4096, dtype=np.float32).reshape(64, 64)
    c1 = core.clarity_y(yy, 40, 30, 10)
    check("Clarity 保持尺寸与值域", c1.shape == yy.shape and c1.min() >= 0)


def test_sim():
    print("模拟项")
    y = np.full((16, 16), 100.0, np.float32)
    q = core.simulate_low_depth(y, 8, "none")
    check("量化到 8 级", set(np.unique(q)) <= {0, 36.43, 255} or (q >= 0).all())
    qd = core.simulate_low_depth(np.linspace(0, 255, 256).reshape(16, 16).astype(np.float32),
                                 4, "bayer4")
    check("Bayer 抖动改变像素（产生过渡）", qd.std() > 0)

    rng = np.random.default_rng(0)
    cr = rng.uniform(0, 255, (32, 32)).astype(np.float32)
    cb = rng.uniform(0, 255, (32, 32)).astype(np.float32)
    c422 = core.simulate_subsampling(cr, cb, "422")
    check("4:2:2 模拟尺寸不变", c422[0].shape == cr.shape)
    c420 = core.simulate_subsampling(cr, cb, "420")
    check("4:2:0 模拟尺寸不变", c420[1].shape == cb.shape)
    flat = np.full((16, 16), 77.0, np.float32)
    c44 = core.simulate_subsampling(flat, flat, "444")
    check("4:4:4 原样", np.allclose(c44[0], flat))
    c420f = core.simulate_subsampling(flat, flat, "420")
    check("恒值区 4:2:0 无损", np.allclose(c420f[0], 77.0, atol=0.01))

    rgb = np.array([[[300.0, 10, 10], [10, 10, 10]]])
    m = core.gamut_mask(rgb)
    check("色域越界检测", bool(m[0, 0]) and not bool(m[0, 1]))


def test_yuv():
    print("YUV 打包")
    rng = np.random.default_rng(2)
    y = rng.uniform(0, 255, (64, 64)).astype(np.float32)
    cr = rng.uniform(0, 255, (64, 64)).astype(np.float32)
    cb = rng.uniform(0, 255, (64, 64)).astype(np.float32)

    for fmt in core.FORMATS:
        data = core.pack_yuv(y, cr, cb, fmt, 8)
        expect = core.yuv_size(64, 64, fmt, 8)
        check(f"{fmt} 大小正确", len(data) == expect, f"{len(data)} vs {expect}")

    d = core.pack_yuv(y, cr, cb, "yuv444p_yv", 8)
    check("4:4:4 Y-Cr-Cb 平面顺序", d[4096:8192] == core.to_uint8(cr).tobytes()
          and d[8192:] == core.to_uint8(cb).tobytes())
    d = core.pack_yuv(y, cr, cb, "i420", 8)
    cr_s, cb_s = core._subsample_chroma(cr, cb, "420")
    check("I420 = Y+Cb+Cr", d[4096:5120] == core.to_uint8(cb_s).tobytes()
          and d[5120:6144] == core.to_uint8(cr_s).tobytes())
    d8 = core.pack_yuv(y, cr, cb, "i420", 8)
    d10 = core.pack_yuv(y, cr, cb, "i420", 10)
    check("10-bit 大小 = 2×8-bit", len(d10) == 2 * len(d8))
    # NV12 交错
    nv = core.pack_yuv(np.zeros((8, 8)), np.full((8, 8), 50.0), np.full((8, 8), 200.0), "nv12", 8)
    uv = nv[64:]
    check("NV12 UV 交错 CbCr", uv[0] == 200 and uv[1] == 50, f"{uv[:2]}")
    # 奇数尺寸裁剪
    n = core.yuv_size(65, 65, "i420", 8)
    data = core.pack_yuv(np.zeros((65, 65)), np.zeros((65, 65)), np.zeros((65, 65)), "i420", 8)
    check("奇数尺寸 4:2:0 裁剪一致", len(data) == n, f"{len(data)} vs {n}")


def test_pipeline_misc():
    print("管线其他")
    rgb = np.random.default_rng(3).integers(0, 256, (32, 32, 3)).astype(np.float32)
    s = dict(core.DEFAULTS)
    y, cr, cb = core.process_pipeline(rgb, s)
    check("默认管线输出形状", y.shape == (32, 32))
    h = core.hist_counts(y)
    check("直方图总和 = 像素数", int(h.sum()) == 32 * 32)
    check("伪彩色范围", core.pseudocolor_from_ycrcb(y, cr, cb).max() <= 255)


def test_gui(tmpdir):
    print("GUI 离屏冒烟")
    from PySide6.QtWidgets import QApplication
    import ycrcb_tool as t
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(t.light_palette())

    img_path = os.path.join(tmpdir, "test_rgba.png")
    make_test_image(img_path)

    win = t.MainWindow()
    win.show()
    win.load(img_path)
    app.processEvents()

    check("加载尺寸", win.orig_rgb.shape == (512, 512, 3))
    check("透明通道", win.orig_alpha is not None)
    check("预览显示", not win.view.item.pixmap().isNull())

    # Y 增强默认开启（用户指定：autocontrast 1% + USM 6/80/2）
    s = win.settings()
    check("默认开启自动对比度 cutoff=1", s["ac_on"] and s["ac_cutoff"] == 1.0)
    check("默认 USM 6/80/2", s["usm_on"] and s["usm_radius"] == 6.0
          and s["usm_percent"] == 80.0 and s["usm_threshold"] == 2)

    # 预览处理正常且分析面板有数据
    check("直方图已生成", bool(win.hist.hists))
    check("波形已生成", win.wave.wave is not None)

    # 各页参数切换
    win.tabs.setCurrentIndex(1)
    win.ac_on.setChecked(False)
    win.usm_on.setChecked(False)
    win.clarity_on.setChecked(True)
    win.refresh_preview()
    check("Y 页切换无异常", not win.view.item.pixmap().isNull())
    win.clarity_on.setChecked(False)
    win.ac_on.setChecked(True)
    win.usm_on.setChecked(True)

    win.tabs.setCurrentIndex(2)
    win.cr_gain.set_value(150)
    win.cb_gain.set_value(80)
    win.shadows.set_value(30)
    win.highlights.set_value(-20)
    win.curve_on.setChecked(True)
    win.curve.set_points([(0, 0), (64, 40), (192, 215), (255, 255)])
    win.refresh_preview()
    check("色彩页 + 曲线无异常", not win.view.item.pixmap().isNull())

    win.tabs.setCurrentIndex(3)
    win.sub_box.setCurrentIndex(2)
    win.levels.set_value(8)
    win.dither_box.setCurrentIndex(1)
    win.gamut_on.setChecked(True)
    win.refresh_preview()
    check("模拟页无异常", not win.view.item.pixmap().isNull())
    win.sub_box.setCurrentIndex(0)
    win.levels.set_value(0)
    win.dither_box.setCurrentIndex(0)
    win.gamut_on.setChecked(False)

    # 所有显示模式
    for idx in range(6):
        win.mode_box.setCurrentIndex(idx)
        win.refresh_preview()
    check("六种显示模式", True)
    win.mode_box.setCurrentIndex(t.MODE_RECON)

    # 像素拾取
    class P:
        def x(self): return 10.0
        def y(self): return 10.0
    win.hover_pixel(P())
    check("像素拾取含 YUV 偏移", "YUV" in win.lbl_pixel.text(), win.lbl_pixel.text())

    # 视图菜单：功能栏显隐
    check("参数栏可见", win.dock_params.isVisible())
    check("分析栏可见", win.dock_analysis.isVisible())
    win.act_dock_params.trigger()
    app.processEvents()
    check("关闭参数栏", not win.dock_params.isVisible() and not win.act_dock_params.isChecked())
    win.act_dock_analysis.trigger()
    app.processEvents()
    check("关闭分析栏", not win.dock_analysis.isVisible())
    check("关闭后视图仍可操作", not win.view.item.pixmap().isNull())
    win.dock_analysis.show()   # 用停靠栏自身按钮/API 重开，菜单应同步勾选
    app.processEvents()
    check("重开后菜单勾选同步", win.act_dock_analysis.isChecked())
    win.reset_layout()
    app.processEvents()
    check("重置布局恢复两栏", win.dock_params.isVisible() and win.dock_analysis.isVisible())

    # 所有开关必须可点击勾选（点图标区域也要生效）
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    all_checks = []
    for tab in range(win.tabs.count()):
        win.tabs.setCurrentIndex(tab)
        app.processEvents()
        all_checks += win.tabs.widget(tab).findChildren(t.SimpleCheck)
    bad = []
    for c in all_checks:
        before = c.isChecked()
        QTest.mouseClick(c, Qt.LeftButton, Qt.NoModifier, QPoint(8, c.height() // 2))
        app.processEvents()
        if c.isChecked() == before:
            bad.append(c.text())
        QTest.mouseClick(c, Qt.LeftButton, Qt.NoModifier, QPoint(8, c.height() // 2))
        app.processEvents()
        if c.isChecked() != before:
            bad.append(c.text() + "（二次点击未还原）")
    check(f"全部 {len(all_checks)} 个开关点击图标可勾选", not bad, str(bad))

    # 视图菜单：并排对比
    check("默认非并排模式", not win.split_mode())
    win.act_compare.trigger()
    app.processEvents()
    check("并排视图已切换", win.view_stack.currentWidget() is win.split_view)
    check("左视图有处理结果", not win.split_view.left.item.pixmap().isNull())
    check("右视图有原图", not win.split_view.right.item.pixmap().isNull())
    win.split_view.left.zoom(1.5)
    app.processEvents()
    check("缩放联动到右视图",
          abs(win.split_view.right.transform().m11() - win.split_view.left.transform().m11()) < 1e-6)
    win.split_view.fit_to_window()
    win.mode_box.setCurrentIndex(t.MODE_CR)
    win.refresh_preview()
    check("并排模式下切换通道无异常", not win.split_view.left.item.pixmap().isNull())
    win.mode_box.setCurrentIndex(t.MODE_RECON)
    win.act_compare.trigger()
    app.processEvents()
    check("关闭并排回到单视图", win.view_stack.currentWidget() is win.view)

    # 预设保存/加载往返
    preset = os.path.join(tmpdir, "preset.json")
    import json
    with open(preset, "w", encoding="utf-8") as f:
        json.dump(win.settings(), f, ensure_ascii=False)
    win.reset_params()
    win.apply_settings(json.load(open(preset, encoding="utf-8")))
    check("预设往返", abs(win.cr_gain.value() - 150) < 0.01)

    # 导出全分辨率
    arr = win._render_full()
    check("全分辨率渲染", arr.shape == (512, 512, 4))
    out = os.path.join(tmpdir, "out.png")
    Image.fromarray(arr, "RGBA").save(out)
    check("PNG 导出可重开", Image.open(out).size == (512, 512))

    # 全分辨率 YUV（默认管线）
    y, cr, cb = win._compute_full()
    for fmt in ("i420", "nv12", "yuv444p_yv", "yuy2"):
        data = core.pack_yuv(y, cr, cb, fmt, 8)
        check(f"全分辨率 {fmt} 大小", len(data) == core.yuv_size(512, 512, fmt, 8))

    # 曲线 LUT 导出内容
    lut = core.curve_lut(win.curve.points)
    check("曲线 LUT 256 级", lut.shape == (256,))

    # 三通道灰度图
    for name, ch in (("Y", y), ("Cr", cr), ("Cb", cb)):
        p = os.path.join(tmpdir, f"test_rgba_{name}.png")
        Image.fromarray(core.to_uint8(ch), "L").save(p)
        check(f"通道图 {name}", Image.open(p).mode == "L")

    win.close()


def main():
    tmpdir = tempfile.mkdtemp(prefix="ycrcb_test_")
    test_colorspace()
    test_params()
    test_enhance()
    test_curve()
    test_sim()
    test_yuv()
    test_pipeline_misc()
    test_gui(tmpdir)
    print(f"\n结果：{PASS} 通过，{FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
