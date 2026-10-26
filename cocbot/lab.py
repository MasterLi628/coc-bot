"""实验室研究：找到实验室 → 点"研究" → 在彩色卡片（能研究的兵种）里挑最便宜的 → 确认。

规则：先研究军队里的主力兵（按现在成绩最好的配兵里的占比），同一档里先研究便宜的。
安全：选中实验室后的"提速"、"研究药水获取"是道具，永远不点；
确认窗口里灰色的"确认 战斗之锤"也是道具，只点绿色的、价格是白字的"确认"。
"""
import time

import cv2

from .adbtool import screenshot, swipe, tap
from .config import TEMPLATES, imread, save_debug
from .scout import read_number
from .upgrade import DESELECT, Danger, _open_menu, _pages, _safe_shot, check_confirm, close_menu, close_windows

BTN_SCALES = (0.8, 0.85, 0.9, 0.95, 1.0, 1.05)
# 研究窗口里兵种卡片的中心（2 行 x 6 列）和半宽高
CARD_X = (336, 525, 715, 904, 1094, 1283)
CARD_Y = (532, 722)
CARD_HALF = (70, 70)
# 卡片是竖着排的：第一列野蛮人、弓箭手，第二列巨人、哥布林……
LAB_TROOPS = ["barb", "archer", "giant", "goblin", "breaker", "balloon", "wizard", "healer", "dragon", "pekka",
              "babydragon", "miner"]
TROOP_NAMES = {"barb": "野蛮人", "archer": "弓箭手", "giant": "巨人", "goblin": "哥布林", "breaker": "炸弹人",
               "balloon": "气球", "wizard": "法师", "healer": "天使", "dragon": "飞龙", "pekka": "皮卡超人",
               "babydragon": "飞龙宝宝", "miner": "掘地矿工"}


def _troop_at(x, y, page=0):
    """第 1 页的卡片是哪个兵种；后面几页（新兵种、法术）认不出，返回 None。"""
    if page:
        return None
    col = min(range(len(CARD_X)), key=lambda i: abs(CARD_X[i] - x))
    i = col * 2 + CARD_Y.index(y)
    return LAB_TROOPS[i] if i < len(LAB_TROOPS) else None


def _research_order():
    """研究优先级：现在成绩最好的配兵里占比大的兵种先研究，野蛮人其次，别的兵种最后。"""
    from .stats import PLANS, plan_scores
    scores = {k: v for k, v in plan_scores().items() if v[1]}
    plan = max(scores, key=lambda k: scores[k][0]) if scores else "均衡"
    order = [k for k, _ in sorted(PLANS[plan], key=lambda t: -t[1])]
    return order + [k for k in LAB_TROOPS if k not in order]


def _lab_candidates(img, find_all_scaled):
    """按 templates/lab_*.png（每个等级一张）找实验室。"""
    pts = []
    for f in sorted(TEMPLATES.glob("lab_*.png")):
        if f.stem == "lab_title":
            continue
        for x, y in find_all_scaled(img[130:720, 120:1460], imread(f), 0.7, (0.45, 0.5, 0.55, 0.6, 0.7, 0.8, 0.9, 1.0, 1.15, 1.3)):
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


GRID_X = (245, 1355)  # 研究窗口卡片区的左右边界
PAGES = 6             # 最多往右翻几页（第 1 页是老兵种，后面是新兵种、法术、暗黑兵种）


