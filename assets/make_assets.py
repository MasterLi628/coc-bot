"""生成控制面板和软件用的图片（改了这里要重新运行：python assets/make_assets.py）。

- icon.png / icon.ico：软件图标（游戏风格：紫底、金边红盾、交叉双剑、金币）
- game/*.png：总览页战果卡片上的游戏风小图（金币、圣水、双剑、星星）
- ui/*.png：界面上的线条小图标（白色，用的时候再上色），从 Windows 11 自带的 Segoe Fluent Icons 字体渲染
"""
import math
from PIL import Image, ImageDraw, ImageFilter, ImageChops, ImageFont
S = 1024
from pathlib import Path

OUT = Path(__file__).parent


def mask(fn):
    m = Image.new("L", (S, S), 0)
    fn(ImageDraw.Draw(m))
    return m


def dilate(m, r):
    return m.filter(ImageFilter.GaussianBlur(r / 2)).point(lambda v: 255 if v > 18 else 0).filter(ImageFilter.GaussianBlur(1.5))


def erode(m, r):
    return ImageChops.invert(dilate(ImageChops.invert(m), r))


def gradient(c1, c2, y0=0, y1=S):
    g = Image.new("RGBA", (S, S))
    d = ImageDraw.Draw(g)
    for y in range(S):
        t = min(1, max(0, (y - y0) / max(1, y1 - y0)))
        d.line([(0, y), (S, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)) + (255,))
    return g


def radial(c_in, c_out, cx, cy, r):
    g = Image.new("RGBA", (S, S), c_out + (255,))
    d = ImageDraw.Draw(g)
    for i in range(r, 0, -4):
        t = i / r
        d.ellipse((cx - i, cy - i, cx + i, cy + i), fill=tuple(int(a + (b - a) * t) for a, b in zip(c_in, c_out)) + (255,))
    return g


def paint(base, m, fill):
    layer = fill if isinstance(fill, Image.Image) else Image.new("RGBA", (S, S), fill + (255,))
    out = base.copy()
    out.paste(layer, (0, 0), m)
    return out


def obj(m, c1, c2, outline=(40, 22, 8), ow=26, gloss=True, bevel=True):
    """一个游戏风物件：外描边 → 渐变本体 → 下沿暗边 → 上半高光。"""
    bb = m.getbbox()
    L = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    if ow > 1:
        L = paint(L, dilate(m, ow), outline)
    L = paint(L, m, gradient(c1, c2, bb[1], bb[3]))
    if bevel:
        inner = erode(m, 22)
        rim = ImageChops.subtract(m, ImageChops.offset(inner, 0, -10))
        L = paint(L, rim.point(lambda v: int(v * 0.45)), (0, 0, 0))
    if gloss:
        top = Image.new("L", (S, S), 0)
        h = bb[3] - bb[1]
        ImageDraw.Draw(top).ellipse((bb[0] - 200, bb[1] - h * 0.9, bb[2] + 200, bb[1] + h * 0.42), fill=255)
        g = ImageChops.multiply(erode(m, 30), top).point(lambda v: int(v * 0.30))
        L = paint(L, g, (255, 255, 255))
    return L


