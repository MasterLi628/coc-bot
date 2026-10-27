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
    # 字很小，背后的地面颜色一变（换阵型后挨着红色范围圈）分数就掉到 0.65~0.72；"训练"等别处都不到 0.55
    return [(x, y + 55) for x, y in find_all_scaled(img[:780], tag, 0.6, scales) if y < 720]


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


def _select_lab(find_all, find_all_scaled, tpl, log):
    """选中实验室，返回选中后的截图；找不到返回 None。"""
    tap(*DESELECT)  # 先取消选中别的建筑，底部按钮栏收起来
    time.sleep(0.8)
    img = _safe_shot(find_all, tpl)
    for x, y in (_lab_by_tag(img, find_all_scaled) + _lab_candidates(img, find_all_scaled))[:3]:
        tap(x, y + 10)
        time.sleep(1.5)
        img = _safe_shot(find_all, tpl)
        if find_all(img, tpl["label_lab"], 0.8):
            return img
        tap(*DESELECT)
        time.sleep(0.8)
    img = _lab_from_menu(find_all, tpl)
    if img is None:
        log("实验室：没找到实验室（截图存在 debug\\没找到实验室.png）")
        save_debug(_safe_shot(find_all, tpl), "没找到实验室")
    return img


def _try_research(find_all, find_all_scaled, tpl, log):
    img = _select_lab(find_all, find_all_scaled, tpl, log)
    if img is None:
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


# ---------- 研究药水 ----------
# 研究药水：1 小时内研究速度 x24，相当于省 23 小时。剩下的研究时间太短就浪费了，所以只在研究时间够长时用。
POTION_HOURS = 20
BUSY_TIME_BOX = (250, 305, 530, 780)  # 研究中窗口"总时间：4小时 22分钟"那行大字（y0, y1, x0, x1）
CLOSE_X = (1337, 52)                  # 研究窗口右上角的 X（窗口里有"宝石立即完成"按钮，只点这个 X）


def _white_mask(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return ((hsv[..., 1] < 70) & (hsv[..., 2] > 200)).astype("float32")


def _unit_hours(unit, tpl, small=False):
    """时间单位那一块（白字二值图）代表多少小时：小时 1、分钟 1/60；都不像时，窄的（单字）当"天"，宽的当"秒"。
    small：按钮上的小号字（数字高 17 左右），用另一套单位模板。"""
    best, hours = 0.5, None
    sfx = "_s" if small else ""
    for name, h in (("unit_hour" + sfx, 1), ("unit_min" + sfx, 1 / 60)):
        t = _white_mask(tpl[name])
        th, tw = t.shape
        u = cv2.copyMakeBorder(unit, th // 2, th // 2, tw // 2, tw // 2, cv2.BORDER_CONSTANT, value=0)  # 四周留白，模板能错开对齐
        v = float(cv2.matchTemplate(u, t, cv2.TM_CCOEFF_NORMED).max())
        if v > best:
            best, hours = v, h
    if hours is None:
        hours = 24 if unit.shape[1] < (30 if small else 45) else 1 / 3600
    return hours


def _read_hours(img, tpl, box=BUSY_TIME_BOX, small=False):
    """读"4小时 22分钟"这种时间，返回小时数；读不出返回 None。大的连通块是数字，数字之间的碎块是单位。
    small：按钮上的小号字（数字高 17 左右）。"""
    import numpy as np
    from .scout import SIZE, _DIGITS
    y0, y1, x0, x1 = box
    m = _white_mask(img[y0:y1, x0:x1]).astype("uint8")
    n, _, st, _ = cv2.connectedComponentsWithStats(m)
    if small:  # 小字的"1"和单位碎块一样细，靠顶部对齐分：数字顶到最上面，单位的笔画都低两三像素
        tops = [st[i, 1] for i in range(1, n) if st[i, 4] >= 100]
        big = [i for i in range(1, n) if tops and st[i, 3] >= 14 and st[i, 4] >= 60 and st[i, 1] <= min(tops) + 1]
    else:
        big = [i for i in range(1, n) if st[i, 3] >= 24 and st[i, 4] >= 250]
    digit = {}
    for i in big:
        x, y, w, h = st[i, :4]
        g = cv2.resize(m[y:y + h, x:x + w] * 255, SIZE, interpolation=cv2.INTER_AREA).astype(float)
        digit[x] = min(_DIGITS, key=lambda d: np.abs(_DIGITS[d] - g).mean())
    digits = sorted((st[i, 0], st[i, 0] + st[i, 2]) for i in big)
    if not digits:
        return None
    groups = [[digits[0]]]  # 挨着的数字是同一个数
    for d in digits[1:]:
        if d[0] - groups[-1][-1][1] < 8:
            groups[-1].append(d)
        else:
            groups.append([d])
    total = 0.0
    for i, g in enumerate(groups):
        num = int("".join(str(digit[a]) for a, _ in g))
        end = groups[i + 1][0][0] if i + 1 < len(groups) else m.shape[1]
        cols = [x for x in range(g[-1][1], end) if m[:, x].any()]
        if num is None or not cols:
            return None
        total += num * _unit_hours(m[:, cols[0]:cols[-1] + 1].astype("float32"), tpl, small)
    return total


def try_potion(find_all, find_all_scaled, tpl, log):
    """实验室在研究、有研究药水、研究时间够长，就用一瓶。返回是否用了。
    只点"提速"按钮（上面有药水图标才点）和"加速研究？"确认框里带药水图标的按钮，宝石按钮一概不碰。"""
    try:
        img = _select_lab(find_all, find_all_scaled, tpl, log)
        if img is None:
            return False
        btn = _find_btn(img, tpl["btn_research"])
        if not find_all(img, tpl["potion_btn"], 0.8) or not btn:
            tap(*DESELECT)
            return False  # 没有研究药水（"提速"按钮上没有药水图标）
        tap(*btn)
        time.sleep(1.5)
        img = screenshot()  # 研究中窗口里有宝石按钮，只读时间，然后点 X
        hours = _read_hours(img, tpl)
        tap(*CLOSE_X)
        time.sleep(1.2)
        if hours is None:
            log("实验室：读不出研究时间，先不用研究药水（截图存在 debug\研究时间.png）")
            save_debug(img, "研究时间")
        if hours is None or hours < POTION_HOURS:
            tap(*DESELECT)
            return False
        pts = find_all(screenshot(), tpl["potion_btn"], 0.8)
        if not pts:
            tap(*DESELECT)
            return False
        tap(*pts[0])
        time.sleep(1.5)
        img = screenshot()
        ok = find_all(img, tpl["potion_ok"], 0.8)
        if not (ok and find_all(img, tpl["potion_title"], 0.8)):
            log("实验室：没出现\"加速研究\"确认框，不用药水（截图存在 debug\研究药水.png）")
            save_debug(img, "研究药水")
            close_windows(find_all, find_all_scaled, tpl, log)
            return False
        tap(*ok[0])
        time.sleep(1.5)
        log(f"实验室：这项研究要约 {hours:.0f} 小时，用了一瓶研究药水（1 小时内研究速度 x24）")
        close_windows(find_all, find_all_scaled, tpl, log)
        return True
    except Danger:
        close_windows(find_all, find_all_scaled, tpl, log)
        return False