def _colored_cards(img):
    """彩色（能研究）的卡片中心。灰色的是没解锁或者实验室等级不够。
    翻页后卡片不一定停在固定位置，所以按列找连成一片的彩色区域；被窗口边缘切掉一截的卡不要（价格读不全）。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    out = []
    for y in CARD_Y:
        band = hsv[y - CARD_HALF[1]:y + CARD_HALF[1], GRID_X[0]:GRID_X[1]]
        on = ((band[..., 1] > 90) & (band[..., 2] > 90)).mean(axis=0) > 0.15
        x = 0
        while x < len(on):
            if not on[x]:
                x += 1
                continue
            start = x
            while x < len(on) and on[x:x + 20].any():  # 卡片中间偶尔有几列颜色淡，隔得近就算同一张
                x += 1
            if x - start >= 140 and start > 3 and x < len(on) - 3:
                out.append((GRID_X[0] + (start + x) // 2, y))
    return out


def _card_price(img, x, y):
    """卡片底部的价格；红字（圣水不够）读不出来，返回 None。"""
    return read_number(img[y + 42:y + 78, x - 85:x + 50])


def _lab_by_tag(img, find_all_scaled):
    """实验室空闲时头顶有个虚线框的"研究"字样，实验室就在它正下方。"""
    tag = imread(TEMPLATES / "lab_idle_tag.png")
    scales = [s / 100 for s in range(60, 141, 5)]  # 字跟着地图缩放，尺寸差一点就认不出，所以挡位分得细
    # 只在地图上找：选中实验室时底部按钮上也有"研究"两个字
    return [(x, y + 55) for x, y in find_all_scaled(img[:780], tag, 0.75, scales) if y < 720]


def _lab_from_menu(find_all, tpl):
    """画面里认不出实验室时，在工人菜单里点"实验室"那一行，镜头会对准并选中它。选中了返回截图，否则 None。"""
    mx = _open_menu(find_all, tpl)
    if mx is None:
        return None
    for _, rows in _pages(find_all, tpl, mx):
        for r in rows:
            if r["key"] == "lab":
                tap(mx + 75, r["y"])
                time.sleep(2)
                close_menu(find_all, tpl)  # 菜单会挡住底部"实验室"几个字
                img = _safe_shot(find_all, tpl)
                if find_all(img, tpl["label_lab"], 0.8):
                    return img
                tap(*DESELECT)
                return None
    tap(*DESELECT)
    return None


def try_research(find_all, find_all_scaled, tpl, log):
    """返回 "done"（开始研究了）/ "none"（没东西可研究）/ "poor"（圣水不够）/ "busy"（正在研究）/ "fail"。"""
    try:
        return _try_research(find_all, find_all_scaled, tpl, log)
    except Danger:
        log("实验室：出现可能花宝石的窗口，放弃")
        close_windows(find_all, find_all_scaled, tpl, log)
        return "fail"


def _try_research(find_all, find_all_scaled, tpl, log):
    tap(*DESELECT)  # 先取消选中别的建筑，底部按钮栏收起来
    time.sleep(0.8)
    img = _safe_shot(find_all, tpl)
    for x, y in (_lab_by_tag(img, find_all_scaled) + _lab_candidates(img, find_all_scaled))[:3]:
        tap(x, y + 10)
        time.sleep(1.5)
        img = _safe_shot(find_all, tpl)
        if find_all(img, tpl["label_lab"], 0.8):
            break
        tap(*DESELECT)
        time.sleep(0.8)
    else:
        img = _lab_from_menu(find_all, tpl)
        if img is None:
            log("实验室：没找到实验室（截图存在 debug\\没找到实验室.png）")
            save_debug(_safe_shot(find_all, tpl), "没找到实验室")
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

    # 一页一页往右翻，记下每页彩色卡片和价格；翻不动（画面不变）就是到底了
    found, priced, page = 0, [], 0
    while True:
        cards = _colored_cards(img)
        found += len(cards)
        priced += [(p, x, y, page) for x, y in cards if (p := _card_price(img, x, y))]
        if page + 1 >= PAGES:
            break
        swipe(1200, 620, 300, 620, 800)
        time.sleep(1.5)
        new = _safe_shot(find_all, tpl)
        if not find_all(new, tpl["lab_title"], 0.8):  # 窗口没了（不应该发生），别再在外面乱点
            log("实验室：翻页时研究窗口不见了（截图存在 debug\\实验室_翻页.png）")
            save_debug(new, "实验室_翻页")
            close_windows(find_all, find_all_scaled, tpl, log)
            return "fail"
        grid = lambda im: cv2.cvtColor(im[450:810, GRID_X[0]:GRID_X[1]], cv2.COLOR_BGR2GRAY)
        if cv2.absdiff(grid(img), grid(new)).mean() < 3:
            break
        img, page = new, page + 1
    if not found:
        log("实验室：现在没有能研究的兵种")
        close_windows(find_all, find_all_scaled, tpl, log)
        return "none"
    if not priced:
        log(f"实验室：有 {found} 个能研究，但圣水不够")
        close_windows(find_all, find_all_scaled, tpl, log)
        return "poor"
    order = _research_order()
    rank = lambda t: order.index(t) if t in order else len(order)
    # 主力兵优先，同档便宜的先
    price, x, y, pick = min(priced, key=lambda c: (rank(_troop_at(c[1], c[2], c[3])), c[0]))
    troop = TROOP_NAMES.get(_troop_at(x, y, pick), "兵种/法术")
    for _ in range(page - pick):  # 翻回选中的那一页
        swipe(300, 620, 1200, 620, 800)
        time.sleep(1.5)
    if page != pick:
        img = _safe_shot(find_all, tpl)
        again = [(x2, y2) for x2, y2 in _colored_cards(img) if y2 == y and _card_price(img, x2, y2) == price]
        if not again:
            log("实验室：翻回去没找到要研究的卡片，下次再试")
            close_windows(find_all, find_all_scaled, tpl, log)
            return "fail"
        x, y = min(again, key=lambda p: abs(p[0] - x))
    tap(x, y - 20)
    time.sleep(1.5)
    img = _safe_shot(find_all, tpl)
    cx, cy = check_confirm(find_all, tpl, log, img, "研究")
    tap(cx, cy + 30)
    time.sleep(2)
    img = _safe_shot(find_all, tpl)
    if find_all(img, tpl["confirm_label"], 0.8):
        log("实验室：点了确认窗口还在（截图存在 debug\\研究_确认后窗口还在.png）")
        save_debug(img, "研究_确认后窗口还在")
        close_windows(find_all, find_all_scaled, tpl, log)
        return "fail"
    log(f"实验室：已开始研究{troop}（{price} 圣水）")
    close_windows(find_all, find_all_scaled, tpl, log)
    return "done"
