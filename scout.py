"""看对手：读左上角"可获得的战利品"的金币/圣水数字，决定打还是换下一个。"""

import cv2
import numpy as np

from config import TEMPLATES, imread

DIGITS_DIR = TEMPLATES / "digits"
GOLD_ROW = (108, 148)       # 战利品金币那一行的 y 范围
ELIXIR_ROW = (155, 195)     # 圣水那一行
ROW_X = (78, 260)
SIZE = (14, 20)

_DIGITS = {d: imread(DIGITS_DIR / f"{d}.png", cv2.IMREAD_GRAYSCALE).astype(float) for d in range(10)}


def read_number(roi):
    """读一块区域里的白色数字（游戏里描黑边的那种字体），读不出来返回 None。
    每个数字按连通块切开、缩放到同一大小再比对，所以字大一点小一点都能读。"""
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 1] < 70) & (hsv[..., 2] > 200)).astype("uint8")
    n, _, st, _ = cv2.connectedComponentsWithStats(m)
    comps = sorted((st[i, 0], st[i, 1], st[i, 2], st[i, 3]) for i in range(1, n)
                   if 13 <= st[i, 3] <= 30 and st[i, 4] >= 25)
    if not comps:
        return None
    text = ""
    for x, y, w, h in comps:
        g = cv2.resize(m[y:y + h, x:x + w] * 255, SIZE, interpolation=cv2.INTER_AREA).astype(float)
        best = min(_DIGITS, key=lambda d: np.abs(_DIGITS[d] - g).mean())
        text += str(best)
    return int(text)


def _read_row(img, y0, y1):
    """读一行数字，读不出来返回 None。"""
    return read_number(img[y0:y1, ROW_X[0]:ROW_X[1]])


def read_loot(img):
    """返回 (金币, 圣水)，读不出的那一项是 None。"""
    return _read_row(img, *GOLD_ROW), _read_row(img, *ELIXIR_ROW)


def worth_attacking(img, need_gold, need_elixir, min_gold, min_elixir):
    """战利品够不够：没存满的资源里，任意一种达到自己的门槛就打。need_* 表示仓库没满。"""
    gold, elixir = read_loot(img)
    gold, elixir = gold or 0, elixir or 0
    ok = (need_gold and gold >= min_gold) or (need_elixir and elixir >= min_elixir)
    return ok, gold, elixir
