"""读"建筑和英雄"侧栏：每一行是什么建筑、是不是"新"建筑、用什么资源付、买不买得起。

新版本游戏（2026-10 更新）没有顶部工人菜单了，改成右下角"建造"按钮划出的右侧侧栏，
面板从 x≈1163 拉到右边缘。每一行两行字：上面是建筑名字（靠左），下面是价格（贴着右下角），
价格右边还有一个资源图标。行高跟着名字长短变（有的行多一行小字），所以按名字的白字行找，
价格和资源图标都相对名字那一行定位。
"""
import cv2
import numpy as np

from .config import TEMPLATES, imread
from .scout import read_number

SIDEBAR_X = (1163, 1597)      # 侧栏面板的 x 范围
NAME_X = (1220, 1400)         # 名字那一列
NAME_Y = (60, 795)            # 面板里能找到的行（上面是标题和进度条，下面被底部按钮挡）
PRICE_X = (1390, 1568)        # 价格数字区（右对齐，实际数字从哪开始不固定）
ICON_X = (1570, 1596)         # 每行价格右边的资源图标
NEW_X = (1228, 1276)          # 名字下面那个绿色"新"字（和名字左对齐）
CLICK_X = 1280                # 点某一行的横坐标（名字左边一点，避开价格）
NAME_MATCH = 0.75


def _white(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return ((hsv[..., 1] < 60) & (hsv[..., 2] > 200)).astype(np.float32)


def _title_text(img):
    """名字行的文字：普通行是白字，"新"建筑那一行是绿字。分组标题（建议升级/其他升级）也算进来，
    它们没有价格和图标，后面自然会被当成不可升级的行。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    white = (s < 60) & (v > 200)
    green = (h >= 35) & (h <= 85) & (s > 100) & (v > 150)
    return (white | green).astype(np.float32)


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


def _cursor(icon):
    """价格右边那个资源图标 → gold / elixir / dark / gem / None。
    图标不大、边上还混着侧栏的棕色背景，所以按颜色分块数像素，不靠单个像素。
    圣水和暗黑重油都是紫色，靠亮度分：圣水亮紫（v>200），暗黑深紫（v<140）。"""
    h, s, v = icon[..., 0], icon[..., 1], icon[..., 2]
    kinds = {
        "gold": ((h >= 16) & (h <= 35) & (s > 150) & (v > 180)),
        "gem": ((h >= 36) & (h <= 80) & (s > 120) & (v > 170)),
        "elixir": ((h >= 130) & (h <= 175) & (s > 150) & (v > 200)),
        "dark": ((h >= 120) & (h <= 175) & (s > 50) & (v >= 30) & (v <= 140)),
    }
    counts = {k: int(m.sum()) for k, m in kinds.items()}
    best = max(counts, key=counts.get)
    return best if counts[best] >= 8 else None


def read_rows(img):
    """当前看得到的每一行：dict(y, key, new, cur, red, price)。
    y：名字那一行的中心（点行用 y+10，价格在它下面）；
    key：建筑（templates/names 里的文件名），认不出是 None；
    new：绿色"新"字（要摆放的新建筑）；
    cur："gold" / "elixir" / "dark" / "gem" / None（免费、升级中的倒计时等）；
    red：价格是红字（资源不够）；
    price：价格数字（红字、免费、倒计时读不出来，是 None）。"""
    x0, x1 = NAME_X
    y0, y1 = NAME_Y
    lines = _title_text(img[y0:y1, x0:x1]).sum(1) > 2
    rows, y = [], 0
    while y < len(lines):
        if not lines[y]:
            y += 1
            continue
        top = y
        while y < len(lines) and lines[y]:
            y += 1
        if y - top < 8 or top < 3 or y > len(lines) - 3:  # 太矮不是字；贴着边的行只露出一半
            continue
        a, b = y0 + top, y0 + y
        yc = (a + b) // 2
        # 绿色"新"字标签挂在名字下面一行，不在名字那一行的正中间
        hsv_new = cv2.cvtColor(img[yc + 2:yc + 42, NEW_X[0]:NEW_X[1]], cv2.COLOR_BGR2HSV)
        new = _count(hsv_new, 35, 85, 120, 150) > 120
        key = _name_of(_white(img[yc - 14:yc + 14, x0:x1]))
        cost = cv2.cvtColor(img[yc + 6:yc + 34, PRICE_X[0]:PRICE_X[1]], cv2.COLOR_BGR2HSV)
        red = _count(cost, 0, 8, 120, 150) + _count(cost, 172, 180, 120, 150) > 30
        icon = cv2.cvtColor(img[yc + 10:yc + 34, ICON_X[0]:ICON_X[1]], cv2.COLOR_BGR2HSV)
        cur = _cursor(icon) if icon.size else None
        price = read_number(img[yc + 4:yc + 36, PRICE_X[0]:PRICE_X[1]]) if cur in ("gold", "elixir", "dark") and not red else None
        # 分组标题、"新"字标签、顶上的训练营进度条、大本营下面的提示语都没有价格，不当成可点的行
        if price is None and not red and cur != "gem":
            continue
        rows.append({"y": yc, "key": key, "new": new, "cur": cur, "red": red, "price": price})
    return rows
