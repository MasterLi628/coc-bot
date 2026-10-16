"""实验室研究：找到实验室 → 点"研究" → 看有没有能研究的兵种（彩色卡片）。

还没做完的一步：研究确认窗口还没见过（写的时候所有兵种都是灰的），
所以发现能研究的兵种时，先截图存到 debug\\实验室_可研究.png 并写日志，等补上确认这一步。
安全：选中实验室后的"提速"按钮是道具（研究药水），永远不点。
"""
import time

import cv2

from adbtool import screenshot, tap
from config import TEMPLATES, imread, save_debug
from upgrade import DESELECT, Danger, _safe_shot, close_windows

BTN_SCALES = (0.8, 0.85, 0.9, 0.95, 1.0, 1.05)
# 研究窗口里兵种卡片的中心（2 行 x 6 列）和半宽高
CARD_X = (336, 525, 715, 904, 1094, 1283)
CARD_Y = (532, 722)
CARD_HALF = (70, 70)


def _lab_candidates(img, find_all_scaled):
    """按 templates/lab_*.png（每个等级一张）找实验室。"""
    pts = []
    for f in sorted(TEMPLATES.glob("lab_*.png")):
        if f.stem == "lab_title":
            continue
        for x, y in find_all_scaled(img[130:720, 120:1460], imread(f), 0.7, (0.6, 0.7, 0.8, 0.9, 1.0, 1.15, 1.3)):
            pts.append((x + 120, y + 130))
    return pts


def _find_btn(img, tpl):
    best = None
    for s in BTN_SCALES:
        t = cv2.resize(tpl, None, fx=s, fy=s)
        r = cv2.matchTemplate(img[600:], t, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(r)
        if mv >= 0.8 and (best is None or mv > best[0]):
            best = (mv, ml[0] + t.shape[1] // 2, ml[1] + 600 + t.shape[0] // 2)
    return best[1:] if best else None


def _colored_cards(img):
    """彩色（能研究）的卡片中心。灰色的是没解锁或者实验室等级不够。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    out = []
    for y in CARD_Y:
        for x in CARD_X:
            card = hsv[y - CARD_HALF[1]:y + CARD_HALF[1], x - CARD_HALF[0]:x + CARD_HALF[0]]
            colored = ((card[..., 1] > 90) & (card[..., 2] > 90)).mean()
            if colored > 0.15:
                out.append((x, y))
    return out


def try_research(find_all, find_all_scaled, tpl, log):
    """返回 "none"（没东西可研究）/ "busy"（正在研究）/ "todo"（有能研究的，确认步骤待补）/ "fail"。"""
    try:
        return _try_research(find_all, find_all_scaled, tpl, log)
    except Danger:
        log("实验室：出现可能花宝石的窗口，放弃")
        close_windows(find_all, find_all_scaled, tpl, log)
        return "fail"


def _try_research(find_all, find_all_scaled, tpl, log):
    img = _safe_shot(find_all, tpl)
    for x, y in _lab_candidates(img, find_all_scaled)[:3]:
        tap(x, y + 10)
        time.sleep(1.5)
        img = _safe_shot(find_all, tpl)
        if find_all(img, tpl["label_lab"], 0.8):
            break
        tap(*DESELECT)
        time.sleep(0.8)
    else:
        log("实验室：没找到实验室（截图存在 debug\\没找到实验室.png）")
        save_debug(img, "没找到实验室")
        return "fail"

    btn = _find_btn(img, tpl["btn_research"])
    if not btn:
        tap(*DESELECT)
        log("实验室：没有\"研究\"按钮，可能正在研究")
        return "busy"
    tap(*btn)
    time.sleep(1.5)
    img = _safe_shot(find_all, tpl)
    if not find_all(img, tpl["lab_title"], 0.8):
        log("实验室：研究窗口没打开（截图存在 debug\\实验室_窗口.png）")
        save_debug(img, "实验室_窗口")
        close_windows(find_all, find_all_scaled, tpl, log)
        return "fail"

    cards = _colored_cards(img)
    if not cards:
        log("实验室：现在没有能研究的兵种")
        close_windows(find_all, find_all_scaled, tpl, log)
        return "none"
    save_debug(img, "实验室_可研究")
    log(f"实验室：发现 {len(cards)} 个能研究的兵种，但研究确认这一步还没做（截图存在 debug\\实验室_可研究.png，请找 Claude）")
    close_windows(find_all, find_all_scaled, tpl, log)
    return "todo"
