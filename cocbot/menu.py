"""读工人菜单：每一行是什么建筑、是不是"新"建筑、用什么资源付、买不买得起。

菜单面板挂在顶部工人图标下面，左右有白色细边框；面板宽度跟着最长的那行字变，所以边框要现找。
名字那一列左边是 mx（= 左边框 + 14），价格贴着右边框（右边框往左 140 以内）。行高约 48。
"""
import cv2
import numpy as np

from .config import TEMPLATES, imread
from .scout import read_number

MENU_DX = -158            # 找不到边框时：面板左边框 ≈ 工人图标中心 x + MENU_DX
MENU_Y = (128, 688)       # 菜单面板里能看到行的 y 范围
NAME_MATCH = 0.75


def _white(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return ((hsv[..., 1] < 60) & (hsv[..., 2] > 200)).astype(np.float32)


_NAMES = {p.stem: _white(imread(p)) for p in sorted((TEMPLATES / "names").glob("*.png"))}


def _count(hsv, h0, h1, s=120, v=150):
    h = hsv[..., 0]
    return int(((h >= h0) & (h <= h1) & (hsv[..., 1] > s) & (hsv[..., 2] > v)).sum())


def _name_of(mask):
    """名字那一块（白字二值图）最像哪个模板，认不出返回 None。"""
    best, key = NAME_MATCH, None
    for k, t in _NAMES.items():
        if t.shape[0] > mask.shape[0] or t.shape[1] > mask.shape[1]:
            continue
        v = float(cv2.matchTemplate(mask, t, cv2.TM_CCOEFF_NORMED).max())
        if v > best:
            best, key = v, k
    return key


def _border_cols(img, x0, x1):
    """x0~x1 之间、从上到下几乎都是纯白的列（面板边框）。"""
    g = cv2.cvtColor(img[MENU_Y[0]:MENU_Y[1], x0:x1], cv2.COLOR_BGR2GRAY)
    return [x0 + int(i) for i in np.flatnonzero((g >= 245).mean(0) > 0.8)]


def menu_left(img, icon_x):
    """菜单名字列的左边 mx。"""
    cols = _border_cols(img, max(icon_x - 400, 0), icon_x)
    return (max(cols) if cols else icon_x + MENU_DX) + 14


def _menu_right(img, mx):
    cols = _border_cols(img, mx + 250, min(mx + 800, img.shape[1]))
    return min(cols) if cols else mx + 440


def read_rows(img, mx):
    """当前看得到的每一行：dict(y, key, new, cur, red, price)。
    key：建筑（templates/names 里的文件名），认不出是 None；
    new：绿色"新"字（要摆放的新建筑）；
    cur："gold" / "elixir" / "gem" / None（免费、升级中的倒计时等）；
    red：价格是红字（资源不够）；
    price：价格数字（红字、免费、倒计时读不出来，是 None）。"""
    y0, y1 = MENU_Y
    right = _menu_right(img, mx)
    px = right - 140  # 价格那一列的左边
    white = _white(img[y0:y1, mx + 4:px])
    lines = white.sum(1) > 2
    rows, y = [], 0
    while y < len(lines):
        if not lines[y]:
            y += 1
            continue
        top = y
        while y < len(lines) and lines[y]:
            y += 1
        if y - top < 14 or top < 3 or y > len(lines) - 3:  # 太矮不是字；贴着边的行只露出一半
            continue
        a, b = y0 + top, y0 + y
        hsv_new = cv2.cvtColor(img[a:b, mx + 2:mx + 30], cv2.COLOR_BGR2HSV)
        new = _count(hsv_new, 35, 85) > 40
        key = _name_of(_white(img[a - 4:b + 4, mx:px]))
        cost = cv2.cvtColor(img[a - 6:b + 6, px:right - 6], cv2.COLOR_BGR2HSV)
        gold, elixir, gem = _count(cost, 18, 32, 150, 180), _count(cost, 138, 165, 100, 150), _count(cost, 36, 80)
        red = _count(cost, 0, 8, 120, 150) + _count(cost, 172, 180, 120, 150) > 30
        cur = max((gold, "gold"), (elixir, "elixir"), (gem, "gem"))
        cur = cur[1] if cur[0] > 40 else None
        price = read_number(img[a - 6:b + 6, px:right - 6]) if cur in ("gold", "elixir") and not red else None
        rows.append({"y": (a + b) // 2, "key": key, "new": new, "cur": cur, "red": red, "price": price})
    return rows
