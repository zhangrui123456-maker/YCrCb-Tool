# -*- coding: utf-8 -*-
"""
YCrCb 核心处理（纯 numpy + Pillow，无 GUI 依赖）

覆盖：多色彩标准转换、全范围/有限范围、Y 通道增强、曲线、阴影高光、
通道增益、色深模拟（抖动）、色度子采样模拟、色域检测、YUV 打包、直方图。

所有函数输入为 float32/uint8 数组，(H, W) 或 (H, W, 3)，值域 0-255。
"""

import numpy as np
from PIL import Image, ImageFilter, ImageOps

STANDARDS = ("bt601", "bt709", "bt2020")


# ============================================================ 转换矩阵

_MATRICES, _INVERSES = {}, {}


def _matrix(standard):
    """RGB→YCrCb 的 3×3 矩阵（行：Y, Cr, Cb），Cr/Cb 中值在 128。"""
    if standard == "bt709":
        return np.array([[0.2126, 0.7152, 0.0722],
                         [0.500, -0.4542, -0.0458],
                         [-0.1146, -0.3854, 0.500]], np.float32)
    if standard == "bt2020":
        return np.array([[0.2627, 0.6780, 0.0593],
                         [0.500, -0.4598, -0.0402],
                         [-0.1396, -0.3604, 0.500]], np.float32)
    return np.array([[0.299, 0.587, 0.114],
                     [0.500, -0.4187, -0.0813],
                     [-0.1687, -0.3313, 0.500]], np.float32)


def rgb_to_ycrcb(rgb, standard="bt601"):
    """RGB (H,W,3) 0-255 → (Y, Cr, Cb)，均为 0-255 float32。"""
    if standard not in _MATRICES:
        _MATRICES[standard] = _matrix(standard)
        _INVERSES[standard] = np.linalg.inv(_MATRICES[standard]).astype(np.float32)
    m = _MATRICES[standard]
    rgb = np.asarray(rgb, np.float32)
    y = rgb @ m[0]
    cr = rgb @ m[1] + 128.0
    cb = rgb @ m[2] + 128.0
    return y, np.clip(cr, 0, 255), np.clip(cb, 0, 255)


def ycrcb_to_rgb(y, cr, cb, standard="bt601"):
    """(Y, Cr, Cb) → RGB (H,W,3) float32 0-255（未裁剪）。"""
    if standard not in _INVERSES:
        rgb_to_ycrcb(np.zeros((1, 1, 3), np.float32), standard)
    inv = _INVERSES[standard]
    centered = np.stack([y, cr - 128.0, cb - 128.0], axis=-1)
    return centered @ inv.T


def to_limited(y, cr, cb):
    """全范围 → 有限范围（studio）：Y 16-235，Cr/Cb 16-240。"""
    return (np.clip(y, 0, 255) * (219.0 / 255.0) + 16.0,
            np.clip(cr, 0, 255) * (224.0 / 255.0) + 16.0,
            np.clip(cb, 0, 255) * (224.0 / 255.0) + 16.0)


def from_limited(y, cr, cb):
    """有限范围 → 全范围（逆向映射，用于模拟往返损失）。"""
    return (np.clip((np.clip(y, 16, 235) - 16.0) * (255.0 / 219.0), 0, 255),
            np.clip((np.clip(cr, 16, 240) - 16.0) * (255.0 / 224.0), 0, 255),
            np.clip((np.clip(cb, 16, 240) - 16.0) * (255.0 / 224.0), 0, 255))


def to_uint8(arr):
    if arr.dtype == np.uint8:
        return arr
    return np.clip(np.rint(arr), 0, 255).astype(np.uint8)


# ============================================================ Y 通道处理

def apply_params(y, cr, cb, brightness=0.0, contrast=1.0, saturation=1.0, hue_deg=0.0):
    """在 YCrCb 空间做亮度/对比度/饱和度/色相调整。"""
    if brightness or contrast != 1.0:
        y = np.clip((y - 128.0) * contrast + 128.0 + brightness, 0, 255)
    if saturation != 1.0 or hue_deg:
        u = cb - 128.0
        v = cr - 128.0
        if saturation != 1.0:
            u, v = u * saturation, v * saturation
        if hue_deg:
            theta = np.deg2rad(hue_deg)
            ct, st = np.cos(theta), np.sin(theta)
            u, v = u * ct - v * st, u * st + v * ct
        cb = np.clip(u + 128.0, 0, 255)
        cr = np.clip(v + 128.0, 0, 255)
    return y, cr, cb


