"""在阵型编辑器里按设计摆建筑（编辑器已打开、已缩到最小、已"移除全部"、右侧面板已收起）。

格子坐标 (i, j)：i 往右下、j 往左下，(0, 0) 是校准时第一段城墙所在的格子（地图中心附近）。
"""
import subprocess
import time
from pathlib import Path

import cv2

from adbtool import ADB, device, screenshot, tap

O = (770, 271.6)       # 格子 (0,0) 的中心（整体上移 4 格后）
U = (15.4, 11.8)        # i+1
V = (-15.4, 11.8)       # j+1
DROP = (10, 18)         # 从仓库拖出来时，手指要比格子中心偏这么多
CARDS = Path(__file__).parent / "templates" / "cards"
_card = {p.stem: cv2.imread(str(p)) for p in CARDS.glob("*.png")}
EMPTY = (230, 140)      # 地图左上角草地，点一下取消选中


def tile(i, j):
    return O[0] + U[0] * i + V[0] * j, O[1] + U[1] * i + V[1] * j


def _swipe(x1, y1, x2, y2, ms):
    subprocess.run([ADB, "-s", device(), "shell", "input", "swipe", *map(str, map(int, (x1, y1, x2, y2, ms)))])


def find_card(names):
    """在仓库栏里找卡片（先滑到最左，再一页页往右找）。"""
    for _ in range(3):
        _swipe(250, 800, 1450, 800, 300)
        time.sleep(0.4)
    for page in range(4):
        img = screenshot()[720:890]
        for name in names:
            r = cv2.matchTemplate(img, _card[name], cv2.TM_CCOEFF_NORMED)
            _, mv, _, ml = cv2.minMaxLoc(r)
            if mv > 0.85:
                return ml[0] + 50, ml[1] + 720 + 42
        _swipe(1400, 800, 500, 800, 600)
        time.sleep(1.0)
    return None


def place(names, i, j):
    tap(*EMPTY)
    time.sleep(0.3)
    c = find_card(names if isinstance(names, list) else [names])
    if not c:
        print("找不到卡片", names)
        return False
    x, y = tile(i, j)
    _swipe(c[0], c[1], x + DROP[0], y + DROP[1], 900)
    time.sleep(0.6)
    return True


def place_walls(cells):
    """城墙卡片在最左边，连续拖不用每次找。"""
    tap(*EMPTY)
    c = find_card(["wall"])
    for i, j in cells:
        x, y = tile(i, j)
        _swipe(c[0], c[1], x + DROP[0], y + DROP[1], 700)
        time.sleep(0.35)
        tap(*EMPTY)
        time.sleep(0.25)


# ---------- 设计 ----------
LO, HI = -5, 7                      # 城墙方框
RING = sorted({(k, LO) for k in range(LO, HI + 1)} | {(k, HI) for k in range(LO, HI + 1)}
              | {(LO, k) for k in range(LO, HI + 1)} | {(HI, k) for k in range(LO, HI + 1)})

CORE = [                             # 城墙里：大本营 + 8 个 3x3
    ("th", 1.5, 1.5),
    (["esto"], -3, -3), (["cannon2", "cannon"], 1, -3), (["gsto"], 5, -3),
    (["archer"], -3, 1),                                 (["mortar"], 5, 1),
    (["gsto1"], -3, 5), (["cannon", "cannon2"], 1, 5),   (["esto1"], 5, 5),
]

OUTER = [                            # 城墙外一圈
    (["camp"], -7.5, -9.5), (["mine"], -2, -9), (["ecol", "ecol2"], 4, -9), (["mine"], 9, -9),
    (["ecol2", "ecol"], -9, -2), (["camp"], -9.5, 4.5),
    (["barracks"], 11, -2), (["lab"], 11, 4),
    (["mine"], -7, 14), (["ecol2", "ecol"], -2, 14), (["hut"], 9.5, 14.5), (["hut"], 13.5, 9.5),
]

EXTRA_WALLS = [(-6, 1), (8, 1)]
BOMBS = [(1, -7), (1, 9)]


if __name__ == "__main__":
    import sys
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step in ("core", "all"):
        for n, i, j in CORE:
            place(n, i, j)
    if step in ("walls", "all"):
        place_walls(RING + EXTRA_WALLS)
    if step in ("outer", "all"):
        for n, i, j in OUTER:
            place(n, i, j)
        for i, j in BOMBS:
            place(["bomb"], i, j)
