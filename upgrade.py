"""自动升级：打开工人菜单，挑一个买得起的现有建筑升级。

安全规则：
- 跳过绿色"新"字的行（新建筑要摆放位置）和价格是红色的行（资源不够）
- 每次点击前都检查屏幕上有没有"工人都很忙"或宝石按钮，有就关掉并放弃
- 确认按钮上的图标是绿色宝石时不点
- 最后点空地取消选中，避免误点"立即完成"
"""
import random
import time

import cv2
import numpy as np

from adbtool import screenshot, swipe, tap
from config import BUILDINGS, NEVER_UPGRADE, TEMPLATES, imread, priority, save_debug
from menu import MENU_DX, read_rows

DESELECT = (1580, 400)  # 地图右侧空地/树林，点了没副作用
ROW_H = 48
X_SCALES = (0.8, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5)


class Danger(Exception):
    """屏幕上出现了可能花宝石的窗口。"""


def _green_count(hsv):
    return int(((hsv[..., 0] >= 30) & (hsv[..., 0] <= 85) & (hsv[..., 1] > 120) & (hsv[..., 2] > 150)).sum())


def _red_count(hsv):
    h = hsv[..., 0]
    return int((((h < 10) | (h > 170)) & (hsv[..., 1] > 100) & (hsv[..., 2] > 150)).sum())


def _white_count(hsv):
    return int(((hsv[..., 1] < 40) & (hsv[..., 2] > 200)).sum())


def _gold_count(hsv):
    return int(((hsv[..., 0] >= 18) & (hsv[..., 0] <= 32) & (hsv[..., 1] > 150) & (hsv[..., 2] > 180)).sum())


def _icon_is_gem(img, cx, cy):
    """确认按钮右侧资源图标是否为绿色宝石。"""
    hsv = cv2.cvtColor(img[cy + 30:cy + 62, cx + 80:cx + 112], cv2.COLOR_BGR2HSV)
    sat = hsv[hsv[..., 1] > 100]
    if len(sat) == 0:
        return True  # 看不清就当危险处理
    hue = float(sat[:, 0].mean())
    return 40 <= hue <= 90


def danger_on_screen(find_all, tpl, img):
    return bool(find_all(img, tpl["popup_busy"], 0.8) or find_all(img, tpl["gem_icon"], 0.8)
                or find_all(img, tpl["popup_nogem"], 0.8))


def close_windows(find_all, find_all_scaled, tpl, log=None):
    """反复关闭弹窗，先关最上层的"工人都很忙"。返回是否关过窗口。"""
    closed = False
    for _ in range(5):
        img = screenshot()
        busy = find_all(img, tpl["popup_busy"], 0.8)
        xs = find_all_scaled(img, tpl["close_x"], 0.75, X_SCALES)
        if not xs:
            break
        if busy:  # 选离"工人都很忙"标题最近的 X
            bx, by = busy[0]
            x, y = min(xs, key=lambda p: abs(p[0] - bx) + abs(p[1] - by))
            if log:
                log("检测到'工人都很忙'窗口，关闭")
        else:
            x, y = xs[0]
        tap(x, y)
        closed = True
        time.sleep(1.2)
    tap(*DESELECT)
    return closed


def _safe_shot(find_all, tpl):
    img = screenshot()
    if danger_on_screen(find_all, tpl, img):
        raise Danger
    return img


def try_upgrade(find_all, find_all_scaled, tpl, log, skip_gold=False, allow_walls=False):
    """按升级优先级升一个现有建筑，成功返回 True。
    skip_gold：金币留着升大本营，只升花圣水的；allow_walls：允许升城墙（一般是金币满了）。"""
    banned = set()  # 这一轮点了升不了的建筑，换下一个
    for _ in range(3):
        try:
            r = _try_upgrade(find_all, tpl, log, skip_gold, allow_walls, banned)
        except Danger:
            log("出现可能花宝石的窗口，放弃本次升级")
            close_windows(find_all, find_all_scaled, tpl, log)
            return False
        if r is True or r is False:
            return r
        banned.add(r)  # 返回的是升不了的那个 (建筑, 资源)
        time.sleep(1)
    return False