def enhance_y(y, autocontrast_cutoff=None, usm=None):
    """仅处理亮度通道 Y：自动对比度 → USM 锐化。色度通道不参与、不受影响。

    autocontrast_cutoff：两端各掐掉的极值百分比（None 关闭）
    usm：(半径, 强度%, 阈值)（None 关闭）
    """
    if autocontrast_cutoff is None and not usm:
        return y
    im = Image.fromarray(to_uint8(y), "L")
    if autocontrast_cutoff is not None:
        im = ImageOps.autocontrast(im, cutoff=float(autocontrast_cutoff))
    if usm:
        radius, percent, threshold = usm
        im = im.filter(ImageFilter.UnsharpMask(
            radius=float(radius), percent=int(percent), threshold=int(threshold)))
    return np.asarray(im, np.float32)


def clarity_y(y, radius=40.0, percent=30.0, threshold=10):
    """中等半径 USM（局部对比度），提升中等尺度层次。"""
    im = Image.fromarray(to_uint8(y), "L")
    im = im.filter(ImageFilter.UnsharpMask(radius=float(radius), percent=int(percent),
                                           threshold=int(threshold)))
    return np.asarray(im, np.float32)


def shadow_highlight(y, shadows=0.0, highlights=0.0):
    """只作用于暗部/亮部的平滑调整。正值提亮对应区域，负值压暗。"""
    if not shadows and not highlights:
        return y
    t = y / 255.0
    y = y.copy()
    if shadows:
        m = np.clip(1.0 - t / 0.55, 0.0, 1.0)
        y = y + (shadows / 100.0) * 90.0 * (m * m * (3.0 - 2.0 * m))
    if highlights:
        m = np.clip((t - 0.45) / 0.55, 0.0, 1.0)
        y = y + (highlights / 100.0) * 90.0 * (m * m * (3.0 - 2.0 * m))
    return np.clip(y, 0, 255)


def curve_lut(points):
    """控制点 → 256 级查询表（分段线性）。points 至少 2 个，按 x 升序。"""
    pts = sorted([(float(x), float(y)) for x, y in points])
    if len(pts) < 2:
        pts = [(0.0, 0.0), (255.0, 255.0)]
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    return np.interp(np.arange(256), xs, ys).astype(np.float32)


def apply_lut(y, lut):
    idx = np.clip(np.rint(y), 0, 255).astype(np.uint8)
    return lut[idx]


def gain_channel(c, gain):
    """色度通道独立增益：围绕 128 缩放。"""
    if gain == 1.0:
        return c
    return np.clip((c - 128.0) * gain + 128.0, 0, 255)


# ============================================================ 模拟

def _bayer(n):
    m = np.array([[0]], np.int32)
    while m.shape[0] < n:
        m = np.block([[4 * m, 4 * m + 2], [4 * m + 3, 4 * m + 1]])
    return (m / float(m.size) - 0.5).astype(np.float32)


_BAYER4 = _bayer(4)
_BAYER8 = _bayer(8)


