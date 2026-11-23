# -*- coding: utf-8 -*-
"""
生成应用图标 app.ico：色相环 + 中心 "Y" 字母（代表 YCrCb）。

运行：python make_icon.py
输出：app.ico（16/24/32/48/64/128/256 多尺寸）、icon_preview.png
"""

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

S = 1024            # 超采样画布
R_OUT = 470         # 色相环外半径
R_IN = 230          # 中心圆盘半径
RING_W = 14         # 分隔白环宽度
BG = (21, 24, 29, 255)      # 中心圆盘颜色
WHITE = (255, 255, 255, 255)

OUT_ICO = "app.ico"
OUT_PNG = "icon_preview.png"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def hsv_to_rgb(h, s, v):
    """向量化 HSV→RGB，h: [0,360)，s,v: [0,1]。返回 (...,3) float。"""
    h6 = (h / 60.0) % 6.0
    i = np.floor(h6).astype(np.int32)
    f = h6 - i
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))
    i = i % 6
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=-1)


def smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def draw_hue_wheel():
    c = S / 2.0
    yy, xx = np.mgrid[0:S, 0:S].astype(np.float64)
    xx -= c - 0.5
    yy -= c - 0.5
    r = np.hypot(xx, yy)
    hue = (np.degrees(np.arctan2(yy, xx)) + 360.0) % 360.0

    # 饱和度随半径增大，外缘轻微压暗，产生球面立体感
    sat = np.clip(r / R_OUT, 0.0, 1.0) ** 0.92
    val = 1.0 - 0.10 * np.clip(r / R_OUT, 0.0, 1.0) ** 2.5
    rgb = hsv_to_rgb(hue, sat, val)

    alpha = 1.0 - smoothstep(R_OUT - 1.5, R_OUT + 1.0, r)   # 外缘抗锯齿
    rgba = np.zeros((S, S, 4), np.float64)
    rgba[:, :, :3] = rgb
    rgba[:, :, 3] = alpha

    # 白色分隔环
    ring = (1.0 - np.clip(np.abs(r - (R_IN + RING_W / 2 + 4)) - RING_W / 2 + 1, 0, 1))
    ring = np.clip(ring, 0, 1)
    for ch in range(4):
        v = WHITE[ch] / 255.0
        rgba[:, :, ch] = rgba[:, :, ch] * (1 - ring) + v * ring

    # 中心圆盘（含抗锯齿）
    disc = 1.0 - smoothstep(R_IN - 1.0, R_IN + 1.5, r)
    for ch in range(4):
        v = BG[ch] / 255.0
        rgba[:, :, ch] = rgba[:, :, ch] * (1 - disc) + v * disc

    img = Image.fromarray((np.clip(rgba, 0, 1) * 255).astype(np.uint8), "RGBA")
    return img


def load_font(size):
    for path in (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\segoeuib.ttf",
                 r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main():
    img = draw_hue_wheel()

    # 中心白色 "Y"（按字形实际包围盒居中，避免字体边距偏位）
    draw = ImageDraw.Draw(img)
    font = load_font(int(R_IN * 1.18))
    bbox = draw.textbbox((0, 0), "Y", font=font)
    gx = S / 2 - (bbox[0] + bbox[2]) / 2
    gy = S / 2 - (bbox[1] + bbox[3]) / 2 - R_IN * 0.03
    draw.text((gx, gy), "Y", font=font, fill=WHITE)

    # 缩放到各尺寸并保存多尺寸 ICO
    frames = [img.resize((sz, sz), Image.Resampling.LANCZOS) for sz in SIZES]
    frames[-1].save(OUT_ICO, format="ICO",
                    sizes=[(sz, sz) for sz in SIZES],
                    append_images=frames[:-1])
    frames[-1].save(OUT_PNG)
    print(f"已生成 {OUT_ICO}（{', '.join(str(s) for s in SIZES)} px）与 {OUT_PNG}")


if __name__ == "__main__":
    main()