MAX_PAGES = 8  # 工人菜单最多往下翻几页


def _open_menu(find_all, tpl):
    """打开工人菜单，返回菜单左边 mx；打不开返回 None。"""
    img = _safe_shot(find_all, tpl)
    icons = find_all(img, tpl["builder_icon"], 0.8)
    if not icons:
        return None
    tap(*icons[0])
    time.sleep(1.5)
    if not find_all(_safe_shot(find_all, tpl), tpl["menu_suggest"], 0.8):
        tap(*DESELECT)
        return None
    return icons[0][0] + MENU_DX


def _usable(row, skip_gold, allow_walls, banned):
    if row["new"] or row["red"] or row["cur"] not in ("gold", "elixir") or row["key"] in NEVER_UPGRADE:
        return False
    if (row["key"], row["cur"]) in banned:
        return False
    if skip_gold and row["cur"] == "gold":
        return False
    return allow_walls or row["key"] != "wall"


def _pages(find_all, tpl, mx):
    """一页页往下翻菜单，每页返回 (截图, 行)，翻到底就停。"""
    last = None
    for page in range(MAX_PAGES):
        if page:
            swipe(mx + 170, 620, mx + 170, 330, 800)
            time.sleep(1.2)
        img = _safe_shot(find_all, tpl)
        rows = read_rows(img, mx)
        sig = [(r["key"], r["cur"], r["y"] // 8) for r in rows]
        if sig == last:
            return
        last = sig
        yield img, rows


def _try_upgrade(find_all, tpl, log, skip_gold, allow_walls, banned):
    order = priority()
    rank = lambda k: order.index(k) if k in order else order.index("other")

    # 第一遍：整个菜单看一遍，找优先级最高的买得起的建筑
    mx = _open_menu(find_all, tpl)
    if mx is None:
        log("工人菜单没打开")
        return False
    best = None
    for _, rows in _pages(find_all, tpl, mx):
        for r in rows:
            if _usable(r, skip_gold, allow_walls, banned) and (best is None or rank(r["key"]) < rank(best[0])):
                best = (r["key"], r["cur"])
    tap(*DESELECT)
    time.sleep(1)
    if not best:
        log("没有买得起的升级" + ("（金币留着升大本营）" if skip_gold else ""))
        return False
    name = BUILDINGS.get(best[0], "建筑")
    log(f"按优先级升级：{name}（{'金币' if best[1] == 'gold' else '圣水'}）")

    # 第二遍：重新打开菜单，翻到这个建筑点它
    mx = _open_menu(find_all, tpl)
    if mx is None:
        return False
    for _, rows in _pages(find_all, tpl, mx):
        for r in rows:
            if (r["key"], r["cur"]) == best and _usable(r, skip_gold, allow_walls, banned):
                tap(mx + 75, r["y"])
                time.sleep(2)
                res = _press_upgrade(find_all, tpl, log, name)
                return best if res == "skip" else res
    log(f"第二遍没在菜单里找到{name}")
    tap(*DESELECT)
    return False


UPGRADE_SCALES = (0.8, 0.85, 0.9, 0.95, 1.0, 1.05)


def _find_upgrade_btn(img, tpl):
    """选中建筑后底部的"升级"按钮。按钮多的建筑（比如大本营）每个按钮都会缩小，所以多尺寸找。
    返回 (x, y, 缩放) 或 None。"""
    best = None
    for s in UPGRADE_SCALES:
        t = cv2.resize(tpl["btn_upgrade"], None, fx=s, fy=s)
        r = cv2.matchTemplate(img[560:], t, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(r)
        if mv >= 0.8 and (best is None or mv > best[0]):
            best = (mv, ml[0] + t.shape[1] // 2, ml[1] + 560 + t.shape[0] // 2, s)
    return best[1:] if best else None


def _press_upgrade(find_all, tpl, log, name):
    """建筑已选中：点"升级"→检查→点"确认"。"""
    img = _safe_shot(find_all, tpl)
    btn = _find_upgrade_btn(img, tpl)
    if not btn:
        log("没找到升级按钮，换下一项")
        tap(*DESELECT)
        time.sleep(0.5)
        tap(*DESELECT)  # 第一下可能只是关掉了菜单
        return "skip"
    tap(btn[0], btn[1])
    time.sleep(1.5)

    img = _safe_shot(find_all, tpl)
    confirm = find_all(img, tpl["confirm_label"], 0.8)
    if not confirm:
        log(f"{name}没出现确认窗口（截图存在 debug\\{name}_没出现确认窗口.png）")
        save_debug(img, f"{name}_没出现确认窗口")
        raise Danger  # 交给统一的关窗口逻辑
    cx, cy = confirm[0]
    if _icon_is_gem(img, cx, cy):
        log("确认按钮要花宝石，取消")
        raise Danger
    cost = cv2.cvtColor(img[cy + 28:cy + 62, cx - 80:cx + 40], cv2.COLOR_BGR2HSV)
    if _white_count(cost) < 300:  # 买得起时价格是白字，红字/看不清都不点
        log(f"{name}的价格不是白色（资源不够），取消")
        raise Danger
    tap(cx + random.randint(-20, 20), cy + 30 + random.randint(-5, 5))

    # 弹窗有动画，分几次检查
    for _ in range(4):
        time.sleep(1)
        img = _safe_shot(find_all, tpl)
    if find_all(img, tpl["confirm_label"], 0.8) or find_all(img, tpl["close_x"], 0.8):
        log("确认后窗口还在，当作失败处理")
        raise Danger
    tap(*DESELECT)
    log(f"已开始升级{name}")
    return True


def _price_is_red(img, bx, by, s=1.0):
    """选中建筑后，"升级"按钮上方的价格是不是红色（钱不够）。s 是按钮的缩放。"""
    hsv = cv2.cvtColor(img[by - int(118 * s):by - int(88 * s), bx - int(40 * s):bx + int(60 * s)], cv2.COLOR_BGR2HSV)
    return _red_count(hsv) > 30


def _townhall_by_template(img, find_all_scaled):
    """用 templates/townhall_*.png（每个等级一张）多尺寸匹配大本营。"""
    pts = []
    for f in sorted(TEMPLATES.glob("townhall_*.png")):
        t = imread(f)
        for x, y in find_all_scaled(img[130:720, 120:1460], t, 0.7, (0.6, 0.7, 0.8, 0.9, 1.0, 1.15, 1.3)):
            pts.append((x + 120, y + 130))
    return pts


def _townhall_candidates(img, top=3):
    """地图中间橙色屋顶的色块中心，大的在前。"""
    hsv = cv2.cvtColor(img[150:720, 150:1450], cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 0] >= 12) & (hsv[..., 0] <= 22) & (hsv[..., 1] > 150) & (hsv[..., 2] > 180)).astype("uint8")
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _, stats, cents = cv2.connectedComponentsWithStats(m)
    blobs = sorted(((stats[i, cv2.CC_STAT_AREA], cents[i]) for i in range(1, n)), key=lambda b: -b[0])
    return [(int(c[0]) + 150, int(c[1]) + 150) for a, c in blobs[:top] if a > 400]


def try_townhall(find_all, find_all_scaled, tpl, log):
    """尝试升级大本营。返回 "done"（已开始升级）/ "poor"（金币不够）/
    "busy"（正在升级中）/ "fail"（没找到、被别的条件卡住等）。"""
    try:
        return _try_townhall(find_all, find_all_scaled, tpl, log)
    except Danger:
        log("出现可能花宝石的窗口，放弃升级大本营")
        close_windows(find_all, find_all_scaled, tpl, log)
        return "fail"


def _try_townhall(find_all, find_all_scaled, tpl, log):
    """升级大本营。"""
    img = _safe_shot(find_all, tpl)
    # 先按各等级大本营模板找，再按橙色屋顶找；点开看名字确认
    cands = _townhall_by_template(img, find_all_scaled)[:3] + _townhall_candidates(img)
    for x, y in cands:
        tap(x, y + 10)
        time.sleep(1.5)
        img = _safe_shot(find_all, tpl)
        if find_all(img, tpl["label_townhall"], 0.8):
            break
        tap(*DESELECT)
        time.sleep(0.8)
    else:
        log("没找到大本营（截图存在 debug\没找到大本营.png）")
        save_debug(img, "没找到大本营")
        return "fail"
    btn = _find_upgrade_btn(img, tpl)
    if not btn:
        tap(*DESELECT)
        return "busy"
    if _price_is_red(img, *btn):
        tap(*DESELECT)
        return "poor"
    return "done" if _press_upgrade(find_all, tpl, log, "大本营") is True else "fail"


def new_rows(img, mx, my, skip_gold=False):
    """菜单里带绿色"新"字、买得起、而且不是用宝石付款的行。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    rows = []
    for k in range(1, 10):
        y = my + ROW_H * k
        if y + 25 > img.shape[0] - 230:  # 菜单底部被截断的行不要
            break
        xin = hsv[y - 5:y + 25, mx:mx + 25]
        rest = hsv[y - 5:y + 25, mx + 30:mx + 80]
        cost = hsv[y - 5:y + 25, mx + 250:mx + 355]
        if _green_count(xin) < 100 or _green_count(rest) > 30:  # 不是"新"行（或是标题行）
            continue
        if _red_count(cost) > 30 or _gem_count(cost) > 30 or _white_count(cost) < 100:
            continue  # 买不起 / 要花宝石 / 空
        if skip_gold and _gold_count(cost) > 100:
            continue
        rows.append((mx + 75, y + 15))
    return rows


def _gem_count(hsv):
    """宝石图标是黄绿色，色相 33~60（金币是 18~30）。"""
    return int(((hsv[..., 0] >= 33) & (hsv[..., 0] <= 60) & (hsv[..., 1] > 90) & (hsv[..., 2] > 200)).sum())


def _target_card(img):
    """商店里橙色箭头指着的那张卡片的中心；找不到或者那张卡要花宝石时返回 None。
    注意：所有能造的卡片都是蓝色的，必须靠箭头来认。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    top = hsv[280:480]
    arrow = ((top[..., 0] >= 8) & (top[..., 0] <= 22) & (top[..., 1] > 180) & (top[..., 2] > 200)).astype("uint8")
    n, _, st, _ = cv2.connectedComponentsWithStats(arrow)
    if n <= 1:
        return None
    i = 1 + int(st[1:, cv2.CC_STAT_AREA].argmax())
    if st[i, cv2.CC_STAT_AREA] < 4000:
        return None
    aim_x = int(st[i, cv2.CC_STAT_LEFT]) - 80  # 箭头朝左下，指向它左边那张卡

    cards = hsv[370:690]
    blue = ((cards[..., 0] >= 88) & (cards[..., 0] <= 110) & (cards[..., 1] > 90) & (cards[..., 2] > 200)).astype("uint8")
    n, _, st, cents = cv2.connectedComponentsWithStats(blue)
    for k in range(1, n):
        x, w, area = st[k, cv2.CC_STAT_LEFT], st[k, cv2.CC_STAT_WIDTH], st[k, cv2.CC_STAT_AREA]
        if area > 15000 and x <= aim_x <= x + w:
            price = hsv[625:670, x:x + w]
            if _gem_count(price) > 40 or _red_count(price) > 40:
                return None
            return int(cents[k][0]), int(cents[k][1]) + 370
    return None


def new_rows_scan(img, mx, skip_gold=False):
    """菜单滚动后标题看不到了，直接按左侧绿色"新"字找行。mx 是菜单文字左边缘。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    col = hsv[130:690, mx:mx + 25]
    green = ((col[..., 0] >= 30) & (col[..., 0] <= 85) & (col[..., 1] > 120) & (col[..., 2] > 150)).sum(axis=1)
    rows, y = [], 0
    while y < len(green):
        if green[y] >= 3:
            y0 = y
            while y < len(green) and green[y] >= 3:
                y += 1
            if y - y0 >= 12:  # 一行字的高度
                cy = 130 + (y0 + y) // 2
                rest = hsv[cy - 12:cy + 12, mx + 30:mx + 80]
                cost = hsv[cy - 15:cy + 15, mx + 250:mx + 355]
                if _green_count(rest) > 30:  # 整行都是绿字 = 标题
                    continue
                if _red_count(cost) > 30 or _gem_count(cost) > 30 or _white_count(cost) < 100:
                    continue
                if skip_gold and _gold_count(cost) > 100:
                    continue
                rows.append((mx + 75, cy))
        y += 1
    return rows


def try_build_new(find_all, find_all_scaled, tpl, log, skip_gold=False):
    """造一个新建筑（城墙、防御塔等），成功返回 True。
    前面的项被挡住（比如工人都忙）就试下一项，城墙不占工人。"""
    for pick in range(5):
        try:
            r = _try_build_new(find_all, tpl, log, skip_gold, pick)
        except Danger:
            close_windows(find_all, find_all_scaled, tpl)
            time.sleep(1)
            continue
        return r
    return False


PLACE_SCALES = (0.4, 0.45, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.6)


def _find_place(img, tpl, name):
    """摆放模式的 ✓ / ✗ 按钮。它们的大小跟着地图缩放变，所以多尺寸找。返回 [(x, y)] 或 []。"""
    best = None
    for sc in PLACE_SCALES:
        t = cv2.resize(tpl[name], None, fx=sc, fy=sc)
        r = cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(r)
        if mv >= 0.8 and (best is None or mv > best[0]):
            best = (mv, ml[0] + t.shape[1] // 2, ml[1] + t.shape[0] // 2)
    return [best[1:]] if best else []


def cancel_placement(img, tpl, log):
    """上一轮异常退出后还停在摆放模式（✓ 和 ✗ 都在）：点 ✗ 取消。返回是否取消了。"""
    ok, cancel = _find_place(img, tpl, "place_ok"), _find_place(img, tpl, "place_cancel")
    if ok and cancel and abs(ok[0][1] - cancel[0][1]) < 15:  # 两个按钮在同一行，排除商店关闭按钮之类的误判
        log("还停在摆放模式，先取消")
        tap(*cancel[0])
        return True
    return False


def _try_build_new(find_all, tpl, log, skip_gold, pick=0):
    img = _safe_shot(find_all, tpl)
    icons = find_all(img, tpl["builder_icon"], 0.8)
    if not icons:
        return False
    tap(*icons[0])
    time.sleep(1.5)

    img = _safe_shot(find_all, tpl)
    menu = find_all(img, tpl["menu_suggest"], 0.8)
    if not menu:
        tap(*DESELECT)
        return False
    th, tw = tpl["menu_suggest"].shape[:2]
    mx = menu[0][0] - tw // 2
    rows = new_rows(img, mx, menu[0][1] - th // 2, skip_gold)
    if not rows:  # 新建筑常在菜单下面，往上滑一下再找
        swipe(mx + 170, 620, mx + 170, 260, 600)
        time.sleep(1.2)
        img = _safe_shot(find_all, tpl)
        rows = new_rows_scan(img, mx, skip_gold)
    if pick >= len(rows):
        tap(*DESELECT)
        return False
    tap(*rows[pick])
    time.sleep(2)

    # 打开商店，要造的建筑卡片是亮蓝色的，其他是灰色
    img = _safe_shot(find_all, tpl)
    card = _target_card(img)
    if not card:
        log("商店里没找到要造的建筑卡片（或者它要花宝石）")
        raise Danger  # 统一关窗口
    tap(*card)
    time.sleep(2)

    img = _safe_shot(find_all, tpl)
    ok = _find_place(img, tpl, "place_ok")
    if not ok:
        log("没进入摆放模式（截图存在 debug\没进入摆放模式.png）")
        save_debug(img, "没进入摆放模式")
        cancel = _find_place(img, tpl, "place_cancel")
        if cancel:
            tap(*cancel[0])
        raise Danger
    tap(*ok[0])
    time.sleep(1.5)
    _safe_shot(find_all, tpl)
    if _find_place(screenshot(), tpl, "place_ok"):  # 位置不合法，✓ 还在
        log("摆放位置不合法，取消")
        cancel = _find_place(screenshot(), tpl, "place_cancel")
        if cancel:
            tap(*cancel[0])
        return False
    tap(*DESELECT)
    log("已建造一个新建筑")
    return True