def simulate_low_depth(ch, levels, method="none", strength=1.0):
    """色深模拟：量化到 levels 级，可选抖动。仅用于观察条带/平网。"""
    if levels <= 1:
        return ch
    step = 255.0 / (levels - 1)
    h, w = ch.shape
    if method == "bayer4":
        tile = np.tile(_BAYER4, (h // 4 + 1, w // 4 + 1))[:h, :w]
        ch = ch + tile * step * strength
    elif method == "bayer8":
        tile = np.tile(_BAYER8, (h // 8 + 1, w // 8 + 1))[:h, :w]
        ch = ch + tile * step * strength
    elif method == "noise":
        rng = np.random.default_rng(12345)      # 固定种子：预览不闪烁
        ch = ch + rng.uniform(-0.5, 0.5, (h, w)).astype(np.float32) * step * strength
    return np.clip(np.rint(ch / step) * step, 0, 255)


def _down_up(ch, by, bx):
    """块平均下采样 + 双线性上采样回原尺寸（色度子采样可视化）。"""
    h, w = ch.shape
    h2, w2 = (h // by) * by, (w // bx) * bx
    if h2 == 0 or w2 == 0:
        return ch
    core = ch[:h2, :w2].reshape(h2 // by, by, w2 // bx, bx).mean(axis=(1, 3))
    im = Image.fromarray(core.astype(np.float32), "F")
    return np.asarray(im.resize((w, h), Image.Resampling.BILINEAR), np.float32)


def simulate_subsampling(cr, cb, mode):
    """4:2:2 / 4:2:0 色度子采样往返模拟。返回同尺寸 (cr, cb)。"""
    if mode == "422":
        return _down_up(cr, 1, 2), _down_up(cb, 1, 2)
    if mode == "420":
        return _down_up(cr, 2, 2), _down_up(cb, 2, 2)
    return cr, cb


def gamut_mask(rgb):
    """重建 RGB 后越界的像素掩码（float 未裁剪输入）。"""
    return (rgb < -0.5).any(axis=-1) | (rgb > 255.5).any(axis=-1)


def hist_counts(ch):
    """256 级直方图计数。"""
    return np.bincount(to_uint8(ch).ravel(), minlength=256)


def pseudocolor_from_ycrcb(y, cr, cb):
    """伪彩色：Cr→红, Cb→绿, Y→蓝。"""
    return np.clip(np.stack([cr, cb, y], axis=-1), 0, 255)


# ============================================================ 完整管线

DEFAULTS = dict(
    standard="bt601",
    limited=False,
    brightness=0.0, contrast=1.0, saturation=1.0, hue=0.0,
    cr_gain=1.0, cb_gain=1.0,
    ac_on=True, ac_cutoff=1.0,
    usm_on=True, usm_radius=6.0, usm_percent=80.0, usm_threshold=2,
    clarity_on=False, clarity_radius=40.0, clarity_percent=30.0, clarity_threshold=10,
    curve_on=False, curve_points=[[0, 0], [255, 255]],
    shadows=0.0, highlights=0.0,
    levels=0, dither="none", dither_strength=0.6,
    subsampling="444",
    gamut=False,
)


def process_pipeline(rgb, s, include_sim=True):
    """按设置对 RGB (H,W,3) 全流程处理，返回 (Y, Cr, Cb)。

    顺序：Y 增强 → 曲线 → 阴影高光 → 全局调参 → 色度增益
          → [模拟] 色深/抖动 → 子采样 → 有限范围往返
    """
    y, cr, cb = rgb_to_ycrcb(rgb, s.get("standard", "bt601"))

    ac = s.get("ac_cutoff", 1.0) if s.get("ac_on") else None
    usm = ((s.get("usm_radius", 6.0), s.get("usm_percent", 80.0),
            s.get("usm_threshold", 2)) if s.get("usm_on") else None)
    y = enhance_y(y, ac, usm)

    if s.get("clarity_on"):
        y = clarity_y(y, s.get("clarity_radius", 40.0),
                      s.get("clarity_percent", 30.0), s.get("clarity_threshold", 10))
    if s.get("curve_on"):
        y = apply_lut(y, curve_lut(s.get("curve_points", [[0, 0], [255, 255]])))
    if s.get("shadows") or s.get("highlights"):
        y = shadow_highlight(y, s.get("shadows", 0.0), s.get("highlights", 0.0))

    y, cr, cb = apply_params(y, cr, cb, s.get("brightness", 0.0), s.get("contrast", 1.0),
                             s.get("saturation", 1.0), s.get("hue", 0.0))
    cr = gain_channel(cr, s.get("cr_gain", 1.0))
    cb = gain_channel(cb, s.get("cb_gain", 1.0))

    if include_sim:
        levels = int(s.get("levels", 0))
        if levels > 1:
            y = simulate_low_depth(y, levels, s.get("dither", "none"),
                                   s.get("dither_strength", 0.6))
        cr, cb = simulate_subsampling(cr, cb, s.get("subsampling", "444"))
    if s.get("limited"):
        y, cr, cb = from_limited(*to_limited(y, cr, cb))
    return y, cr, cb


# ============================================================ YUV 打包

FORMATS = {
    # key: (显示名, 子采样)
    "yuv444p_yv": ("4:4:4 planar（Y-Cr-Cb）", "444"),
    "yuv444p_yuv": ("4:4:4 planar（Y-Cb-Cr，即 I444）", "444"),
    "i420": ("I420（Y-Cb-Cr planar）", "420"),
    "yv12": ("YV12（Y-Cr-Cb planar）", "420"),
    "nv12": ("NV12（Y + CbCr 交错）", "420"),
    "nv21": ("NV21（Y + CrCb 交错）", "420"),
    "yuv422p_yv": ("4:2:2 planar（Y-Cr-Cb）", "422"),
    "yuv422p_yuv": ("4:2:2 planar（Y-Cb-Cr，即 I422）", "422"),
    "yuy2": ("YUY2（4:2:2 打包：Y0 U Y1 V）", "422"),
    "uyvy": ("UYVY（4:2:2 打包：U Y0 V Y1）", "422"),
}


def _crop_even(ch, by, bx):
    h, w = ch.shape
    return ch[:(h // by) * by, :(w // bx) * bx]


def _subsample_chroma(cr, cb, mode):
    if mode == "420":
        h, w = cr.shape
        h2, w2 = (h // 2) * 2, (w // 2) * 2
        cr_s = cr[:h2, :w2].reshape(h2 // 2, 2, w2 // 2, 2).mean(axis=(1, 3))
        cb_s = cb[:h2, :w2].reshape(h2 // 2, 2, w2 // 2, 2).mean(axis=(1, 3))
        return cr_s, cb_s
    if mode == "422":
        h, w = cr.shape
        w2 = (w // 2) * 2
        cr_s = cr[:, :w2].reshape(h, w2 // 2, 2).mean(axis=2)
        cb_s = cb[:, :w2].reshape(h, w2 // 2, 2).mean(axis=2)
        return cr_s, cb_s
    return cr, cb


def _pack_plane(arr, bitdepth):
    if bitdepth == 8:
        return to_uint8(arr).tobytes()
    v = np.clip(np.rint(arr), 0, 255).astype(np.uint16) * 4   # 8→10bit
    return v.astype("<u2").tobytes()


def pack_yuv(y, cr, cb, fmt="i420", bitdepth=8):
    """按格式打包 planar/packed YUV 字节流。返回 bytes；不支持的组合抛 ValueError。"""
    if fmt not in FORMATS:
        raise ValueError(f"未知 YUV 格式：{fmt}")
    mode = FORMATS[fmt][1]
    if bitdepth not in (8, 10):
        raise ValueError("位深只支持 8 或 10")
    if bitdepth == 10 and fmt in ("yuy2", "uyvy"):
        raise ValueError("打包格式 YUY2/UYVY 仅支持 8-bit")

    cr_s, cb_s = _subsample_chroma(cr, cb, mode)
    if mode == "444":
        y_p = to_uint8(y)
    else:
        y_p = _crop_even(to_uint8(y), *((2, 2) if mode == "420" else (1, 2)))

    if fmt in ("yuv444p_yv",):
        planes = [y_p, to_uint8(cr), to_uint8(cb)]
        return b"".join(_pack_plane(p, bitdepth) for p in planes)
    if fmt in ("yuv444p_yuv",):
        planes = [y_p, to_uint8(cb), to_uint8(cr)]
        return b"".join(_pack_plane(p, bitdepth) for p in planes)
    if fmt == "i420":
        return (_pack_plane(y_p, bitdepth) + _pack_plane(cb_s, bitdepth)
                + _pack_plane(cr_s, bitdepth))
    if fmt == "yv12":
        return (_pack_plane(y_p, bitdepth) + _pack_plane(cr_s, bitdepth)
                + _pack_plane(cb_s, bitdepth))
    if fmt in ("nv12", "nv21"):
        y_bytes = _pack_plane(y_p, bitdepth)
        inter = np.stack([cb_s, cr_s], axis=-1) if fmt == "nv12" else np.stack([cr_s, cb_s], axis=-1)
        if bitdepth == 8:
            uv = to_uint8(inter.reshape(-1).reshape(inter.shape[0], -1)).tobytes()
        else:
            uv = (np.clip(np.rint(inter), 0, 255).astype(np.uint16) * 4).astype("<u2").tobytes()
        return y_bytes + uv
    if fmt in ("yuv422p_yv", "yuv422p_yuv"):
        second, third = (cr_s, cb_s) if fmt == "yuv422p_yv" else (cb_s, cr_s)
        return (_pack_plane(y_p, bitdepth) + _pack_plane(second, bitdepth)
                + _pack_plane(third, bitdepth))
    if fmt in ("yuy2", "uyvy"):
        h, w2 = y_p.shape
        u = to_uint8(cb_s)
        v = to_uint8(cr_s)
        y0 = y_p[:, 0::2]
        y1 = y_p[:, 1::2]
        if fmt == "yuy2":
            packed = np.stack([y0, u, y1, v], axis=-1)
        else:
            packed = np.stack([u, y0, v, y1], axis=-1)
        return packed.astype(np.uint8).tobytes()
    raise ValueError(fmt)


def yuv_size(w, h, fmt="i420", bitdepth=8):
    """计算 YUV 输出字节数（与 pack_yuv 结果一致）。"""
    mode = FORMATS[fmt][1]
    if mode == "420":
        h2, w2 = (h // 2) * 2, (w // 2) * 2
        n = w2 * h2 + 2 * (w2 // 2) * (h2 // 2)
    elif mode == "422":
        w2 = (w // 2) * 2
        n = w2 * h + 2 * (w2 // 2) * h
    elif fmt in ("yuy2", "uyvy"):
        n = 0
    else:
        n = w * h * 3
    if fmt in ("yuy2", "uyvy"):
        w2 = (w // 2) * 2
        return w2 * h * 2
    return n * (2 if bitdepth == 10 else 1)


# 兼容旧接口
def make_yuv_bytes(y, cr, cb, subsampling="444", order="ycr_cb"):
    if subsampling == "420":
        cr_s, cb_s = _subsample_chroma(cr, cb, "420")
        y8 = _crop_even(to_uint8(y), 2, 2)
        second, third = (cr_s, cb_s) if order == "ycr_cb" else (cb_s, cr_s)
        return (y8.tobytes() + to_uint8(second).tobytes() + to_uint8(third).tobytes())
    second, third = (cr, cb) if order == "ycr_cb" else (cb, cr)
    return to_uint8(y).tobytes() + to_uint8(second).tobytes() + to_uint8(third).tobytes()