def shadow(layer, off=(0, 22), blur=22, a=120):
    al = layer.split()[3].point(lambda v: v * a // 255)
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sh.putalpha(al)
    sh = ImageChops.offset(sh.filter(ImageFilter.GaussianBlur(blur)), *off)
    return Image.alpha_composite(sh, layer)


def tile(c_in, c_out):
    m = mask(lambda d: d.rounded_rectangle((32, 32, S - 32, S - 32), 220, fill=255))
    t = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    t = paint(t, m, radial(c_in, c_out, 512, 360, 800))
    rim = ImageChops.subtract(m, erode(m, 26))
    t = paint(t, rim.point(lambda v: int(v * 0.35)), (255, 255, 255))
    return t, m


def star_pts(cx, cy, R, r, rot=-90):
    return [(cx + (R if i % 2 == 0 else r) * math.cos(math.radians(rot + i * 36)),
             cy + (R if i % 2 == 0 else r) * math.sin(math.radians(rot + i * 36))) for i in range(10)]


def sword(x1, y1, x2, y2, w=40):
    ang = math.atan2(y2 - y1, x2 - x1)
    nx, ny = -math.sin(ang), math.cos(ang)
    P = lambda t, o: (x1 + (x2 - x1) * t + nx * o, y1 + (y2 - y1) * t + ny * o)
    blade = mask(lambda d: d.polygon([P(0, 0), P(.13, w), P(.68, w), P(.68, -w), P(.13, -w)], fill=255))
    guard = mask(lambda d: d.polygon([P(.66, w * 2.9), P(.74, w * 2.9), P(.74, -w * 2.9), P(.66, -w * 2.9)], fill=255))
    hilt = mask(lambda d: d.polygon([P(.74, w * .75), P(.93, w * .75), P(.93, -w * .75), P(.74, -w * .75)], fill=255))
    cx, cy = P(.97, 0)
    pommel = mask(lambda d: d.ellipse((cx - w * 1.35, cy - w * 1.35, cx + w * 1.35, cy + w * 1.35), fill=255))
    L = obj(blade, (250, 252, 255), (150, 165, 190), ow=22, bevel=False)
    L = Image.alpha_composite(L, obj(hilt, (150, 90, 40), (90, 48, 18), ow=22, gloss=False, bevel=False))
    L = Image.alpha_composite(L, obj(guard, (255, 214, 80), (214, 130, 10), ow=22, gloss=False, bevel=False))
    L = Image.alpha_composite(L, obj(pommel, (255, 214, 80), (214, 130, 10), ow=22, bevel=False))
    return L


def coin(cx, cy, r, face="star"):
    side = mask(lambda d: d.ellipse((cx - r, cy - r + r * .16, cx + r, cy + r + r * .16), fill=255))
    top = mask(lambda d: d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255))
    L = obj(ImageChops.lighter(side, top), (200, 120, 10), (150, 80, 0), gloss=False, bevel=False)
    L = Image.alpha_composite(L, obj(top, (255, 226, 92), (236, 158, 18), ow=1, bevel=False, gloss=False))
    inner = mask(lambda d: d.ellipse((cx - r * .76, cy - r * .76, cx + r * .76, cy + r * .76), fill=255))
    L = paint(L, dilate(inner, 10), (196, 120, 8))
    L = paint(L, inner, gradient((236, 160, 20), (255, 210, 70), int(cy - r), int(cy + r)))
    if face == "star":
        st = mask(lambda d: d.polygon(star_pts(cx, cy + r * .02, r * .5, r * .22), fill=255))
        L = paint(L, ImageChops.offset(st, 0, 8), (170, 96, 0))
        L = paint(L, st, (255, 240, 170))
    gl = Image.new("L", (S, S), 0)
    ImageDraw.Draw(gl).ellipse((cx - r * 1.2, cy - r * 1.9, cx + r * 1.2, cy - r * .05), fill=255)
    L = paint(L, ImageChops.multiply(erode(top, 20), gl).point(lambda v: int(v * .32)), (255, 255, 255))
    return L


def sparkle(L, cx, cy, s):
    m = mask(lambda d: d.polygon([(cx, cy - s), (cx + s * .22, cy - s * .22), (cx + s, cy), (cx + s * .22, cy + s * .22),
                                  (cx, cy + s), (cx - s * .22, cy + s * .22), (cx - s, cy), (cx - s * .22, cy - s * .22)],
                                 fill=255))
    return paint(L, m, (255, 255, 255))


def shield_mask(cx, top, w, h):
    x0, x1 = cx - w / 2, cx + w / 2

    def f(d):
        d.polygon([(x0, top + h * .1), (cx, top), (x1, top + h * .1), (x1, top + h * .5), (cx, top + h),
                   (x0, top + h * .5)], fill=255)
        d.chord((x0, top + h * .05, x1, top + h), 0, 180, fill=255)
    return mask(f)


def finish(img, tmask, name):
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), tmask)
    return out


def blank():
    return Image.new("RGBA", (S, S), (0, 0, 0, 0))




def app_icon():
    t, tm = tile((186, 110, 255), (58, 20, 128))
    L = blank()
    L = Image.alpha_composite(L, sword(200, 180, 760, 860, 34))
    L = Image.alpha_composite(L, sword(824, 180, 264, 860, 34))
    sm = shield_mask(512, 170, 560, 700)
    L = Image.alpha_composite(L, shadow(obj(sm, (255, 220, 90), (220, 130, 10)), blur=14))
    L = Image.alpha_composite(L, obj(erode(sm, 70), (236, 70, 70), (150, 20, 30), ow=8))
    L = Image.alpha_composite(L, shadow(coin(512, 480, 165), blur=10))
    L = sparkle(L, 830, 250, 50)
    return finish(Image.alpha_composite(t, shadow(L)), tm, "icon")


def elixir():
    """圣水瓶：圆肚细颈的紫色药水瓶。"""
    body = mask(lambda d: d.ellipse((232, 330, 792, 890), fill=255))
    neck = mask(lambda d: d.rounded_rectangle((420, 150, 604, 420), 30, fill=255))
    L = obj(ImageChops.lighter(body, neck), (236, 140, 255), (140, 30, 190))
    cork = mask(lambda d: d.rounded_rectangle((390, 110, 634, 200), 30, fill=255))
    L = Image.alpha_composite(L, obj(cork, (200, 140, 90), (130, 80, 40), ow=22, bevel=False))
    return L


def crossed_swords():
    L = Image.alpha_composite(sword(170, 150, 860, 860, 46), sword(854, 150, 164, 860, 46))
    return L


def star_icon():
    st = mask(lambda d: d.polygon(star_pts(512, 540, 420, 190), fill=255))
    return obj(st, (255, 236, 120), (240, 150, 10))


def save_small(img, path, size):
    img = shadow(img, off=(0, 14), blur=14, a=90)
    bb = img.getbbox()
    img = img.crop(bb)
    w, h = img.size
    side = max(w, h)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(img, ((side - w) // 2, (side - h) // 2))
    sq.resize((size, size), Image.LANCZOS).save(path)


UI_GLYPHS = {  # 名字 → Segoe Fluent Icons 字形编号
    "overview": 0xF0E2, "log": 0xE8FD, "settings": 0xE713, "play": 0xE768, "stop": 0xE71A, "search": 0xE721,
    "up": 0xE74A, "down": 0xE74B, "reset": 0xE72C, "folder": 0xE838, "auto": 0xE99A, "detect": 0xE895,
    "clear": 0xE74D, "save": 0xE74E, "wrench": 0xE90F, "bulb": 0xEA80, "warn": 0xE7BA, "error": 0xEA39,
    "done": 0xE930, "clock": 0xE823, "flag": 0xE7C1, "game": 0xE7FC, "sun": 0xE706, "moon": 0xE708,
}


def ui_icons():
    font = ImageFont.truetype(r"C:/Windows/Fonts/SegoeIcons.ttf", 192)
    (OUT / "ui").mkdir(exist_ok=True)
    for name, code in UI_GLYPHS.items():
        img = Image.new("L", (256, 256), 0)
        ImageDraw.Draw(img).text((128, 128), chr(code), font=font, fill=255, anchor="mm")
        rgba = Image.new("RGBA", (256, 256), (255, 255, 255, 0))
        rgba.putalpha(img)
        rgba.resize((64, 64), Image.LANCZOS).save(OUT / "ui" / f"{name}.png")


if __name__ == "__main__":
    icon = app_icon()
    icon.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png")
    icon.resize((256, 256), Image.LANCZOS).save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    (OUT / "game").mkdir(exist_ok=True)
    save_small(coin(512, 480, 360), OUT / "game" / "gold.png", 96)
    save_small(elixir(), OUT / "game" / "elixir.png", 96)
    save_small(crossed_swords(), OUT / "game" / "battle.png", 96)
    save_small(star_icon(), OUT / "game" / "star.png", 96)
    ui_icons()
    print("已生成", OUT)
